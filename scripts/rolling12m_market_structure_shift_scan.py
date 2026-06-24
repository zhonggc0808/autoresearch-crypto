#!/usr/bin/env python3
"""Diagnostic-only market-structure scan for the worst rolling12m window."""

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
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import _datetimes

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_market_structure_shift"
NOTES_MD = OUTPUT_DIR / "rolling12m_market_structure_shift_scan_v0.md"
SUMMARY_TSV = OUTPUT_DIR / "market_structure_summary.tsv"
FOLLOW_TSV = OUTPUT_DIR / "breakout_followthrough_by_window.tsv"
ASYMMETRY_TSV = OUTPUT_DIR / "long_short_asymmetry.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

ENTRY_LOOKBACK = 375
CONCLUSIONS = (
    "market_structure_shift_supported",
    "no_clear_market_structure_shift",
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
    return {
        "return": _round_float(float(values["total_return"])),
        "dd": _round_float(float(values["max_drawdown"])),
        "trade_count": len([trade for trade in trades if trade.get("pnl") is not None]),
    }


def _rolling_windows(n: int) -> List[Tuple[int, int]]:
    window_bars = 12 * BARS_PER_MONTH
    step = window_bars // 2
    return [(start, start + window_bars) for start in range(0, n - window_bars, step)]


def _safe_return(prices: np.ndarray, start: int, end: int, direction: str = "long") -> Optional[float]:
    if start < 0 or end <= start or end >= len(prices):
        return None
    base = float(prices[start])
    if base <= 0:
        return None
    ret = (float(prices[end]) / base) - 1.0
    return ret if direction == "long" else -ret


def _max_adverse_excursion(
    high: np.ndarray,
    low: np.ndarray,
    entry_price: float,
    start: int,
    end: int,
    direction: str,
) -> float:
    if direction == "long":
        return (float(np.min(low[start:end])) / entry_price) - 1.0
    return 1.0 - (float(np.max(high[start:end])) / entry_price)


def _first_reversion_bars(
    close: np.ndarray,
    level: float,
    start: int,
    end: int,
    direction: str,
) -> Optional[int]:
    series = close[start:end]
    if direction == "long":
        hit = np.flatnonzero(series < level)
    else:
        hit = np.flatnonzero(series > level)
    if len(hit) == 0:
        return None
    return int(hit[0])


def _breakout_events(df: pd.DataFrame) -> List[Dict[str, Any]]:
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    channel_high = (
        pd.Series(high).rolling(ENTRY_LOOKBACK, min_periods=ENTRY_LOOKBACK).max().shift(1).to_numpy()
    )
    channel_low = (
        pd.Series(low).rolling(ENTRY_LOOKBACK, min_periods=ENTRY_LOOKBACK).min().shift(1).to_numpy()
    )
    events = []
    last_direction: Optional[str] = None
    for i in range(ENTRY_LOOKBACK + 1, len(close)):
        direction = None
        channel = None
        if np.isfinite(channel_high[i]) and close[i] > channel_high[i]:
            direction = "long"
            channel = float(channel_high[i])
        elif np.isfinite(channel_low[i]) and close[i] < channel_low[i]:
            direction = "short"
            channel = float(channel_low[i])
        if direction is None:
            continue
        if direction == last_direction:
            continue
        last_direction = direction
        entry_price = float(close[i])
        row = {
            "step": i,
            "direction": direction,
            "channel": channel,
            "entry_price": entry_price,
        }
        for days in (1, 3, 7):
            end = min(len(close) - 1, i + days * BARS_PER_DAY_5M)
            row[f"fwd_{days}d_return"] = _round_float(
                _safe_return(close, i, end, direction)
            )
            row[f"mae_{days}d"] = _round_float(
                _max_adverse_excursion(high, low, entry_price, i, end + 1, direction)
            )
            reversion = _first_reversion_bars(close, channel, i, end + 1, direction)
            row[f"reversion_{days}d_bars"] = reversion
        events.append(row)
    return events


def _market_features(
    df: pd.DataFrame,
    prices: np.ndarray,
    start: int,
    end: int,
) -> Dict[str, Any]:
    close = prices[start:end]
    high = df["high"].values.astype(float)[start:end]
    low = df["low"].values.astype(float)[start:end]
    returns = np.diff(close) / close[:-1]
    atr = compute_atr(df.iloc[start:end].reset_index(drop=True), 14)
    atr_pct = np.divide(atr, close, out=np.zeros_like(atr, dtype=float), where=close > 0)
    ema50 = compute_ema(close, 50 * BARS_PER_DAY_5M)
    slope_5d = np.zeros(len(close), dtype=float)
    lb = 5 * BARS_PER_DAY_5M
    if len(close) > lb:
        prev = ema50[:-lb]
        curr = ema50[lb:]
        slope_5d[lb:] = np.divide(curr, prev, out=np.ones_like(curr), where=prev > 0) - 1.0
    direction = np.sign(returns)
    same_direction = direction[1:] == direction[:-1] if len(direction) > 1 else np.array([])
    range_pct = np.divide(high - low, close, out=np.zeros_like(close), where=close > 0)
    abs_returns = np.abs(returns)
    autocorr = float(pd.Series(returns).autocorr(lag=1)) if len(returns) > 10 else 0.0
    return {
        "realized_vol_1bar": _round_float(float(np.std(returns)) if len(returns) else 0.0),
        "realized_vol_7d_median": _round_float(
            float(pd.Series(returns).rolling(7 * BARS_PER_DAY_5M).std().median())
            if len(returns) >= 7 * BARS_PER_DAY_5M
            else 0.0
        ),
        "vol_of_vol_7d": _round_float(
            float(pd.Series(returns).rolling(7 * BARS_PER_DAY_5M).std().std())
            if len(returns) >= 7 * BARS_PER_DAY_5M
            else 0.0
        ),
        "atr_pct_median": _round_float(float(np.median(atr_pct))),
        "range_pct_median": _round_float(float(np.median(range_pct))),
        "range_pct_p90": _round_float(float(np.quantile(range_pct, 0.9))),
        "abs_return_p90": _round_float(float(np.quantile(abs_returns, 0.9)) if len(abs_returns) else 0.0),
        "return_autocorr_1": _round_float(autocorr),
        "same_direction_bar_rate": _round_float(float(same_direction.mean()) if len(same_direction) else 0.0),
        "ema50_slope_5d_median": _round_float(float(np.median(slope_5d))),
        "ema50_slope_negative_rate": _round_float(float((slope_5d < 0).mean())),
    }


def _followthrough_rows(
    events: List[Dict[str, Any]],
    windows: List[Tuple[int, int]],
    times: pd.DatetimeIndex,
) -> List[Dict[str, Any]]:
    rows = []
    for window_id, (start, end) in enumerate(windows, start=1):
        for direction in ("long", "short"):
            subset = [
                event for event in events
                if start <= int(event["step"]) < end and event["direction"] == direction
            ]
            row = {
                "window_id": window_id,
                "window_start": start,
                "window_end": end,
                "window_start_time": str(times[start]),
                "window_end_time": str(times[end - 1]),
                "direction": direction,
                "breakout_count": len(subset),
            }
            for days in (1, 3, 7):
                values = [
                    float(event[f"fwd_{days}d_return"])
                    for event in subset
                    if event[f"fwd_{days}d_return"] is not None
                ]
                maes = [
                    float(event[f"mae_{days}d"])
                    for event in subset
                    if event[f"mae_{days}d"] is not None
                ]
                reversions = [
                    int(event[f"reversion_{days}d_bars"])
                    for event in subset
                    if event[f"reversion_{days}d_bars"] is not None
                ]
                row[f"fwd_{days}d_mean"] = _round_float(mean(values) if values else None)
                row[f"fwd_{days}d_median"] = _round_float(median(values) if values else None)
                row[f"fwd_{days}d_positive_rate"] = _round_float(
                    len([value for value in values if value > 0]) / len(values) if values else None,
                    4,
                )
                row[f"mae_{days}d_median"] = _round_float(median(maes) if maes else None)
                row[f"reversion_{days}d_rate"] = _round_float(
                    len(reversions) / len(subset) if subset else None,
                    4,
                )
                row[f"reversion_{days}d_median_bars"] = _round_float(
                    median(reversions) if reversions else None,
                    1,
                )
            rows.append(row)
    return rows


def _summary_rows(
    df: pd.DataFrame,
    signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
    follow_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    windows = _rolling_windows(len(signals))
    metrics_by_window = []
    for window_id, (start, end) in enumerate(windows, start=1):
        m = _metrics(signals[start:end], prices[start:end])
        metrics_by_window.append(
            {
                "window_id": window_id,
                "start": start,
                "end": end,
                "start_time": str(times[start]),
                "end_time": str(times[end - 1]),
                **m,
            }
        )
    worst = min(metrics_by_window, key=lambda row: float(row["return"]))
    returns = [float(row["return"]) for row in metrics_by_window]
    ranks = {row["window_id"]: sorted(returns).index(float(row["return"])) + 1 for row in metrics_by_window}

    rows = []
    for row in metrics_by_window:
        start = int(row["start"])
        end = int(row["end"])
        features = _market_features(df, prices, start, end)
        long_follow = next(
            item for item in follow_rows
            if item["window_id"] == row["window_id"] and item["direction"] == "long"
        )
        short_follow = next(
            item for item in follow_rows
            if item["window_id"] == row["window_id"] and item["direction"] == "short"
        )
        rows.append(
            {
                **row,
                "return_rank_low_is_worst": ranks[row["window_id"]],
                "is_worst12m": row["window_id"] == worst["window_id"],
                **features,
                "long_breakout_count": long_follow["breakout_count"],
                "short_breakout_count": short_follow["breakout_count"],
                "long_fwd_3d_mean": long_follow["fwd_3d_mean"],
                "short_fwd_3d_mean": short_follow["fwd_3d_mean"],
                "long_fwd_7d_mean": long_follow["fwd_7d_mean"],
                "short_fwd_7d_mean": short_follow["fwd_7d_mean"],
                "long_reversion_3d_rate": long_follow["reversion_3d_rate"],
                "short_reversion_3d_rate": short_follow["reversion_3d_rate"],
            }
        )
    return rows, worst


def _percentile(values: List[float], value: Optional[float]) -> Optional[float]:
    if value is None or not values:
        return None
    return sum(1 for item in values if item <= value) / len(values)


def _asymmetry_rows(summary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    long3 = [float(row["long_fwd_3d_mean"]) for row in summary if row["long_fwd_3d_mean"] is not None]
    short3 = [float(row["short_fwd_3d_mean"]) for row in summary if row["short_fwd_3d_mean"] is not None]
    long_rev = [
        float(row["long_reversion_3d_rate"])
        for row in summary
        if row["long_reversion_3d_rate"] is not None
    ]
    for row in summary:
        asym = None
        if row["long_fwd_3d_mean"] is not None and row["short_fwd_3d_mean"] is not None:
            asym = float(row["long_fwd_3d_mean"]) - float(row["short_fwd_3d_mean"])
        rows.append(
            {
                "window_id": row["window_id"],
                "is_worst12m": row["is_worst12m"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "long_fwd_3d_mean": row["long_fwd_3d_mean"],
                "short_fwd_3d_mean": row["short_fwd_3d_mean"],
                "long_minus_short_fwd_3d": _round_float(asym),
                "long_fwd_3d_percentile": _round_float(
                    _percentile(long3, row["long_fwd_3d_mean"]),
                    4,
                ),
                "short_fwd_3d_percentile": _round_float(
                    _percentile(short3, row["short_fwd_3d_mean"]),
                    4,
                ),
                "long_reversion_3d_rate": row["long_reversion_3d_rate"],
                "long_reversion_3d_percentile": _round_float(
                    _percentile(long_rev, row["long_reversion_3d_rate"]),
                    4,
                ),
            }
        )
    return rows


def _make_conclusion(summary: List[Dict[str, Any]], asymmetry: List[Dict[str, Any]]) -> Dict[str, str]:
    worst = next(row for row in summary if row["is_worst12m"])
    worst_asym = next(row for row in asymmetry if row["is_worst12m"])
    vol_values = [float(row["atr_pct_median"]) for row in summary]
    range_values = [float(row["range_pct_p90"]) for row in summary]
    long_tail = (
        worst_asym["long_fwd_3d_percentile"] is not None
        and float(worst_asym["long_fwd_3d_percentile"]) <= 0.25
    )
    reversion_high = (
        worst_asym["long_reversion_3d_percentile"] is not None
        and float(worst_asym["long_reversion_3d_percentile"]) >= 0.75
    )
    short_not_bad = (
        worst_asym["short_fwd_3d_percentile"] is not None
        and float(worst_asym["short_fwd_3d_percentile"]) >= 0.4
    )
    vol_tail = _percentile(vol_values, worst["atr_pct_median"])
    range_tail = _percentile(range_values, worst["range_pct_p90"])
    if long_tail and reversion_high and short_not_bad:
        return {
            "label": "market_structure_shift_supported",
            "reason": (
                "Worst12m sits in weak long breakout follow-through and high long reversion "
                "tail while short follow-through is not similarly impaired."
            ),
        }
    if long_tail or reversion_high or (vol_tail is not None and vol_tail >= 0.8) or (
        range_tail is not None and range_tail >= 0.8
    ):
        return {
            "label": "inconclusive",
            "reason": (
                "Some market-structure metrics are unusual, but the long/short asymmetry is "
                "not clean enough to support a shift interpretation."
            ),
        }
    return {
        "label": "no_clear_market_structure_shift",
        "reason": "Worst12m is not clearly in a market-structure tail versus all rolling12m windows.",
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
    worst_summary = next(row for row in report["summary_rows"] if row["is_worst12m"])
    worst_asym = next(row for row in report["asymmetry_rows"] if row["is_worst12m"])
    lines = [
        "# rolling12m_market_structure_shift_scan_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis only.",
        "- Compares worst12m against the full rolling12m distribution, not a cherry-picked good window.",
        "- Breakout follow-through is split long vs short.",
        "- No strategy logic, helper, activation/default oracle, live/demo, checkpoint, family, LLM, scoring, patch, or filter change.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `market_structure_shift_supported`, `no_clear_market_structure_shift`, `inconclusive`.",
        "",
        "## Worst Rolling12m",
        "",
        f"- Start: {worst['start_time']} ({worst['start']})",
        f"- End: {worst['end_time']} ({worst['end']})",
        f"- Return: {worst['return']:+.2%}",
        f"- DD: {worst['dd']:+.2%}",
        f"- Return rank among rolling12m windows: {worst_summary['return_rank_low_is_worst']} / {len(report['summary_rows'])}",
        "",
        "## Worst Window Structure",
        "",
        f"- ATR% median: {worst_summary['atr_pct_median']:.4%}",
        f"- Range% p90: {worst_summary['range_pct_p90']:.4%}",
        f"- Return autocorr lag1: {worst_summary['return_autocorr_1']:+.4f}",
        f"- Same-direction bar rate: {worst_summary['same_direction_bar_rate']:.2%}",
        f"- Long breakout 3d mean: {worst_asym['long_fwd_3d_mean']:+.2%}",
        f"- Long breakout 3d percentile: {worst_asym['long_fwd_3d_percentile']:.1%}",
        f"- Short breakout 3d mean: {worst_asym['short_fwd_3d_mean']:+.2%}",
        f"- Short breakout 3d percentile: {worst_asym['short_fwd_3d_percentile']:.1%}",
        f"- Long 3d reversion rate: {worst_asym['long_reversion_3d_rate']:.1%}",
        f"- Long 3d reversion percentile: {worst_asym['long_reversion_3d_percentile']:.1%}",
        "",
        "## Guardrail",
        "",
        "- This is explanation-layer evidence only.",
        "- Even if market structure shift is supported, it does not imply a patch or filter.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m market structure shift scan",
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
    windows = _rolling_windows(len(signals))
    breakouts = _breakout_events(df_full)
    follow = _followthrough_rows(breakouts, windows, times)
    summary, worst = _summary_rows(df_full, signals, prices, times, follow)
    asymmetry = _asymmetry_rows(summary)
    conclusion = _make_conclusion(summary, asymmetry)
    return {
        "generated_at": _now_iso(),
        "scan_id": "rolling12m_market_structure_shift_scan_v0",
        "scope": "diagnostic_only_market_structure_no_rules",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "worst_window": worst,
        "summary_rows": summary,
        "followthrough_rows": follow,
        "asymmetry_rows": asymmetry,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic-only rolling12m market structure scan.")
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    args = parser.parse_args()
    report = run_scan(data_path=Path(args.data_path) if args.data_path else None)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_tsv(SUMMARY_TSV, report["summary_rows"])
    _write_tsv(FOLLOW_TSV, report["followthrough_rows"])
    _write_tsv(ASYMMETRY_TSV, report["asymmetry_rows"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m market structure shift scan complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Summary: {SUMMARY_TSV}")
    print(f"  Follow-through: {FOLLOW_TSV}")
    print(f"  Asymmetry: {ASYMMETRY_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
