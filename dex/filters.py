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
                  None = apply to all regimes. Case-insensitive.
        regimes: Daily regime labels per bar (BULL/BEAR/NEUTRAL, uppercase).

    Returns:
        Modified signals with low-ADX entries blocked.
    """
    if threshold <= 0:
        return signals  # disabled

    # Normalize apply_to to uppercase for case-insensitive comparison
    normalized_apply_to = None
    if apply_to is not None:
        normalized_apply_to = [r.upper() for r in apply_to]

    out = signals.copy()
    position = 0

    for i in range(len(out)):
        raw = int(out[i])
        sig = raw

        # Determine if this bar should be filtered
        should_filter = True
        if regimes is not None and normalized_apply_to is not None:
            regime = str(regimes[i]).upper() if i < len(regimes) else "NEUTRAL"
            should_filter = regime in normalized_apply_to

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


def apply_volatility_gate(
    signals: np.ndarray,
    atr_ratio: np.ndarray,
    threshold: float = 0.06,
    action: str = "block_entries_when_high_vol",
) -> np.ndarray:
    """Block new position entries when volatility metric exceeds threshold.

    Entry-only filter — existing positions are NOT closed. This prevents
    the gate from causing forced exits that may be worse than staying in.

    Args:
        signals: Raw strategy signals (0=flat, 1=hold, 2=long, 3=short).
        atr_ratio: ATR/close ratio values, same length as signals.
        threshold: Volatility threshold to trigger the gate.
        action: Gate behavior:
            - "block_entries_when_high_vol": block new entries when ratio > threshold
            - "block_entries_when_low_vol": block new entries when ratio < threshold
            - "reduce_position_size": currently maps to block_entries (placeholder)

    Returns:
        Modified signals with entries blocked during high volatility.
    """
    if threshold <= 0:
        return signals

    out = signals.copy()
    position = 0

    for i in range(len(out)):
        raw = int(out[i])
        sig = raw

        # Determine if volatility gate should block entry
        if action == "block_entries_when_high_vol":
            blocked = atr_ratio[i] > threshold
        elif action == "block_entries_when_low_vol":
            blocked = atr_ratio[i] < threshold
        elif action == "reduce_position_size":
            blocked = atr_ratio[i] > threshold  # placeholder
        else:
            blocked = False

        if blocked:
            if sig == 2 and position != 1:  # new long
                sig = 1
            elif sig == 3 and position != -1:  # new short
                sig = 1

        out[i] = sig

        if sig == 2:
            position = 1
        elif sig == 3:
            position = -1
        elif sig == 0:
            position = 0

    return out


def compute_atr_close_ratio(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    lookback: int = 48,
) -> np.ndarray:
    """Compute ATR/close ratio for volatility gate.

    Uses a simple max-of-range true-range per bar, then exponential
    smoothing to match lookback semantics.
    """
    n = len(close)
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1)),
        ),
    )
    tr[0] = high[0] - low[0]  # first bar

    alpha = 2.0 / (lookback + 1)
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]

    return atr / close


def build_filter_from_config(
    config: Dict[str, Any],
    adx: np.ndarray,
    regimes: np.ndarray,
    df: Optional[Any] = None,
) -> callable:
    """Build a filter function from a candidate JSON filter config.

    Args:
        config: The ``filter`` block from a candidate JSON spec.
        adx: Pre-computed ADX array (full data length).
        regimes: Pre-computed regime labels (full data length).
        df: Optional OHLC DataFrame — required for volatility_gate
            to compute ATR/close. Not needed for adx_gate.

    Returns:
        A callable ``filter_fn(signals: np.ndarray) -> np.ndarray``.

    Raises:
        ValueError: If filter type is unknown (typo guard).
    """
    if not config:
        return lambda s: s

    # Accept both "type" (old format) and "family" (new format)
    filter_type = config.get("type") or config.get("family", "")

    if filter_type == "adx_gate":
        threshold = config.get("adx_threshold", 20)
        apply_to = config.get("apply_to")
        return lambda s: apply_adx_filter(
            s, adx, threshold=threshold, apply_to=apply_to, regimes=regimes,
        )

    if filter_type == "volatility_gate":
        threshold = config.get("threshold", 0.06)
        action = config.get("action", "block_entries_when_high_vol")
        lookback = config.get("lookback", 48)
        metric = config.get("metric", "atr_close_ratio")

        if df is not None and metric == "atr_close_ratio":
            atr_ratio = compute_atr_close_ratio(
                df["high"].values.astype(float),
                df["low"].values.astype(float),
                df["close"].values.astype(float),
                lookback=lookback,
            )
            return lambda s: apply_volatility_gate(
                s, atr_ratio, threshold=threshold, action=action,
            )
        else:
            # No OHLC data available — passthrough
            return lambda s: s

    raise ValueError(
        f"Unknown filter type: {filter_type!r}. "
        f"Supported types: adx_gate, volatility_gate"
    )
