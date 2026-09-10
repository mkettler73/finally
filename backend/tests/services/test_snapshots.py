"""The periodic portfolio snapshot task.

The interval is injected, never slept for real (standing rule for this suite).
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.session_open import SessionOpenTracker
from app.services.snapshots import (
    SNAPSHOT_INTERVAL_SECONDS,
    record_snapshot_now,
    run_snapshot_loop,
)


async def run_until(condition, task, timeout=2.0):
    """Let the loop run until ``condition`` holds, then cancel it."""
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not condition():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("snapshot task never made progress")
            await asyncio.sleep(0)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_default_interval_is_thirty_seconds():
    assert SNAPSHOT_INTERVAL_SECONDS == 30.0


def test_record_snapshot_now_values_the_portfolio(db, price_cache):
    db.cash = 500.0
    db.positions["AAPL"] = {
        "id": "p1",
        "ticker": "AAPL",
        "quantity": 2.0,
        "avg_cost": 100.0,
        "updated_at": "2026-09-09T14:30:00.000000Z",
    }

    record_snapshot_now(price_cache)

    assert db.snapshots[-1]["total_value"] == pytest.approx(880.0)


async def test_loop_records_snapshots(db, price_cache):
    before = len(db.snapshots)
    task = asyncio.create_task(run_snapshot_loop(price_cache, interval=0))

    await run_until(lambda: len(db.snapshots) > before, task)

    assert db.snapshots[-1]["total_value"] == pytest.approx(10000.0)


async def test_loop_updates_session_opens(db, price_cache):
    tracker = SessionOpenTracker()
    task = asyncio.create_task(run_snapshot_loop(price_cache, interval=0, session_opens=tracker))

    await run_until(lambda: tracker.get("AAPL") is not None, task)

    assert tracker.get("AAPL") == 190.0


async def test_loop_survives_a_failing_snapshot(db, price_cache, monkeypatch):
    """Losing one chart point must not take the task down for the process."""
    import app.db as db_module

    calls: list[int] = []
    original = db_module.record_snapshot

    def flaky(total_value, user_id="default"):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db locked")
        original(total_value, user_id)

    monkeypatch.setattr(db_module, "record_snapshot", flaky)
    task = asyncio.create_task(run_snapshot_loop(price_cache, interval=0))

    await run_until(lambda: len(calls) >= 2, task)

    assert len(db.snapshots) >= 1


async def test_loop_stops_on_cancel(db, price_cache):
    task = asyncio.create_task(run_snapshot_loop(price_cache, interval=0))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
