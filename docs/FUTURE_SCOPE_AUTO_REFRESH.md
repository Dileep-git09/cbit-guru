# Future scope — keeping the knowledge base fresh automatically

This is a **design document, not implemented code**. Today, keeping the
Qdrant knowledge base current means an admin manually re-running the
scraper and hitting "reindex" in the panel. This document plans out
replacing that manual step with a scheduled pipeline that notices when
cbit.ac.in changes and updates only what changed — without a human doing
anything.

## Why this isn't trivial (grounded in what the code actually does today)

Two real gaps in the current ingestion code make "just re-run the scraper
on a timer" unsafe as-is:

1. **Point IDs are random, not deterministic.**
   `vectorstore.upsert_chunks()` (`backend/app/services/vectorstore.py`)
   assigns every chunk `id=str(uuid.uuid4())`. Re-running
   `ingest_directory()` (`backend/app/services/ingest.py`) against
   unchanged pages today would not update anything in place — it would
   insert a second, duplicate copy of every chunk, because nothing ties a
   new point back to an old one it should replace.

2. **`doc_id` is derived partly from content, not just source identity.**
   For scraped pages, `_doc_id(source_name, url, cleaned[:200])` folds the
   first 200 characters of the page's own text into the hash. That's
   intentional for admin-uploaded text (lets identical pastes dedupe), but
   it means a page's `doc_id` changes *whenever its content changes* — so
   there's no way to look up "the old doc_id for this URL" without having
   separately recorded what it was last time.

The good news: `vectorstore.delete_by_doc(doc_id)` already exists and is
exactly the primitive an incremental pipeline needs — it's what the admin
panel's "delete this document" button calls today. The plan below is
essentially: *do automatically, on a schedule, what an admin already does
by hand* — record what was ingested last time, diff against what the site
looks like now, and delete-then-reinsert only what actually changed.

## Proposed pipeline

```
GitHub Actions (scheduled, e.g. weekly)
        |
        v
1. Run scraper/crawl.py against cbit.ac.in -> data/ (as today)
        |
        v
2. Diff against data/crawl_state.json (new file, committed as pipeline state)
   for each scraped page: sha256(page text) compared to last-seen hash
        |
        +-- new URL           -> ingest normally, record (url, doc_id, hash)
        +-- changed hash       -> delete_by_doc(old doc_id), re-ingest, record new pair
        +-- unchanged hash     -> skip entirely (no embedding call, no Qdrant write)
        +-- URL disappeared    -> delete_by_doc(old doc_id), drop from state file
        |
        v
3. Commit the updated data/crawl_state.json back to the repo (small, diffable,
   auditable history of what changed and when)
        |
        v
4. Post a summary (pages added/updated/removed, chunks written) — Actions
   job summary is enough; a Slack/webhook notification is an easy add-on
```

### Why a state file instead of querying Qdrant to figure out what changed

Qdrant holds embeddings and chunk text, not a compact per-URL fingerprint,
and querying it for "does this URL's content still match" would mean
pulling and re-hashing every chunk on every run. A small
`data/crawl_state.json` (`{url: {doc_id, content_sha256, last_ingested}}`)
is the cheap, obvious source of truth for the diff, and committing it to
git makes every re-scrape's effect visible in `git log` — genuinely useful
for a viva ("here's proof the pipeline only touched the 3 pages that
changed this week").

### Why GitHub Actions specifically

- It's free and already wired up (`.github/workflows/ci.yml` exists) —
  adding a second, `schedule:`-triggered workflow reuses the same repo,
  the same `gh`-based git hygiene habits, and needs zero new
  infrastructure or accounts.
- It runs *outside* the Render web service, so a slow/failing scrape can
  never take down the live chat endpoint students are using — this
  mirrors the existing design principle behind liveness vs. readiness
  probes (`app/main.py`): the thing that updates the knowledge base and
  the thing that serves chat requests should be able to fail
  independently.
- Secrets (`QDRANT_URL`, `QDRANT_API_KEY`, `GEMINI_API_KEY`) go in GitHub
  Actions repo secrets — same mechanism, no new secret-management story.

A real webhook from cbit.ac.in (the site pinging us the moment a page
changes) isn't realistic — the institute's own site has no such mechanism,
and standing up one would require access this project doesn't have.
Scheduled polling is the honest, achievable version of "stay up to date."

### Politeness and safety

- Keep the existing `HEADERS["User-Agent"]` identification
  (`scraper/crawl.py`) and `max_pages` cap — a weekly run against ~150
  pages is a light load, not a scrape-storm.
- Sanity-check before trusting a bad run: if the crawl returns
  suspiciously few pages (e.g. site was down mid-scrape and only served
  error pages), skip the diff/delete step entirely rather than treating a
  transient outage as "all pages were removed." A simple floor check
  (`pages_saved > 0.5 * previous_run_count`) is enough to catch this.
- Rate-limit re-embedding the same way live chat already does — reuse
  `GEMINI_MAX_CONCURRENCY`/`services/embeddings.py`'s semaphore and
  circuit breaker rather than a second, separate throttling scheme.

### What this buys, concretely

- A new faculty announcement, an updated fee circular, or a new event page
  shows up in chat answers within a week of being posted, with no admin
  action.
- Pages removed from the live site (e.g. an expired notice) stop being
  cited — today they'd linger in Qdrant forever since nothing ever
  deletes stale content.
- Every re-scrape's diff is a committed, auditable artifact
  (`crawl_state.json`'s git history), not a silent background mutation.

### What's deliberately out of scope for this plan

- Real-time (sub-daily) freshness — a weekly or daily cron is the right
  cadence for a college site that doesn't change hour to hour; anything
  faster adds complexity for no real benefit here.
- Automatically retraining/re-tuning retrieval parameters — this pipeline
  only keeps *content* current, not the RAG tuning (`TOP_K`,
  `SCORE_THRESHOLD`, etc.), which stays a manual, deliberate change.
