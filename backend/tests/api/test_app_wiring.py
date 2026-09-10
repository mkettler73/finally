"""app.main wiring: routes, lifespan state, the snapshot task, the chat router.

The SSE endpoint is never requested here: TestClient's blocking portal never
returns headers for an unbounded stream, so the call hangs rather than failing.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.services.market_registry import clear_market_source, get_market_source

from ..services.fake_db import install_fake_db


@pytest.fixture
def main_module(monkeypatch):
    """A freshly reloaded app.main with a fake db and no real market key."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("FINALLY_STATIC_DIR", raising=False)
    store = install_fake_db(monkeypatch)

    from app import main as module

    importlib.reload(module)
    module.store = store  # type: ignore[attr-defined]
    yield module

    clear_market_source()
    importlib.reload(module)


def test_all_contract_routes_are_registered(main_module):
    routes = {
        (route.path, method)
        for route in main_module.app.routes
        for method in getattr(route, "methods", [])
    }

    assert ("/api/health", "GET") in routes
    assert ("/api/stream/prices", "GET") in routes
    assert ("/api/portfolio", "GET") in routes
    assert ("/api/portfolio/trade", "POST") in routes
    assert ("/api/portfolio/history", "GET") in routes
    assert ("/api/watchlist", "GET") in routes
    assert ("/api/watchlist", "POST") in routes
    assert ("/api/watchlist/{ticker}", "DELETE") in routes
    # The chat endpoints belong here too. Omitting them is how the app once
    # booted, logged a warning, and served no /api/chat at all while this test
    # stayed green.
    assert ("/api/chat", "POST") in routes
    assert ("/api/chat/history", "GET") in routes


def test_lifespan_populates_state_and_registry(main_module):
    with TestClient(main_module.app) as client:
        state = client.app.state

        assert state.price_cache is main_module.price_cache
        assert state.session_opens is main_module.session_opens
        assert get_market_source() is state.market_source
        # start() seeds the cache, so day-change baselines exist immediately.
        assert state.session_opens.get("AAPL") is not None

    assert get_market_source() is None


def test_lifespan_initialises_the_database(main_module):
    with TestClient(main_module.app):
        pass

    assert main_module.store.init_calls == 1


def test_snapshot_task_runs_and_is_cancelled_on_shutdown(main_module):
    with TestClient(main_module.app) as client:
        task = client.app.state.snapshot_task
        assert not task.done()

    assert task.cancelled() or task.done()


def test_endpoints_work_against_the_real_app(main_module):
    with TestClient(main_module.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/portfolio").json()["cash_balance"] == 10000.0
        assert len(client.get("/api/watchlist").json()["tickers"]) == 10


def test_a_trade_through_the_real_app_prices_from_the_live_cache(main_module):
    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"}
        )

        assert response.status_code == 200
        price = response.json()["trade"]["price"]
        assert price == main_module.price_cache.get_price("AAPL")


def test_chat_router_is_built_from_the_real_llm_package(main_module):
    """The real app.llm must yield a router carrying the contract's endpoints.

    This deliberately exercises the actual package rather than a stand-in. An
    earlier version of this test injected a synthetic `app.llm` module shaped
    the way this layer *assumed* the LLM package looked, asserted the loader
    found it, and passed -- while the real package exposed only a
    `create_chat_router` factory, so the running app served no /api/chat at all.
    A test that builds its own counterpart can only ever confirm its own guess.
    """
    router = main_module._load_chat_router()

    assert isinstance(router, APIRouter)
    routes = {
        (route.path, method) for route in router.routes for method in getattr(route, "methods", [])
    }
    assert ("/api/chat", "POST") in routes
    assert ("/api/chat/history", "GET") in routes


def test_missing_chat_router_does_not_break_the_app(main_module, monkeypatch):
    """A broken LLM package must cost the chat endpoint, not the whole app."""
    import sys

    # Setting a sys.modules entry to None makes the next import of it raise
    # ImportError, which is exactly the failure the loader guards against.
    monkeypatch.setitem(sys.modules, "app.llm", None)

    assert main_module._load_chat_router() is None
    assert any(route.path == "/api/health" for route in main_module.app.routes)


def test_default_tickers_match_seed_prices(main_module):
    from app.market.seed_prices import SEED_PRICES

    assert set(main_module.DEFAULT_TICKERS) == set(SEED_PRICES.keys())


def test_static_mount_is_last(main_module, tmp_path, monkeypatch):
    """A mount at "/" swallows everything after it, so it must come last."""
    (tmp_path / "index.html").write_text("<html>finally</html>")
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(tmp_path))

    module = importlib.reload(main_module)

    assert getattr(module.app.routes[-1], "name", None) == "static"
    with TestClient(module.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert "finally" in client.get("/dashboard").text


async def test_snapshot_task_name_is_stable(main_module):
    """Named so it is identifiable in a task dump when debugging a hang."""
    async with main_module.lifespan(main_module.app):
        task = main_module.app.state.snapshot_task
        assert task.get_name() == "portfolio-snapshots"
        await asyncio.sleep(0)
