"""Snapshots, cash balance and position reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import (
    STARTING_CASH,
    DbError,
    execute_trade_atomic,
    get_cash_balance,
    get_position,
    list_positions,
    list_snapshots,
    record_snapshot,
    set_cash_balance,
)
from app.db.connection import get_connection


def test_cash_balance_round_trip(db_path: Path) -> None:
    assert get_cash_balance() == STARTING_CASH
    set_cash_balance(1234.56)
    assert get_cash_balance() == pytest.approx(1234.56)


def test_cash_balance_for_unknown_user_raises(db_path: Path) -> None:
    with pytest.raises(DbError):
        get_cash_balance(user_id="nobody")
    with pytest.raises(DbError):
        set_cash_balance(1.0, user_id="nobody")


def test_record_snapshot_appends(db_path: Path) -> None:
    record_snapshot(10500.0)
    record_snapshot(10750.0)

    snapshots = list_snapshots()
    assert [snap["total_value"] for snap in snapshots] == [10000.0, 10500.0, 10750.0]
    assert all(snap["recorded_at"].endswith("Z") for snap in snapshots)


def test_list_snapshots_returns_most_recent_n_oldest_first(db_path: Path) -> None:
    for value in range(1, 21):
        record_snapshot(float(value))

    recent = list_snapshots(limit=5)
    assert [snap["total_value"] for snap in recent] == [16.0, 17.0, 18.0, 19.0, 20.0]

    everything = list_snapshots(limit=500)
    assert len(everything) == 21  # 20 plus the seed row
    assert everything[0]["total_value"] == 10000.0


def test_list_snapshots_limit_of_one_returns_the_newest(db_path: Path) -> None:
    record_snapshot(11000.0)
    assert [snap["total_value"] for snap in list_snapshots(limit=1)] == [11000.0]


def test_snapshot_row_shape(db_path: Path) -> None:
    record_snapshot(10100.0)
    snapshot = list_snapshots(limit=1)[0]
    assert set(snapshot) == {"total_value", "recorded_at"}


def test_position_reads(db_path: Path) -> None:
    assert list_positions() == []
    assert get_position("AAPL") is None

    execute_trade_atomic("AAPL", "buy", 3, 100.0, total_value_after=10000.0)
    execute_trade_atomic("GOOGL", "buy", 2, 150.0, total_value_after=10000.0)

    positions = list_positions()
    assert [position["ticker"] for position in positions] == ["AAPL", "GOOGL"]
    assert set(positions[0]) == {"id", "ticker", "quantity", "avg_cost", "updated_at"}

    aapl = get_position("aapl")
    assert aapl is not None and aapl["quantity"] == 3.0


def test_positions_are_scoped_by_user(db_path: Path) -> None:
    get_connection().execute(
        "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
        ("other", 500.0, "2026-01-01T00:00:00.000000Z"),
    )
    execute_trade_atomic("AAPL", "buy", 1, 100.0, total_value_after=10000.0)
    execute_trade_atomic("NVDA", "buy", 1, 100.0, total_value_after=400.0, user_id="other")

    assert [position["ticker"] for position in list_positions()] == ["AAPL"]
    assert [position["ticker"] for position in list_positions(user_id="other")] == ["NVDA"]
    assert get_cash_balance() == pytest.approx(9900.0)
    assert get_cash_balance(user_id="other") == pytest.approx(400.0)
