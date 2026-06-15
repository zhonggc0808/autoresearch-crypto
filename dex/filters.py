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
    diag: Optional[Dict[str, Any]] = None,
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

    counters = {
        "high_vol_bars": 0,
        "attempted_long_entries": 0,
        "attempted_short_entries": 0,
        "blocked_long_entries": 0,
        "blocked_short_entries": 0,
        "signals_changed_total": 0,
    }

    for i in range(len(out)):
        raw = int(out[i])
        sig = raw

        # Determine if volatility gate should block entry
        if action in ("block_entries_when_high_vol",
                      "block_long_entries_when_high_vol",
                      "block_short_entries_when_high_vol"):
            blocked = atr_ratio[i] > threshold
        elif action == "block_entries_when_low_vol":
            blocked = atr_ratio[i] < threshold
        elif action == "reduce_position_size":
            blocked = atr_ratio[i] > threshold  # placeholder
        else:
            blocked = False

        if blocked:
            counters["high_vol_bars"] += 1
            if sig == 2 and position != 1:
                counters["attempted_long_entries"] += 1
            elif sig == 3 and position != -1:
                counters["attempted_short_entries"] += 1

            if action == "block_short_entries_when_high_vol":
                if sig == 3 and position != -1:
                    sig = 1
                    counters["blocked_short_entries"] += 1
            elif action == "block_long_entries_when_high_vol":
                if sig == 2 and position != 1:
                    sig = 1
                    counters["blocked_long_entries"] += 1
            else:
                if sig == 2 and position != 1:
                    sig = 1
                    counters["blocked_long_entries"] += 1
                elif sig == 3 and position != -1:
                    sig = 1
                    counters["blocked_short_entries"] += 1

        if sig != raw:
            counters["signals_changed_total"] += 1

        out[i] = sig

        if sig == 2:
            position = 1
        elif sig == 3:
            position = -1
        elif sig == 0:
            position = 0

    if diag is not None:
        diag.update(counters)

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


def compute_close_drawdown(
    close: np.ndarray,
    lookback: int = 2016,
) -> np.ndarray:
    """Compute rolling drawdown from close price peak.

    Returns an array of drawdown ratios [0, 1) per bar,
    where higher = deeper drawdown.

    Args:
        close: Array of close prices.
        lookback: Rolling window for the peak (bars).

    Returns:
        Drawdown ratio array, same length as close.
    """
    n = len(close)
    peak = np.zeros(n)
    dd = np.zeros(n)

    running_max = close[0]
    for i in range(n):
        if close[i] > running_max:
            running_max = close[i]
        # Rolling window: reset peak lookback
        if i >= lookback:
            rolling_max = np.max(close[i - lookback + 1 : i + 1])
            running_max = max(running_max, rolling_max)
        peak[i] = running_max
        dd[i] = 1.0 - close[i] / running_max

    return dd


def apply_cooldown_after_drawdown(
    signals: np.ndarray,
    drawdown: np.ndarray,
    threshold: float = 0.15,
    cooldown_bars: int = 288,
) -> np.ndarray:
    """Block new entries after a drawdown breach, with cooldown.

    When drawdown exceeds threshold, a cooldown window starts.
    During cooldown, new entries are blocked. Existing positions
    are untouched.

    Args:
        signals: Raw strategy signals.
        drawdown: Drawdown ratio array.
        threshold: DD ratio to trigger cooldown.
        cooldown_bars: Cooldown duration.

    Returns:
        Modified signals with entries blocked during cooldown.
    """
    if threshold <= 0:
        return signals

    out = signals.copy()
    position = 0
    cooldown_remaining = 0

    for i in range(len(out)):
        raw = int(out[i])
        sig = raw

        # Enter cooldown if DD breaches threshold
        if drawdown[i] >= threshold:
            cooldown_remaining = cooldown_bars

        # During cooldown, block new entries only
        if cooldown_remaining > 0:
            if sig == 2 and position != 1:
                sig = 1
            elif sig == 3 and position != -1:
                sig = 1
            cooldown_remaining -= 1

        out[i] = sig

        if sig == 2:
            position = 1
        elif sig == 3:
            position = -1
        elif sig == 0:
            position = 0

    return out


def apply_neutral_regime_block(
    signals: np.ndarray,
    regimes: np.ndarray,
    action: str = "block_entries_when_neutral",
    diag: Optional[Dict[str, Any]] = None,
) -> np.ndarray:
    """Block new entries during NEUTRAL regime.

    Entry-only — existing positions are NOT closed. This targets
    low-directional-conviction bars where fees tend to erode returns.

    Args:
        signals: Raw strategy signals.
        regimes: Regime labels per bar (BULL/BEAR/NEUTRAL, uppercase).
        action: What to block during NEUTRAL:
            - block_entries_when_neutral: block all new entries
            - block_short_entries_when_neutral: only block new shorts
            - block_long_entries_when_neutral: only block new longs
        diag: Optional diagnostics dict.

    Returns:
        Modified signals with entries blocked during NEUTRAL regime.
    """
    out = signals.copy()
    position = 0

    counters = {
        "neutral_bars": 0,
        "blocked_long_entries": 0,
        "blocked_short_entries": 0,
        "signals_changed_total": 0,
    }

    for i in range(len(out)):
        raw = int(out[i])
        sig = raw
        is_neutral = i < len(regimes) and str(regimes[i]).upper() == "NEUTRAL"

        if is_neutral:
            counters["neutral_bars"] += 1

            if action == "block_short_entries_when_neutral":
                if sig == 3 and position != -1:
                    sig = 1
                    counters["blocked_short_entries"] += 1
            elif action == "block_long_entries_when_neutral":
                if sig == 2 and position != 1:
                    sig = 1
                    counters["blocked_long_entries"] += 1
            else:  # block_entries_when_neutral
                if sig == 2 and position != 1:
                    sig = 1
                    counters["blocked_long_entries"] += 1
                elif sig == 3 and position != -1:
                    sig = 1
                    counters["blocked_short_entries"] += 1

        if sig != raw:
            counters["signals_changed_total"] += 1

        out[i] = sig

        if sig == 2:
            position = 1
        elif sig == 3:
            position = -1
        elif sig == 0:
            position = 0

    if diag is not None:
        diag.update(counters)

    return out


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
            # Capture diagnostics in a mutable dict so callers can inspect
            filter_diag: Dict[str, Any] = {}
            def _vg_fn(s: np.ndarray) -> np.ndarray:
                return apply_volatility_gate(
                    s, atr_ratio, threshold=threshold, action=action,
                    diag=filter_diag,
                )
            _vg_fn.diag = filter_diag  # type: ignore[attr-defined]
            return _vg_fn
        else:
            # No OHLC data available — passthrough
            return lambda s: s

    if filter_type == "cooldown_after_drawdown":
        threshold = config.get("threshold", 0.15)
        lookback = config.get("lookback", 2016)
        cooldown_bars = config.get("cooldown_bars", 288)
        action = config.get("action", "block_entries_during_cooldown")
        metric = config.get("metric", "close_drawdown")

        if df is not None and metric == "close_drawdown":
            close = df["close"].values.astype(float)
            dd = compute_close_drawdown(close, lookback=lookback)
            return lambda s: apply_cooldown_after_drawdown(
                s, dd, threshold=threshold, cooldown_bars=cooldown_bars,
            )
        else:
            return lambda s: s

    raise ValueError(
        f"Unknown filter type: {filter_type!r}. "
    if filter_type == "neutral_regime_entry_block":
        action = config.get("action", "block_entries_when_neutral")
        filter_diag: Dict[str, Any] = {}
        def _nr_fn(s: np.ndarray) -> np.ndarray:
            return apply_neutral_regime_block(
                s, regimes, action=action, diag=filter_diag,
            )
        _nr_fn.diag = filter_diag  # type: ignore[attr-defined]
        return _nr_fn

    raise ValueError(
        f"Unknown filter type: {filter_type!r}. "
        f"Supported types: adx_gate, volatility_gate, cooldown_after_drawdown, neutral_regime_entry_block"
    )
    )
