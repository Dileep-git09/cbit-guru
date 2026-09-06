"""Offline smoke test — proves the whole pipeline wires together without
spending a single API call.

Fakes the Gemini embedder (deterministic hash vectors) and the Cohere
generator, runs against an in-memory Qdrant, and exercises:
    ingest text -> ingest PDF -> ingest image -> retrieve -> build context
    -> generate -> admin login -> chat endpoint -> admin ingest endpoint

Why this exists: on a 12-day deadline you cannot afford to discover a wiring
bug (a typo'd field name, a missing await) only when burning real API quota
during a 40-minute ingest. This proves the *plumbing* works in under a
second, for free — every time you touch the pipeline, run this first.

Run:
    python -m scripts.smoke_test
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys

# Setting these BEFORE importing app.config means Settings() picks them up
# instead of trying to read backend/.env (which may have real keys the smoke
# test must never touch). setdefault so a real .env is still respected if
# someone deliberately sets QDRANT_URL etc. beforehand.
os.environ.setdefault("QDRANT_URL", ":memory:")
os.environ.setdefault("ADMIN_DB_FILE", ":memory:")
os.environ.setdefault("GEMINI_API_KEY", "fake")
os.environ.setdefault("COHERE_API_KEY", "fake")
os.environ.setdefault("ADMIN_EMAIL", "admin@cbit.ac.in")
os.environ.setdefault("ADMIN_PASSWORD", "smoke-pass")
os.environ.setdefault("JWT_SECRET", "smoke-secret")

from app.config import settings  # noqa: E402
from app.services import embeddings, ingest, llm, retriever, vectorstore  # noqa: E402

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}{f'  — {detail}' if detail else ''}")


# ---- stub the paid APIs ---------------------------------------------------
def fake_vector(text: str) -> list[float]:
    """Deterministic pseudo-embedding: bag-of-words hashed into 3072 buckets.

    Not a real embedding (no semantic meaning) — but it's stable: the same
    word always lands in the same bucket, so two texts sharing words get
    vectors with nonzero cosine similarity. That's just enough structure for
    the retrieval checks below to be meaningful rather than random.
    """
    vec = [0.0] * settings.embedding_dim
    for word in text.lower().split():
        idx = int(hashlib.md5(word.encode()).hexdigest(), 16) % settings.embedding_dim
        vec[idx] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]  # normalised, like a real embedding


async def fake_embed_one(text, task_type="RETRIEVAL_DOCUMENT"):
    return fake_vector(text)


async def fake_embed_many(texts, task_type="RETRIEVAL_DOCUMENT", concurrency=4):
    return [fake_vector(t) for t in texts]


async def fake_generate(question, context, history=None):
    head = context.split("\n")[1][:90] if context else "no context"
    return f"[stub answer] q={question!r} grounded_on={head!r}"


# Monkey-patching the module-level functions: every other module imported
# `from app.services import embeddings` and calls `embeddings.embed_one(...)`
# at call time, so reassigning the attribute here redirects every caller —
# no need to touch ingest.py, retriever.py, or llm.py at all.
embeddings.embed_one = fake_embed_one
embeddings.embed_many = fake_embed_many
embeddings.embed_query = fake_embed_one
llm.generate = fake_generate


SAMPLE = """
CBIT — Chaitanya Bharathi Institute of Technology, Gandipet, Hyderabad 500075.
CBIT is an autonomous college affiliated to Osmania University, established in 1979.
The Department of Artificial Intelligence and Data Science offers a B.E. programme.
Admission to B.E. is through TS EAMCET counselling. The principal is Prof. C.V. Narsimhulu.
Sruthi, the National Students' Fest, is scheduled for February 19-21, 2026.
Hostel facilities include a boys hostel with mess, a girls hostel, and a library.
"""


async def main() -> None:
    await vectorstore.ensure_collection()
    check("Qdrant collection created", True, settings.qdrant_collection)

    # --- ingestion ---
    res = await ingest.ingest_text(SAMPLE, source_name="cbit_overview.txt")
    check("Ingest raw text", res["chunks"] > 0, f"{res['chunks']} chunk(s)")

    img = await ingest.ingest_image(
        file_name="boys_hostel_mess_block.jpg",
        image_url="https://www.cbit.ac.in/img/boys_hostel_mess_block.jpg",
        caption="Boys Hostel Mess",
        surrounding="The hostel mess offers dining facilities for hostel residents.",
        page_title="Hostel",
        tags=["campus"],
    )
    check("Ingest image as text", img["chunks"] == 1, img["doc_id"][:8])

    total = await vectorstore.count()
    check("Vectors stored in Qdrant", total >= 2, f"{total} points")

    # --- image-to-text representation ---
    body = ingest.image_to_text("boys_hostel_mess_block.jpg", "Boys Hostel Mess")
    check(
        "Filename slug expanded to words",
        "boys hostel mess block" in body.lower(),
        body.split("\n")[0],
    )

    # --- retrieval ---
    hits = await retriever.retrieve("Where is CBIT located?")
    ctx = retriever.build_context(hits["text"])
    check("Text retrieval returns context", bool(ctx), f"{len(hits['text'])} hit(s)")
    check("Context mentions Gandipet", "gandipet" in ctx.lower())

    hits2 = await retriever.retrieve("hostel mess photos")
    imgs = retriever.format_images(hits2["images"])
    check("Image retrieval returns a picture", len(imgs) >= 1,
          imgs[0]["caption"] if imgs else "none")

    # --- generation ---
    answer = await llm.generate("Where is CBIT located?", ctx)
    check("Generator produced an answer", len(answer) > 10)

    # --- API surface (exercises main.py + routers end-to-end, in-process) ---
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app.main import app  # noqa: PLC0415

    with TestClient(app) as client:
        r = client.get("/api/health")
        check("GET /api/health", r.status_code == 200, r.json().get("status", ""))

        r = client.post("/api/chat", json={"message": "Where is CBIT located?"})
        ok = r.status_code == 200 and r.json()["answer"]
        check("POST /api/chat", ok, f"{len(r.json().get('sources', []))} sources")

        r = client.post("/api/admin/login",
                        json={"email": "admin@cbit.ac.in", "password": "wrong"})
        check("Bad admin password rejected", r.status_code == 401)

        r = client.post("/api/admin/login",
                        json={"email": "admin@cbit.ac.in", "password": "smoke-pass"})
        check("Admin login issues JWT", r.status_code == 200)
        token = r.json()["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        check("Stats without token is 401",
              client.get("/api/admin/stats").status_code == 401)

        r = client.get("/api/admin/stats", headers=hdr)
        check("GET /api/admin/stats", r.status_code == 200,
              f"{r.json().get('total_points')} points")

        r = client.post("/api/admin/ingest/text", headers=hdr,
                        json={"text": "The library is open 9am to 8pm on weekdays at CBIT.",
                              "source_name": "library_hours"})
        check("POST /api/admin/ingest/text", r.status_code == 200,
              f"{r.json().get('chunks')} chunk(s)")

        r = client.get("/api/admin/browse?limit=10", headers=hdr)
        check("GET /api/admin/browse", r.status_code == 200,
              f"{len(r.json().get('items', []))} rows")

        # --- multi-admin auth: roles, user management, password changes ---
        r = client.get("/api/admin/me", headers=hdr)
        check("GET /api/admin/me reports superadmin", r.status_code == 200
              and r.json().get("role") == "superadmin")

        r = client.post("/api/admin/users", headers=hdr,
                         json={"email": "staff@cbit.ac.in", "password": "staffpass123", "role": "admin"})
        check("Superadmin creates a new admin user", r.status_code == 201, r.json().get("id", "")[:8])
        staff_id = r.json()["id"]

        r = client.post("/api/admin/login",
                         json={"email": "staff@cbit.ac.in", "password": "staffpass123"})
        check("New admin can log in", r.status_code == 200)
        staff_hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.post("/api/admin/users", headers=staff_hdr,
                         json={"email": "x@x.com", "password": "whatever1", "role": "admin"})
        check("Plain admin blocked from creating users", r.status_code == 403)

        r = client.get("/api/admin/users", headers=staff_hdr)
        check("Plain admin blocked from listing users", r.status_code == 403)

        r = client.get("/api/admin/users", headers=hdr)
        check("Superadmin lists all users", r.status_code == 200 and len(r.json()) == 2,
              f"{len(r.json())} user(s)")

        r = client.patch("/api/admin/me/password", headers=staff_hdr,
                          json={"current_password": "wrong", "new_password": "newpass1234"})
        check("Password change rejects wrong current password", r.status_code == 401)

        r = client.patch("/api/admin/me/password", headers=staff_hdr,
                          json={"current_password": "staffpass123", "new_password": "newpass1234"})
        check("Self password change succeeds", r.status_code == 200)

        r = client.post("/api/admin/login",
                         json={"email": "staff@cbit.ac.in", "password": "newpass1234"})
        check("Login works with the new password", r.status_code == 200)

        r = client.patch(f"/api/admin/users/{staff_id}/password", headers=hdr,
                          json={"new_password": "resetbyroot1"})
        check("Superadmin resets another user's password", r.status_code == 200)

        r = client.delete(f"/api/admin/users/{staff_id}", headers=hdr)
        check("Superadmin deletes the admin user", r.status_code == 200)

        me = client.get("/api/admin/me", headers=hdr).json()
        r = client.delete(f"/api/admin/users/{me['id']}", headers=hdr)
        check("Deleting the last super admin is blocked", r.status_code == 400)

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("Failed:", ", ".join(failed))
        sys.exit(1)
    print("Pipeline is wired correctly. Add real API keys to go live.")


if __name__ == "__main__":
    asyncio.run(main())
