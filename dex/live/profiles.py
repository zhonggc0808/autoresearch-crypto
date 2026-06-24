"""Shared live strategy profiles.

Profiles name the signal checkpoint and optional live risk profile separately.
Old profiles keep ``risk_profile=none`` so live entrypoints stay backward compatible.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

COMBO_BALANCED_RISK_CONFIG: dict[str, Any] = {
    "risk_profile": "combo_balanced",
    "base_size": 0.425,
    "adverse_stop": {
        "enabled": True,
        "threshold_pct": -0.05,
        "action": "reduce_half",
    },
    "equity_dd_sizing": {
        "enabled": True,
        "tiers": [(0.05, 0.95), (0.10, 0.85), (0.15, 0.65), (0.20, 0.40)],
        "no_new_entry_dd": 0.20,
        "kill_switch_dd": 0.25,
    },
}


@dataclass(frozen=True)
class LiveStrategyProfile:
    name: str
    checkpoint: str
    strategy_version: str
    risk_profile: str = "none"
    risk_config: dict[str, Any] | None = None


LIVE_STRATEGY_PROFILES: dict[str, LiveStrategyProfile] = {
    "channel_breakout_v2": LiveStrategyProfile(
        name="channel_breakout_v2",
        checkpoint="checkpoints/channel_breakout_375_432.pt",
        strategy_version="2.0",
    ),
    "channel_breakout_v2_regime_filter_50_200": LiveStrategyProfile(
        name="channel_breakout_v2_regime_filter_50_200",
        checkpoint="checkpoints/channel_breakout_v2_regime_filter_50_200.pt",
        strategy_version="2.0-regime-filter",
    ),
    "channel_breakout_v2_1_balanced": LiveStrategyProfile(
        name="channel_breakout_v2_1_balanced",
        checkpoint="checkpoints/channel_breakout_v2_1_balanced.pt",
        strategy_version="2.1",
    ),
    "channel_breakout_v2_2_mtg_bcd": LiveStrategyProfile(
        name="channel_breakout_v2_2_mtg_bcd",
        checkpoint="checkpoints/channel_breakout_v2_2_mtg_bcd.json",
        strategy_version="2.2",
    ),
    "channel_breakout_v2_2_m375_bbm375_1p5": LiveStrategyProfile(
        name="channel_breakout_v2_2_m375_bbm375_1p5",
        checkpoint="checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json",
        strategy_version="2.2-bb375-1.5",
    ),
    "channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen": LiveStrategyProfile(
        name="channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen",
        checkpoint=(
            "checkpoints/"
            "channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen.json"
        ),
        strategy_version="2.2-bb375-1.5-retest-w96-tol50bp-nextopen",
    ),
    "channel_breakout_v2_2_m375_bbm375_1p5_retest_w72_tol50bp_bbdist20_nextopen": LiveStrategyProfile(
        name="channel_breakout_v2_2_m375_bbm375_1p5_retest_w72_tol50bp_bbdist20_nextopen",
        checkpoint=(
            "checkpoints/"
            "channel_breakout_v2_2_m375_bbm375_1p5_retest_w72_tol50bp_bbdist20_nextopen.json"
        ),
        strategy_version="2.2-bb375-1.5-retest-w72-tol50bp-bbdist20-nextopen",
    ),
    "channel_breakout_v2_3_combo_balanced": LiveStrategyProfile(
        name="channel_breakout_v2_3_combo_balanced",
        checkpoint="checkpoints/channel_breakout_375_432.pt",
        strategy_version="2.3",
        risk_profile="combo_balanced",
        risk_config=COMBO_BALANCED_RISK_CONFIG,
    ),
    "channel_breakout": LiveStrategyProfile(
        name="channel_breakout",
        checkpoint="checkpoints/eth_optimal.pt",
        strategy_version="legacy",
    ),
    "hybrid_mm": LiveStrategyProfile(
        name="hybrid_mm",
        checkpoint="checkpoints/quant_model.pt",
        strategy_version="legacy",
    ),
}


def profile_checkpoint_map() -> dict[str, str]:
    return {name: profile.checkpoint for name, profile in LIVE_STRATEGY_PROFILES.items()}


def get_live_strategy_profile(name: str) -> LiveStrategyProfile:
    try:
        return LIVE_STRATEGY_PROFILES[name]
    except KeyError as exc:
        known = ", ".join(sorted(LIVE_STRATEGY_PROFILES))
        raise ValueError(f"Unknown strategy profile {name!r}; expected one of: {known}") from exc


def resolve_checkpoint_path(checkpoint_path: str | None, strategy_profile: str) -> str:
    if checkpoint_path:
        return checkpoint_path
    return get_live_strategy_profile(strategy_profile).checkpoint


def risk_config_for_profile(strategy_profile: str) -> dict[str, Any]:
    profile = get_live_strategy_profile(strategy_profile)
    if profile.risk_profile == "none":
        return {"risk_profile": "none"}
    return copy.deepcopy(profile.risk_config or {"risk_profile": profile.risk_profile})
