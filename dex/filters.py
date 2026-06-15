"""
Candidate-selectable signal filters for research_oracle.py.

Filters are applied AFTER strategy signal generation, BEFORE evaluation.
They are ONLY used in --candidate mode, never in --baseline mode.
This guarantees --baseline output is bit-identical regardless of filter changes.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any

import numpy as np


def apply_adx_filter(
    signals: np.ndarray,
    adx: np.ndarray,
    threshold: float = 20.0,
    apply_to: Optional[List[str]] = None,
    regimes: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Block new position entries when ADX is below threshold.

    Only blocks NEW entries (signal=2 or 3 when currently flat or flipping).
    Existing positions are NOT closed — the filter is entry-only, not an exit signal.

    Args:
        signals: Raw strategy signals (0=flat, 1=hold, 2=long, 3=short).
        adx: ADX values, same length as signals.
        threshold: Minimum ADX to allow entry (0=disabled).
        apply_to: List of regimes to enforce filter on (e.g. ["neutral"]).
                  None = apply to all regimes.
        regimes: Daily regime labels per bar (BULL/BEAR/NEUTRAL).

    Returns:
        Modified signals with low-ADX entries blocked.
    """
    if threshold <= 0:
        return signals  # disabled

    out = signals.copy()
    position = 0

    for i in range(len(out)):
        raw = int(out[i])
        sig = raw

        # Determine if this bar should be filtered
        should_filter = True
        if regimes is not None and apply_to is not None:
            regime = str(regimes[i]) if i < len(regimes) else "NEUTRAL"
            should_filter = regime in apply_to

        # Block new entries when ADX is low
        if should_filter and adx[i] < threshold:
            if sig == 2 and position != 1:  # new long entry
                sig = 1  # hold instead
            elif sig == 3 and position != -1:  # new short entry
                sig = 1  # hold instead

        out[i] = sig

        # Track position
        if sig == 2:
            position = 1
        elif sig == 3:
            position = -1
        elif sig == 0:
            position = 0

    return out


def build_filter_from_config(
    config: Dict[str, Any],
    adx: np.ndarray,
    regimes: np.ndarray,
) -> callable:
    """Build a filter function from a candidate JSON filter config.

    Args:
        config: The ``filter`` block from a candidate JSON spec.
        adx: Pre-computed ADX array (full data length).
        regimes: Pre-computed regime labels (full data length).

    Returns:
        A callable ``filter_fn(signals: np.ndarray) -> np.ndarray``.
    """
    if not config:
        return lambda s: s

    filter_type = config.get("type", "")
    threshold = config.get("adx_threshold", 20)
    apply_to = config.get("apply_to")

    if filter_type == "adx_gate":
        return lambda s: apply_adx_filter(
            s, adx, threshold=threshold, apply_to=apply_to, regimes=regimes,
        )

    # Unknown filter type → pass through
    return lambda s: s
