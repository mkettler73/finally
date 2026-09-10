"""FastAPI router for the chat endpoints (API_CONTRACT.md section 5).

`POST /api/chat` is the whole assistant in one request: build the live account
context, ask the model, execute what it asked for, persist the turn, and return
the reply with its action chips. `GET /api/chat/history` restores the
conversation when the frontend mounts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..market import MarketDataSource, PriceCache
from ._deps import get_db
from ._rows import field as row_field
from .client import ChatClient, LLMError, build_chat_client
from .context import build_context
from .executor import execute_actions
from .models import (
    ChatHistoryReply,
    ChatMessageOut,
    ChatReply,
    ChatRequest,
)

logger = logging.getLogger(__name__)

HISTORY_DEFAULT = 50
HISTORY_MAX = 500

# Turns replayed into the prompt. Smaller than the history the UI restores:
# the account context carries the facts, so older turns add tokens, not value.
PROMPT_HISTORY_TURNS = 20


def create_chat_router(
    price_cache: PriceCache,
    *,
    market_source: MarketDataSource | None = None,
    session_opens: Any = None,
    client: ChatClient | None = None,
    user_id: str = "default",
    prompt_history_turns: int = PROMPT_HISTORY_TURNS,
) -> APIRouter:
    """Build the chat router.

    Built by a factory rather than declared at module scope for the same reason
    as the SSE router: a module-level router would bind to the first price
    cache it ever saw and quietly serve that one forever.

    `market_source` may be omitted, in which case it is resolved per request
    from `app.state.market_source` - which is where `main.py`'s lifespan puts
    it, since the source does not exist yet when the router is constructed.

    `session_opens` is the API layer's `SessionOpenTracker`, resolved the same
    way from `app.state.session_opens`. It is duck-typed rather than imported so
    `app.llm` stays independent of `app.services`.

    `client` may be omitted, in which case it is chosen per request from the
    environment, so `LLM_MOCK` is honoured even when it is set after import.
    """
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    def _client() -> ChatClient:
        return client if client is not None else build_chat_client()

    def _source(request: Request) -> MarketDataSource | None:
        if market_source is not None:
            return market_source
        return getattr(request.app.state, "market_source", None)

    def _session_opens(request: Request) -> Any:
        if session_opens is not None:
            return session_opens
        return getattr(request.app.state, "session_opens", None)

    @router.post("", response_model=ChatReply)
    async def chat(payload: ChatRequest, request: Request) -> ChatReply:
        db = get_db()

        # History is read before the new message is stored, so the turn is not
        # handed to the model twice.
        prior = db.list_chat_messages(prompt_history_turns, user_id)
        history = [
            {
                "role": str(row_field(row, "role", "")),
                "content": str(row_field(row, "content", "")),
            }
            for row in prior
            if row_field(row, "role") in ("user", "assistant")
        ]

        # Deliberately NOT persisted yet. A failed completion below returns
        # 502, and a user turn already committed would have no assistant reply
        # to pair with: it would be replayed into the prompt of every later
        # turn and rendered on reload by GET /api/chat/history, so a transient
        # upstream failure would leave a permanent dangling message in the
        # transcript. Both turns are written together once the call succeeds.
        context = build_context(price_cache, user_id)
        chat_client = _client()

        try:
            # The only slow call in the request. Threaded so the SSE price
            # stream keeps flowing while the model thinks.
            response = await asyncio.to_thread(
                chat_client.complete,
                user_message=payload.message,
                context=context,
                history=history,
            )
        except LLMError as exc:
            logger.error("Chat completion failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={"code": "LLM_ERROR", "message": str(exc)},
            ) from exc
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the client
            logger.exception("Unexpected chat failure")
            raise HTTPException(
                status_code=502,
                detail={"code": "LLM_ERROR", "message": f"The chat assistant failed: {exc}"},
            ) from exc

        actions = await execute_actions(
            response,
            price_cache=price_cache,
            market_source=_source(request),
            session_opens=_session_opens(request),
            user_id=user_id,
        )

        # The user turn lands here, immediately before the assistant's, so the
        # transcript never holds a question with no answer.
        db.append_chat_message("user", payload.message, None, user_id)
        stored = db.append_chat_message(
            "assistant",
            response.message,
            [action.model_dump() for action in actions],
            user_id,
        )

        return ChatReply(
            message=response.message,
            actions=actions,
            created_at=str(row_field(stored, "created_at", "")),
        )

    @router.get("/history", response_model=ChatHistoryReply)
    async def history(
        limit: int = Query(HISTORY_DEFAULT, ge=1, le=HISTORY_MAX),
    ) -> ChatHistoryReply:
        db = get_db()
        rows = db.list_chat_messages(limit, user_id)
        return ChatHistoryReply(
            messages=[
                ChatMessageOut(
                    id=str(row_field(row, "id", "")),
                    role=row_field(row, "role"),
                    content=str(row_field(row, "content", "")),
                    actions=row_field(row, "actions"),
                    created_at=str(row_field(row, "created_at", "")),
                )
                for row in rows
            ]
        )

    return router
