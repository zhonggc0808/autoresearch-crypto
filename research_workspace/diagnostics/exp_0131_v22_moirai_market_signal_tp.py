from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXP0110_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0110_v22_moirai_reversal_confirmation_delay.py"
spec = importlib.util.spec_from_file_location("exp0110_helper", EXP0110_PATH)
exp0110 = importlib.util.module_from_spec(spec)
sys.modules["exp0110_helper"] = exp0110
assert spec.loader is not None
spec.loader.exec_module(exp0110)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp"
OI_PATH = PROJECT_ROOT / "data/market_intel/ETHUSDT_1h_bybit_oi_2600d.parquet"
LIVE_CACHE = PROJECT_ROOT / "data/live_cache/bitget_ETH_USDT_USDT_5m.parquet"

SHADOW_RATIOS = (0.60, 0.75, 0.85)
VOLUME_RATIOS = (1.5, 2.0)
OI_THRESHOLDS = (0.0, -0.005)
MACD_TFS = ("2h", "4h")

LIVE_ENTRY_TIME = "2026-06-23 04:00:00"
LIVE_EXIT_TIME = "2026-06-27 14:30:00"
LIVE_ENTRY_PRICE = 1729.17
LIVE_SIDE = -1


@dataclass(frozen=True)
class VariantSpec:
    variant: str
    use_2h_wick: bool = False
    wick_shadow_ratio: float = 0.75
    wick_volume_ratio: float = 1.5
    engulf_style: str = ""
    engulf_volume_ratio: float = 1.5
    oi_threshold: float | None = None
    macd_tf: str = ""
    daily_context: bool = False
    sequence_macd_tf: str = ""
    sequence_window_bars: int = 0


def pct(x: float | None) -> str:
    return "" if x is None else f"{x * 100:.2f}%"


def bar_time(df: pd.DataFrame, i: int) -> str:
    return str(pd.Timestamp(df["datetime"].iloc[int(i)]))


def target_position(signal: int, current_position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return current_position


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns:
        if "timestamp" not in out.columns:
            raise ValueError("OHLCV data must contain datetime or timestamp")
        out["datetime"] = parse_times(out["timestamp"])
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates(subset="datetime")
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            raise ValueError(f"OHLCV data must contain {col}")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    return out.reset_index(drop=True)


def resample_ohlcv(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    source = clean_ohlcv(df)
    bars = (
        source.set_index("datetime")
        .resample(freq, label="right", closed="right")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
    )
    bars["available_at"] = bars.index
    return bars.reset_index(drop=True)


def add_candle_features(bars: pd.DataFrame, volume_lookback: int = 24) -> pd.DataFrame:
    out = bars.copy()
    rng = (out["high"] - out["low"]).replace(0.0, np.nan)
    body_high = out[["open", "close"]].max(axis=1)
    body_low = out[["open", "close"]].min(axis=1)
    out["lower_shadow_ratio"] = ((body_low - out["low"]) / rng).replace([np.inf, -np.inf], np.nan)
    out["upper_shadow_ratio"] = ((out["high"] - body_high) / rng).replace([np.inf, -np.inf], np.nan)
    out["close_pos"] = ((out["close"] - out["low"]) / rng).replace([np.inf, -np.inf], np.nan)
    out["body_ratio"] = ((out["close"] - out["open"]).abs() / rng).replace([np.inf, -np.inf], np.nan)
    volume_base = out["volume"].rolling(volume_lookback, min_periods=6).median().shift(1)
    out["volume_ratio"] = (out["volume"] / volume_base).replace([np.inf, -np.inf], np.nan)

    prev_open = out["open"].shift(1)
    prev_close = out["close"].shift(1)
    prev_mid = (prev_open + prev_close) / 2.0
    out["bullish_engulf_strict"] = (
        (prev_close < prev_open)
        & (out["close"] > out["open"])
        & (out["open"] <= prev_close)
        & (out["close"] >= prev_open)
    )
    out["bearish_engulf_strict"] = (
        (prev_close > prev_open)
        & (out["close"] < out["open"])
        & (out["open"] >= prev_close)
        & (out["close"] <= prev_open)
    )
    out["bullish_engulf_loose"] = (
        (prev_close < prev_open)
        & (out["close"] > out["open"])
        & (out["close"] >= prev_mid)
        & (out["close_pos"] >= 0.55)
    )
    out["bearish_engulf_loose"] = (
        (prev_close > prev_open)
        & (out["close"] < out["open"])
        & (out["close"] <= prev_mid)
        & (out["close_pos"] <= 0.45)
    )
    return out


def add_macd_features(bars: pd.DataFrame) -> pd.DataFrame:
    out = bars.copy()
    close = out["close"].astype(float)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    out["macd"] = macd
    out["macd_hist"] = hist
    out["macd_bull_cross"] = (hist > 0.0) & (hist.shift(1) <= 0.0)
    out["macd_bear_cross"] = (hist < 0.0) & (hist.shift(1) >= 0.0)
    return out


def daily_context_features(df: pd.DataFrame, tolerance: float = 0.01) -> pd.DataFrame:
    source = clean_ohlcv(df)
    daily = (
        source.assign(date=source["datetime"].dt.floor("D"))
        .groupby("date", as_index=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .sort_index()
    )
    daily = add_macd_features(daily.assign(available_at=daily.index + pd.Timedelta(days=1)))

    prior_low = daily["low"].shift(10).rolling(36, min_periods=10).min()
    prior_high = daily["high"].shift(10).rolling(36, min_periods=10).max()
    prior_macd_min = daily["macd"].shift(10).rolling(36, min_periods=10).min()
    prior_macd_max = daily["macd"].shift(10).rolling(36, min_periods=10).max()
    prior_hist_min = daily["macd_hist"].shift(10).rolling(36, min_periods=10).min()
    prior_hist_max = daily["macd_hist"].shift(10).rolling(36, min_periods=10).max()

    low_match = ((daily["low"] - prior_low).abs() / prior_low.replace(0.0, np.nan)) <= tolerance
    high_match = ((daily["high"] - prior_high).abs() / prior_high.replace(0.0, np.nan)) <= tolerance
    daily["daily_bull_div_context"] = low_match & (daily["macd"] > prior_macd_min) & (daily["macd_hist"] > prior_hist_min)
    daily["daily_bear_div_context"] = high_match & (daily["macd"] < prior_macd_max) & (daily["macd_hist"] < prior_hist_max)
    return daily.reset_index(drop=True)[["available_at", "daily_bull_div_context", "daily_bear_div_context"]]


def build_oi_feature_frame(oi: pd.DataFrame | None) -> pd.DataFrame:
    if oi is None or oi.empty:
        return pd.DataFrame(columns=["available_at", "oi_change_2h", "oi_change_4h"])
    out = oi.copy()
    time_col = "available_at" if "available_at" in out.columns else "datetime"
    if time_col not in out.columns and "timestamp" in out.columns:
        time_col = "timestamp"
    out["available_at"] = parse_times(out[time_col])

    value_col = ""
    for candidate in ("combined_open_interest", "bybit_open_interest", "open_interest"):
        if candidate in out.columns:
            value_col = candidate
            break
    if not value_col:
        exchange_cols = [c for c in out.columns if c.endswith("_open_interest")]
        if not exchange_cols:
            return pd.DataFrame(columns=["available_at", "oi_change_2h", "oi_change_4h"])
        out["combined_open_interest"] = out[exchange_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        value_col = "combined_open_interest"

    out[value_col] = pd.to_numeric(out[value_col], errors="coerce")
    out = out.dropna(subset=["available_at"]).sort_values("available_at").drop_duplicates(subset="available_at")
    oi_value = out[value_col].replace(0.0, np.nan)
    out["oi_change_2h"] = oi_value.pct_change(2, fill_method=None).replace([np.inf, -np.inf], np.nan).shift(1)
    out["oi_change_4h"] = oi_value.pct_change(4, fill_method=None).replace([np.inf, -np.inf], np.nan).shift(1)
    return out[["available_at", "oi_change_2h", "oi_change_4h"]]


def parse_times(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        values = series.to_numpy(dtype=float)
        max_abs = float(np.nanmax(np.abs(values))) if len(values) else 0.0
        if max_abs > 10_000_000_000:
            return pd.to_datetime(series, unit="ms", errors="coerce")
        if max_abs > 10_000_000:
            return pd.to_datetime(series, unit="s", errors="coerce")
    return pd.to_datetime(series, errors="coerce")


def rename_feature_columns(features: pd.DataFrame, suffix: str, columns: list[str]) -> pd.DataFrame:
    keep = features[["available_at", *columns]].copy()
    return keep.rename(columns={col: f"{col}_{suffix}" for col in columns})


def align_feature_frames(df: pd.DataFrame, frames: list[pd.DataFrame]) -> pd.DataFrame:
    bars = clean_ohlcv(df)
    aligned = pd.DataFrame({"datetime": bars["datetime"]})
    decisions = pd.DataFrame({"_order": np.arange(len(bars)), "decision_at": bars["datetime"]})
    for frame in frames:
        if frame.empty:
            continue
        clean = frame.copy()
        clean["available_at"] = pd.to_datetime(clean["available_at"], errors="coerce")
        clean = clean.dropna(subset=["available_at"]).sort_values("available_at")
        matched = pd.merge_asof(
            decisions.sort_values("decision_at"),
            clean,
            left_on="decision_at",
            right_on="available_at",
            direction="backward",
        ).sort_values("_order")
        for col in clean.columns:
            if col != "available_at":
                aligned[col] = matched[col].to_numpy()
    bool_cols = [c for c in aligned.columns if c.endswith("_cross") or c.endswith("_context") or "engulf" in c]
    for col in bool_cols:
        aligned[col] = aligned[col].astype("boolean").fillna(False).to_numpy(dtype=bool)
    return aligned


def build_market_signal_features(df: pd.DataFrame, oi: pd.DataFrame | None = None) -> pd.DataFrame:
    candle_cols = [
        "lower_shadow_ratio",
        "upper_shadow_ratio",
        "close_pos",
        "volume_ratio",
        "bullish_engulf_strict",
        "bearish_engulf_strict",
        "bullish_engulf_loose",
        "bearish_engulf_loose",
    ]
    one_h = rename_feature_columns(add_candle_features(resample_ohlcv(df, "1h")), "1h", candle_cols)
    two_h = add_macd_features(add_candle_features(resample_ohlcv(df, "2h")))
    two_h = rename_feature_columns(two_h, "2h", [*candle_cols, "macd_bull_cross", "macd_bear_cross", "macd_hist"])
    four_h = add_macd_features(add_candle_features(resample_ohlcv(df, "4h")))
    four_h = rename_feature_columns(four_h, "4h", ["macd_bull_cross", "macd_bear_cross", "macd_hist"])
    daily = daily_context_features(df)
    oi_features = build_oi_feature_frame(oi)
    features = align_feature_frames(df, [one_h, two_h, four_h, daily, oi_features])
    expected = [
        "lower_shadow_ratio_2h",
        "upper_shadow_ratio_2h",
        "volume_ratio_2h",
        "volume_ratio_1h",
        "oi_change_2h",
        "oi_change_4h",
        "macd_hist_2h",
        "macd_hist_4h",
    ]
    for col in expected:
        if col not in features.columns:
            features[col] = np.nan
    return features


def load_local_oi() -> pd.DataFrame:
    if not OI_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(OI_PATH)


def as_float_array(features: pd.DataFrame, column: str) -> np.ndarray:
    if column not in features.columns:
        return np.full(len(features), np.nan)
    return pd.to_numeric(features[column], errors="coerce").to_numpy(dtype=float)


def as_bool_array(features: pd.DataFrame, column: str) -> np.ndarray:
    if column not in features.columns:
        return np.zeros(len(features), dtype=bool)
    return features[column].astype("boolean").fillna(False).to_numpy(dtype=bool)


def rising_edges(mask: np.ndarray) -> np.ndarray:
    out = np.asarray(mask, dtype=bool).copy()
    if len(out) > 1:
        out[1:] &= ~out[:-1]
    return out


def event_bool_array(features: pd.DataFrame, column: str) -> np.ndarray:
    return rising_edges(as_bool_array(features, column))


def shape_masks(features: pd.DataFrame, spec: VariantSpec) -> tuple[np.ndarray, np.ndarray]:
    n = len(features)
    short_shape = np.zeros(n, dtype=bool)
    long_shape = np.zeros(n, dtype=bool)
    if spec.use_2h_wick:
        lower = as_float_array(features, "lower_shadow_ratio_2h")
        upper = as_float_array(features, "upper_shadow_ratio_2h")
        close_pos = as_float_array(features, "close_pos_2h")
        vol = as_float_array(features, "volume_ratio_2h")
        short_shape |= rising_edges(
            (lower >= spec.wick_shadow_ratio) & (close_pos >= 0.55) & (vol >= spec.wick_volume_ratio)
        )
        long_shape |= rising_edges(
            (upper >= spec.wick_shadow_ratio) & (close_pos <= 0.45) & (vol >= spec.wick_volume_ratio)
        )
    if spec.engulf_style:
        vol = as_float_array(features, "volume_ratio_1h")
        short_shape |= rising_edges(
            as_bool_array(features, f"bullish_engulf_{spec.engulf_style}_1h") & (vol >= spec.engulf_volume_ratio)
        )
        long_shape |= rising_edges(
            as_bool_array(features, f"bearish_engulf_{spec.engulf_style}_1h") & (vol >= spec.engulf_volume_ratio)
        )
    return short_shape, long_shape


def trigger_masks(features: pd.DataFrame, spec: VariantSpec) -> tuple[np.ndarray, np.ndarray]:
    short_shape, long_shape = shape_masks(features, spec)
    short_mask = short_shape.copy()
    long_mask = long_shape.copy()
    if spec.oi_threshold is not None:
        oi2 = as_float_array(features, "oi_change_2h")
        oi4 = as_float_array(features, "oi_change_4h")
        oi_flush = (oi2 <= spec.oi_threshold) | (oi4 <= spec.oi_threshold)
        short_mask &= oi_flush
        long_mask &= oi_flush
    if spec.macd_tf:
        short_mask &= event_bool_array(features, f"macd_bull_cross_{spec.macd_tf}")
        long_mask &= event_bool_array(features, f"macd_bear_cross_{spec.macd_tf}")
    if spec.daily_context:
        short_mask &= as_bool_array(features, "daily_bull_div_context")
        long_mask &= as_bool_array(features, "daily_bear_div_context")
    return short_mask, long_mask


def sequence_confirm_masks(features: pd.DataFrame, spec: VariantSpec) -> tuple[np.ndarray, np.ndarray]:
    if not spec.sequence_macd_tf:
        return np.zeros(len(features), dtype=bool), np.zeros(len(features), dtype=bool)
    return (
        event_bool_array(features, f"macd_bull_cross_{spec.sequence_macd_tf}"),
        event_bool_array(features, f"macd_bear_cross_{spec.sequence_macd_tf}"),
    )


def trigger_reasons(row: pd.Series, position: int, spec: VariantSpec) -> list[str]:
    reasons: list[str] = []
    if spec.use_2h_wick:
        if position < 0:
            if (
                finite_ge(row.get("lower_shadow_ratio_2h"), spec.wick_shadow_ratio)
                and finite_ge(row.get("close_pos_2h"), 0.55)
                and finite_ge(row.get("volume_ratio_2h"), spec.wick_volume_ratio)
            ):
                reasons.append("2h_lower_wick")
        elif (
            finite_ge(row.get("upper_shadow_ratio_2h"), spec.wick_shadow_ratio)
            and finite_le(row.get("close_pos_2h"), 0.45)
            and finite_ge(row.get("volume_ratio_2h"), spec.wick_volume_ratio)
        ):
            reasons.append("2h_upper_wick")
    if spec.engulf_style:
        prefix = "bullish" if position < 0 else "bearish"
        if bool(row.get(f"{prefix}_engulf_{spec.engulf_style}_1h", False)) and finite_ge(
            row.get("volume_ratio_1h"), spec.engulf_volume_ratio
        ):
            reasons.append(f"1h_{prefix}_{spec.engulf_style}_engulf")
    if spec.oi_threshold is not None and (
        finite_le(row.get("oi_change_2h"), spec.oi_threshold)
        or finite_le(row.get("oi_change_4h"), spec.oi_threshold)
    ):
        reasons.append("oi_flush")
    if spec.macd_tf:
        cross = "bull" if position < 0 else "bear"
        if bool(row.get(f"macd_{cross}_cross_{spec.macd_tf}", False)):
            reasons.append(f"macd_{cross}_cross_{spec.macd_tf}")
    if spec.daily_context:
        context = "daily_bull_div_context" if position < 0 else "daily_bear_div_context"
        if bool(row.get(context, False)):
            reasons.append(context)
    return reasons


def finite_ge(value: Any, threshold: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return np.isfinite(v) and v >= threshold


def finite_le(value: Any, threshold: float) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return np.isfinite(v) and v <= threshold


def build_variant_matrix() -> list[VariantSpec]:
    variants: list[VariantSpec] = []
    for shadow_ratio in SHADOW_RATIOS:
        for volume_ratio in VOLUME_RATIOS:
            base_name = f"2h_wick_r{int(shadow_ratio * 100)}_v{str(volume_ratio).replace('.', 'p')}"
            variants.append(
                VariantSpec(
                    variant=base_name,
                    use_2h_wick=True,
                    wick_shadow_ratio=shadow_ratio,
                    wick_volume_ratio=volume_ratio,
                )
            )
            for oi_threshold in OI_THRESHOLDS:
                variants.append(
                    VariantSpec(
                        variant=f"{base_name}_oi{threshold_label(oi_threshold)}",
                        use_2h_wick=True,
                        wick_shadow_ratio=shadow_ratio,
                        wick_volume_ratio=volume_ratio,
                        oi_threshold=oi_threshold,
                    )
                )
            for macd_tf in MACD_TFS:
                variants.append(
                    VariantSpec(
                        variant=f"{base_name}_macd{macd_tf}",
                        use_2h_wick=True,
                        wick_shadow_ratio=shadow_ratio,
                        wick_volume_ratio=volume_ratio,
                        macd_tf=macd_tf,
                    )
                )
                variants.append(
                    VariantSpec(
                        variant=f"{base_name}_oiN05_macd{macd_tf}_daily",
                        use_2h_wick=True,
                        wick_shadow_ratio=shadow_ratio,
                        wick_volume_ratio=volume_ratio,
                        oi_threshold=-0.005,
                        macd_tf=macd_tf,
                        daily_context=True,
                    )
                )
            if shadow_ratio >= 0.75:
                for macd_tf in MACD_TFS:
                    for window in (144, 288):
                        variants.append(
                            VariantSpec(
                                variant=f"{base_name}_then_macd{macd_tf}_{int(window / 12)}h",
                                use_2h_wick=True,
                                wick_shadow_ratio=shadow_ratio,
                                wick_volume_ratio=volume_ratio,
                                sequence_macd_tf=macd_tf,
                                sequence_window_bars=window,
                            )
                        )

    for style in ("strict", "loose"):
        for volume_ratio in VOLUME_RATIOS:
            base_name = f"1h_{style}_engulf_v{str(volume_ratio).replace('.', 'p')}"
            variants.append(VariantSpec(variant=base_name, engulf_style=style, engulf_volume_ratio=volume_ratio))
            for oi_threshold in OI_THRESHOLDS:
                variants.append(
                    VariantSpec(
                        variant=f"{base_name}_oi{threshold_label(oi_threshold)}",
                        engulf_style=style,
                        engulf_volume_ratio=volume_ratio,
                        oi_threshold=oi_threshold,
                    )
                )
            for macd_tf in MACD_TFS:
                variants.append(
                    VariantSpec(
                        variant=f"{base_name}_macd{macd_tf}",
                        engulf_style=style,
                        engulf_volume_ratio=volume_ratio,
                        macd_tf=macd_tf,
                    )
                )
                variants.append(
                    VariantSpec(
                        variant=f"{base_name}_oiN05_macd{macd_tf}_daily",
                        engulf_style=style,
                        engulf_volume_ratio=volume_ratio,
                        oi_threshold=-0.005,
                        macd_tf=macd_tf,
                        daily_context=True,
                    )
                )
    return variants


def threshold_label(value: float) -> str:
    if value == 0:
        return "0"
    return f"N{abs(value) * 100:.1f}".replace(".", "p")


def trade_at_bar(trades: list[dict[str, Any]], bar: int) -> dict[str, Any] | None:
    for trade in trades:
        if int(trade["entry_step"]) <= bar <= int(trade["step"]):
            return trade
    return None


def unique_trade_rows(exits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in exits:
        key = item.get("base_trade_entry_bar")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def capture_ratio(signals: np.ndarray, df: pd.DataFrame) -> dict[str, float]:
    _, trades = exp0110.helper0108.next_open_trades(signals, df)
    rows = exp0110.helper0108.trade_features(trades, signals, df)
    vals = [r["realized_return"] / r["life_mfe"] for r in rows if r["life_mfe"] >= 0.06 and r["life_mfe"] > 0]
    giveback_losses = [r for r in rows if r["life_mfe"] >= 0.06 and r["pnl"] <= 0]
    return {
        "mfe_capture_avg": float(np.nanmean(vals)) if vals else 0.0,
        "giveback_to_loss_6_count": len(giveback_losses),
        "giveback_to_loss_6_pnl": float(sum(r["pnl"] for r in giveback_losses)),
        "avg_bars_held": float(np.mean([r["bars_held"] for r in rows])) if rows else 0.0,
    }


def yearly_delta(base_signals: np.ndarray, test_signals: np.ndarray, df: pd.DataFrame) -> dict[str, Any]:
    years = pd.to_datetime(df["datetime"]).dt.year.to_numpy()
    deltas = []
    for year in sorted(set(int(y) for y in years)):
        idx = np.flatnonzero(years == year)
        if len(idx) < 100:
            continue
        start, end = int(idx[0]), int(idx[-1]) + 1
        dfi = df.iloc[start:end].reset_index(drop=True)
        base = exp0110.helper0108.helper.evaluate_next_open(base_signals[start:end], dfi, None)
        test = exp0110.helper0108.helper.evaluate_next_open(test_signals[start:end], dfi, None)
        deltas.append(test["return"] - base["return"])
    return {
        "year_wins": sum(1 for x in deltas if x > 1e-12),
        "year_losses": sum(1 for x in deltas if x < -1e-12),
        "year_flat": sum(1 for x in deltas if abs(x) <= 1e-12),
        "year_min_delta": min(deltas) if deltas else 0.0,
    }


def apply_market_signal_tp(
    signals: np.ndarray,
    df: pd.DataFrame,
    features: pd.DataFrame,
    spec: VariantSpec,
    base_trades: list[dict[str, Any]],
    top20_cutoff: float,
    worst20_entries: set[int],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    out = signals.astype(int).copy()
    short_mask, long_mask = trigger_masks(features, spec)
    short_setup, long_setup = shape_masks(features, spec)
    short_confirm, long_confirm = sequence_confirm_masks(features, spec)
    open_ = df["open"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    pos = 0
    entry_bar = -1
    entry_price = 0.0
    lockout = 0
    setup_side = 0
    setup_bar = -1
    setup_until = -1
    exits: list[dict[str, Any]] = []

    for i in range(len(out)):
        if setup_side and i > setup_until:
            setup_side = 0
            setup_bar = -1
            setup_until = -1
        prev_signal = int(out[i - 1]) if i else 1
        target = target_position(prev_signal, pos)
        if target != pos:
            if target == 0:
                pos = 0
                entry_bar = -1
                entry_price = 0.0
                setup_side = 0
                setup_bar = -1
                setup_until = -1
            else:
                pos = target
                entry_bar = i
                entry_price = float(open_[i]) * (
                    1 + exp0110.helper0108.SLIPPAGE if pos > 0 else 1 - exp0110.helper0108.SLIPPAGE
                )
                setup_side = 0
                setup_bar = -1
                setup_until = -1

        decision = int(signals[i])
        raw_target = target_position(decision, pos)
        if lockout and raw_target != lockout:
            lockout = 0
        if lockout and pos == 0 and raw_target == lockout:
            out[i] = 1
            continue

        current_return = 0.0
        if pos > 0 and entry_price > 0:
            current_return = close[i] / entry_price - 1.0
        elif pos < 0 and entry_price > 0:
            current_return = entry_price / max(close[i], 1e-12) - 1.0

        if (
            spec.sequence_macd_tf
            and pos != 0
            and raw_target == pos
            and current_return > 0.0
            and ((pos < 0 and short_setup[i]) or (pos > 0 and long_setup[i]))
        ):
            setup_side = pos
            setup_bar = i
            setup_until = i + int(spec.sequence_window_bars)

        forced_exit = False
        sequence_reasons: list[str] = []
        if spec.sequence_macd_tf:
            confirm = (pos < 0 and short_confirm[i]) or (pos > 0 and long_confirm[i])
            forced_exit = (
                pos != 0
                and raw_target == pos
                and current_return > 0.0
                and setup_side == pos
                and i <= setup_until
                and confirm
            )
            if forced_exit:
                setup_label = "2h_lower_wick_setup" if pos < 0 else "2h_upper_wick_setup"
                cross_label = "macd_bull_cross" if pos < 0 else "macd_bear_cross"
                sequence_reasons = [f"{setup_label}@{bar_time(df, setup_bar)}", f"{cross_label}_{spec.sequence_macd_tf}"]
        else:
            forced_exit = (
                pos != 0
                and raw_target == pos
                and current_return > 0.0
                and ((pos < 0 and short_mask[i]) or (pos > 0 and long_mask[i]))
            )
        if forced_exit:
            reasons = sequence_reasons if sequence_reasons else trigger_reasons(features.iloc[i], pos, spec)
            base_trade = trade_at_bar(base_trades, i)
            base_pnl = float(base_trade["pnl"]) if base_trade else 0.0
            base_entry = int(base_trade["entry_step"]) if base_trade else -1
            exits.append(
                {
                    "variant": spec.variant,
                    "bar": i,
                    "time": bar_time(df, i),
                    "side": "long" if pos > 0 else "short",
                    "entry_bar": entry_bar,
                    "bars_held": i - entry_bar if entry_bar >= 0 else 0,
                    "entry_price": entry_price,
                    "close": close[i],
                    "current_return": current_return,
                    "reasons": "+".join(reasons),
                    "base_trade_entry_bar": base_entry,
                    "base_trade_exit_bar": int(base_trade["step"]) if base_trade else -1,
                    "base_trade_pnl": base_pnl,
                    "base_trade_winner": base_pnl > 0,
                    "base_trade_top20_winner": base_pnl >= top20_cutoff,
                    "base_trade_worst20_loser": base_entry in worst20_entries,
                    "lower_shadow_ratio_2h": safe_float(features.at[i, "lower_shadow_ratio_2h"]),
                    "upper_shadow_ratio_2h": safe_float(features.at[i, "upper_shadow_ratio_2h"]),
                    "volume_ratio_2h": safe_float(features.at[i, "volume_ratio_2h"]),
                    "volume_ratio_1h": safe_float(features.at[i, "volume_ratio_1h"]),
                    "oi_change_2h": safe_float(features.at[i, "oi_change_2h"]),
                    "oi_change_4h": safe_float(features.at[i, "oi_change_4h"]),
                    "macd_hist_2h": safe_float(features.at[i, "macd_hist_2h"]),
                    "macd_hist_4h": safe_float(features.at[i, "macd_hist_4h"]),
                }
            )
            decision = 0
            lockout = pos
            pos = 0
            entry_bar = -1
            entry_price = 0.0
            setup_side = 0
            setup_bar = -1
            setup_until = -1
        out[i] = decision
    return out, exits


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if np.isfinite(out) else float("nan")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = sorted({k for row in rows for k in row}) if rows else ["variant"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_matrix() -> dict[str, Any]:
    df, base_signals, scope = exp0110.load_base()
    df = clean_ohlcv(df)
    split_idx = int(scope["split_idx"])
    oi = load_local_oi()
    features = build_market_signal_features(df, oi)
    variants = build_variant_matrix()

    base_eval = exp0110.evaluate(base_signals, df, split_idx)
    base_summary = base_eval["summary"]
    base_metrics = base_eval["metrics"]
    _, base_trades = exp0110.helper0108.next_open_trades(base_signals, df)
    top_winners = sorted([float(t["pnl"]) for t in base_trades if float(t["pnl"]) > 0], reverse=True)[:20]
    top20_cutoff = min(top_winners) if top_winners else float("inf")
    worst_losers = sorted([t for t in base_trades if float(t["pnl"]) < 0], key=lambda t: float(t["pnl"]))[:20]
    worst20_entries = {int(t["entry_step"]) for t in worst_losers}
    base_capture = capture_ratio(base_signals, df)

    rows: list[dict[str, Any]] = []
    exit_rows: list[dict[str, Any]] = []
    for variant in variants:
        print(f"=== {variant.variant} ===", flush=True)
        sig, exits = apply_market_signal_tp(base_signals, df, features, variant, base_trades, top20_cutoff, worst20_entries)
        result = exp0110.evaluate(sig, df, split_idx)
        cap = capture_ratio(sig, df)
        unique_exits = unique_trade_rows(exits)
        dd_improve = (abs(base_metrics["dd"]) - abs(result["metrics"]["dd"])) / abs(base_metrics["dd"])
        row = {
            **asdict(variant),
            "exits": len(exits),
            "unique_base_trades_exited": len(unique_exits),
            "oos_return": result["summary"]["oos"]["return"],
            "delta_oos": result["summary"]["oos"]["return"] - base_summary["oos"]["return"],
            "full_return": result["metrics"]["return"],
            "delta_full": result["metrics"]["return"] - base_metrics["return"],
            "full_dd": result["metrics"]["dd"],
            "dd_improve_rel": dd_improve,
            "rolling12_min": result["summary"]["rolling_12m_min_return"],
            "delta_roll12": result["summary"]["rolling_12m_min_return"] - base_summary["rolling_12m_min_return"],
            "trades": result["metrics"]["trades"],
            "mfe_capture_avg": cap["mfe_capture_avg"],
            "delta_mfe_capture_avg": cap["mfe_capture_avg"] - base_capture["mfe_capture_avg"],
            "giveback_to_loss_6_count": cap["giveback_to_loss_6_count"],
            "delta_giveback_to_loss_6_count": cap["giveback_to_loss_6_count"]
            - base_capture["giveback_to_loss_6_count"],
            "giveback_to_loss_6_pnl": cap["giveback_to_loss_6_pnl"],
            "avg_bars_held": cap["avg_bars_held"],
            "delta_avg_bars_held": cap["avg_bars_held"] - base_capture["avg_bars_held"],
            "exited_base_pnl": float(sum(e["base_trade_pnl"] for e in unique_exits)),
            "exited_base_winner_pnl": float(sum(e["base_trade_pnl"] for e in unique_exits if e["base_trade_winner"])),
            "exited_base_loser_pnl": float(sum(e["base_trade_pnl"] for e in unique_exits if not e["base_trade_winner"])),
            "top20_winner_exits": sum(1 for e in unique_exits if e["base_trade_top20_winner"]),
            "top20_winner_exited_pnl": float(
                sum(e["base_trade_pnl"] for e in unique_exits if e["base_trade_top20_winner"])
            ),
            "worst20_loser_exits": sum(1 for e in unique_exits if e["base_trade_worst20_loser"]),
            "worst20_loser_exited_pnl": float(
                sum(e["base_trade_pnl"] for e in unique_exits if e["base_trade_worst20_loser"])
            ),
            **yearly_delta(base_signals, sig, df),
        }
        row["pass_gate"] = (
            row["exits"] > 0
            and row["oos_return"] >= base_summary["oos"]["return"] * 0.95
            and row["dd_improve_rel"] >= 0.0
            and row["delta_roll12"] >= -0.01
            and row["top20_winner_exits"] <= 1
            and row["trades"] <= base_metrics["trades"] * 1.20
            and row["year_losses"] <= 2
        )
        rows.append(row)
        exit_rows.extend(exits)

    rows = sorted(rows, key=lambda r: (r["pass_gate"], r["dd_improve_rel"], r["delta_oos"]), reverse=True)
    report = {
        "scope": {
            "experiment_id": "exp_0131",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "mode": "market-signal take-profit overlay; profitable positions only; next-open close-to-flat",
            "data": str(exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{bar_time(df, 0)} to {bar_time(df, len(df) - 1)}",
            "oi": str(OI_PATH.relative_to(PROJECT_ROOT)) if OI_PATH.exists() else "missing",
            "lookahead_guard": "completed HTF bars plus shifted OI, aligned by merge_asof backward",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "variant_count": len(variants),
            "moirai_blocked": int(scope["moirai_blocked"]),
        },
        "baseline": {
            "raw_next_open": base_summary,
            "metrics": base_metrics,
            "capture": base_capture,
            "regime_permission_result": "not_applicable_post_v22_moirai_baseline",
            "safe_execution_result": "next_open_evaluation_close_to_flat_overlay",
        },
        "rows": rows,
        "exits": exit_rows,
    }
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), rows)
    write_csv(OUT.with_name(OUT.name + "_exits").with_suffix(".csv"), exit_rows)
    write_matrix_markdown(rows, report)
    print(OUT.with_suffix(".md"))
    for row in rows[:10]:
        print(
            row["variant"],
            "pass",
            row["pass_gate"],
            "exits",
            row["exits"],
            "dOOS",
            round(row["delta_oos"] * 100, 2),
            "ddImp",
            round(row["dd_improve_rel"] * 100, 2),
            "top20",
            row["top20_winner_exits"],
        )
    return report


def write_matrix_markdown(rows: list[dict[str, Any]], report: dict[str, Any]) -> None:
    base = report["baseline"]
    base_summary = base["raw_next_open"]
    base_metrics = base["metrics"]
    pass_count = sum(1 for row in rows if row["pass_gate"])
    verdict = "OBSERVE" if pass_count else "REJECT"
    md = [
        "# exp_0131 v2.2 + Moirai market-signal take-profit",
        "",
        "- diagnostic only; no live/checkpoint/config change",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- overlay: completed HTF market reversal signal, current trade must be profitable, next-open close-to-flat",
        "- lockout: same-direction reentry is suppressed until the base signal leaves that direction",
        "- no MFE/giveback/fixed take-profit signal is used for triggering",
        f"- baseline OOS/full/DD/roll12/trades: {pct(base_summary['oos']['return'])} / {pct(base_metrics['return'])} / {pct(base_metrics['dd'])} / {pct(base_summary['rolling_12m_min_return'])} / {base_metrics['trades']}",
        f"- verdict: `{verdict}` from matrix pass count `{pass_count}`; live-case replay is reported separately",
        "",
        "## Top Rows",
        "",
        "| variant | exits | OOS | dOOS | full DD | DD improve | roll12 | top20 cut | worst20 hit | year W/L/F | pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows[:30]:
        md.append(
            f"| {row['variant']} | {row['exits']} | {pct(row['oos_return'])} | {pct(row['delta_oos'])} | "
            f"{pct(row['full_dd'])} | {pct(row['dd_improve_rel'])} | {pct(row['rolling12_min'])} | "
            f"{row['top20_winner_exits']} | {row['worst20_loser_exits']} | "
            f"{row['year_wins']}/{row['year_losses']}/{row['year_flat']} | {row['pass_gate']} |"
        )
    md.extend(
        [
            "",
            "## Reporting Contract",
            "",
            f"- raw result: baseline and overlay rows use next-open evaluation; full matrix in `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            "- regime-permission result: not applicable here because the input is already the v2.2 + Moirai post-gate baseline signal stream.",
            "- safe-execution result: overlay only emits close-to-flat and is evaluated at next open; it never directly reverses or opens a new position.",
            "- top-winner damage: `top20_winner_exits` and `top20_winner_exited_pnl` columns.",
            "- worst-loser reduction: `worst20_loser_exits` and `worst20_loser_exited_pnl` columns.",
            "- conclusion: no live action; any non-rejected row remains research-only until separately reviewed and approved.",
            "",
            f"Exits CSV: `{OUT.with_name(OUT.name + '_exits').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def fetch_json(url: str, params: dict[str, Any], timeout: float = 15.0) -> Any:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "autoresearch-crypto-exp0131"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_live_oi(start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    frames = []
    sources = []
    try:
        rows = fetch_json(
            "https://fapi.binance.com/futures/data/openInterestHist",
            {
                "pair": "ETHUSDT",
                "contractType": "PERPETUAL",
                "period": "1h",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 500,
            },
        )
        if rows:
            frames.append(
                pd.DataFrame(
                    {
                        "available_at": pd.to_datetime([int(r["timestamp"]) for r in rows], unit="ms"),
                        "binance_open_interest": [float(r["sumOpenInterest"]) for r in rows],
                    }
                )
            )
            sources.append("binance")
    except Exception as exc:  # noqa: BLE001 - diagnostics should keep running without public OI.
        sources.append(f"binance_error:{type(exc).__name__}")
    try:
        payload = fetch_json(
            "https://api.bybit.com/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": "ETHUSDT",
                "intervalTime": "1h",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 200,
            },
        )
        rows = payload.get("result", {}).get("list", []) if isinstance(payload, dict) else []
        if rows:
            frames.append(
                pd.DataFrame(
                    {
                        "available_at": pd.to_datetime([int(r["timestamp"]) for r in rows], unit="ms"),
                        "bybit_open_interest": [float(r["openInterest"]) for r in rows],
                    }
                )
            )
            sources.append("bybit")
    except Exception as exc:  # noqa: BLE001
        sources.append(f"bybit_error:{type(exc).__name__}")
    if not frames:
        return pd.DataFrame(), ",".join(sources) if sources else "unavailable"

    merged = frames[0].sort_values("available_at")
    for frame in frames[1:]:
        merged = pd.merge(merged, frame.sort_values("available_at"), on="available_at", how="outer")
    oi_cols = [c for c in merged.columns if c.endswith("_open_interest")]
    merged["combined_open_interest"] = merged[oi_cols].sum(axis=1, min_count=1)
    return merged.sort_values("available_at"), ",".join(sources)


def run_live_case() -> list[dict[str, Any]]:
    if not LIVE_CACHE.exists():
        raise SystemExit(f"missing live cache: {LIVE_CACHE}")
    df = clean_ohlcv(pd.read_parquet(LIVE_CACHE))
    start = pd.Timestamp(LIVE_ENTRY_TIME) - pd.Timedelta(days=3)
    end = pd.Timestamp(LIVE_EXIT_TIME) + pd.Timedelta(hours=4)
    oi, oi_source = fetch_live_oi(start, end)
    features = build_market_signal_features(df, oi)
    variants = build_variant_matrix()
    times = pd.to_datetime(df["datetime"])
    entry_idx = int(np.searchsorted(times.to_numpy(), np.datetime64(LIVE_ENTRY_TIME), side="left"))
    end_idx = int(np.searchsorted(times.to_numpy(), np.datetime64(LIVE_EXIT_TIME), side="right") - 1)
    if entry_idx >= len(df) or end_idx < entry_idx:
        raise SystemExit("live cache does not cover configured live-case window")

    rows: list[dict[str, Any]] = []
    for variant in variants:
        trigger_row = first_live_trigger(df, features, variant, entry_idx, end_idx, oi_source)
        if trigger_row is None:
            trigger_row = {
                **asdict(variant),
                "status": "no_trigger",
                "trigger_bar": -1,
                "trigger_time_utc": "",
                "trigger_time_cst": "",
                "close": float("nan"),
                "current_return": float("nan"),
                "saved_vs_1590": float("nan"),
                "before_1590": False,
                "reasons": "",
                "oi_source": oi_source,
            }
        rows.append(trigger_row)

    rows = sorted(
        rows,
        key=lambda r: (
            r["status"] != "triggered",
            r["trigger_bar"] if r["trigger_bar"] >= 0 else 10**12,
            r["variant"],
        ),
    )
    path = OUT.with_name(OUT.name + "_live_case").with_suffix(".csv")
    write_csv(path, rows)
    print(path)
    for row in rows[:12]:
        print(row["variant"], row["status"], row["trigger_time_cst"], row["close"], row["reasons"])
    return rows


def first_live_trigger(
    df: pd.DataFrame,
    features: pd.DataFrame,
    variant: VariantSpec,
    entry_idx: int,
    end_idx: int,
    oi_source: str,
) -> dict[str, Any] | None:
    short_mask, long_mask = trigger_masks(features, variant)
    short_setup, long_setup = shape_masks(features, variant)
    short_confirm, long_confirm = sequence_confirm_masks(features, variant)
    mask = short_mask if LIVE_SIDE < 0 else long_mask
    setup = short_setup if LIVE_SIDE < 0 else long_setup
    confirm = short_confirm if LIVE_SIDE < 0 else long_confirm
    setup_bar = -1
    setup_until = -1

    for i in range(entry_idx, end_idx + 1):
        close = float(df["close"].iloc[i])
        current_return = LIVE_ENTRY_PRICE / max(close, 1e-12) - 1.0 if LIVE_SIDE < 0 else close / LIVE_ENTRY_PRICE - 1.0
        if current_return <= 0.0:
            continue
        reasons: list[str] = []
        triggered = False
        if variant.sequence_macd_tf:
            if setup[i]:
                setup_bar = i
                setup_until = i + int(variant.sequence_window_bars)
            if setup_bar >= 0 and i <= setup_until and confirm[i]:
                setup_label = "2h_lower_wick_setup" if LIVE_SIDE < 0 else "2h_upper_wick_setup"
                cross_label = "macd_bull_cross" if LIVE_SIDE < 0 else "macd_bear_cross"
                reasons = [f"{setup_label}@{bar_time(df, setup_bar)}", f"{cross_label}_{variant.sequence_macd_tf}"]
                triggered = True
            elif setup_bar >= 0 and i > setup_until:
                setup_bar = -1
                setup_until = -1
        elif mask[i]:
            reasons = trigger_reasons(features.iloc[i], LIVE_SIDE, variant)
            triggered = True
        if not triggered:
            continue
        return {
            **asdict(variant),
            "status": "triggered",
            "trigger_bar": i,
            "trigger_time_utc": bar_time(df, i),
            "trigger_time_cst": str(pd.Timestamp(df["datetime"].iloc[i]) + pd.Timedelta(hours=8)),
            "close": close,
            "current_return": current_return,
            "saved_vs_1590": 1590.0 - close,
            "before_1590": close < 1590.0,
            "reasons": "+".join(reasons),
            "oi_source": oi_source,
            "lower_shadow_ratio_2h": safe_float(features.at[i, "lower_shadow_ratio_2h"]),
            "volume_ratio_2h": safe_float(features.at[i, "volume_ratio_2h"]),
            "volume_ratio_1h": safe_float(features.at[i, "volume_ratio_1h"]),
            "oi_change_2h": safe_float(features.at[i, "oi_change_2h"]),
            "oi_change_4h": safe_float(features.at[i, "oi_change_4h"]),
            "macd_hist_2h": safe_float(features.at[i, "macd_hist_2h"]),
            "macd_hist_4h": safe_float(features.at[i, "macd_hist_4h"]),
        }
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Market-signal take-profit matrix for v2.2 + Moirai.")
    parser.add_argument("--mode", choices=["matrix", "live-case", "all"], default="matrix")
    args = parser.parse_args()
    if args.mode in {"matrix", "all"}:
        run_matrix()
    if args.mode in {"live-case", "all"}:
        run_live_case()


if __name__ == "__main__":
    main()
