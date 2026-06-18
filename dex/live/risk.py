"""Exchange-agnostic live risk decisions for versioned strategy profiles."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LiveRiskDecision:
    action: str = "none"
    reason: str = ""
    size_fraction: float = 0.0
    entry_size_multiplier: float = 1.0
    allow_new_entry: bool = True
    current_drawdown: float = 0.0
    state_updates: dict[str, Any] = field(default_factory=dict)


def evaluate_live_risk(
    risk_config: dict[str, Any],
    state: dict[str, Any],
    *,
    position: int,
    entry_price: float,
    current_close: float,
    current_equity: float,
) -> LiveRiskDecision:
    """Return one risk action for a closed candle.

    ``risk_profile=none`` is a no-op for v2/v2.1/v2.2 compatibility. The caller
    persists ``state_updates`` after applying any exchange-specific order action.
    """
    if risk_config.get("risk_profile", "none") == "none":
        return LiveRiskDecision()

    peak_equity = max(float(state.get("risk_peak_equity", current_equity)), current_equity)
    drawdown = _equity_drawdown(peak_equity, current_equity)
    updates: dict[str, Any] = {
        "risk_profile": risk_config.get("risk_profile"),
        "risk_peak_equity": peak_equity,
        "risk_current_drawdown": drawdown,
    }

    entry_size = _entry_size_multiplier(risk_config, drawdown)
    no_new_entry, kill_switch = _dd_blocks(risk_config, drawdown)

    if position == 0:
        updates["risk_adverse_stop_fired"] = False
        return LiveRiskDecision(
            entry_size_multiplier=entry_size,
            allow_new_entry=not no_new_entry and not kill_switch,
            current_drawdown=drawdown,
            state_updates=updates,
        )

    if kill_switch:
        return LiveRiskDecision(
            action="close",
            reason="kill_switch",
            size_fraction=1.0,
            entry_size_multiplier=entry_size,
            allow_new_entry=False,
            current_drawdown=drawdown,
            state_updates=updates,
        )

    adverse = risk_config.get("adverse_stop", {})
    entry_bar_immune = state.get("bar_count") == state.get("confirmed_entry_bar")
    if (
        adverse.get("enabled", False)
        and not state.get("risk_adverse_stop_fired", False)
        and not entry_bar_immune
    ):
        move = compute_adverse_move(position, entry_price, current_close)
        threshold = float(adverse.get("threshold_pct", -1.0))
        if move <= threshold:
            action = str(adverse.get("action", "reduce_half"))
            updates["risk_adverse_stop_fired"] = True
            return LiveRiskDecision(
                action="close" if action == "close" else "reduce",
                reason="adverse_stop",
                size_fraction=1.0 if action == "close" else _action_fraction(action),
                entry_size_multiplier=entry_size,
                allow_new_entry=False,
                current_drawdown=drawdown,
                state_updates=updates,
            )

    return LiveRiskDecision(
        entry_size_multiplier=entry_size,
        allow_new_entry=not no_new_entry,
        current_drawdown=drawdown,
        state_updates=updates,
    )


def compute_adverse_move(position: int, entry_price: float, current_close: float) -> float:
    if position not in (-1, 1) or entry_price <= 0 or current_close <= 0:
        return 0.0
    if position == 1:
        return current_close / entry_price - 1.0
    return 1.0 - current_close / entry_price


def _entry_size_multiplier(risk_config: dict[str, Any], drawdown: float) -> float:
    base_size = float(risk_config.get("base_size", 1.0))
    dd_config = risk_config.get("equity_dd_sizing", {})
    if not dd_config.get("enabled", False):
        return base_size
    multiplier = 1.0
    for threshold, tier_multiplier in sorted(dd_config.get("tiers", [])):
        multiplier = float(tier_multiplier)
        if drawdown < float(threshold):
            break
    return base_size * multiplier


def _dd_blocks(risk_config: dict[str, Any], drawdown: float) -> tuple[bool, bool]:
    dd_config = risk_config.get("equity_dd_sizing", {})
    if not dd_config.get("enabled", False):
        return False, False
    no_new_entry = drawdown >= float(dd_config.get("no_new_entry_dd", math.inf))
    kill_switch = drawdown >= float(dd_config.get("kill_switch_dd", math.inf))
    return no_new_entry, kill_switch


def _equity_drawdown(peak_equity: float, current_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity)


def _action_fraction(action: str) -> float:
    if action == "reduce_quarter":
        return 0.25
    return 0.5
