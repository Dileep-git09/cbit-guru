# CBIT Guru — AI-Powered Chat Assistant for CBIT

[![CI](https://github.com/Dileep-git09/cbit-guru/actions/workflows/ci.yml/badge.svg)](https://github.com/Dileep-git09/cbit-guru/actions/workflows/ci.yml)

A Retrieval-Augmented Generation (RAG) chat assistant that answers questions about
Chaitanya Bharathi Institute of Technology from the institute's own website, PDFs
and notices — with source citations, related campus images, voice input/output and
multilingual replies.

This repository is the working implementation of the major project report
*"AI-Powered Chat Assistant for CBIT"* (Dept. of AI & DS, CBIT, April 2026).

---

## Architecture

```
                        ┌──────────────────────────────┐
  React frontend        │  Chat UI · Voice · Admin      │
  (Vite, port 5173)     └───────────────┬──────────────┘
                                        │ /api/*
                        ┌───────────────▼──────────────┐
  FastAPI backend       │  Orchestrator                 │
  (port 8000)           │  chat · admin · ingestion     │
                        └───┬───────────────────────┬──┘
                            │                       │
              ┌─────────────▼──────┐      ┌─────────▼────────────┐
              │ Gemini embeddings  │      │  Cohere LLM          │
              │ gemini-embedding-  │      │  command-r           │
              │ 001 · 3072 dims    │      │  (generator)         │
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

| Layer | Choice | Why (report §3.4.4) |
|---|---|---|
| Embeddings | `gemini-embedding-001`, 3072-d | Highest-dimensional free-tier embedding; captures fine semantic distinctions |
| Vector DB | Qdrant | Open source, HNSW, metadata filtering, runs locally or in cloud |
| Similarity | Cosine | Magnitude-invariant; correct choice for high-dimensional text vectors |
| Generator | Cohere `command-r` | Purpose-built for RAG grounding; cheap; strong multilingual |
| Backend | FastAPI | Async — many concurrent students without blocking |
| Frontend | React + Vite | Component model suits chat history, voice, admin panel |

---

## Quick start

### 0. Prerequisites
- Python 3.11+
- Node 18+
- A Qdrant instance — Qdrant Cloud (free tier) or local Docker

### 1. Get API keys (both free)
| Key | Where | Free tier |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | generous daily embedding quota |
| `COHERE_API_KEY` | https://dashboard.cohere.com/api-keys | trial key, 1000 calls/month |

### 2. Qdrant
Either sign up for a free cluster at https://cloud.qdrant.io and paste the URL +
API key into `backend/.env`, or run locally:
```bash
docker compose up -d
# dashboard: http://localhost:6333/dashboard
```

### 3. Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # only needed for JS-rendered pages

cp .env.example .env               # then fill in your keys (or edit .env directly)
uvicorn app.main:app --reload --port 8000
```
API docs: http://localhost:8000/docs

### 4. Build the knowledge base
```bash
cd backend
python -m scraper.crawl --max-pages 80     # writes into data/
python -m scripts.ingest_all               # embeds + upserts into Qdrant
```
First run takes 10–30 minutes depending on quota. Add `--reset` to `ingest_all`
to wipe and rebuild the collection.

### 5. Frontend
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Admin panel: http://localhost:5173/admin
(credentials come from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `backend/.env`)

---

## Verify without spending API calls

```bash
cd backend
python -m scripts.smoke_test
```
Runs the whole pipeline against an in-memory Qdrant with stubbed embedding and
LLM calls — 43 assertions covering ingestion, retrieval, image retrieval, multi-admin auth/roles, caching, rate limiting, circuit breaker, observability & health probes
and every endpoint. Use it whenever you change the pipeline.

This same command (plus `ruff check .` for linting, and a frontend
`npm run build`) runs automatically on every push and pull request —
see `.github/workflows/ci.yml`. To run the lint locally:
```bash
cd backend
pip install -r requirements-dev.txt
ruff check .
```

## Measure quality for the results chapter

```bash
cd backend
python -m scripts.evaluate --file scripts/eval_set.json
```
Prints retrieval hit-rate, answer accuracy and mean latency. Edit
`scripts/eval_set.json` to add your own question/keyword pairs.

---

## Deploying for real users

Step-by-step guide to putting this in front of real students on free
infrastructure (Render backend + Vercel frontend, alongside the Qdrant
Cloud/Gemini/Cohere already used in dev): [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Keeping the knowledge base current after that without manual re-ingestion
is planned (not yet built) in [docs/FUTURE_SCOPE_AUTO_REFRESH.md](docs/FUTURE_SCOPE_AUTO_REFRESH.md).

---

## Project layout

```
cbit-guru/
├── backend/
│   ├── requirements.txt       pinned dependencies
│   ├── .env / .env.example    secrets (gitignored) / template (safe to commit)
│   ├── instance/admin.db      SQLite admin accounts — gitignored, has password hashes
│   ├── app/
│   │   ├── main.py            FastAPI app, CORS, lifespan, /api/health(/live|/ready), /metrics
│   │   ├── config.py          all settings, read from .env
│   │   ├── models.py          request/response schemas
│   │   ├── security.py        admin JWT, require_admin/require_superadmin
│   │   ├── ratelimit.py       per-IP limiter on /api/chat* (in-memory or Redis-backed)
│   │   ├── logging_config.py  structured plain/JSON logging + request-id propagation
│   │   ├── middleware.py      RequestContextMiddleware: request IDs, access logs, HTTP metrics
│   │   ├── routers/
│   │   │   ├── chat.py        POST /api/chat  and  /api/chat/stream (SSE)
│   │   │   └── admin.py       login, ingest text/file/URL, browse, stats, admin-user CRUD
│   │   └── services/
│   │       ├── embeddings.py     Gemini, 3072-d, timeout + circuit breaker + 429 backoff
│   │       ├── vectorstore.py    Qdrant collection, upsert, search, browse, ping()
│   │       ├── chunker.py        HTML strip, clean, chunk with overlap
│   │       ├── retriever.py      text + image retrieval, context fusion
│   │       ├── llm.py            Cohere generate + stream, system prompt
│   │       ├── ingest.py         text / PDF / image / URL / folder walk
│   │       ├── users.py          SQLite admin accounts: bcrypt hashes, roles, ping()
│   │       ├── cache.py          exact + semantic response cache (in-memory or Redis)
│   │       ├── circuitbreaker.py fail-fast during a real Gemini/Cohere outage
│   │       ├── redisclient.py    optional shared backend for cache.py + ratelimit.py
│   │       └── metrics.py        Prometheus counters/histograms for GET /metrics
│   ├── scraper/crawl.py       aiohttp + BeautifulSoup + Playwright crawler
│   ├── scripts/
│   │   ├── ingest_all.py      bulk-embed data/ into Qdrant
│   │   ├── smoke_test.py      offline end-to-end verification (43 checks)
│   │   ├── evaluate.py        accuracy + latency harness
│   │   └── eval_set.json      test questions
│   └── data/{text_content,pdfs,images}/
├── frontend/
│   └── src/
│       ├── pages/{Chat,AdminLogin,AdminPanel}.jsx
│       ├── components/{Message,ImageStrip}.jsx
│       ├── lib/{api.js,useVoice.js}
│       └── styles.css
├── docs/BUILD_GUIDE.md        architecture, pipelines, gotchas — the deep-dive reference
├── docker-compose.yml
├── ROADMAP.md                 ← 12-day build plan, read this next
└── README.md
```

---

## How the interesting bits work

**Multimodal retrieval without a vision model.** Each image is stored as a *text*
vector built from its filename slug (`boys_hostel_mess.jpg` → "boys hostel mess"),
its `alt`/caption, and the surrounding page copy. A question like "show me the
hostel mess" therefore matches the image in the same cosine search that matches
text. See `ingest.image_to_text()`.

**Multilingual with zero extra models.** The Cohere system prompt instructs the
model to detect the language *and script* of the question and reply in the same
one — English, Devanagari Hindi, Telugu script, Hinglish, Tinglish. No translation
step, no per-language model.

**Rate-limit resilience.** Both the embedder and the generator retry up to 5 times
with exponential backoff starting at 0.3 s on HTTP 429, so a long ingestion run
survives free-tier quota bursts.

**Grounding.** The system prompt forbids answering outside the retrieved context
and requires inline `[n]` citations. The frontend shows a collapsible Sources list
with cosine scores so a viva examiner can see exactly what the answer came from.

**Multi-admin, role-based auth.** SQLite-backed admin accounts (bcrypt hashes,
never plaintext) with two roles — `admin` can ingest/browse data, only
`superadmin` can create/reset/delete other admin accounts. See `services/users.py`.

**Response caching for many concurrent students.** Repeat and near-repeat
questions are answered from a two-tier cache (exact string match, then
cosine-similarity paraphrase matching bucketed by script) instead of paying for
a fresh Gemini + Cohere round trip every time. See `services/cache.py`.

**Resilience under load or outage.** A process-wide concurrency cap on Gemini/
Cohere calls stops a burst of simultaneous questions from instantly exhausting
the shared free-tier quota; per-call timeouts stop one hung request from
starving that cap forever; a circuit breaker fails fast during a real outage
instead of every request paying a futile retry cost. See `services/embeddings.py`,
`services/llm.py`, `services/circuitbreaker.py`, and `app/ratelimit.py`.

**Observability.** Structured logs (plain or JSON) carry a request ID through
every log line for one request — including third-party libraries — for tracing
a single request end to end; `GET /metrics` exposes Prometheus-format counters;
`GET /api/health/live` and `/api/health/ready` give an orchestrator two separate,
correct signals instead of one conflated one. See `app/logging_config.py`,
`app/middleware.py`, `services/metrics.py`, and `docs/BUILD_GUIDE.md` §5.5–5.6.

---

## License / academic use

Built as a B.E. major project at CBIT, Hyderabad. Respect `robots.txt` and the
institute's terms when crawling; the scraper identifies itself and stays on-domain.

Licensed under the [MIT License](LICENSE) — use, fork, and modify freely, with
attribution. Note this covers the code only: CBIT's own website content isn't
redistributed here (`backend/data/` is gitignored; you generate it yourself by
running the scraper against a source you're authorized to crawl).
