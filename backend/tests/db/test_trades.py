"""`execute_trade_atomic`: cost basis, atomicity, concurrency, float residue."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.db import (
    DbError,
    InsufficientCashError,
    InsufficientSharesError,
    execute_trade_atomic,
    get_cash_balance,
    get_position,
    list_positions,
    list_snapshots,
    list_trades,
    set_cash_balance,
)
from app.db.connection import close_connection, get_connection


def _counts() -> tuple[float, int, int, int]:
    """(cash, positions, trades, snapshots) — the four things a trade touches."""
    conn = get_connection()
    return (
        get_cash_balance(),
        conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"],
        conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"],
        conn.execute("SELECT COUNT(*) AS n FROM portfolio_snapshots").fetchone()["n"],
    )


def test_buy_creates_position_and_moves_cash(db_path: Path) -> None:
    trade = execute_trade_atomic("AAPL", "buy", 10, 190.0, total_value_after=10000.0)

    assert trade["ticker"] == "AAPL"
    assert trade["side"] == "buy"
    assert trade["quantity"] == 10.0
    assert trade["price"] == 190.0
    assert trade["executed_at"].endswith("Z")

    position = get_position("AAPL")
    assert position is not None
    assert position["quantity"] == 10.0
    assert position["avg_cost"] == 190.0
    assert get_cash_balance() == pytest.approx(10000.0 - 1900.0)


def test_weighted_average_cost_across_two_buys_and_a_partial_sell(db_path: Path) -> None:
    execute_trade_atomic("AAPL", "buy", 10, 100.0, total_value_after=10000.0)
    execute_trade_atomic("AAPL", "buy", 30, 200.0, total_value_after=10000.0)

    position = get_position("AAPL")
    assert position is not None
    # (10*100 + 30*200) / 40 = 175
    assert position["quantity"] == pytest.approx(40.0)
    assert position["avg_cost"] == pytest.approx(175.0)

    execute_trade_atomic("AAPL", "sell", 15, 300.0, total_value_after=10000.0)

    position = get_position("AAPL")
    assert position is not None
    assert position["quantity"] == pytest.approx(25.0)
    assert position["avg_cost"] == pytest.approx(175.0), "a sell must not move the cost basis"

    # 10000 - 1000 - 6000 + 4500
    assert get_cash_balance() == pytest.approx(7500.0)


def test_sell_to_zero_deletes_the_position_row(db_path: Path) -> None:
    execute_trade_atomic("NVDA", "buy", 5, 120.0, total_value_after=10000.0)
    execute_trade_atomic("NVDA", "sell", 5, 130.0, total_value_after=10050.0)

    assert get_position("NVDA") is None
    assert list_positions() == []
    assert get_cash_balance() == pytest.approx(10000.0 - 600.0 + 650.0)


def test_hundred_small_buys_then_one_full_sell_leaves_no_residue(db_path: Path) -> None:
    for _ in range(100):
        execute_trade_atomic("MSFT", "buy", 0.01, 50.0, total_value_after=10000.0)

    held = get_position("MSFT")
    assert held is not None
    # Accumulated float error: not exactly 1.0.
    assert held["quantity"] == pytest.approx(1.0, abs=1e-9)

    execute_trade_atomic("MSFT", "sell", 1.0, 50.0, total_value_after=10000.0)

    assert get_position("MSFT") is None, "float residue must not keep the row alive"
    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0


def test_insufficient_cash_raises_and_rolls_back(db_path: Path) -> None:
    execute_trade_atomic("AAPL", "buy", 1, 100.0, total_value_after=10000.0)
    before = _counts()

    with pytest.raises(InsufficientCashError) as excinfo:
        execute_trade_atomic("TSLA", "buy", 200, 250.0, total_value_after=10000.0)

    assert "$50,000.00" in str(excinfo.value)
    assert _counts() == before, (
        "a rejected buy must leave cash, positions, trades and snapshots untouched"
    )


def test_insufficient_shares_raises_and_rolls_back(db_path: Path) -> None:
    execute_trade_atomic("AAPL", "buy", 5, 100.0, total_value_after=10000.0)
    before = _counts()

    with pytest.raises(InsufficientSharesError) as excinfo:
        execute_trade_atomic("AAPL", "sell", 10, 110.0, total_value_after=10000.0)

    assert "AAPL" in str(excinfo.value)
    assert _counts() == before

    position = get_position("AAPL")
    assert position is not None and position["quantity"] == 5.0


def test_selling_a_ticker_never_held_raises(db_path: Path) -> None:
    before = _counts()
    with pytest.raises(InsufficientSharesError):
        execute_trade_atomic("META", "sell", 1, 500.0, total_value_after=10000.0)
    assert _counts() == before


def test_trade_for_an_unknown_user_raises_and_rolls_back(db_path: Path) -> None:
    before = _counts()
    with pytest.raises(DbError):
        execute_trade_atomic("AAPL", "buy", 1, 100.0, total_value_after=10000.0, user_id="nobody")
    assert _counts() == before


def test_buy_using_the_entire_balance_is_allowed(db_path: Path) -> None:
    execute_trade_atomic("V", "buy", 40, 250.0, total_value_after=10000.0)
    assert get_cash_balance() == pytest.approx(0.0)


def test_each_trade_writes_exactly_one_snapshot(db_path: Path) -> None:
    execute_trade_atomic("AAPL", "buy", 1, 100.0, total_value_after=10001.0)
    execute_trade_atomic("AAPL", "sell", 1, 105.0, total_value_after=10005.0)

    snapshots = list_snapshots()
    # Seed snapshot plus one per trade.
    assert len(snapshots) == 3
    assert [snap["total_value"] for snap in snapshots] == [10000.0, 10001.0, 10005.0]


def test_trade_and_snapshot_share_the_transaction_timestamp(db_path: Path) -> None:
    trade = execute_trade_atomic("AAPL", "buy", 1, 100.0, total_value_after=9900.0)
    assert list_snapshots()[-1]["recorded_at"] == trade["executed_at"]


def test_ticker_is_normalised(db_path: Path) -> None:
    execute_trade_atomic("  aapl ", "buy", 1, 100.0, total_value_after=10000.0)
    assert get_position("aapl") is not None
    assert list_positions()[0]["ticker"] == "AAPL"
    assert list_trades()[0]["ticker"] == "AAPL"


def test_list_trades_is_most_recent_first_and_honours_limit(db_path: Path) -> None:
    for i in range(5):
        execute_trade_atomic("AAPL", "buy", 1, 100.0 + i, total_value_after=10000.0)

    trades = list_trades()
    assert len(trades) == 5
    assert trades[0]["price"] == 104.0
    assert trades[-1]["price"] == 100.0
    assert [trade["price"] for trade in list_trades(limit=2)] == [104.0, 103.0]


@pytest.mark.parametrize(
    ("side", "quantity", "price"),
    [("hold", 1, 100.0), ("buy", 0, 100.0), ("buy", -1, 100.0), ("buy", 1, -5.0)],
)
def test_invalid_arguments_raise_value_error(
    db_path: Path, side: str, quantity: float, price: float
) -> None:
    with pytest.raises(ValueError):
        execute_trade_atomic("AAPL", side, quantity, price, total_value_after=10000.0)  # type: ignore[arg-type]


def test_concurrent_buys_never_overdraw_cash(db_path: Path) -> None:
    """Eight threads race to spend $2,000 each out of a $10,000 balance.

    Exactly five can succeed. The in-transaction re-check — not the caller's
    pre-check — is what makes that true.
    """
    set_cash_balance(10000.0)
    barrier = threading.Barrier(8)
    successes: list[str] = []
    rejections: list[str] = []

    def worker() -> None:
        barrier.wait()
        try:
            trade = execute_trade_atomic("AAPL", "buy", 1, 2000.0, total_value_after=10000.0)
            successes.append(trade["id"])
        except InsufficientCashError:
            rejections.append("rejected")
        finally:
            close_connection()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(successes) == 5
    assert len(rejections) == 3
    assert get_cash_balance() == pytest.approx(0.0)
    assert get_cash_balance() >= 0.0

    position = get_position("AAPL")
    assert position is not None
    assert position["quantity"] == pytest.approx(5.0)
    assert len(list_trades()) == 5
    assert len(list_snapshots()) == 6  # seed + five fills


def test_concurrent_buys_and_sells_keep_the_books_balanced(db_path: Path) -> None:
    """Buyers and sellers interleave; cash and shares must stay non-negative."""
    execute_trade_atomic("AAPL", "buy", 20, 100.0, total_value_after=10000.0)
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        barrier.wait()
        try:
            side = "buy" if index % 2 == 0 else "sell"
            execute_trade_atomic("AAPL", side, 1, 100.0, total_value_after=10000.0)
        except (InsufficientCashError, InsufficientSharesError):
            pass
        except Exception as exc:  # pragma: no cover - surfaced by the assert below
            errors.append(exc)
        finally:
            close_connection()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert get_cash_balance() >= 0.0
    position = get_position("AAPL")
    assert position is not None
    assert position["quantity"] > 0

    # Cash and shares must reconcile against the trade log exactly.
    net_cash = 10000.0
    net_shares = 0.0
    for trade in list_trades(limit=1000):
        signed = trade["quantity"] * trade["price"]
        if trade["side"] == "buy":
            net_cash -= signed
            net_shares += trade["quantity"]
        else:
            net_cash += signed
            net_shares -= trade["quantity"]
    assert get_cash_balance() == pytest.approx(net_cash)
    assert position["quantity"] == pytest.approx(net_shares)
