"""Shared fixtures for the chat suite.

Standing rule for this repository: tests never touch the network. Nothing here
constructs a client that can reach OpenRouter - the live client is always
driven through an injected `completion_fn`.
"""

from __future__ import annotations

import pytest

from app.market import PriceCache

from .fakes import FakeDb, FakeSource, FakeTrading, install_fakes


@pytest.fixture(autouse=True)
def no_llm_mock_env(monkeypatch):
    """Start every test from a known LLM_MOCK / API-key state."""
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")


@pytest.fixture
def db() -> FakeDb:
    return FakeDb(
        watchlist=[
            {"id": "wl-1", "ticker": "AAPL", "added_at": "2026-09-09T14:30:00.000000Z"},
            {"id": "wl-2", "ticker": "GOOGL", "added_at": "2026-09-09T14:30:01.000000Z"},
        ],
        snapshots=[
            {"total_value": 10000.0, "recorded_at": "2026-09-09T14:30:00.000000Z"},
        ],
    )


@pytest.fixture
def trading() -> FakeTrading:
    return FakeTrading()


@pytest.fixture(autouse=True)
def fakes(monkeypatch, db: FakeDb, trading: FakeTrading) -> None:
    install_fakes(monkeypatch, db, trading)


@pytest.fixture
def price_cache() -> PriceCache:
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("AAPL", 192.0)
    cache.update("GOOGL", 175.0)
    return cache


@pytest.fixture
def source() -> FakeSource:
    return FakeSource(["AAPL", "GOOGL"])
