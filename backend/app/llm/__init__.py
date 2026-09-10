"""LLM chat subsystem for FinAlly.

Public API:
    create_chat_router      - FastAPI router factory for POST /api/chat and
                              GET /api/chat/history
    build_chat_client       - Returns the mock or live client per LLM_MOCK
    LiveChatClient          - LiteLLM -> OpenRouter -> Cerebras, structured output
    MockChatClient          - Deterministic offline stand-in (LLM_MOCK=true)
    LLMError                - Upstream failure; maps to 502 LLM_ERROR
    build_context           - Live portfolio/watchlist/history context
    execute_actions         - Applies model-requested trades and watchlist changes
    ChatResponse and friends - The structured-output and HTTP models
"""

from .client import LiveChatClient, LLMError, build_chat_client, is_mock_mode
from .context import PortfolioContext, build_context, render_context
from .executor import execute_actions
from .mock import MockChatClient, build_mock_response
from .models import (
    ActionResult,
    ChatHistoryReply,
    ChatMessageOut,
    ChatReply,
    ChatRequest,
    ChatResponse,
    ChatTrade,
    ChatWatchlistChange,
)
from .prompt import SYSTEM_PROMPT
from .router import create_chat_router

__all__ = [
    "SYSTEM_PROMPT",
    "ActionResult",
    "ChatHistoryReply",
    "ChatMessageOut",
    "ChatReply",
    "ChatRequest",
    "ChatResponse",
    "ChatTrade",
    "ChatWatchlistChange",
    "LLMError",
    "LiveChatClient",
    "MockChatClient",
    "PortfolioContext",
    "build_chat_client",
    "build_context",
    "build_mock_response",
    "create_chat_router",
    "execute_actions",
    "is_mock_mode",
    "render_context",
]
