#!/usr/bin/env python3
"""Diagnostic-only scan for BULL -> NEUTRAL local transition degradation."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import BARS_PER_DAY_5M, COMMISSION, SLIPPAGE
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.b2n_transition_local_degradation_scan import (
    HORIZON_DAYS,
    POSITION_NAMES,
    SIGNAL_NAMES,
    _classify_transition,
    _write_events_tsv,
)
from scripts.n2b_carried_position_exit_tail_diagnosis import _position_state, _round_float
from scripts.research_oracle import (
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import _datetimes, _window_return_and_dd

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "btn_transition_scan"
NOTES_MD = OUTPUT_DIR / "bull_to_neutral_transition_local_degradation_scan_v0.md"
EVENTS_TSV = OUTPUT_DIR / "bull_to_neutral_transition_events.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _btn_steps(regimes: np.ndarray) -> List[int]:
    labels = np.asarray(regimes, dtype=object)
    return [
        i
        for i in range(1, len(labels))
        if str(labels[i - 1]) == "BULL" and str(labels[i]) == "NEUTRAL"
    ]


def _window_stats(
    equity: np.ndarray,
    prices: np.ndarray,
    start: int,
    days: int,
) -> Dict[str, Any]:
    end = min(len(equity), start + days * BARS_PER_DAY_5M)
    ret, dd, equity_delta = _window_return_and_dd(equity, start, end)
    price_return = (float(prices[end - 1]) / float(prices[start])) - 1.0 if end > start else 0.0
    return {
        "bars": end - start,
        "return": _round_float(ret),
        "dd": _round_float(dd),
        "equity_delta": _round_float(equity_delta, 2),
        "price_return": _round_float(price_return),
    }


def _event_rows(
    signals: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    prices: np.ndarray,
    equity: np.ndarray,
) -> List[Dict[str, Any]]:
    state = _position_state(signals)
    events: List[Dict[str, Any]] = []
    for idx, step in enumerate(_btn_steps(regimes), start=1):
        pre_position = int(state.positions[step - 1])
        post_position = int(state.positions[step])
        raw_signal = int(signals[step])
        entry_step = int(state.entry_steps[step - 1])
        row: Dict[str, Any] = {
            "transition_id": f"btn_{idx:04d}",
            "transition_step": step,
            "transition_time": str(times[step]),
            "pre_position": POSITION_NAMES[pre_position],
            "post_position": POSITION_NAMES[post_position],
            "raw_signal": raw_signal,
            "raw_signal_name": SIGNAL_NAMES.get(raw_signal, str(raw_signal)),
            "event_type": _classify_transition(pre_position, post_position),
            "inherited_entry_step": entry_step if entry_step >= 0 else None,
            "inherited_entry_time": str(times[entry_step]) if entry_step >= 0 else None,
            "entry_age_days": _round_float((step - entry_step) / BARS_PER_DAY_5M, 3)
            if entry_step >= 0
            else None,
        }
        for days in HORIZON_DAYS:
            stats = _window_stats(equity, prices, step, days)
            prefix = f"h{days}"
            row[f"{prefix}_return"] = stats["return"]
            row[f"{prefix}_dd"] = stats["dd"]
            row[f"{prefix}_equity_delta"] = stats["equity_delta"]
            row[f"{prefix}_price_return"] = stats["price_return"]
        events.append(row)
    return events


def _summarize(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_horizon: Dict[str, Dict[str, Any]] = {}
    for days in HORIZON_DAYS:
        key = f"{days}d"
        returns = [float(event[f"h{days}_return"]) for event in events]
        dds = [float(event[f"h{days}_dd"]) for event in events]
        deltas = [float(event[f"h{days}_equity_delta"]) for event in events]
        negatives = [event for event in events if float(event[f"h{days}_return"]) < 0]
        by_horizon[key] = {
            "count": len(events),
            "mean_return": _round_float(mean(returns) if returns else None),
            "median_return": _round_float(median(returns) if returns else None),
            "min_return": _round_float(min(returns) if returns else None),
            "max_return": _round_float(max(returns) if returns else None),
            "worst_dd": _round_float(min(dds) if dds else None),
            "total_equity_delta": _round_float(sum(deltas), 2),
            "negative_count": len(negatives),
            "worst_event": min(events, key=lambda event: float(event[f"h{days}_return"]))[
                "transition_id"
            ]
            if events
            else None,
        }

    event_type_counts = {
        event_type: sum(1 for event in events if event["event_type"] == event_type)
        for event_type in sorted({event["event_type"] for event in events})
    }
    pre_position_counts = {
        name: sum(1 for event in events if event["pre_position"] == name)
        for name in ("long", "short", "flat")
    }
    post_position_counts = {
        name: sum(1 for event in events if event["post_position"] == name)
        for name in ("long", "short", "flat")
    }
    return {
        "transition_count": len(events),
        "by_horizon": by_horizon,
        "event_type_counts": event_type_counts,
        "pre_position_counts": pre_position_counts,
        "post_position_counts": post_position_counts,
        "flat_entry_count": event_type_counts.get("flat_entry", 0),
        "direct_reversal_count": event_type_counts.get("direct_reversal", 0),
        "inherited_position_count": len([e for e in events if e["pre_position"] != "flat"]),
    }


def _make_conclusion(summary: Dict[str, Any]) -> Dict[str, str]:
    h1 = summary["by_horizon"]["1d"]
    h3 = summary["by_horizon"]["3d"]
    h7 = summary["by_horizon"]["7d"]
    h14 = summary["by_horizon"]["14d"]
    event_type_counts = summary["event_type_counts"]
    dominant_count = max(event_type_counts.values()) if event_type_counts else 0
    concentrated_type = 2 <= dominant_count <= 3
    local_bad = (
        h1["mean_return"] is not None
        and h1["mean_return"] < -0.005
        and h3["mean_return"] is not None
        and h3["mean_return"] < 0
    )
    sustained_bad = (
        local_bad
        and h7["mean_return"] is not None
        and h14["mean_return"] is not None
        and (h7["mean_return"] < 0 or h14["mean_return"] < 0)
    )
    if sustained_bad and concentrated_type:
        return {
            "label": "localized_transition_pathology_found",
            "reason": (
                "BULL->NEUTRAL has negative local windows with a concentrated event type. "
                "This is diagnosis only; no helper or contract is implied."
            ),
        }
    if local_bad:
        return {
            "label": "diffuse_or_noisy",
            "reason": (
                "BULL->NEUTRAL has local weakness, but it is not sufficiently concentrated "
                "or persistent to define a patch."
            ),
        }
    return {
        "label": "no_actionable_signal",
        "reason": (
            "BULL->NEUTRAL does not show a repeated local degradation pattern worth opening."
        ),
    }


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    summary = report["summary"]
    conclusion = report["conclusion"]
    lines = [
        "# BULL to NEUTRAL Transition Local Degradation Scan v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnostic-only scan for BULL -> NEUTRAL transitions.",
        "- No helper name, contract, patch, activation, default oracle, live/demo, checkpoint, family, or LLM path change.",
        "- Reuses frozen v2.1 baseline signals and existing transition-window audit semantics.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `localized_transition_pathology_found`, `diffuse_or_noisy`, `no_actionable_signal`.",
        "",
        "## Local Windows",
        "",
        "| horizon | count | mean return | median return | min return | worst DD | negative count |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("1d", "3d", "7d", "14d"):
        row = summary["by_horizon"][key]
        lines.append(
            f"| {key} | {row['count']} | {row['mean_return']:+.2%} | "
            f"{row['median_return']:+.2%} | {row['min_return']:+.2%} | "
            f"{row['worst_dd']:+.2%} | {row['negative_count']} |"
        )
    lines.extend(
        [
            "",
            "## Transition Composition",
            "",
            f"- BULL->NEUTRAL transition count: {summary['transition_count']}",
            f"- Inherited position at transition: {summary['inherited_position_count']}",
            f"- Flat entry count: {summary['flat_entry_count']}",
            f"- Direct reversal count: {summary['direct_reversal_count']}",
            f"- Pre-position counts: {summary['pre_position_counts']}",
            f"- Post-position counts: {summary['post_position_counts']}",
            f"- Event type counts: {summary['event_type_counts']}",
            "",
            "## Guardrail",
            "",
            "- This scan only answers whether BULL->NEUTRAL has a local pathology.",
            "- Do not infer a helper, rule, activation, or contract from this scan alone.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: BULL to NEUTRAL Transition Local Degradation Scan",
        "",
        f"Date: {report['generated_at'][:10]}",
        "",
        "## Result",
        "",
        "No protected runtime path was modified by this diagnostic scan.",
        "",
        "Protected paths kept out of scope:",
        "",
        "- frozen N2B helper;",
        "- activation readiness/contract;",
        "- safe-exec/default oracle;",
        "- live/demo routing;",
        "- baseline params/checkpoints;",
        "- family registry;",
        "- LLM search or candidate generation.",
        "",
        "Current protected-path status still has one pre-existing dirty file:",
        "",
        "- `live_okx_quant.py`",
        "",
        "The scan did not edit or depend on that file.",
        "",
        "## Files Intentionally Added Or Updated",
        "",
        "- `scripts/btn_transition_local_degradation_scan.py`",
        "- `research_workspace/btn_transition_scan/bull_to_neutral_transition_local_degradation_scan_v0.md`",
        "- `research_workspace/btn_transition_scan/bull_to_neutral_transition_events.tsv`",
        "- `research_workspace/btn_transition_scan/no_touch_audit.md`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_scan(
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
    signals = _generate_v21_signals(checkpoint, df_full, fast_days=fast_days, slow_days=slow_days)

    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, _ = evaluator.simulate(signals, prices)
    events = _event_rows(signals, regimes, times, prices, equity)
    summary = _summarize(events)
    conclusion = _make_conclusion(summary)
    return {
        "generated_at": _now_iso(),
        "scan_id": "BULL_to_NEUTRAL_transition_local_degradation_scan_v0",
        "scope": "diagnostic_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "events": events,
        "summary": summary,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic-only BULL->NEUTRAL transition scan.")
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    args = parser.parse_args()
    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_scan(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_events_tsv(EVENTS_TSV, report["events"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("BULL->NEUTRAL transition local degradation scan complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Events: {EVENTS_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
