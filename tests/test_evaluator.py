from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dex.config import BARS_PER_YEAR
from dex.strategies.base import StrategyEvaluator


def test_annualized_return_uses_5m_bars_per_year() -> None:
    evaluator = StrategyEvaluator()
    equity = np.linspace(100.0, 110.0, BARS_PER_YEAR)

    metrics = evaluator.compute_metrics(equity, [])

    assert np.isclose(metrics["total_return"], 0.10)
    assert 0.099 < metrics["annualized_return"] < 0.101


def test_position_sizes_default_matches_old_full_size_behavior() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 0])
    prices = np.array([100.0, 110.0])

    full_equity, _ = evaluator.simulate(signals, prices)
    sized_equity, _ = evaluator.simulate(signals, prices, position_sizes=np.ones(2))

    assert full_equity.tolist() == sized_equity.tolist()


def test_position_sizes_validate_length() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    try:
        evaluator.simulate(np.array([2, 0]), np.array([100.0, 110.0]), position_sizes=np.ones(1))
    except ValueError as exc:
        assert "position_sizes" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_half_size_entry_freezes_for_existing_position() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    equity, _ = evaluator.simulate(
        np.array([2, 1, 0]),
        np.array([100.0, 200.0, 200.0]),
        position_sizes=np.array([0.5, 1.0, 1.0]),
    )

    assert equity[-1] == 150.0


def test_funding_rate_debits_long_and_credits_short() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame({"funding_rate": [0.0, 0.01, 0.0]})

    long_equity, long_trades = evaluator.simulate(
        np.array([2, 1, 0]), np.array([100.0, 100.0, 100.0]), df=df
    )
    short_equity, short_trades = evaluator.simulate(
        np.array([3, 1, 0]), np.array([100.0, 100.0, 100.0]), df=df
    )

    assert long_equity[-1] == pytest.approx(99.0)
    assert long_trades[-1]["funding_pnl"] == pytest.approx(-1.0)
    assert short_equity[-1] == pytest.approx(101.0)
    assert short_trades[-1]["funding_pnl"] == pytest.approx(1.0)


def test_stop_config_funding_rate_debits_long_and_credits_short() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame({"funding_rate": [0.0, 0.01, 0.0]})

    long_equity, long_trades = evaluator.simulate(
        np.array([2, 1, 0]),
        np.array([100.0, 100.0, 100.0]),
        df=df,
        stop_config={"base_size": 1.0},
    )
    short_equity, short_trades = evaluator.simulate(
        np.array([3, 1, 0]),
        np.array([100.0, 100.0, 100.0]),
        df=df,
        stop_config={"base_size": 1.0},
    )

    assert long_equity[-1] == pytest.approx(99.0)
    assert long_trades[-1]["funding_pnl"] == pytest.approx(-1.0)
    assert short_equity[-1] == pytest.approx(101.0)
    assert short_trades[-1]["funding_pnl"] == pytest.approx(1.0)


def test_zero_size_does_not_force_close_existing_position() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    equity, _ = evaluator.simulate(
        np.array([2, 1, 0]),
        np.array([100.0, 200.0, 200.0]),
        position_sizes=np.array([0.5, 0.0, 1.0]),
    )

    assert equity[-1] == 150.0


def test_zero_size_flip_closes_old_position_without_opening_new_one() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    equity, trades = evaluator.simulate(
        np.array([2, 3, 0]),
        np.array([100.0, 110.0, 100.0]),
        position_sizes=np.array([1.0, 0.0, 1.0]),
    )

    assert equity[-1] == 110.0
    assert [t["type"] for t in trades] == ["buy", "sell"]


def test_simulate_open_event_has_entry_fields() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(np.array([2, 1, 0]), np.array([100.0, 200.0, 200.0]))

    buy = next(t for t in trades if t["type"] == "buy")
    assert buy["entry_price"] == 100.0
    assert buy["entry_notional"] == 100.0
    assert buy["entry_size"] == 1.0


def test_simulate_long_close_event_has_entry_and_exit_fields() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(np.array([2, 0]), np.array([100.0, 110.0]))

    sell = next(t for t in trades if t["type"] == "sell")
    assert sell["entry_price"] == 100.0
    assert sell["exit_price"] == 110.0
    assert sell["entry_notional"] == 100.0


def test_simulate_short_close_event_has_entry_and_exit_fields() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(np.array([3, 0]), np.array([100.0, 90.0]))

    cover = next(t for t in trades if t["type"] == "buy_cover")
    assert cover["entry_price"] == 100.0
    assert cover["exit_price"] == 90.0
    assert cover["entry_notional"] == 100.0


def test_signal_bar_open_execution_uses_open_for_trades_and_close_for_marking() -> None:
    evaluator = StrategyEvaluator(
        initial_capital=100.0,
        commission=0.0,
        slippage=0.0,
        execution_price="signal_bar_open",
    )
    df = pd.DataFrame({"open": [90.0, 180.0]})

    equity, trades = evaluator.simulate(np.array([2, 0]), np.array([100.0, 200.0]), df=df)

    assert equity[0] == pytest.approx(100.0 / 90.0 * 100.0)
    assert equity[-1] == pytest.approx(200.0)
    assert trades[0]["entry_price"] == 90.0
    assert trades[1]["exit_price"] == 180.0


def test_signal_bar_open_execution_requires_open_column() -> None:
    evaluator = StrategyEvaluator(execution_price="signal_bar_open")

    with pytest.raises(ValueError, match="open column"):
        evaluator.simulate(np.array([2, 0]), np.array([100.0, 110.0]))


def test_mixed_retest_open_without_hints_matches_close_execution() -> None:
    df = pd.DataFrame({"open": [90.0, 180.0]})
    signals = np.array([2, 0])
    prices = np.array([100.0, 200.0])
    close_ev = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    mixed_ev = StrategyEvaluator(
        initial_capital=100.0,
        commission=0.0,
        slippage=0.0,
        execution_price="mixed_retest_open",
    )

    close_equity, close_trades = close_ev.simulate(signals, prices, df=df)
    mixed_equity, mixed_trades = mixed_ev.simulate(signals, prices, df=df)

    assert mixed_equity.tolist() == close_equity.tolist()
    assert mixed_trades == close_trades


def test_mixed_retest_open_only_scheduled_entry_uses_open() -> None:
    evaluator = StrategyEvaluator(
        initial_capital=100.0,
        commission=0.0,
        slippage=0.0,
        execution_price="mixed_retest_open",
    )
    df = pd.DataFrame({"open": [90.0, 180.0]})

    equity, trades = evaluator.simulate(
        np.array([2, 0]),
        np.array([100.0, 200.0]),
        df=df,
        execution_hints=np.array(["scheduled_entry_executed", "normal_exit"]),
    )

    assert equity[0] == pytest.approx(100.0 / 90.0 * 100.0)
    assert equity[-1] == pytest.approx(200.0 / 90.0 * 100.0)
    assert trades[0]["entry_price"] == 90.0
    assert trades[1]["exit_price"] == 200.0


def test_mixed_retest_open_blocks_hold_reentry_from_flat() -> None:
    evaluator = StrategyEvaluator(
        initial_capital=100.0,
        commission=0.0,
        slippage=0.0,
        execution_price="mixed_retest_open",
    )
    df = pd.DataFrame({"open": [90.0, 180.0, 190.0]})

    equity, trades = evaluator.simulate(
        np.array([2, 2, 0]),
        np.array([100.0, 200.0, 210.0]),
        df=df,
        execution_hints=np.array(["hold_long", "", "normal_exit"]),
    )

    assert equity.tolist() == [100.0, 100.0, 100.0]
    assert trades == []


def test_mixed_retest_open_rejects_scheduled_entry_while_position_exists() -> None:
    evaluator = StrategyEvaluator(execution_price="mixed_retest_open")
    df = pd.DataFrame({"open": [100.0, 100.0]})

    with pytest.raises(ValueError, match="scheduled_entry_executed"):
        evaluator.simulate(
            np.array([2, 3]),
            np.array([100.0, 100.0]),
            df=df,
            execution_hints=np.array(["scheduled_entry_executed", "scheduled_entry_executed"]),
        )


def test_stop_config_base_size_controls_entries() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    equity, trades = evaluator.simulate(
        np.array([2, 0]),
        np.array([100.0, 200.0]),
        stop_config={"base_size": 0.5},
    )

    assert equity[-1] == 150.0
    assert next(t for t in trades if t["type"] == "buy")["entry_size"] == 0.5


def test_stop_config_rejects_non_one_position_sizes() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    with pytest.raises(ValueError, match="stop_config.base_size"):
        evaluator.simulate(
            np.array([2, 0]),
            np.array([100.0, 200.0]),
            position_sizes=np.array([0.5, 0.5]),
            stop_config={"base_size": 0.5},
        )


def test_equity_dd_kill_switch_closes_position_and_blocks_reentry() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 1, 2]),
        np.array([100.0, 70.0, 70.0]),
        stop_config={
            "base_size": 1.0,
            "equity_dd_sizing": {
                "enabled": True,
                "tiers": [(0.05, 1.0)],
                "no_new_entry_dd": 0.20,
                "kill_switch_dd": 0.25,
            },
        },
    )

    kill = [t for t in trades if t.get("exit_reason") == "kill_switch"]
    assert len(kill) == 1
    assert kill[0]["is_partial"] is False
    assert [t["type"] for t in trades].count("buy") == 1


def test_adverse_stop_reduce_half_then_signal_close_reconciles() -> None:
    from dex.strategies.trade_ledger import build_logical_trade_ledger

    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 1, 0]),
        np.array([100.0, 94.0, 94.0]),
        stop_config={
            "base_size": 1.0,
            "adverse_stop": {
                "enabled": True,
                "threshold_pct": -0.05,
                "action": "reduce_half",
            },
        },
    )

    partials = [t for t in trades if t.get("is_partial")]
    assert len(partials) == 1
    assert partials[0]["exit_reason"] == "adverse_stop"
    assert partials[0]["closed_fraction"] == 0.5
    event_pnl = sum(t.get("pnl", 0.0) for t in trades if t.get("pnl") is not None)
    logical_pnl = sum(t["total_pnl"] for t in build_logical_trade_ledger(trades))
    assert logical_pnl == pytest.approx(event_pnl)


def test_signal_exit_has_priority_over_adverse_stop() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 0]),
        np.array([100.0, 94.0]),
        stop_config={
            "base_size": 1.0,
            "adverse_stop": {
                "enabled": True,
                "threshold_pct": -0.05,
                "action": "close",
            },
        },
    )

    assert [t.get("exit_reason") for t in trades if t.get("pnl") is not None] == ["signal"]


def test_equity_dd_sizing_reduces_subsequent_entry_only() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 0, 2, 0]),
        np.array([100.0, 90.0, 90.0, 100.0]),
        stop_config={
            "base_size": 1.0,
            "equity_dd_sizing": {
                "enabled": True,
                "tiers": [(0.05, 1.0), (0.20, 0.5)],
                "no_new_entry_dd": 0.30,
                "kill_switch_dd": 0.50,
            },
        },
    )

    buys = [t for t in trades if t["type"] == "buy"]
    assert buys[0]["entry_notional"] == 100.0
    assert buys[1]["entry_notional"] == 45.0
    assert buys[1]["entry_size"] == 0.5


def test_invariant_no_stop_on_entry_bar() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 1]),
        np.array([100.0, 94.0]),
        stop_config={
            "base_size": 1.0,
            "adverse_stop": {
                "enabled": True,
                "threshold_pct": -0.05,
                "action": "close",
            },
        },
    )

    stop_closes = [t for t in trades if t.get("exit_reason") == "adverse_stop"]
    assert stop_closes
    assert all(t["step"] > t["entry_step"] for t in stop_closes)


def test_invariant_at_most_one_risk_close_per_bar() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2] + [1] * 10),
        np.array([100.0] + [92.0] * 10),
        stop_config={
            "base_size": 1.0,
            "adverse_stop": {
                "enabled": True,
                "threshold_pct": -0.05,
                "action": "close",
            },
        },
    )

    counts: dict[tuple[int, int], int] = {}
    for event in trades:
        if event.get("exit_reason") is None:
            continue
        key = (event["step"], event["parent_trade_id"])
        counts[key] = counts.get(key, 0) + 1
    assert all(count == 1 for count in counts.values())


def test_time_in_loss_stop_reduce_half_at_horizon() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 1, 1, 0]),
        np.array([100.0, 99.0, 98.0, 98.0]),
        stop_config={
            "base_size": 1.0,
            "time_in_loss_stop": {
                "enabled": True,
                "tiers": [(2, -0.01, "reduce_half")],
            },
        },
    )

    partials = [t for t in trades if t.get("is_partial")]
    assert len(partials) == 1
    assert partials[0]["step"] == 2
    assert partials[0]["exit_reason"] == "time_in_loss_stop"
    assert partials[0]["closed_fraction"] == 0.5


def test_time_in_loss_stop_close_at_horizon() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 1, 1, 1]),
        np.array([100.0, 99.0, 96.0, 96.0]),
        stop_config={
            "base_size": 1.0,
            "time_in_loss_stop": {
                "enabled": True,
                "tiers": [(2, -0.03, "close")],
            },
        },
    )

    closes = [t for t in trades if t.get("exit_reason") == "time_in_loss_stop"]
    assert len(closes) == 1
    assert closes[0]["step"] == 2
    assert closes[0]["is_partial"] is False


def test_time_in_loss_close_beats_adverse_reduce_same_bar() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    _, trades = evaluator.simulate(
        np.array([2, 1, 1]),
        np.array([100.0, 98.0, 98.0]),
        stop_config={
            "base_size": 1.0,
            "adverse_stop": {
                "enabled": True,
                "threshold_pct": -0.01,
                "action": "reduce_half",
            },
            "time_in_loss_stop": {
                "enabled": True,
                "tiers": [(1, -0.01, "close")],
            },
        },
    )

    risk_events = [t for t in trades if t.get("exit_reason")]
    assert len(risk_events) == 1
    assert risk_events[0]["exit_reason"] == "time_in_loss_stop"
    assert risk_events[0]["is_partial"] is False


def test_squeeze_stop_reduce_half_short() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame(
        {
            "high": [100.0, 106.0, 101.0],
            "low": [100.0, 100.0, 100.0],
            "close": [100.0, 101.0, 101.0],
            "atr": [1.0, 1.0, 1.0],
            "donchian_upper_lagged": [100.0, 100.0, 100.0],
        }
    )

    _, trades = evaluator.simulate(
        np.array([3, 1, 0]),
        df["close"].to_numpy(dtype=float),
        df=df,
        stop_config={
            "base_size": 1.0,
            "squeeze_stop": {
                "enabled": True,
                "atr_multiple": 2.5,
                "require_channel_break": True,
                "action": "reduce_half",
            },
        },
    )

    [event] = [t for t in trades if t.get("exit_reason") == "squeeze_stop"]
    assert event["step"] == 1
    assert event["is_partial"] is True
    assert event["closed_fraction"] == 0.5


def test_squeeze_stop_only_applies_to_shorts() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame(
        {
            "high": [100.0, 106.0],
            "low": [100.0, 100.0],
            "close": [100.0, 101.0],
            "atr": [1.0, 1.0],
            "donchian_upper_lagged": [100.0, 100.0],
        }
    )

    _, trades = evaluator.simulate(
        np.array([2, 1]),
        df["close"].to_numpy(dtype=float),
        df=df,
        stop_config={
            "base_size": 1.0,
            "squeeze_stop": {
                "enabled": True,
                "atr_multiple": 2.5,
                "require_channel_break": True,
                "action": "reduce_half",
            },
        },
    )

    assert [t for t in trades if t.get("exit_reason") == "squeeze_stop"] == []


def test_squeeze_stop_uses_lagged_donchian_upper() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame(
        {
            "high": [100.0, 106.0],
            "low": [100.0, 100.0],
            "close": [100.0, 101.0],
            "atr": [1.0, 1.0],
            "donchian_upper": [100.0, 106.0],
        }
    )

    _, trades = evaluator.simulate(
        np.array([3, 1]),
        df["close"].to_numpy(dtype=float),
        df=df,
        stop_config={
            "base_size": 1.0,
            "squeeze_stop": {
                "enabled": True,
                "atr_multiple": 2.5,
                "require_channel_break": True,
                "channel_upper_lagged": True,
                "action": "close",
            },
        },
    )

    [event] = [t for t in trades if t.get("exit_reason") == "squeeze_stop"]
    assert event["step"] == 1
    assert event["is_partial"] is False


def test_break_even_stop_reduce_half_long_after_mfe_retrace() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame(
        {
            "high": [100.0, 104.0, 100.0],
            "low": [100.0, 99.0, 100.0],
            "close": [100.0, 100.0, 100.0],
        }
    )

    _, trades = evaluator.simulate(
        np.array([2, 1, 0]),
        df["close"].to_numpy(dtype=float),
        df=df,
        stop_config={
            "base_size": 1.0,
            "break_even_stop": {
                "enabled": True,
                "trigger_mfe_pct": 0.03,
                "stop_level_pct": 0.0,
                "action": "reduce_half",
            },
        },
    )

    [event] = [t for t in trades if t.get("exit_reason") == "break_even_stop"]
    assert event["step"] == 1
    assert event["is_partial"] is True
    assert event["closed_fraction"] == 0.5


def test_break_even_stop_close_short_after_mfe_retrace() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame(
        {
            "high": [100.0, 101.0],
            "low": [100.0, 96.0],
            "close": [100.0, 100.0],
        }
    )

    _, trades = evaluator.simulate(
        np.array([3, 1]),
        df["close"].to_numpy(dtype=float),
        df=df,
        stop_config={
            "base_size": 1.0,
            "break_even_stop": {
                "enabled": True,
                "trigger_mfe_pct": 0.03,
                "stop_level_pct": 0.0,
                "action": "close",
            },
        },
    )

    [event] = [t for t in trades if t.get("exit_reason") == "break_even_stop"]
    assert event["step"] == 1
    assert event["is_partial"] is False


def test_break_even_stop_waits_for_mfe_threshold() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    df = pd.DataFrame(
        {
            "high": [100.0, 102.0],
            "low": [100.0, 99.0],
            "close": [100.0, 100.0],
        }
    )

    _, trades = evaluator.simulate(
        np.array([2, 1]),
        df["close"].to_numpy(dtype=float),
        df=df,
        stop_config={
            "base_size": 1.0,
            "break_even_stop": {
                "enabled": True,
                "trigger_mfe_pct": 0.03,
                "stop_level_pct": 0.0,
                "action": "close",
            },
        },
    )

    assert [t for t in trades if t.get("exit_reason") == "break_even_stop"] == []
