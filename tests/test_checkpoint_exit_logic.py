from __future__ import annotations

import json

from dex.checkpoints import (
    apply_exit_logic_to_strategy_params,
    build_channel_breakout_strategy_from_checkpoint,
    is_regime_channel_breakout_checkpoint,
    load_checkpoint,
)


def _checkpoint_with_exit_logic() -> dict:
    base_regime = {
        "candidate": "unit",
        "description": "unit",
        "strategy_params": {
            "entry_lookback": 3,
            "exit_lookback": 9,
            "min_hold_bars": 2,
            "enable_long": True,
            "enable_short": True,
        },
        "permission": {"allow_long": True, "allow_short": True},
    }
    return {
        "strategy_type": "exit_logic_channel_breakout",
        "regime_change_policy": "permission_based",
        "exit_logic": {
            "exit_lookback": 5,
            "take_profit_pct": 0.08,
            "stop_loss_pct": 0.12,
            "max_hold_bars": 720,
            "profit_lock": {
                "enabled": True,
                "activate_profit_pct": 0.06,
                "giveback_ratio": 0.35,
                "trailing_atr_multiplier": 2.5,
                "atr_period": 21,
                "min_hold_bars_before_lock": 12,
            },
        },
        "bull": dict(base_regime),
        "bear": dict(base_regime),
        "neutral": dict(base_regime),
    }


def test_exit_logic_channel_breakout_is_v21_compatible_checkpoint() -> None:
    checkpoint = _checkpoint_with_exit_logic()

    assert is_regime_channel_breakout_checkpoint(checkpoint)


def test_position_sized_channel_breakout_is_v21_compatible_checkpoint() -> None:
    checkpoint = _checkpoint_with_exit_logic()
    checkpoint["strategy_type"] = "position_sized_channel_breakout"
    checkpoint["position_sizing"] = {"mode": "fixed_fraction", "fixed_fraction": 0.5}

    assert is_regime_channel_breakout_checkpoint(checkpoint)


def test_profit_lock_exit_logic_maps_to_strategy_params_without_mutating_input() -> None:
    checkpoint = _checkpoint_with_exit_logic()
    original = dict(checkpoint["bull"]["strategy_params"])

    params = apply_exit_logic_to_strategy_params(
        checkpoint["bull"]["strategy_params"],
        checkpoint["exit_logic"],
    )

    assert checkpoint["bull"]["strategy_params"] == original
    assert params["exit_lookback"] == 5
    assert params["take_profit_pct"] == 0.08
    assert params["stop_loss_pct"] == 0.12
    assert params["max_hold_bars"] == 720
    assert params["profit_lock_enabled"] is True
    assert params["profit_lock_activate_pct"] == 0.06
    assert params["profit_lock_giveback_ratio"] == 0.35
    assert params["profit_lock_atr_multiplier"] == 2.5
    assert params["profit_lock_atr_period"] == 21
    assert params["profit_lock_min_hold_bars"] == 12


def test_build_channel_breakout_strategy_applies_top_level_exit_logic() -> None:
    checkpoint = _checkpoint_with_exit_logic()

    strategy = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bull")

    assert strategy.entry_lookback == 3
    assert strategy.exit_lookback == 5
    assert strategy.profit_lock_enabled is True
    assert strategy.profit_lock_activate_pct == 0.06
    assert strategy.profit_lock_giveback_ratio == 0.35
    assert strategy.profit_lock_atr_multiplier == 2.5


def test_regime_specific_profit_lock_overrides_global_profit_lock() -> None:
    checkpoint = _checkpoint_with_exit_logic()
    checkpoint["exit_logic"]["profit_lock"] = {
        "enabled": False,
        "activate_profit_pct": 0.20,
        "giveback_ratio": 0.80,
    }
    checkpoint["exit_logic"]["profit_lock_by_regime"] = {
        "bear": {
            "enabled": True,
            "activate_profit_pct": 0.08,
            "giveback_ratio": 0.35,
            "trailing_atr_multiplier": 3.0,
            "atr_period": 14,
            "min_hold_bars_before_lock": 72,
        }
    }

    bull = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bull")
    bear = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bear")

    assert bull.profit_lock_enabled is False
    assert bull.profit_lock_activate_pct == 0.20
    assert bear.profit_lock_enabled is True
    assert bear.profit_lock_activate_pct == 0.08
    assert bear.profit_lock_giveback_ratio == 0.35
    assert bear.profit_lock_atr_multiplier == 3.0
    assert bear.profit_lock_atr_period == 14
    assert bear.profit_lock_min_hold_bars == 72


def test_load_checkpoint_accepts_nested_candidate_json(tmp_path) -> None:
    candidate = {
        "experiment_id": "exp_unit",
        "status": "research_only",
        "params": _checkpoint_with_exit_logic(),
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")

    checkpoint = load_checkpoint(path)

    assert checkpoint["strategy_type"] == "exit_logic_channel_breakout"
    assert checkpoint["experiment_id"] == "exp_unit"
    assert checkpoint["status"] == "research_only"
    assert is_regime_channel_breakout_checkpoint(checkpoint)
