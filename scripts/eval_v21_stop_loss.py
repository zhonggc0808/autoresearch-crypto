"""Compare v2.1 balanced with vs without emergency_stop_pct at multiple levels.

Usage: uv run python scripts/eval_v21_stop_loss.py [2600d|1300d]
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dex.checkpoints import load_checkpoint
from dex.config import DATA_DIR
from scripts.research_oracle import (
    _evaluate_signals,
    _generate_v21_signals,
    _load_and_split_data,
    _safe_execution_signals,
)

SPLIT_RATIO = 0.70


def _summary(label: str, d: dict) -> str:
    return (
        f"  {label:20s}  return={d['return']:+.2%}  dd={d['dd']:+.2%}  "
        f"sharpe={d['sharpe']:.3f}  trades={d['trades']}"
    )


def main():
    days = sys.argv[1] if len(sys.argv) > 1 else "1300d"
    data_path = Path(DATA_DIR) / f"ETHUSDT_5m_{days}.parquet"
    if not data_path.exists():
        print(f"Data file not found: {data_path}")
        sys.exit(1)

    checkpoint_path = (
        Path(__file__).resolve().parent.parent
        / "checkpoints"
        / "channel_breakout_v2_1_balanced.pt"
    )
    checkpoint = load_checkpoint(str(checkpoint_path))

    print(f"Data: {data_path.name}")
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    prices_is = df_is["close"].values.astype(float)
    prices_oos = df_oos["close"].values.astype(float)

    total_bars = len(df_full)
    is_bars = len(df_is)
    oos_bars = len(df_oos)
    print(f"Total bars: {total_bars}  IS: {is_bars} ({is_bars/total_bars:.0%})  OOS: {oos_bars} ({oos_bars/total_bars:.0%})")

    # Baseline
    print("Generating baseline (no stop)...")
    sig_base_full = _generate_v21_signals(checkpoint, df_full)
    sig_base_safe_full = _safe_execution_signals(sig_base_full)

    # Stop-loss levels
    levels = [0.03, 0.05, 0.07, 0.10, 0.15]
    sigs = {}
    for pct in levels:
        label = f"{pct:.0%}"
        print(f"Generating {label} stop...")
        ckpt = copy.deepcopy(checkpoint)
        for regime in ("bull", "bear", "neutral"):
            ckpt[regime]["strategy_params"]["emergency_stop_pct"] = pct
        raw = _generate_v21_signals(ckpt, df_full)
        sigs[label] = {
            "raw": raw,
            "safe": _safe_execution_signals(raw),
        }

    baseline_is = _evaluate_signals(sig_base_full[:split_idx], prices_is)
    baseline_oos = _evaluate_signals(sig_base_full[split_idx:], prices_oos)
    baseline_safe_is = _evaluate_signals(sig_base_safe_full[:split_idx], prices_is)
    baseline_safe_oos = _evaluate_signals(sig_base_safe_full[split_idx:], prices_oos)

    # ── IS ──
    print("\n" + "=" * 90)
    print("IS")
    print("=" * 90)
    print(f"{'variant':20s}  {'return':>8s}  {'dd':>8s}  {'sharpe':>7s}  {'trades':>6s}")
    print("-" * 65)
    print(_summary("baseline raw", baseline_is))
    print(_summary("baseline safe", baseline_safe_is))
    for pct in levels:
        label = f"{pct:.0%}"
        r = _evaluate_signals(sigs[label]["raw"][:split_idx], prices_is)
        s = _evaluate_signals(sigs[label]["safe"][:split_idx], prices_is)
        print(_summary(f"{label} stop raw", r))
        print(_summary(f"{label} stop safe", s))

    # ── OOS ──
    print("\n" + "=" * 90)
    print("OOS")
    print("=" * 90)
    print(f"{'variant':20s}  {'return':>8s}  {'dd':>8s}  {'sharpe':>7s}  {'trades':>6s}")
    print("-" * 65)
    print(_summary("baseline raw", baseline_oos))
    print(_summary("baseline safe", baseline_safe_oos))
    for pct in levels:
        label = f"{pct:.0%}"
        r = _evaluate_signals(sigs[label]["raw"][split_idx:], prices_oos)
        s = _evaluate_signals(sigs[label]["safe"][split_idx:], prices_oos)
        print(_summary(f"{label} stop raw", r))
        print(_summary(f"{label} stop safe", s))

    # ── Full-period summary ──
    prices_full = pd.concat([pd.Series(prices_is), pd.Series(prices_oos)]).values
    print("\n" + "=" * 90)
    print(f"Full {days} summary")
    print("=" * 90)
    print(f"{'variant':20s}  {'return':>8s}  {'dd':>8s}  {'sharpe':>7s}  {'trades':>6s}  {'stops':>6s}")
    print("-" * 80)

    def _row(label, sig):
        m = _evaluate_signals(sig, prices_full)
        stops = ((sig == 0) & ((sig_base_full == 2) | (sig_base_full == 3))).sum()
        return f"{label:20s}  {m['return']:+.2%}  {m['dd']:+.2%}  {m['sharpe']:7.3f}  {m['trades']:6d}  {stops:6d}"

    print(_row("baseline raw", sig_base_full))
    print(_row("baseline safe", sig_base_safe_full))
    for pct in levels:
        label = f"{pct:.0%} stop"
        print(_row(f"{label} raw", sigs[label]["raw"]))
        print(_row(f"{label} safe", sigs[label]["safe"]))


if __name__ == "__main__":
    main()
