"""SessionOpenTracker: the baseline behind the watchlist's day-change column."""

from __future__ import annotations

from app.market import PriceCache
from app.services.session_open import SessionOpenTracker


def test_first_price_wins():
    tracker = SessionOpenTracker()

    tracker.observe("AAPL", 190.0)
    tracker.observe("AAPL", 195.0)

    assert tracker.get("AAPL") == 190.0


def test_unknown_ticker_has_no_open():
    assert SessionOpenTracker().get("AAPL") is None


def test_none_price_is_ignored():
    """A ticker with no cached price must not be baselined at nothing."""
    tracker = SessionOpenTracker()

    tracker.observe("AAPL", None)

    assert tracker.get("AAPL") is None
    tracker.observe("AAPL", 190.0)
    assert tracker.get("AAPL") == 190.0


def test_zero_price_is_recorded():
    tracker = SessionOpenTracker()

    tracker.observe("AAPL", 0.0)

    assert tracker.get("AAPL") == 0.0


def test_observe_cache_baselines_every_ticker():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("GOOGL", 175.0)
    tracker = SessionOpenTracker()

    tracker.observe_cache(cache)
    cache.update("AAPL", 200.0)
    tracker.observe_cache(cache)

    assert tracker.get("AAPL") == 190.0
    assert tracker.get("GOOGL") == 175.0


def test_forget_restarts_the_baseline():
    tracker = SessionOpenTracker()
    tracker.observe("AAPL", 190.0)

    tracker.forget("AAPL")
    tracker.observe("AAPL", 200.0)

    assert tracker.get("AAPL") == 200.0


def test_forget_is_safe_for_unknown_tickers():
    SessionOpenTracker().forget("NOPE")


def test_reset_clears_everything():
    tracker = SessionOpenTracker()
    tracker.observe("AAPL", 190.0)

    tracker.reset()

    assert tracker.get("AAPL") is None
