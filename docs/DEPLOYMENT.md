# Deployment guide — going live on free infrastructure

This is the checklist for taking CBIT Guru from "runs on my laptop" to "real
students can open a URL and use it." Every account signup and every payment
detail below is something **you** do by hand in the provider's own UI —
Claude does not create accounts, enter credentials, or click "deploy" on
your behalf. This doc gets the repo itself ready and tells you exactly what
to click.

## Architecture once deployed

```
Student's browser
      |
      v
Vercel (static React build)  --/api/*-->  Render (FastAPI, Docker)
                                                |         |
                                                v         v
                                          Qdrant Cloud   Gemini + Cohere
                                          (already used   (already used
                                           in dev)         in dev)
```

Nothing about the RAG pipeline changes — Qdrant Cloud, Gemini, and Cohere
are already external, already free-tier, and already how this project runs
today. Deployment only adds two new hops: a real frontend host and a real
backend host, both free.

## 1. Backend — Render

1. Push this repo to GitHub (already done — `Dileep-git09/cbit-guru`).
2. In Render: **New +** -> **Blueprint**, point it at this repo. Render
   reads [`render.yaml`](../render.yaml) at the repo root and proposes one
   web service, `cbit-guru-api`, built from
   [`backend/Dockerfile`](../backend/Dockerfile).
3. Render will prompt for every env var marked `sync: false` in
   `render.yaml` — this is where the real secrets go (never into git):
   `GEMINI_API_KEY`, `COHERE_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`,
   `JWT_SECRET` (generate a fresh random hex, don't reuse the local dev
   one), `ADMIN_EMAIL`, `ADMIN_PASSWORD`.
4. Leave `ALLOWED_ORIGINS` blank for this first deploy — you don't know the
   Vercel URL yet. Come back and set it after step 2.
5. Deploy. Render builds the Docker image and boots it; health checks hit
   `/api/health/live` (see `app/main.py`) until the service is marked live.
   Note the assigned URL, e.g. `https://cbit-guru-api.onrender.com`.

## 2. Frontend — Vercel

1. **New Project** -> import the same GitHub repo -> set **Root Directory**
   to `frontend/`. Vercel auto-detects the Vite build (`npm run build`,
   output `dist/`); [`frontend/vercel.json`](../frontend/vercel.json) adds
   the SPA rewrite so refreshing `/admin/panel` doesn't 404 (react-router
   handles routing client-side; without the rewrite Vercel would look for a
   literal `admin/panel` file and fail).
2. Add one env var: `VITE_API_BASE` = `https://cbit-guru-api.onrender.com/api`
   (the Render URL from step 1, with `/api` appended — see
   `frontend/src/lib/api.js`, which reads this var and falls back to the
   same-origin `/api` proxy that only exists in local dev).
3. Deploy. Note the assigned URL, e.g. `https://cbit-guru.vercel.app`.

## 3. Wire CORS both ways

Go back to Render -> `cbit-guru-api` -> Environment -> set
`ALLOWED_ORIGINS` = `https://cbit-guru.vercel.app` (the real Vercel URL,
comma-separated if you add a custom domain later) and redeploy. Without
this, the browser's CORS preflight fails and every chat request errors out
— `app/main.py`'s `CORSMiddleware` only allows origins listed here.

## 4. Verify end to end

- `curl https://cbit-guru-api.onrender.com/api/health/live` -> `{"status":"alive"}`
- `curl https://cbit-guru-api.onrender.com/api/health/ready` -> `{"status":"ready", "checks": {"qdrant": true, "admin_db": true}}`
- Open the Vercel URL, ask a real question, confirm citations render.
- Log into `/admin` with `ADMIN_EMAIL`/`ADMIN_PASSWORD`, confirm the panel loads and `/api/admin/stats` shows real point counts.

## Known limitations of this free setup (be upfront about these in the viva)

- **Cold starts.** Render's free web service spins down after ~15 minutes
  with no traffic and takes 30-50s to wake on the next request. Acceptable
  for a college project demo; a paid "Starter" instance removes this.
- **Ephemeral disk.** Render's free tier gives the container a local disk
  that is wiped on every new deploy (not on every restart, but on every
  code push). Two things live on that disk today:
  - `backend/instance/admin.db` (the SQLite admin-user table). This is
    fine by design — `services/users.py` only seeds the first superadmin
    from `ADMIN_EMAIL`/`ADMIN_PASSWORD` when the table is empty, so a wiped
    disk just re-seeds the same default account. Any *extra* admin
    accounts created via the panel after that would be lost on the next
    deploy — acceptable for a single-admin college project, worth calling
    out as a real production gap.
  - The in-memory/local response cache (`services/cache.py`) — resets on
    every deploy too, which just means a few slower first requests, not
    data loss (Qdrant is the actual source of truth for the knowledge
    base, and Qdrant Cloud is unaffected by Render restarts).
  - If this needs to survive redeploys later, the fix is Render's paid
    persistent disk, or moving `admin.db` to a small managed Postgres
    (Render also has a free Postgres tier with its own separate limits).
- **Single instance, in-memory rate limiting.** `REDIS_URL` is left unset —
  fine for one Render instance; only matters if this ever scales to
  multiple replicas (`REDIS_URL` in `.env.example` documents the upgrade
  path already built into `ratelimit.py`/`cache.py`).
- **Playwright is dev/ingest-only.** The deployed backend image
  deliberately never installs Playwright's browser binaries (see
  `backend/Dockerfile`'s comment) — only the offline scraper needs it, and
  it runs on your machine or in CI, never on the live web service.
