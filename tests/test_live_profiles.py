from __future__ import annotations

import importlib

from dex.checkpoints import is_regime_channel_breakout_checkpoint, load_checkpoint
from dex.live.profiles import (
    get_live_strategy_profile,
    profile_checkpoint_map,
    resolve_checkpoint_path,
    risk_config_for_profile,
)


def test_live_profiles_keep_old_versions_risk_noop() -> None:
    for name in [
        "channel_breakout_v2",
        "channel_breakout_v2_regime_filter_50_200",
        "channel_breakout_v2_1_balanced",
        "channel_breakout_v2_2_mtg_bcd",
        "channel_breakout_v2_2_m375_bbm375_1p5",
        "channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen",
        "channel_breakout_v2_2_m375_bbm375_1p5_retest_w72_tol50bp_bbdist20_nextopen",
    ]:
        profile = get_live_strategy_profile(name)
        assert profile.risk_profile == "none"
        assert risk_config_for_profile(name) == {"risk_profile": "none"}


def test_live_profile_v23_names_combo_balanced() -> None:
    profile = get_live_strategy_profile("channel_breakout_v2_3_combo_balanced")
    risk = risk_config_for_profile(profile.name)

    assert profile.strategy_version == "2.3"
    assert profile.checkpoint == "checkpoints/channel_breakout_375_432.pt"
    assert risk["risk_profile"] == "combo_balanced"
    assert risk["base_size"] == 0.425
    assert risk["adverse_stop"]["threshold_pct"] == -0.05
    assert risk["adverse_stop"]["action"] == "reduce_half"


def test_live_profile_v2_regime_filter_checkpoint_metadata() -> None:
    profile = get_live_strategy_profile("channel_breakout_v2_regime_filter_50_200")
    checkpoint = load_checkpoint(profile.checkpoint)

    assert profile.strategy_version == "2.0-regime-filter"
    assert profile.checkpoint == "checkpoints/channel_breakout_v2_regime_filter_50_200.pt"
    assert checkpoint["params"]["entry_lookback"] == 375
    assert checkpoint["params"]["min_hold_bars"] == 432
    assert checkpoint["signal_filter"] == {
        "type": "regime_short_filter",
        "fast_days": 50,
        "slow_days": 200,
    }


def test_resolve_checkpoint_uses_override_or_profile() -> None:
    assert (
        resolve_checkpoint_path(None, "channel_breakout_v2_3_combo_balanced")
        == "checkpoints/channel_breakout_375_432.pt"
    )
    assert resolve_checkpoint_path("custom.pt", "channel_breakout_v2") == "custom.pt"


def test_profile_checkpoint_map_supports_all_live_entrypoints() -> None:
    profiles = profile_checkpoint_map()

    assert "channel_breakout_v2" in profiles
    assert "channel_breakout_v2_regime_filter_50_200" in profiles
    assert "channel_breakout_v2_1_balanced" in profiles
    assert "channel_breakout_v2_2_mtg_bcd" in profiles
    assert "channel_breakout_v2_2_m375_bbm375_1p5" in profiles
    assert "channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen" in profiles
    assert "channel_breakout_v2_2_m375_bbm375_1p5_retest_w72_tol50bp_bbdist20_nextopen" in profiles
    assert "channel_breakout_v2_3_combo_balanced" in profiles
    assert "hybrid_mm" in profiles


def test_binance_v22_profile_has_regime_signal_helper() -> None:
    checkpoint_path = resolve_checkpoint_path(None, "channel_breakout_v2_2_mtg_bcd")
    checkpoint = load_checkpoint(checkpoint_path)
    module = importlib.import_module("live_binance_quant")

    assert is_regime_channel_breakout_checkpoint(checkpoint)
    assert hasattr(module, "_v21_generate_signal")


def test_live_profile_v22_bb_checkpoint_loads_bollinger_params() -> None:
    from dex.checkpoints import build_channel_breakout_strategy_from_checkpoint

    profile = get_live_strategy_profile("channel_breakout_v2_2_m375_bbm375_1p5")
    checkpoint = load_checkpoint(profile.checkpoint)

    assert profile.strategy_version == "2.2-bb375-1.5"
    assert is_regime_channel_breakout_checkpoint(checkpoint)

    for regime in ("bull", "bear", "neutral"):
        strategy = build_channel_breakout_strategy_from_checkpoint(checkpoint, regime)
        assert strategy.bollinger_breakout_enabled is True
        assert strategy.bollinger_window == 375
        assert strategy.bollinger_std_dev == 1.5


def test_live_profile_v22_retest_checkpoint_loads_retest_params() -> None:
    from dex.checkpoints import build_channel_breakout_strategy_from_checkpoint

    name = "channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen"
    profile = get_live_strategy_profile(name)
    checkpoint = load_checkpoint(profile.checkpoint)

    assert profile.strategy_version == "2.2-bb375-1.5-retest-w96-tol50bp-nextopen"
    assert is_regime_channel_breakout_checkpoint(checkpoint)

    for regime in ("bull", "bear", "neutral"):
        strategy = build_channel_breakout_strategy_from_checkpoint(checkpoint, regime)
        assert strategy.bollinger_breakout_enabled is True
        assert strategy.retest_enabled is True
        assert strategy.retest_window_bars == 96
        assert strategy.retest_tolerance_pct == 0.005
        assert strategy.retest_entry_delay_bars == 1
        assert strategy.retest_require_bollinger_confirmation is True


def test_live_profile_v22_retest_quality_checkpoint_loads_w72_bbdist_params() -> None:
    from dex.checkpoints import build_channel_breakout_strategy_from_checkpoint

    name = "channel_breakout_v2_2_m375_bbm375_1p5_retest_w72_tol50bp_bbdist20_nextopen"
    profile = get_live_strategy_profile(name)
    checkpoint = load_checkpoint(profile.checkpoint)

    assert profile.strategy_version == "2.2-bb375-1.5-retest-w72-tol50bp-bbdist20-nextopen"
    assert is_regime_channel_breakout_checkpoint(checkpoint)

    for regime in ("bull", "bear", "neutral"):
        strategy = build_channel_breakout_strategy_from_checkpoint(checkpoint, regime)
        assert strategy.bollinger_breakout_enabled is True
        assert strategy.retest_enabled is True
        assert strategy.retest_window_bars == 72
        assert strategy.retest_tolerance_pct == 0.005
        assert strategy.retest_entry_delay_bars == 1
        assert strategy.retest_require_bollinger_confirmation is True
        assert strategy.retest_min_reclaim_pct == 0.0
        assert strategy.retest_min_bollinger_distance_pct == 0.002
