"""The chat client: LiteLLM -> OpenRouter -> Cerebras, with structured outputs.

Two implementations sit behind one shape. `LiveChatClient` calls the model;
`MockChatClient` (in `mock.py`) does not and cannot. `build_chat_client()`
picks between them from the environment, and mock mode is decided *before* any
network module is imported - `litellm` is imported lazily inside the live path,
so a mock-mode process never loads it at all.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol, runtime_checkable

from .context import PortfolioContext, render_context
from .mock import MockChatClient
from .models import ChatResponse
from .prompt import build_system_message

logger = logging.getLogger(__name__)

# Fixed by the `cerebras` skill - model id and provider routing travel together.
MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY: dict[str, Any] = {"provider": {"order": ["cerebras"]}}
REASONING_EFFORT = "low"

# One call plus one retry. A second retry mostly buys latency: if Cerebras
# returned unparseable output twice, a third attempt rarely differs.
MAX_ATTEMPTS = 2
REQUEST_TIMEOUT = 60.0

# Conversation turns replayed to the model. The live account context carries
# the state that matters, so history is for tone and follow-ups, not for facts.
HISTORY_TURNS = 20

_RETRY_NUDGE = (
    "Your previous reply could not be parsed. Reply with a single JSON object and "
    "nothing else: keys `message` (string), `trades` (array), `watchlist_changes` (array)."
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class LLMError(Exception):
    """The upstream call failed, or its output could not be parsed after retry.

    Surfaces as `502 LLM_ERROR` (API_CONTRACT section 0). Never raised for a
    trade or watchlist action the model asked for and the account refused -
    those are 200 responses carrying an error action.
    """


@runtime_checkable
class ChatClient(Protocol):
    """What the router needs from a chat backend."""

    is_mock: bool

    def complete(
        self,
        *,
        user_message: str,
        context: PortfolioContext,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse: ...


def is_mock_mode() -> bool:
    """True when `LLM_MOCK` is set to a truthy value."""
    return os.environ.get("LLM_MOCK", "").strip().lower() in _TRUTHY


def build_chat_client(*, mock: bool | None = None) -> ChatClient:
    """Return the mock or the live client. `mock` overrides the environment."""
    use_mock = is_mock_mode() if mock is None else mock
    if use_mock:
        return MockChatClient()
    return LiveChatClient()


class LiveChatClient:
    """Calls `openrouter/openai/gpt-oss-120b` through LiteLLM with Cerebras routing."""

    is_mock = False

    def __init__(
        self,
        *,
        model: str = MODEL,
        completion_fn: Any = None,
        max_attempts: int = MAX_ATTEMPTS,
        reasoning_effort: str = REASONING_EFFORT,
        timeout: float = REQUEST_TIMEOUT,
        history_turns: int = HISTORY_TURNS,
    ) -> None:
        self.model = model
        self.max_attempts = max(1, max_attempts)
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.history_turns = history_turns
        self._completion_fn = completion_fn

    # --- transport ---

    def _completion(self) -> Any:
        """Resolve `litellm.completion` lazily.

        Importing litellm costs seconds and pulls in a stack of HTTP machinery.
        Deferring it keeps app startup fast and keeps mock-mode processes clean
        of it entirely.
        """
        if self._completion_fn is None:
            from litellm import completion

            self._completion_fn = completion
        return self._completion_fn

    def build_messages(
        self,
        *,
        user_message: str,
        context: PortfolioContext,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": build_system_message(render_context(context))}]
        for turn in (history or [])[-self.history_turns :]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages

    def complete(
        self,
        *,
        user_message: str,
        context: PortfolioContext,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse:
        if not os.environ.get("OPENROUTER_API_KEY", "").strip():
            raise LLMError(
                "OPENROUTER_API_KEY is not set, so the chat assistant cannot reach the model."
            )

        messages = self.build_messages(user_message=user_message, context=context, history=history)
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            attempt_messages = messages
            if attempt > 1:
                attempt_messages = [*messages, {"role": "system", "content": _RETRY_NUDGE}]
            try:
                return self._attempt(attempt_messages)
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised as LLMError
                last_error = exc
                logger.warning(
                    "LLM attempt %d/%d failed: %s: %s",
                    attempt,
                    self.max_attempts,
                    type(exc).__name__,
                    exc,
                )

        raise LLMError(
            f"The model call failed after {self.max_attempts} attempts: {last_error}"
        ) from last_error

    def _attempt(self, messages: list[dict[str, str]]) -> ChatResponse:
        response = self._completion()(
            model=self.model,
            messages=messages,
            response_format=ChatResponse,
            reasoning_effort=self.reasoning_effort,
            extra_body=EXTRA_BODY,
            timeout=self.timeout,
        )
        content = _content_of(response)
        if not content or not content.strip():
            raise ValueError("model returned an empty message")
        return ChatResponse.model_validate_json(_extract_json(content))


def _content_of(response: Any) -> str | None:
    """Pull the assistant text out of a LiteLLM response object."""
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError, KeyError, TypeError) as exc:
        raise ValueError(f"unexpected completion response shape: {exc}") from exc


def _extract_json(content: str) -> str:
    """Recover the JSON object from a reply that may be wrapped in prose or fences.

    Structured outputs usually make this a no-op, but a fenced block or a
    leading sentence is a cheap failure to absorb rather than retry.
    """
    text = content.strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1)

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text
