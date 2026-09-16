# Auto-refresh knowledge base — implemented

**Status: built.** This started as a design-only document; the pipeline
described below is now real code. Keeping the Qdrant knowledge base current
no longer means an admin manually re-running the scraper and hitting
"reindex" in the panel — a scheduled GitHub Actions workflow does it,
touching Qdrant only for what actually changed on cbit.ac.in.

- `backend/scripts/refresh_knowledge_base.py` — the scrape-diff-reingest
  logic itself
- `backend/scripts/bootstrap_crawl_state.py` — one-time script that seeded
  the state file against the collection's pre-existing (non-incremental)
  ingest, so adopting this didn't duplicate anything already there
- `.github/workflows/refresh_kb.yml` — runs it weekly (and on manual
  dispatch)
- `backend/data/crawl_state.json` — the pipeline's own state, committed to
  git

## Why this wasn't trivial (grounded in what the code actually does)

Two real gaps in the ingestion code made "just re-run the scraper on a
timer" unsafe as-is:

1. **Point IDs are random, not deterministic.**
   `vectorstore.upsert_chunks()` assigns every chunk `id=str(uuid.uuid4())`.
   Re-running `ingest_directory()` against unchanged pages doesn't update
   anything in place — it inserts a second, duplicate copy of every chunk,
   because nothing ties a new point back to an old one it should replace.

2. **`doc_id` is derived partly from content, not just source identity.**
   For scraped pages, `_doc_id(source_name, url, cleaned[:200])` folds part
   of the page's own text into the hash, so a page's `doc_id` changes
   *whenever its content changes* — there's no way to look up "the old
   doc_id for this URL" without having separately recorded what it was.

`vectorstore.delete_by_doc(doc_id)` already existed (it's what the admin
panel's Browse Data delete button calls) and is exactly the primitive an
incremental pipeline needs. The implementation is: record what was
ingested last time, diff against what the site looks like now, and
delete-then-reinsert only what actually changed.

## How it actually works

```
.github/workflows/refresh_kb.yml (weekly cron + manual dispatch)
        |
        v
1. python -m scraper.crawl  -> data/{text_content,pdfs,images}
        |
        v
2. python -m scripts.refresh_knowledge_base
   For each local source, keyed by filename (text: "text:<file>", pdf:
   "pdf:<file>", image: "image:<image_url>") — see "keyed by filename,
   not URL" below:
        +-- new key            -> ingest, record {doc_id, content_sha256}
        +-- known key, hash differs  -> delete_by_doc(old doc_id), re-ingest,
                                        record the new pair
        +-- known key, hash same     -> skip entirely (no embedding call)
        +-- known key, source gone   -> delete_by_doc(old doc_id), drop
                                        from state
   A source that legitimately embeds to 0 chunks (see "scanned PDFs" below)
   is never recorded — recording it would make it look "done" forever
   even though nothing backs that doc_id in Qdrant.
        |
        v
3. Commit the updated crawl_state.json back to main (small, diffable —
   `git log -p -- backend/data/crawl_state.json` shows exactly what
   changed and when)
```

### Keyed by filename, not URL

The original design in this doc keyed the state file by each page's URL.
That broke against the real, already-populated collection:
`ingest_directory()` (the code that did the *first* real ingest, back on
Day 3) never passed a `url=` for scraped text pages or PDFs — their stored
payload has `url: ""`. Bootstrapping against real production data with a
URL-based key matched 0 of the 149 existing text documents. Filenames,
though, are exactly what `payload.file_name` already carries for every
source type, and `crawl.py`'s `safe_name(url)` makes a filename a stable,
1:1, already-deterministic function of the URL anyway — so keying on it
directly loses nothing and actually matches the data that exists.
(Fixed going forward too — `ingest_directory()` now extracts the page's
`URL: ` header and passes it through, so future ingests carry a real
`url` for citations. The state file's key scheme doesn't depend on it
either way.)

### The bootstrap step the original design didn't anticipate

Adopting this pipeline on a collection that already had 755 points from
the original non-incremental ingest meant the very first run needed to
know what was *already* there, or it would have re-ingested and duplicated
all of it. `bootstrap_crawl_state.py` scrolls every existing point,
recovers each document's real `doc_id` from its payload, matches it back
to the corresponding local file by the same key scheme, and seeds
`crawl_state.json` with the *existing* `doc_id` plus a hash of the
*current* local content — so the first real refresh run correctly sees
"unchanged" instead of "new". Run exactly once, before this pipeline's
first real run, against a collection populated the old way.

### A real bug this surfaced: scanned PDFs

Bootstrapping found only 19 distinct pdf `doc_id`s in Qdrant against 82
locally scraped PDF files. Not a collision — 63 of the 82 are scanned,
image-only PDFs with no OCR text layer, so `ingest_pdf()` legitimately
extracts 0 characters and `_store()`'s `if not chunks: return 0` guard
means nothing was ever written for them, silently, since the very first
ingest. This pipeline doesn't (and can't, without adding OCR) recover that
content — but it does surface it plainly: `refresh_knowledge_base.py`
reports these as `new` on every single run, forever, because a
zero-chunk result is deliberately never written to the state file (see
above). That's an honest signal that a real gap exists, not a bug in the
diff logic. (Separately hardened `ingest_pdf()`'s `doc_id` derivation from
`len(body)` to `body[:200]` while investigating this — the length-only
version was a real, if not-yet-triggered, collision risk for any two
same-length PDFs.)

### Why a state file instead of querying Qdrant to figure out what changed

Qdrant holds embeddings and chunk text, not a compact per-source
fingerprint, and querying it for "does this source's content still match"
would mean pulling and re-hashing every chunk on every run. The state file
is the cheap, obvious source of truth for the diff, and committing it to
git makes every re-scrape's effect visible in `git log` — genuinely useful
for a viva ("here's proof the pipeline only touched the N sources that
changed this week").

### Why GitHub Actions specifically

- Free, and this repo already had one workflow (`ci.yml`) — a second,
  `schedule:`-triggered workflow needed no new infrastructure or accounts.
- Runs *outside* the Render web service, so a slow or failing scrape can
  never take down the live chat endpoint students are using — the same
  principle behind `app/main.py`'s liveness vs. readiness split: the thing
  that updates the knowledge base and the thing that serves chat requests
  fail independently.
- Secrets (`QDRANT_URL`, `QDRANT_API_KEY`, `GEMINI_API_KEY`) live in GitHub
  Actions repo secrets — same mechanism already used by nothing else here
  yet, but the standard, no-new-story place for them.

A real webhook from cbit.ac.in isn't realistic — the institute's site has
no such mechanism. Scheduled polling is the honest, achievable version of
"stay up to date."

### Politeness and safety

- Keeps the existing `HEADERS["User-Agent"]` identification and
  `max_pages` cap — a weekly run is a light load, not a scrape-storm.
- Sanity-checked before trusting a bad run:
  `refresh_knowledge_base.py`'s `MIN_SURVIVING_FRACTION = 0.5` aborts the
  delete/removal step (and leaves `crawl_state.json` untouched) if a crawl
  comes back with fewer than half as many sources as last time — a
  transient site outage serving error pages doesn't get misread as "every
  page was removed."
- Re-embedding goes through the same `GEMINI_MAX_CONCURRENCY`
  semaphore/circuit breaker live chat already uses — no second, separate
  throttling scheme.

### What this buys, concretely

- A new faculty announcement, updated fee circular, or new event page
  shows up in chat answers within a week, with no admin action.
- Pages removed from the live site stop being cited — previously they'd
  linger in Qdrant forever, since nothing ever deleted stale content.
- Every re-scrape's diff is a committed, auditable artifact
  (`crawl_state.json`'s git history), not a silent background mutation.

### What's deliberately out of scope

- Real-time (sub-daily) freshness — weekly is the right cadence for a
  college site that doesn't change hour to hour.
- OCR for scanned PDFs — the 63 zero-text PDFs above are a real, known
  gap; closing it means adding an OCR step to `ingest_pdf()`, not
  something this pipeline does.
- Automatically retuning retrieval parameters — this keeps *content*
  current, not `TOP_K`/`SCORE_THRESHOLD`, which stay a manual, deliberate
  change.
