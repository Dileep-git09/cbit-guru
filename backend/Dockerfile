# Production image for the FastAPI backend only. The scraper's Playwright
# path is a separate offline job (see docs/DEPLOYMENT.md) — this image never
# installs Playwright's browser binaries, keeping the live web service small
# and inside Render's free-tier memory limit.
FROM python:3.11-slim

WORKDIR /app

# System deps for lxml/PyMuPDF wheels to build cleanly on slim images.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Render (and most PaaS free tiers) inject the port to bind via $PORT — the
# app must listen on it, not a hardcoded 8000, or the platform's health
# check can never reach it. Shell form so $PORT actually expands.
ENV PORT=8000
EXPOSE 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
