#!/usr/bin/env python3
"""Diagnostic-only audit for N2B carried-position exit tails.

This script does not define or apply a rule. It replays frozen v2.1 baseline
signals and asks whether the remaining NEUTRAL -> BEAR risk pocket is explained
by positions inherited from before the transition and carried into BEAR.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import BARS_PER_DAY_5M, COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import _datetimes, _window_return_and_dd

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "n2b_carried_position_exit_tail"
REPORT_JSON = OUTPUT_DIR / "n2b_carried_tail_report.json"
SUMMARY_TSV = OUTPUT_DIR / "n2b_carried_tail_summary.tsv"
EVENTS_TSV = OUTPUT_DIR / "n2b_carried_tail_events.tsv"
NOTES_MD = OUTPUT_DIR / "n2b_carried_tail_notes.md"

HORIZON_DAYS = (1, 3, 7, 14)
POSITION_NAMES = {-1: "short", 0: "flat", 1: "long"}
SIGNAL_NAMES = {0: "flat", 1: "hold", 2: "long", 3: "short"}


@dataclass(frozen=True)
class PositionState:
    positions: np.ndarray
    entry_steps: np.ndarray


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


def _position_state(signals: np.ndarray) -> PositionState:
    positions = np.zeros(len(signals), dtype=np.int8)
    entry_steps = np.full(len(signals), -1, dtype=int)
    position = 0
    entry_step = -1
    for i, raw in enumerate(signals):
        target = _position_after_signal(int(raw), position)
        if target != position:
            if target == 0:
                entry_step = -1
            else:
                entry_step = i
            position = target
        positions[i] = position
        entry_steps[i] = entry_step
    return PositionState(positions=positions, entry_steps=entry_steps)


def _n2b_steps(regimes: np.ndarray) -> List[int]:
    labels = np.asarray(regimes, dtype=object)
    return [
        i for i in range(1, len(labels))
        if str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR"
    ]


def _find_tail_end(positions: np.ndarray, start: int, inherited_position: int) -> tuple[int, str]:
    for i in range(start, len(positions)):
        if int(positions[i]) != inherited_position:
            if int(positions[i]) == 0:
                return i + 1, "exit_to_flat"
            return i + 1, "direct_reversal"
    return len(positions), "still_open_at_dataset_end"


def _tail_metrics(equity: np.ndarray, start: int, end: int) -> Dict[str, Any]:
    if start >= end:
        return {"return": 0.0, "dd": 0.0, "equity_delta": 0.0}
    ret, dd, equity_delta = _window_return_and_dd(equity, start, end)
    return {"return": ret, "dd": dd, "equity_delta": equity_delta}


def _price_return(prices: np.ndarray, start: int, end: int, direction: int) -> float:
    if start >= end:
        return 0.0
    start_price = float(prices[start])
    end_price = float(prices[end - 1])
    if start_price <= 0:
        return 0.0
    raw_ret = (end_price / start_price) - 1.0
    return raw_ret if direction == 1 else -raw_ret


def _horizon_windows(
    equity: np.ndarray,
    prices: np.ndarray,
    start: int,
    direction: int,
) -> Dict[str, Dict[str, Any]]:
    windows: Dict[str, Dict[str, Any]] = {}
    for days in HORIZON_DAYS:
        end = min(len(equity), start + days * BARS_PER_DAY_5M)
        metrics = _tail_metrics(equity, start, end)
        windows[f"{days}d"] = {
            "bars": max(0, end - start),
            "return": round(float(metrics["return"]), 6),
            "dd": round(float(metrics["dd"]), 6),
            "equity_delta": round(float(metrics["equity_delta"]), 2),
            "directional_price_return": round(float(_price_return(prices, start, end, direction)), 6),
        }
    return windows


def _round_float(value: Any, digits: int = 6) -> Any:
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return value
    if not np.isfinite(val):
        return None
    return round(val, digits)


def _event_rows(
    signals: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    prices: np.ndarray,
    equity: np.ndarray,
) -> List[Dict[str, Any]]:
    state = _position_state(signals)
    events: List[Dict[str, Any]] = []

    for transition_idx, step in enumerate(_n2b_steps(regimes), start=1):
        pre_position = int(state.positions[step - 1])
        post_position = int(state.positions[step])
        raw_signal = int(signals[step])
        inherited_entry_step = int(state.entry_steps[step - 1])
        entry_age_bars = step - inherited_entry_step if inherited_entry_step >= 0 else None

        if pre_position == 0:
            category = "flat_at_transition"
            tail_start = None
            tail_end = None
            exit_reason = None
            tail = {"return": None, "dd": None, "equity_delta": None}
            directional_tail_return = None
            tail_bars = 0
            exit_time = None
            carried_into_bear = False
        elif post_position == pre_position:
            category = "carried_position_continues"
            tail_start = step
            tail_end, exit_reason = _find_tail_end(state.positions, step, pre_position)
            tail = _tail_metrics(equity, tail_start, tail_end)
            directional_tail_return = _price_return(prices, tail_start, tail_end, pre_position)
            tail_bars = tail_end - tail_start
            exit_time = str(times[tail_end - 1])
            carried_into_bear = True
        elif post_position == 0:
            category = "inherited_position_exits_on_transition"
            tail_start = step
            tail_end = step + 1
            exit_reason = "exit_on_transition_bar"
            tail = _tail_metrics(equity, tail_start, tail_end)
            directional_tail_return = _price_return(prices, tail_start, tail_end, pre_position)
            tail_bars = 1
            exit_time = str(times[step])
            carried_into_bear = False
        else:
            category = "inherited_position_reverses_on_transition"
            tail_start = step
            tail_end = step + 1
            exit_reason = "reversal_on_transition_bar"
            tail = _tail_metrics(equity, tail_start, tail_end)
            directional_tail_return = _price_return(prices, tail_start, tail_end, pre_position)
            tail_bars = 1
            exit_time = str(times[step])
            carried_into_bear = False

        windows = (
            _horizon_windows(equity, prices, step, pre_position)
            if pre_position != 0
            else {f"{days}d": None for days in HORIZON_DAYS}
        )
        event = {
            "transition_id": f"n2b_{transition_idx:04d}",
            "transition_step": step,
            "transition_time": str(times[step]),
            "prev_regime": str(regimes[step - 1]),
            "current_regime": str(regimes[step]),
            "raw_signal_at_transition": raw_signal,
            "raw_signal_name": SIGNAL_NAMES.get(raw_signal, str(raw_signal)),
            "pre_position": pre_position,
            "pre_position_name": POSITION_NAMES[pre_position],
            "post_position": post_position,
            "post_position_name": POSITION_NAMES[post_position],
            "category": category,
            "carried_into_bear": carried_into_bear,
            "inherited_entry_step": inherited_entry_step if inherited_entry_step >= 0 else None,
            "inherited_entry_time": (
                str(times[inherited_entry_step]) if inherited_entry_step >= 0 else None
            ),
            "entry_age_bars": entry_age_bars,
            "entry_age_days": _round_float(entry_age_bars / BARS_PER_DAY_5M, 3)
            if entry_age_bars is not None
            else None,
            "tail_start_step": tail_start,
            "tail_end_step": tail_end,
            "tail_exit_time": exit_time,
            "tail_exit_reason": exit_reason,
            "tail_bars": tail_bars,
            "tail_days": _round_float(tail_bars / BARS_PER_DAY_5M, 3),
            "tail_return": _round_float(tail["return"]),
            "tail_dd": _round_float(tail["dd"]),
            "tail_equity_delta": _round_float(tail["equity_delta"], 2),
            "tail_directional_price_return": _round_float(directional_tail_return),
            "horizon_windows": windows,
        }
        events.append(event)
    return events


def _summarize(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    carried = [event for event in events if event["category"] == "carried_position_continues"]
    inherited = [event for event in events if event["pre_position"] != 0]
    flat = [event for event in events if event["pre_position"] == 0]

    by_direction: Dict[str, Dict[str, Any]] = {}
    for direction in ("long", "short"):
        subset = [event for event in carried if event["pre_position_name"] == direction]
        returns = [float(event["tail_return"]) for event in subset if event["tail_return"] is not None]
        dds = [float(event["tail_dd"]) for event in subset if event["tail_dd"] is not None]
        by_direction[direction] = {
            "count": len(subset),
            "mean_tail_return": _round_float(mean(returns) if returns else None),
            "median_tail_return": _round_float(median(returns) if returns else None),
            "min_tail_return": _round_float(min(returns) if returns else None),
            "worst_tail_dd": _round_float(min(dds) if dds else None),
            "total_tail_equity_delta": _round_float(
                sum(float(event["tail_equity_delta"] or 0.0) for event in subset), 2
            ),
            "mean_tail_days": _round_float(
                mean(float(event["tail_days"]) for event in subset) if subset else None, 3
            ),
        }

    returns = [float(event["tail_return"]) for event in carried if event["tail_return"] is not None]
    dds = [float(event["tail_dd"]) for event in carried if event["tail_dd"] is not None]
    tail_days = [float(event["tail_days"]) for event in carried]
    total_tail_delta = sum(float(event["tail_equity_delta"] or 0.0) for event in carried)

    return {
        "transition_count": len(events),
        "flat_at_transition_count": len(flat),
        "inherited_position_count": len(inherited),
        "carried_position_continues_count": len(carried),
        "carried_position_continues_pct": _round_float(len(carried) / len(events), 4)
        if events
        else None,
        "mean_tail_return": _round_float(mean(returns) if returns else None),
        "median_tail_return": _round_float(median(returns) if returns else None),
        "min_tail_return": _round_float(min(returns) if returns else None),
        "max_tail_return": _round_float(max(returns) if returns else None),
        "worst_tail_dd": _round_float(min(dds) if dds else None),
        "total_tail_equity_delta": _round_float(total_tail_delta, 2),
        "mean_tail_days": _round_float(mean(tail_days) if tail_days else None, 3),
        "median_tail_days": _round_float(median(tail_days) if tail_days else None, 3),
        "by_direction": by_direction,
        "exit_reasons": {
            reason: sum(1 for event in carried if event["tail_exit_reason"] == reason)
            for reason in sorted({str(event["tail_exit_reason"]) for event in carried})
        },
    }


def _horizon_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    carried = [event for event in events if event["category"] == "carried_position_continues"]
    out: Dict[str, Any] = {}
    for days in HORIZON_DAYS:
        key = f"{days}d"
        rows = [event["horizon_windows"][key] for event in carried if event["horizon_windows"][key]]
        returns = [float(row["return"]) for row in rows]
        dds = [float(row["dd"]) for row in rows]
        out[key] = {
            "count": len(rows),
            "mean_return": _round_float(mean(returns) if returns else None),
            "median_return": _round_float(median(returns) if returns else None),
            "min_return": _round_float(min(returns) if returns else None),
            "worst_dd": _round_float(min(dds) if dds else None),
            "total_equity_delta": _round_float(
                sum(float(row["equity_delta"]) for row in rows), 2
            ),
        }
    return out


def _make_conclusion(summary: Dict[str, Any], horizon_summary: Dict[str, Any]) -> Dict[str, Any]:
    count = int(summary["carried_position_continues_count"])
    mean_tail = summary["mean_tail_return"]
    worst_dd = summary["worst_tail_dd"]
    h3 = horizon_summary["3d"]["mean_return"]
    if count >= 3 and mean_tail is not None and mean_tail < 0 and h3 is not None and h3 < 0:
        return {
            "label": "carried_tail_likely_contributor",
            "reason": (
                "N2B transitions often inherit an existing position that continues into BEAR, "
                "and carried tails are negative on average across full-tail and 3d windows."
            ),
        }
    if count == 0:
        return {
            "label": "carried_tail_not_primary",
            "reason": "No N2B transitions carried an existing position into BEAR.",
        }
    if worst_dd is not None and worst_dd < -0.05:
        return {
            "label": "inconclusive_tail_risk_present",
            "reason": (
                "Carried tails have material adverse samples, but average attribution is not "
                "clean enough to define an exit-tail rule."
            ),
        }
    return {
        "label": "carried_tail_not_primary",
        "reason": "Carried tails do not show repeated negative average attribution.",
    }


def _write_events_tsv(path: Path, events: List[Dict[str, Any]]) -> None:
    fields = [
        "transition_id",
        "transition_step",
        "transition_time",
        "pre_position_name",
        "post_position_name",
        "raw_signal_name",
        "category",
        "carried_into_bear",
        "entry_age_days",
        "tail_days",
        "tail_exit_reason",
        "tail_return",
        "tail_dd",
        "tail_equity_delta",
        "tail_directional_price_return",
        "h1_return",
        "h3_return",
        "h7_return",
        "h14_return",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for event in events:
            windows = event["horizon_windows"]
            writer.writerow(
                {
                    "transition_id": event["transition_id"],
                    "transition_step": event["transition_step"],
                    "transition_time": event["transition_time"],
                    "pre_position_name": event["pre_position_name"],
                    "post_position_name": event["post_position_name"],
                    "raw_signal_name": event["raw_signal_name"],
                    "category": event["category"],
                    "carried_into_bear": event["carried_into_bear"],
                    "entry_age_days": event["entry_age_days"],
                    "tail_days": event["tail_days"],
                    "tail_exit_reason": event["tail_exit_reason"],
                    "tail_return": event["tail_return"],
                    "tail_dd": event["tail_dd"],
                    "tail_equity_delta": event["tail_equity_delta"],
                    "tail_directional_price_return": event["tail_directional_price_return"],
                    "h1_return": windows["1d"]["return"] if windows["1d"] else "",
                    "h3_return": windows["3d"]["return"] if windows["3d"] else "",
                    "h7_return": windows["7d"]["return"] if windows["7d"] else "",
                    "h14_return": windows["14d"]["return"] if windows["14d"] else "",
                }
            )


def _write_summary_tsv(path: Path, summary: Dict[str, Any], horizon_summary: Dict[str, Any]) -> None:
    fields = ["section", "metric", "value"]
    rows = [
        ("overall", "transition_count", summary["transition_count"]),
        ("overall", "flat_at_transition_count", summary["flat_at_transition_count"]),
        ("overall", "inherited_position_count", summary["inherited_position_count"]),
        (
            "overall",
            "carried_position_continues_count",
            summary["carried_position_continues_count"],
        ),
        ("overall", "mean_tail_return", summary["mean_tail_return"]),
        ("overall", "worst_tail_dd", summary["worst_tail_dd"]),
        ("overall", "total_tail_equity_delta", summary["total_tail_equity_delta"]),
    ]
    for direction, values in summary["by_direction"].items():
        for key, value in values.items():
            rows.append((direction, key, value))
    for horizon, values in horizon_summary.items():
        for key, value in values.items():
            rows.append((horizon, key, value))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for section, metric, value in rows:
            writer.writerow({"section": section, "metric": metric, "value": value})


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    summary = report["summary"]
    horizon = report["horizon_summary"]
    conclusion = report["conclusion"]
    lines = [
        "# N2B Carried Position Exit Tail Diagnosis",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnostic-only. No helper, activation, live/demo, checkpoint, family, or LLM path changed.",
        "- Only NEUTRAL -> BEAR transitions are analyzed.",
        "- A carried tail means an existing non-flat position before transition continues into BEAR.",
        "- This is attribution, not a rule proposal.",
        "",
        "## Conclusion",
        "",
        f"**{conclusion['label']}**",
        "",
        conclusion["reason"],
        "",
        "## Summary",
        "",
        f"- N2B transitions: {summary['transition_count']}",
        f"- Flat at transition: {summary['flat_at_transition_count']}",
        f"- Inherited position at transition: {summary['inherited_position_count']}",
        f"- Carried position continues into BEAR: {summary['carried_position_continues_count']}",
        f"- Mean carried-tail return: {summary['mean_tail_return']:+.2%}"
        if summary["mean_tail_return"] is not None
        else "- Mean carried-tail return: N/A",
        f"- Worst carried-tail DD: {summary['worst_tail_dd']:+.2%}"
        if summary["worst_tail_dd"] is not None
        else "- Worst carried-tail DD: N/A",
        f"- Total carried-tail equity delta: {summary['total_tail_equity_delta']}",
        "",
        "## Carried Tail Horizons",
        "",
        "| horizon | count | mean return | worst DD | total equity delta |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in ("1d", "3d", "7d", "14d"):
        row = horizon[key]
        mean_ret = "N/A" if row["mean_return"] is None else f"{row['mean_return']:+.2%}"
        worst_dd = "N/A" if row["worst_dd"] is None else f"{row['worst_dd']:+.2%}"
        lines.append(
            f"| {key} | {row['count']} | {mean_ret} | {worst_dd} | "
            f"{row['total_equity_delta']} |"
        )
    lines.extend(
        [
            "",
            "## Direction Split",
            "",
            "| direction | count | mean tail return | worst DD | total equity delta |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for direction in ("long", "short"):
        row = summary["by_direction"][direction]
        mean_ret = "N/A" if row["mean_tail_return"] is None else f"{row['mean_tail_return']:+.2%}"
        worst_dd = "N/A" if row["worst_tail_dd"] is None else f"{row['worst_tail_dd']:+.2%}"
        lines.append(
            f"| {direction} | {row['count']} | {mean_ret} | {worst_dd} | "
            f"{row['total_tail_equity_delta']} |"
        )
    lines.extend(
        [
            "",
            "## Next Step Boundary",
            "",
            "- Do not modify the frozen reversal helper from this diagnosis.",
            "- Do not connect activation or live/demo paths.",
            "- If attribution is promising, write a separate carried-tail contract before any rule.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_diagnosis(
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
    signals = _generate_v21_signals(
        checkpoint,
        df_full,
        fast_days=fast_days,
        slow_days=slow_days,
    )
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, trades = evaluator.simulate(signals, prices)
    metrics = evaluator.compute_metrics(equity, trades)
    events = _event_rows(signals, regimes, times, prices, equity)
    summary = _summarize(events)
    horizon_summary = _horizon_summary(events)
    conclusion = _make_conclusion(summary, horizon_summary)

    return {
        "generated_at": _now_iso(),
        "diagnosis_id": "N2B_carried_position_exit_tail_diagnosis_v0",
        "scope": "diagnostic_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "baseline": {
            "id": "channel_breakout_v2_1_balanced",
            "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
            "return": _round_float(float(metrics["total_return"])),
            "dd": _round_float(float(metrics["max_drawdown"])),
            "sharpe": _round_float(float(metrics["sharpe_ratio"])),
            "initial_capital": INITIAL_CAPITAL,
        },
        "summary": summary,
        "horizon_summary": horizon_summary,
        "events": events,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only audit for N2B carried-position exit tails."
    )
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    args = parser.parse_args()

    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_diagnosis(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_events_tsv(EVENTS_TSV, report["events"])
    _write_summary_tsv(SUMMARY_TSV, report["summary"], report["horizon_summary"])
    _write_notes(NOTES_MD, report)

    print("N2B carried-position exit-tail diagnosis complete.")
    print(f"  JSON:  {REPORT_JSON}")
    print(f"  Events: {EVENTS_TSV}")
    print(f"  TSV:   {SUMMARY_TSV}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Conclusion: {report['conclusion']['label']} - {report['conclusion']['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
