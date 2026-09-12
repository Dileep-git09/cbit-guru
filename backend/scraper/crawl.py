"""CBIT website crawler.

Report §3.4.1: data lands in a fixed folder layout —
    data/
      text_content/   one .txt per page
      pdfs/           downloaded PDFs
      images/         downloaded images + manifest.json (captions/context)

Two fetch strategies:
  * aiohttp + BeautifulSoup  — fast path for ordinary server-rendered pages
  * Playwright               — fallback for pages whose content is drawn by JS
    (many modern college sites render the menu/content via a JS framework;
    a plain aiohttp GET only sees the empty shell HTML in that case)

Run:
    python -m scraper.crawl                     # default: whole site, 150 pages
    python -m scraper.crawl --max-pages 40      # quick run for a demo
    python -m scraper.crawl --no-playwright     # skip the browser entirely
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("scraper")

# Identifying ourselves honestly in the User-Agent, per report's "respect
# robots.txt and the institute's terms" note in README's license section.
HEADERS = {"User-Agent": "CBITGuruBot/1.0 (+college major project; contact dept.)"}
SKIP_EXT = {
    ".zip", ".rar", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp4", ".mp3", ".exe", ".apk",
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def safe_name(url: str, fallback: str = "page") -> str:
    """Turn a URL into a filesystem-safe filename (no slashes, no query junk)."""
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/") or fallback
    name = _SAFE.sub("_", raw)
    return name[:120] or fallback  # cap length — Windows has a ~260 char path limit


def same_site(url: str, root_host: str) -> bool:
    # endswith (not ==) lets subdomains like cse.cbit.ac.in count as same-site
    # if root_host is cbit.ac.in — department pages often live on subdomains.
    host = urlparse(url).netloc.lower()
    return host.endswith(root_host.lower())


class Crawler:
    def __init__(
        self,
        root: str,
        out_dir: Path,
        max_pages: int,
        use_playwright: bool = True,
        download_images: bool = True,
    ) -> None:
        self.root = root.rstrip("/")
        self.root_host = urlparse(self.root).netloc.replace("www.", "")
        self.out = out_dir
        self.max_pages = max_pages
        self.use_playwright = use_playwright
        self.download_images = download_images

        self.text_dir = out_dir / "text_content"
        self.pdf_dir = out_dir / "pdfs"
        self.img_dir = out_dir / "images"
        for d in (self.text_dir, self.pdf_dir, self.img_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.seen: set[str] = set()          # visited URLs, prevents infinite loops on cyclic links
        self.image_manifest: list[dict] = []
        self.pages_saved = 0
        self.pdfs_saved = 0

    # ---------------------------------------------------------------- fetch
    async def fetch(self, session: aiohttp.ClientSession, url: str) -> tuple[str, bytes | None]:
        """Return (content_type, body). Body is None on failure."""
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return "", None
                ctype = resp.headers.get("content-type", "").lower()
                return ctype, await resp.read()
        except Exception as exc:  # noqa: BLE001 — one bad URL must not kill the whole crawl
            log.debug("fetch failed %s: %s", url, exc)
            return "", None

    async def render(self, url: str) -> str | None:
        """Playwright fallback for JavaScript-rendered pages.

        Imported lazily inside the function (not at module top) so that
        `--no-playwright` runs never even need the playwright package
        installed — useful for the very first "does the site even respond"
        smoke run described in ROADMAP.md Day 2.
        """
        if not self.use_playwright:
            return None
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415 — see docstring above
        except ImportError:
            return None
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page(user_agent=HEADERS["User-Agent"])
                # networkidle: wait until no network requests for 500ms —
                # gives client-side JS time to finish rendering the DOM.
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                html = await page.content()
                await browser.close()
                return html
        except Exception as exc:  # noqa: BLE001
            log.debug("playwright failed %s: %s", url, exc)
            return None

    # ---------------------------------------------------------------- parse
    def extract(self, html: str, url: str) -> tuple[str, str, list[str], list[dict]]:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        title = (soup.title.get_text(strip=True) if soup.title else "") or url
        text = soup.get_text("\n")

        links: list[str] = []
        for a in soup.find_all("a", href=True):
            nxt, _ = urldefrag(urljoin(url, a["href"]))  # urldefrag strips #anchor fragments
            if nxt.startswith(("http://", "https://")):
                links.append(nxt)

        images: list[dict] = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""  # data-src covers lazy-loaded images
            if not src:
                continue
            abs_src = urljoin(url, src)
            if Path(urlparse(abs_src).path).suffix.lower() not in IMG_EXT:
                continue
            # Nearby text gives the image its searchable meaning (report §3.4.1)
            # — this is the other half of ingest.image_to_text()'s "surrounding".
            parent = img.find_parent(["figure", "div", "td", "li", "section"])
            surrounding = parent.get_text(" ", strip=True)[:600] if parent else ""
            images.append(
                {
                    "image_url": abs_src,
                    "file_name": Path(urlparse(abs_src).path).name,
                    "caption": (img.get("alt") or img.get("title") or "").strip(),
                    "surrounding": surrounding,
                    "page_title": title,
                    "page_url": url,
                    "tags": [t for t in urlparse(url).path.strip("/").split("/") if t][:3],
                }
            )

        return title, text, links, images

    # ---------------------------------------------------------------- save
    def save_page(self, url: str, title: str, text: str) -> None:
        lines = [ln.strip() for ln in text.split("\n")]
        body = "\n".join(ln for ln in lines if ln)
        if len(body) < 200:
            # Nav-only / redirect / error pages produce almost no text —
            # not worth a chunk, and would just be noise in retrieval.
            return
        path = self.text_dir / f"{safe_name(url)}.txt"
        path.write_text(f"URL: {url}\nTITLE: {title}\n\n{body}", encoding="utf-8")
        self.pages_saved += 1

    async def save_pdf(self, session: aiohttp.ClientSession, url: str) -> None:
        name = safe_name(url, "doc")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        path = self.pdf_dir / name
        if path.exists():
            return  # already downloaded — skip re-fetching on a re-run
        _, body = await self.fetch(session, url)
        if body and body[:4] == b"%PDF":  # verify it's actually a PDF, not an error page served as 200
            path.write_bytes(body)
            self.pdfs_saved += 1
            log.info("pdf   %s", name)

    async def save_image(self, session: aiohttp.ClientSession, meta: dict) -> None:
        self.image_manifest.append(meta)
        if not self.download_images:
            return
        name = safe_name(meta["image_url"], "img")
        path = self.img_dir / name
        if path.exists():
            return
        _, body = await self.fetch(session, meta["image_url"])
        if body and len(body) > 4096:  # skip tiny icons/spacer gifs — not real campus photos
            path.write_bytes(body)

    # ---------------------------------------------------------------- run
    async def run(self) -> dict:
        # BFS via deque: pages closer to the homepage (more important, more
        # likely to be a real content page) get crawled first, before the
        # page budget (max_pages) runs out on deep, less relevant pages.
        queue: deque[str] = deque([self.root])
        timeout = aiohttp.ClientTimeout(total=45)
        # limit=8 caps concurrent connections; ssl=False tolerates some
        # colleges' misconfigured certs.
        connector = aiohttp.TCPConnector(limit=8, ssl=False)

        async with aiohttp.ClientSession(
            timeout=timeout, headers=HEADERS, connector=connector
        ) as session:
            while queue and self.pages_saved < self.max_pages:
                url = queue.popleft()
                url, _ = urldefrag(url)
                if url in self.seen or not same_site(url, self.root_host):
                    continue
                self.seen.add(url)

                ext = Path(urlparse(url).path).suffix.lower()
                if ext in SKIP_EXT:
                    continue
                if ext == ".pdf":
                    await self.save_pdf(session, url)
                    continue

                ctype, body = await self.fetch(session, url)
                html = ""
                if body and "html" in ctype:
                    html = body.decode("utf-8", errors="ignore")
                elif body and "pdf" in ctype:
                    # Some sites serve PDFs from URLs with no .pdf extension —
                    # caught here via the actual Content-Type header instead.
                    await self.save_pdf(session, url)
                    continue

                if len(html) < 500:
                    # Suspiciously small HTML usually means "JS renders this
                    # page" — fall back to a real browser.
                    html = await self.render(url) or html
                if not html:
                    continue

                title, text, links, images = self.extract(html, url)
                self.save_page(url, title, text)
                log.info("page  [%3d] %s", self.pages_saved, url[:90])

                for meta in images[:20]:  # cap per-page images so one gallery page can't dominate the crawl budget
                    await self.save_image(session, meta)

                for link in links:
                    if link not in self.seen and same_site(link, self.root_host):
                        queue.append(link)

            # de-duplicate images by URL before writing the manifest — the
            # same image (e.g. the college logo) appears on every page.
            unique: dict[str, dict] = {m["image_url"]: m for m in self.image_manifest}
            (self.img_dir / "manifest.json").write_text(
                json.dumps(list(unique.values()), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        return {
            "pages": self.pages_saved,
            "pdfs": self.pdfs_saved,
            "images": len(unique),
            "visited": len(self.seen),
        }


def main() -> None:
    ap = argparse.ArgumentParser(description="Crawl the CBIT website into data/")
    ap.add_argument("--root", default=settings.scrape_root_url)
    ap.add_argument("--max-pages", type=int, default=settings.scrape_max_pages)
    ap.add_argument("--out", default=str(settings.data_dir))
    ap.add_argument("--no-playwright", action="store_true")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    crawler = Crawler(
        root=args.root,
        out_dir=Path(args.out),
        max_pages=args.max_pages,
        use_playwright=not args.no_playwright,
        download_images=not args.no_images,
    )
    stats = asyncio.run(crawler.run())
    log.info("DONE %s", stats)


if __name__ == "__main__":
    main()
