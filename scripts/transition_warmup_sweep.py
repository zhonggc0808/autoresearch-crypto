#!/usr/bin/env python3
"""Manual diagnostic sweep for the neutral_to_bear_warmup contract.

This is not a registered family and not part of the LLM search loop. The current
pass applies two narrow reversal-only diagnostics on top of the frozen v2.1
baseline:

    - N2B_1d_block_reversals_only
    - N2B_3d_block_reversals_only

Only direct long <-> short flips are blocked during the NEUTRAL -> BEAR warmup
and converted to flat. New entries from flat and existing position holds are
left unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
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
    _compute_fee_sensitivity,
    _compute_rolling_metrics,
    _evaluate_signals,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
    _safe_execution_signals,
)
from scripts.transition_attribution_audit import (
    _datetimes,
    _transition_events,
    _window_return_and_dd,
)

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "transition_warmup"
REPORT_JSON = OUTPUT_DIR / "transition_warmup_report.json"
SUMMARY_TSV = OUTPUT_DIR / "transition_warmup_summary.tsv"
NOTES_MD = OUTPUT_DIR / "transition_warmup_notes.md"

NEUTRAL_BLOCK_ALL_BLOCKED_COUNT = 77_593
MAX_BLOCKED_COUNT = 7_759
OOS_SAFE_FLOOR_MULTIPLIER = 0.90
N2B_LONG_HORIZON_TOLERANCE = 0.01


@dataclass(frozen=True)
class CandidateConfig:
    candidate_id: str
    warmup_days: int
    mechanism: str

    @property
    def warmup_bars(self) -> int:
        return self.warmup_days * BARS_PER_DAY_5M


CANDIDATES = (
    CandidateConfig("N2B_1d_block_reversals_only", 1, "block_reversals_to_flat_only"),
    CandidateConfig("N2B_3d_block_reversals_only", 3, "block_reversals_to_flat_only"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _position_after_signal(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def _warmup_mask(regimes: np.ndarray, warmup_bars: int) -> np.ndarray:
    mask = np.zeros(len(regimes), dtype=bool)
    labels = np.asarray(regimes, dtype=object)
    for i in range(1, len(labels)):
        if str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR":
            end = min(len(mask), i + warmup_bars)
            mask[i:end] = True
    return mask


def _apply_block_new_entries(
    signals: np.ndarray,
    regimes: np.ndarray,
    warmup_bars: int,
) -> tuple[np.ndarray, Dict[str, Any]]:
    """Block new entries from flat during NEUTRAL -> BEAR warmup windows.

    Existing positions are not touched. If a signal reverses an existing
    position, it is left unchanged; reversal-specific logic belongs to a later
    contract if this first diagnostic fails.
    """
    out = np.asarray(signals, dtype=int).copy()
    labels = np.asarray(regimes, dtype=object)
    position = 0
    warmup_until = -1

    diag = {
        "transition_count": 0,
        "warmup_bars_marked": 0,
        "blocked_actions_total": 0,
        "blocked_entries_total": 0,
        "blocked_long_entries": 0,
        "blocked_short_entries": 0,
        "blocked_reversals_total": 0,
        "blocked_long_to_short_reversals": 0,
        "blocked_short_to_long_reversals": 0,
        "signals_changed_total": 0,
    }

    for i in range(len(out)):
        if i > 0 and str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR":
            diag["transition_count"] += 1
            warmup_until = max(warmup_until, i + warmup_bars)

        raw = int(out[i])
        sig = raw
        in_warmup = i < warmup_until
        if in_warmup:
            diag["warmup_bars_marked"] += 1
            if position == 0 and sig in (2, 3):
                sig = 1
                diag["blocked_actions_total"] += 1
                diag["blocked_entries_total"] += 1
                if raw == 2:
                    diag["blocked_long_entries"] += 1
                else:
                    diag["blocked_short_entries"] += 1

        if sig != raw:
            diag["signals_changed_total"] += 1
        out[i] = sig
        position = _position_after_signal(sig, position)

    return out, diag


def _apply_block_reversals(
    signals: np.ndarray,
    regimes: np.ndarray,
    warmup_bars: int,
) -> tuple[np.ndarray, Dict[str, Any]]:
    """Block only direct long <-> short flips during NEUTRAL -> BEAR warmup.

    Reversal signals are converted to flat. This tests whether the immediate
    opposite-side confirmation is harmful without turning the rejected reversal
    into a forced carried position.
    """
    result = apply_n2b_1d_block_reversals_only(
        signals,
        regimes,
        warmup_bars=warmup_bars,
    )
    diag = {
        "blocked_long_entries": 0,
        "blocked_short_entries": 0,
        **result.diagnostics,
    }
    return result.signals, diag


def _simulate_metrics(
    signals: np.ndarray,
    prices: np.ndarray,
    df_full: pd.DataFrame,
    regimes: np.ndarray,
    split_idx: int,
) -> Dict[str, Any]:
    prices_is = prices[:split_idx]
    prices_oos = prices[split_idx:]
    signals_is = signals[:split_idx]
    signals_oos = signals[split_idx:]
    safe_full = _safe_execution_signals(signals)
    safe_is = safe_full[:split_idx]
    safe_oos = safe_full[split_idx:]

    return {
        "is": {
            "raw": _evaluate_signals(signals_is, prices_is),
            "safe_execution": _evaluate_signals(safe_is, prices_is),
        },
        "oos": {
            "raw": _evaluate_signals(signals_oos, prices_oos),
            "safe_execution": _evaluate_signals(safe_oos, prices_oos),
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
    horizon_bars = horizon_days * BARS_PER_DAY_5M
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, _ = evaluator.simulate(signals, prices)
    events = [
        event for event in _transition_events(regimes, times)
        if event.transition == "NEUTRAL->BEAR"
    ]

    returns: List[float] = []
    dds: List[float] = []
    samples = []
    for event in events:
        start = event.step
        end = min(len(signals), start + horizon_bars)
        if start >= end:
            continue
        ret, dd, equity_delta = _window_return_and_dd(equity, start, end)
        returns.append(ret)
        dds.append(dd)
        samples.append(
            {
                "transition_time": event.time,
                "start_bar": start,
                "end_bar": end,
                "return": round(float(ret), 6),
                "dd": round(float(dd), 6),
                "equity_delta": round(float(equity_delta), 2),
            }
        )

    samples.sort(key=lambda row: row["return"])
    return {
        "horizon": f"{horizon_days}d",
        "transition_count": len(events),
        "mean_return": round(float(mean(returns)), 6) if returns else None,
        "min_return": round(float(min(returns)), 6) if returns else None,
        "worst_dd": round(float(min(dds)), 6) if dds else None,
        "worst_samples": samples[:5],
    }


def _acceptance_gates(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    diag: Dict[str, Any],
    n2b_stats: Dict[str, Dict[str, Any]],
    baseline_n2b_stats: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    baseline_oos_safe = baseline["metrics"]["oos"]["safe_execution"]["return"]
    candidate_oos_safe = candidate["metrics"]["oos"]["safe_execution"]["return"]
    baseline_r12 = baseline["metrics"]["rolling"]["12m_min_return"]
    candidate_r12 = candidate["metrics"]["rolling"]["12m_min_return"]

    baseline_n2b_7d = baseline_n2b_stats["7d"]["mean_return"]
    baseline_n2b_14d = baseline_n2b_stats["14d"]["mean_return"]
    candidate_n2b_7d = n2b_stats["7d"]["mean_return"]
    candidate_n2b_14d = n2b_stats["14d"]["mean_return"]

    gates = {
        "rolling12_not_worse": {
            "pass": candidate_r12 is not None and candidate_r12 >= baseline_r12,
            "candidate": candidate_r12,
            "baseline": baseline_r12,
            "required": "candidate >= baseline",
        },
        "oos_safe_not_materially_sacrificed": {
            "pass": candidate_oos_safe >= baseline_oos_safe * OOS_SAFE_FLOOR_MULTIPLIER,
            "candidate": round(float(candidate_oos_safe), 6),
            "baseline": round(float(baseline_oos_safe), 6),
            "required": f">= {OOS_SAFE_FLOOR_MULTIPLIER:.0%} of baseline",
        },
        "blocked_count_far_below_neutral_block_all": {
            "pass": diag["blocked_actions_total"] < MAX_BLOCKED_COUNT,
            "candidate": diag["blocked_actions_total"],
            "baseline_failure_count": NEUTRAL_BLOCK_ALL_BLOCKED_COUNT,
            "required": f"< {MAX_BLOCKED_COUNT}",
        },
        "n2b_7d_not_damaged": {
            "pass": (
                candidate_n2b_7d is not None
                and baseline_n2b_7d is not None
                and candidate_n2b_7d >= baseline_n2b_7d - N2B_LONG_HORIZON_TOLERANCE
            ),
            "candidate": candidate_n2b_7d,
            "baseline": baseline_n2b_7d,
            "required": f">= baseline - {N2B_LONG_HORIZON_TOLERANCE}",
        },
        "n2b_14d_not_damaged": {
            "pass": (
                candidate_n2b_14d is not None
                and baseline_n2b_14d is not None
                and candidate_n2b_14d >= baseline_n2b_14d - N2B_LONG_HORIZON_TOLERANCE
            ),
            "candidate": candidate_n2b_14d,
            "baseline": baseline_n2b_14d,
            "required": f">= baseline - {N2B_LONG_HORIZON_TOLERANCE}",
        },
    }
    return {
        "all_pass": all(gate["pass"] for gate in gates.values()),
        "gates_passed": sum(1 for gate in gates.values() if gate["pass"]),
        "gates_total": len(gates),
        "gates": gates,
    }


def _metric_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    metrics = record["metrics"]
    return {
        "is_return": metrics["is"]["raw"]["return"],
        "is_dd": metrics["is"]["raw"]["dd"],
        "oos_return": metrics["oos"]["raw"]["return"],
        "oos_dd": metrics["oos"]["raw"]["dd"],
        "oos_safe_return": metrics["oos"]["safe_execution"]["return"],
        "oos_safe_dd": metrics["oos"]["safe_execution"]["dd"],
        "rolling_6m_min_return": metrics["rolling"]["6m_min_return"],
        "rolling_12m_min_return": metrics["rolling"]["12m_min_return"],
        "is_fee_10bp": metrics["sensitivity"]["is"]["fees"]["10bp"],
        "oos_fee_10bp": metrics["sensitivity"]["oos"]["fees"]["10bp"],
    }


def _write_tsv(path: Path, records: List[Dict[str, Any]]) -> None:
    fields = [
        "candidate_id",
        "warmup_days",
        "mechanism",
        "blocked_actions_total",
        "blocked_entries_total",
        "blocked_long_entries",
        "blocked_short_entries",
        "blocked_reversals_total",
        "blocked_long_to_short_reversals",
        "blocked_short_to_long_reversals",
        "signals_changed_total",
        "oos_safe_return",
        "oos_safe_delta",
        "rolling_12m_min_return",
        "rolling_12m_delta",
        "rolling_6m_min_return",
        "is_dd",
        "oos_dd",
        "is_fee_10bp",
        "oos_fee_10bp",
        "n2b_1d_mean_return",
        "n2b_3d_mean_return",
        "n2b_7d_mean_return",
        "n2b_14d_mean_return",
        "gates_passed",
        "gates_total",
        "all_pass",
    ]
    baseline = records[0]
    baseline_summary = _metric_summary(baseline)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for record in records:
            summary = _metric_summary(record)
            gates = record.get("acceptance_gates") or {}
            diag = record.get("diagnostics", {})
            n2b = record["n2b_windows"]
            writer.writerow(
                {
                    "candidate_id": record["candidate_id"],
                    "warmup_days": record.get("warmup_days", 0),
                    "mechanism": record.get("mechanism", "baseline"),
                    "blocked_actions_total": diag.get("blocked_actions_total", 0),
                    "blocked_entries_total": diag.get("blocked_entries_total", 0),
                    "blocked_long_entries": diag.get("blocked_long_entries", 0),
                    "blocked_short_entries": diag.get("blocked_short_entries", 0),
                    "blocked_reversals_total": diag.get("blocked_reversals_total", 0),
                    "blocked_long_to_short_reversals": diag.get(
                        "blocked_long_to_short_reversals", 0
                    ),
                    "blocked_short_to_long_reversals": diag.get(
                        "blocked_short_to_long_reversals", 0
                    ),
                    "signals_changed_total": diag.get("signals_changed_total", 0),
                    "oos_safe_return": summary["oos_safe_return"],
                    "oos_safe_delta": round(
                        float(summary["oos_safe_return"] - baseline_summary["oos_safe_return"]), 6
                    ),
                    "rolling_12m_min_return": summary["rolling_12m_min_return"],
                    "rolling_12m_delta": round(
                        float(
                            summary["rolling_12m_min_return"]
                            - baseline_summary["rolling_12m_min_return"]
                        ),
                        6,
                    ),
                    "rolling_6m_min_return": summary["rolling_6m_min_return"],
                    "is_dd": summary["is_dd"],
                    "oos_dd": summary["oos_dd"],
                    "is_fee_10bp": summary["is_fee_10bp"],
                    "oos_fee_10bp": summary["oos_fee_10bp"],
                    "n2b_1d_mean_return": n2b["1d"]["mean_return"],
                    "n2b_3d_mean_return": n2b["3d"]["mean_return"],
                    "n2b_7d_mean_return": n2b["7d"]["mean_return"],
                    "n2b_14d_mean_return": n2b["14d"]["mean_return"],
                    "gates_passed": gates.get("gates_passed", ""),
                    "gates_total": gates.get("gates_total", ""),
                    "all_pass": gates.get("all_pass", ""),
                }
            )


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    baseline = report["records"][0]
    candidates = report["records"][1:]

    lines = [
        "# Neutral -> Bear Warmup Sweep",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Manual diagnostic sweep for `regime_transition_logic / neutral_to_bear_warmup`.",
        "- Only `NEUTRAL->BEAR` transitions are touched.",
        "- Only 1d and 3d warmup windows are tested.",
        "- Reversal-only candidates only block direct long <-> short flips and convert the blocked flip to flat.",
        "- New entries from flat and existing position holds are unchanged.",
        "- No baseline, checkpoint, live/demo, oracle scoring, family registry, or LLM search changes.",
        "",
        "## Baseline",
        "",
    ]
    bs = _metric_summary(baseline)
    lines.extend(
        [
            f"- OOS safe return: {bs['oos_safe_return']:+.2%}",
            f"- Rolling12m min return: {bs['rolling_12m_min_return']:+.2%}",
            f"- N2B 7d mean return: {baseline['n2b_windows']['7d']['mean_return']:+.2%}",
            f"- N2B 14d mean return: {baseline['n2b_windows']['14d']['mean_return']:+.2%}",
            "",
            "## Candidates",
            "",
            "| candidate | mechanism | blocked | OOS safe | rolling12m | N2B 1d | N2B 3d | N2B 7d | N2B 14d | gates |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for record in candidates:
        s = _metric_summary(record)
        n2b = record["n2b_windows"]
        gates = record["acceptance_gates"]
        lines.append(
            f"| {record['candidate_id']} | {record['mechanism']} | "
            f"{record['diagnostics']['blocked_actions_total']} | "
            f"{s['oos_safe_return']:+.2%} | {s['rolling_12m_min_return']:+.2%} | "
            f"{n2b['1d']['mean_return']:+.2%} | {n2b['3d']['mean_return']:+.2%} | "
            f"{n2b['7d']['mean_return']:+.2%} | {n2b['14d']['mean_return']:+.2%} | "
            f"{gates['gates_passed']}/{gates['gates_total']} |"
        )

    lines.extend(["", "## Interpretation", ""])
    if any(record["acceptance_gates"]["all_pass"] for record in candidates):
        winners = [
            record["candidate_id"]
            for record in candidates
            if record["acceptance_gates"]["all_pass"]
        ]
        lines.append(
            "At least one minimal warmup diagnostic passed all gates: "
            + ", ".join(winners)
            + ". Write a stricter implementation contract before registering any family."
        )
        reversal_records = [
            record for record in candidates
            if record.get("mechanism") == "block_reversals_to_flat_only"
        ]
        if reversal_records:
            best = min(
                reversal_records,
                key=lambda record: (
                    record["diagnostics"]["blocked_actions_total"],
                    -record["n2b_windows"]["3d"]["mean_return"],
                ),
            )
            lines.extend(
                [
                    "",
                    "Reversal-only readout:",
                    "",
                    f"- Baseline N2B 1d/3d mean returns: "
                    f"{baseline['n2b_windows']['1d']['mean_return']:+.2%} / "
                    f"{baseline['n2b_windows']['3d']['mean_return']:+.2%}.",
                    f"- Best narrow candidate by blocked count: {best['candidate_id']} "
                    f"with {best['diagnostics']['blocked_actions_total']} blocked reversals.",
                    f"- Candidate N2B 1d/3d mean returns: "
                    f"{best['n2b_windows']['1d']['mean_return']:+.2%} / "
                    f"{best['n2b_windows']['3d']['mean_return']:+.2%}.",
                    "- This supports reversal as a likely local contributor, but OOS safe is unchanged rather than improved.",
                    "- Because 1d and 3d are nearly identical, prefer the 1d variant if this becomes an implementation contract.",
                ]
            )
    else:
        lines.append(
            "No minimal block-new-entries diagnostic passed all gates. Do not tune duration blindly; "
            "next attribution should isolate direct reversals versus carried positions."
        )

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_sweep(
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

    records: List[Dict[str, Any]] = []
    baseline_record = {
        "candidate_id": "baseline_v2_1_balanced",
        "warmup_days": 0,
        "diagnostics": {
            "transition_count": len(
                [
                    event for event in _transition_events(regimes, times)
                    if event.transition == "NEUTRAL->BEAR"
                ]
            ),
            "warmup_bars_marked": 0,
            "blocked_actions_total": 0,
            "blocked_entries_total": 0,
            "blocked_long_entries": 0,
            "blocked_short_entries": 0,
            "blocked_reversals_total": 0,
            "blocked_long_to_short_reversals": 0,
            "blocked_short_to_long_reversals": 0,
            "signals_changed_total": 0,
        },
        "metrics": _simulate_metrics(baseline_signals, prices, df_full, regimes, split_idx),
        "n2b_windows": {
            f"{days}d": _n2b_window_stats(baseline_signals, prices, regimes, times, days)
            for days in (1, 3, 7, 14)
        },
        "acceptance_gates": None,
    }
    records.append(baseline_record)

    for config in CANDIDATES:
        if config.mechanism == "block_new_entries_from_flat_only":
            candidate_signals, diag = _apply_block_new_entries(
                baseline_signals,
                regimes,
                config.warmup_bars,
            )
        elif config.mechanism == "block_reversals_to_flat_only":
            candidate_signals, diag = _apply_block_reversals(
                baseline_signals,
                regimes,
                config.warmup_bars,
            )
        else:
            raise ValueError(f"Unknown mechanism: {config.mechanism}")

        record = {
            "candidate_id": config.candidate_id,
            "warmup_days": config.warmup_days,
            "mechanism": config.mechanism,
            "diagnostics": diag,
            "metrics": _simulate_metrics(candidate_signals, prices, df_full, regimes, split_idx),
            "n2b_windows": {
                f"{days}d": _n2b_window_stats(candidate_signals, prices, regimes, times, days)
                for days in (1, 3, 7, 14)
            },
        }
        record["acceptance_gates"] = _acceptance_gates(
            baseline_record,
            record,
            diag,
            record["n2b_windows"],
            baseline_record["n2b_windows"],
        )
        records.append(record)

    report = {
        "generated_at": _now_iso(),
        "sweep_version": "neutral_to_bear_warmup_v0.1",
        "scope": "manual_diagnostic_only_no_family_no_llm_search",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "acceptance_thresholds": {
            "oos_safe_floor_multiplier": OOS_SAFE_FLOOR_MULTIPLIER,
            "max_blocked_count": MAX_BLOCKED_COUNT,
            "neutral_block_all_blocked_count": NEUTRAL_BLOCK_ALL_BLOCKED_COUNT,
            "n2b_7d_14d_tolerance": N2B_LONG_HORIZON_TOLERANCE,
        },
        "records": records,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual diagnostic sweep for NEUTRAL->BEAR warmup candidates."
    )
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    args = parser.parse_args()

    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_sweep(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_tsv(SUMMARY_TSV, report["records"])
    _write_notes(NOTES_MD, report)

    print("Neutral -> Bear warmup sweep complete.")
    print(f"  JSON:  {REPORT_JSON}")
    print(f"  TSV:   {SUMMARY_TSV}")
    print(f"  Notes: {NOTES_MD}")
    for record in report["records"][1:]:
        gates = record["acceptance_gates"]
        print(
            f"  {record['candidate_id']}: gates {gates['gates_passed']}/{gates['gates_total']} "
            f"({'PASS' if gates['all_pass'] else 'FAIL'}), "
            f"blocked={record['diagnostics']['blocked_actions_total']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
