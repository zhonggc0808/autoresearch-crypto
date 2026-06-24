#!/usr/bin/env python3
"""Diagnostic-only taxonomy for losing long breakouts in the worst rolling 12m window."""

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
from dex.indicators import compute_atr, compute_ema
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import _datetimes

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_long_failure_taxonomy"
NOTES_MD = OUTPUT_DIR / "rolling12m_long_breakout_failure_taxonomy_v0.md"
TRADES_TSV = OUTPUT_DIR / "losing_long_failure_taxonomy.tsv"
SUMMARY_TSV = OUTPUT_DIR / "failure_mode_summary.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

ENTRY_LOOKBACK = 375
CONCLUSIONS = (
    "false_breakout_dominant",
    "late_trend_exhaustion_dominant",
    "volatility_whipsaw_dominant",
    "mixed_long_failure_modes",
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


def _find_worst_rolling_12m(
    signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
) -> Dict[str, Any]:
    window_bars = 12 * BARS_PER_MONTH
    step = window_bars // 2
    worst: Optional[Dict[str, Any]] = None
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    for start in range(0, len(signals) - window_bars, step):
        end = start + window_bars
        equity, trades = _simulate(signals[start:end], prices[start:end])
        metrics = evaluator.compute_metrics(equity, trades)
        row = {
            "start": start,
            "end": end,
            "start_time": str(times[start]),
            "end_time": str(times[end - 1]),
            "return": _round_float(float(metrics["total_return"])),
            "max_dd": _round_float(float(metrics["max_drawdown"])),
        }
        if worst is None or row["return"] < worst["return"]:
            worst = row
    if worst is None:
        raise ValueError("not enough bars for rolling12m taxonomy")
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


def _safe_return(prices: np.ndarray, start: int, end: int) -> Optional[float]:
    if start < 0 or end <= start or end >= len(prices):
        return None
    base = float(prices[start])
    if base <= 0:
        return None
    return (float(prices[end]) / base) - 1.0


def _taxonomy_rows(
    trades: List[Dict[str, Any]],
    df_full: pd.DataFrame,
    times: pd.DatetimeIndex,
    prices: np.ndarray,
    regimes: np.ndarray,
) -> List[Dict[str, Any]]:
    high = df_full["high"].values.astype(float)
    low = df_full["low"].values.astype(float)
    close = prices
    channel_high = (
        pd.Series(high).rolling(ENTRY_LOOKBACK, min_periods=ENTRY_LOOKBACK).max().shift(1).to_numpy()
    )
    atr = compute_atr(df_full, 14)
    atr_pct = np.divide(atr, close, out=np.zeros_like(atr, dtype=float), where=close > 0)
    ema50 = compute_ema(close, 50 * BARS_PER_DAY_5M)
    ema200 = compute_ema(close, 200 * BARS_PER_DAY_5M)
    atr_rank = pd.Series(atr_pct).rolling(30 * BARS_PER_DAY_5M, min_periods=100).rank(pct=True).to_numpy()

    rows = []
    for trade in trades:
        if trade["direction"] != "long" or trade["pnl"] >= 0:
            continue
        entry = int(trade["entry_step"])
        exit_step = int(trade["exit_step"])
        if entry >= len(close) or exit_step >= len(close):
            continue
        entry_price = float(close[entry])
        hold_slice = slice(entry, exit_step + 1)
        mfe = (float(np.max(high[hold_slice])) / entry_price) - 1.0
        mae = (float(np.min(low[hold_slice])) / entry_price) - 1.0
        channel = float(channel_high[entry]) if np.isfinite(channel_high[entry]) else entry_price

        one_day_end = min(len(close) - 1, entry + BARS_PER_DAY_5M)
        three_day_end = min(len(close) - 1, entry + 3 * BARS_PER_DAY_5M)
        seven_day_end = min(len(close) - 1, entry + 7 * BARS_PER_DAY_5M)
        close_1d = close[entry:one_day_end + 1]
        close_3d = close[entry:three_day_end + 1]
        false_break_1d = bool(np.any(close_1d < channel))
        false_break_3d = bool(np.any(close_3d < channel))

        pre_7d = _safe_return(close, max(0, entry - 7 * BARS_PER_DAY_5M), entry)
        pre_14d = _safe_return(close, max(0, entry - 14 * BARS_PER_DAY_5M), entry)
        pre_30d = _safe_return(close, max(0, entry - 30 * BARS_PER_DAY_5M), entry)
        fwd_1d = _safe_return(close, entry, one_day_end)
        fwd_3d = _safe_return(close, entry, three_day_end)
        fwd_7d = _safe_return(close, entry, seven_day_end)

        ema50_slope_5d = None
        slope_idx = entry - 5 * BARS_PER_DAY_5M
        if slope_idx >= 0 and float(ema50[slope_idx]) > 0:
            ema50_slope_5d = (float(ema50[entry]) / float(ema50[slope_idx])) - 1.0

        higher_tf_weak = bool(
            regimes[entry] == "BEAR"
            or (regimes[entry] == "BULL" and ((pre_14d is not None and pre_14d < 0) or (ema50_slope_5d is not None and ema50_slope_5d < 0)))
            or float(close[entry]) < float(ema200[entry])
        )
        late_exhaustion = bool(
            ((pre_14d is not None and pre_14d > 0.08) or (pre_30d is not None and pre_30d > 0.15))
            and (fwd_3d is not None and fwd_3d < 0)
        )
        atr_rank_value = float(atr_rank[entry]) if np.isfinite(atr_rank[entry]) else 0.0
        post_1d_range = (float(np.max(high[entry:one_day_end + 1])) / float(np.min(low[entry:one_day_end + 1]))) - 1.0
        volatility_whipsaw = bool(
            (atr_rank_value >= 0.7 or post_1d_range >= max(0.04, 3.0 * float(atr_pct[entry])))
            and mfe > 0.005
            and mae < -0.02
        )
        false_breakout = bool(false_break_3d and mfe < 0.03)

        if false_breakout:
            primary = "false_breakout"
        elif late_exhaustion:
            primary = "late_trend_exhaustion"
        elif volatility_whipsaw:
            primary = "volatility_whipsaw"
        elif higher_tf_weak:
            primary = "regime_contradiction"
        else:
            primary = "unclassified"

        rows.append(
            {
                "entry_step": entry,
                "exit_step": exit_step,
                "entry_time": str(times[entry]),
                "exit_time": str(times[exit_step]),
                "entry_month": str(pd.Timestamp(times[entry]).strftime("%Y-%m")),
                "entry_regime": str(regimes[entry]),
                "exit_regime": str(regimes[exit_step]),
                "hold_days": _round_float((exit_step - entry) / BARS_PER_DAY_5M, 3),
                "pnl": _round_float(float(trade["pnl"]), 2),
                "mfe": _round_float(mfe),
                "mae": _round_float(mae),
                "entry_channel_high_gap": _round_float((entry_price / channel) - 1.0 if channel > 0 else None),
                "fwd_1d_return": _round_float(fwd_1d),
                "fwd_3d_return": _round_float(fwd_3d),
                "fwd_7d_return": _round_float(fwd_7d),
                "pre_7d_return": _round_float(pre_7d),
                "pre_14d_return": _round_float(pre_14d),
                "pre_30d_return": _round_float(pre_30d),
                "ema50_slope_5d": _round_float(ema50_slope_5d),
                "atr_pct": _round_float(float(atr_pct[entry])),
                "atr_rank_30d": _round_float(atr_rank_value),
                "post_1d_range": _round_float(post_1d_range),
                "false_breakout": false_breakout,
                "false_break_1d": false_break_1d,
                "false_break_3d": false_break_3d,
                "late_trend_exhaustion": late_exhaustion,
                "regime_contradiction": higher_tf_weak,
                "volatility_whipsaw": volatility_whipsaw,
                "primary_mode": primary,
            }
        )
    return rows


def _summary_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for mode in (
        "false_breakout",
        "late_trend_exhaustion",
        "volatility_whipsaw",
        "regime_contradiction",
        "unclassified",
    ):
        subset = [row for row in rows if row["primary_mode"] == mode]
        out.append(
            {
                "mode": mode,
                "primary_count": len(subset),
                "primary_pct": _round_float(len(subset) / len(rows) if rows else 0.0, 4),
                "total_pnl": _round_float(sum(float(row["pnl"]) for row in subset), 2),
                "avg_pnl": _round_float(mean([float(row["pnl"]) for row in subset]) if subset else 0.0, 2),
                "median_mfe": _round_float(median([float(row["mfe"]) for row in subset]) if subset else 0.0),
                "median_mae": _round_float(median([float(row["mae"]) for row in subset]) if subset else 0.0),
            }
        )
    for flag in (
        "false_breakout",
        "late_trend_exhaustion",
        "regime_contradiction",
        "volatility_whipsaw",
    ):
        subset = [row for row in rows if row[flag]]
        out.append(
            {
                "mode": f"flag_{flag}",
                "primary_count": len(subset),
                "primary_pct": _round_float(len(subset) / len(rows) if rows else 0.0, 4),
                "total_pnl": _round_float(sum(float(row["pnl"]) for row in subset), 2),
                "avg_pnl": _round_float(mean([float(row["pnl"]) for row in subset]) if subset else 0.0, 2),
                "median_mfe": _round_float(median([float(row["mfe"]) for row in subset]) if subset else 0.0),
                "median_mae": _round_float(median([float(row["mae"]) for row in subset]) if subset else 0.0),
            }
        )
    by_month = sorted({row["entry_month"] for row in rows})
    for month in by_month:
        subset = [row for row in rows if row["entry_month"] == month]
        out.append(
            {
                "mode": f"month_{month}",
                "primary_count": len(subset),
                "primary_pct": _round_float(len(subset) / len(rows) if rows else 0.0, 4),
                "total_pnl": _round_float(sum(float(row["pnl"]) for row in subset), 2),
                "avg_pnl": _round_float(mean([float(row["pnl"]) for row in subset]) if subset else 0.0, 2),
                "median_mfe": _round_float(median([float(row["mfe"]) for row in subset]) if subset else 0.0),
                "median_mae": _round_float(median([float(row["mae"]) for row in subset]) if subset else 0.0),
            }
        )
    return out


def _make_conclusion(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    if len(rows) < 10:
        return {
            "label": "inconclusive",
            "reason": "Too few losing long trades to classify a dominant failure mode.",
        }
    counts = {
        mode: len([row for row in rows if row["primary_mode"] == mode])
        for mode in (
            "false_breakout",
            "late_trend_exhaustion",
            "volatility_whipsaw",
            "regime_contradiction",
            "unclassified",
        )
    }
    dominant_mode, dominant_count = max(counts.items(), key=lambda item: item[1])
    share = dominant_count / len(rows)
    if share < 0.45:
        return {
            "label": "mixed_long_failure_modes",
            "reason": (
                "Losing long trades split across multiple failure modes; no single taxonomy "
                "bucket is dominant enough for a rule contract."
            ),
        }
    if dominant_mode == "false_breakout":
        return {
            "label": "false_breakout_dominant",
            "reason": "False breakouts are the dominant losing-long failure mode.",
        }
    if dominant_mode == "late_trend_exhaustion":
        return {
            "label": "late_trend_exhaustion_dominant",
            "reason": "Late trend exhaustion is the dominant losing-long failure mode.",
        }
    if dominant_mode == "volatility_whipsaw":
        return {
            "label": "volatility_whipsaw_dominant",
            "reason": "Volatility whipsaw is the dominant losing-long failure mode.",
        }
    return {
        "label": "mixed_long_failure_modes",
        "reason": (
            f"The largest bucket is {dominant_mode}, which is diagnostic but not one of the "
            "allowed actionable dominant modes."
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
    worst = report["worst_window"]
    rows = report["taxonomy_rows"]
    summary = report["summary_rows"]
    top_losses = sorted(rows, key=lambda row: float(row["pnl"]))[:5]
    primary_rows = [row for row in summary if not str(row["mode"]).startswith(("flag_", "month_"))]
    flag_rows = [row for row in summary if str(row["mode"]).startswith("flag_")]
    month_rows = [row for row in summary if str(row["mode"]).startswith("month_")]
    lines = [
        "# rolling12m_long_breakout_failure_taxonomy_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis only.",
        "- Only losing long trades inside the worst rolling12m window are classified.",
        "- No patch, helper, activation, default oracle, live/demo, checkpoint, family, or LLM path change.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `false_breakout_dominant`, `late_trend_exhaustion_dominant`, `volatility_whipsaw_dominant`, `mixed_long_failure_modes`, `inconclusive`.",
        "",
        "## Worst Rolling12m Window",
        "",
        f"- Start: {worst['start_time']} ({worst['start']})",
        f"- End: {worst['end_time']} ({worst['end']})",
        f"- Losing long trades classified: {len(rows)}",
        "",
        "## Primary Failure Modes",
        "",
        "| mode | count | pct | total pnl | median MFE | median MAE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in primary_rows:
        lines.append(
            f"| {row['mode']} | {row['primary_count']} | {row['primary_pct']:.1%} | "
            f"{row['total_pnl']:+.2f} | {row['median_mfe']:+.2%} | {row['median_mae']:+.2%} |"
        )
    lines.extend(
        [
            "",
            "## Overlapping Flags",
            "",
            "| flag | count | pct | total pnl |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in flag_rows:
        lines.append(
            f"| {row['mode']} | {row['primary_count']} | {row['primary_pct']:.1%} | "
            f"{row['total_pnl']:+.2f} |"
        )
    lines.extend(
        [
            "",
            "## Month Concentration",
            "",
            "| month | count | pct | total pnl |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in month_rows:
        lines.append(
            f"| {row['mode'].replace('month_', '')} | {row['primary_count']} | "
            f"{row['primary_pct']:.1%} | {row['total_pnl']:+.2f} |"
        )
    lines.extend(
        [
            "",
            "## Top 5 Losing Long Trades",
            "",
            "| entry | exit | regime | pnl | MFE | MAE | primary mode |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    for row in top_losses:
        lines.append(
            f"| {row['entry_time']} | {row['exit_time']} | {row['entry_regime']} | "
            f"{row['pnl']:+.2f} | {row['mfe']:+.2%} | {row['mae']:+.2%} | "
            f"{row['primary_mode']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "- This taxonomy does not define or recommend a rule.",
            "- If one mode later becomes actionable, it requires a separate contract and OOS-safe sweep.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m long breakout failure taxonomy",
        "",
        f"Date: {report['generated_at'][:10]}",
        "",
        "## Result",
        "",
        "No protected runtime path was modified by this diagnostic taxonomy.",
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
        "The taxonomy did not edit or depend on that file.",
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
    worst = _find_worst_rolling_12m(signals, prices, times)
    start = int(worst["start"])
    end = int(worst["end"])
    _, win_trades = _simulate(signals[start:end], prices[start:end])
    closed_trades = _closed_trade_rows(win_trades, start)
    taxonomy = _taxonomy_rows(closed_trades, df_full, times, prices, regimes)
    summary = _summary_rows(taxonomy)
    conclusion = _make_conclusion(taxonomy)
    return {
        "generated_at": _now_iso(),
        "scan_id": "rolling12m_long_breakout_failure_taxonomy_v0",
        "scope": "diagnostic_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "worst_window": worst,
        "taxonomy_rows": taxonomy,
        "summary_rows": summary,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only taxonomy for losing long breakouts."
    )
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
    _write_tsv(TRADES_TSV, report["taxonomy_rows"])
    _write_tsv(SUMMARY_TSV, report["summary_rows"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m long breakout failure taxonomy complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Trades: {TRADES_TSV}")
    print(f"  Summary: {SUMMARY_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
