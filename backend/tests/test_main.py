"""Tests for the FastAPI app wiring: lifespan, health check, and SSE routing.

NOTE: do not try to consume /api/stream/prices through TestClient. Its blocking
portal never returns headers for an unbounded stream, so the call hangs rather
than failing. The generator is tested directly in tests/market/test_stream.py;
the live endpoint is covered by the Playwright E2E suite.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from massive.rest.models import TickerSnapshot


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """The lifespan initialises the database, so keep it out of the repo's
    runtime db/finally.db and give every test a fresh file."""
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "finally.db"))
    from app.db import reset_db_for_tests
    from app.db.connection import close_connection

    reset_db_for_tests()
    yield
    close_connection()


@pytest.fixture
def client(monkeypatch):
    # Force the simulator (no real API calls in tests).
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

    # Reload the module so a fresh price_cache/app is built per test, since
    # main.py holds process-lifetime module state.
    import importlib

    from app import main as main_module

    importlib.reload(main_module)

    with TestClient(main_module.app) as test_client:
        yield test_client, main_module


def test_health_check(client):
    test_client, _ = client
    response = test_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_starts_market_data_source(client):
    test_client, main_module = client
    assert test_client.app.state.market_source is not None
    assert set(test_client.app.state.market_source.get_tickers()) == set(
        main_module.DEFAULT_TICKERS
    )


def test_stream_route_reads_from_app_price_cache(client):
    """The SSE router must read from the same cache the lifespan populates."""
    test_client, main_module = client
    assert test_client.app.state.price_cache is main_module.price_cache


def test_default_tickers_match_seed_prices():
    from app.main import DEFAULT_TICKERS
    from app.market.seed_prices import SEED_PRICES

    assert set(DEFAULT_TICKERS) == set(SEED_PRICES.keys())


def test_uses_simulator_when_no_massive_key(client, monkeypatch):
    test_client, main_module = client
    from app.market.simulator import SimulatorDataSource

    assert isinstance(test_client.app.state.market_source, SimulatorDataSource)


def test_stream_route_is_registered_exactly_once(client):
    """A module-scope router would accumulate duplicate, cache-shadowing routes."""
    test_client, _ = client
    paths = [r.path for r in test_client.app.routes if r.path.startswith("/api/stream")]
    assert paths == ["/api/stream/prices"]


def test_massive_source_fills_the_cache_end_to_end(monkeypatch):
    """Regression: the client used to read LastTrade.timestamp, which does not
    exist, so every snapshot was skipped and the cache stayed empty forever."""
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")

    import importlib

    from app import main as main_module

    importlib.reload(main_module)

    def fake_snapshots(self):
        return [
            TickerSnapshot.from_dict(
                {"ticker": t, "lastTrade": {"p": 190.5, "t": 1605192894630916600}}
            )
            for t in self._tickers
        ]

    with patch("app.market.massive_client.RESTClient", MagicMock()):
        with patch(
            "app.market.massive_client.MassiveDataSource._fetch_snapshots",
            fake_snapshots,
        ):
            with TestClient(main_module.app) as test_client:
                cache = test_client.app.state.price_cache
                assert len(cache) == len(main_module.DEFAULT_TICKERS)
                assert cache.get_price("AAPL") == 190.5
                # Unix seconds, not nanoseconds.
                assert 1_000_000_000 < cache.get("AAPL").timestamp < 3_000_000_000
