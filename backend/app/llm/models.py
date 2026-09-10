"""Pydantic models for the chat subsystem.

Two families live here, and keeping them apart matters:

* The **model-facing** schema (`ChatResponse` and friends) is what the LLM is
  asked to emit via structured outputs. It is defined verbatim in
  API_CONTRACT.md §6 and must not gain fields the model is not told about.
* The **client-facing** schema (`ChatReply`, `ActionResult`, ...) is what
  `POST /api/chat` returns *after* the requested actions have been executed.
  The model never sees it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Model-facing structured output (API_CONTRACT §6) ---------------------


class ChatTrade(BaseModel):
    """A single trade the model wants executed."""

    ticker: str
    side: Literal["buy", "sell"]
    quantity: float


class ChatWatchlistChange(BaseModel):
    """A single watchlist mutation the model wants applied."""

    ticker: str
    action: Literal["add", "remove"]


class ChatResponse(BaseModel):
    """The complete structured response requested from the LLM."""

    message: str
    trades: list[ChatTrade] = Field(default_factory=list)
    watchlist_changes: list[ChatWatchlistChange] = Field(default_factory=list)

    # Models occasionally emit `null` for an omitted array. Coerce rather than
    # reject: a whole chat turn is too expensive to throw away over a null.
    @field_validator("trades", "watchlist_changes", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        return [] if value is None else value


# --- Client-facing request/response (API_CONTRACT §5) ---------------------


class ChatRequest(BaseModel):
    """Body of `POST /api/chat`."""

    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)


class ActionResult(BaseModel):
    """One executed (or attempted) action, rendered inline in the chat bubble."""

    type: Literal["trade", "watchlist"]
    status: Literal["ok", "error"]
    detail: str
    data: dict[str, Any] = Field(default_factory=dict)


class ChatReply(BaseModel):
    """Response body of `POST /api/chat`."""

    message: str
    actions: list[ActionResult] = Field(default_factory=list)
    created_at: str


class ChatMessageOut(BaseModel):
    """One stored conversation turn, as returned by `GET /api/chat/history`."""

    id: str
    role: Literal["user", "assistant"]
    content: str
    actions: list[dict[str, Any]] | None = None
    created_at: str


class ChatHistoryReply(BaseModel):
    """Response body of `GET /api/chat/history`."""

    messages: list[ChatMessageOut] = Field(default_factory=list)
