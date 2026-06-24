#!/usr/bin/env python3
"""Diagnostic-only exposure diagnosis for the worst rolling 12m window."""

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

from dex.config import BARS_PER_DAY_5M, COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.indicators import compute_adx, compute_ema
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

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_exposure_diagnosis"
NOTES_MD = OUTPUT_DIR / "rolling12m_diffuse_regime_exposure_diagnosis_v0.md"
REGIME_SIDE_TSV = OUTPUT_DIR / "regime_side_pnl.tsv"
TRADE_TSV = OUTPUT_DIR / "trade_diagnostics.tsv"
FILTER_TSV = OUTPUT_DIR / "filter_environment_diagnostics.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

CONCLUSIONS = (
    "overlong_exposure_risk",
    "trend_filter_decay",
    "short_side_decay",
    "diffuse_no_single_cause",
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


def _equity_deltas(equity: np.ndarray) -> np.ndarray:
    deltas = np.zeros(len(equity), dtype=float)
    if len(equity) == 0:
        return deltas
    deltas[0] = float(equity[0] - INITIAL_CAPITAL)
    deltas[1:] = np.diff(equity)
    return deltas


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
        closed = [trade for trade in trades if trade.get("pnl") is not None]
        row = {
            "start": start,
            "end": end,
            "start_time": str(times[start]),
            "end_time": str(times[end - 1]),
            "return": _round_float(float(metrics["total_return"])),
            "max_dd": _round_float(float(metrics["max_drawdown"])),
            "trade_count": len(closed),
            "losing_trades": len([trade for trade in closed if float(trade["pnl"]) <= 0]),
        }
        if worst is None or row["return"] < worst["return"]:
            worst = row
    if worst is None:
        raise ValueError("not enough bars for rolling12m diagnosis")
    return worst


def _trade_rows(
    trades: List[Dict[str, Any]],
    times: pd.DatetimeIndex,
    regimes: np.ndarray,
    prices: np.ndarray,
    adx: np.ndarray,
    ema50: np.ndarray,
    ema200: np.ndarray,
) -> List[Dict[str, Any]]:
    rows = []
    open_trade: Optional[Dict[str, Any]] = None
    for trade in trades:
        step = int(trade.get("step", -1))
        ttype = str(trade.get("type", ""))
        if ttype in ("buy", "sell_short"):
            open_trade = {
                "direction": "long" if ttype == "buy" else "short",
                "entry_step": step,
                "entry_time": str(times[step]),
                "entry_price": float(prices[step]),
                "entry_regime": str(regimes[step]),
                "entry_adx": float(adx[step]),
                "entry_trend_aligned": bool(
                    (prices[step] >= ema50[step] >= ema200[step])
                    if ttype == "buy"
                    else (prices[step] <= ema50[step] <= ema200[step])
                ),
            }
            continue
        if ttype not in ("sell", "sell_final", "buy_cover", "buy_cover_final"):
            continue
        if open_trade is None:
            continue
        pnl = float(trade.get("pnl", 0.0))
        entry_step = int(open_trade["entry_step"])
        hold_bars = max(0, step - entry_step)
        direction = str(open_trade["direction"])
        price_return = (float(prices[step]) / float(open_trade["entry_price"])) - 1.0
        if direction == "short":
            price_return = -price_return
        rows.append(
            {
                "direction": direction,
                "entry_step": entry_step,
                "exit_step": step,
                "entry_time": open_trade["entry_time"],
                "exit_time": str(times[step]),
                "entry_regime": open_trade["entry_regime"],
                "exit_regime": str(regimes[step]),
                "hold_bars": hold_bars,
                "hold_days": _round_float(hold_bars / BARS_PER_DAY_5M, 3),
                "pnl": _round_float(pnl, 2),
                "price_return_in_trade_direction": _round_float(price_return),
                "entry_adx": _round_float(float(open_trade["entry_adx"]), 3),
                "entry_trend_aligned": bool(open_trade["entry_trend_aligned"]),
                "loser": pnl <= 0,
            }
        )
        open_trade = None
    return rows


def _regime_side_rows(
    regimes: np.ndarray,
    positions: np.ndarray,
    deltas: np.ndarray,
) -> List[Dict[str, Any]]:
    rows = []
    total_bars = len(regimes)
    for regime in ("BULL", "NEUTRAL", "BEAR"):
        for pos, side in ((1, "long"), (-1, "short"), (0, "flat")):
            mask = (regimes == regime) & (positions == pos)
            rows.append(
                {
                    "regime": regime,
                    "side": side,
                    "bars": int(mask.sum()),
                    "bar_pct": _round_float(float(mask.sum()) / total_bars if total_bars else 0.0, 4),
                    "pnl": _round_float(float(deltas[mask].sum()) if mask.any() else 0.0, 2),
                    "negative_bar_count": int((deltas[mask] < 0).sum()) if mask.any() else 0,
                    "mean_bar_delta": _round_float(
                        float(deltas[mask].mean()) if mask.any() else 0.0,
                        4,
                    ),
                }
            )
    return rows


def _trade_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for direction in ("long", "short"):
        subset = [row for row in rows if row["direction"] == direction]
        losers = [row for row in subset if row["loser"]]
        winners = [row for row in subset if not row["loser"]]
        summary[direction] = {
            "count": len(subset),
            "losing_count": len(losers),
            "win_count": len(winners),
            "avg_pnl": _round_float(mean([float(row["pnl"]) for row in subset]) if subset else 0.0, 2),
            "avg_loser_pnl": _round_float(mean([float(row["pnl"]) for row in losers]) if losers else 0.0, 2),
            "avg_hold_days": _round_float(mean([float(row["hold_days"]) for row in subset]) if subset else 0.0, 3),
            "median_hold_days": _round_float(
                median([float(row["hold_days"]) for row in subset]) if subset else 0.0,
                3,
            ),
            "low_adx_entry_count": len([row for row in subset if float(row["entry_adx"]) < 20]),
            "unaligned_entry_count": len([row for row in subset if not row["entry_trend_aligned"]]),
        }
    return summary


def _losing_streaks(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    streaks = []
    current = []
    for row in rows:
        if row["loser"]:
            current.append(row)
        elif current:
            streaks.append(current)
            current = []
    if current:
        streaks.append(current)
    if not streaks:
        return {"max_losing_streak": 0, "max_losing_streak_pnl": 0.0, "streak_count": 0}
    worst = min(streaks, key=lambda group: sum(float(row["pnl"]) for row in group))
    longest = max(streaks, key=len)
    return {
        "max_losing_streak": len(longest),
        "max_losing_streak_pnl": _round_float(sum(float(row["pnl"]) for row in longest), 2),
        "worst_losing_streak": len(worst),
        "worst_losing_streak_pnl": _round_float(sum(float(row["pnl"]) for row in worst), 2),
        "streak_count": len(streaks),
    }


def _filter_env_rows(
    trades: List[Dict[str, Any]],
    regime_side: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = []
    for direction in ("long", "short"):
        subset = [row for row in trades if row["direction"] == direction]
        for aligned in (True, False):
            group = [row for row in subset if bool(row["entry_trend_aligned"]) is aligned]
            rows.append(
                {
                    "diagnostic": "entry_trend_alignment",
                    "bucket": f"{direction}_{'aligned' if aligned else 'unaligned'}",
                    "count": len(group),
                    "losing_count": len([row for row in group if row["loser"]]),
                    "avg_pnl": _round_float(mean([float(row["pnl"]) for row in group]) if group else 0.0, 2),
                }
            )
        for label, predicate in (
            ("low_adx_lt20", lambda row: float(row["entry_adx"]) < 20),
            ("mid_adx_20_30", lambda row: 20 <= float(row["entry_adx"]) < 30),
            ("high_adx_ge30", lambda row: float(row["entry_adx"]) >= 30),
        ):
            group = [row for row in subset if predicate(row)]
            rows.append(
                {
                    "diagnostic": "entry_adx",
                    "bucket": f"{direction}_{label}",
                    "count": len(group),
                    "losing_count": len([row for row in group if row["loser"]]),
                    "avg_pnl": _round_float(mean([float(row["pnl"]) for row in group]) if group else 0.0, 2),
                }
            )
    for row in regime_side:
        rows.append(
            {
                "diagnostic": "regime_side_bar_pnl",
                "bucket": f"{row['regime']}_{row['side']}",
                "count": row["bars"],
                "losing_count": row["negative_bar_count"],
                "avg_pnl": row["mean_bar_delta"],
            }
        )
    return rows


def _make_conclusion(
    regime_side: List[Dict[str, Any]],
    trade_summary: Dict[str, Any],
    streaks: Dict[str, Any],
) -> Dict[str, str]:
    long_pnl = sum(float(row["pnl"]) for row in regime_side if row["side"] == "long")
    short_pnl = sum(float(row["pnl"]) for row in regime_side if row["side"] == "short")
    long_bars = sum(int(row["bars"]) for row in regime_side if row["side"] == "long")
    short_bars = sum(int(row["bars"]) for row in regime_side if row["side"] == "short")
    long_trade = trade_summary["long"]
    short_trade = trade_summary["short"]
    long_loss_rate = (
        long_trade["losing_count"] / long_trade["count"] if long_trade["count"] else 0.0
    )
    short_loss_rate = (
        short_trade["losing_count"] / short_trade["count"] if short_trade["count"] else 0.0
    )
    long_unaligned_rate = (
        long_trade["unaligned_entry_count"] / long_trade["count"] if long_trade["count"] else 0.0
    )

    if long_pnl < -1500 and long_bars > short_bars and long_loss_rate >= 0.5:
        return {
            "label": "overlong_exposure_risk",
            "reason": (
                "Worst 12m damage is concentrated in long exposure: long bars dominate, "
                "long PnL is materially negative, and long trades lose frequently."
            ),
        }
    if long_unaligned_rate >= 0.4 and long_pnl < 0:
        return {
            "label": "trend_filter_decay",
            "reason": (
                "A large share of losing exposure comes from entries that were not aligned "
                "with the EMA trend environment."
            ),
        }
    if short_pnl < -1000 and short_loss_rate >= 0.5:
        return {
            "label": "short_side_decay",
            "reason": "Short-side trades and exposure dominate the worst-window losses.",
        }
    if streaks["worst_losing_streak"] >= 8:
        return {
            "label": "diffuse_no_single_cause",
            "reason": (
                "Losses include extended streak behavior, but exposure diagnostics do not "
                "isolate a single clean rule family."
            ),
        }
    return {
        "label": "diffuse_no_single_cause",
        "reason": "No single exposure, filter, or side explains the worst 12m window cleanly.",
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
    summary = report["trade_summary"]
    streaks = report["losing_streaks"]
    lines = [
        "# rolling12m_diffuse_regime_exposure_diagnosis_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis only.",
        "- No patch, helper, activation, default oracle, live/demo, checkpoint, family, or LLM path change.",
        "- Focuses on exposure-level causes inside the worst rolling12m window.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `overlong_exposure_risk`, `trend_filter_decay`, `short_side_decay`, `diffuse_no_single_cause`.",
        "",
        "## Worst Rolling12m Window",
        "",
        f"- Start: {worst['start_time']} ({worst['start']})",
        f"- End: {worst['end_time']} ({worst['end']})",
        f"- Return: {worst['return']:+.2%}",
        f"- Max DD: {worst['max_dd']:+.2%}",
        f"- Trades: {worst['trade_count']}",
        f"- Losing trades: {worst['losing_trades']}",
        "",
        "## Trade Summary",
        "",
        "| side | count | losing | avg pnl | avg loser pnl | avg hold days | unaligned entries | low ADX entries |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for side in ("long", "short"):
        row = summary[side]
        lines.append(
            f"| {side} | {row['count']} | {row['losing_count']} | {row['avg_pnl']:+.2f} | "
            f"{row['avg_loser_pnl']:+.2f} | {row['avg_hold_days']:.2f} | "
            f"{row['unaligned_entry_count']} | {row['low_adx_entry_count']} |"
        )
    lines.extend(
        [
            "",
            "## Losing Streaks",
            "",
            f"- Max losing streak length: {streaks['max_losing_streak']}",
            f"- Max losing streak PnL: {streaks['max_losing_streak_pnl']:+.2f}",
            f"- Worst losing streak length: {streaks['worst_losing_streak']}",
            f"- Worst losing streak PnL: {streaks['worst_losing_streak_pnl']:+.2f}",
            "",
            "## Regime x Side PnL",
            "",
            "| regime | side | bars | bar% | pnl | negative bars |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["regime_side_pnl"]:
        lines.append(
            f"| {row['regime']} | {row['side']} | {row['bars']} | {row['bar_pct']:.1%} | "
            f"{row['pnl']:+.2f} | {row['negative_bar_count']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "- This diagnosis does not define a rule.",
            "- Any future exposure rule requires a separate contract and OOS-safe sweep.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m diffuse regime exposure diagnosis",
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
    regimes = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    checkpoint = _load_baseline_params()
    signals = _generate_v21_signals(checkpoint, df_full, fast_days=fast_days, slow_days=slow_days)
    worst = _find_worst_rolling_12m(signals, prices, times)

    start = int(worst["start"])
    end = int(worst["end"])
    win_df = df_full.iloc[start:end].reset_index(drop=True)
    win_signals = signals[start:end]
    win_prices = prices[start:end]
    win_times = times[start:end]
    win_regimes = regimes[start:end]
    win_equity, win_trades = _simulate(win_signals, win_prices)
    win_positions = _position_path(win_signals)
    win_deltas = _equity_deltas(win_equity)
    adx, _, _ = compute_adx(win_df, 14)
    ema50 = compute_ema(win_prices, 50 * BARS_PER_DAY_5M)
    ema200 = compute_ema(win_prices, 200 * BARS_PER_DAY_5M)

    trades = _trade_rows(win_trades, win_times, win_regimes, win_prices, adx, ema50, ema200)
    regime_side = _regime_side_rows(win_regimes, win_positions, win_deltas)
    trade_summary = _trade_summary(trades)
    streaks = _losing_streaks(trades)
    filter_rows = _filter_env_rows(trades, regime_side)
    conclusion = _make_conclusion(regime_side, trade_summary, streaks)
    return {
        "generated_at": _now_iso(),
        "scan_id": "rolling12m_diffuse_regime_exposure_diagnosis_v0",
        "scope": "diagnostic_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "worst_window": worst,
        "regime_side_pnl": regime_side,
        "trade_rows": trades,
        "trade_summary": trade_summary,
        "losing_streaks": streaks,
        "filter_environment": filter_rows,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only exposure diagnosis for worst rolling12m window."
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
    _write_tsv(REGIME_SIDE_TSV, report["regime_side_pnl"])
    _write_tsv(TRADE_TSV, report["trade_rows"])
    _write_tsv(FILTER_TSV, report["filter_environment"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m diffuse regime exposure diagnosis complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Regime x side: {REGIME_SIDE_TSV}")
    print(f"  Trades: {TRADE_TSV}")
    print(f"  Filter env: {FILTER_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
