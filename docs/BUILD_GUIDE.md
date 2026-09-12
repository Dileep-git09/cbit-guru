# CBIT Guru — Complete Build Guide & Architecture Reference

This document explains **everything**: what CBIT Guru is, why every technology
choice was made, the exact folder/file structure, how the two core pipelines
(ingestion and query) work end to end, and a step-by-step guide to build the
entire project from an empty folder — the order we actually built it in.

Read this if you want to understand the system deeply enough to defend every
line in a viva, rebuild it from scratch, or extend it.

---

## Table of contents

1. [What this project is](#1-what-this-project-is)
2. [Architecture overview](#2-architecture-overview)
3. [Technology choices and why](#3-technology-choices-and-why)
4. [Complete folder structure](#4-complete-folder-structure)
5. [The two core pipelines](#5-the-two-core-pipelines)
6. [Step-by-step: building it from scratch](#6-step-by-step-building-it-from-scratch)
7. [Environment setup (the part that touches your machine)](#7-environment-setup)
8. [Running and verifying](#8-running-and-verifying)
9. [Known gotchas and how we solved them](#9-known-gotchas-and-how-we-solved-them)
10. [File-by-file reference](#10-file-by-file-reference)

---

## 1. What this project is

**CBIT Guru** is a Retrieval-Augmented Generation (RAG) chat assistant for
Chaitanya Bharathi Institute of Technology. Instead of a general-purpose
chatbot that might hallucinate facts about the college, it answers **only**
from real CBIT data — the institute's own website, PDFs, and notices — and
shows exactly which source each answer came from.

**Why RAG instead of fine-tuning an LLM on CBIT data?** Fine-tuning bakes
knowledge into model weights: every new notice would require retraining, and
the model can still confidently invent facts. RAG stores facts in a vector
database instead. Adding a new notice is a database write (instant, cheap,
no GPU needed), and every answer can cite exactly which stored fact it came
from. For an institution whose information changes weekly (exam schedules,
placement news, events), retrieval is the only maintainable design.

**Core capabilities:**
- Grounded Q&A with inline citations `[1]`, `[2]` and a Sources list
- Multimodal retrieval — campus images are searchable via natural-language questions, with **no vision model** at all
- Multilingual replies (English, Hindi/Devanagari, Telugu script, Hinglish, Tinglish) via prompt engineering alone
- Voice input/output (Web Speech API)
- An admin panel to add new knowledge (paste text, upload a file, fetch a URL) without touching a terminal

---

## 2. Architecture overview

```
                        ┌──────────────────────────────┐
  React frontend        │  Chat UI · Voice · Admin      │
  (Vite, port 5173)     └───────────────┬──────────────┘
                                        │ /api/*  (HTTP + SSE)
                        ┌───────────────▼──────────────┐
  FastAPI backend       │  Orchestrator                 │
  (port 8000)           │  chat · admin · ingestion     │
                        └───┬───────────────────────┬──┘
                            │                       │
              ┌─────────────▼──────┐      ┌─────────▼────────────┐
              │ Gemini embeddings  │      │  Cohere LLM           │
              │ gemini-embedding-  │      │  command-r            │
              │ 001 · 3072 dims    │      │  (generator)          │
              └─────────┬──────────┘      └─────────▲────────────┘
                        │ vectors                    │ context
              ┌─────────▼────────────────────────────┴────────────┐
              │  Qdrant · collection `cbit_guru_rag`               │
              │  cosine similarity · HNSW · metadata filtering     │
              └───────────────────────▲───────────────────────────┘
                                      │
        ┌─────────────────────────────┴──────────────────────────┐
        │ Ingestion: Playwright + aiohttp + BeautifulSoup crawler │
        │            PyMuPDF (PDFs) · image-as-text (multimodal)  │
        └────────────────────────────────────────────────────────┘
```

**One request, traced end to end** (this is the exact path a chat message
takes — draw this on paper, it's the answer to "explain your architecture"):

```
Frontend send()
  → POST /api/chat/stream                (routers/chat.py)
  → retriever.retrieve(question)         (services/retriever.py)
      → embeddings.embed_query(question) (services/embeddings.py — Gemini)
      → vectorstore.search(vector)       (services/vectorstore.py — Qdrant)
  → retriever.build_context(hits)        (numbered, citable context block)
  → llm.generate_stream(question, ctx)   (services/llm.py — Cohere, SSE)
  → tokens streamed back to the browser
```

---

## 3. Technology choices and why

| Layer | Choice | Why |
|---|---|---|
| Embeddings | Gemini `gemini-embedding-001`, 3072-d | Free tier, high-dimensional vectors capture fine semantic distinctions |
| Vector DB | Qdrant | Open source, Rust-based HNSW (fast), metadata filtering built in, runs locally (Docker) or in the cloud (free tier) |
| Similarity metric | Cosine | Embedding vectors vary in magnitude with text length, but length isn't relevance. Cosine measures only the *angle* between vectors, so a one-line notice and a five-page PDF are compared fairly. Dot product is faster but biases toward longer documents. |
| Generator | Cohere `command-r` | Purpose-built for RAG grounding, cheap, strong multilingual support |
| Backend | FastAPI | Async by default — many concurrent students without blocking; auto-generates OpenAPI docs |
| Frontend | React + Vite | Component model suits chat history, streaming, voice, and an admin panel; Vite gives instant dev reload |
| Auth | Multi-admin JWT + SQLite | Admin accounts (with a superadmin/admin role split) live in a small SQLite table — a real database, but a lightweight embedded one, since this is a handful of low-traffic structured records, not the core RAG data |
| Web scraping | aiohttp + BeautifulSoup, Playwright fallback | Fast path for ordinary server-rendered pages; Playwright only kicks in for JS-rendered pages, so most of the crawl doesn't need a browser at all |

---

## 4. Complete folder structure

```
cbit-guru/
├── .gitignore                    # excludes .env, venvs, node_modules, regenerable scraped data
├── docker-compose.yml            # local Qdrant option (alternative to Qdrant Cloud)
├── README.md                     # quick-start guide
├── ROADMAP.md                    # 12-day build/demo plan with viva Q&A
├── docs/
│   └── BUILD_GUIDE.md            # this file
│
├── backend/
│   ├── requirements.txt          # pinned Python dependencies
│   ├── .env.example              # config template (safe to commit)
│   ├── .env                      # REAL secrets (gitignored, never committed)
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI app entry point: CORS, lifespan, /api/health
│   │   ├── config.py             # Settings loaded from .env — the single source of truth for every tunable
│   │   ├── models.py             # Pydantic request/response schemas (the frontend<->backend contract)
│   │   ├── security.py           # JWT creation/validation, role dependencies (require_admin/require_superadmin)
│   │   ├── ratelimit.py          # per-IP sliding-window limit on /api/chat*
│   │   ├── logging_config.py     # structured (plain/JSON) logging + request-id propagation
│   │   ├── middleware.py         # RequestContextMiddleware: request IDs, access logs, HTTP metrics
│   │   │
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── chat.py           # POST /api/chat and /api/chat/stream (SSE)
│   │   │   └── admin.py          # login, me, ingest text/file/url, browse, stats, delete, admin-user CRUD
│   │   │
│   │   └── services/             # the actual RAG logic, one concern per file
│   │       ├── __init__.py
│   │       ├── embeddings.py     # Gemini embedding calls + retry/backoff
│   │       ├── vectorstore.py    # Qdrant collection management, upsert, search, browse
│   │       ├── chunker.py        # HTML stripping, text cleaning, chunking with overlap
│   │       ├── ingest.py         # unifies text/PDF/image/URL input into one storage schema
│   │       ├── retriever.py      # question -> embed -> search -> context block
│   │       ├── llm.py            # Cohere generation, the grounding + multilingual system prompt
│   │       ├── users.py          # SQLite-backed admin accounts: bcrypt hashes, roles, CRUD
│   │       ├── cache.py          # exact + semantic response cache (cuts repeat API calls)
│   │       ├── circuitbreaker.py # fail-fast during a real Gemini/Cohere outage
│   │       ├── redisclient.py    # optional shared backend for cache.py + ratelimit.py
│   │       └── metrics.py        # Prometheus counters/histograms, exposed at GET /metrics
│   │
│   ├── instance/
│   │   └── admin.db              # SQLite admin-user table — gitignored, contains password hashes
│   │
│   ├── scraper/
│   │   ├── __init__.py
│   │   └── crawl.py              # BFS website crawler -> data/{text_content,pdfs,images}
│   │
│   ├── scripts/
│   │   ├── __init__.py
│   │   ├── smoke_test.py         # offline, zero-cost, 29-assertion pipeline check
│   │   ├── ingest_all.py         # bulk-embed everything under data/ into Qdrant
│   │   ├── evaluate.py           # retrieval hit-rate / answer accuracy / latency harness
│   │   └── eval_set.json         # test questions with expected keywords
│   │
│   └── data/                     # scraper output — entirely regenerable, gitignored
│       ├── text_content/         # one .txt per scraped page
│       ├── pdfs/                 # downloaded PDFs
│       └── images/               # downloaded images + manifest.json (captions/context)
│
└── frontend/
    ├── package.json
    ├── vite.config.js            # dev server + /api proxy to :8000
    ├── index.html
    └── src/
        ├── main.jsx              # router: / (chat), /admin (login), /admin/panel
        ├── styles.css
        ├── pages/
        │   ├── Chat.jsx          # the main chat UI: streaming, voice, suggestions
        │   ├── AdminLogin.jsx    # JWT login form
        │   └── AdminPanel.jsx    # Text/File/URL ingestion tabs + Browse Data table
        ├── components/
        │   ├── Message.jsx       # one chat bubble: markdown, sources dropdown, TTS button
        │   └── ImageStrip.jsx    # "Related Images" row + lightbox
        └── lib/
            ├── api.js            # the ONLY place that calls fetch() — single API surface
            └── useVoice.js       # Web Speech STT/TTS hooks
```

---

## 5. The two core pipelines

Everything the system does reduces to two pipelines. Understand these and
you understand the whole project.

### 5.1 Ingestion pipeline — turning raw data into searchable vectors

This runs whenever the scraper collects a page, an admin uploads a file, or
`ingest_all.py` does a bulk run.

```
Raw input (HTML page / PDF file / pasted text / image metadata)
        │
        ▼
strip_html()            services/chunker.py — remove <script>/<style>, get plain text
        │
        ▼
clean_text()             services/chunker.py — normalise whitespace, drop control chars
        │
        ▼
chunk_text()             services/chunker.py — split into ~1200-char pieces on paragraph
        │                boundaries, each carrying 200 chars of overlap from the previous
        │                chunk (so a fact split across a chunk boundary is still findable
        │                whole in at least one chunk)
        ▼
embed_many()             services/embeddings.py — Gemini embeds each chunk into a
        │                3072-dim vector (task_type=RETRIEVAL_DOCUMENT), with 5-attempt
        │                exponential backoff on HTTP 429
        ▼
upsert_chunks()          services/vectorstore.py — each vector + its metadata
                         (user_id, doc_id, chunk_index, type, file_name, url, text)
                         is written to Qdrant as a point with cosine distance indexing
```

**Special case — images (the "multimodal without a vision model" trick):**
An image is never sent to any model. Instead, `ingest.image_to_text()` builds
a short *textual description* of it — the filename slug expanded to words
(`boys_hostel_mess.jpg` → "boys hostel mess"), its alt/caption text, and the
surrounding page copy the scraper captured — and that text is embedded and
stored exactly like any other chunk, just tagged `type: "image"`. A query
like "show me the hostel mess" matches it in the same cosine search that
matches ordinary text.

**Special case — PDFs:** `ingest.ingest_pdf()` uses PyMuPDF to extract text
page by page, stopping once `MAX_PDF_CHARS` (50,000) is hit, so one huge PDF
can't dominate the whole knowledge base.

### 5.2 Query pipeline — turning a question into a grounded answer

This runs on every chat message.

```
User types a question in Chat.jsx
        │
        ▼
POST /api/chat/stream   routers/chat.py
        │
        ▼
embed_query()            services/embeddings.py — Gemini embeds the question
        │                (task_type=RETRIEVAL_QUERY — embedded slightly differently
        │                from documents, for better recall)
        ▼
vectorstore.search()     services/vectorstore.py — cosine similarity search,
        │                top_k=8 for text and image_top_k=5 for images, run as
        │                two separate filtered searches from the ONE query vector
        ▼
retriever.build_context()  concatenates the retrieved chunks into a numbered,
        │                   citable block: "[1] (source: ...)\n<chunk text>"
        ▼
llm.generate_stream()    services/llm.py — Cohere is given a system prompt that
        │                forbids answering outside the context, requires inline
        │                [n] citations, and detects+matches the question's
        │                language/script (English/Hindi/Telugu/Hinglish/Tinglish)
        ▼
Server-Sent Events stream back to the browser:
  event: meta   → {sources, images, grounded}   (sent BEFORE any answer text,
                   so the UI can show citations as soon as the reply starts typing)
  event: token  → each word/chunk of the answer as it's generated
  event: done   → stream complete
```

**The grounding guarantee, concretely:** if retrieval finds nothing above the
score threshold, `retriever.retrieve()` retries once *without* the threshold
(an "almost relevant" chunk beats no context at all) — but the system prompt
still forbids inventing facts, so the LLM will say "I don't have that
information" rather than guess, even with weak context.

### 5.3 Admin authentication — multi-user, role-based

The admin panel started as a single hardcoded login in `.env`. It's now a
real (if small) auth system, because a real institution needs more than one
person able to manage the knowledge base, and needs some way to control who
can grant that access in the first place.

```
Login request
        │
        ▼
security.verify_admin()  → services/users.py verify_credentials()
        │                    looks up the email in SQLite, then bcrypt-verifies
        │                    the password against the stored hash — a match
        │                    returns {id, email, role}, anything else returns
        │                    None (the caller can't tell which part was wrong)
        ▼
security.create_token()  encodes {sub: id, email, role, exp} as a JWT
        │
        ▼
Every protected route depends on require_admin (any valid admin JWT) or
require_superadmin (require_admin, PLUS role == "superadmin")
```

**Two roles, one clear line:** `admin` can log in and use every ingestion/
browse tool; only `superadmin` can create, list, reset the password of, or
delete OTHER admin accounts (`routers/admin.py`'s `/users*` routes). A plain
admin account being compromised or misused therefore can't be used to mint
new admin accounts — the blast radius of one leaked login is capped.

**Bootstrapping:** on the very first boot, `services/users.py`'s `init()`
creates the `admin_users` SQLite table and, if it's empty, seeds exactly one
`superadmin` row from `.env`'s `ADMIN_EMAIL`/`ADMIN_PASSWORD`. After that
first boot, the database is authoritative — `.env`'s admin credentials are
never read again. This means the very first login must use whatever's in
`.env`, but every account after that is created *through the app itself*
(the Admins tab, superadmin-only), not by editing config and restarting.

**Self-service vs. override:** an admin changing their OWN password
(`PATCH /admin/me/password`) must supply their current password — proof of
identity, so a stolen-but-still-valid JWT can't be used to permanently lock
the real owner out. A superadmin resetting SOMEONE ELSE's password
(`PATCH /admin/users/{id}/password`) does NOT need that user's current
password — the whole point is the target user may have forgotten it. Two
different trust models for what looks like "the same" operation.

**The lockout guard:** deleting an admin account checks
`count_superadmins()` first — you cannot delete the last remaining
superadmin, even as that superadmin, because that would leave the system
with no account capable of ever creating another one.

### 5.4 Scaling to many concurrent students

A single laptop running one uvicorn process can still serve many students at
once — but only if it's protected from the two ways that goes wrong: many
students asking the *same* question, and many students asking *at the same
instant*. Both are addressed with real code, not just a claim in a report.

**Problem 1 — repeat questions waste the shared, scarce API quota.** In any
real cohort, a handful of questions dominate ("where is CBIT located",
"what are the placement stats", the four suggested questions the UI itself
puts in front of every visitor). Answering each of those fresh, every time,
burns the same free-tier Gemini/Cohere quota that every OTHER student is
also drawing from.

`services/cache.py` fixes this with two tiers, checked before any real API
call:
- **exact** — the normalised question string maps straight to a stored
  response. Catches literal repeats at zero cost.
- **semantic** — the new query's embedding (already needed for retrieval)
  is compared via cosine similarity against a capped list of past
  (embedding, response) pairs. Catches paraphrases the exact tier misses
  ("Where's CBIT?" vs "Where is CBIT located?") at the cost of one
  embedding call instead of a full retrieval + generation round trip.

Measured on this exact deployment: a repeated question dropped from
**13.6s to 0.27s** (exact-match hit, zero API calls), and a paraphrased
version of the same question answered in **0.9s** (semantic hit — one
embedding call, no generation call), both returning the byte-identical
cached answer. `GET /api/admin/stats` exposes `cache_exact_entries` and
`cache_semantic_entries` so this is visibly demonstrable, not just
asserted.

**Problem 2 — a burst of simultaneous requests can exhaust quota
instantly.** This is not hypothetical: the very first real bulk ingest run
hit this exact failure mode (§9.1) when one file's 61 chunks fired at once
and blew through the free tier's requests-per-minute ceiling. The same
thing happens if 50 students click "send" within the same second — 50
simultaneous Gemini + Cohere calls, and the free tier rejects all of them,
not just the excess.

`embeddings.py` and `llm.py` each hold a **process-wide `asyncio.Semaphore`**
(`GEMINI_MAX_CONCURRENCY` / `COHERE_MAX_CONCURRENCY`, default 5) around the
actual outbound network call — not per-request, but shared across *every*
concurrent request the process is handling. The 6th, 7th, ... simultaneous
caller simply waits its turn for a slot instead of firing immediately, so a
burst degrades to "briefly slower" instead of "rejected for everyone."

**Problem 3 — one client (bug or abuse) can starve everyone else.**
`ratelimit.py` is a per-IP rate limiter (`CHAT_RATE_LIMIT_PER_MINUTE`,
default 30/min) in front of both chat endpoints. It runs *before* the cache
lookup, so even a flood of cache-hitting requests (which would otherwise be
nearly free) still counts against the sender's own limit — this caps how
much of the shared quota any single visitor can consume, protecting
everyone else's access to the free tier.

**Problem 4 — a single hung request could starve the other four forever.**
The concurrency semaphores from Problem 2 have a sharp edge: if one Gemini
or Cohere call never returns (a network stall, not an error — just
silence), it holds its semaphore slot forever. With only 5 slots, five
unlucky hung requests would be enough to freeze the app for every student,
indefinitely, with no exception ever raised to say why. `embeddings.py` and
`llm.py`'s single-shot calls (`embed_one`, `generate`) wrap the actual
network call in `asyncio.wait_for(..., timeout=API_CALL_TIMEOUT_SECONDS)`
(default 20s) — the semaphore's `async with` block still releases the slot
the instant the timeout fires, even though the underlying OS thread
(`asyncio.to_thread`) can't be forcibly killed and keeps running in the
background until the real call eventually returns or errors on its own.
Streaming (`generate_stream`) deliberately has no fixed timeout — a stream
is expected to run as long as the answer takes, so a single deadline would
either cut off long legitimate answers or be too generous to catch a truly
dead connection — it relies on Problem 5's circuit breaker instead.

**Problem 5 — during a real outage, every request pays the full retry
cost, without changing the outcome.** Both `embed_one` and `generate`
retry up to 5 times with exponential backoff on a 429. That's the right
behaviour for a *brief* rate-limit blip, but if Gemini or Cohere is
actually down, every single request from every single student
independently discovers that the slow way, taking several seconds each to
fail, while also keeping the concurrency semaphore saturated with calls
that were never going to succeed. `services/circuitbreaker.py` is a small
three-state circuit breaker (closed → open → half-open): after
`CIRCUIT_BREAKER_THRESHOLD` (default 5) *consecutive* failures, it "opens"
and every call fails immediately — no retries, no waiting — for
`CIRCUIT_BREAKER_COOLDOWN_SECONDS` (default 30s). After the cooldown,
exactly one call is let through as a trial: if it succeeds the breaker
closes again, if it fails the cooldown restarts. Gemini and Cohere each
get their own independent breaker instance, since one being down says
nothing about the other.

**The scaling-past-one-process gap — closed, not just documented.** An
earlier version of this project stated that the cache and rate limiter
were in-memory-only, correct for a single uvicorn worker but requiring a
"future" swap to a shared store to run multiple worker processes or
replicas. That swap is now actually implemented: set `REDIS_URL` (e.g.
`redis://localhost:6379/0`) and both `services/cache.py`'s exact-match
tier and `ratelimit.py` switch to Redis automatically — `services/
redisclient.py` is the shared lazy connection both use. Leave `REDIS_URL`
blank (the default) and both fall back to the exact same in-memory
behaviour as before; nothing changes for the single-process deployment
this project actually runs. If Redis is *configured* but unreachable when
first needed, the same fallback kicks in automatically with a logged
warning — a caching/rate-limiting optimisation failing to connect
shouldn't take the whole chatbot down with it. Verified live: pointed
`REDIS_URL` at a deliberately unreachable address and confirmed the app
logs the warning and keeps serving real chat requests normally.

**What's still genuinely single-process-only, and why that's fine:** the
*semantic* cache tier (the cosine-similarity paraphrase matcher) stays
in-memory regardless of `REDIS_URL`. A real vector-similarity index
belongs in a proper vector store — this project already has one, Qdrant,
for the actual knowledge base — not reimplemented on top of Redis's plain
key-value/sorted-set primitives for what's at most a few hundred cache
entries. Multiple workers would each keep their own separate semantic
cache; only the exact-match tier and the rate limiter are shared. This is
a deliberate, documented scope boundary, not an oversight.

### 5.5 Observability: structured logs, request tracing, metrics

Everything above (caching, concurrency limits, circuit breakers) makes the
system more resilient, but resilience you can't *see* is hard to trust or
debug. This layer answers two different questions a real deployment needs
answered: "what happened on THIS one request" (structured logs + request
IDs) and "what's the trend over the last hour" (metrics) — deliberately
two different tools, because they answer different questions and neither
substitutes for the other.

**Request tracing.** `app/middleware.py`'s `RequestContextMiddleware`
assigns a short ID to every incoming HTTP request (or reuses one supplied
via an `X-Request-ID` header, if this API sits behind a gateway that
already assigns one) and stores it in a `contextvars.ContextVar`
(`app/logging_config.py`). A logging `Filter` reads that contextvar back
out for *every* log line emitted anywhere while handling that request —
including lines logged deep inside `services/embeddings.py`, or even
inside third-party libraries like `httpx`, none of which know request
tracing exists. The practical effect: grep one request ID and see that
request's whole story — which Qdrant queries it ran, which Cohere call it
made, how long each took, and the final outcome — as one coherent
narrative instead of hunting through interleaved output from every
concurrent request. Verified directly: a single `/api/chat` call's ID
appeared on its two Qdrant queries, its Cohere call, the chat summary
line, and the access-log line — all four, tied together, none of them
written by code that was told to do that explicitly.

This middleware is deliberately a raw ASGI callable, not Starlette's
`BaseHTTPMiddleware` — that wrapper buffers an entire response before
forwarding it, which would silently turn `/api/chat/stream`'s token-by-
token SSE typing effect into "the whole answer arrives at once." Wrapping
`send` directly instead preserves streaming exactly as before.

**Structured logs.** `LOG_FORMAT` in `.env` picks the output format:
`plain` (the default) is a readable one-liner for a terminal during
development; `json` emits one JSON object per log line — timestamp,
level, logger name, message, request_id, and any extra fields a call site
attached (`log.info(..., extra={"cache_tier": "exact", ...})` becomes real
JSON keys, not text buried in a message string). That's the format a real
deployment ships to a log aggregator (Loki, CloudWatch, ELK) that indexes
fields for querying — "show me every request over 5 seconds in the last
hour" becomes a query instead of a regex over free text.

**Metrics.** `GET /metrics` exposes Prometheus's plain-text exposition
format (`app/services/metrics.py`): total requests and request-duration
histograms by method/path/status, response-cache hits by tier
(`exact`/`semantic`/`miss` — a rising miss ratio over time is the signal
that the cache has stopped earning its keep, long before anyone would
notice reading logs), and a gauge per external dependency showing whether
its circuit breaker is currently open. Deliberately unauthenticated,
matching standard Prometheus practice — a real deployment restricts
scrape access at the network layer, not behind the same JWT auth as the
admin API. Path labels are normalised (`/api/admin/users/{id}/password`,
not the literal id) so the number of distinct label combinations stays
bounded instead of growing with every user ever created.

---

## 6. Step-by-step: building it from scratch

This is the exact order we built the project in, and the order we'd
recommend to anyone rebuilding it — each step only depends on what came
before it.

### Phase 1 — Backend foundation
1. Create the folder skeleton (`backend/app/{routers,services}`, `backend/{scraper,scripts}`, `backend/data/{text_content,pdfs,images}`, `frontend/src/{pages,components,lib}`)
2. Write `.gitignore` (exclude `.env`, venvs, `node_modules/`, and all of `data/` since it's regenerable)
3. Write `backend/requirements.txt` — every Python dependency, pinned
4. Write `backend/.env.example` and `backend/.env` — every tunable value, in one place
5. Write `app/config.py` — a `pydantic-settings` `Settings` class that reads `.env`, cached as a singleton via `@lru_cache`
6. Write `app/models.py` — every request/response Pydantic schema (this is the contract the frontend and backend agree on)

### Phase 2 — Services layer (the RAG logic itself)
Build these **in dependency order** — each one only needs the ones before it:
1. `services/embeddings.py` — wraps the Gemini embedding API with retry/backoff
2. `services/vectorstore.py` — wraps the Qdrant client: create collection, upsert, search, browse, delete
3. `services/chunker.py` — pure text functions: strip HTML, clean, chunk with overlap (no external API calls, so it's trivially unit-testable)
4. `services/ingest.py` — combines chunker + embeddings + vectorstore into one `_store()` helper, exposed as `ingest_text` / `ingest_pdf` / `ingest_image` / `ingest_url` / `ingest_directory`
5. `services/retriever.py` — combines embeddings + vectorstore into `retrieve()` (question → hits) and `build_context()` (hits → citable text block)
6. `services/llm.py` — wraps the Cohere API with the grounding/multilingual system prompt, plus streaming support

### Phase 3 — API surface
1. `app/security.py` — JWT creation/validation, constant-time password comparison
2. `app/routers/chat.py` — the two chat endpoints, built on `retriever` + `llm`
3. `app/routers/admin.py` — login + all four ingestion routes + browse/stats/delete, built on `ingest` + `vectorstore`
4. `app/main.py` — wires the routers into a FastAPI app, adds CORS, and runs `vectorstore.ensure_collection()` once at startup

### Phase 4 — Scripts
1. `scripts/smoke_test.py` — monkey-patches `embeddings.embed_one/embed_many` and `llm.generate` with fake, deterministic implementations, runs against an in-memory Qdrant (`QDRANT_URL=:memory:`), and exercises the entire pipeline plus every API endpoint — **zero real API calls, runs in under a second**
2. `scripts/ingest_all.py` — walks `data/` and bulk-ingests everything via `ingest.ingest_directory()`
3. `scripts/evaluate.py` + `eval_set.json` — runs real questions through the real pipeline and reports retrieval hit-rate / answer accuracy / latency

### Phase 5 — Scraper
1. `scraper/crawl.py` — a BFS crawler: aiohttp+BeautifulSoup for ordinary pages, Playwright as a lazy-imported fallback for JS-rendered ones. Writes `data/text_content/*.txt`, downloads `data/pdfs/*.pdf`, and builds `data/images/manifest.json` (filename + alt/caption + surrounding text for every image) — the manifest is exactly what `ingest.image_to_text()` consumes later.

### Phase 6 — Frontend
1. `lib/api.js` — the single module that calls `fetch()`; every other file goes through it. Includes manual SSE frame parsing (the browser's built-in `EventSource` only supports GET, and this needs to POST a JSON body).
2. `lib/useVoice.js` — Web Speech STT/TTS hooks; TTS picks its voice by detecting the Unicode script of the model's *reply* (not the question), since the LLM already decided the reply language.
3. `components/Message.jsx` + `ImageStrip.jsx` — one chat bubble (markdown, sources, images, listen button) and the related-images strip with a lightbox
4. `pages/Chat.jsx` — the main chat page: streams tokens into a message, shows suggestions when empty, wires up voice
5. `pages/AdminLogin.jsx` + `AdminPanel.jsx` — JWT login, then four ingestion tabs + a browse-data table
6. `main.jsx`, `vite.config.js`, `index.html`, `styles.css` — routing, dev-server proxy, and the visual theme

### Phase 7 — Make it real
This is the part that can't be done by writing files — it requires your
actual machine and real accounts. See the next two sections.

---

## 7. Environment setup

### 7.1 Prerequisites
- **Python 3.11+** — check with `python --version`. On Windows, the built-in `python` command is sometimes just a Microsoft Store stub; install the real thing from [python.org](https://www.python.org/downloads/) (or `winget install --id Python.Python.3.11`) and make sure "Add to PATH" is checked.
- **Node.js 18+** and npm
- **Git**
- A way to run **Qdrant** — either:
  - **Qdrant Cloud** (recommended if Docker isn't already installed): a free-tier cluster at [cloud.qdrant.io](https://cloud.qdrant.io), no local install needed
  - **Docker Desktop** + the provided `docker-compose.yml`, for a fully offline setup (better for a demo on an unreliable network)

### 7.2 Get your API keys (all free)

| Key | Where | Notes |
|---|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Sign in with Google, "Create API key" |
| `COHERE_API_KEY` | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) | Free trial key, created automatically on signup |
| Qdrant URL + API key | [cloud.qdrant.io](https://cloud.qdrant.io) | Sign up, create a free-tier cluster, copy the cluster URL and API key |

Paste all of these into `backend/.env` (never commit this file — it's
gitignored on purpose). Also generate a random `JWT_SECRET`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 7.3 Install dependencies

**Backend:**
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
playwright install chromium     # only needed for JS-rendered pages during scraping
```

**Frontend:**
```bash
cd frontend
npm install
```

---

## 8. Running and verifying

### 8.1 Zero-cost sanity check
Before spending a single API call, prove the whole pipeline is wired
correctly:
```bash
cd backend
python -m scripts.smoke_test
```
Expect `40/40 checks passed`. This runs against an in-memory Qdrant with
fake embeddings/LLM — safe to run as often as you like.

### 8.2 Start the backend for real
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```
Check `http://localhost:8000/api/health` — should report
`{"status": "ok", "qdrant": "connected", ...}`.

### 8.3 Build the real knowledge base
```bash
cd backend
python -m scraper.crawl --max-pages 150     # scrapes the live site into data/
python -m scripts.ingest_all                # embeds everything into Qdrant
```
See [§9.1](#91-free-tier-embedding-rate-limits) if this crashes partway —
it almost certainly will on a large first run, and that section explains
exactly why and how to resume without duplicating data.

### 8.4 Start the frontend
```bash
cd frontend
npm run dev
```
Open `http://localhost:5173`. The green "● Online" dot confirms the
frontend can reach the backend.

### 8.5 Measure quality
```bash
cd backend
python -m scripts.evaluate --file scripts/eval_set.json
```
Prints retrieval hit-rate, answer accuracy, and mean latency — the numbers
that belong in a results chapter.

**Real measured results on this deployment** (30 questions in
`eval_set.json`, every `must_contain` keyword pre-verified against the
actual scraped data — see `git log` for the commit that built this set —
`TOP_K=8`, real Gemini + Cohere, real CBIT data):

| Metric | Result |
|---|---|
| Retrieval hit-rate | 28/30 (93.3%) |
| Answer accuracy | 29/30 (96.7%) |
| Mean latency | 11.57s |

The one miss was a phrasing mismatch, not a defect: asked for the
"eligibility criteria," the model correctly explained rank-based admission
via TGEAPCET but never used the literal word "eligibility."

**Ablation — why `TOP_K=8`, not a higher value:** raising `TOP_K` to 12 was
tested and made things *worse* — answer accuracy dropped to 90.0% (27/30)
for the *same* retrieval hit-rate and near-identical latency.

| Config | Retrieval hit-rate | Answer accuracy | Mean latency |
|---|---|---|---|
| `TOP_K=8` (kept) | 93.3% | **96.7%** | 11.57s |
| `TOP_K=12` | 93.3% | 90.0% | 11.74s |

Two of the three `TOP_K=12` regressions weren't wrong facts — they were
the model correctly answering an *English* question in *Hindi*. Pulling in
more chunks occasionally drags in a non-English source snippet, and that
snippet's language appears to influence the model's language-matching
decision more than the question's own language does. This is a genuine,
reportable finding: more context isn't free — it can measurably *reduce*
answer quality by introducing noise, even when it doesn't change what gets
retrieved.

---

## 9. Known gotchas and how we solved them

These are real problems we hit while building and running this exact
project — not hypotheticals. Knowing them (and why the fix works) is good
viva material.

### 9.1 Free-tier embedding rate limits

**What happened:** `ingest_all.py` crashed with `429 RESOURCE_EXHAUSTED`
after successfully ingesting 116 of 149 text files. A second attempt crashed
again almost immediately.

**Root cause:** one single page (`examination-notification.txt`) produced
**61 chunks**. `ingest_directory()` calls `ingest_text()` file-by-file, and
`embed_many()` embeds all of a file's chunks with `concurrency=4` — so this
one file alone fired dozens of embedding requests in quick succession,
instantly blowing through the free tier's actual requests-per-minute
ceiling (which turned out to be much stricter than the per-file retry
backoff was designed to absorb).

**Why the built-in retry didn't save it:** `embeddings.py`'s backoff
(5 attempts, starting at 0.3s and doubling) is designed for *brief* rate
limiting — a few requests arriving too close together. It's too short to
wait out a *sustained* per-minute quota wall, so it gives up and raises.

**The fix that worked:** a one-off recovery script that:
1. Computed exactly which files were already ingested (by parsing the crash
   log) vs. what was still missing — so nothing gets duplicated by
   re-processing files that already succeeded (there's no dedup: Qdrant
   point IDs are random UUIDs, not keyed on content, so re-ingesting a file
   creates brand-new duplicate chunks rather than overwriting).
2. Monkey-patched `embeddings.embed_many` to embed chunks **sequentially**
   with a deliberate delay between every single call, instead of the
   default concurrent behaviour.
3. Wrapped each file in its own `try/except` so one stubborn failure logs a
   warning and moves on, instead of crashing the entire batch.

**A known gap this exposes:** `ingest_all.py` / `ingest_directory()` has no
resume or dedup logic built in. If you rerun it after a crash, it *will*
duplicate every file processed before the crash. If you extend this
project, consider: lowering `embed_many`'s default concurrency, adding a
fixed delay between calls, or checking for an existing `doc_id` before
re-embedding a file.

### 9.2 A `.gitignore` ordering bug silently dropped a tracked file

**What happened:** `backend/data/images/.gitkeep` was created on disk in
Phase 1, but `git status` never showed it as untracked *or* committed — it
had simply vanished from git's view.

**Root cause:** the original `.gitignore` had:
```gitignore
backend/data/text_content/*
backend/data/pdfs/*
!backend/data/**/.gitkeep      # un-ignore .gitkeep everywhere
# keep the image manifest layout but not the downloaded binaries
backend/data/images/*          # <- this re-ignores images/.gitkeep, since it comes AFTER
!backend/data/images/manifest.json
```
Gitignore rules apply in file order, and a later rule can re-exclude
something an earlier rule un-excluded. The `images/*` line came *after* the
`.gitkeep` exception, silently swallowing it back up.

**The fix:** reorder so every ignore rule comes before the single `.gitkeep`
exception, and drop the `manifest.json` exception entirely (it contradicted
the project's own rule that all scraper output is regenerable and should
never be committed).
```gitignore
backend/data/text_content/*
backend/data/pdfs/*
backend/data/images/*
!backend/data/**/.gitkeep
```

**Lesson:** in `.gitignore`, rule *order* matters as much as the patterns
themselves — always put broad excludes before the specific exceptions that
should survive them, and double-check with `git check-ignore -v <path>`.

### 9.3 The response cache matched across languages

**What happened:** found during Day 6 multilingual testing. Asking "Where
is CBIT located?" in Hindi, then the identical question in Telugu, returned
the *Hindi* answer for the Telugu question — the reply didn't even change
script.

**Root cause:** `services/cache.py`'s semantic tier (§5.4) matches on
cosine similarity between question embeddings alone. Gemini's embedding
model places the same *meaning* very close together in vector space
regardless of language — "Where is CBIT?" in Hindi and in Telugu land well
above the 0.97 similarity threshold — so the cache had no way to tell them
apart and served whichever answer was cached first.

**The fix:** bucket the semantic cache by the question's detected Unicode
script (Telugu / Devanagari / Latin) — see `_script_bucket()` — so the
cosine comparison only ever runs *within* a bucket. A Telugu question can
now only match a previously-cached *Telugu* question, never a Hindi one.

**Known, accepted limitation:** Hinglish and Tinglish are both written in
Latin script, so this doesn't (and without real language detection,
can't) separate those two from each other. Documented rather than hidden —
see the module docstring in `cache.py`.

**A debugging detour worth knowing about:** chasing this down initially
looked like the fix hadn't worked — typing Devanagari/Telugu Unicode
directly into a `curl -d` command via Bash on Windows silently corrupts
the bytes before they reach the server (both scripts collapsed into the
same corrupted bytes, so they still collided even after the real fix was
in place). Confirmed the fix was correct all along using a plain Python
`urllib` client that sends a proper UTF-8 JSON body. If you're ever
testing non-ASCII input from a Windows terminal and results look wrong in
a way the code doesn't explain, suspect the terminal before the code.

### 9.4 A blank message crashed the server, and errors were unreadable

**What happened:** found while working through the ROADMAP's Day 9
hardening checklist. A message of only whitespace (`"   "`) passed
`ChatRequest`'s `min_length=1` check (it counts raw characters, not
meaningful content), reached `embeddings.embed_one()`, and hit a bare
`raise ValueError("Cannot embed empty text")` — uncaught anywhere in the
router, surfacing as an unhandled 500.

**The fix:** a Pydantic `field_validator` on `ChatRequest.message` rejects
a message that's blank *after stripping*, at the same request-validation
boundary as the existing empty-string check — so it's a clean 422 like
every other bad input, not a crash.

**A second, related gap this surfaced:** FastAPI/Pydantic validation
failures return `{"detail": [{"msg": ..., "loc": ...}, ...]}` — a list of
objects, not a plain string. The frontend's shared `handle()` helper
(`lib/api.js`) only handled the plain-string shape, so an `Error` built
from the list rendered as `[object Object]`. Worse, `chatStream()` — the
function the *actual chat page* uses — didn't call `handle()` at all, so
any failure there showed a bare `"Stream failed (422)"` with no real
reason. Both now share one `errorDetail()` helper that unpacks either
shape. Verified live: pasting 5000 characters into the chat composer (the
`<input>` has no `maxLength`, so this is genuinely reachable by a real
user, not just a curl test) now shows "String should have at most 4000
characters" instead of a dead end.

**Lesson:** a length check on raw character count and a length check on
*meaningful* content are different checks — `min_length=1` stops an empty
string, not a whitespace one. And a generic catch-all error message at an
API boundary is only as good as its weakest caller; here, one of the two
call sites bypassed the shared error-handling helper entirely.

---

## 10. File-by-file reference

Every backend file has a module-level docstring explaining its purpose and
which report section (if applicable) it corresponds to — read the source
directly for the authoritative, always-up-to-date explanation. The table
below is a map to help you find the right file fast.

| I want to change... | File |
|---|---|
| How many chunks feed the LLM, or the similarity threshold | `backend/app/config.py` (`TOP_K`, `SCORE_THRESHOLD` in `.env`) |
| Chunk size or overlap | `backend/app/config.py` (`CHUNK_SIZE`, `CHUNK_OVERLAP`) |
| The system prompt / grounding rules / multilingual behaviour | `backend/app/services/llm.py` (`SYSTEM_PROMPT`) |
| How images are turned into searchable text | `backend/app/services/ingest.py` (`image_to_text()`) |
| What counts as "same site" during crawling, or the page budget | `backend/scraper/crawl.py` |
| The FIRST superadmin's bootstrap credentials, or token lifetime | `backend/.env` (`ADMIN_EMAIL`, `ADMIN_PASSWORD` — first boot only, `JWT_EXPIRE_MINUTES`) |
| Who can create/reset/delete admin accounts | `backend/app/security.py` (`require_superadmin`), `backend/app/services/users.py` |
| Admin account storage (roles, password hashes) | `backend/app/services/users.py`, `backend/instance/admin.db` |
| The chat UI itself | `frontend/src/pages/Chat.jsx` |
| The Admins tab / change-password UI | `frontend/src/pages/AdminPanel.jsx` |
| How voice input/output works | `frontend/src/lib/useVoice.js` |
| The visual theme | `frontend/src/styles.css` |
| Which Qdrant instance is used (local vs. cloud) | `backend/.env` (`QDRANT_URL`, `QDRANT_API_KEY`) |
| How many API calls can be in flight at once | `backend/.env` (`GEMINI_MAX_CONCURRENCY`, `COHERE_MAX_CONCURRENCY`) |
| Response caching (on/off, TTL, similarity threshold) | `backend/.env` (`CACHE_ENABLED`, `CACHE_TTL_SECONDS`, `CACHE_SEMANTIC_THRESHOLD`), `backend/app/services/cache.py` |
| Per-IP chat rate limit | `backend/.env` (`CHAT_RATE_LIMIT_PER_MINUTE`), `backend/app/ratelimit.py` |
| Sharing the cache/rate-limiter across multiple worker processes | `backend/.env` (`REDIS_URL`), `backend/app/services/redisclient.py` |
| How long a hung API call can block before it's abandoned | `backend/.env` (`API_CALL_TIMEOUT_SECONDS`) |
| When a dependency outage should trip the circuit breaker | `backend/.env` (`CIRCUIT_BREAKER_THRESHOLD`, `CIRCUIT_BREAKER_COOLDOWN_SECONDS`), `backend/app/services/circuitbreaker.py` |
| Plain vs. JSON structured logs | `backend/.env` (`LOG_FORMAT`), `backend/app/logging_config.py` |
| How requests get their ID / what the access log records | `backend/app/middleware.py` |
| What's tracked in `/metrics` | `backend/app/services/metrics.py` |

For the full annotated source, start at `backend/app/main.py` and follow the
imports outward — every file was written with generous inline comments
explaining *why*, not just *what*.
