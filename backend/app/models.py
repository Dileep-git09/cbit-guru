"""Request / response schemas.

These Pydantic models are the *contract* between the React frontend and the
FastAPI backend. FastAPI uses them to (a) validate incoming JSON, (b) serialise
outgoing JSON, and (c) auto-generate the /docs API page. If the frontend sends
a bad shape, FastAPI rejects it with a 422 before our code ever runs.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Turn(BaseModel):
    # One line of conversation history. `Literal` restricts role to exactly
    # these two strings — anything else is a validation error.
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # The user's question. Field(...) adds guard rails: non-empty, and capped
    # at 4000 chars so a pasted essay can't blow up the embedding call.
    message: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list)   # prior turns for memory
    user_id: str | None = None                          # for per-user doc scoping
    stream: bool = False

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # min_length=1 above only counts raw characters, so "   " (all
        # whitespace) passes it — and then reaches embeddings.embed_one(),
        # which raises a raw ValueError on empty-after-strip text. That
        # ValueError isn't caught anywhere in the router, so it surfaced as
        # an ugly unhandled 500 instead of a clean validation error. Reject
        # it right here instead, at the same boundary as the empty-string case.
        if not v.strip():
            raise ValueError("message cannot be blank")
        return v


class SourceRef(BaseModel):
    # One entry in the collapsible "Sources" list under an answer.
    n: int                 # citation number the LLM references inline, e.g. [2]
    label: str             # file name or URL shown to the user
    type: str              # text / pdf / web_scrape / image ...
    url: str = ""
    score: float           # cosine similarity — lets the examiner see relevance


class RelatedImage(BaseModel):
    url: str
    caption: str = ""
    tags: list[str] = Field(default_factory=list)
    score: float = 0.0


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceRef] = Field(default_factory=list)
    images: list[RelatedImage] = Field(default_factory=list)
    grounded: bool = True          # False when nothing relevant was retrieved
    latency_ms: int = 0            # end-to-end time, useful for the results chapter


# --- Admin panel schemas ---

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    # What the frontend calls right after login to decide what to show —
    # e.g. only a superadmin's panel gets the "Admins" tab.
    id: str
    email: str
    role: str


class CreateAdminUserRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: Literal["admin", "superadmin"] = "admin"


class AdminUserOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: str


class ChangeOwnPasswordRequest(BaseModel):
    # Self-service password change requires the CURRENT password as proof of
    # identity — the JWT alone isn't enough (a stolen-but-still-valid token
    # shouldn't be enough to permanently take over the account).
    current_password: str
    new_password: str = Field(min_length=8)


class ResetPasswordRequest(BaseModel):
    # A super admin resetting someone ELSE's password is an admin override —
    # no current password needed, since the whole point is the target user
    # may have forgotten it.
    new_password: str = Field(min_length=8)


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=20)          # reject trivially short pastes
    source_name: str = "manual-text"


class IngestUrlRequest(BaseModel):
    url: str


class IngestResult(BaseModel):
    doc_id: str
    chunks: int
    chars: int
    message: str = "Ingested successfully"


class StatsResponse(BaseModel):
    total_points: int
    collection: str
    embedding_model: str
    llm_model: str
    cache_exact_entries: int = 0
    cache_semantic_entries: int = 0


class BrowseResponse(BaseModel):
    items: list[dict[str, Any]]
    next_offset: str | None = None
