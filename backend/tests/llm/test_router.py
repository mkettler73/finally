"""The chat endpoints end to end, with the model and the data layer faked."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.llm.client import LLMError
from app.llm.models import ChatResponse, ChatTrade, ChatWatchlistChange
from app.llm.router import create_chat_router

from .fakes import TradeError


class ScriptedClient:
    """A chat client that returns prepared `ChatResponse` objects.

    Built from the real Pydantic model rather than a `MagicMock`, so a router
    that mishandles the response shape fails here instead of passing green.
    """

    is_mock = False

    def __init__(self, *responses: ChatResponse | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, *, user_message, context, history=None):
        self.calls.append(
            {"user_message": user_message, "context": context, "history": history or []}
        )
        response = self._responses.pop(0) if self._responses else ChatResponse(message="ok")
        if isinstance(response, Exception):
            raise response
        return response


def make_app(price_cache, client, source=None, **kwargs) -> FastAPI:
    app = FastAPI()
    app.include_router(create_chat_router(price_cache, client=client, **kwargs))
    if source is not None:
        app.state.market_source = source
    return app


@pytest.fixture
def client_factory(price_cache, source):
    def _factory(*responses, use_state_source: bool = True, **kwargs):
        chat_client = ScriptedClient(*responses)
        app = make_app(
            price_cache,
            chat_client,
            source=source if use_state_source else None,
            **kwargs,
        )
        return TestClient(app), chat_client

    return _factory


class TestChatEndpoint:
    def test_a_plain_conversation_returns_no_actions(self, client_factory, db):
        api, _ = client_factory(ChatResponse(message="You are up 1.2% today."))
        response = api.post("/api/chat", json={"message": "How am I doing?"})

        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "You are up 1.2% today."
        assert body["actions"] == []
        assert body["created_at"]

    def test_both_turns_are_persisted(self, client_factory, db):
        api, _ = client_factory(ChatResponse(message="Noted."))
        api.post("/api/chat", json={"message": "Hello"})

        assert [(row["role"], row["content"]) for row in db.chat] == [
            ("user", "Hello"),
            ("assistant", "Noted."),
        ]
        assert db.chat[0]["actions"] is None
        assert db.chat[1]["actions"] == []

    def test_the_new_message_is_not_replayed_as_history(self, client_factory, db):
        db.chat = [
            {
                "id": "1",
                "role": "user",
                "content": "earlier",
                "actions": None,
                "created_at": "t",
            }
        ]
        api, chat_client = client_factory(ChatResponse(message="ok"))
        api.post("/api/chat", json={"message": "now"})

        assert chat_client.calls[0]["history"] == [{"role": "user", "content": "earlier"}]
        assert chat_client.calls[0]["user_message"] == "now"

    def test_context_reflects_live_state(self, client_factory, db):
        db.cash_balance = 8097.5
        db.positions = [{"id": "p1", "ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.25}]
        api, chat_client = client_factory(ChatResponse(message="ok"))
        api.post("/api/chat", json={"message": "status"})

        context = chat_client.calls[0]["context"]
        assert context.cash_balance == 8097.5
        assert context.position_count == 1

    def test_a_trade_is_executed_and_reported_inline(self, client_factory, trading):
        api, _ = client_factory(
            ChatResponse(
                message="Bought 5 NVDA.",
                trades=[ChatTrade(ticker="NVDA", side="buy", quantity=5)],
            )
        )
        body = api.post("/api/chat", json={"message": "Buy 5 NVDA"}).json()

        assert trading.calls[0]["ticker"] == "NVDA"
        assert body["actions"] == [
            {
                "type": "trade",
                "status": "ok",
                "detail": "Bought 5 NVDA @ $100.00",
                "data": {
                    "ticker": "NVDA",
                    "side": "buy",
                    "quantity": 5.0,
                    "price": 100.0,
                    "total": 500.0,
                },
            }
        ]

    def test_a_rejected_trade_is_a_200_with_an_error_action(self, client_factory, trading):
        trading.error = TradeError("INSUFFICIENT_CASH", "Not enough cash.")
        api, _ = client_factory(
            ChatResponse(
                message="Trying to buy.",
                trades=[ChatTrade(ticker="TSLA", side="buy", quantity=200)],
            )
        )
        response = api.post("/api/chat", json={"message": "Buy 200 TSLA"})

        assert response.status_code == 200
        actions = response.json()["actions"]
        assert actions[0]["status"] == "error"
        assert actions[0]["detail"] == "Not enough cash."

    def test_watchlist_changes_reach_the_source_from_app_state(self, client_factory, source, db):
        api, _ = client_factory(
            ChatResponse(
                message="Watching PYPL.",
                watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")],
            )
        )
        body = api.post("/api/chat", json={"message": "watch PYPL"}).json()

        assert source.added == ["PYPL"]
        assert body["actions"][0]["detail"] == "Added PYPL to the watchlist"

    def test_actions_are_persisted_with_the_assistant_turn(self, client_factory, db):
        api, _ = client_factory(
            ChatResponse(
                message="Added.",
                watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")],
            )
        )
        api.post("/api/chat", json={"message": "add PYPL"})

        stored = db.chat[-1]["actions"]
        assert stored[0]["type"] == "watchlist"
        assert stored[0]["status"] == "ok"


class TestChatErrors:
    def test_an_llm_failure_is_a_502_in_the_error_envelope(self, client_factory):
        api, _ = client_factory(LLMError("upstream refused the request"))
        response = api.post("/api/chat", json={"message": "hi"})

        assert response.status_code == 502
        assert response.json() == {
            "detail": {"code": "LLM_ERROR", "message": "upstream refused the request"}
        }

    def test_an_unexpected_client_failure_is_also_a_502(self, client_factory):
        api, _ = client_factory(RuntimeError("boom"))
        response = api.post("/api/chat", json={"message": "hi"})
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "LLM_ERROR"

    def test_an_empty_message_is_rejected(self, client_factory):
        api, _ = client_factory(ChatResponse(message="ok"))
        assert api.post("/api/chat", json={"message": "   "}).status_code == 422

    def test_a_missing_message_is_rejected(self, client_factory):
        api, _ = client_factory(ChatResponse(message="ok"))
        assert api.post("/api/chat", json={}).status_code == 422


class TestHistoryEndpoint:
    def test_returns_stored_messages_oldest_first(self, client_factory, db):
        db.chat = [
            {
                "id": "1",
                "role": "user",
                "content": "Buy 5 NVDA",
                "actions": None,
                "created_at": "2026-09-09T14:32:05.123456Z",
            },
            {
                "id": "2",
                "role": "assistant",
                "content": "Bought.",
                "actions": [{"type": "trade", "status": "ok", "detail": "d", "data": {}}],
                "created_at": "2026-09-09T14:32:06.123456Z",
            },
        ]
        api, _ = client_factory()
        body = api.get("/api/chat/history").json()

        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        assert body["messages"][0]["actions"] is None
        assert body["messages"][1]["actions"][0]["type"] == "trade"

    def test_empty_history(self, client_factory):
        api, _ = client_factory()
        assert api.get("/api/chat/history").json() == {"messages": []}

    def test_limit_is_passed_through(self, client_factory, db):
        db.chat = [
            {
                "id": str(i),
                "role": "user",
                "content": str(i),
                "actions": None,
                "created_at": "t",
            }
            for i in range(10)
        ]
        api, _ = client_factory()
        body = api.get("/api/chat/history?limit=3").json()
        assert [m["content"] for m in body["messages"]] == ["7", "8", "9"]

    @pytest.mark.parametrize("limit", [0, -1, 5000])
    def test_out_of_range_limits_are_rejected(self, client_factory, limit):
        api, _ = client_factory()
        assert api.get(f"/api/chat/history?limit={limit}").status_code == 422


class TestRouterWiring:
    def test_registers_exactly_the_two_contracted_paths(self, price_cache):
        router = create_chat_router(price_cache, client=ScriptedClient())
        assert sorted(route.path for route in router.routes) == [
            "/api/chat",
            "/api/chat/history",
        ]

    def test_an_injected_source_beats_app_state(self, price_cache, db):
        from .fakes import FakeSource

        injected = FakeSource()
        state_source = FakeSource()
        chat_client = ScriptedClient(
            ChatResponse(
                message="ok",
                watchlist_changes=[ChatWatchlistChange(ticker="PYPL", action="add")],
            )
        )
        app = make_app(price_cache, chat_client, source=state_source, market_source=injected)
        TestClient(app).post("/api/chat", json={"message": "add PYPL"})

        assert injected.added == ["PYPL"]
        assert state_source.added == []

    def test_mock_mode_is_used_when_no_client_is_injected(self, price_cache, db, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        app = FastAPI()
        app.include_router(create_chat_router(price_cache))
        body = TestClient(app).post("/api/chat", json={"message": "How am I doing?"}).json()

        assert body["message"] == (
            "You are holding 0 positions with $10,000.00 in cash. "
            "Ask me to buy or sell a ticker, or to add one to your watchlist."
        )
        assert body["actions"] == []

    def test_user_id_is_threaded_into_the_data_layer(self, price_cache, db):
        chat_client = ScriptedClient(ChatResponse(message="ok"))
        app = make_app(price_cache, chat_client, user_id="someone-else")
        seen: list[str] = []
        import sys

        original = sys.modules["app.db"].append_chat_message

        def spy(role, content, actions=None, user_id="default"):
            seen.append(user_id)
            return original(role, content, actions, user_id)

        sys.modules["app.db"].append_chat_message = spy
        TestClient(app).post("/api/chat", json={"message": "hi"})
        assert seen == ["someone-else", "someone-else"]
