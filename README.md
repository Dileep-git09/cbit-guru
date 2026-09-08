# CBIT Guru — AI-Powered Chat Assistant for CBIT

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
LLM calls — 38 assertions covering ingestion, retrieval, image retrieval, multi-admin auth/roles, caching, rate limiting & circuit breaker
and every endpoint. Use it whenever you change the pipeline.

## Measure quality for the results chapter

```bash
cd backend
python -m scripts.evaluate --file scripts/eval_set.json
```
Prints retrieval hit-rate, answer accuracy and mean latency. Edit
`scripts/eval_set.json` to add your own question/keyword pairs.

---

## Project layout

```
cbit-guru/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app + CORS + lifespan
│   │   ├── config.py          all settings, read from .env
│   │   ├── models.py          request/response schemas
│   │   ├── security.py        admin JWT
│   │   ├── routers/
│   │   │   ├── chat.py        POST /api/chat  and  /api/chat/stream (SSE)
│   │   │   └── admin.py       login, ingest text/file/URL, browse, stats
│   │   └── services/
│   │       ├── embeddings.py  Gemini, 3072-d, 429 backoff (5 tries, 0.3s base)
│   │       ├── vectorstore.py Qdrant collection, upsert, search, browse
│   │       ├── chunker.py     HTML strip, clean, chunk with overlap
│   │       ├── retriever.py   text + image retrieval, context fusion
│   │       ├── llm.py         Cohere generate + stream, system prompt
│   │       └── ingest.py      text / PDF / image / URL / folder walk
│   ├── scraper/crawl.py       aiohttp + BeautifulSoup + Playwright crawler
│   ├── scripts/
│   │   ├── ingest_all.py      bulk-embed data/ into Qdrant
│   │   ├── smoke_test.py      offline end-to-end verification
│   │   ├── evaluate.py        accuracy + latency harness
│   │   └── eval_set.json      test questions
│   └── data/{text_content,pdfs,images}/
├── frontend/
│   └── src/
│       ├── pages/{Chat,AdminLogin,AdminPanel}.jsx
│       ├── components/{Message,ImageStrip}.jsx
│       ├── lib/{api.js,useVoice.js}
│       └── styles.css
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

---

## License / academic use

Built as a B.E. major project at CBIT, Hyderabad. Respect `robots.txt` and the
institute's terms when crawling; the scraper identifies itself and stays on-domain.
