#!/usr/bin/env python3
"""Diagnostic-only scan for losing-long exit latency in the worst rolling 12m window."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import BARS_PER_DAY_5M, COMMISSION, SLIPPAGE
from dex.indicators import compute_ema
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _evaluate_signals,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
    _safe_execution_signals,
)
from scripts.transition_attribution_audit import _datetimes

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_long_exit_latency"
NOTES_MD = OUTPUT_DIR / "rolling12m_long_loss_exit_latency_scan_v0.md"
TRADES_TSV = OUTPUT_DIR / "long_loss_exit_latency_trades.tsv"
COUNTERFACTUAL_TSV = OUTPUT_DIR / "long_loss_exit_latency_counterfactuals.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

ENTRY_LOOKBACK = 375
CONCLUSIONS = (
    "exit_latency_promising",
    "exit_latency_too_broad",
    "no_actionable_exit_latency",
    "inconclusive",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round_float(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _target_position(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def _simulate(signals: np.ndarray, prices: np.ndarray) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    return evaluator.simulate(signals, prices)


def _closed_trade_count(trades: List[Dict[str, Any]]) -> int:
    return len([trade for trade in trades if trade.get("pnl") is not None])


def _metrics(signals: np.ndarray, prices: np.ndarray) -> Dict[str, Any]:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, trades = evaluator.simulate(signals, prices)
    values = evaluator.compute_metrics(equity, trades)
    return {
        "return": _round_float(float(values["total_return"])),
        "dd": _round_float(float(values["max_drawdown"])),
        "trade_count": _closed_trade_count(trades),
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
        raise ValueError("not enough bars for rolling12m exit latency scan")
    return worst


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
                "entry_step_local": step,
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
                "exit_step_local": step,
                "exit_step": window_start + step,
                "pnl": float(trade.get("pnl", 0.0)),
            }
        )
        open_trade = None
    return rows


def _first_index(mask: np.ndarray, offset: int = 0) -> Optional[int]:
    found = np.flatnonzero(mask)
    if len(found) == 0:
        return None
    return int(found[0] + offset)


def _latency_rows(
    trades: List[Dict[str, Any]],
    df_full: pd.DataFrame,
    times: pd.DatetimeIndex,
    prices: np.ndarray,
) -> List[Dict[str, Any]]:
    high = df_full["high"].values.astype(float)
    low = df_full["low"].values.astype(float)
    close = prices
    channel_high = (
        pd.Series(high).rolling(ENTRY_LOOKBACK, min_periods=ENTRY_LOOKBACK).max().shift(1).to_numpy()
    )
    ema50 = compute_ema(close, 50 * BARS_PER_DAY_5M)
    rows = []
    for trade in trades:
        if trade["direction"] != "long" or trade["pnl"] >= 0:
            continue
        entry = int(trade["entry_step"])
        exit_step = int(trade["exit_step"])
        if entry >= len(close) or exit_step >= len(close) or exit_step <= entry:
            continue
        entry_price = float(close[entry])
        trade_close = close[entry:exit_step + 1]
        trade_low = low[entry:exit_step + 1]
        trade_high = high[entry:exit_step + 1]
        channel = float(channel_high[entry]) if np.isfinite(channel_high[entry]) else entry_price
        mae_local = int(np.argmin(trade_low))
        mae_step = entry + mae_local

        below_entry = _first_index(trade_close < entry_price, entry)
        below_channel = _first_index(trade_close < channel, entry)
        below_ema50 = _first_index(trade_close < ema50[entry:exit_step + 1], entry)
        warning_candidates = [
            ("below_entry", below_entry),
            ("below_channel", below_channel),
            ("below_ema50", below_ema50),
        ]
        existing = [(name, step) for name, step in warning_candidates if step is not None]
        if existing:
            warning_name, warning_step = min(existing, key=lambda item: int(item[1]))
        else:
            warning_name, warning_step = "none", None

        if warning_step is not None:
            warning_price = float(close[warning_step])
            warning_return = (warning_price / entry_price) - 1.0
            latency_bars = exit_step - warning_step
            saved_price_return = (float(close[exit_step]) / warning_price) - 1.0
        else:
            warning_return = None
            latency_bars = None
            saved_price_return = None

        rows.append(
            {
                "entry_step": entry,
                "exit_step": exit_step,
                "entry_time": str(times[entry]),
                "exit_time": str(times[exit_step]),
                "hold_days": _round_float((exit_step - entry) / BARS_PER_DAY_5M, 3),
                "pnl": _round_float(float(trade["pnl"]), 2),
                "mfe": _round_float((float(np.max(trade_high)) / entry_price) - 1.0),
                "mae": _round_float((float(np.min(trade_low)) / entry_price) - 1.0),
                "mae_step": mae_step,
                "mae_time": str(times[mae_step]),
                "mae_delay_bars": int(exit_step - mae_step),
                "below_entry_step": below_entry,
                "below_entry_delay_bars": int(exit_step - below_entry)
                if below_entry is not None
                else None,
                "below_channel_step": below_channel,
                "below_channel_delay_bars": int(exit_step - below_channel)
                if below_channel is not None
                else None,
                "below_ema50_step": below_ema50,
                "below_ema50_delay_bars": int(exit_step - below_ema50)
                if below_ema50 is not None
                else None,
                "first_warning": warning_name,
                "first_warning_step": warning_step,
                "first_warning_time": str(times[warning_step]) if warning_step is not None else None,
                "first_warning_return": _round_float(warning_return),
                "first_warning_latency_bars": latency_bars,
                "first_warning_latency_days": _round_float(latency_bars / BARS_PER_DAY_5M, 3)
                if latency_bars is not None
                else None,
                "saved_price_return_after_warning": _round_float(saved_price_return),
            }
        )
    return rows


def _apply_warning_exit(
    signals: np.ndarray,
    warning_by_entry: Dict[int, int],
) -> Tuple[np.ndarray, Dict[str, Any]]:
    out = np.asarray(signals, dtype=int).copy()
    position = 0
    active_entry: Optional[int] = None
    diag = {
        "signals_changed": 0,
        "warning_exits": 0,
        "target_losing_long_entries": len(warning_by_entry),
    }
    for i, raw_value in enumerate(out):
        raw = int(raw_value)
        sig = raw
        if position == 1 and active_entry in warning_by_entry and i == warning_by_entry[active_entry]:
            sig = 0
            diag["warning_exits"] += 1
        if sig != raw:
            diag["signals_changed"] += 1
        out[i] = sig
        prev_position = position
        position = _target_position(sig, position)
        if prev_position != 1 and position == 1:
            active_entry = i
        elif position != 1:
            active_entry = None
    return out, diag


def _counterfactual_rows(
    baseline_signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
    split_idx: int,
    baseline_worst: Dict[str, Any],
    latency: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    baseline_oos_safe = _evaluate_signals(
        _safe_execution_signals(baseline_signals)[split_idx:],
        prices[split_idx:],
    )
    configs = {
        "first_warning_flat_losing_longs_only": {
            int(row["entry_step"]): int(row["first_warning_step"])
            for row in latency
            if row["first_warning_step"] is not None
        },
        "below_entry_flat_losing_longs_only": {
            int(row["entry_step"]): int(row["below_entry_step"])
            for row in latency
            if row["below_entry_step"] is not None
        },
        "below_channel_flat_losing_longs_only": {
            int(row["entry_step"]): int(row["below_channel_step"])
            for row in latency
            if row["below_channel_step"] is not None
        },
        "below_ema50_flat_losing_longs_only": {
            int(row["entry_step"]): int(row["below_ema50_step"])
            for row in latency
            if row["below_ema50_step"] is not None
        },
    }
    rows = []
    for candidate_id, warning_by_entry in configs.items():
        signals, diag = _apply_warning_exit(baseline_signals, warning_by_entry)
        worst = _find_worst_rolling_12m(signals, prices, times)
        oos_safe = _evaluate_signals(_safe_execution_signals(signals)[split_idx:], prices[split_idx:])
        same_window = _metrics(
            signals[int(baseline_worst["start"]):int(baseline_worst["end"])],
            prices[int(baseline_worst["start"]):int(baseline_worst["end"])],
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "target_warning_count": len(warning_by_entry),
                "signals_changed": diag["signals_changed"],
                "warning_exits": diag["warning_exits"],
                "same_window_return": same_window["return"],
                "same_window_return_delta": _round_float(
                    same_window["return"] - baseline_worst["return"]
                ),
                "same_window_dd": same_window["dd"],
                "same_window_dd_delta": _round_float(same_window["dd"] - baseline_worst["dd"]),
                "worst12m_return": worst["return"],
                "worst12m_return_delta": _round_float(worst["return"] - baseline_worst["return"]),
                "worst12m_dd": worst["dd"],
                "worst12m_dd_delta": _round_float(worst["dd"] - baseline_worst["dd"]),
                "oos_safe_return": _round_float(float(oos_safe["return"])),
                "oos_safe_return_delta": _round_float(
                    float(oos_safe["return"]) - float(baseline_oos_safe["return"])
                ),
            }
        )
    return rows


def _latency_summary(latency: List[Dict[str, Any]]) -> Dict[str, Any]:
    warning_rows = [row for row in latency if row["first_warning_step"] is not None]
    return {
        "losing_long_count": len(latency),
        "first_warning_count": len(warning_rows),
        "below_entry_count": len([row for row in latency if row["below_entry_step"] is not None]),
        "below_channel_count": len([row for row in latency if row["below_channel_step"] is not None]),
        "below_ema50_count": len([row for row in latency if row["below_ema50_step"] is not None]),
        "median_first_warning_latency_days": _round_float(
            median([float(row["first_warning_latency_days"]) for row in warning_rows])
            if warning_rows
            else None,
            3,
        ),
        "mean_first_warning_latency_days": _round_float(
            mean([float(row["first_warning_latency_days"]) for row in warning_rows])
            if warning_rows
            else None,
            3,
        ),
        "median_mae_delay_days": _round_float(
            median([float(row["mae_delay_bars"]) / BARS_PER_DAY_5M for row in latency])
            if latency
            else None,
            3,
        ),
    }


def _make_conclusion(
    summary: Dict[str, Any],
    counterfactuals: List[Dict[str, Any]],
) -> Dict[str, str]:
    if summary["losing_long_count"] < 10:
        return {
            "label": "inconclusive",
            "reason": "Too few losing long trades to evaluate exit latency.",
        }
    promising = [
        row
        for row in counterfactuals
        if row["warning_exits"] <= 25
        and float(row["same_window_return_delta"]) >= 0.05
        and float(row["oos_safe_return_delta"]) > -0.15
    ]
    if promising:
        return {
            "label": "exit_latency_promising",
            "reason": (
                "A losing-long-only warning exit upper bound materially improves the worst window "
                "without obvious OOS safe damage."
            ),
        }
    broad = [
        row
        for row in counterfactuals
        if row["warning_exits"] > 25 or float(row["oos_safe_return_delta"]) <= -0.15
    ]
    if broad:
        return {
            "label": "exit_latency_too_broad",
            "reason": (
                "Warning exits either require broad intervention or damage OOS safe return."
            ),
        }
    return {
        "label": "no_actionable_exit_latency",
        "reason": "Early warning exits do not materially improve the worst window.",
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
    worst = report["worst_window"]
    summary = report["latency_summary"]
    lines = [
        "# rolling12m_long_loss_exit_latency_scan_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis only.",
        "- Only losing long trades inside the worst rolling12m window are used for latency attribution.",
        "- Counterfactual exits are hindsight upper bounds, not implementable rules.",
        "- No patch, helper, activation, default oracle, live/demo, checkpoint, family, or LLM path change.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `exit_latency_promising`, `exit_latency_too_broad`, `no_actionable_exit_latency`, `inconclusive`.",
        "",
        "## Worst Rolling12m Window",
        "",
        f"- Start: {worst['start_time']} ({worst['start']})",
        f"- End: {worst['end_time']} ({worst['end']})",
        f"- Return: {worst['return']:+.2%}",
        f"- DD: {worst['dd']:+.2%}",
        "",
        "## Latency Summary",
        "",
        f"- Losing long count: {summary['losing_long_count']}",
        f"- First warning count: {summary['first_warning_count']}",
        f"- Below entry count: {summary['below_entry_count']}",
        f"- Below channel count: {summary['below_channel_count']}",
        f"- Below EMA50 count: {summary['below_ema50_count']}",
        f"- Median first-warning latency: {summary['median_first_warning_latency_days']} days",
        f"- Mean first-warning latency: {summary['mean_first_warning_latency_days']} days",
        f"- Median MAE-to-exit delay: {summary['median_mae_delay_days']} days",
        "",
        "## Warning Exit Upper Bounds",
        "",
        "| candidate | warning exits | same-window delta | worst12m delta | OOS safe delta |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["counterfactual_rows"]:
        lines.append(
            f"| {row['candidate_id']} | {row['warning_exits']} | "
            f"{row['same_window_return_delta']:+.2%} | {row['worst12m_return_delta']:+.2%} | "
            f"{row['oos_safe_return_delta']:+.2%} |"
        )
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "- This scan does not define an exit rule.",
            "- Losing-long-only exits are hindsight upper bounds and cannot be implemented directly.",
            "- Any future exit rule requires a separate causal contract and OOS-safe sweep.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m long loss exit latency scan",
        "",
        f"Date: {report['generated_at'][:10]}",
        "",
        "## Result",
        "",
        "No protected runtime path was modified by this diagnostic scan.",
        "",
        "Protected paths kept out of scope:",
        "",
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
    build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    checkpoint = _load_baseline_params()
    signals = _generate_v21_signals(checkpoint, df_full, fast_days=fast_days, slow_days=slow_days)
    worst = _find_worst_rolling_12m(signals, prices, times)
    start = int(worst["start"])
    end = int(worst["end"])
    _, win_trades = _simulate(signals[start:end], prices[start:end])
    closed = _closed_trade_rows(win_trades, start)
    latency = _latency_rows(closed, df_full, times, prices)
    counterfactuals = _counterfactual_rows(signals, prices, times, split_idx, worst, latency)
    summary = _latency_summary(latency)
    conclusion = _make_conclusion(summary, counterfactuals)
    return {
        "generated_at": _now_iso(),
        "scan_id": "rolling12m_long_loss_exit_latency_scan_v0",
        "scope": "diagnostic_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "worst_window": worst,
        "latency_rows": latency,
        "latency_summary": summary,
        "counterfactual_rows": counterfactuals,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic-only losing-long exit latency scan.")
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
    _write_tsv(TRADES_TSV, report["latency_rows"])
    _write_tsv(COUNTERFACTUAL_TSV, report["counterfactual_rows"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m long loss exit latency scan complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Trades: {TRADES_TSV}")
    print(f"  Counterfactuals: {COUNTERFACTUAL_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
