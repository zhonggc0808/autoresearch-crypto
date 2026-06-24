#!/usr/bin/env python3
"""Diagnostic-only counterfactual sweep for broad long exposure throttles."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
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

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_long_counterfactual"
NOTES_MD = OUTPUT_DIR / "rolling12m_long_exposure_counterfactual_sweep_v0.md"
SUMMARY_TSV = OUTPUT_DIR / "long_exposure_counterfactual_summary.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

CONCLUSIONS = ("long_throttle_promising", "too_broad_or_damaging", "inconclusive")


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


def _apply_disable_long_mask(signals: np.ndarray, disable_mask: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    out = np.asarray(signals, dtype=int).copy()
    position = 0
    diag = {
        "long_signals_blocked": 0,
        "long_entries_blocked": 0,
        "long_holds_closed": 0,
        "short_to_long_reversals_blocked": 0,
        "signals_changed": 0,
        "disable_bars": int(np.asarray(disable_mask, dtype=bool).sum()),
    }
    for i, raw_value in enumerate(out):
        raw = int(raw_value)
        sig = raw
        if disable_mask[i]:
            if position == 1 and raw in (1, 2):
                sig = 0
                diag["long_holds_closed"] += 1
            elif raw == 2:
                sig = 0 if position == -1 else 1
                diag["long_entries_blocked"] += 1
                if position == -1:
                    diag["short_to_long_reversals_blocked"] += 1
            if raw == 2 or (position == 1 and raw in (1, 2)):
                diag["long_signals_blocked"] += 1
        if sig != raw:
            diag["signals_changed"] += 1
        out[i] = sig
        position = _target_position(sig, position)
    return out, diag


def _simulate(signals: np.ndarray, prices: np.ndarray) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    return evaluator.simulate(signals, prices)


def _closed_trade_count(trades: List[Dict[str, Any]]) -> int:
    return len([trade for trade in trades if trade.get("pnl") is not None])


def _find_worst_rolling_12m(
    signals: np.ndarray,
    prices: np.ndarray,
    times: pd.DatetimeIndex,
) -> Dict[str, Any]:
    window_bars = 12 * BARS_PER_MONTH
    step = window_bars // 2
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    worst: Optional[Dict[str, Any]] = None
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
            "dd": _round_float(float(metrics["max_drawdown"])),
            "trade_count": _closed_trade_count(trades),
        }
        if worst is None or row["return"] < worst["return"]:
            worst = row
    if worst is None:
        raise ValueError("not enough bars for rolling12m counterfactual")
    return worst


def _baseline_trade_direction(trade_type: str) -> Optional[str]:
    if trade_type in ("sell", "sell_final"):
        return "long"
    if trade_type in ("buy_cover", "buy_cover_final"):
        return "short"
    return None


def _apply_after_3_long_losses(signals: np.ndarray, prices: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    out = np.asarray(signals, dtype=int).copy()
    position = 0
    capital = 10_000.0
    shares = 0.0
    entry_cost_basis = 0.0
    long_loss_streak = 0
    diag = {
        "long_signals_blocked": 0,
        "long_entries_blocked": 0,
        "long_holds_closed": 0,
        "short_to_long_reversals_blocked": 0,
        "signals_changed": 0,
        "disable_bars": 0,
        "max_long_loss_streak": 0,
    }

    for i, raw_value in enumerate(out):
        raw = int(raw_value)
        sig = raw
        disabled = long_loss_streak >= 3
        if disabled:
            diag["disable_bars"] += 1
            if position == 1 and raw in (1, 2):
                sig = 0
                diag["long_holds_closed"] += 1
            elif raw == 2:
                sig = 0 if position == -1 else 1
                diag["long_entries_blocked"] += 1
                if position == -1:
                    diag["short_to_long_reversals_blocked"] += 1
            if raw == 2 or (position == 1 and raw in (1, 2)):
                diag["long_signals_blocked"] += 1

        if sig != raw:
            diag["signals_changed"] += 1
        out[i] = sig

        price = float(prices[i])
        target = _target_position(sig, position)
        if target != position:
            if position == 1 and target <= 0:
                exec_price = price * (1 - SLIPPAGE)
                gross = shares * exec_price
                cost = gross * COMMISSION
                capital = gross - cost
                pnl = capital - entry_cost_basis
                long_loss_streak = long_loss_streak + 1 if pnl <= 0 else 0
                diag["max_long_loss_streak"] = max(diag["max_long_loss_streak"], long_loss_streak)
                shares = 0.0
                position = 0
            elif position == -1 and target >= 0:
                exec_price = price * (1 + SLIPPAGE)
                buy_cost = abs(shares) * exec_price
                buy_cost_total = buy_cost * (1 + COMMISSION)
                pnl = entry_cost_basis - buy_cost_total
                capital = max(0.0, capital + pnl)
                shares = 0.0
                position = 0

            if target == 1 and position == 0 and capital > 0:
                exec_price = price * (1 + SLIPPAGE)
                shares = capital * (1 - COMMISSION) / exec_price
                entry_cost_basis = capital
                capital = 0.0
                position = 1
            elif target == -1 and position == 0 and capital > 0:
                exec_price = price * (1 - SLIPPAGE)
                shares = -(capital * (1 - COMMISSION) / exec_price)
                entry_cost_basis = capital
                capital = capital * (1 - COMMISSION)
                position = -1

    return out, diag


def _regime_contradiction_mask(
    df: pd.DataFrame,
    regimes: np.ndarray,
    prices: np.ndarray,
) -> np.ndarray:
    ema50 = compute_ema(prices, 50 * BARS_PER_DAY_5M)
    ema200 = compute_ema(prices, 200 * BARS_PER_DAY_5M)
    mask = regimes == "BEAR"
    slope = np.zeros(len(prices), dtype=float)
    lookback = 5 * BARS_PER_DAY_5M
    valid = np.arange(len(prices)) >= lookback
    prev = ema50[:-lookback]
    curr = ema50[lookback:]
    slope[lookback:] = np.divide(curr, prev, out=np.ones_like(curr), where=prev > 0) - 1.0
    close = np.asarray(df["close"], dtype=float)
    pre14 = np.zeros(len(prices), dtype=float)
    lb14 = 14 * BARS_PER_DAY_5M
    prev14 = close[:-lb14]
    curr14 = close[lb14:]
    pre14[lb14:] = np.divide(curr14, prev14, out=np.ones_like(curr14), where=prev14 > 0) - 1.0
    bull_weak = (regimes == "BULL") & (valid | (np.arange(len(prices)) >= lb14)) & (
        (slope < 0) | (pre14 < 0)
    )
    below_ema200 = prices < ema200
    return mask | bull_weak | below_ema200


def _evaluate_candidate(
    candidate_id: str,
    signals: np.ndarray,
    diagnostics: Dict[str, Any],
    prices: np.ndarray,
    times: pd.DatetimeIndex,
    split_idx: int,
    baseline_worst: Dict[str, Any],
    baseline_oos_safe: Dict[str, Any],
) -> Dict[str, Any]:
    worst = _find_worst_rolling_12m(signals, prices, times)
    oos_safe = _evaluate_signals(_safe_execution_signals(signals)[split_idx:], prices[split_idx:])
    equity, trades = _simulate(signals, prices)
    return {
        "candidate_id": candidate_id,
        "supported": True,
        "worst12m_return": worst["return"],
        "worst12m_dd": worst["dd"],
        "worst12m_trade_count": worst["trade_count"],
        "worst12m_start": worst["start"],
        "worst12m_start_time": worst["start_time"],
        "worst12m_return_delta": _round_float(worst["return"] - baseline_worst["return"]),
        "worst12m_dd_delta": _round_float(worst["dd"] - baseline_worst["dd"]),
        "oos_safe_return": _round_float(float(oos_safe["return"])),
        "oos_safe_return_delta": _round_float(float(oos_safe["return"]) - float(baseline_oos_safe["return"])),
        "total_return": _round_float((float(equity[-1]) / float(equity[0])) - 1.0),
        "trade_count_total": _closed_trade_count(trades),
        **diagnostics,
    }


def _unsupported_row(candidate_id: str, reason: str) -> Dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "supported": False,
        "reason": reason,
        "worst12m_return": None,
        "worst12m_dd": None,
        "worst12m_trade_count": None,
        "worst12m_start": None,
        "worst12m_start_time": None,
        "worst12m_return_delta": None,
        "worst12m_dd_delta": None,
        "oos_safe_return": None,
        "oos_safe_return_delta": None,
        "total_return": None,
        "trade_count_total": None,
        "long_signals_blocked": None,
        "long_entries_blocked": None,
        "long_holds_closed": None,
        "short_to_long_reversals_blocked": None,
        "signals_changed": None,
        "disable_bars": None,
    }


def _make_conclusion(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    supported = [row for row in rows if row.get("supported")]
    if not supported:
        return {"label": "inconclusive", "reason": "No supported counterfactuals were evaluated."}
    promising = [
        row
        for row in supported
        if float(row["worst12m_return_delta"]) >= 0.03
        and float(row["worst12m_dd_delta"]) >= 0.03
        and float(row["oos_safe_return_delta"]) > -0.15
        and int(row["long_signals_blocked"]) < 20_000
    ]
    if promising:
        return {
            "label": "long_throttle_promising",
            "reason": (
                "At least one broad long-throttle counterfactual materially improves worst12m "
                "without obvious OOS damage or excessive blocking."
            ),
        }
    damaging = [
        row
        for row in supported
        if float(row["oos_safe_return_delta"]) <= -0.15 or int(row["long_signals_blocked"]) >= 20_000
    ]
    if damaging:
        return {
            "label": "too_broad_or_damaging",
            "reason": (
                "Broad long throttles either damage OOS safe return or block too much long exposure "
                "for a local rule direction."
            ),
        }
    return {
        "label": "inconclusive",
        "reason": "Counterfactuals are directionally mixed and do not justify a rule contract.",
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
    baseline = report["baseline"]
    lines = [
        "# rolling12m_long_exposure_counterfactual_sweep_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis-only shadow counterfactual sweep.",
        "- No patch, helper, activation, default oracle, live/demo, checkpoint, family, or LLM path change.",
        "- Tests whether broad long exposure throttles point in the right direction.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `long_throttle_promising`, `too_broad_or_damaging`, `inconclusive`.",
        "",
        "## Baseline",
        "",
        f"- Worst12m return: {baseline['worst12m_return']:+.2%}",
        f"- Worst12m DD: {baseline['worst12m_dd']:+.2%}",
        f"- OOS safe return: {baseline['oos_safe_return']:+.2%}",
        "",
        "## Counterfactuals",
        "",
        "| candidate | supported | worst12m return | worst12m delta | DD delta | OOS safe delta | long signals blocked |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        if not row.get("supported"):
            lines.append(f"| {row['candidate_id']} | false |  |  |  |  |  |")
            continue
        lines.append(
            f"| {row['candidate_id']} | true | {row['worst12m_return']:+.2%} | "
            f"{row['worst12m_return_delta']:+.2%} | {row['worst12m_dd_delta']:+.2%} | "
            f"{row['oos_safe_return_delta']:+.2%} | {row['long_signals_blocked']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "- This sweep is not a patch selection.",
            "- `disable_all_longs_in_worst12m_only` is a diagnostic upper bound and is not implementable.",
            "- Any promising direction still requires a separate, narrow contract before implementation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m long exposure counterfactual sweep",
        "",
        f"Date: {report['generated_at'][:10]}",
        "",
        "## Result",
        "",
        "No protected runtime path was modified by this diagnostic sweep.",
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
        "The sweep did not edit or depend on that file.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    times = _datetimes(df_full)
    prices = df_full["close"].values.astype(float)
    regimes = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    checkpoint = _load_baseline_params()
    baseline_signals = _generate_v21_signals(
        checkpoint,
        df_full,
        fast_days=fast_days,
        slow_days=slow_days,
    )
    baseline_worst = _find_worst_rolling_12m(baseline_signals, prices, times)
    baseline_oos_safe = _evaluate_signals(
        _safe_execution_signals(baseline_signals)[split_idx:],
        prices[split_idx:],
    )

    rows: List[Dict[str, Any]] = []
    worst_mask = np.zeros(len(baseline_signals), dtype=bool)
    worst_mask[int(baseline_worst["start"]):int(baseline_worst["end"])] = True
    bear_mask = regimes == "BEAR"
    contradiction_mask = _regime_contradiction_mask(df_full, regimes, prices)

    for candidate_id, mask in (
        ("disable_all_longs_in_worst12m_only", worst_mask),
        ("disable_longs_in_BEAR", bear_mask),
        ("disable_longs_when_regime_contradiction_flag", contradiction_mask),
    ):
        signals, diag = _apply_disable_long_mask(baseline_signals, mask)
        rows.append(
            _evaluate_candidate(
                candidate_id,
                signals,
                diag,
                prices,
                times,
                split_idx,
                baseline_worst,
                baseline_oos_safe,
            )
        )

    streak_signals, streak_diag = _apply_after_3_long_losses(baseline_signals, prices)
    rows.append(
        _evaluate_candidate(
            "disable_longs_after_3_consecutive_long_losses",
            streak_signals,
            streak_diag,
            prices,
            times,
            split_idx,
            baseline_worst,
            baseline_oos_safe,
        )
    )
    rows.append(
        _unsupported_row(
            "long_size_half_after_3_consecutive_long_losses",
            "Signal evaluator has no sizing multiplier; skipped per scope.",
        )
    )

    conclusion = _make_conclusion(rows)
    return {
        "generated_at": _now_iso(),
        "sweep_id": "rolling12m_long_exposure_counterfactual_sweep_v0",
        "scope": "diagnostic_only_shadow_counterfactuals",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "baseline": {
            "worst12m_return": baseline_worst["return"],
            "worst12m_dd": baseline_worst["dd"],
            "worst12m_start": baseline_worst["start"],
            "worst12m_start_time": baseline_worst["start_time"],
            "oos_safe_return": _round_float(float(baseline_oos_safe["return"])),
        },
        "rows": rows,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only long exposure counterfactual sweep."
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
    _write_tsv(SUMMARY_TSV, report["rows"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m long exposure counterfactual sweep complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Summary: {SUMMARY_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
