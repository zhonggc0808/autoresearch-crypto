"""Frozen market-intelligence overlay for historical backtests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

VALID_MODES = {"normal", "cautious", "defensive", "block_new_entries"}
MODE_POSITION_SIZE = {
    "normal": 1.0,
    "cautious": 0.5,
    "defensive": 0.0,
    "block_new_entries": 0.0,
}
TIME_COLUMNS = ("available_at", "timestamp", "datetime")


@dataclass(frozen=True)
class MarketIntelOverlayStats:
    """Summary of how frozen market intelligence affected entries."""

    total_bars: int
    mode_counts: dict[str, int]
    vetoed_new_entries: int
    half_size_entries: int
    expired_intel_bars: int
    missing_intel_bars: int
    vetoed_entry_steps: tuple[int, ...]
    half_size_entry_steps: tuple[int, ...]


@dataclass(frozen=True)
class RandomEntryControlStats:
    """Summary of a random entry-level veto/half-size control run."""

    seed: int
    available_entry_count: int
    vetoed_new_entries: int
    half_size_entries: int
    vetoed_entry_steps: tuple[int, ...]
    half_size_entry_steps: tuple[int, ...]


def load_market_intel(path: str | Path) -> pd.DataFrame:
    """Load a frozen market-intel table from CSV or Parquet."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    raise ValueError("market intel path must be .csv or .parquet")


def build_market_intel_overlay(
    bars: pd.DataFrame,
    intel: pd.DataFrame,
    signals: np.ndarray | None = None,
    max_age_hours: float = 3.0,
    unknown_mode: str = "cautious",
) -> tuple[np.ndarray, np.ndarray, MarketIntelOverlayStats]:
    """Return effective modes and entry size multipliers for each bar."""
    unknown_mode = unknown_mode.lower()
    if unknown_mode not in VALID_MODES:
        raise ValueError(f"unknown_mode must be one of {sorted(VALID_MODES)}")
    if "mode" not in intel.columns:
        raise ValueError("market intel table must contain mode")
    if max_age_hours <= 0:
        raise ValueError("max_age_hours must be positive")

    bar_time_col = _time_column(bars)
    intel_time_col = _time_column(intel)

    clean_intel = intel.copy()
    clean_intel["mode"] = clean_intel["mode"].astype(str).str.lower()
    bad_modes = sorted(set(clean_intel["mode"]) - VALID_MODES)
    if bad_modes:
        raise ValueError(f"unknown market intel mode(s): {bad_modes}")

    decisions = pd.DataFrame(
        {
            "_order": np.arange(len(bars)),
            "decision_at": _to_datetime(bars[bar_time_col]),
        }
    ).dropna(subset=["decision_at"])
    clean_intel = pd.DataFrame(
        {
            "available_at": _to_datetime(clean_intel[intel_time_col]),
            "mode": clean_intel["mode"].to_numpy(),
        }
    ).dropna(subset=["available_at"])

    matched = pd.merge_asof(
        decisions.sort_values("decision_at"),
        clean_intel.sort_values("available_at"),
        left_on="decision_at",
        right_on="available_at",
        direction="backward",
    ).sort_values("_order")

    missing = matched["available_at"].isna().to_numpy()
    age = matched["decision_at"] - matched["available_at"]
    expired = (~missing) & (age > pd.Timedelta(hours=max_age_hours)).to_numpy()

    modes = matched["mode"].fillna(unknown_mode).to_numpy(dtype=object)
    modes[expired | missing] = unknown_mode
    position_sizes = np.array([MODE_POSITION_SIZE[m] for m in modes], dtype=float)

    veto_steps, half_steps = _entry_override_steps(signals, position_sizes)
    stats = MarketIntelOverlayStats(
        total_bars=len(position_sizes),
        mode_counts={m: int((modes == m).sum()) for m in sorted(VALID_MODES)},
        vetoed_new_entries=len(veto_steps),
        half_size_entries=len(half_steps),
        expired_intel_bars=int(expired.sum()),
        missing_intel_bars=int(missing.sum()),
        vetoed_entry_steps=tuple(veto_steps),
        half_size_entry_steps=tuple(half_steps),
    )
    return modes, position_sizes, stats


def build_random_entry_control(
    signals: np.ndarray,
    veto_count: int,
    half_count: int,
    seed: int = 42,
) -> tuple[np.ndarray, RandomEntryControlStats]:
    """Build a random control with the same entry veto/half-size counts."""
    if veto_count < 0 or half_count < 0:
        raise ValueError("veto_count and half_count must be non-negative")
    entry_steps = _new_entry_steps(signals)
    total_requested = veto_count + half_count
    if total_requested > len(entry_steps):
        raise ValueError("requested random controls exceed available entry count")

    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(entry_steps)
    veto_steps = tuple(sorted(int(i) for i in shuffled[:veto_count]))
    half_steps = tuple(sorted(int(i) for i in shuffled[veto_count:total_requested]))

    position_sizes = np.ones(len(signals), dtype=float)
    position_sizes[list(veto_steps)] = 0.0
    position_sizes[list(half_steps)] = 0.5
    stats = RandomEntryControlStats(
        seed=seed,
        available_entry_count=len(entry_steps),
        vetoed_new_entries=len(veto_steps),
        half_size_entries=len(half_steps),
        vetoed_entry_steps=veto_steps,
        half_size_entry_steps=half_steps,
    )
    return position_sizes, stats


def _entry_override_steps(
    signals: np.ndarray | None,
    position_sizes: np.ndarray,
) -> tuple[list[int], list[int]]:
    if signals is None:
        return [], []
    if len(signals) != len(position_sizes):
        raise ValueError("signals and position_sizes must have the same length")

    veto_steps: list[int] = []
    half_steps: list[int] = []
    position = 0
    for i, signal in enumerate(np.asarray(signals, dtype=int)):
        target = position
        if signal == 2:
            target = 1
        elif signal == 3:
            target = -1
        elif signal == 0:
            target = 0

        opens_new = target != 0 and target != position
        if opens_new:
            if position_sizes[i] == 0:
                veto_steps.append(i)
                position = 0
                continue
            if position_sizes[i] < 1:
                half_steps.append(i)

        position = target
    return veto_steps, half_steps


def _new_entry_steps(signals: np.ndarray) -> np.ndarray:
    steps: list[int] = []
    position = 0
    for i, signal in enumerate(np.asarray(signals, dtype=int)):
        target = position
        if signal == 2:
            target = 1
        elif signal == 3:
            target = -1
        elif signal == 0:
            target = 0

        if target != 0 and target != position:
            steps.append(i)
        position = target
    return np.asarray(steps, dtype=int)


def _time_column(df: pd.DataFrame) -> str:
    for col in TIME_COLUMNS:
        if col in df.columns:
            return col
    raise ValueError(f"table must contain one of {TIME_COLUMNS}")


def _to_datetime(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        values = series.to_numpy(dtype=float)
        max_abs = float(np.nanmax(np.abs(values))) if len(values) else 0.0
        if max_abs > 10_000_000_000:
            return pd.to_datetime(series, unit="ms", errors="coerce")
        if max_abs > 10_000_000:
            return pd.to_datetime(series, unit="s", errors="coerce")
    return pd.to_datetime(series, errors="coerce")
