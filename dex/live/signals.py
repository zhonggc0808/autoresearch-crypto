"""Shared live signal pipelines for regime-routed ChannelBreakout checkpoints."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from dex.exit_overlays import apply_exit_overlays
from dex.indicators import compute_adx
from dex.regime_filter import build_daily_regime_labels
from dex.regime_permissions import (
    RiskOffConfig,
    apply_permission_arrays,
    build_permission_arrays,
    compute_daily_indicators,
    route_regime_signals,
)
from dex.strategy_signals import generate_strategy_signals


def generate_live_regime_channel_breakout_signal(
    df: pd.DataFrame,
    bull_s: Any,
    bear_s: Any,
    neutral_s: Any,
    bull_cfg: RiskOffConfig,
    bear_cfg: RiskOffConfig,
    neutral_cfg: RiskOffConfig,
    policy: str,
    regime_fast: int,
    regime_slow: int,
    *,
    exit_logic: dict[str, Any] | None = None,
    exit_overlays_enabled: bool = True,
    adx_gate_threshold: float = 0.0,
    adx_gate_regimes: Sequence[str] = (),
) -> tuple[int, dict[str, Any]]:
    """Generate the latest live signal through the v2.1/v2.2 regime pipeline."""
    n = len(df)
    bull_raw = generate_strategy_signals(bull_s, df, enable_short=bull_s.enable_short)
    bear_raw = generate_strategy_signals(bear_s, df, enable_short=bear_s.enable_short)
    neutral_raw = generate_strategy_signals(neutral_s, df, enable_short=neutral_s.enable_short)

    regimes = build_daily_regime_labels(df, fast_days=regime_fast, slow_days=regime_slow)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)

    routed = route_regime_signals(
        bull_raw,
        bear_raw,
        neutral_raw,
        regimes,
        regime_change_policy=policy,
    )
    allow_long, allow_short, force_flat, exit_only = build_permission_arrays(
        df,
        regimes,
        bull_cfg,
        bear_cfg,
        neutral_cfg,
        daily_ctx,
        adx_full,
    )
    permission_signals = apply_permission_arrays(
        routed,
        allow_long,
        allow_short,
        force_flat,
        exit_only,
    )

    final_signals = permission_signals
    overlays_active = bool(exit_overlays_enabled and exit_logic)
    if overlays_active:
        final_signals = apply_exit_overlays(permission_signals, df, regimes, exit_logic)

    overlay_changed = bool(np.any(final_signals != permission_signals))
    last_overlay_changed = bool(final_signals[-1] != permission_signals[-1])

    adx_gate_regime_set = {str(regime).upper() for regime in adx_gate_regimes}
    adx_gate_active = adx_gate_threshold > 0 and bool(adx_gate_regime_set)
    if adx_gate_active:
        final_signals = final_signals.copy()
        for j in range(n):
            regime = str(regimes[j]).upper()
            if regime in adx_gate_regime_set and adx_full[j] < adx_gate_threshold:
                final_signals[j] = 0

    i = n - 1
    regime = str(regimes[i])
    raw_sig = int(routed[i])
    permission_sig = int(permission_signals[i])
    final_sig = int(final_signals[i])
    adx_gated = (
        adx_gate_active
        and regime.upper() in adx_gate_regime_set
        and adx_full[i] < adx_gate_threshold
    )

    if force_flat[i]:
        reason = "force_flat"
    elif exit_only[i]:
        reason = "exit_only"
    elif adx_gated:
        reason = f"adx_gate (ADX={adx_full[i]:.1f}<{adx_gate_threshold}, {regime})"
    elif last_overlay_changed:
        reason = f"exit_overlay (permission={permission_sig}, final={final_sig}, regime={regime})"
    elif raw_sig == 2 and final_sig != 2:
        reason = f"long_blocked (regime={regime})"
    elif raw_sig == 3 and final_sig != 3:
        reason = f"short_blocked (regime={regime})"
    elif final_sig in (2, 3):
        reason = f"allowed (regime={regime})"
    elif final_sig == 0:
        reason = f"close (regime={regime})"
    else:
        reason = f"hold (regime={regime})"

    return final_sig, {
        "regime": regime,
        "permission_reason": reason,
        "allow_long": bool(allow_long[i]),
        "allow_short": bool(allow_short[i]),
        "force_flat": bool(force_flat[i]) or adx_gated,
        "exit_only": bool(exit_only[i]),
        "raw_signal": raw_sig,
        "permission_signal": permission_sig,
        "routed_signal": final_sig,
        "exit_overlays_enabled": overlays_active,
        "exit_overlay_changed": overlay_changed,
        "exit_overlay_changed_last": last_overlay_changed,
        "adx_gate_active": adx_gate_active,
        "adx_gate_threshold": adx_gate_threshold if adx_gate_active else 0,
    }
