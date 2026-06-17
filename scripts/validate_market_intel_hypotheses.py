#!/usr/bin/env python3
"""Validate frozen derivatives market-intel candidate hypotheses."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_market_intel_rule_attribution import build_attribution

CANDIDATES = {
    "long_oi_down_price_up": lambda df: (
        (df["side"] == "long") & (df["oi_change_1h"] <= 0) & (df["price_return_1h"] > 0)
    ),
    "short_oi_up_price_down": lambda df: (
        (df["side"] == "short") & (df["oi_change_1h"] > 0) & (df["price_return_1h"] <= 0)
    ),
    "abs_funding_z_ge_2": lambda df: df["funding_zscore"].abs() >= 2,
}


def validate_hypotheses(df: pd.DataFrame, seeds: int = 100) -> pd.DataFrame:
    """Return candidate stats plus same-count random-bucket controls."""
    rows = []
    baseline_win_rate = float((df["pnl"] > 0).mean()) if len(df) else 0.0
    work = _with_time_split(df)
    for name, predicate in CANDIDATES.items():
        mask = predicate(work).fillna(False)
        bucket = work[mask]
        stats = _stats(name, bucket, baseline_win_rate)
        first_half = work[mask & (work["time_split"] == "first_half")]
        second_half = work[mask & (work["time_split"] == "second_half")]
        vol_bucket = _highest_vol_bucket(work, len(bucket))
        random_means = _random_bucket_means(work, len(bucket), seeds)
        stats.update(
            {
                "first_half_count": int(len(first_half)),
                "first_half_mean": _mean_or_nan(first_half),
                "second_half_count": int(len(second_half)),
                "second_half_mean": _mean_or_nan(second_half),
                "vol_only_mean": _mean_or_nan(vol_bucket),
                "vol_only_median": _median_or_nan(vol_bucket),
                "vol_only_win_rate": _win_rate_or_nan(vol_bucket),
                "random_mean_min": float(np.min(random_means)) if len(random_means) else np.nan,
                "random_mean_avg": float(np.mean(random_means)) if len(random_means) else np.nan,
                "random_mean_max": float(np.max(random_means)) if len(random_means) else np.nan,
                "random_as_bad_or_worse": int((random_means <= stats["mean"]).sum())
                if len(random_means)
                else 0,
                "random_trials": len(random_means),
            }
        )
        rows.append(stats)
    return pd.DataFrame(rows)


def write_report(results: pd.DataFrame, output_md: Path, output_csv: Path) -> None:
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output_csv, index=False)
    lines = [
        "# Market Intel Candidate Hypothesis Validation",
        "",
        "Frozen candidates, no threshold tuning:",
        "",
        "- `long_oi_down_price_up`: side=long, OI down, price up",
        "- `short_oi_up_price_down`: side=short, OI up, price down",
        "- `abs_funding_z_ge_2`: abs(funding_zscore) >= 2",
        "",
        _format_table(results),
        "",
        "Rule of thumb: a candidate is not strong if same-count random buckets often match or exceed its weakness.",
    ]
    output_md.write_text("\n".join(lines), encoding="utf-8")


def _stats(name: str, df: pd.DataFrame, baseline_win_rate: float) -> dict[str, float | str | int]:
    pnl = df["pnl"] if len(df) else pd.Series(dtype=float)
    win_rate = float((pnl > 0).mean()) if len(pnl) else np.nan
    return {
        "candidate": name,
        "count": int(len(df)),
        "mean": float(pnl.mean()) if len(pnl) else np.nan,
        "median": float(pnl.median()) if len(pnl) else np.nan,
        "win_rate": win_rate,
        "baseline_win_rate": baseline_win_rate,
        "total": float(pnl.sum()) if len(pnl) else 0.0,
        "avg_holding_bars": float(df["holding_bars"].mean()) if len(df) else np.nan,
        "passes_min_count": int(len(df) >= 20),
        "mean_negative": int(len(pnl) > 0 and pnl.mean() < 0),
        "median_nonpositive": int(len(pnl) > 0 and pnl.median() <= 0),
        "win_rate_below_baseline": int(pd.notna(win_rate) and win_rate < baseline_win_rate),
        "total_negative": int(len(pnl) > 0 and pnl.sum() < 0),
    }


def _with_time_split(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "entry_time" not in work.columns or work.empty:
        work["time_split"] = "all"
        return work
    ordered = work.sort_values("entry_time")
    split_at = len(ordered) // 2
    work["time_split"] = "second_half"
    work.loc[ordered.index[:split_at], "time_split"] = "first_half"
    return work


def _highest_vol_bucket(df: pd.DataFrame, count: int) -> pd.DataFrame:
    if count <= 0 or "realized_vol_1h" not in df.columns:
        return df.iloc[0:0]
    return df.sort_values("realized_vol_1h", ascending=False).head(count)


def _random_bucket_means(df: pd.DataFrame, count: int, seeds: int) -> np.ndarray:
    if count <= 0 or count > len(df):
        return np.array([], dtype=float)
    pnl = df["pnl"].to_numpy(dtype=float)
    means = []
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(pnl), size=count, replace=False)
        means.append(float(pnl[idx].mean()))
    return np.asarray(means, dtype=float)


def _mean_or_nan(df: pd.DataFrame) -> float:
    return float(df["pnl"].mean()) if len(df) else np.nan


def _median_or_nan(df: pd.DataFrame) -> float:
    return float(df["pnl"].median()) if len(df) else np.nan


def _win_rate_or_nan(df: pd.DataFrame) -> float:
    return float((df["pnl"] > 0).mean()) if len(df) else np.nan


def _format_table(df: pd.DataFrame) -> str:
    formatted = df.copy()
    for col in formatted.columns:
        if formatted[col].dtype.kind in {"f"}:
            formatted[col] = formatted[col].map(lambda x: f"{x:.4f}" if pd.notna(x) else "nan")
    cols = list(formatted.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for row in formatted.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate frozen market-intel hypotheses")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--days", type=int, default=25)
    parser.add_argument("--checkpoint", default="checkpoints/channel_breakout_v2_1_balanced.pt")
    parser.add_argument("--market-intel", required=True)
    parser.add_argument("--random-trials", type=int, default=100)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args()

    trades, _ = build_attribution(
        args.symbol,
        args.interval,
        args.days,
        args.checkpoint,
        args.market_intel,
    )
    results = validate_hypotheses(trades, seeds=args.random_trials)
    write_report(results, Path(args.output_md), Path(args.output_csv))
    print(f"Wrote {len(results)} hypothesis rows to {args.output_csv}")
    print(f"Wrote report to {args.output_md}")


if __name__ == "__main__":
    main()
