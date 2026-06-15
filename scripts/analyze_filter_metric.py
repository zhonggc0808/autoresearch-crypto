#!/usr/bin/env python3
"""Analyze filter metric distribution for threshold calibration.

Usage:
    uv run python scripts/analyze_filter_metric.py --metric atr_close_ratio --lookback 48
    uv run python scripts/analyze_filter_metric.py --metric atr_close_ratio --lookback 48 --quantiles 90,95,97,99
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.filters import compute_atr_close_ratio


def main():
    parser = argparse.ArgumentParser(
        description="Analyze filter metric distribution for threshold calibration"
    )
    parser.add_argument(
        "--metric", type=str, default="atr_close_ratio",
        choices=["atr_close_ratio"],
        help="Metric to analyze",
    )
    parser.add_argument(
        "--lookback", type=int, default=48,
        help="Lookback bars for metric computation",
    )
    parser.add_argument(
        "--quantiles", type=str, default="50,75,90,95,97,99",
        help="Comma-separated percentile list",
    )
    args = parser.parse_args()

    # Load data
    from scripts.research_oracle import _find_eth_data
    data_path = _find_eth_data()

    import pandas as pd
    df = (
        pd.read_parquet(data_path)
        .sort_values("timestamp")
        .drop_duplicates(subset="timestamp")
        .reset_index(drop=True)
    )

    print(f"\nData: {data_path.name}")
    print(f"Bars: {len(df)}")
    print(f"Range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    print(f"Lookback: {args.lookback} bars")

    if args.metric == "atr_close_ratio":
        ratio = compute_atr_close_ratio(
            df["high"].values.astype(float),
            df["low"].values.astype(float),
            df["close"].values.astype(float),
            lookback=args.lookback,
        )
    else:
        print(f"Unknown metric: {args.metric}")
        sys.exit(1)

    qs = [int(q.strip()) for q in args.quantiles.split(",")]
    values = np.percentile(ratio, qs)

    print(f"\n--- ATR/close ratio percentiles ---")
    print(f"  min:   {ratio.min():.6f}")
    for q, v in zip(qs, values):
        print(f"  p{q:02d}:   {v:.6f}")
    print(f"  max:   {ratio.max():.6f}")
    print(f"  mean:  {ratio.mean():.6f}")

    # Suggested thresholds
    print(f"\n--- Suggested thresholds (calibrated) ---")
    for q, v in zip(qs, values):
        if q >= 90:
            print(f"  p{q}:  threshold = {v:.4f}  ({(ratio > v).sum()} of {len(ratio)} bars hit)")

    # Count bars that would have been hit at a few thresholds
    print(f"\n--- Entry blocking impact (bars where gate would activate) ---")
    for thresh in [0.005, 0.01, 0.02, 0.03, 0.05, 0.06]:
        hit = (ratio > thresh).sum()
        pct = hit / len(ratio) * 100
        print(f"  > {thresh:.3f}:  {hit} bars ({pct:.1f}%)")


if __name__ == "__main__":
    main()
