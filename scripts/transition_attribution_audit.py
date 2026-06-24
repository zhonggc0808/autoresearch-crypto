#!/usr/bin/env python3
"""Diagnostic-only attribution audit for daily EMA regime transitions.

This script does not define or apply any new filter, gate, position scaler, or
cooldown rule. It replays the frozen v2.1 baseline signal path, marks BULL /
BEAR / NEUTRAL regime transitions, and attributes baseline equity/trade
behavior inside fixed post-transition windows.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    _safe_execution_signals,
)

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "transition_audit"
REPORT_JSON = OUTPUT_DIR / "transition_audit_report.json"
SUMMARY_TSV = OUTPUT_DIR / "transition_audit_summary.tsv"
NOTES_MD = OUTPUT_DIR / "transition_audit_notes.md"

TRANSITIONS = (
    "BULL->NEUTRAL",
    "NEUTRAL->BULL",
    "BEAR->NEUTRAL",
    "NEUTRAL->BEAR",
    "BULL->BEAR",
    "BEAR->BULL",
)

HORIZON_DAYS = (1, 3, 7, 14)
ROLLING_MONTHS = (6, 12)


@dataclass
class TransitionEvent:
    step: int
    time: str
    from_regime: str
    to_regime: str
    transition: str


@dataclass
class WindowStats:
    transition: str
    horizon: str
    horizon_days: int
    transition_count: int = 0
    bars_total: int = 0
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    returns: List[float] = field(default_factory=list)
    equity_delta: float = 0.0
    window_dds: List[float] = field(default_factory=list)
    position_bars: Dict[str, int] = field(
        default_factory=lambda: {"long": 0, "short": 0, "flat": 0}
    )
    direction_equity_delta: Dict[str, float] = field(
        default_factory=lambda: {"long": 0.0, "short": 0.0, "flat": 0.0}
    )
    opened: Dict[str, int] = field(default_factory=lambda: {"long": 0, "short": 0})
    closed: Dict[str, int] = field(default_factory=lambda: {"long": 0, "short": 0})
    closed_pnl: Dict[str, float] = field(default_factory=lambda: {"long": 0.0, "short": 0.0})
    rolling_overlap_bars: Dict[str, int] = field(
        default_factory=lambda: {"6m": 0, "12m": 0}
    )
    transition_starts_in_worst: Dict[str, int] = field(
        default_factory=lambda: {"6m": 0, "12m": 0}
    )
    max_dd_overlap_bars: int = 0
    transition_starts_in_max_dd: int = 0
    samples: List[Dict[str, Any]] = field(default_factory=list)

    def add_sample(self, sample: Dict[str, Any]) -> None:
        self.samples.append(sample)
        self.samples.sort(key=lambda s: s["return"])
        del self.samples[10:]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _datetimes(df: pd.DataFrame) -> pd.DatetimeIndex:
    if "datetime" in df.columns:
        return pd.DatetimeIndex(pd.to_datetime(df["datetime"], errors="coerce"))
    if "timestamp" in df.columns:
        raw = df["timestamp"]
        if pd.api.types.is_numeric_dtype(raw):
            unit = "ms" if float(np.nanmax(np.abs(raw.to_numpy(dtype=float)))) > 10_000_000_000 else "s"
            return pd.DatetimeIndex(pd.to_datetime(raw, unit=unit, errors="coerce"))
        return pd.DatetimeIndex(pd.to_datetime(raw, errors="coerce"))
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index
    raise ValueError("df must contain datetime/timestamp column or DatetimeIndex")


def _position_path(signals: np.ndarray) -> np.ndarray:
    """Return post-signal position for each bar: 1 long, -1 short, 0 flat."""
    out = np.zeros(len(signals), dtype=np.int8)
    position = 0
    for i, raw in enumerate(signals):
        sig = int(raw)
        if sig == 2:
            position = 1
        elif sig == 3:
            position = -1
        elif sig == 0:
            position = 0
        out[i] = position
    return out


def _transition_events(regimes: np.ndarray, times: pd.DatetimeIndex) -> List[TransitionEvent]:
    events: List[TransitionEvent] = []
    labels = np.asarray(regimes, dtype=object)
    for i in range(1, len(labels)):
        prev = str(labels[i - 1])
        curr = str(labels[i])
        if prev == curr:
            continue
        transition = f"{prev}->{curr}"
        if transition not in TRANSITIONS:
            continue
        events.append(
            TransitionEvent(
                step=i,
                time=str(times[i]),
                from_regime=prev,
                to_regime=curr,
                transition=transition,
            )
        )
    return events


def _target_position_from_signal(signal: int, current: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return current


def _trade_events(signals: np.ndarray, evaluator_trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize StrategyEvaluator trade events into open/close events."""
    normalized = []
    for trade in evaluator_trades:
        ttype = str(trade.get("type", ""))
        step = int(trade.get("step", -1))
        if ttype == "buy":
            normalized.append({"step": step, "kind": "open", "direction": "long", "pnl": None})
        elif ttype == "sell_short":
            normalized.append({"step": step, "kind": "open", "direction": "short", "pnl": None})
        elif ttype in ("sell", "sell_final"):
            normalized.append(
                {
                    "step": step,
                    "kind": "close",
                    "direction": "long",
                    "pnl": float(trade.get("pnl", 0.0)),
                }
            )
        elif ttype in ("buy_cover", "buy_cover_final"):
            normalized.append(
                {
                    "step": step,
                    "kind": "close",
                    "direction": "short",
                    "pnl": float(trade.get("pnl", 0.0)),
                }
            )
    return normalized


def _bar_equity_deltas(equity: np.ndarray) -> np.ndarray:
    deltas = np.zeros(len(equity), dtype=float)
    if len(equity) == 0:
        return deltas
    deltas[0] = float(equity[0] - INITIAL_CAPITAL)
    deltas[1:] = np.diff(equity)
    return deltas


def _window_return_and_dd(equity: np.ndarray, start: int, end: int) -> Tuple[float, float, float]:
    pre_equity = float(equity[start - 1]) if start > 0 else INITIAL_CAPITAL
    end_equity = float(equity[end - 1])
    ret = (end_equity / pre_equity) - 1.0 if pre_equity > 0 else 0.0

    curve = np.concatenate(([pre_equity], equity[start:end].astype(float)))
    peak = curve[0]
    max_dd = 0.0
    for val in curve:
        if val > peak:
            peak = val
        dd = (val / peak) - 1.0 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
    return ret, max_dd, end_equity - pre_equity


def _find_global_max_dd(equity: np.ndarray) -> Dict[str, Any]:
    peak_value = float(equity[0])
    peak_idx = 0
    trough_idx = 0
    max_dd = 0.0
    dd_start = 0
    for i, val in enumerate(equity):
        val_f = float(val)
        if val_f > peak_value:
            peak_value = val_f
            peak_idx = i
        dd = (val_f / peak_value) - 1.0 if peak_value > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            trough_idx = i
            dd_start = peak_idx
    return {"start": dd_start, "end": trough_idx + 1, "dd": max_dd}


def _find_worst_rolling_windows(
    signals: np.ndarray,
    prices: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
) -> Dict[str, Dict[str, Any]]:
    windows: Dict[str, Dict[str, Any]] = {}
    for months in ROLLING_MONTHS:
        window_bars = months * 30 * BARS_PER_DAY_5M
        step = window_bars // 2
        key = f"{months}m"
        worst: Optional[Dict[str, Any]] = None
        if window_bars >= len(signals):
            continue
        for start in range(0, len(signals) - window_bars, step):
            end = start + window_bars
            ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
            _, metrics, _ = ev.evaluate(signals[start:end], prices[start:end])
            ret = float(metrics.get("total_return", 0.0))
            if worst is None or ret < worst["return"]:
                win_regimes = regimes[start:end]
                bull_pct = float((win_regimes == "BULL").mean())
                bear_pct = float((win_regimes == "BEAR").mean())
                neutral_pct = float((win_regimes == "NEUTRAL").mean())
                dominant = max(
                    [("BULL", bull_pct), ("BEAR", bear_pct), ("NEUTRAL", neutral_pct)],
                    key=lambda item: item[1],
                )[0]
                worst = {
                    "start": start,
                    "end": end,
                    "return": ret,
                    "dd": float(metrics.get("max_drawdown", 0.0)),
                    "sharpe": float(metrics.get("sharpe_ratio", 0.0)),
                    "start_time": str(times[start]),
                    "end_time": str(times[end - 1]),
                    "regime_mix": {
                        "dominant": dominant,
                        "bull_pct": round(bull_pct, 4),
                        "bear_pct": round(bear_pct, 4),
                        "neutral_pct": round(neutral_pct, 4),
                    },
                }
        if worst is not None:
            windows[key] = worst
    return windows


def _overlap(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _merge_range_length(ranges: Iterable[Tuple[int, int]]) -> int:
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return sum(end - start for start, end in merged)


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


def _summarize(stats: WindowStats) -> Dict[str, Any]:
    returns = stats.returns
    dds = stats.window_dds
    unique_bars = _merge_range_length(stats.ranges)
    bars_total = max(stats.bars_total, 1)
    position_pct = {
        key: stats.position_bars[key] / bars_total for key in ("long", "short", "flat")
    }
    closed_total = sum(stats.closed.values())
    opened_total = sum(stats.opened.values())

    return {
        "transition": stats.transition,
        "horizon": stats.horizon,
        "horizon_days": stats.horizon_days,
        "transition_count": stats.transition_count,
        "bars_total_summed": stats.bars_total,
        "bars_unique": unique_bars,
        "mean_return": _round_float(mean(returns) if returns else None),
        "median_return": _round_float(median(returns) if returns else None),
        "min_return": _round_float(min(returns) if returns else None),
        "max_return": _round_float(max(returns) if returns else None),
        "total_equity_delta": _round_float(stats.equity_delta, 2),
        "mean_window_dd": _round_float(mean(dds) if dds else None),
        "worst_window_dd": _round_float(min(dds) if dds else None),
        "position_bars": stats.position_bars,
        "position_pct": {k: _round_float(v, 4) for k, v in position_pct.items()},
        "direction_equity_delta": {
            k: _round_float(v, 2) for k, v in stats.direction_equity_delta.items()
        },
        "trades_opened": {"total": opened_total, **stats.opened},
        "trades_closed": {"total": closed_total, **stats.closed},
        "closed_pnl": {k: _round_float(v, 2) for k, v in stats.closed_pnl.items()},
        "rolling_overlap_bars": stats.rolling_overlap_bars,
        "rolling_overlap_pct": {
            key: _round_float(val / bars_total, 4)
            for key, val in stats.rolling_overlap_bars.items()
        },
        "transition_starts_in_worst": stats.transition_starts_in_worst,
        "max_dd_overlap_bars": stats.max_dd_overlap_bars,
        "max_dd_overlap_pct": _round_float(stats.max_dd_overlap_bars / bars_total, 4),
        "transition_starts_in_max_dd": stats.transition_starts_in_max_dd,
        "worst_samples": stats.samples,
    }


def _make_conclusion(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    risky = []
    for row in summaries:
        if row["transition_count"] < 2:
            continue
        worst_overlap = max(row["rolling_overlap_pct"].get("6m", 0), row["rolling_overlap_pct"].get("12m", 0))
        dd_overlap = row.get("max_dd_overlap_pct") or 0
        mean_ret = row.get("mean_return")
        worst_dd = row.get("worst_window_dd")
        if mean_ret is None or worst_dd is None:
            continue
        risk_score = (
            max(0.0, -mean_ret) * 10.0
            + max(0.0, -worst_dd) * 2.0
            + worst_overlap
            + dd_overlap
        )
        if mean_ret < 0 and (worst_overlap >= 0.20 or dd_overlap >= 0.20 or worst_dd <= -0.10):
            risky.append((risk_score, row))

    risky.sort(key=lambda item: item[0], reverse=True)
    if risky:
        top = risky[0][1]
        return {
            "label": "transition risk concentrated",
            "reason": (
                f"{top['transition']} / {top['horizon']} has negative mean return "
                f"({top['mean_return']:+.4f}), worst DD {top['worst_window_dd']:+.4f}, "
                f"and material overlap with worst rolling/global DD windows."
            ),
            "top_transition": top["transition"],
            "top_horizon": top["horizon"],
        }

    negative_rows = [
        row for row in summaries if row["transition_count"] >= 2 and (row.get("mean_return") or 0) < 0
    ]
    if negative_rows:
        return {
            "label": "inconclusive",
            "reason": (
                "Some post-transition windows are negative, but overlap with the worst rolling/global "
                "drawdown windows is not concentrated enough to justify rules yet."
            ),
            "top_transition": None,
            "top_horizon": None,
        }

    return {
        "label": "diffuse/noisy",
        "reason": "No repeated transition/horizon bucket shows negative returns with material risk overlap.",
        "top_transition": None,
        "top_horizon": None,
    }


def _write_tsv(path: Path, summaries: List[Dict[str, Any]]) -> None:
    fields = [
        "transition",
        "horizon",
        "transition_count",
        "bars_unique",
        "mean_return",
        "median_return",
        "min_return",
        "max_return",
        "total_equity_delta",
        "mean_window_dd",
        "worst_window_dd",
        "long_pct",
        "short_pct",
        "flat_pct",
        "long_equity_delta",
        "short_equity_delta",
        "flat_equity_delta",
        "trades_opened_total",
        "trades_opened_long",
        "trades_opened_short",
        "trades_closed_total",
        "trades_closed_long",
        "trades_closed_short",
        "closed_pnl_long",
        "closed_pnl_short",
        "worst6m_overlap_pct",
        "worst12m_overlap_pct",
        "max_dd_overlap_pct",
        "starts_in_worst6m",
        "starts_in_worst12m",
        "starts_in_max_dd",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in summaries:
            writer.writerow(
                {
                    "transition": row["transition"],
                    "horizon": row["horizon"],
                    "transition_count": row["transition_count"],
                    "bars_unique": row["bars_unique"],
                    "mean_return": row["mean_return"],
                    "median_return": row["median_return"],
                    "min_return": row["min_return"],
                    "max_return": row["max_return"],
                    "total_equity_delta": row["total_equity_delta"],
                    "mean_window_dd": row["mean_window_dd"],
                    "worst_window_dd": row["worst_window_dd"],
                    "long_pct": row["position_pct"]["long"],
                    "short_pct": row["position_pct"]["short"],
                    "flat_pct": row["position_pct"]["flat"],
                    "long_equity_delta": row["direction_equity_delta"]["long"],
                    "short_equity_delta": row["direction_equity_delta"]["short"],
                    "flat_equity_delta": row["direction_equity_delta"]["flat"],
                    "trades_opened_total": row["trades_opened"]["total"],
                    "trades_opened_long": row["trades_opened"]["long"],
                    "trades_opened_short": row["trades_opened"]["short"],
                    "trades_closed_total": row["trades_closed"]["total"],
                    "trades_closed_long": row["trades_closed"]["long"],
                    "trades_closed_short": row["trades_closed"]["short"],
                    "closed_pnl_long": row["closed_pnl"]["long"],
                    "closed_pnl_short": row["closed_pnl"]["short"],
                    "worst6m_overlap_pct": row["rolling_overlap_pct"]["6m"],
                    "worst12m_overlap_pct": row["rolling_overlap_pct"]["12m"],
                    "max_dd_overlap_pct": row["max_dd_overlap_pct"],
                    "starts_in_worst6m": row["transition_starts_in_worst"]["6m"],
                    "starts_in_worst12m": row["transition_starts_in_worst"]["12m"],
                    "starts_in_max_dd": row["transition_starts_in_max_dd"],
                }
            )


def _write_notes(
    path: Path,
    report: Dict[str, Any],
    summaries: List[Dict[str, Any]],
) -> None:
    def _fmt_pct(value: Any) -> str:
        if value is None:
            return "N/A"
        return f"{float(value):+.2%}"

    def _fmt_pct_unsigned(value: Any) -> str:
        if value is None:
            return "N/A"
        return f"{float(value):.1%}"

    conclusion = report["conclusion"]
    by_risk = sorted(
        [row for row in summaries if row["transition_count"] > 0],
        key=lambda row: (
            row["mean_return"] if row["mean_return"] is not None else 0,
            row["worst_window_dd"] if row["worst_window_dd"] is not None else 0,
        ),
    )[:8]
    lines = [
        "# Transition Attribution Audit",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnostic-only audit. No filter, gate, exposure scaler, cooldown, baseline, checkpoint, or live/demo code was changed.",
        "- Regimes use existing completed-daily EMA labels: fast=50, slow=200.",
        "- Baseline signal path uses frozen v2.1 params through `research_oracle._generate_v21_signals()`.",
        "- Window return/DD are measured from the baseline equity curve from the bar before transition through the post-transition window.",
        "- Long/short/flat contribution is approximate, using equity deltas grouped by the post-signal holding direction in each bar.",
        "",
        "## Conclusion",
        "",
        f"**{conclusion['label']}**",
        "",
        conclusion["reason"],
        "",
        "Allowed interpretations are: `transition risk concentrated`, `diffuse/noisy`, or `inconclusive`.",
        "",
        "## Worst Rolling Windows",
        "",
    ]

    for key, win in report["worst_rolling_windows"].items():
        lines.append(
            f"- {key}: return {win['return']:+.2%}, DD {win['dd']:+.2%}, "
            f"{win['start_time']} -> {win['end_time']}, dominant={win['regime_mix']['dominant']}"
        )

    dd = report["global_max_drawdown_window"]
    lines.extend(
        [
            "",
            "## Global Max Drawdown",
            "",
            f"- DD {dd['dd']:+.2%}: {dd['start_time']} -> {dd['end_time']}",
            "",
            "## Highest-Risk Buckets",
            "",
            "| transition | horizon | count | mean return | worst DD | long% | short% | worst6m overlap | worst12m overlap | maxDD overlap |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in by_risk:
        lines.append(
            f"| {row['transition']} | {row['horizon']} | {row['transition_count']} | "
            f"{_fmt_pct(row['mean_return'])} | {_fmt_pct(row['worst_window_dd'])} | "
            f"{_fmt_pct_unsigned(row['position_pct']['long'])} | "
            f"{_fmt_pct_unsigned(row['position_pct']['short'])} | "
            f"{_fmt_pct_unsigned(row['rolling_overlap_pct']['6m'])} | "
            f"{_fmt_pct_unsigned(row['rolling_overlap_pct']['12m'])} | "
            f"{_fmt_pct_unsigned(row['max_dd_overlap_pct'])} |"
        )

    lines.extend(
        [
            "",
            "## Next Step Rule",
            "",
            "- If conclusion is `transition risk concentrated`, write a `regime_transition_logic` contract before any rule implementation.",
            "- If conclusion is `diffuse/noisy`, do not open a transition family.",
            "- If conclusion is `inconclusive`, refine attribution first; do not implement a rule.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_audit(
    *,
    data_path: Optional[Path] = None,
    fast_days: int = 50,
    slow_days: int = 200,
    signal_mode: str = "raw",
) -> Dict[str, Any]:
    if data_path is None:
        data_path = _find_eth_data()

    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    times = _datetimes(df_full)
    prices = df_full["close"].values.astype(float)

    checkpoint = _load_baseline_params()
    raw_signals = _generate_v21_signals(checkpoint, df_full, fast_days=fast_days, slow_days=slow_days)
    signals = _safe_execution_signals(raw_signals) if signal_mode == "safe" else raw_signals
    regimes = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)

    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    equity, evaluator_trades = evaluator.simulate(signals, prices)
    metrics = evaluator.compute_metrics(equity, evaluator_trades)
    positions = _position_path(signals)
    deltas = _bar_equity_deltas(equity)
    trades = _trade_events(signals, evaluator_trades)
    transitions = _transition_events(regimes, times)

    worst_rolling = _find_worst_rolling_windows(signals, prices, regimes, times)
    max_dd = _find_global_max_dd(equity)
    max_dd_range = (max_dd["start"], max_dd["end"])

    stats: Dict[Tuple[str, int], WindowStats] = {
        (transition, days): WindowStats(
            transition=transition,
            horizon=f"{days}d",
            horizon_days=days,
        )
        for transition in TRANSITIONS
        for days in HORIZON_DAYS
    }

    for event in transitions:
        for days in HORIZON_DAYS:
            horizon_bars = days * BARS_PER_DAY_5M
            start = event.step
            end = min(len(signals), start + horizon_bars)
            if start >= end:
                continue
            bucket = stats[(event.transition, days)]
            bucket.transition_count += 1
            window_len = end - start
            bucket.bars_total += window_len
            bucket.ranges.append((start, end))

            ret, dd, equity_delta = _window_return_and_dd(equity, start, end)
            bucket.returns.append(ret)
            bucket.window_dds.append(dd)
            bucket.equity_delta += equity_delta

            pos_slice = positions[start:end]
            bucket.position_bars["long"] += int((pos_slice == 1).sum())
            bucket.position_bars["short"] += int((pos_slice == -1).sum())
            bucket.position_bars["flat"] += int((pos_slice == 0).sum())

            for i in range(start, end):
                direction = "long" if positions[i] == 1 else "short" if positions[i] == -1 else "flat"
                bucket.direction_equity_delta[direction] += float(deltas[i])

            for trade in trades:
                step = int(trade["step"])
                if start <= step < end:
                    direction = str(trade["direction"])
                    if trade["kind"] == "open":
                        bucket.opened[direction] += 1
                    else:
                        bucket.closed[direction] += 1
                        bucket.closed_pnl[direction] += float(trade.get("pnl") or 0.0)

            window_range = (start, end)
            for key, worst in worst_rolling.items():
                worst_range = (int(worst["start"]), int(worst["end"]))
                bucket.rolling_overlap_bars[key] += _overlap(window_range, worst_range)
                if worst_range[0] <= start < worst_range[1]:
                    bucket.transition_starts_in_worst[key] += 1

            bucket.max_dd_overlap_bars += _overlap(window_range, max_dd_range)
            if max_dd_range[0] <= start < max_dd_range[1]:
                bucket.transition_starts_in_max_dd += 1

            bucket.add_sample(
                {
                    "transition_time": event.time,
                    "start_bar": start,
                    "end_bar": end,
                    "return": _round_float(ret),
                    "dd": _round_float(dd),
                    "equity_delta": _round_float(equity_delta, 2),
                    "position_pct": {
                        "long": _round_float(float((pos_slice == 1).mean()), 4),
                        "short": _round_float(float((pos_slice == -1).mean()), 4),
                        "flat": _round_float(float((pos_slice == 0).mean()), 4),
                    },
                }
            )

    summaries = [_summarize(s) for s in stats.values()]
    summaries.sort(key=lambda row: (row["transition"], row["horizon_days"]))
    conclusion = _make_conclusion(summaries)

    max_dd_with_time = {
        **max_dd,
        "dd": _round_float(max_dd["dd"]),
        "start_time": str(times[max_dd["start"]]),
        "end_time": str(times[max_dd["end"] - 1]),
    }
    worst_rolling_clean = {}
    for key, value in worst_rolling.items():
        clean = dict(value)
        clean["return"] = _round_float(clean["return"])
        clean["dd"] = _round_float(clean["dd"])
        clean["sharpe"] = _round_float(clean["sharpe"])
        worst_rolling_clean[key] = clean

    report = {
        "generated_at": _now_iso(),
        "audit_version": "transition_attribution_audit_v0.1",
        "scope": "diagnostic_only_no_rules",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "baseline": {
            "id": "channel_breakout_v2_1_balanced",
            "signal_mode": signal_mode,
            "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
            "return": _round_float(float(metrics["total_return"])),
            "dd": _round_float(float(metrics["max_drawdown"])),
            "sharpe": _round_float(float(metrics["sharpe_ratio"])),
            "trades_closed": len([t for t in evaluator_trades if t.get("pnl") is not None]),
        },
        "transition_count_total": len(transitions),
        "transition_counts": {
            transition: sum(1 for event in transitions if event.transition == transition)
            for transition in TRANSITIONS
        },
        "horizons": {f"{days}d": days * BARS_PER_DAY_5M for days in HORIZON_DAYS},
        "worst_rolling_windows": worst_rolling_clean,
        "global_max_drawdown_window": max_dd_with_time,
        "summary": summaries,
        "conclusion": conclusion,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic-only attribution audit for EMA regime transitions."
    )
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    parser.add_argument(
        "--signal-mode",
        choices=("raw", "safe"),
        default="raw",
        help="Baseline signal stream to audit (default raw).",
    )
    args = parser.parse_args()

    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_audit(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
        signal_mode=args.signal_mode,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_tsv(SUMMARY_TSV, report["summary"])
    _write_notes(NOTES_MD, report, report["summary"])

    print("Transition attribution audit complete.")
    print(f"  JSON:  {REPORT_JSON}")
    print(f"  TSV:   {SUMMARY_TSV}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Conclusion: {report['conclusion']['label']} — {report['conclusion']['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
