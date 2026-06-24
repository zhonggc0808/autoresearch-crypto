#!/usr/bin/env python3
"""Diagnostic-only attribution safety check for the frozen N2B helper.

This script does not enable the helper in any runtime path. It creates a shadow
adjusted signal copy, compares transition-window attribution against baseline,
and checks whether the changed bars contaminate non-N2B transition buckets.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import BARS_PER_DAY_5M, COMMISSION, SLIPPAGE
from dex.execution_safety import apply_n2b_1d_block_reversals_only
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import (
    HORIZON_DAYS,
    TRANSITIONS,
    _datetimes,
    _transition_events,
    _window_return_and_dd,
)

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "n2b_attribution_safety_check"
NOTES_MD = OUTPUT_DIR / "n2b_frozen_patch_attribution_safety_check_v0.md"
SUMMARY_TSV = OUTPUT_DIR / "n2b_frozen_patch_attribution_safety_summary.tsv"
CHANGED_TSV = OUTPUT_DIR / "n2b_frozen_patch_changed_bars.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_float(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _simulate_equity(signals: np.ndarray, prices: np.ndarray) -> np.ndarray:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, _ = evaluator.simulate(signals, prices)
    return equity


def _changed_bar_rows(
    baseline: np.ndarray,
    adjusted: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
) -> List[Dict[str, Any]]:
    changed = np.flatnonzero(np.asarray(baseline, dtype=int) != np.asarray(adjusted, dtype=int))
    labels = np.asarray(regimes, dtype=object)
    rows = []
    for idx in changed:
        warmup_start = None
        for j in range(idx, -1, -1):
            if j > 0 and str(labels[j - 1]) == "NEUTRAL" and str(labels[j]) == "BEAR":
                if idx < j + BARS_PER_DAY_5M:
                    warmup_start = j
                break
        rows.append(
            {
                "bar": int(idx),
                "time": str(times[idx]),
                "baseline_signal": int(baseline[idx]),
                "adjusted_signal": int(adjusted[idx]),
                "regime": str(labels[idx]),
                "prev_regime": str(labels[idx - 1]) if idx > 0 else None,
                "n2b_warmup_start": warmup_start,
                "n2b_warmup_start_time": str(times[warmup_start])
                if warmup_start is not None
                else None,
                "bars_since_n2b": int(idx - warmup_start) if warmup_start is not None else None,
            }
        )
    return rows


def _bucket_rows(
    baseline_signals: np.ndarray,
    adjusted_signals: np.ndarray,
    prices: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    changed_bars: np.ndarray,
) -> List[Dict[str, Any]]:
    baseline_equity = _simulate_equity(baseline_signals, prices)
    adjusted_equity = _simulate_equity(adjusted_signals, prices)
    transitions = _transition_events(regimes, times)
    rows = []

    for transition in TRANSITIONS:
        events = [event for event in transitions if event.transition == transition]
        for days in HORIZON_DAYS:
            horizon_bars = days * BARS_PER_DAY_5M
            baseline_returns = []
            adjusted_returns = []
            baseline_dds = []
            adjusted_dds = []
            windows_with_changed_bars = 0
            changed_bar_count = 0
            changed_event_ids = []
            for event_idx, event in enumerate(events, start=1):
                start = event.step
                end = min(len(baseline_signals), start + horizon_bars)
                if start >= end:
                    continue
                base_ret, base_dd, _ = _window_return_and_dd(baseline_equity, start, end)
                adj_ret, adj_dd, _ = _window_return_and_dd(adjusted_equity, start, end)
                baseline_returns.append(base_ret)
                adjusted_returns.append(adj_ret)
                baseline_dds.append(base_dd)
                adjusted_dds.append(adj_dd)

                overlap = changed_bars[(changed_bars >= start) & (changed_bars < end)]
                if len(overlap) > 0:
                    windows_with_changed_bars += 1
                    changed_bar_count += int(len(overlap))
                    changed_event_ids.append(f"{transition}_{event_idx:04d}")

            base_mean = mean(baseline_returns) if baseline_returns else None
            adj_mean = mean(adjusted_returns) if adjusted_returns else None
            base_worst_dd = min(baseline_dds) if baseline_dds else None
            adj_worst_dd = min(adjusted_dds) if adjusted_dds else None
            rows.append(
                {
                    "transition": transition,
                    "horizon": f"{days}d",
                    "horizon_days": days,
                    "transition_count": len(events),
                    "baseline_mean_return": _round_float(base_mean),
                    "adjusted_mean_return": _round_float(adj_mean),
                    "mean_return_delta": _round_float(
                        adj_mean - base_mean if adj_mean is not None and base_mean is not None else None
                    ),
                    "baseline_worst_dd": _round_float(base_worst_dd),
                    "adjusted_worst_dd": _round_float(adj_worst_dd),
                    "worst_dd_delta": _round_float(
                        adj_worst_dd - base_worst_dd
                        if adj_worst_dd is not None and base_worst_dd is not None
                        else None
                    ),
                    "windows_with_changed_bars": windows_with_changed_bars,
                    "changed_bar_count": changed_bar_count,
                    "changed_event_ids": ",".join(changed_event_ids),
                }
            )
    return rows


def _make_conclusion(
    diagnostics: Dict[str, Any],
    bucket_rows: List[Dict[str, Any]],
    changed_rows: List[Dict[str, Any]],
) -> Dict[str, str]:
    if diagnostics.get("blocked_reversals_total") != 6 or len(changed_rows) != 6:
        return {
            "label": "inconclusive",
            "reason": "Frozen helper did not reproduce the expected six changed reversal bars.",
        }
    non_n2b_contamination = [
        row
        for row in bucket_rows
        if row["transition"] != "NEUTRAL->BEAR" and int(row["changed_bar_count"]) > 0
    ]
    changed_outside_n2b = [row for row in changed_rows if row["n2b_warmup_start"] is None]
    if non_n2b_contamination or changed_outside_n2b:
        return {
            "label": "cross_transition_contamination",
            "reason": (
                "Shadow helper changed bars outside the N2B warmup or inside non-N2B "
                "transition attribution windows."
            ),
        }
    return {
        "label": "attribution_clean",
        "reason": (
            "All six shadow changes remain inside N2B 1d warmups, and no non-N2B "
            "transition bucket contains changed bars."
        ),
    }


def _write_tsv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    conclusion = report["conclusion"]
    n2b_rows = [row for row in report["bucket_rows"] if row["transition"] == "NEUTRAL->BEAR"]
    contaminated = [
        row
        for row in report["bucket_rows"]
        if row["transition"] != "NEUTRAL->BEAR" and int(row["changed_bar_count"]) > 0
    ]
    lines = [
        "# N2B Frozen Patch Attribution Safety Check v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnostic-only safety check for the frozen N2B reversal helper.",
        "- Helper is applied only to a shadow adjusted signal copy.",
        "- No activation, default oracle, live/demo, checkpoint, family, LLM, or scoring path change.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `attribution_clean`, `cross_transition_contamination`, `inconclusive`.",
        "",
        "## Shadow Change Summary",
        "",
        f"- Changed shadow signals: {report['changed_signal_count']}",
        f"- Changed bars outside N2B 1d warmup: {report['changed_outside_n2b_warmup_count']}",
        f"- Blocked actions: {report['helper_diagnostics']['blocked_actions_total']}",
        f"- Blocked entries: {report['helper_diagnostics']['blocked_entries_total']}",
        f"- Blocked reversals: {report['helper_diagnostics']['blocked_reversals_total']}",
        f"- N2B transition count seen by helper: {report['helper_diagnostics']['transition_count']}",
        "",
        "## N2B Attribution Deltas",
        "",
        "| horizon | baseline mean | adjusted mean | delta | changed bars in windows |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in n2b_rows:
        lines.append(
            f"| {row['horizon']} | {row['baseline_mean_return']:+.2%} | "
            f"{row['adjusted_mean_return']:+.2%} | {row['mean_return_delta']:+.2%} | "
            f"{row['changed_bar_count']} |"
        )
    lines.extend(
        [
            "",
            "## Cross-Transition Check",
            "",
            f"- Non-N2B buckets with changed bars: {len(contaminated)}",
        ]
    )
    if contaminated:
        lines.append(
            "- These are attribution-window overlaps with N2B changed bars, not signal changes "
            "outside the frozen N2B helper scope."
        )
        for row in contaminated:
            lines.append(
                f"- {row['transition']} {row['horizon']}: changed_bar_count={row['changed_bar_count']}"
            )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "- This check supports attribution safety only.",
            "- It does not activate or recommend activating the helper.",
            "- Future shadow or runtime activation still requires a separate activation contract.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: N2B Frozen Patch Attribution Safety Check",
        "",
        f"Date: {report['generated_at'][:10]}",
        "",
        "## Result",
        "",
        "No protected runtime path was modified by this diagnostic safety check.",
        "",
        "Protected paths kept out of scope:",
        "",
        "- activation/default oracle wiring;",
        "- live/demo routing;",
        "- baseline params/checkpoints;",
        "- family registry;",
        "- LLM search or candidate generation;",
        "- scoring or production execution path.",
        "",
        "Current protected-path status still has one pre-existing dirty file:",
        "",
        "- `live_okx_quant.py`",
        "",
        "The check did not edit or depend on that file.",
        "",
        "## Files Intentionally Added Or Updated",
        "",
        "- `scripts/n2b_frozen_patch_attribution_safety_check.py`",
        "- `research_workspace/n2b_attribution_safety_check/n2b_frozen_patch_attribution_safety_check_v0.md`",
        "- `research_workspace/n2b_attribution_safety_check/n2b_frozen_patch_attribution_safety_summary.tsv`",
        "- `research_workspace/n2b_attribution_safety_check/n2b_frozen_patch_changed_bars.tsv`",
        "- `research_workspace/n2b_attribution_safety_check/no_touch_audit.md`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_check(
    *,
    data_path: Optional[Path] = None,
    fast_days: int = 50,
    slow_days: int = 200,
) -> Dict[str, Any]:
    if data_path is None:
        data_path = _find_eth_data()
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    times = _datetimes(df_full)
    prices = df_full["close"].values.astype(float)
    regimes = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    checkpoint = _load_baseline_params()
    baseline_signals = _generate_v21_signals(
        checkpoint,
        df_full,
        fast_days=fast_days,
        slow_days=slow_days,
    )
    shadow = apply_n2b_1d_block_reversals_only(baseline_signals, regimes)
    adjusted_signals = shadow.signals
    changed_bars = np.flatnonzero(
        np.asarray(baseline_signals, dtype=int) != np.asarray(adjusted_signals, dtype=int)
    )
    changed_rows = _changed_bar_rows(baseline_signals, adjusted_signals, regimes, times)
    bucket_rows = _bucket_rows(
        baseline_signals,
        adjusted_signals,
        prices,
        regimes,
        times,
        changed_bars,
    )
    conclusion = _make_conclusion(shadow.diagnostics, bucket_rows, changed_rows)
    changed_outside_n2b = [row for row in changed_rows if row["n2b_warmup_start"] is None]
    return {
        "generated_at": _now_iso(),
        "check_id": "N2B_frozen_patch_attribution_safety_check_v0",
        "scope": "diagnostic_only_shadow_adjusted_copy",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "helper_diagnostics": shadow.diagnostics,
        "changed_signal_count": int(len(changed_bars)),
        "changed_outside_n2b_warmup_count": len(changed_outside_n2b),
        "changed_rows": changed_rows,
        "bucket_rows": bucket_rows,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only attribution safety check for frozen N2B helper."
    )
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    args = parser.parse_args()
    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_check(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_tsv(SUMMARY_TSV, report["bucket_rows"])
    _write_tsv(CHANGED_TSV, report["changed_rows"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("N2B frozen patch attribution safety check complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Summary: {SUMMARY_TSV}")
    print(f"  Changed bars: {CHANGED_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
