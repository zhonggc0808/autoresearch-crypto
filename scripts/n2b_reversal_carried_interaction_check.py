#!/usr/bin/env python3
"""Interaction check for frozen reversal patch and carried-long shadow.

Diagnostic only. Compares baseline, frozen reversal shadow, carried-long shadow,
and stacked shadow without activating or wiring any path.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import BARS_PER_DAY_5M
from dex.execution_safety import apply_n2b_1d_block_reversals_only
from dex.regime_filter import build_daily_regime_labels
from scripts.n2b_carried_position_exit_tail_diagnosis import _position_state, _round_float
from scripts.research_oracle import (
    _compute_fee_sensitivity,
    _compute_rolling_metrics,
    _evaluate_signals,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
    _safe_execution_signals,
)
from scripts.transition_attribution_audit import _datetimes, _window_return_and_dd

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "n2b_reversal_carried_interaction"
REPORT_JSON = OUTPUT_DIR / "n2b_reversal_carried_interaction_report.json"
SUMMARY_TSV = OUTPUT_DIR / "n2b_reversal_carried_interaction_summary.tsv"
EVENTS_TSV = OUTPUT_DIR / "n2b_reversal_carried_interaction_events.tsv"
NOTES_MD = OUTPUT_DIR / "n2b_reversal_carried_interaction_notes.md"

SIGNAL_NAMES = {0: "flat", 1: "hold", 2: "long", 3: "short"}
POSITION_NAMES = {-1: "short", 0: "flat", 1: "long"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _n2b_steps(regimes: np.ndarray) -> List[int]:
    labels = np.asarray(regimes, dtype=object)
    return [
        i for i in range(1, len(labels))
        if str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR"
    ]


def _simulate_metrics(
    signals: np.ndarray,
    prices: np.ndarray,
    df_full: pd.DataFrame,
    regimes: np.ndarray,
    split_idx: int,
) -> Dict[str, Any]:
    signals_is = signals[:split_idx]
    signals_oos = signals[split_idx:]
    prices_is = prices[:split_idx]
    prices_oos = prices[split_idx:]
    safe_full = _safe_execution_signals(signals)
    return {
        "is": {
            "raw": _evaluate_signals(signals_is, prices_is),
            "safe_execution": _evaluate_signals(safe_full[:split_idx], prices_is),
        },
        "oos": {
            "raw": _evaluate_signals(signals_oos, prices_oos),
            "safe_execution": _evaluate_signals(safe_full[split_idx:], prices_oos),
        },
        "rolling": _compute_rolling_metrics(signals, prices, [6, 12], regimes=regimes, df=df_full),
        "sensitivity": {
            "is": {"fees": _compute_fee_sensitivity(signals_is, prices_is)},
            "oos": {"fees": _compute_fee_sensitivity(signals_oos, prices_oos)},
        },
    }


def _n2b_window_stats(
    signals: np.ndarray,
    prices: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    horizon_days: int,
) -> Dict[str, Any]:
    from dex.config import COMMISSION, SLIPPAGE
    from dex.strategies.base import StrategyEvaluator

    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, _ = evaluator.simulate(signals, prices)
    horizon_bars = horizon_days * BARS_PER_DAY_5M
    returns = []
    dds = []
    for step in _n2b_steps(regimes):
        end = min(len(signals), step + horizon_bars)
        ret, dd, _ = _window_return_and_dd(equity, step, end)
        returns.append(ret)
        dds.append(dd)
    return {
        "mean_return": _round_float(mean(returns) if returns else None),
        "worst_dd": _round_float(min(dds) if dds else None),
        "transition_count": len(returns),
    }


def _record(
    candidate_id: str,
    signals: np.ndarray,
    prices: np.ndarray,
    df_full: pd.DataFrame,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    split_idx: int,
) -> Dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "metrics": _simulate_metrics(signals, prices, df_full, regimes, split_idx),
        "n2b_windows": {
            f"{days}d": _n2b_window_stats(signals, prices, regimes, times, days)
            for days in (1, 3, 7, 14)
        },
    }


def _reversal_events(
    baseline_signals: np.ndarray,
    reversal_signals: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
) -> List[Dict[str, Any]]:
    labels = np.asarray(regimes, dtype=object)
    position = 0
    warmup_until = -1
    active_transition_id: Optional[str] = None
    transition_count = 0
    events: List[Dict[str, Any]] = []
    for i in range(len(baseline_signals)):
        if i > 0 and str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR":
            transition_count += 1
            active_transition_id = f"n2b_{transition_count:04d}"
            warmup_until = max(warmup_until, i + BARS_PER_DAY_5M)
        raw = int(baseline_signals[i])
        adjusted = int(reversal_signals[i])
        if raw != adjusted:
            events.append(
                {
                    "event_type": "reversal_patch",
                    "transition_id": active_transition_id,
                    "bar_index": i,
                    "bar_time": str(times[i]),
                    "prev_effective_position": POSITION_NAMES[position],
                    "raw_signal": raw,
                    "raw_signal_name": SIGNAL_NAMES.get(raw, str(raw)),
                    "adjusted_signal": adjusted,
                    "adjusted_signal_name": SIGNAL_NAMES.get(adjusted, str(adjusted)),
                    "reason": "n2b_1d_direct_reversal_to_flat",
                    "in_warmup": i < warmup_until,
                }
            )
        if adjusted == 2:
            position = 1
        elif adjusted == 3:
            position = -1
        elif adjusted == 0:
            position = 0
    return events


def _apply_carried_long_shadow(signals: np.ndarray, regimes: np.ndarray) -> np.ndarray:
    shadow = np.asarray(signals, dtype=int).copy()
    state = _position_state(signals)
    for step in _n2b_steps(regimes):
        pre_position = int(state.positions[step - 1])
        post_position = int(state.positions[step])
        if pre_position == 1 and post_position == 1:
            shadow[step] = 0
    return shadow


def _carried_events(
    baseline_signals: np.ndarray,
    carried_signals: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
) -> List[Dict[str, Any]]:
    state = _position_state(baseline_signals)
    events: List[Dict[str, Any]] = []
    for transition_idx, step in enumerate(_n2b_steps(regimes), start=1):
        raw = int(baseline_signals[step])
        adjusted = int(carried_signals[step])
        if raw == adjusted:
            continue
        entry_step = int(state.entry_steps[step - 1])
        events.append(
            {
                "event_type": "carried_long_shadow",
                "transition_id": f"n2b_{transition_idx:04d}",
                "bar_index": step,
                "bar_time": str(times[step]),
                "prev_effective_position": "long",
                "raw_signal": raw,
                "raw_signal_name": SIGNAL_NAMES.get(raw, str(raw)),
                "adjusted_signal": adjusted,
                "adjusted_signal_name": SIGNAL_NAMES.get(adjusted, str(adjusted)),
                "reason": "n2b_inherited_long_flatten_shadow",
                "in_warmup": True,
                "inherited_entry_step": entry_step,
                "inherited_entry_time": str(times[entry_step]),
            }
        )
    return events


def _summary_rows(records: Dict[str, Dict[str, Any]], baseline_id: str) -> List[Dict[str, Any]]:
    baseline = records[baseline_id]
    rows = []
    for candidate_id, record in records.items():
        for horizon in ("1d", "3d", "7d", "14d"):
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "metric": f"n2b_{horizon}_mean_return",
                    "value": record["n2b_windows"][horizon]["mean_return"],
                    "delta_vs_baseline": _round_float(
                        record["n2b_windows"][horizon]["mean_return"]
                        - baseline["n2b_windows"][horizon]["mean_return"]
                    ),
                }
            )
        rows.extend(
            [
                {
                    "candidate_id": candidate_id,
                    "metric": "oos_safe_return",
                    "value": record["metrics"]["oos"]["safe_execution"]["return"],
                    "delta_vs_baseline": _round_float(
                        record["metrics"]["oos"]["safe_execution"]["return"]
                        - baseline["metrics"]["oos"]["safe_execution"]["return"]
                    ),
                },
                {
                    "candidate_id": candidate_id,
                    "metric": "rolling12_min_return",
                    "value": record["metrics"]["rolling"]["12m_min_return"],
                    "delta_vs_baseline": _round_float(
                        record["metrics"]["rolling"]["12m_min_return"]
                        - baseline["metrics"]["rolling"]["12m_min_return"]
                    ),
                },
            ]
        )
    return rows


def _make_conclusion(
    records: Dict[str, Dict[str, Any]],
    reversal_count: int,
    carried_count: int,
    overlap_count: int,
) -> Dict[str, Any]:
    rev = records["reversal_patch_only"]
    stacked = records["reversal_patch_plus_carried_long_shadow"]
    rev_1d = rev["n2b_windows"]["1d"]["mean_return"]
    stacked_1d = stacked["n2b_windows"]["1d"]["mean_return"]
    rev_3d = rev["n2b_windows"]["3d"]["mean_return"]
    stacked_3d = stacked["n2b_windows"]["3d"]["mean_return"]
    rev_oos = rev["metrics"]["oos"]["safe_execution"]["return"]
    stacked_oos = stacked["metrics"]["oos"]["safe_execution"]["return"]

    stacked_not_better = (
        stacked_1d <= rev_1d
        and stacked_3d <= rev_3d
        and stacked_oos <= rev_oos
    )
    total_unique = reversal_count + carried_count - overlap_count
    if carried_count == 2 and overlap_count == 0 and stacked_not_better:
        return {
            "label": "close_carried_long_branch",
            "reason": (
                "Carried-long shadow is independent from the frozen reversal patch, but stacking it "
                "does not improve N2B 1d/3d or OOS safe versus reversal-only."
            ),
            "total_unique_events": total_unique,
        }
    if total_unique > 8:
        return {
            "label": "needs_narrower_attribution",
            "reason": "Stacked event count exceeds the intended narrow interaction budget.",
            "total_unique_events": total_unique,
        }
    return {
        "label": "interaction_inconclusive",
        "reason": "Interaction result does not cleanly close or support the branch.",
        "total_unique_events": total_unique,
    }


def _write_events_tsv(path: Path, events: List[Dict[str, Any]]) -> None:
    fields = [
        "event_type",
        "transition_id",
        "bar_index",
        "bar_time",
        "prev_effective_position",
        "raw_signal",
        "raw_signal_name",
        "adjusted_signal",
        "adjusted_signal_name",
        "reason",
        "in_warmup",
        "inherited_entry_step",
        "inherited_entry_time",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for event in events:
            writer.writerow({field: event.get(field, "") for field in fields})


def _write_summary_tsv(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["candidate_id", "metric", "value", "delta_vs_baseline"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    records = report["records"]
    conclusion = report["conclusion"]
    lines = [
        "# N2B Reversal Patch Plus Carried Tail Shadow Interaction",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnostic interaction check only.",
        "- No helper, activation, safe-exec/default-oracle/live/demo/checkpoint/family/LLM path changed.",
        "- Compares baseline, frozen reversal patch, carried-long shadow, and stacked shadow.",
        "",
        "## Conclusion",
        "",
        f"**{conclusion['label']}**",
        "",
        conclusion["reason"],
        "",
        "## Event Counts",
        "",
        f"- Reversal events: {report['event_counts']['reversal_patch_only']}",
        f"- Carried-long events: {report['event_counts']['carried_long_shadow_only']}",
        f"- Overlap events: {report['event_counts']['overlap']}",
        f"- Stacked unique events: {conclusion['total_unique_events']}",
        "",
        "## Readout",
        "",
        "| candidate | N2B 1d | N2B 3d | OOS safe | rolling12m |",
        "|---|---:|---:|---:|---:|",
    ]
    for candidate_id in (
        "baseline_v2_1_balanced",
        "reversal_patch_only",
        "carried_long_shadow_only",
        "reversal_patch_plus_carried_long_shadow",
    ):
        record = records[candidate_id]
        lines.append(
            f"| {candidate_id} | "
            f"{record['n2b_windows']['1d']['mean_return']:+.2%} | "
            f"{record['n2b_windows']['3d']['mean_return']:+.2%} | "
            f"{record['metrics']['oos']['safe_execution']['return']:+.2%} | "
            f"{record['metrics']['rolling']['12m_min_return']:+.2%} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- `close_carried_long_branch` means do not implement the carried-long shadow candidate.",
            "- The frozen reversal patch remains unchanged.",
            "- This report does not activate or promote any path.",
        ]
    )
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
    prices = df_full["close"].values.astype(float)
    times = _datetimes(df_full)
    regimes = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    checkpoint = _load_baseline_params()
    baseline_signals = _generate_v21_signals(
        checkpoint,
        df_full,
        fast_days=fast_days,
        slow_days=slow_days,
    )

    reversal_result = apply_n2b_1d_block_reversals_only(baseline_signals, regimes)
    reversal_signals = reversal_result.signals
    carried_signals = _apply_carried_long_shadow(baseline_signals, regimes)
    stacked_signals = _apply_carried_long_shadow(reversal_signals, regimes)

    reversal_events = _reversal_events(baseline_signals, reversal_signals, regimes, times)
    carried_events = _carried_events(baseline_signals, carried_signals, regimes, times)
    reversal_steps: Set[int] = {int(event["bar_index"]) for event in reversal_events}
    carried_steps: Set[int] = {int(event["bar_index"]) for event in carried_events}
    overlap_count = len(reversal_steps & carried_steps)

    records = {
        "baseline_v2_1_balanced": _record(
            "baseline_v2_1_balanced",
            baseline_signals,
            prices,
            df_full,
            regimes,
            times,
            split_idx,
        ),
        "reversal_patch_only": _record(
            "reversal_patch_only",
            reversal_signals,
            prices,
            df_full,
            regimes,
            times,
            split_idx,
        ),
        "carried_long_shadow_only": _record(
            "carried_long_shadow_only",
            carried_signals,
            prices,
            df_full,
            regimes,
            times,
            split_idx,
        ),
        "reversal_patch_plus_carried_long_shadow": _record(
            "reversal_patch_plus_carried_long_shadow",
            stacked_signals,
            prices,
            df_full,
            regimes,
            times,
            split_idx,
        ),
    }
    conclusion = _make_conclusion(
        records,
        reversal_count=len(reversal_events),
        carried_count=len(carried_events),
        overlap_count=overlap_count,
    )

    return {
        "generated_at": _now_iso(),
        "check_id": "N2B_reversal_patch_plus_carried_tail_shadow_interaction_v0",
        "scope": "diagnostic_interaction_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "event_counts": {
            "reversal_patch_only": len(reversal_events),
            "carried_long_shadow_only": len(carried_events),
            "overlap": overlap_count,
            "stacked_unique": conclusion["total_unique_events"],
        },
        "events": reversal_events + carried_events,
        "records": records,
        "summary_rows": _summary_rows(records, "baseline_v2_1_balanced"),
        "conclusion": conclusion,
        "non_activation_guards": {
            "helper_changed": False,
            "activation_changed": False,
            "default_oracle_changed": False,
            "live_demo_changed": False,
            "checkpoint_changed": False,
            "family_registry_changed": False,
            "llm_search_changed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interaction check for frozen reversal patch and carried-long shadow."
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
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_summary_tsv(SUMMARY_TSV, report["summary_rows"])
    _write_events_tsv(EVENTS_TSV, report["events"])
    _write_notes(NOTES_MD, report)

    print("N2B reversal/carried interaction check complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  JSON:  {REPORT_JSON}")
    print(f"  TSV:   {SUMMARY_TSV}")
    print(f"  Events: {EVENTS_TSV}")
    print(f"  Notes: {NOTES_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
