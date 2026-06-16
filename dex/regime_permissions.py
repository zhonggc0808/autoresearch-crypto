"""Fast risk-off overlay on top of slow daily-EMA regime labels.

All indicators are computed from **previous completed** daily candles so there is
no lookahead bias — intraday bars on day D use the daily close of day D-1.

Every bar receives::

    allow_long   — new LONG entries are permitted
    allow_short  — new SHORT entries are permitted
    force_flat   — any open position should be closed immediately
    exit_only    — no new entries; existing positions may be held or closed

The caller is responsible for routing these flags into signal modifications.

Single source of truth
----------------------
``compute_permissions()``, ``build_permission_arrays()``, and
``route_regime_signals()`` are the three public entry points.  All backtest,
tune, and live paths must go through these — **do not** re-implement daily
indicator or permission logic elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

# ═══════════════════════════════════════════════════════════════════════════════
# config
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RiskOffConfig:
    """Parameters for a specific regime's risk-off overlay.

    All daily-close-based checks use the **previous completed** daily candle.

    ADX fields (new — preferred over ``adx_min_for_trade``):

    - ``adx_force_flat_below``: if ADX < this value → force_flat (even close existing).
    - ``adx_entry_min``: if ADX >= force_flat_below but < entry_min → exit_only
      (hold/close existing, no new entries).

    For backward compat, setting ``adx_min_for_trade = X`` is equivalent to
    ``adx_force_flat_below = X``, ``adx_entry_min = X`` (two-tier, no exit_only band).
    """

    # Basic direction permissions (baseline for this regime)
    allow_long: bool = True
    allow_short: bool = False

    # --- risk-off triggers (all based on previous-day daily close) ---
    ema_fast: int = 50          # fast EMA period (days) for close comparison
    ema_slow: int = 100         # slow EMA period, 0 = disabled
    ema_slope_days: int = 5     # lookback for EMA slope computation
    close_below_ema_disables_long: bool = False   # daily close < EMA_fast → no long
    close_below_ema_slow_disables_long: bool = False
    ema_slope_negative_disables_long: bool = False
    consecutive_below_ema_days: int = 0  # 0=disabled, N=require N consecutive days
    max_dd_from_peak_pct: float = 0.0    # 0=disabled, drawdown from 90d high disables long

    # neutral-specific
    directional_only: bool = False  # only allow longs above EMA, shorts below EMA
    short_if_below_ema: bool = False  # allow short ONLY if close < EMA_fast AND slope < 0

    # --- ADX gate (new, preferred) ---
    adx_force_flat_below: float = 0.0   # ADX below this → force_flat
    adx_entry_min: float = 0.0          # ADX below this (but ≥ force_flat_below) → exit_only

    # backward compat — if set, overrides the two fields above at call time
    adx_min_for_trade: float = 0.0      # DEPRECATED: prefer adx_force_flat_below + adx_entry_min

    force_flat: bool = False            # always flat (don't trade this regime at all)


@dataclass(frozen=True)
class BarPermission:
    """Per-bar trade permission."""
    allow_long: bool
    allow_short: bool
    force_flat: bool
    exit_only: bool = False
    reason: str = ""


# ── per-regime candidate presets ─────────────────────────────────────────────

BULL_PRESETS: Dict[str, RiskOffConfig] = {
    "U0_baseline": RiskOffConfig(allow_long=True, allow_short=False),
    "U1_close_below_ema50": RiskOffConfig(
        allow_long=True, allow_short=False,
        close_below_ema_disables_long=True, ema_fast=50,
    ),
    "U2_slope_negative": RiskOffConfig(
        allow_long=True, allow_short=False,
        ema_slope_negative_disables_long=True, ema_fast=50, ema_slope_days=5,
    ),
    "U3_close_below_ema100": RiskOffConfig(
        allow_long=True, allow_short=False,
        close_below_ema_slow_disables_long=True, ema_slow=100,
    ),
    "U4_consecutive3_below_ema50": RiskOffConfig(
        allow_long=True, allow_short=False,
        close_below_ema_disables_long=True, ema_fast=50, consecutive_below_ema_days=3,
    ),
    "U5_dd15pct_from_peak": RiskOffConfig(
        allow_long=True, allow_short=False,
        max_dd_from_peak_pct=15.0,
    ),
    "U6_ema50_adx20": RiskOffConfig(
        allow_long=True, allow_short=False,
        close_below_ema_disables_long=True, ema_fast=50,
        adx_force_flat_below=20, adx_entry_min=20,
    ),
    "U7_cons3_adx20": RiskOffConfig(
        allow_long=True, allow_short=False,
        close_below_ema_disables_long=True, ema_fast=50, consecutive_below_ema_days=3,
        adx_force_flat_below=20, adx_entry_min=20,
    ),
}

BEAR_PRESETS: Dict[str, RiskOffConfig] = {
    "K0_baseline": RiskOffConfig(allow_long=True, allow_short=True),
    "K1_atr25": RiskOffConfig(allow_long=True, allow_short=True),
    "K2_adx18": RiskOffConfig(
        allow_long=True, allow_short=True,
        adx_force_flat_below=18, adx_entry_min=18,
    ),
}

NEUTRAL_PRESETS: Dict[str, RiskOffConfig] = {
    "N0_force_flat": RiskOffConfig(force_flat=True),
    "N1_long_only": RiskOffConfig(allow_long=True, allow_short=False),
    "N2_short_if_bearish": RiskOffConfig(
        allow_long=False, allow_short=False,
        short_if_below_ema=True, ema_fast=50, ema_slope_days=5,
    ),
    "N3_directional": RiskOffConfig(
        allow_long=True, allow_short=True, directional_only=True,
        ema_fast=50, ema_slope_days=5,
    ),
    # N4/N5 are strategy-param variants (4000/576 slow channel), not permission-only
    "N7_adx_gated": RiskOffConfig(
        allow_long=True, allow_short=True, directional_only=True,
        ema_fast=50, ema_slope_days=5,
        adx_force_flat_below=18, adx_entry_min=22,
    ),
}


# ═══════════════════════════════════════════════════════════════════════════════
# daily indicator helpers (single source of truth)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_daily_indicators(df: pd.DataFrame) -> dict:
    """Compute daily-bar indicators once (expensive — call once, reuse).

    Returns a dict with keys: ``close``, ``ema50``, ``ema100``, ``slope``, ``peak90``,
    ``consecutive_below``.

    All series are indexed by daily timestamp (end of day).
    """
    close_vals = df["close"].values.astype(float)
    dts = pd.DatetimeIndex(pd.to_datetime(df["datetime"]))
    daily = pd.Series(close_vals, index=dts).resample("1D").last().dropna()

    if len(daily) < 5:
        return {"close": daily, "ema50": pd.Series(dtype=float), "ema100": pd.Series(dtype=float),
                "slope": pd.Series(dtype=float), "peak90": pd.Series(dtype=float),
                "consecutive_below": pd.Series(dtype=int)}

    ema50 = daily.ewm(span=50, adjust=False, min_periods=50).mean()
    ema100 = daily.ewm(span=100, adjust=False, min_periods=100).mean()

    # EMA50 slope (% change over `slope_lookback` days)
    slope_lookback = 5
    ema50_slope = pd.Series(np.nan, index=daily.index, dtype=float)
    for i in range(slope_lookback, len(ema50)):
        prev = ema50.iloc[i - slope_lookback]
        ema50_slope.iloc[i] = (ema50.iloc[i] / prev - 1.0) if prev > 0 else 0.0

    # 90-day rolling peak for drawdown
    peak90 = daily.rolling(90, min_periods=1).max()

    # consecutive days below EMA50
    consec = pd.Series(0, index=daily.index, dtype=int)
    cnt = 0
    for i in range(len(daily)):
        if i > 0:
            prev_c = float(daily.iloc[i - 1])
            prev_e = float(ema50.iloc[i - 1]) if (i - 1) < len(ema50) else float("nan")
            if not np.isnan(prev_e) and prev_c < prev_e:
                cnt += 1
            else:
                cnt = 0
        consec.iloc[i] = cnt

    return {"close": daily, "ema50": ema50, "ema100": ema100,
            "slope": ema50_slope, "peak90": peak90, "consecutive_below": consec}


def _build_per_day_lookups(daily_ctx: dict) -> dict:
    """Build per-day lookup dicts keyed by day (pd.Timestamp)."""
    days_idx = daily_ctx["close"].index
    day_close: dict = {}
    day_ema50: dict = {}
    day_ema100: dict = {}
    day_slope: dict = {}
    day_peak: dict = {}
    day_consec: dict = {}

    for i, day in enumerate(days_idx):
        prev = max(0, i - 1)
        day_close[day] = float(daily_ctx["close"].iloc[prev])
        day_ema50[day] = float(daily_ctx["ema50"].iloc[prev]) if prev < len(daily_ctx["ema50"]) else float("nan")
        day_ema100[day] = float(daily_ctx["ema100"].iloc[prev]) if prev < len(daily_ctx["ema100"]) else float("nan")
        day_slope[day] = float(daily_ctx["slope"].iloc[prev]) if prev < len(daily_ctx["slope"]) else float("nan")
        day_peak[day] = float(daily_ctx["peak90"].iloc[prev]) if prev < len(daily_ctx["peak90"]) else float("nan")
        # ``consecutive_below`` is already computed as the streak completed
        # through the previous daily candle for this day, so applying ``prev``
        # here would add a second day of lag.
        day_consec[day] = int(daily_ctx["consecutive_below"].iloc[i]) if i < len(daily_ctx["consecutive_below"]) else 0

    return {"close": day_close, "ema50": day_ema50, "ema100": day_ema100,
            "slope": day_slope, "peak": day_peak, "consecutive_below": day_consec}


def _resolve_adx_thresholds(cfg: RiskOffConfig) -> Tuple[float, float]:
    """Return (adx_force_flat_below, adx_entry_min), handling backward compat."""
    ff = cfg.adx_force_flat_below
    entry = cfg.adx_entry_min
    if cfg.adx_min_for_trade > 0:
        if ff == 0.0:
            ff = cfg.adx_min_for_trade
        if entry == 0.0:
            entry = cfg.adx_min_for_trade
    return ff, entry


# ═══════════════════════════════════════════════════════════════════════════════
# public API — per-bar permission computation
# ═══════════════════════════════════════════════════════════════════════════════


def compute_permissions(
    df: pd.DataFrame,
    slow_regimes: np.ndarray,
    bull_config: RiskOffConfig,
    bear_config: RiskOffConfig,
    neutral_config: RiskOffConfig,
    adx: Optional[np.ndarray] = None,
) -> list[BarPermission]:
    """Compute per-bar trade permissions from previous-day daily indicators.

    Parameters
    ----------
    df:
        OHLCV DataFrame with ``close`` and ``datetime`` column or DatetimeIndex.
    slow_regimes:
        Per-bar regime labels ("BULL" / "BEAR" / "NEUTRAL"), length = len(df).
    bull_config / bear_config / neutral_config:
        Risk-off configuration for each regime.
    adx:
        Optional per-bar ADX array (used for ADX-gated rules).

    Returns
    -------
    List of BarPermission, one per bar.
    """
    daily_ctx = compute_daily_indicators(df)
    return _compute_permissions_core(df, slow_regimes, bull_config, bear_config,
                                     neutral_config, daily_ctx, adx)


def build_permission_arrays(
    df: pd.DataFrame,
    regimes: np.ndarray,
    bull_cfg: RiskOffConfig,
    bear_cfg: RiskOffConfig,
    neutral_cfg: RiskOffConfig,
    daily_ctx: dict,
    adx: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute per-bar permissions as numpy int8 arrays — fast path for bulk search.

    Parameters
    ----------
    df, regimes, bull_cfg, bear_cfg, neutral_cfg, adx:
        Same as ``compute_permissions``.
    daily_ctx:
        Pre-computed dict from ``compute_daily_indicators()``.

    Returns
    -------
    (allow_long, allow_short, force_flat, exit_only) — each an int8 array (0/1).
    """
    n = len(df)
    allow_long = np.ones(n, dtype=np.int8)
    allow_short = np.ones(n, dtype=np.int8)
    force_flat = np.zeros(n, dtype=np.int8)
    exit_only = np.zeros(n, dtype=np.int8)

    dts = pd.DatetimeIndex(pd.to_datetime(df["datetime"]))
    bar_days = dts.floor("1D")
    lookups = _build_per_day_lookups(daily_ctx)

    for i in range(n):
        regime = str(regimes[i])
        cfg = {"BULL": bull_cfg, "BEAR": bear_cfg, "NEUTRAL": neutral_cfg}.get(regime)
        if cfg is None:
            allow_long[i] = 0
            allow_short[i] = 0
            force_flat[i] = 1
            continue

        if cfg.force_flat:
            allow_long[i] = 0
            allow_short[i] = 0
            force_flat[i] = 1
            continue

        allow_long[i] = 1 if cfg.allow_long else 0
        allow_short[i] = 1 if cfg.allow_short else 0

        day = pd.Timestamp(bar_days[i])
        prev_close = lookups["close"].get(day, float("nan"))
        prev_ema_f = lookups["ema50"].get(day, float("nan"))
        prev_ema_s = lookups["ema100"].get(day, float("nan"))
        prev_slope = lookups["slope"].get(day, float("nan"))
        prev_peak = lookups["peak"].get(day, float("nan"))
        consec = lookups["consecutive_below"].get(day, 0)

        # --- risk-off: close below EMA ---
        if cfg.close_below_ema_disables_long and not np.isnan(prev_ema_f):
            if cfg.consecutive_below_ema_days > 0:
                if consec >= cfg.consecutive_below_ema_days:
                    allow_long[i] = 0
            elif prev_close < prev_ema_f:
                allow_long[i] = 0

        if cfg.close_below_ema_slow_disables_long and not np.isnan(prev_ema_s):
            if prev_close < prev_ema_s:
                allow_long[i] = 0

        # --- risk-off: EMA slope negative ---
        if cfg.ema_slope_negative_disables_long and not np.isnan(prev_slope):
            if prev_slope < 0:
                allow_long[i] = 0

        # --- risk-off: drawdown from peak ---
        if cfg.max_dd_from_peak_pct > 0 and not np.isnan(prev_peak):
            dd = (prev_peak - prev_close) / prev_peak
            if dd > cfg.max_dd_from_peak_pct / 100:
                allow_long[i] = 0

        # --- directional override (neutral) ---
        if cfg.directional_only and not np.isnan(prev_slope) and not np.isnan(prev_ema_f):
            if prev_close > prev_ema_f and prev_slope > 0:
                allow_long[i] = 1
                allow_short[i] = 0
            elif prev_close < prev_ema_f and prev_slope < 0:
                allow_long[i] = 0
                allow_short[i] = 1
            else:
                allow_long[i] = 0
                allow_short[i] = 0

        # --- short-if-bearish ---
        if cfg.short_if_below_ema and not cfg.directional_only:
            if not np.isnan(prev_ema_f) and not np.isnan(prev_slope):
                if prev_close < prev_ema_f and prev_slope < 0:
                    allow_short[i] = 1
                else:
                    allow_short[i] = 0

        # --- ADX gate (three-tier) ---
        adx_ff_below, adx_entry_min = _resolve_adx_thresholds(cfg)
        if (adx_ff_below > 0 or adx_entry_min > 0) and adx is not None:
            bar_adx = float(adx[i]) if i < len(adx) else 0.0

            if adx_ff_below > 0 and bar_adx < adx_ff_below:
                # Tier 1: force_flat — close everything
                allow_long[i] = 0
                allow_short[i] = 0
                force_flat[i] = 1
            elif adx_entry_min > 0 and bar_adx < adx_entry_min:
                # Tier 2: exit_only — no new entries, but existing can hold/close
                allow_long[i] = 0
                allow_short[i] = 0
                exit_only[i] = 1

    return allow_long, allow_short, force_flat, exit_only


def _compute_permissions_core(
    df: pd.DataFrame,
    slow_regimes: np.ndarray,
    bull_config: RiskOffConfig,
    bear_config: RiskOffConfig,
    neutral_config: RiskOffConfig,
    daily_ctx: dict,
    adx: Optional[np.ndarray] = None,
) -> list[BarPermission]:
    """Core permission computation — used by both APIs."""
    al, as_, ff, eo = build_permission_arrays(
        df, slow_regimes, bull_config, bear_config, neutral_config, daily_ctx, adx,
    )

    # Build reason strings (lightweight pass)
    n = len(df)
    result: list[BarPermission] = []
    for i in range(n):
        reason = ""
        if ff[i]:
            reason = "force_flat"
        elif eo[i]:
            reason = "exit_only"
        elif not al[i] and not as_[i]:
            reason = "no_direction_allowed"
        elif not al[i]:
            reason = "long_blocked"
        elif not as_[i]:
            reason = "short_blocked"
        else:
            reason = "ok"
        result.append(BarPermission(
            allow_long=bool(al[i]), allow_short=bool(as_[i]),
            force_flat=bool(ff[i]), exit_only=bool(eo[i]), reason=reason,
        ))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# public API — signal routing & permission application
# ═══════════════════════════════════════════════════════════════════════════════


def route_regime_signals(
    bull_signals: np.ndarray,
    bear_signals: np.ndarray,
    neutral_signals: np.ndarray,
    regimes: np.ndarray,
    regime_change_policy: str = "permission_based",
) -> np.ndarray:
    """Route per-regime raw signals into a single signal stream.

    Parameters
    ----------
    bull_signals / bear_signals / neutral_signals:
        Raw signal arrays (one per regime), each length N.  Values: 0=close, 1=hold, 2=long, 3=short.
    regimes:
        Per-bar regime labels ("BULL" / "BEAR" / "NEUTRAL"), length N.
    regime_change_policy:
        - ``"always_close"``: output close(0) on the first bar after a regime change.
          Introduces a 1-bar gap between regime segments.
        - ``"permission_based"``: route normally; the permission layer decides whether
          the current position is still allowed under the new regime.  (default)
        - ``"never_close"``: route normally; never force close just because regime
          changed.  The permission layer still applies its normal rules.

    Returns
    -------
    Routed signal array (int), same length as inputs.
    """
    n = len(regimes)
    routed = np.ones(n, dtype=int)
    regimes_arr = np.asarray(regimes, dtype=object)
    prev_regime = "NEUTRAL"

    for i in range(n):
        r = str(regimes_arr[i])

        if regime_change_policy == "always_close":
            if r != prev_regime and i > 0:
                routed[i] = 0
                prev_regime = r
                continue

        prev_regime = r
        sig = {"BULL": bull_signals, "BEAR": bear_signals}.get(r, neutral_signals)[i]
        routed[i] = int(sig)

    return routed


def apply_permissions_to_signals(
    signals: np.ndarray,
    permissions: list[BarPermission],
) -> np.ndarray:
    """Apply permission mask to raw strategy signals (non-position-aware).

    - If force_flat: output close(0) regardless of raw signal.
    - If raw=LONG(2) but allow_long=False: output hold(1) if flat, close(0) handled by
      position-aware version.
    - If raw=SHORT(3) but allow_short=False: same logic.

    Prefer :func:`apply_permissions_with_position` for accurate position tracking.
    """
    out = np.asarray(signals, dtype=int).copy()
    for i in range(len(out)):
        perm = permissions[i]
        raw = out[i]
        if perm.force_flat:
            out[i] = 0
        elif raw == 2 and not perm.allow_long:
            out[i] = 1
        elif raw == 3 and not perm.allow_short:
            out[i] = 1
    return out


def apply_permissions_with_position(
    signals: np.ndarray,
    permissions: list[BarPermission],
) -> np.ndarray:
    """Apply permission mask **with position awareness**.

    - force_flat: always output close(0), position → flat.
    - exit_only: if flat, block new entries; if in position, allow hold or close
      but not reversal.
    - HOLD with position that's no longer allowed: force close(0).
    - ENTRY into disallowed direction: skip (hold if flat, close if opposite position).
    """
    out = np.asarray(signals, dtype=int).copy()
    position = 0  # 0=flat, 1=long, -1=short

    for i in range(len(out)):
        perm = permissions[i]
        raw = out[i]

        if perm.force_flat:
            out[i] = 0
            position = 0
            continue

        # exit_only: no new entries, but existing positions can hold or close
        if perm.exit_only:
            if position == 0:
                # flat — can't enter anything
                if raw in (2, 3):
                    out[i] = 1  # hold (stay flat)
                # raw == 0 or 1: already fine
            else:
                # in position — allow hold(1), allow close(0), block reversal
                if raw == 0:
                    out[i] = 0
                    position = 0
                elif (position == 1 and raw == 3) or (position == -1 and raw == 2):
                    # reversal attempt → close instead
                    out[i] = 0
                    position = 0
                elif raw == 1:
                    pass  # hold, position unchanged
                elif (position == 1 and raw == 2) or (position == -1 and raw == 3):
                    pass  # same direction, allowed
            continue

        # Check if current position is still allowed
        if position == 1 and not perm.allow_long:
            out[i] = 0
            position = 0
            continue
        if position == -1 and not perm.allow_short:
            out[i] = 0
            position = 0
            continue

        # Resolve target position from raw signal
        if raw == 2:
            if perm.allow_long:
                position = 1
            else:
                out[i] = 1  # hold, can't enter long
        elif raw == 3:
            if perm.allow_short:
                position = -1
            else:
                out[i] = 1
        elif raw == 0:
            position = 0
        # raw == 1: hold, position unchanged

    return out


def apply_permission_arrays(
    signals: np.ndarray,
    allow_long: np.ndarray,
    allow_short: np.ndarray,
    force_flat: np.ndarray,
    exit_only: np.ndarray,
) -> np.ndarray:
    """Fast position-aware permission application using precomputed arrays.

    Equivalent to ``apply_permissions_with_position`` but uses int8 arrays
    instead of BarPermission objects — suitable for hot loops in tune scripts.
    """
    out = np.asarray(signals, dtype=int).copy()
    al = np.asarray(allow_long, dtype=np.int8)
    as_ = np.asarray(allow_short, dtype=np.int8)
    ff = np.asarray(force_flat, dtype=np.int8)
    eo = np.asarray(exit_only, dtype=np.int8)
    pos = 0

    for i in range(len(out)):
        if ff[i]:
            out[i] = 0
            pos = 0
            continue

        if eo[i]:
            if pos == 0:
                if out[i] in (2, 3):
                    out[i] = 1
            else:
                raw = out[i]
                if raw == 0:
                    out[i] = 0
                    pos = 0
                elif (pos == 1 and raw == 3) or (pos == -1 and raw == 2):
                    out[i] = 0
                    pos = 0
                elif raw in (1, 2, 3):
                    pass  # hold or same direction
            continue

        if pos == 1 and not al[i]:
            out[i] = 0
            pos = 0
            continue
        if pos == -1 and not as_[i]:
            out[i] = 0
            pos = 0
            continue

        raw = out[i]
        if raw == 2:
            pos = 1 if al[i] else pos
            if not al[i]:
                out[i] = 1
        elif raw == 3:
            pos = -1 if as_[i] else pos
            if not as_[i]:
                out[i] = 1
        elif raw == 0:
            pos = 0

    return out


# ═══════════════════════════════════════════════════════════════════════════════
# internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _default_permission(
    slow_regimes: np.ndarray,
    bull_cfg: RiskOffConfig,
    bear_cfg: RiskOffConfig,
    neutral_cfg: RiskOffConfig,
    i: int,
) -> BarPermission:
    regime = str(slow_regimes[i])
    cfg = {"BULL": bull_cfg, "BEAR": bear_cfg, "NEUTRAL": neutral_cfg}.get(regime)
    if cfg is None:
        return BarPermission(False, False, True, False, "unknown")
    if cfg.force_flat:
        return BarPermission(False, False, True, False, "force_flat")
    return BarPermission(cfg.allow_long, cfg.allow_short, False, False, "default")
