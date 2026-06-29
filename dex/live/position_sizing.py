from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

EXP0140_VOL_REF = 0.794812
VOL_TARGET_20D_WINDOW_BARS = 20 * 288
VOL_TARGET_BARS_PER_YEAR = 365.25 * 288
VOL_TARGET_CLIP_MIN = 0.40
VOL_TARGET_CLIP_MAX = 1.00


@dataclass(frozen=True)
class VolTargetSizingDecision:
    multiplier: float
    realized_vol: float | None
    raw_multiplier: float | None
    reason: str
    fallback: bool
    bars_available: int
    required_bars: int
    vol_ref: float = EXP0140_VOL_REF
    clip_min: float = VOL_TARGET_CLIP_MIN
    clip_max: float = VOL_TARGET_CLIP_MAX
    window_bars: int = VOL_TARGET_20D_WINDOW_BARS

    def state_updates(self) -> dict[str, Any]:
        return {
            "vol_target_multiplier": self.multiplier,
            "vol_target_realized_vol_20d": self.realized_vol,
            "vol_target_raw_multiplier": self.raw_multiplier,
            "vol_target_reason": self.reason,
            "vol_target_fallback": self.fallback,
            "vol_target_bars_available": self.bars_available,
            "vol_target_required_bars": self.required_bars,
            "vol_target_vol_ref": self.vol_ref,
            "vol_target_clip_min": self.clip_min,
            "vol_target_clip_max": self.clip_max,
            "vol_target_window_bars": self.window_bars,
        }


@dataclass(frozen=True)
class EntryFillability:
    raw_size: float
    rounded_size: float
    notional: float
    min_lot_notional: float
    min_order_notional: float
    fillable: bool


def shifted_realized_vol_20d(
    df: pd.DataFrame,
    *,
    window_bars: int = VOL_TARGET_20D_WINDOW_BARS,
    bars_per_year: float = VOL_TARGET_BARS_PER_YEAR,
) -> pd.Series:
    """Match exp0139/0140 realized vol: pct-change rolling std shifted one bar."""
    if "close" not in df.columns:
        return pd.Series(dtype="float64")
    close = pd.to_numeric(df["close"], errors="coerce")
    returns = close.pct_change()
    return returns.rolling(window_bars, min_periods=window_bars).std().shift(1) * (
        bars_per_year**0.5
    )


def compute_vol_target_sizing(
    df: pd.DataFrame,
    *,
    vol_ref: float = EXP0140_VOL_REF,
    clip_min: float = VOL_TARGET_CLIP_MIN,
    clip_max: float = VOL_TARGET_CLIP_MAX,
    window_bars: int = VOL_TARGET_20D_WINDOW_BARS,
) -> VolTargetSizingDecision:
    """Return the exp0140 entry-fixed realized-vol sizing multiplier."""
    bars_available = 0 if df is None else len(df)
    required_bars = window_bars + 2
    if df is None or df.empty or "close" not in df.columns:
        return VolTargetSizingDecision(
            multiplier=1.0,
            realized_vol=None,
            raw_multiplier=None,
            reason="missing_close_data",
            fallback=True,
            bars_available=bars_available,
            required_bars=required_bars,
            vol_ref=vol_ref,
            clip_min=clip_min,
            clip_max=clip_max,
            window_bars=window_bars,
        )
    if bars_available < required_bars:
        return VolTargetSizingDecision(
            multiplier=1.0,
            realized_vol=None,
            raw_multiplier=None,
            reason="insufficient_history",
            fallback=True,
            bars_available=bars_available,
            required_bars=required_bars,
            vol_ref=vol_ref,
            clip_min=clip_min,
            clip_max=clip_max,
            window_bars=window_bars,
        )

    vol = shifted_realized_vol_20d(df, window_bars=window_bars).iloc[-1]
    realized_vol = float(vol) if pd.notna(vol) else None
    if realized_vol is None or realized_vol <= 0 or vol_ref <= 0:
        return VolTargetSizingDecision(
            multiplier=1.0,
            realized_vol=realized_vol,
            raw_multiplier=None,
            reason="invalid_realized_vol",
            fallback=True,
            bars_available=bars_available,
            required_bars=required_bars,
            vol_ref=vol_ref,
            clip_min=clip_min,
            clip_max=clip_max,
            window_bars=window_bars,
        )

    raw_multiplier = vol_ref / realized_vol
    multiplier = min(clip_max, max(clip_min, raw_multiplier))
    return VolTargetSizingDecision(
        multiplier=float(multiplier),
        realized_vol=realized_vol,
        raw_multiplier=float(raw_multiplier),
        reason="ok",
        fallback=False,
        bars_available=bars_available,
        required_bars=required_bars,
        vol_ref=vol_ref,
        clip_min=clip_min,
        clip_max=clip_max,
        window_bars=window_bars,
    )


def estimate_entry_fillability(
    *,
    notional: float,
    current_price: float,
    lot_size: float,
    min_order_notional: float,
) -> EntryFillability:
    if notional <= 0 or current_price <= 0 or lot_size <= 0:
        return EntryFillability(
            raw_size=0.0,
            rounded_size=0.0,
            notional=0.0,
            min_lot_notional=lot_size * current_price if lot_size > 0 and current_price > 0 else 0.0,
            min_order_notional=min_order_notional,
            fillable=False,
        )
    raw_size = notional / current_price
    rounded_size = int(raw_size / lot_size) * lot_size
    rounded_notional = rounded_size * current_price
    return EntryFillability(
        raw_size=float(raw_size),
        rounded_size=float(rounded_size),
        notional=float(rounded_notional),
        min_lot_notional=float(lot_size * current_price),
        min_order_notional=float(min_order_notional),
        fillable=rounded_size > 0 and rounded_notional >= min_order_notional,
    )
