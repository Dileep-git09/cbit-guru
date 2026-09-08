"""Central configuration. Every tunable value lives here, loaded from .env.

Why one file: the viva examiner can ask "where do I change TOP_K?" and the
answer is always "config.py / .env" — no magic numbers scattered in the code.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to the backend/ folder, computed from THIS file's location.
# __file__ = backend/app/config.py  ->  .parent.parent = backend/
# Using a computed path (not a hardcoded string) means the app runs the same
# no matter which directory you launch uvicorn from.
BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Tell pydantic-settings to read backend/.env, and to ignore any extra
    # keys in that file it doesn't recognise (so a stray line won't crash boot).
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Gemini embeddings ---
    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 3072              # vector length; MUST match the Qdrant collection

    # --- Cohere generation ---
    cohere_api_key: str = ""
    cohere_model: str = "command-r-08-2024"

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "cbit_guru_rag"

    # --- Retrieval (the knobs you tune in Day 3 / Day 8 of the roadmap) ---
    top_k: int = 8                         # how many text chunks feed the LLM
    image_top_k: int = 5                   # how many images for the "Related Images" strip
    score_threshold: float = 0.35          # drop matches weaker than this cosine score
    chunk_size: int = 1200                 # max chars per chunk
    chunk_overlap: int = 200               # chars carried across chunk boundaries
    max_pdf_chars: int = 50_000            # cap so one huge PDF can't dominate the KB

    # --- Admin auth ---
    # admin_email/admin_password only matter on the very first boot: they seed
    # the one initial superadmin row in the user database (services/users.py).
    # After that the database is authoritative — these two values are ignored.
    admin_email: str = "admin@cbit.ac.in"
    admin_password: str = "change_me_now"
    admin_db_file: str = "instance/admin.db"   # ":memory:" for tests — see users.py
    jwt_secret: str = "dev-only-insecure-secret"
    jwt_expire_minutes: int = 720          # token lifetime = 12 hours

    # --- CORS: which browser origins may call this API ---
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Scaling for many concurrent students (see services/cache.py, ratelimit.py) ---
    # Caps on simultaneous outbound calls to each paid/free-tier API, shared
    # across EVERY concurrent request this process is handling. Without this,
    # 50 students clicking "send" at the same instant fire 50 simultaneous
    # Gemini/Cohere calls and instantly blow through the free-tier rate limit
    # for everyone — this is the exact failure mode a real ingest run hit.
    gemini_max_concurrency: int = 5
    cohere_max_concurrency: int = 5

    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600              # how long a cached answer stays valid
    cache_max_semantic_entries: int = 200      # cap on the paraphrase-matching cache
    cache_semantic_threshold: float = 0.97     # cosine similarity required to reuse an answer

    # Per-IP requests/minute to /api/chat*. 0 disables the limiter entirely
    # (e.g. for a controlled demo where you don't want to risk tripping it).
    chat_rate_limit_per_minute: int = 30

    # --- Scraper ---
    scrape_root_url: str = "https://www.cbit.ac.in"
    scrape_max_pages: int = 150
    scrape_output_dir: str = "data"

    # pydantic-settings maps env keys case-insensitively, so ALLOWED_ORIGINS
    # in .env fills `allowed_origins` here. We store it as one comma-joined
    # string (env vars are strings) and split it on demand:
    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def data_dir(self) -> Path:
        # Allow either an absolute path or one relative to backend/.
        p = Path(self.scrape_output_dir)
        return p if p.is_absolute() else BACKEND_ROOT / p

    @property
    def admin_db_path(self) -> Path:
        p = Path(self.admin_db_file)
        return p if p.is_absolute() else BACKEND_ROOT / p


@lru_cache
def get_settings() -> Settings:
    # lru_cache => the .env file is parsed exactly once, and every module that
    # imports `settings` shares the same object (a cheap singleton).
    return Settings()


settings = get_settings()
