from __future__ import annotations

import importlib

import pandas as pd
import pytest

from dex.live.common import EntryOrderPlan
from dex.live.position_sizing import (
    EXP0140_VOL_REF,
    compute_vol_target_sizing,
    estimate_entry_fillability,
    shifted_realized_vol_20d,
)


def test_shifted_realized_vol_matches_exp0139_shift() -> None:
    df = pd.DataFrame({"close": [100.0, 101.0, 103.0, 107.0, 109.0]})
    realized = shifted_realized_vol_20d(df, window_bars=2, bars_per_year=100.0)
    expected = df["close"].pct_change().rolling(2, min_periods=2).std().shift(1) * 10.0

    pd.testing.assert_series_equal(realized, expected)


def test_vol_target_falls_back_when_history_is_short() -> None:
    df = pd.DataFrame({"close": [100.0, 101.0, 102.0]})

    decision = compute_vol_target_sizing(df, window_bars=2)

    assert decision.multiplier == 1.0
    assert decision.fallback is True
    assert decision.reason == "insufficient_history"
    assert decision.required_bars == 4


def test_vol_target_clips_high_realized_vol_to_minimum_size() -> None:
    df = pd.DataFrame({"close": [100.0, 200.0, 100.0, 200.0, 100.0]})

    decision = compute_vol_target_sizing(
        df,
        vol_ref=0.10,
        clip_min=0.40,
        clip_max=1.00,
        window_bars=2,
    )

    assert decision.reason == "ok"
    assert decision.multiplier == pytest.approx(0.40)
    assert decision.raw_multiplier is not None
    assert decision.raw_multiplier < 0.40


def test_entry_fillability_flags_bitget_lot_size_problem() -> None:
    low = estimate_entry_fillability(
        notional=10.0,
        current_price=1600.0,
        lot_size=0.01,
        min_order_notional=5.0,
    )
    high = estimate_entry_fillability(
        notional=40.0,
        current_price=1600.0,
        lot_size=0.01,
        min_order_notional=5.0,
    )

    assert low.raw_size == pytest.approx(0.00625)
    assert low.rounded_size == 0.0
    assert low.fillable is False
    assert high.rounded_size == pytest.approx(0.02)
    assert high.notional == pytest.approx(32.0)
    assert high.fillable is True


def test_bitget_maker_pending_preserves_vol_target_context(monkeypatch) -> None:
    module = importlib.import_module("live_bitget_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _exchange, _symbol: True)
    monkeypatch.setattr(module, "get_position", lambda _exchange, _symbol: 0.0)
    monkeypatch.setattr(
        module,
        "plan_entry_order",
        lambda **_kwargs: EntryOrderPlan(
            action="maker",
            side="buy",
            position_side="long",
            size=0.04,
            price=1599.9,
            notional=64.0,
            target_position=1,
            trade_type="BUY_OPEN_MAKER",
        ),
    )
    monkeypatch.setattr(
        module,
        "place_limit_order",
        lambda *_args, **_kwargs: "bitget-order-1",
    )
    monkeypatch.setattr(
        module,
        "place_market_order",
        lambda *_args, **_kwargs: pytest.fail("market order should not be used"),
    )

    state = {
        "position": 0,
        "strategy_size": 0.0,
        "trades": [],
        "bar_count": 42,
        "entry_size_multiplier": 0.72,
        "vol_target_multiplier": 0.72,
        "vol_target_realized_vol_20d": EXP0140_VOL_REF / 0.72,
        "vol_target_reason": "ok",
    }

    new_state = module.execute_trade(
        signal_id=2,
        exchange=object(),
        symbol="ETH/USDT:USDT",
        tick_sz=0.1,
        lot_sz=0.01,
        capital_per_trade=64.0,
        state=state,
        current_price=1600.0,
        best_bid=1599.9,
        best_ask=1600.0,
    )

    assert new_state["pending_open"] is True
    assert new_state["pending_open_capital_per_trade"] == pytest.approx(64.0)
    assert new_state["pending_open_entry_size_multiplier"] == pytest.approx(0.72)
    assert new_state["pending_open_vol_target_multiplier"] == pytest.approx(0.72)
    assert new_state["pending_open_vol_target_reason"] == "ok"
    assert new_state["trades"][0]["entry_size_multiplier"] == pytest.approx(0.72)
    assert new_state["trades"][0]["vol_target_multiplier"] == pytest.approx(0.72)


def test_pending_open_sizing_restore_and_clear() -> None:
    module = importlib.import_module("live_bitget_quant")
    state = {
        "pending_open_capital_per_trade": 64.0,
        "pending_open_entry_size_multiplier": 0.72,
        "pending_open_vol_target_multiplier": 0.72,
        "pending_open_vol_target_realized_vol_20d": 1.10,
        "pending_open_vol_target_reason": "ok",
        "entry_size_multiplier": 1.0,
        "vol_target_multiplier": 1.0,
    }

    assert module.pending_open_capital_per_trade(state, 100.0) == pytest.approx(64.0)

    module.restore_pending_open_sizing(state)
    assert state["entry_size_multiplier"] == pytest.approx(0.72)
    assert state["vol_target_multiplier"] == pytest.approx(0.72)
    assert state["vol_target_realized_vol_20d"] == pytest.approx(1.10)

    module.clear_pending_open_sizing(state)
    assert state["pending_open_capital_per_trade"] == 0.0
    assert state["pending_open_entry_size_multiplier"] == 1.0
    assert state["pending_open_vol_target_realized_vol_20d"] is None
