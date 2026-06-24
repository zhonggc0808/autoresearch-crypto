#!/usr/bin/env python3
"""Shadow replay for flatten_carried_long_on_N2B_1d_shadow.

Diagnostic only: this script does not implement a helper, does not activate any
path, and does not modify baseline/default-oracle/live/demo behavior.
"""

from __future__ import annotations

import argparse
import csv
import json
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

from dex.config import BARS_PER_DAY_5M
from dex.regime_filter import build_daily_regime_labels
from scripts.n2b_carried_position_exit_tail_diagnosis import (
    _position_state,
    _round_float,
)
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

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "n2b_carried_tail_exit_shadow"
REPORT_JSON = OUTPUT_DIR / "n2b_carried_tail_exit_shadow_report.json"
EVENTS_TSV = OUTPUT_DIR / "n2b_carried_tail_exit_shadow_events.tsv"
SUMMARY_TSV = OUTPUT_DIR / "n2b_carried_tail_exit_shadow_summary.tsv"
NOTES_MD = OUTPUT_DIR / "n2b_carried_tail_exit_shadow_notes.md"

CANDIDATE_ID = "flatten_carried_long_on_N2B_1d_shadow"
REASON = "n2b_inherited_long_flatten_shadow"
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


def _position_after_signal(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


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

    horizon_bars = horizon_days * BARS_PER_DAY_5M
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, _ = evaluator.simulate(signals, prices)
    returns = []
    dds = []
    samples = []
    for transition_idx, step in enumerate(_n2b_steps(regimes), start=1):
        end = min(len(signals), step + horizon_bars)
        ret, dd, equity_delta = _window_return_and_dd(equity, step, end)
        returns.append(ret)
        dds.append(dd)
        samples.append(
            {
                "transition_id": f"n2b_{transition_idx:04d}",
                "transition_time": str(times[step]),
                "return": _round_float(ret),
                "dd": _round_float(dd),
                "equity_delta": _round_float(equity_delta, 2),
            }
        )
    return {
        "horizon": f"{horizon_days}d",
        "transition_count": len(returns),
        "mean_return": _round_float(mean(returns) if returns else None),
        "min_return": _round_float(min(returns) if returns else None),
        "worst_dd": _round_float(min(dds) if dds else None),
        "samples": samples,
    }


def _apply_shadow_flatten(
    signals: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    baseline_equity: np.ndarray,
    shadow_equity: np.ndarray,
) -> tuple[np.ndarray, Dict[str, Any], List[Dict[str, Any]]]:
    shadow = np.asarray(signals, dtype=int).copy()
    state = _position_state(signals)
    events: List[Dict[str, Any]] = []
    flat_entries_affected = 0
    inherited_shorts_affected = 0
    direct_reversals_affected = 0
    skipped_direct_reversals = 0

    for transition_idx, step in enumerate(_n2b_steps(regimes), start=1):
        pre_position = int(state.positions[step - 1])
        post_position = int(state.positions[step])
        raw_signal = int(signals[step])
        inherited_entry_step = int(state.entry_steps[step - 1])

        if pre_position == 0:
            flat_entries_affected += 0
            continue
        if pre_position == -1:
            inherited_shorts_affected += 0
            continue
        if pre_position == 1 and post_position != 1:
            skipped_direct_reversals += 1
            continue
        if pre_position != 1 or post_position != 1:
            continue

        shadow[step] = 0
        baseline_1d_return, _, _ = _window_return_and_dd(
            baseline_equity,
            step,
            min(len(signals), step + BARS_PER_DAY_5M),
        )
        shadow_1d_return, _, _ = _window_return_and_dd(
            shadow_equity,
            step,
            min(len(signals), step + BARS_PER_DAY_5M),
        )
        baseline_3d_return, _, _ = _window_return_and_dd(
            baseline_equity,
            step,
            min(len(signals), step + 3 * BARS_PER_DAY_5M),
        )
        shadow_3d_return, _, _ = _window_return_and_dd(
            shadow_equity,
            step,
            min(len(signals), step + 3 * BARS_PER_DAY_5M),
        )
        events.append(
            {
                "transition_id": f"n2b_{transition_idx:04d}",
                "transition_step": step,
                "transition_time": str(times[step]),
                "pre_position": POSITION_NAMES[pre_position],
                "post_position_baseline": POSITION_NAMES[post_position],
                "raw_signal_at_transition": raw_signal,
                "raw_signal_name": SIGNAL_NAMES.get(raw_signal, str(raw_signal)),
                "shadow_signal_at_transition": 0,
                "shadow_signal_name": "flat",
                "inherited_entry_step": inherited_entry_step,
                "inherited_entry_time": str(times[inherited_entry_step]),
                "entry_age_days": _round_float((step - inherited_entry_step) / BARS_PER_DAY_5M, 3),
                "reason": REASON,
                "primary_window": "1d",
                "baseline_1d_return": _round_float(baseline_1d_return),
                "shadow_1d_return": _round_float(shadow_1d_return),
                "baseline_3d_return": _round_float(baseline_3d_return),
                "shadow_3d_return": _round_float(shadow_3d_return),
            }
        )

    diagnostics = {
        "candidate_id": CANDIDATE_ID,
        "would_flatten_total": len(events),
        "flat_entries_affected": flat_entries_affected,
        "inherited_shorts_affected": inherited_shorts_affected,
        "direct_reversals_affected": direct_reversals_affected,
        "skipped_direct_reversals": skipped_direct_reversals,
        "production_signals_changed_total": 0,
        "shadow_signals_changed_total": int((shadow != signals).sum()),
    }
    return shadow, diagnostics, events


def _acceptance_readout(
    baseline: Dict[str, Any],
    shadow: Dict[str, Any],
    diagnostics: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    baseline_1d = baseline["n2b_windows"]["1d"]["mean_return"]
    shadow_1d = shadow["n2b_windows"]["1d"]["mean_return"]
    baseline_3d = baseline["n2b_windows"]["3d"]["mean_return"]
    shadow_3d = shadow["n2b_windows"]["3d"]["mean_return"]
    baseline_oos_safe = baseline["metrics"]["oos"]["safe_execution"]["return"]
    shadow_oos_safe = shadow["metrics"]["oos"]["safe_execution"]["return"]
    baseline_r12 = baseline["metrics"]["rolling"]["12m_min_return"]
    shadow_r12 = shadow["metrics"]["rolling"]["12m_min_return"]

    gates = {
        "would_flatten_count_is_2": diagnostics["would_flatten_total"] == 2,
        "all_events_are_inherited_long": all(
            event["pre_position"] == "long" and event["post_position_baseline"] == "long"
            for event in events
        ),
        "flat_entries_affected_zero": diagnostics["flat_entries_affected"] == 0,
        "inherited_shorts_affected_zero": diagnostics["inherited_shorts_affected"] == 0,
        "direct_reversals_affected_zero": diagnostics["direct_reversals_affected"] == 0,
        "n2b_1d_improves": shadow_1d is not None and baseline_1d is not None and shadow_1d > baseline_1d,
        "n2b_3d_not_materially_worse": (
            shadow_3d is not None and baseline_3d is not None and shadow_3d >= baseline_3d - 0.005
        ),
        "oos_safe_not_materially_degraded": shadow_oos_safe >= baseline_oos_safe * 0.98,
        "rolling12_not_worse": shadow_r12 is not None and shadow_r12 >= baseline_r12,
        "production_signals_unchanged": diagnostics["production_signals_changed_total"] == 0,
    }
    if all(gates.values()):
        conclusion = "carried_long_tail_promising"
    elif diagnostics["would_flatten_total"] != 2 or not gates["all_events_are_inherited_long"]:
        conclusion = "inconclusive"
    else:
        conclusion = "diffuse_or_noisy"
    return {
        "conclusion": conclusion,
        "allowed_conclusions": [
            "carried_long_tail_promising",
            "diffuse_or_noisy",
            "inconclusive",
        ],
        "gates": gates,
        "gates_passed": sum(1 for passed in gates.values() if passed),
        "gates_total": len(gates),
        "deltas": {
            "n2b_1d_mean_return": _round_float(shadow_1d - baseline_1d),
            "n2b_3d_mean_return": _round_float(shadow_3d - baseline_3d),
            "oos_safe_return": _round_float(shadow_oos_safe - baseline_oos_safe),
            "rolling12_min_return": _round_float(shadow_r12 - baseline_r12),
        },
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


def _write_events_tsv(path: Path, events: List[Dict[str, Any]]) -> None:
    fields = [
        "transition_id",
        "transition_step",
        "transition_time",
        "pre_position",
        "post_position_baseline",
        "raw_signal_at_transition",
        "raw_signal_name",
        "shadow_signal_at_transition",
        "shadow_signal_name",
        "inherited_entry_step",
        "inherited_entry_time",
        "entry_age_days",
        "reason",
        "primary_window",
        "baseline_1d_return",
        "shadow_1d_return",
        "baseline_3d_return",
        "shadow_3d_return",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(events)


def _write_summary_tsv(path: Path, report: Dict[str, Any]) -> None:
    rows = []
    baseline = report["baseline"]
    shadow = report["shadow"]
    for horizon in ("1d", "3d", "7d", "14d"):
        rows.append(
            {
                "metric": f"n2b_{horizon}_mean_return",
                "baseline": baseline["n2b_windows"][horizon]["mean_return"],
                "shadow": shadow["n2b_windows"][horizon]["mean_return"],
                "delta": _round_float(
                    shadow["n2b_windows"][horizon]["mean_return"]
                    - baseline["n2b_windows"][horizon]["mean_return"]
                ),
            }
        )
    rows.extend(
        [
            {
                "metric": "oos_safe_return",
                "baseline": baseline["metrics"]["oos"]["safe_execution"]["return"],
                "shadow": shadow["metrics"]["oos"]["safe_execution"]["return"],
                "delta": report["readout"]["deltas"]["oos_safe_return"],
            },
            {
                "metric": "rolling12_min_return",
                "baseline": baseline["metrics"]["rolling"]["12m_min_return"],
                "shadow": shadow["metrics"]["rolling"]["12m_min_return"],
                "delta": report["readout"]["deltas"]["rolling12_min_return"],
            },
        ]
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["metric", "baseline", "shadow", "delta"], delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    baseline = report["baseline"]
    shadow = report["shadow"]
    readout = report["readout"]
    diag = report["diagnostics"]
    lines = [
        "# N2B Carried Long Exit Shadow Replay",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Shadow diagnosis only. No helper, activation, safe-exec/default-oracle/live/demo/checkpoint/family/LLM path changed.",
        "- Candidate: `flatten_carried_long_on_N2B_1d_shadow`.",
        "- Target: inherited long carried through NEUTRAL -> BEAR.",
        "- Primary readout: 1d. Reference: 3d.",
        "",
        "## Conclusion",
        "",
        f"**{readout['conclusion']}**",
        "",
        f"- Gates: {readout['gates_passed']}/{readout['gates_total']}.",
        f"- Would-flatten count: {diag['would_flatten_total']}.",
        f"- Flat entries affected: {diag['flat_entries_affected']}.",
        f"- Inherited shorts affected: {diag['inherited_shorts_affected']}.",
        f"- Direct reversals affected: {diag['direct_reversals_affected']}.",
        f"- Production signals changed: {diag['production_signals_changed_total']}.",
        "",
        "## Readout",
        "",
        "| metric | baseline | shadow | delta |",
        "|---|---:|---:|---:|",
    ]
    for horizon in ("1d", "3d", "7d", "14d"):
        b = baseline["n2b_windows"][horizon]["mean_return"]
        s = shadow["n2b_windows"][horizon]["mean_return"]
        lines.append(f"| N2B {horizon} mean | {b:+.2%} | {s:+.2%} | {s - b:+.2%} |")
    b_oos = baseline["metrics"]["oos"]["safe_execution"]["return"]
    s_oos = shadow["metrics"]["oos"]["safe_execution"]["return"]
    b_r12 = baseline["metrics"]["rolling"]["12m_min_return"]
    s_r12 = shadow["metrics"]["rolling"]["12m_min_return"]
    lines.extend(
        [
            f"| OOS safe return | {b_oos:+.2%} | {s_oos:+.2%} | {s_oos - b_oos:+.2%} |",
            f"| Rolling12m min | {b_r12:+.2%} | {s_r12:+.2%} | {s_r12 - b_r12:+.2%} |",
            "",
            "## Boundary",
            "",
            "- Passing this shadow readout is not implementation approval.",
            "- Next required step before any patch: interaction check with the frozen reversal patch.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_shadow(
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

    from dex.config import COMMISSION, SLIPPAGE
    from dex.strategies.base import StrategyEvaluator

    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    baseline_equity, _ = ev.simulate(baseline_signals, prices)
    shadow_seed = baseline_signals.copy()
    shadow_signals, diagnostics, _ = _apply_shadow_flatten(
        shadow_seed,
        regimes,
        times,
        baseline_equity,
        baseline_equity,
    )
    shadow_equity, _ = ev.simulate(shadow_signals, prices)
    shadow_signals, diagnostics, events = _apply_shadow_flatten(
        baseline_signals,
        regimes,
        times,
        baseline_equity,
        shadow_equity,
    )

    baseline_record = _record(
        "baseline_v2_1_balanced",
        baseline_signals,
        prices,
        df_full,
        regimes,
        times,
        split_idx,
    )
    shadow_record = _record(
        CANDIDATE_ID,
        shadow_signals,
        prices,
        df_full,
        regimes,
        times,
        split_idx,
    )
    readout = _acceptance_readout(baseline_record, shadow_record, diagnostics, events)

    return {
        "generated_at": _now_iso(),
        "shadow_id": "N2B_carried_tail_exit_shadow_v0",
        "candidate_id": CANDIDATE_ID,
        "scope": "diagnostic_shadow_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "diagnostics": diagnostics,
        "events": events,
        "baseline": baseline_record,
        "shadow": shadow_record,
        "readout": readout,
        "non_activation_guards": {
            "helper_added": False,
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
        description="Shadow replay for flatten_carried_long_on_N2B_1d_shadow."
    )
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    args = parser.parse_args()

    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_shadow(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_events_tsv(EVENTS_TSV, report["events"])
    _write_summary_tsv(SUMMARY_TSV, report)
    _write_notes(NOTES_MD, report)

    print("N2B carried-long exit shadow replay complete.")
    print(f"  Conclusion: {report['readout']['conclusion']}")
    print(f"  Would-flatten: {report['diagnostics']['would_flatten_total']}")
    print(f"  JSON:  {REPORT_JSON}")
    print(f"  Events: {EVENTS_TSV}")
    print(f"  TSV:   {SUMMARY_TSV}")
    print(f"  Notes: {NOTES_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
