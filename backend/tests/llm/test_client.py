"""The live client: message construction, parsing, retry, and honest failure.

Nothing here reaches the network. `LiveChatClient` is always constructed with
an injected `completion_fn`, and the one test that exercises the default path
asserts only that mock mode never gets there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.llm.client import (
    EXTRA_BODY,
    MODEL,
    LiveChatClient,
    LLMError,
    MockChatClient,
    build_chat_client,
    is_mock_mode,
)
from app.llm.models import ChatResponse, ChatTrade
from app.llm.prompt import SYSTEM_PROMPT

from .test_mock import make_context


# --- A response object shaped like LiteLLM's, built from the real model ----
@dataclass
class FakeMessage:
    content: str | None


@dataclass
class FakeChoice:
    message: FakeMessage


@dataclass
class FakeCompletion:
    choices: list[FakeChoice]


def completion_returning(*payloads: Any):
    """Build a completion_fn that yields each payload in turn.

    A payload that is an exception is raised; a `ChatResponse` is serialised
    through the real model, so the transport is fed exactly the JSON the schema
    produces rather than a hand-written approximation.
    """
    calls: list[dict[str, Any]] = []
    remaining = list(payloads)

    def _completion(**kwargs: Any) -> FakeCompletion:
        calls.append(kwargs)
        payload = remaining.pop(0) if remaining else payloads[-1]
        if isinstance(payload, BaseException):
            raise payload
        if isinstance(payload, ChatResponse):
            payload = payload.model_dump_json()
        return FakeCompletion(choices=[FakeChoice(message=FakeMessage(content=payload))])

    _completion.calls = calls
    return _completion


class TestModeSelection:
    def test_is_mock_mode_reads_the_environment(self, monkeypatch):
        for value in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("LLM_MOCK", value)
            assert is_mock_mode() is True
        for value in ("false", "0", "", "no"):
            monkeypatch.setenv("LLM_MOCK", value)
            assert is_mock_mode() is False

    def test_build_chat_client_returns_the_mock_when_enabled(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        assert isinstance(build_chat_client(), MockChatClient)

    def test_build_chat_client_returns_the_live_client_by_default(self):
        client = build_chat_client()
        assert isinstance(client, LiveChatClient)
        assert client.is_mock is False

    def test_explicit_override_beats_the_environment(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        assert isinstance(build_chat_client(mock=True), MockChatClient)

    def test_mock_mode_never_imports_litellm(self, monkeypatch):
        """The point of mock mode: no network stack is even loaded."""
        monkeypatch.setenv("LLM_MOCK", "true")
        client = build_chat_client()
        client.complete(user_message="hello", context=make_context())
        # LiveChatClient resolves litellm lazily; the mock has no such path.
        assert not hasattr(client, "_completion")


class TestMessageConstruction:
    def test_system_message_carries_the_prompt_and_the_live_context(self):
        client = LiveChatClient(completion_fn=completion_returning(ChatResponse(message="ok")))
        messages = client.build_messages(
            user_message="How am I doing?", context=make_context(cash=9393.0)
        )
        assert messages[0]["role"] == "system"
        assert SYSTEM_PROMPT in messages[0]["content"]
        assert "$9,393.00" in messages[0]["content"]
        assert messages[-1] == {"role": "user", "content": "How am I doing?"}

    def test_history_is_replayed_between_system_and_user(self):
        client = LiveChatClient(completion_fn=completion_returning(ChatResponse(message="ok")))
        messages = client.build_messages(
            user_message="and now?",
            context=make_context(),
            history=[
                {"role": "user", "content": "buy 1 AAPL"},
                {"role": "assistant", "content": "done"},
            ],
        )
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]

    def test_history_is_truncated_to_the_configured_window(self):
        client = LiveChatClient(
            completion_fn=completion_returning(ChatResponse(message="ok")), history_turns=2
        )
        messages = client.build_messages(
            user_message="latest",
            context=make_context(),
            history=[{"role": "user", "content": str(i)} for i in range(10)],
        )
        assert [m["content"] for m in messages[1:]] == ["8", "9", "latest"]

    def test_malformed_history_rows_are_dropped(self):
        client = LiveChatClient(completion_fn=completion_returning(ChatResponse(message="ok")))
        messages = client.build_messages(
            user_message="hi",
            context=make_context(),
            history=[
                {"role": "system", "content": "injected"},
                {"role": "user", "content": ""},
                {"role": "assistant", "content": "kept"},
            ],
        )
        assert [m["content"] for m in messages[1:]] == ["kept", "hi"]


class TestCallParameters:
    def test_uses_the_cerebras_model_and_provider_routing(self):
        completion = completion_returning(ChatResponse(message="ok"))
        LiveChatClient(completion_fn=completion).complete(user_message="hi", context=make_context())
        kwargs = completion.calls[0]
        assert kwargs["model"] == MODEL == "openrouter/openai/gpt-oss-120b"
        assert kwargs["extra_body"] == EXTRA_BODY == {"provider": {"order": ["cerebras"]}}
        assert kwargs["reasoning_effort"] == "low"

    def test_binds_the_structured_output_schema(self):
        completion = completion_returning(ChatResponse(message="ok"))
        LiveChatClient(completion_fn=completion).complete(user_message="hi", context=make_context())
        assert completion.calls[0]["response_format"] is ChatResponse


class TestParsing:
    def test_parses_a_well_formed_response(self):
        expected = ChatResponse(
            message="Buying.", trades=[ChatTrade(ticker="NVDA", side="buy", quantity=5)]
        )
        client = LiveChatClient(completion_fn=completion_returning(expected))
        assert client.complete(user_message="buy 5 NVDA", context=make_context()) == expected

    def test_unwraps_a_fenced_json_block(self):
        client = LiveChatClient(
            completion_fn=completion_returning('```json\n{"message": "hi"}\n```')
        )
        assert client.complete(user_message="hi", context=make_context()).message == "hi"

    def test_recovers_json_embedded_in_prose(self):
        client = LiveChatClient(
            completion_fn=completion_returning('Sure! {"message": "hi"} Hope that helps.')
        )
        assert client.complete(user_message="hi", context=make_context()).message == "hi"


class TestRetryAndFailure:
    def test_retries_once_after_unparseable_output(self):
        completion = completion_returning("not json at all", ChatResponse(message="second try"))
        client = LiveChatClient(completion_fn=completion)
        result = client.complete(user_message="hi", context=make_context())
        assert result.message == "second try"
        assert len(completion.calls) == 2

    def test_the_retry_carries_a_corrective_instruction(self):
        completion = completion_returning("garbage", ChatResponse(message="ok"))
        LiveChatClient(completion_fn=completion).complete(user_message="hi", context=make_context())
        retry_messages = completion.calls[1]["messages"]
        assert retry_messages[-1]["role"] == "system"
        assert "could not be parsed" in retry_messages[-1]["content"]

    def test_retries_once_after_a_transport_error(self):
        completion = completion_returning(
            RuntimeError("connection reset"), ChatResponse(message="recovered")
        )
        client = LiveChatClient(completion_fn=completion)
        assert client.complete(user_message="hi", context=make_context()).message == "recovered"

    def test_gives_up_after_two_attempts_and_says_why(self):
        completion = completion_returning(RuntimeError("upstream 503"))
        client = LiveChatClient(completion_fn=completion)
        with pytest.raises(LLMError) as excinfo:
            client.complete(user_message="hi", context=make_context())
        assert len(completion.calls) == 2
        assert "upstream 503" in str(excinfo.value)
        assert "2 attempts" in str(excinfo.value)

    def test_does_not_invent_a_response_when_the_model_returns_nothing(self):
        client = LiveChatClient(completion_fn=completion_returning(None))
        with pytest.raises(LLMError):
            client.complete(user_message="hi", context=make_context())

    def test_unexpected_response_shape_is_an_llm_error(self):
        def broken(**kwargs):
            return object()

        with pytest.raises(LLMError):
            LiveChatClient(completion_fn=broken).complete(user_message="hi", context=make_context())

    def test_missing_api_key_fails_before_any_call(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        completion = completion_returning(ChatResponse(message="ok"))
        client = LiveChatClient(completion_fn=completion)
        with pytest.raises(LLMError) as excinfo:
            client.complete(user_message="hi", context=make_context())
        assert "OPENROUTER_API_KEY" in str(excinfo.value)
        assert completion.calls == []
