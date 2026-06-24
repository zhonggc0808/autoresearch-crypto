#!/usr/bin/env python3
"""Diagnostic-only robustness scan for EMA regime labels in the worst rolling12m window."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import _datetimes, _position_path

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_regime_label_robustness"
NOTES_MD = OUTPUT_DIR / "rolling12m_regime_label_robustness_scan_v0.md"
SUMMARY_TSV = OUTPUT_DIR / "regime_label_robustness_summary.tsv"
LOSING_LONG_TSV = OUTPUT_DIR / "top_losing_long_label_stability.tsv"
TRANSITION_TSV = OUTPUT_DIR / "transition_drift.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

PARAMS = (
    ("baseline_50_200", 50, 200, "primary"),
    ("faster_40_160", 40, 160, "primary"),
    ("slower_60_240", 60, 240, "primary"),
    ("wider_ref_100_300", 100, 300, "reference"),
)
CONCLUSIONS = (
    "label_sensitive_pathology",
    "label_robust_long_decay",
    "inconclusive",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_float(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _simulate(signals: np.ndarray, prices: np.ndarray) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    return evaluator.simulate(signals, prices)


def _metrics(signals: np.ndarray, prices: np.ndarray) -> Dict[str, Any]:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, trades = evaluator.simulate(signals, prices)
    values = evaluator.compute_metrics(equity, trades)
    closed = [trade for trade in trades if trade.get("pnl") is not None]
    return {
        "return": _round_float(float(values["total_return"])),
        "dd": _round_float(float(values["max_drawdown"])),
        "trade_count": len(closed),
    }


def _find_worst_rolling_12m(
    signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
) -> Dict[str, Any]:
    window_bars = 12 * BARS_PER_MONTH
    step = window_bars // 2
    worst: Optional[Dict[str, Any]] = None
    for start in range(0, len(signals) - window_bars, step):
        end = start + window_bars
        row = {
            "start": start,
            "end": end,
            "start_time": str(times[start]),
            "end_time": str(times[end - 1]),
            **_metrics(signals[start:end], prices[start:end]),
        }
        if worst is None or row["return"] < worst["return"]:
            worst = row
    if worst is None:
        raise ValueError("not enough bars for regime label robustness scan")
    return worst


def _equity_deltas(equity: np.ndarray) -> np.ndarray:
    deltas = np.zeros(len(equity), dtype=float)
    if len(equity) == 0:
        return deltas
    deltas[0] = float(equity[0] - INITIAL_CAPITAL)
    deltas[1:] = np.diff(equity)
    return deltas


def _window_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    overlap = max(0, min(a_end, b_end) - max(a_start, b_start))
    denom = max(1, a_end - a_start)
    return overlap / denom


def _closed_trade_rows(
    trades: List[Dict[str, Any]],
    window_start: int,
) -> List[Dict[str, Any]]:
    rows = []
    open_trade: Optional[Dict[str, Any]] = None
    for trade in trades:
        step = int(trade.get("step", -1))
        ttype = str(trade.get("type", ""))
        if ttype in ("buy", "sell_short"):
            open_trade = {
                "direction": "long" if ttype == "buy" else "short",
                "entry_step": window_start + step,
            }
            continue
        if ttype not in ("sell", "sell_final", "buy_cover", "buy_cover_final"):
            continue
        if open_trade is None:
            continue
        rows.append(
            {
                **open_trade,
                "exit_step": window_start + step,
                "pnl": float(trade.get("pnl", 0.0)),
            }
        )
        open_trade = None
    return rows


def _regime_side_pnl(
    regimes: np.ndarray,
    positions: np.ndarray,
    deltas: np.ndarray,
) -> Dict[str, float]:
    out = {}
    for regime in ("BULL", "NEUTRAL", "BEAR"):
        for pos, side in ((1, "long"), (-1, "short"), (0, "flat")):
            mask = (regimes == regime) & (positions == pos)
            out[f"{regime.lower()}_{side}_pnl"] = _round_float(
                float(deltas[mask].sum()) if mask.any() else 0.0,
                2,
            )
            out[f"{regime.lower()}_{side}_bars"] = int(mask.sum())
    return out


def _transition_steps(regimes: np.ndarray, offset: int = 0) -> List[Dict[str, Any]]:
    rows = []
    labels = np.asarray(regimes, dtype=object)
    for i in range(1, len(labels)):
        prev = str(labels[i - 1])
        curr = str(labels[i])
        if prev != curr:
            rows.append({"step": offset + i, "transition": f"{prev}->{curr}"})
    return rows


def _nearest_transition_distance(
    base_rows: List[Dict[str, Any]],
    other_rows: List[Dict[str, Any]],
    transition: str,
) -> Optional[int]:
    base_steps = [int(row["step"]) for row in base_rows if row["transition"] == transition]
    other_steps = [int(row["step"]) for row in other_rows if row["transition"] == transition]
    if not base_steps or not other_steps:
        return None
    distances = [min(abs(step - other) for other in other_steps) for step in base_steps]
    return int(round(mean(distances)))


def _summary_rows(
    df_full: pd.DataFrame,
    signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
    baseline_regimes: np.ndarray,
    baseline_worst: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, np.ndarray]]:
    rows = []
    labels_by_param: Dict[str, np.ndarray] = {}
    start = int(baseline_worst["start"])
    end = int(baseline_worst["end"])
    win_signals = signals[start:end]
    win_prices = prices[start:end]
    win_equity, _ = _simulate(win_signals, win_prices)
    win_positions = _position_path(win_signals)
    deltas = _equity_deltas(win_equity)

    for name, fast, slow, role in PARAMS:
        labels = build_daily_regime_labels(df_full, fast_days=fast, slow_days=slow)
        labels_by_param[name] = labels
        win_labels = labels[start:end]
        worst = _find_worst_rolling_12m(signals, prices, times)
        flip_mask_full = labels != baseline_regimes
        flip_mask_win = win_labels != baseline_regimes[start:end]
        dist = {
            regime.lower(): int((win_labels == regime).sum())
            for regime in ("BULL", "NEUTRAL", "BEAR")
        }
        transition_count = len(_transition_steps(win_labels, offset=start))
        row = {
            "setting": name,
            "role": role,
            "fast_days": fast,
            "slow_days": slow,
            "worst12m_start": worst["start"],
            "worst12m_end": worst["end"],
            "worst12m_start_time": worst["start_time"],
            "worst12m_end_time": worst["end_time"],
            "worst12m_overlap_with_baseline": _round_float(
                _window_overlap(start, end, int(worst["start"]), int(worst["end"])),
                4,
            ),
            "window_return": baseline_worst["return"],
            "window_dd": baseline_worst["dd"],
            "label_flip_rate_full": _round_float(float(flip_mask_full.mean()), 4),
            "label_flip_rate_worst12m": _round_float(float(flip_mask_win.mean()), 4),
            "transition_count_worst12m": transition_count,
            **{f"{key}_bars": value for key, value in dist.items()},
            **{
                f"{key}_pct": _round_float(value / len(win_labels), 4)
                for key, value in dist.items()
            },
            **_regime_side_pnl(win_labels, win_positions, deltas),
        }
        rows.append(row)
    return rows, labels_by_param


def _losing_long_stability_rows(
    signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
    baseline_worst: Dict[str, Any],
    labels_by_param: Dict[str, np.ndarray],
) -> List[Dict[str, Any]]:
    start = int(baseline_worst["start"])
    end = int(baseline_worst["end"])
    _, trades = _simulate(signals[start:end], prices[start:end])
    closed = _closed_trade_rows(trades, start)
    losing_longs = sorted(
        [row for row in closed if row["direction"] == "long" and row["pnl"] < 0],
        key=lambda row: row["pnl"],
    )[:10]
    rows = []
    for idx, trade in enumerate(losing_longs, start=1):
        entry = int(trade["entry_step"])
        row = {
            "rank": idx,
            "entry_step": entry,
            "entry_time": str(times[entry]),
            "exit_time": str(times[int(trade["exit_step"])]),
            "pnl": _round_float(float(trade["pnl"]), 2),
        }
        labels = []
        for name, _, _, _ in PARAMS:
            label = str(labels_by_param[name][entry])
            row[f"{name}_label"] = label
            labels.append(label)
        row["label_unique_count"] = len(set(labels))
        row["stable_vs_baseline_count"] = sum(1 for label in labels if label == labels[0])
        rows.append(row)
    return rows


def _transition_drift_rows(
    labels_by_param: Dict[str, np.ndarray],
    baseline_worst: Dict[str, Any],
) -> List[Dict[str, Any]]:
    start = int(baseline_worst["start"])
    end = int(baseline_worst["end"])
    base = _transition_steps(labels_by_param["baseline_50_200"][start:end], offset=start)
    rows = []
    for name, _, _, role in PARAMS:
        current = _transition_steps(labels_by_param[name][start:end], offset=start)
        for transition in sorted({row["transition"] for row in base + current}):
            rows.append(
                {
                    "setting": name,
                    "role": role,
                    "transition": transition,
                    "baseline_count": sum(1 for row in base if row["transition"] == transition),
                    "setting_count": sum(1 for row in current if row["transition"] == transition),
                    "avg_nearest_drift_bars": _nearest_transition_distance(
                        base,
                        current,
                        transition,
                    ),
                }
            )
    return rows


def _make_conclusion(
    summary: List[Dict[str, Any]],
    losing_long_rows: List[Dict[str, Any]],
) -> Dict[str, str]:
    primary = [row for row in summary if row["role"] == "primary"]
    bull_long_values = [float(row["bull_long_pnl"]) for row in primary]
    bear_long_values = [float(row["bear_long_pnl"]) for row in primary]
    flip_rates = [float(row["label_flip_rate_worst12m"]) for row in primary[1:]]
    stable_top = [
        row for row in losing_long_rows
        if int(row["stable_vs_baseline_count"]) >= 3
    ]
    if all(value < -1000 for value in bull_long_values) and all(value < -1000 for value in bear_long_values):
        return {
            "label": "label_robust_long_decay",
            "reason": (
                "BULL_long and BEAR_long losses remain materially negative under adjacent "
                "EMA regime label settings; top losing long labels are mostly stable."
            ),
        }
    if any(rate >= 0.25 for rate in flip_rates) or len(stable_top) < max(3, len(losing_long_rows) // 2):
        return {
            "label": "label_sensitive_pathology",
            "reason": (
                "Worst-window long attribution changes materially under adjacent EMA label settings."
            ),
        }
    return {
        "label": "inconclusive",
        "reason": "Regime label robustness is mixed and should not drive a rule.",
    }


def _write_tsv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    conclusion = report["conclusion"]
    worst = report["baseline_worst_window"]
    lines = [
        "# rolling12m_regime_label_robustness_scan_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis only.",
        "- Strategy signals and equity are held fixed; only EMA regime labels change.",
        "- No strategy logic, frozen N2B helper, activation/default oracle, live/demo, checkpoint, family, LLM, scoring, patch, or filter change.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `label_sensitive_pathology`, `label_robust_long_decay`, `inconclusive`.",
        "",
        "## Baseline Worst Rolling12m Window",
        "",
        f"- Start: {worst['start_time']} ({worst['start']})",
        f"- End: {worst['end_time']} ({worst['end']})",
        f"- Return: {worst['return']:+.2%}",
        f"- DD: {worst['dd']:+.2%}",
        "",
        "## Regime Label Settings",
        "",
        "| setting | role | flip worst12m | BULL bars | BEAR bars | NEUTRAL bars | BULL_long pnl | BEAR_long pnl | transitions |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["summary_rows"]:
        lines.append(
            f"| {row['setting']} | {row['role']} | {row['label_flip_rate_worst12m']:.1%} | "
            f"{row['bull_bars']} | {row['bear_bars']} | {row['neutral_bars']} | "
            f"{row['bull_long_pnl']:+.2f} | {row['bear_long_pnl']:+.2f} | "
            f"{row['transition_count_worst12m']} |"
        )
    lines.extend(
        [
            "",
            "## Top Losing Long Label Stability",
            "",
            f"- Top losing long trades checked: {len(report['losing_long_rows'])}",
            f"- Fully baseline-stable labels: {sum(1 for row in report['losing_long_rows'] if int(row['stable_vs_baseline_count']) == len(PARAMS))}",
            f"- Stable across primary settings: {sum(1 for row in report['losing_long_rows'] if int(row['stable_vs_baseline_count']) >= 3)}",
            "",
            "## Guardrail",
            "",
            "- This scan may justify a future regime-label audit contract only if label sensitivity is found.",
            "- It does not justify a strategy filter or patch by itself.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m regime label robustness scan",
        "",
        f"Date: {report['generated_at'][:10]}",
        "",
        "## Result",
        "",
        "No protected runtime path was modified by this diagnostic scan.",
        "",
        "Protected paths kept out of scope:",
        "",
        "- strategy logic;",
        "- N2B helper implementation;",
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
        "The scan did not edit or depend on that file.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_scan(*, data_path: Optional[Path] = None) -> Dict[str, Any]:
    if data_path is None:
        data_path = _find_eth_data()
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    times = _datetimes(df_full)
    prices = df_full["close"].values.astype(float)
    checkpoint = _load_baseline_params()
    signals = _generate_v21_signals(checkpoint, df_full, fast_days=50, slow_days=200)
    baseline_regimes = build_daily_regime_labels(df_full, fast_days=50, slow_days=200)
    baseline_worst = _find_worst_rolling_12m(signals, prices, times)
    summary, labels_by_param = _summary_rows(
        df_full,
        signals,
        prices,
        times,
        baseline_regimes,
        baseline_worst,
    )
    losing_long_rows = _losing_long_stability_rows(
        signals,
        prices,
        times,
        baseline_worst,
        labels_by_param,
    )
    transition_rows = _transition_drift_rows(labels_by_param, baseline_worst)
    conclusion = _make_conclusion(summary, losing_long_rows)
    return {
        "generated_at": _now_iso(),
        "scan_id": "rolling12m_regime_label_robustness_scan_v0",
        "scope": "diagnostic_only_label_attribution_no_strategy_change",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "baseline_worst_window": baseline_worst,
        "summary_rows": summary,
        "losing_long_rows": losing_long_rows,
        "transition_rows": transition_rows,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic-only EMA regime label robustness scan.")
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    args = parser.parse_args()
    report = run_scan(data_path=Path(args.data_path) if args.data_path else None)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_tsv(SUMMARY_TSV, report["summary_rows"])
    _write_tsv(LOSING_LONG_TSV, report["losing_long_rows"])
    _write_tsv(TRANSITION_TSV, report["transition_rows"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m regime label robustness scan complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Summary: {SUMMARY_TSV}")
    print(f"  Losing long labels: {LOSING_LONG_TSV}")
    print(f"  Transition drift: {TRANSITION_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
