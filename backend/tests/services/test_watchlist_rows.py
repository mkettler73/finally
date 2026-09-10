"""Watchlist row shape (API_CONTRACT §4)."""

from __future__ import annotations

import pytest

from app.market import PriceCache
from app.services.session_open import SessionOpenTracker
from app.services.watchlist import build_watchlist_row

ENTRY = {"id": "w1", "ticker": "AAPL", "added_at": "2026-09-09T14:30:00.000000Z"}


def test_row_without_a_price_is_all_nulls():
    row = build_watchlist_row(ENTRY, PriceCache(), SessionOpenTracker())

    assert row == {
        "ticker": "AAPL",
        "price": None,
        "previous_price": None,
        "change": None,
        "change_percent": None,
        "direction": "flat",
        "session_open": None,
        "session_change": None,
        "session_change_percent": None,
        "added_at": ENTRY["added_at"],
    }


def test_row_mirrors_the_price_cache_tick_over_tick():
    cache = PriceCache()
    cache.update("AAPL", 191.9)
    cache.update("AAPL", 192.1)

    row = build_watchlist_row(ENTRY, cache, SessionOpenTracker())

    assert row["price"] == 192.1
    assert row["previous_price"] == 191.9
    assert row["change"] == pytest.approx(0.2)
    assert row["change_percent"] == pytest.approx(0.1042, abs=1e-4)
    assert row["direction"] == "up"


def test_session_change_is_measured_against_the_session_open():
    cache = PriceCache()
    tracker = SessionOpenTracker()
    cache.update("AAPL", 190.0)
    tracker.observe_cache(cache)
    cache.update("AAPL", 192.1)

    row = build_watchlist_row(ENTRY, cache, tracker)

    assert row["session_open"] == 190.0
    assert row["session_change"] == pytest.approx(2.1)
    assert row["session_change_percent"] == pytest.approx(1.1053, abs=1e-4)


def test_session_change_is_null_until_a_price_arrives():
    tracker = SessionOpenTracker()
    tracker.observe("AAPL", 190.0)

    row = build_watchlist_row(ENTRY, PriceCache(), tracker)

    assert row["session_open"] == 190.0
    assert row["session_change"] is None


def test_zero_session_open_does_not_divide_by_zero():
    cache = PriceCache()
    cache.update("AAPL", 5.0)
    tracker = SessionOpenTracker()
    tracker.observe("AAPL", 0.0)

    row = build_watchlist_row(ENTRY, cache, tracker)

    assert row["session_change_percent"] == 0.0
