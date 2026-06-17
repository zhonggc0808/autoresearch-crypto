from __future__ import annotations

import pandas as pd
import pytest

from dex.strategies.trade_ledger import build_logical_trade_ledger, enrich_trade_ledger


def _df(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [r[0] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[2] for r in rows],
        },
        index=pd.date_range("2024-01-01", periods=len(rows), freq="5min"),
    )


def test_enrich_trade_ledger_long_mae_mfe() -> None:
    trades = [
        {
            "type": "buy",
            "step": 0,
            "entry_price": 100.0,
            "entry_notional": 100.0,
            "entry_size": 1.0,
        },
        {
            "type": "sell",
            "step": 3,
            "entry_step": 0,
            "entry_price": 100.0,
            "exit_price": 108.0,
            "entry_notional": 100.0,
            "entry_size": 1.0,
            "pnl": 8.0,
        },
    ]
    bars = _df([(100.0, 100.0, 100.0), (102.0, 96.0, 101.0), (111.0, 94.0, 105.0), (109.0, 103.0, 108.0)])

    [row] = enrich_trade_ledger(trades, bars)

    assert row["side"] == "long"
    assert row["return_pct"] == pytest.approx(0.08)
    assert row["mae_pct"] == pytest.approx(-0.06)
    assert row["mfe_pct"] == pytest.approx(0.11)
    assert row["mae_step"] == 2
    assert row["mfe_step"] == 2
    assert row["duration_bars"] == 3
    assert row["time_to_mae_hours"] == pytest.approx(2 * 5 / 60)


def test_enrich_trade_ledger_short_mae_mfe_and_regime() -> None:
    trades = [
        {
            "type": "sell_short",
            "step": 0,
            "entry_price": 100.0,
            "entry_notional": 100.0,
            "entry_size": 0.5,
        },
        {
            "type": "buy_cover",
            "step": 3,
            "entry_step": 0,
            "entry_price": 100.0,
            "exit_price": 92.0,
            "entry_notional": 100.0,
            "entry_size": 0.5,
            "pnl": 8.0,
        },
    ]
    bars = _df([(100.0, 100.0, 100.0), (103.0, 98.0, 101.0), (109.0, 89.0, 95.0), (94.0, 91.0, 92.0)])
    bars["regime"] = ["BULL", "BULL", "BEAR", "BEAR"]

    [row] = enrich_trade_ledger(trades, bars)

    assert row["side"] == "short"
    assert row["entry_size"] == 0.5
    assert row["return_pct"] == pytest.approx(0.08)
    assert row["mae_pct"] == pytest.approx(-0.09)
    assert row["mfe_pct"] == pytest.approx(0.11)
    assert row["mae_step"] == 2
    assert row["mfe_step"] == 2
    assert row["entry_regime"] == "BULL"
    assert row["exit_regime"] == "BEAR"


def test_enrich_trade_ledger_empty_window_uses_close_return() -> None:
    trades = [
        {
            "type": "buy",
            "step": 0,
            "entry_price": 100.0,
            "entry_notional": 100.0,
            "entry_size": 1.0,
        },
        {
            "type": "sell",
            "step": 0,
            "entry_step": 0,
            "entry_price": 100.0,
            "exit_price": 97.0,
            "entry_notional": 100.0,
            "entry_size": 1.0,
            "pnl": -3.0,
        },
    ]

    [row] = enrich_trade_ledger(trades, _df([(105.0, 90.0, 97.0)]))

    assert row["mae_pct"] == pytest.approx(-0.03)
    assert row["mfe_pct"] == 0.0
    assert row["mae_step"] == 0
    assert row["mfe_step"] == 0


def test_build_logical_trade_ledger_aggregates_partial_closes() -> None:
    events = [
        {"type": "buy", "step": 0, "trade_id": 1, "parent_trade_id": 1},
        {
            "type": "sell",
            "step": 2,
            "entry_step": 0,
            "pnl": -2.5,
            "is_partial": True,
            "parent_trade_id": 1,
            "exit_reason": "adverse_stop",
            "closed_fraction": 0.5,
            "remaining_fraction": 0.5,
        },
        {
            "type": "sell",
            "step": 5,
            "entry_step": 0,
            "pnl": 4.0,
            "is_partial": False,
            "parent_trade_id": 1,
            "exit_reason": "signal",
            "closed_fraction": 0.5,
            "remaining_fraction": 0.0,
        },
    ]

    [row] = build_logical_trade_ledger(events)

    assert row["parent_trade_id"] == 1
    assert row["total_pnl"] == pytest.approx(1.5)
    assert row["is_winner"] is True
    assert row["partial_close_count"] == 1
    assert row["exit_reasons"] == ["adverse_stop", "signal"]
