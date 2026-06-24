"""
Validate local parquet OHLCV data against Binance official public data.

Downloads monthly kline ZIPs from data.binance.vision for sampled months
and compares OHLCV + volume fields bar-by-bar against the local parquet file.

Usage:
    .venv\\Scripts\\python.exe scripts\validate_data_vs_binance.py
    .venv\\Scripts\\python.exe scripts\validate_data_vs_binance.py --parquet data/crypto/ETHUSDT_5m_2600d.parquet
    .venv\\Scripts\\python.exe scripts\validate_data_vs_binance.py --symbol BTCUSDT --parquet data/crypto/BTCUSDT_5m_730d.parquet
    .venv\\Scripts\\python.exe scripts\validate_data_vs_binance.py --sample-months 2019-09,2024-06,2026-06
    .venv\\Scripts\\python.exe scripts\validate_data_vs_binance.py --all-months  # validate every month (slow)
"""

import argparse
import os
import sys
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BINANCE_DATA_URL = "https://data.binance.vision/data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"

# Columns in Binance monthly CSV (no header)
BINANCE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count", "taker_buy_volume",
    "taker_buy_quote_volume", "ignore",
]

# Fields to compare: (binance_csv_col, parquet_col)
COMPARE_FIELDS = [
    ("open_time", "timestamp"),
    ("open", "open"),
    ("high", "high"),
    ("low", "low"),
    ("close", "close"),
    ("volume", "volume"),
    ("quote_volume", "quote_volume"),
    ("count", "num_trades"),
    ("taker_buy_volume", "taker_buy_volume"),
    ("taker_buy_quote_volume", "taker_buy_quote_volume"),
]

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "validate-data-script/1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def month_year_range(start_ts: int, end_ts: int):
    """Yield (year, month) tuples from start_ts to end_ts (millisecond timestamps)."""
    start = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
    end = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
    y, m = start.year, start.month
    while (y < end.year) or (y == end.year and m <= end.month):
        yield (y, m)
        if m == 12:
            m = 1
            y += 1
        else:
            m += 1


def sample_months(parquet_path: str, num_samples: int = 6):
    """Pick first, last, and random months from within the parquet's time range."""
    df = pd.read_parquet(parquet_path, columns=["timestamp"])
    t0, t1 = int(df["timestamp"].min()), int(df["timestamp"].max())
    all_months = list(month_year_range(t0, t1))
    if len(all_months) <= num_samples:
        return all_months
    # first + last + random middle
    rng = np.random.default_rng(42)
    n_middle = min(num_samples - 2, len(all_months) - 2)
    middle_idxs = rng.choice(len(all_months) - 2, size=n_middle, replace=False)
    middle = [all_months[1:-1][i] for i in middle_idxs]
    selected = [all_months[0]] + sorted(middle) + [all_months[-1]]
    return selected


def download_binance_month(symbol: str, interval: str, year: int, month: int):
    """Download one month ZIP from data.binance.vision, return parsed DataFrame."""
    url = BINANCE_DATA_URL.format(symbol=symbol, interval=interval, year=year, month=month)
    resp = SESSION.get(url, timeout=30)
    if resp.status_code == 404:
        return None, f"404 — no data for {year}-{month:02d}"
    resp.raise_for_status()

    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        csv_name = f"{symbol}-{interval}-{year}-{month:02d}.csv"
        with zf.open(csv_name) as f:
            df = pd.read_csv(f, header=None, names=BINANCE_COLS)
    return df, None


def compare_month(local_df: pd.DataFrame, binance_df: pd.DataFrame, month_label: str):
    """Compare one month of data, return mismatch report."""
    # Index both by timestamp for alignment
    local = local_df.set_index("timestamp").sort_index()
    bnc = binance_df.set_index("open_time").sort_index()

    common = local.index.intersection(bnc.index)
    only_local = len(local.index.difference(bnc.index))
    only_binance = len(bnc.index.difference(local.index))

    mismatches = []
    for bnc_col, pq_col in COMPARE_FIELDS:
        if bnc_col == "open_time":
            continue
        local_vals = local.loc[common, pq_col].values
        bnc_vals = bnc.loc[common, bnc_col].values

        # float comparison with relative tolerance
        diff = np.abs(local_vals - bnc_vals)
        # Use relative + absolute tolerance
        tol = np.maximum(np.abs(bnc_vals) * 1e-8, 1e-10)
        bad = diff > tol

        if bad.any():
            bad_idxs = common[bad]
            for ts in bad_idxs[:20]:  # cap at 20 per field
                mismatches.append({
                    "month": month_label,
                    "timestamp": int(ts),
                    "datetime": pd.to_datetime(ts, unit="ms").isoformat(),
                    "field": pq_col,
                    "local": float(local.loc[ts, pq_col]),
                    "binance": float(bnc.loc[ts, bnc_col]),
                    "diff": float(local.loc[ts, pq_col] - bnc.loc[ts, bnc_col]),
                    "diff_pct": float((local.loc[ts, pq_col] - bnc.loc[ts, bnc_col]) / max(abs(bnc.loc[ts, bnc_col]), 1e-12) * 100),
                })
    return {
        "month": month_label,
        "common_bars": len(common),
        "only_local": only_local,
        "only_binance": only_binance,
        "total_binance": len(bnc),
        "total_local": len(local),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Validate local parquet vs Binance official data")
    parser.add_argument("--parquet", default="data/crypto/ETHUSDT_5m_2600d.parquet",
                        help="Path to local parquet file")
    parser.add_argument("--symbol", default=None,
                        help="Symbol (e.g. ETHUSDT). Auto-detected from filename if not given.")
    parser.add_argument("--interval", default="5m",
                        help="Kline interval (default: 5m)")
    parser.add_argument("--sample-months", default=None,
                        help="Comma-separated YYYY-MM list. Default: auto-sample 6 months.")
    parser.add_argument("--all-months", action="store_true",
                        help="Validate EVERY month in the parquet range (slow).")
    parser.add_argument("--num-samples", type=int, default=6,
                        help="Number of months to sample when auto-selecting (default: 6)")
    parser.add_argument("--keep-temp", action="store_true",
                        help="Keep downloaded CSV files in temp dir")
    args = parser.parse_args()

    # Resolve parquet path
    parquet_path = args.parquet
    if not os.path.exists(parquet_path):
        print(f"ERROR: parquet file not found: {parquet_path}")
        sys.exit(1)

    # Auto-detect symbol from filename
    symbol = args.symbol
    if symbol is None:
        basename = os.path.basename(parquet_path)
        symbol = basename.split("_")[0]  # e.g. ETHUSDT_5m_2600d.parquet -> ETHUSDT

    print(f"Symbol:     {symbol}")
    print(f"Interval:   {args.interval}")
    print(f"Parquet:    {parquet_path}")
    print(f"Source:     {BINANCE_DATA_URL.replace('{symbol}', symbol).replace('{interval}', args.interval)}")
    print()

    # Determine months to validate
    if args.sample_months:
        months = [tuple(map(int, m.strip().split("-"))) for m in args.sample_months.split(",")]
    elif args.all_months:
        months = list(month_year_range(*_parquet_ts_range(parquet_path)))
        print(f"Validating ALL {len(months)} months — this may take a while...")
    else:
        months = sample_months(parquet_path, args.num_samples)
        print(f"Auto-sampled {len(months)} months: {[f'{y}-{m:02d}' for y, m in months]}")

    # Read local parquet once, keep only comparison columns
    pq_cols = ["timestamp"] + [c for _, c in COMPARE_FIELDS[1:]]  # skip open_time
    local_full = pd.read_parquet(parquet_path, columns=pq_cols)

    # Validate each month
    results = []
    all_mismatches = []
    for year, month in months:
        label = f"{year}-{month:02d}"
        print(f"  {label} ... ", end="", flush=True)

        # Filter local data to this month
        ts_start = int(datetime(year, month, 1).timestamp() * 1000)
        if month == 12:
            ts_end = int(datetime(year + 1, 1, 1).timestamp() * 1000) - 1
        else:
            ts_end = int(datetime(year, month + 1, 1).timestamp() * 1000) - 1
        local_month = local_full[(local_full["timestamp"] >= ts_start) & (local_full["timestamp"] <= ts_end)]

        if len(local_month) == 0:
            print("SKIP (no local data for this month)")
            continue

        binance_df, err = download_binance_month(symbol, args.interval, year, month)
        if err:
            print(f"SKIP ({err})")
            continue

        result = compare_month(local_month, binance_df, label)
        results.append(result)
        all_mismatches.extend(result["mismatches"])

        n_bad = len(result["mismatches"])
        if n_bad == 0:
            print(f"OK ({result['common_bars']} bars matched)")
        else:
            # Count unique bad bars
            bad_bars = len(set(m["timestamp"] for m in result["mismatches"]))
            bad_fields = set(m["field"] for m in result["mismatches"])
            print(f"MISMATCH — {bad_bars} bars, {len(result['mismatches'])} field-level diffs across {bad_fields}")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    total_common = sum(r["common_bars"] for r in results)
    total_mismatch_bars = len(set(m["timestamp"] for m in all_mismatches))
    total_mismatch_fields = len(all_mismatches)

    if total_mismatch_fields == 0:
        print(f"[PASS] ALL {total_common} bars across {len(results)} months match Binance official data.")
        print("   The local parquet file is trustworthy.")
    else:
        print(f"[FAIL] {total_mismatch_fields} field-level mismatches across {total_mismatch_bars} bars.")
        print()
        # Show the diff magnitude distribution first
        diffs_pct = [abs(m["diff_pct"]) for m in all_mismatches]
        print(f"  diff_pct range: min={min(diffs_pct):.6f}%  max={max(diffs_pct):.6f}%")
        print()
        print("--- Mismatch details (up to 30) ---")
        for m in all_mismatches[:30]:
            print(f"  [{m['month']}] {m['datetime']}  {m['field']}: "
                  f"local={m['local']}  binance={m['binance']}  "
                  f"diff={m['diff']:.6g} ({m['diff_pct']:.6f}%)")

        # Group by field for quick diagnosis
        from collections import Counter
        field_counts = Counter(m["field"] for m in all_mismatches)
        print()
        print("--- Mismatches by field ---")
        for field, count in field_counts.most_common():
            print(f"  {field}: {count}")

    # Per-month summary table
    print()
    print("--- Per-month summary ---")
    print(f"{'Month':<10} {'Bars':>8} {'OnlyLocal':>10} {'OnlyBinance':>12} {'Mismatches':>10}")
    for r in results:
        print(f"{r['month']:<10} {r['common_bars']:>8} {r['only_local']:>10} {r['only_binance']:>12} {len(r['mismatches']):>10}")

    print()
    print("Done.")


def _parquet_ts_range(parquet_path):
    df = pd.read_parquet(parquet_path, columns=["timestamp"])
    return int(df["timestamp"].min()), int(df["timestamp"].max())


if __name__ == "__main__":
    main()
