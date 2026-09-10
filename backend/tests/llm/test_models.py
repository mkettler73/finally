"""The structured-output and HTTP models (API_CONTRACT.md sections 5 and 6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.models import (
    ActionResult,
    ChatHistoryReply,
    ChatMessageOut,
    ChatReply,
    ChatRequest,
    ChatResponse,
    ChatTrade,
    ChatWatchlistChange,
)


class TestChatResponse:
    def test_defaults_to_empty_action_arrays(self):
        response = ChatResponse(message="Nothing to do.")
        assert response.trades == []
        assert response.watchlist_changes == []

    def test_parses_the_contract_example(self):
        response = ChatResponse.model_validate_json(
            '{"message": "Buying.", '
            '"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}], '
            '"watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}'
        )
        assert response.message == "Buying."
        assert response.trades == [ChatTrade(ticker="AAPL", side="buy", quantity=10.0)]
        assert response.watchlist_changes == [ChatWatchlistChange(ticker="PYPL", action="add")]

    def test_null_arrays_are_coerced_to_empty(self):
        response = ChatResponse.model_validate_json(
            '{"message": "hi", "trades": null, "watchlist_changes": null}'
        )
        assert response.trades == []
        assert response.watchlist_changes == []

    def test_message_is_required(self):
        with pytest.raises(ValidationError):
            ChatResponse.model_validate({"trades": []})

    def test_side_is_constrained(self):
        with pytest.raises(ValidationError):
            ChatTrade(ticker="AAPL", side="short", quantity=1)

    def test_watchlist_action_is_constrained(self):
        with pytest.raises(ValidationError):
            ChatWatchlistChange(ticker="AAPL", action="delete")

    def test_fractional_quantities_are_allowed(self):
        assert ChatTrade(ticker="AAPL", side="buy", quantity=0.5).quantity == 0.5

    def test_schema_carries_all_three_fields(self):
        """The schema is what the model is bound to; it must name every field."""
        properties = ChatResponse.model_json_schema()["properties"]
        assert set(properties) == {"message", "trades", "watchlist_changes"}


class TestChatRequest:
    def test_strips_and_requires_a_message(self):
        assert ChatRequest(message="  hello  ").message == "hello"
        with pytest.raises(ValidationError):
            ChatRequest(message="   ")

    def test_rejects_an_absurdly_long_message(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 4001)


class TestClientFacingModels:
    def test_action_result_matches_the_contract_shape(self):
        action = ActionResult(
            type="trade",
            status="ok",
            detail="Bought 5 NVDA @ $121.40",
            data={"ticker": "NVDA", "side": "buy", "quantity": 5.0},
        )
        assert action.model_dump() == {
            "type": "trade",
            "status": "ok",
            "detail": "Bought 5 NVDA @ $121.40",
            "data": {"ticker": "NVDA", "side": "buy", "quantity": 5.0},
        }

    def test_chat_reply_actions_default_to_empty(self):
        reply = ChatReply(message="hi", created_at="2026-09-09T14:32:05.123456Z")
        assert reply.actions == []

    def test_history_actions_are_nullable_for_user_turns(self):
        history = ChatHistoryReply(
            messages=[
                ChatMessageOut(
                    id="1",
                    role="user",
                    content="Buy 5 NVDA",
                    actions=None,
                    created_at="2026-09-09T14:32:05.123456Z",
                )
            ]
        )
        assert history.messages[0].actions is None
