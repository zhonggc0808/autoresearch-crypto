from __future__ import annotations

import numpy as np

from dex.live.common import (
    check_stop_loss,
    compute_ioc_price,
    compute_order_price,
    plan_close_order,
    plan_entry_order,
    predict_signal,
    quantize_order_size,
    target_position_from_signal,
)
from dex.strategies import PureActionStrategy


def test_compute_order_price_keeps_post_only_price_out_of_spread() -> None:
    assert compute_order_price("buy", best_bid=100.0, best_ask=100.0, tick_size=0.1) == 99.9
    assert compute_order_price("sell", best_bid=100.0, best_ask=100.0, tick_size=0.1) == 100.1


def test_compute_order_price_returns_none_when_required_quote_missing() -> None:
    assert compute_order_price("buy", best_bid=None, best_ask=100.0, tick_size=0.1) is None
    assert compute_order_price("sell", best_bid=99.9, best_ask=None, tick_size=0.1) is None


def test_compute_ioc_price_crosses_spread() -> None:
    assert compute_ioc_price("buy", best_bid=99.9, best_ask=100.0, tick_size=0.1) == 100.2
    assert compute_ioc_price("sell", best_bid=99.9, best_ask=100.0, tick_size=0.1) == 99.7


def test_target_position_from_signal_preserves_hold() -> None:
    assert target_position_from_signal(1, current_position=-1) == -1
    assert target_position_from_signal(0, current_position=-1) == 0
    assert target_position_from_signal(2, current_position=0) == 1
    assert target_position_from_signal(3, current_position=0) == -1


def test_quantize_order_size_floors_to_increment() -> None:
    assert quantize_order_size(100.0, current_price=3010.0, size_increment=0.001) == 0.033
    assert quantize_order_size(100.0, current_price=0.0, size_increment=0.001) == 0.0


def test_plan_entry_order_uses_ioc_when_maker_notional_is_too_small() -> None:
    plan = plan_entry_order(
        target_position=1,
        current_position=0,
        current_price=2000.0,
        capital_per_trade=50.0,
        size_increment=0.001,
        tick_size=0.1,
        best_bid=1999.9,
        best_ask=2000.0,
        min_maker_notional=100.0,
        fallback_to_current_price=True,
    )

    assert plan.action == "ioc"
    assert plan.side == "buy"
    assert plan.size == 0.025
    assert plan.price == 2000.2
    assert plan.trade_type == "BUY_OPEN_IOC_FALLBACK"


def test_plan_entry_order_skips_when_notional_is_below_exchange_minimum() -> None:
    plan = plan_entry_order(
        target_position=-1,
        current_position=0,
        current_price=2000.0,
        capital_per_trade=3.0,
        size_increment=0.001,
        tick_size=0.1,
        best_bid=1999.9,
        best_ask=2000.0,
        min_notional=5.0,
    )

    assert plan.action == "skip"
    assert plan.reason == "notional_below_minimum"


def test_plan_close_order_uses_maker_when_long_exit_is_profitable() -> None:
    plan = plan_close_order(
        current_position=1,
        target_position=0,
        strategy_size=0.037,
        actual_position=0.025,
        entry_price=100.0,
        current_price=103.0,
        size_increment=0.001,
        tick_size=0.1,
        best_bid=102.9,
        best_ask=103.0,
    )

    assert plan.action == "maker"
    assert plan.side == "sell"
    assert plan.position_side == "long"
    assert plan.size == 0.025
    assert plan.price == 103.0
    assert plan.trade_type == "CLOSE_LONG_Maker(TP)"
    assert plan.pnl_pct == 0.03


def test_plan_close_order_uses_taker_when_short_exit_is_losing() -> None:
    plan = plan_close_order(
        current_position=-1,
        target_position=0,
        strategy_size=0.02,
        actual_position=-0.03,
        entry_price=100.0,
        current_price=102.0,
        size_increment=0.001,
        tick_size=0.1,
        best_bid=101.9,
        best_ask=102.0,
    )

    assert plan.action == "taker"
    assert plan.side == "buy"
    assert plan.position_side == "short"
    assert plan.size == 0.02
    assert plan.price == 102.2
    assert plan.trade_type == "CLOSE_SHORT_Taker(SL)"
    assert plan.pnl_pct == -0.02


def test_plan_close_order_can_fallback_to_current_price_when_quote_missing() -> None:
    plan = plan_close_order(
        current_position=1,
        target_position=0,
        strategy_size=0.02,
        actual_position=0.02,
        entry_price=100.0,
        current_price=103.0,
        size_increment=0.001,
        tick_size=0.1,
        best_bid=None,
        best_ask=None,
        fallback_to_current_price=True,
    )

    assert plan.action == "maker"
    assert plan.price == 102.9


def test_predict_signal_handles_strategies_without_enable_short(sample_ohlcv) -> None:
    strategy = PureActionStrategy(window=15, atr_period=7, trend_ma_period=50)

    signal_id, bb_info = predict_signal(strategy, sample_ohlcv, enable_short=True)

    assert signal_id in {0, 1, 2, 3}
    assert np.isfinite(bb_info["price"])
    assert np.isfinite(bb_info["mid"])


def test_check_stop_loss_detects_long_intrabar_stop() -> None:
    state = {
        "position": 1,
        "entry_price": 100.0,
        "confirmed_entry_bar": 5,
        "bar_count": 6,
        "pending_close": False,
    }

    should_exit, reason = check_stop_loss(
        state,
        current_price=99.0,
        kline_low=96.9,
        stop_loss_pct=0.03,
        max_hold_bars=48,
    )

    assert should_exit is True
    assert "多头止损" in reason


def test_check_stop_loss_detects_time_exit() -> None:
    state = {
        "position": -1,
        "entry_price": 100.0,
        "confirmed_entry_bar": 5,
        "bar_count": 53,
        "pending_close": False,
    }

    should_exit, reason = check_stop_loss(
        state,
        current_price=100.0,
        kline_high=100.5,
        stop_loss_pct=0.03,
        max_hold_bars=48,
    )

    assert should_exit is True
    assert "时间退出" in reason
