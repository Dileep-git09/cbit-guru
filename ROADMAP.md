# CBIT Guru — 12-Day Build Map

You have under two weeks. This plan gets you from an empty machine to a
demo-ready system with numbers for your results chapter, with the risky work
front-loaded so nothing can ambush you in the last three days.

The scaffold in this repo already implements everything below. Your job is to
**run it, fill it with real CBIT data, verify it, and be able to explain every
line in the viva.** That last part is why each day has a "understand this"
column — do not skip it.

---

## The one rule

> **Get one real question answered from real CBIT data by end of Day 3.**

Everything else is polish. A system that answers "Where is CBIT located?"
correctly from the scraped site is a passing project. A beautiful UI with no
working retrieval is not.

---

## Day 0 (tonight, ~1 hour) — Accounts and keys

Do this before anything else, because key approval can lag.

| Task | Where | Notes |
|---|---|---|
| Gemini API key | https://aistudio.google.com/apikey | Free. Used for `gemini-embedding-001` at 3072 dims. |
| Cohere API key | https://dashboard.cohere.com/api-keys | Free trial key. Used for the generator. |
| GitHub repo | github.com/new | Private, name it `cbit-guru`. Push the scaffold today. |
| Docker Desktop | docker.com | Needed to run Qdrant locally. |

**Create the repo and push:**
```bash
cd cbit-guru
git init
git add .
git commit -m "Initial scaffold: FastAPI + Qdrant + React RAG assistant"
git branch -M main
git remote add origin https://github.com/<you>/cbit-guru.git
git push -u origin main
```

⚠️ `backend/.env` is gitignored. Never commit your keys — examiners look, and a
leaked key gets your quota drained.

---

## Day 1 — Get the skeleton running locally

**Goal: `http://localhost:5173` loads and the green "Online" dot is lit.**

```bash
# 1. Vector database
docker compose up -d
curl http://localhost:6333/healthz          # expect: healthz check passed

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # paste your two API keys + set ADMIN_PASSWORD
python -c "import secrets; print(secrets.token_hex(32))"   # paste as JWT_SECRET
uvicorn app.main:app --reload --port 8000

# 3. Prove the wiring without burning quota
python -m scripts.smoke_test                # expect 43/43 passed

# 4. Frontend (new terminal)
cd frontend && npm install && npm run dev
```

**Understand this today:** open `backend/app/main.py` and trace one request.
Frontend `send()` → `POST /api/chat/stream` → `chat.py` → `retriever.retrieve()`
→ `embeddings.embed_query()` → `vectorstore.search()` → `llm.generate_stream()`.
Draw that path on paper. That drawing is your viva answer to "explain your
architecture".

**Commit:** `git commit -am "Day 1: local environment running"`

---

## Day 2 — Scrape CBIT

**Goal: `backend/data/` full of real CBIT content.**

```bash
cd backend
python -m scraper.crawl --max-pages 20 --no-playwright   # quick smoke run first
ls data/text_content/                                     # should have ~20 .txt files
head -50 data/text_content/*.txt                          # eyeball the quality
```

If pages look empty or garbled, the site is rendering with JavaScript. Then:
```bash
playwright install chromium
python -m scraper.crawl --max-pages 80        # Playwright fallback kicks in automatically
```

**Then go wide:**
```bash
python -m scraper.crawl --max-pages 150
```
Expect roughly: 100–150 text files, 20–60 PDFs, 200+ image entries in
`data/images/manifest.json`.

**Manually add what the crawler misses.** Drop into `data/pdfs/` any PDF you have
that isn't linked from the site — academic calendar, fee structure, exam
timetable, hostel rules, placement brochure. These are exactly the documents
students actually ask about, and hand-added PDFs are the cheapest accuracy win
in this whole project.

**Understand this today:** `scraper/crawl.py` → `extract()`. Specifically, look
at how the image loop grabs `alt` text and the parent element's text. That is the
mechanism behind your report's "multimodal handling" claim — be ready to explain
that you represent images *as text*, and that no vision model is involved.

**Commit:** `git commit -am "Day 2: crawler tuned, data collected"` (the data
folder itself is gitignored — that's intentional, it's regenerable.)

---

## Day 3 — First real answer ⭐ THE MILESTONE

**Goal: ask a real question, get a real grounded answer.**

```bash
cd backend
python -m scripts.ingest_all --reset
```

Watch the log. This is where free-tier quota bites — the 429 backoff will handle
it, but a 150-page ingest can take 20–40 minutes. Let it run. If it dies, rerun
without `--reset` and it continues.

Then check Qdrant's dashboard (`http://localhost:6333/dashboard`) — you should see
a few thousand points in `cbit_guru_rag`.

Now open the chat UI and ask:
- "Where is CBIT located?"
- "What courses does the AI and DS department offer?"
- "Who is the head of the department?"

**Expand the Sources dropdown on every answer.** If the sources are irrelevant,
you have a *retrieval* problem, not an LLM problem — that distinction is worth
marks in the viva.

### If answers are bad, fix in this order
1. **Sources are irrelevant** → chunks are too big or too small. Try
   `CHUNK_SIZE=800` / `CHUNK_OVERLAP=150` in `.env`, re-ingest.
2. **Sources are right but answer is vague** → raise `TOP_K` to 12.
3. **Model says "I don't know" too often** → lower `SCORE_THRESHOLD` to 0.25.
4. **Model invents things** → raise `SCORE_THRESHOLD` to 0.45 and tighten the
   grounding rules in `llm.py` `SYSTEM_PROMPT`.

**Understand this today:** `services/chunker.py`. Why overlap exists (a fact
split across a chunk boundary is otherwise unfindable), and why cosine similarity
rather than Euclidean (report §3.4.4 — magnitude invariance in high dimensions).

**Commit + take a screenshot.** This screenshot goes in your report.

---

## Day 4 — Admin panel and live ingestion

**Goal: add knowledge without touching the terminal.**

Go to `/admin`, log in, and try all four tabs:
- **Text** — paste an exam notice, ingest, then ask the chat about it. This
  round-trip is the single most impressive thing to demo live.
- **File** — upload a PDF from your own machine.
- **URL** — paste a CBIT page URL.
- **Browse Data** — confirm chunks appear with correct source pills.

**Understand this today:** `security.py` + `services/users.py`. Why the admin
route is JWT-protected, why passwords are hashed with bcrypt (via passlib)
rather than compared or stored as plaintext, and why there are two roles —
`admin` and `superadmin` — with only the latter able to create/reset/delete
admin accounts (so one compromised or careless staff login can't be used to
mint new admin accounts). An examiner asking "how did you secure it?" is a
gift — have this answer ready.

---

## Day 5 — Images and voice

**Goal: the two features that make examiners lean forward.**

**Images:** ask "show me the hostel", "campus facilities", "CBIT library". The
Related Images strip should appear. If it doesn't:
- Check `data/images/manifest.json` has entries with non-empty `caption` or
  `surrounding`.
- Lower `SCORE_THRESHOLD` — image text is short, so its scores run lower.
- Raise `IMAGE_TOP_K` to 8.

**Voice:** use **Chrome or Edge** (Firefox lacks Web Speech API). Click 🎙, speak
a question, confirm it transcribes and auto-sends. Toggle **Auto-speak** and
confirm the answer is read aloud.

⚠️ Voice needs `https://` or `localhost`. If you demo from a phone on the campus
network over plain HTTP, the mic will be blocked. Demo from the laptop.

**Understand this today:** `lib/useVoice.js`, specifically `detectLang()` — it
picks the TTS voice by inspecting the Unicode range of the reply. That is a
concrete, explainable answer to "how does voice handle Telugu?"

---

## Day 6 — Multilingual

**Goal: reproduce Figure 3.13 from your report, live.**

It's already implemented via the system prompt in `llm.py`. Test all five rows:

| Ask | Expect |
|---|---|
| `Where is CBIT located?` | English |
| `CBIT kaha par hai?` | Hinglish (Roman script) |
| `CBIT ekkada undi?` | Tinglish (Roman script) |
| `CBIT कहाँ है?` | Hindi, Devanagari |
| `CBIT ఎక్కడ ఉంది?` | Telugu script |

If it replies in the wrong script, strengthen the LANGUAGE RULES block in
`SYSTEM_PROMPT` with an explicit example for the failing case. Screenshot all
five — that grid is a figure in your report.

**Understand this today:** be ready for "why didn't you use a translation model?"
Answer: modern instruction-tuned LLMs are natively multilingual, so prompt
conditioning gives script-faithful replies at zero extra latency, zero extra
cost, and no translation-drift errors.

---

## Day 7 — Buffer / catch-up day

Do not schedule anything. Something from Days 1–6 will have overrun — quota
limits, a stubborn page, a Windows path bug. This day absorbs it.

If you are genuinely ahead, use it to widen the knowledge base: more PDFs, more
pages, department-specific sub-sites.

---

## Day 8 — Evaluation and numbers

**Goal: real metrics for Chapter 3.5 instead of adjectives.**

Edit `backend/scripts/eval_set.json` — write **25–30** questions you know the
right answer to, each with keywords that must appear. Cover: location, courses,
faculty names, fees, hostel, placements, events, admissions, and 4–5 non-English
questions.

```bash
cd backend
python -m scripts.evaluate --file scripts/eval_set.json
```

You get retrieval hit-rate, answer accuracy, and mean latency. Run it, tune one
parameter (`TOP_K`, `CHUNK_SIZE`, `SCORE_THRESHOLD`), run it again, and **record
both runs**. A before/after table showing you measured and improved something is
worth more than a high score you can't explain.

Also record a small ablation for the report:
| Configuration | Accuracy | Latency |
|---|---|---|
| Keyword search baseline (no embeddings) | | |
| RAG, top-k = 4 | | |
| RAG, top-k = 8 | | |
| RAG, top-k = 12 | | |

That table alone answers "how do you know RAG helped?"

---

## Day 9 — Hardening

Things that break in front of examiners, in the order they break:

1. **API quota dies mid-demo.** Get a second Gemini key on a different Google
   account, keep it in a comment in `.env`, ready to swap.
2. **Wi-Fi dies.** Record a 3-minute screen capture of the working demo *today*.
   Play it if the network fails. This has saved more projects than any code fix.
3. **Cold start is slow.** Warm the system with one question before your slot.
4. **Long answers get cut off.** Test a "tell me everything about CBIT" question.
5. **Empty input, 5000-char input, emoji-only input.** Click through them all.

Also set `ADMIN_PASSWORD` to something real, and confirm `.env` is not in
`git ls-files`.

---

## Day 10 — Report alignment

Walk through the report chapter by chapter and make the code match it — or
update the report where the code turned out better. Examiners cross-check.

| Report says | Verify |
|---|---|
| §3.4.1 folder structure `scraper/output` | Repo uses `backend/data/` — **update the report** to match, or rename the folder |
| §3.4.1 "no chunking, whole file at once" | The code *does* chunk with overlap. Update the report — chunking is the better design and you should say why |
| §3.4.2 3072-dim Gemini vectors | `EMBEDDING_DIM=3072` in `.env` ✓ |
| §3.4.2 collection `cbit_guru_rag` | ✓ |
| §3.4.2 metadata `user_id`, `doc_id`, `chunk_index` | ✓ in `vectorstore.upsert_chunks` |
| §3.4.1 five retries, 0.3 s exponential backoff | ✓ in `embeddings.py` |
| §3.4.1 PDF cap 50,000 chars | ✓ `MAX_PDF_CHARS` |
| §3.4.1 UTF-8 with fallback encoding | ✓ `chunker.read_text_file` |
| Figure 3.8 "Total Rows in Qdrant" | ✓ admin stats card |

Regenerate every screenshot from the *current* build so figures match reality.

---

## Day 11 — Demo script

Write and rehearse a 5-minute run. Suggested order:

1. **Landing page** (10 s) — "This is CBIT Guru, it answers from CBIT's own data."
2. **Simple factual question** (30 s) — expand Sources, point at the cosine
   scores. *"Notice it cites the exact page it came from."*
3. **Image question** (30 s) — "show me the hostel facilities".
4. **Voice question** (45 s) — speak it, let it speak back.
5. **Multilingual** (45 s) — ask the same question in Telugu and Hindi.
6. **Admin panel** (90 s) — paste a notice that isn't on the website, ingest it,
   go back to chat, ask about it. **This is your closing move.** It proves the
   knowledge base is live, not hardcoded.
7. **Hallucination guard** (30 s) — ask something CBIT-adjacent but unknowable
   ("what is the exact hostel fee for 2027?"). Show that it declines rather than
   invents. *This is the slide most projects fail on.*

Rehearse it three times with a timer.

---

## Day 12 — Freeze

- Final commit, tag it: `git tag v1.0 && git push --tags`
- Push the repo, add a `README` screenshot at the top
- Export the 3-minute backup video to your laptop **and** a pen drive
- Print or PDF the architecture diagram
- Charge everything

Do not add features today. Every project that breaks at the viva broke because
someone "just fixed one small thing" the night before.

---

## Viva questions you will be asked

**"Why RAG instead of fine-tuning?"**
Fine-tuning bakes knowledge into weights — every new notice would need retraining,
and the model still hallucinates confidently. RAG lets us add a document in
seconds through the admin panel, and every answer is traceable to a source. For
an institution whose information changes weekly, retrieval is the only
maintainable choice.

**"Why Qdrant over Pinecone/Chroma?"**
Open source (no vendor lock-in, no cost), Rust-based HNSW so it's fast, and it
supports metadata filtering — which we need because chunks carry `user_id` and
`type` so we can restrict a search to images or to one user's documents. Chroma
doesn't scale as well; Pinecone is paid.

**"Why cosine similarity?"**
Embedding vectors vary in magnitude with text length, but length isn't relevance.
Cosine measures the angle only, so a one-line notice and a five-page PDF are
compared fairly. Dot product is faster but biases toward long documents.

**"How do you prevent hallucination?"**
Three layers: (1) the system prompt forbids answering outside the retrieved
context, (2) a score threshold drops weak matches before they reach the model,
(3) every answer carries inline citations and a Sources list, so a wrong answer
is immediately traceable. It reduces hallucination; it does not eliminate it —
and we say so in the limitations section.

**"What are its limitations?"**
Answer quality is capped by what the crawler collected — anything not on the
website is unanswerable. Images are matched by their surrounding text, not by
visual content, so a photo with no caption is invisible. We depend on third-party
APIs, so quota limits affect availability. And ERP integration for personalised
data like attendance is designed but not implemented.

**"What would you build next?"**
The Playwright automation module from Chapter 5 — logging into the ERP to fetch
a student's own attendance and marks. The scraper already uses Playwright, so the
browser-automation layer is in place; what's missing is authentication and a
consent model for handling personal data.

---

## Weekly rhythm for the two teammates

Three people, one repo. Split by layer so you rarely touch the same file:

| | Person A | Person B | Person C |
|---|---|---|---|
| Days 1–3 | Backend + Qdrant setup | Scraper tuning + data collection | Frontend polish + screenshots |
| Days 4–6 | Admin panel testing | PDF collection + ingestion | Voice + multilingual testing |
| Days 8–10 | Evaluation harness + metrics | Report alignment | Demo video + slides |

Rule: **branch per person, PR into main, never push to main directly.** Merge
conflicts on Day 11 are how projects die.

---

## Stretch goals (only if genuinely ahead)

- **Hybrid search** — combine BM25 keyword scores with cosine (report §3.4.4
  mentions this as the highest-accuracy option). Big win on proper nouns like
  faculty names.
- **Reranking** — pull top-20, rerank with Cohere's `rerank` endpoint, keep top-5.
  Usually the single largest accuracy jump for the least code.
- **Conversation memory across sessions** — persist history per `user_id`.
- **ERP automation** — Chapter 5's future scope, using the Playwright dependency
  that's already installed.
- **Auto-refresh knowledge base** — scheduled re-scrape + incremental
  re-ingest so the site stays current without an admin manually
  re-running things. Full design: [docs/FUTURE_SCOPE_AUTO_REFRESH.md](docs/FUTURE_SCOPE_AUTO_REFRESH.md).

## Going live

Deploying to real users on free infrastructure (Render + Vercel, alongside
the Qdrant Cloud/Gemini/Cohere already in use) is documented step by step
in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
