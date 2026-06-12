"""Historical, local-data-only regime filters for backtests."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RegimeFilterStats:
    """Summary of how the historical regime short filter changed signals."""

    total_bars: int
    bullish_bars: int
    bearish_bars: int
    neutral_bars: int
    blocked_short_signals: int
    closed_short_positions: int
    first_bull_time: str | None
    last_bull_time: str | None
    first_bear_time: str | None
    last_bear_time: str | None


BullRegimeFilterStats = RegimeFilterStats


def build_daily_regime_labels(
    df: pd.DataFrame,
    fast_days: int = 50,
    slow_days: int = 200,
) -> np.ndarray:
    """Return per-row BULL/BEAR/NEUTRAL labels from completed daily EMA state."""
    daily_close, datetimes = _daily_close_with_datetimes(df, fast_days, slow_days)
    if daily_close.empty:
        return np.full(len(df), "NEUTRAL", dtype=object)

    ema_fast = daily_close.ewm(
        span=fast_days, adjust=False, min_periods=fast_days
    ).mean()
    ema_slow = daily_close.ewm(
        span=slow_days, adjust=False, min_periods=slow_days
    ).mean()
    raw_regime = pd.Series("NEUTRAL", index=daily_close.index, dtype=object)
    raw_regime[(daily_close > ema_slow) & (ema_fast > ema_slow)] = "BULL"
    raw_regime[(daily_close < ema_slow) & (ema_fast < ema_slow)] = "BEAR"

    confirmed_regime = raw_regime.shift(1, fill_value="NEUTRAL")
    bar_days = pd.DatetimeIndex(datetimes).floor("1D")
    return confirmed_regime.reindex(bar_days, fill_value="NEUTRAL").to_numpy(dtype=object)


def build_daily_bull_regime_mask(
    df: pd.DataFrame,
    fast_days: int = 50,
    slow_days: int = 200,
) -> np.ndarray:
    """Return a per-row bull-regime mask using completed daily EMA state.

    The mask is computed from local OHLCV data only. Intraday bars on day D
    receive the regime from day D-1, so the filter never reads the current
    day's unfinished close.
    """
    regimes = build_daily_regime_labels(df, fast_days=fast_days, slow_days=slow_days)
    return regimes == "BULL"


def apply_bull_regime_short_filter(
    signals: np.ndarray,
    bull_mask: np.ndarray,
    df: pd.DataFrame | None = None,
) -> tuple[np.ndarray, RegimeFilterStats]:
    """Block short exposure during bull-regime bars.

    In bull regime:
    - raw SHORT signals become CLOSE signals;
    - an existing filtered short position is closed even if the raw signal is HOLD;
    - LONG and CLOSE signals keep their normal meaning.
    """
    filtered = np.asarray(signals, dtype=int).copy()
    bull = np.asarray(bull_mask, dtype=bool)
    if len(filtered) != len(bull):
        raise ValueError("signals and bull_mask must have the same length")

    blocked_short_signals = 0
    closed_short_positions = 0
    position = 0

    for i, signal in enumerate(filtered):
        output = int(signal)
        if bull[i]:
            if output == 3:
                output = 0
                blocked_short_signals += 1
            elif output == 1 and position == -1:
                output = 0

            if output == 0 and position == -1:
                closed_short_positions += 1

        filtered[i] = output
        if output == 2:
            position = 1
        elif output == 3:
            position = -1
        elif output == 0:
            position = 0

    first_bull_time, last_bull_time = _mask_time_range(df, bull)
    stats = RegimeFilterStats(
        total_bars=len(filtered),
        bullish_bars=int(bull.sum()),
        bearish_bars=0,
        neutral_bars=len(filtered) - int(bull.sum()),
        blocked_short_signals=blocked_short_signals,
        closed_short_positions=closed_short_positions,
        first_bull_time=first_bull_time,
        last_bull_time=last_bull_time,
        first_bear_time=None,
        last_bear_time=None,
    )
    return filtered, stats


def apply_regime_short_filter(
    signals: np.ndarray,
    regimes: np.ndarray,
    df: pd.DataFrame | None = None,
) -> tuple[np.ndarray, RegimeFilterStats]:
    """Allow short exposure only in BEAR regime, while keeping longs available."""
    filtered = np.asarray(signals, dtype=int).copy()
    labels = np.asarray(regimes, dtype=object)
    if len(filtered) != len(labels):
        raise ValueError("signals and regimes must have the same length")

    blocked_short_signals = 0
    closed_short_positions = 0
    position = 0

    for i, signal in enumerate(filtered):
        output = int(signal)
        short_allowed = labels[i] == "BEAR"
        if not short_allowed:
            if output == 3:
                output = 0
                blocked_short_signals += 1
            elif output == 1 and position == -1:
                output = 0

            if output == 0 and position == -1:
                closed_short_positions += 1

        filtered[i] = output
        if output == 2:
            position = 1
        elif output == 3:
            position = -1
        elif output == 0:
            position = 0

    bull_mask = labels == "BULL"
    bear_mask = labels == "BEAR"
    first_bull_time, last_bull_time = _mask_time_range(df, bull_mask)
    first_bear_time, last_bear_time = _mask_time_range(df, bear_mask)
    stats = RegimeFilterStats(
        total_bars=len(filtered),
        bullish_bars=int(bull_mask.sum()),
        bearish_bars=int(bear_mask.sum()),
        neutral_bars=int((labels == "NEUTRAL").sum()),
        blocked_short_signals=blocked_short_signals,
        closed_short_positions=closed_short_positions,
        first_bull_time=first_bull_time,
        last_bull_time=last_bull_time,
        first_bear_time=first_bear_time,
        last_bear_time=last_bear_time,
    )
    return filtered, stats


def _daily_close_with_datetimes(
    df: pd.DataFrame,
    fast_days: int,
    slow_days: int,
) -> tuple[pd.Series, pd.DatetimeIndex]:
    if fast_days <= 0 or slow_days <= 0:
        raise ValueError("fast_days and slow_days must be positive")
    if "close" not in df.columns:
        raise ValueError("df must contain a close column")
    if len(df) == 0:
        return pd.Series(dtype=float), pd.DatetimeIndex([])

    datetimes = _extract_datetimes(df)
    daily_close = (
        pd.DataFrame({"close": df["close"].astype(float).to_numpy()}, index=datetimes)
        .sort_index()
        .resample("1D")["close"]
        .last()
        .dropna()
    )
    return daily_close, datetimes


def _extract_datetimes(df: pd.DataFrame) -> pd.DatetimeIndex:
    if "datetime" in df.columns:
        raw = df["datetime"]
    elif "timestamp" in df.columns:
        raw = df["timestamp"]
    elif isinstance(df.index, pd.DatetimeIndex):
        return df.index
    else:
        raise ValueError("df must contain datetime/timestamp column or DatetimeIndex")

    if pd.api.types.is_numeric_dtype(raw):
        max_abs = float(np.nanmax(np.abs(raw.to_numpy(dtype=float)))) if len(raw) else 0.0
        if max_abs > 10_000_000_000:
            return pd.DatetimeIndex(pd.to_datetime(raw, unit="ms", errors="coerce"))
        if max_abs > 10_000_000:
            return pd.DatetimeIndex(pd.to_datetime(raw, unit="s", errors="coerce"))

    return pd.DatetimeIndex(pd.to_datetime(raw, errors="coerce"))


def _mask_time_range(
    df: pd.DataFrame | None,
    mask: np.ndarray,
) -> tuple[str | None, str | None]:
    if df is None or not mask.any():
        return None, None

    datetimes = _extract_datetimes(df)
    matched_times = datetimes[mask]
    if len(matched_times) == 0:
        return None, None
    return str(matched_times[0]), str(matched_times[-1])
