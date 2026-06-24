#!/usr/bin/env python3
"""Diagnostic-only scan for the worst rolling 12m drawdown window.

The scan asks whether the worst rolling 12m window is driven by a few localized
events or by diffuse regime/position risk. It does not define a patch or alter
any runtime path.
"""

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

from dex.config import BARS_PER_DAY_5M, COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.execution_safety import apply_n2b_1d_block_reversals_only
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import (
    HORIZON_DAYS,
    TRANSITIONS,
    _datetimes,
    _position_path,
    _transition_events,
    _window_return_and_dd,
)

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "rolling12m_worst_window_scan"
NOTES_MD = OUTPUT_DIR / "rolling12m_worst_window_pathology_scan_v0.md"
REGIME_TSV = OUTPUT_DIR / "rolling12m_regime_exposure.tsv"
TRANSITION_TSV = OUTPUT_DIR / "rolling12m_transition_attribution.tsv"
CLUSTERS_TSV = OUTPUT_DIR / "rolling12m_top_loss_clusters.tsv"
NO_TOUCH_MD = OUTPUT_DIR / "no_touch_audit.md"

POSITION_NAMES = {-1: "short", 0: "flat", 1: "long"}
CONCLUSIONS = (
    "localized_drawdown_pathology_found",
    "diffuse_regime_risk",
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


def _metrics(equity: np.ndarray, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    evaluator = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    metrics = evaluator.compute_metrics(equity, trades)
    closed = [trade for trade in trades if trade.get("pnl") is not None]
    return {
        "return": _round_float(float(metrics["total_return"])),
        "max_dd": _round_float(float(metrics["max_drawdown"])),
        "sharpe": _round_float(float(metrics["sharpe_ratio"])),
        "trade_count": len(closed),
        "winning_trades": len([trade for trade in closed if float(trade["pnl"]) > 0]),
        "losing_trades": len([trade for trade in closed if float(trade["pnl"]) <= 0]),
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
        equity, trades = _simulate(signals[start:end], prices[start:end])
        row = {
            "start": start,
            "end": end,
            "start_time": str(times[start]),
            "end_time": str(times[end - 1]),
            **_metrics(equity, trades),
        }
        if worst is None or row["return"] < worst["return"]:
            worst = row
    if worst is None:
        raise ValueError("not enough bars for a rolling 12m scan")
    return worst


def _equity_deltas(equity: np.ndarray) -> np.ndarray:
    deltas = np.zeros(len(equity), dtype=float)
    if len(equity) == 0:
        return deltas
    deltas[0] = float(equity[0] - INITIAL_CAPITAL)
    deltas[1:] = np.diff(equity)
    return deltas


def _regime_position_breakdown(
    regimes: np.ndarray,
    positions: np.ndarray,
    deltas: np.ndarray,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    total_bars = len(regimes)
    rows = []
    for regime in ("BULL", "NEUTRAL", "BEAR"):
        mask = regimes == regime
        pnl = float(deltas[mask].sum()) if mask.any() else 0.0
        rows.append(
            {
                "category": "regime",
                "name": regime,
                "bars": int(mask.sum()),
                "bar_pct": _round_float(float(mask.mean()) if total_bars else 0.0, 4),
                "pnl": _round_float(pnl, 2),
                "negative_bar_count": int((deltas[mask] < 0).sum()) if mask.any() else 0,
                "mean_bar_delta": _round_float(float(deltas[mask].mean()) if mask.any() else 0.0, 4),
            }
        )

    side_rows = []
    for pos, name in ((1, "long"), (-1, "short"), (0, "flat")):
        mask = positions == pos
        pnl = float(deltas[mask].sum()) if mask.any() else 0.0
        side_rows.append(
            {
                "category": "side",
                "name": name,
                "bars": int(mask.sum()),
                "bar_pct": _round_float(float(mask.mean()) if total_bars else 0.0, 4),
                "pnl": _round_float(pnl, 2),
                "negative_bar_count": int((deltas[mask] < 0).sum()) if mask.any() else 0,
                "mean_bar_delta": _round_float(float(deltas[mask].mean()) if mask.any() else 0.0, 4),
            }
        )
    return rows, side_rows


def _transition_rows(
    regimes_full: np.ndarray,
    times_full: pd.DatetimeIndex,
    equity: np.ndarray,
    window_start: int,
    window_end: int,
) -> List[Dict[str, Any]]:
    rows = []
    events = [
        event
        for event in _transition_events(regimes_full, times_full)
        if window_start <= event.step < window_end
    ]
    for transition in TRANSITIONS:
        bucket_events = [event for event in events if event.transition == transition]
        for days in HORIZON_DAYS:
            returns = []
            dds = []
            deltas = []
            for event in bucket_events:
                local_start = event.step - window_start
                local_end = min(len(equity), local_start + days * BARS_PER_DAY_5M)
                if local_start >= local_end:
                    continue
                ret, dd, equity_delta = _window_return_and_dd(equity, local_start, local_end)
                returns.append(ret)
                dds.append(dd)
                deltas.append(equity_delta)
            rows.append(
                {
                    "transition": transition,
                    "horizon": f"{days}d",
                    "transition_count": len(bucket_events),
                    "mean_return": _round_float(mean(returns) if returns else None),
                    "worst_dd": _round_float(min(dds) if dds else None),
                    "equity_delta": _round_float(sum(deltas), 2) if deltas else None,
                    "negative_count": len([value for value in returns if value < 0]),
                }
            )
    return rows


def _drawdown_clusters(
    equity: np.ndarray,
    times: pd.DatetimeIndex,
    regimes: np.ndarray,
    positions: np.ndarray,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    clusters = []
    peak_idx = 0
    peak_value = float(equity[0])
    trough_idx = 0
    trough_value = float(equity[0])
    in_drawdown = False

    def add_cluster(end_idx: int) -> None:
        if trough_value >= peak_value:
            return
        start = peak_idx
        trough = trough_idx
        end = max(end_idx, trough + 1)
        span_regimes = regimes[start:end]
        span_positions = positions[start:end]
        dominant_regime = max(
            ("BULL", "NEUTRAL", "BEAR"),
            key=lambda label: int((span_regimes == label).sum()),
        )
        dominant_side = max(
            ("long", "short", "flat"),
            key=lambda side: int((span_positions == {"long": 1, "short": -1, "flat": 0}[side]).sum()),
        )
        clusters.append(
            {
                "start_bar": int(start),
                "trough_bar": int(trough),
                "end_bar": int(end),
                "start_time": str(times[start]),
                "trough_time": str(times[trough]),
                "end_time": str(times[min(end, len(times) - 1)]),
                "loss_pct": _round_float((trough_value / peak_value) - 1.0),
                "loss_amount": _round_float(trough_value - peak_value, 2),
                "bars_to_trough": int(trough - start),
                "bars_total": int(end - start),
                "dominant_regime": dominant_regime,
                "dominant_side": dominant_side,
            }
        )

    for idx in range(1, len(equity)):
        value = float(equity[idx])
        if value >= peak_value:
            if in_drawdown:
                add_cluster(idx)
            peak_idx = idx
            peak_value = value
            trough_idx = idx
            trough_value = value
            in_drawdown = False
            continue
        in_drawdown = True
        if value < trough_value:
            trough_idx = idx
            trough_value = value
    if in_drawdown:
        add_cluster(len(equity) - 1)

    clusters.sort(key=lambda row: row["loss_pct"])
    for rank, row in enumerate(clusters[:top_n], start=1):
        row["rank"] = rank
    return clusters[:top_n]


def _patch_comparison(
    baseline_signals: np.ndarray,
    adjusted_signals: np.ndarray,
    prices: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    worst_start: int,
    worst_end: int,
) -> Dict[str, Any]:
    adjusted_worst = _find_worst_rolling_12m(adjusted_signals, prices, times)
    base_equity, base_trades = _simulate(
        baseline_signals[worst_start:worst_end],
        prices[worst_start:worst_end],
    )
    adjusted_same_equity, adjusted_same_trades = _simulate(
        adjusted_signals[worst_start:worst_end],
        prices[worst_start:worst_end],
    )
    changed = np.flatnonzero(
        baseline_signals[worst_start:worst_end] != adjusted_signals[worst_start:worst_end]
    )
    base_metrics = _metrics(base_equity, base_trades)
    adjusted_same_metrics = _metrics(adjusted_same_equity, adjusted_same_trades)
    return {
        "same_window_changed_signals": int(len(changed)),
        "same_window_baseline_return": base_metrics["return"],
        "same_window_adjusted_return": adjusted_same_metrics["return"],
        "same_window_return_delta": _round_float(
            adjusted_same_metrics["return"] - base_metrics["return"]
        ),
        "same_window_baseline_dd": base_metrics["max_dd"],
        "same_window_adjusted_dd": adjusted_same_metrics["max_dd"],
        "same_window_dd_delta": _round_float(adjusted_same_metrics["max_dd"] - base_metrics["max_dd"]),
        "adjusted_worst_start": adjusted_worst["start"],
        "adjusted_worst_end": adjusted_worst["end"],
        "adjusted_worst_start_time": adjusted_worst["start_time"],
        "adjusted_worst_end_time": adjusted_worst["end_time"],
        "adjusted_worst_return": adjusted_worst["return"],
        "adjusted_worst_dd": adjusted_worst["max_dd"],
        "adjusted_worst_trade_count": adjusted_worst["trade_count"],
        "helper_changed_total": int(np.count_nonzero(baseline_signals != adjusted_signals)),
        "helper_changed_inside_worst_window": int(len(changed)),
        "helper_transition_count": apply_n2b_1d_block_reversals_only(
            baseline_signals,
            regimes,
        ).diagnostics["transition_count"],
    }


def _make_conclusion(
    clusters: List[Dict[str, Any]],
    regime_rows: List[Dict[str, Any]],
    side_rows: List[Dict[str, Any]],
    patch: Dict[str, Any],
) -> Dict[str, str]:
    if not clusters:
        return {
            "label": "inconclusive",
            "reason": "No drawdown clusters were detected inside the worst 12m window.",
        }
    losses = [abs(float(row["loss_amount"])) for row in clusters]
    total_loss = sum(losses)
    top1_share = losses[0] / total_loss if total_loss > 0 else 0.0
    top3_share = sum(losses[:3]) / total_loss if total_loss > 0 else 0.0
    dominant_regime_pnl = min(regime_rows, key=lambda row: float(row["pnl"]))
    dominant_side_pnl = min(side_rows, key=lambda row: float(row["pnl"]))
    materially_changed = abs(float(patch["same_window_return_delta"])) >= 0.005

    if top1_share >= 0.45 or top3_share >= 0.75:
        return {
            "label": "localized_drawdown_pathology_found",
            "reason": (
                "Worst 12m losses are concentrated in a small number of drawdown clusters. "
                "A separate contract would be required before any rule."
            ),
        }
    if not materially_changed and (
        dominant_regime_pnl["pnl"] < 0 or dominant_side_pnl["pnl"] < 0 or top3_share < 0.75
    ):
        return {
            "label": "diffuse_regime_risk",
            "reason": (
                "Worst 12m losses are not explained by the frozen N2B reversal patch and "
                "are spread across regime/position exposure rather than one tight event."
            ),
        }
    return {
        "label": "inconclusive",
        "reason": "Worst 12m attribution is mixed and should not be turned into a helper.",
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
    worst = report["worst_window"]
    patch = report["frozen_n2b_shadow_comparison"]
    conclusion = report["conclusion"]
    lines = [
        "# rolling12m_worst_window_pathology_scan_v0",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope",
        "",
        "- Diagnosis only.",
        "- No helper, activation, default oracle, live/demo, checkpoint, family, LLM, or patch change.",
        "- Frozen N2B helper is used only as a shadow comparison.",
        "",
        "## Conclusion",
        "",
        f"Conclusion: {conclusion['label']}",
        "",
        conclusion["reason"],
        "",
        "Allowed conclusions: `localized_drawdown_pathology_found`, `diffuse_regime_risk`, `inconclusive`.",
        "",
        "## Worst Rolling 12m Window",
        "",
        f"- Start: {worst['start_time']} ({worst['start']})",
        f"- End: {worst['end_time']} ({worst['end']})",
        f"- Return: {worst['return']:+.2%}",
        f"- Max DD: {worst['max_dd']:+.2%}",
        f"- Closed trade count: {worst['trade_count']}",
        f"- Losing trades: {worst['losing_trades']}",
        "",
        "## Regime Exposure",
        "",
        "| regime | bars | bar% | pnl | negative bars |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in report["regime_exposure"]:
        lines.append(
            f"| {row['name']} | {row['bars']} | {row['bar_pct']:.1%} | "
            f"{row['pnl']:+.2f} | {row['negative_bar_count']} |"
        )
    lines.extend(
        [
            "",
            "## Position-Side Breakdown",
            "",
            "| side | bars | bar% | pnl | negative bars |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["side_breakdown"]:
        lines.append(
            f"| {row['name']} | {row['bars']} | {row['bar_pct']:.1%} | "
            f"{row['pnl']:+.2f} | {row['negative_bar_count']} |"
        )
    lines.extend(
        [
            "",
            "## Top Loss Clusters",
            "",
            "| rank | start | trough | loss | bars to trough | dominant regime | dominant side |",
            "|---:|---|---|---:|---:|---|---|",
        ]
    )
    for row in report["top_loss_clusters"][:5]:
        lines.append(
            f"| {row['rank']} | {row['start_time']} | {row['trough_time']} | "
            f"{row['loss_pct']:+.2%} | {row['bars_to_trough']} | "
            f"{row['dominant_regime']} | {row['dominant_side']} |"
        )
    lines.extend(
        [
            "",
            "## Frozen N2B Shadow Comparison",
            "",
            f"- Same-window changed signals: {patch['same_window_changed_signals']}",
            f"- Same-window return delta: {patch['same_window_return_delta']:+.2%}",
            f"- Same-window DD delta: {patch['same_window_dd_delta']:+.2%}",
            f"- Adjusted worst 12m return: {patch['adjusted_worst_return']:+.2%}",
            f"- Adjusted worst 12m DD: {patch['adjusted_worst_dd']:+.2%}",
            "",
            "## Guardrail",
            "",
            "- This scan answers whether the worst rolling12m window is localized or diffuse.",
            "- It does not define or recommend a patch.",
            "- If localized evidence is found, a separate contract is required before implementation.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_no_touch(path: Path, report: Dict[str, Any]) -> None:
    lines = [
        "# No-Touch Audit: rolling12m worst window pathology scan",
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
        "",
        "## Files Intentionally Added Or Updated",
        "",
        "- `scripts/rolling12m_worst_window_pathology_scan.py`",
        "- `research_workspace/rolling12m_worst_window_scan/rolling12m_worst_window_pathology_scan_v0.md`",
        "- `research_workspace/rolling12m_worst_window_scan/rolling12m_regime_exposure.tsv`",
        "- `research_workspace/rolling12m_worst_window_scan/rolling12m_transition_attribution.tsv`",
        "- `research_workspace/rolling12m_worst_window_scan/rolling12m_top_loss_clusters.tsv`",
        "- `research_workspace/rolling12m_worst_window_scan/no_touch_audit.md`",
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
    shadow = apply_n2b_1d_block_reversals_only(signals, regimes)

    worst = _find_worst_rolling_12m(signals, prices, times)
    start = int(worst["start"])
    end = int(worst["end"])
    win_signals = signals[start:end]
    win_prices = prices[start:end]
    win_regimes = regimes[start:end]
    win_times = times[start:end]
    win_equity, _ = _simulate(win_signals, win_prices)
    win_positions = _position_path(win_signals)
    win_deltas = _equity_deltas(win_equity)
    regime_rows, side_rows = _regime_position_breakdown(win_regimes, win_positions, win_deltas)
    transition_rows = _transition_rows(regimes, times, win_equity, start, end)
    clusters = _drawdown_clusters(win_equity, win_times, win_regimes, win_positions)
    patch = _patch_comparison(signals, shadow.signals, prices, regimes, times, start, end)
    conclusion = _make_conclusion(clusters, regime_rows, side_rows, patch)

    return {
        "generated_at": _now_iso(),
        "scan_id": "rolling12m_worst_window_pathology_scan_v0",
        "scope": "diagnostic_only_no_rules_no_activation",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "worst_window": worst,
        "regime_exposure": regime_rows,
        "side_breakdown": side_rows,
        "transition_attribution": transition_rows,
        "top_loss_clusters": clusters,
        "frozen_n2b_shadow_comparison": patch,
        "frozen_n2b_helper_diagnostics": shadow.diagnostics,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnostic-only rolling12m worst window scan.")
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
    _write_tsv(REGIME_TSV, report["regime_exposure"] + report["side_breakdown"])
    _write_tsv(TRANSITION_TSV, report["transition_attribution"])
    _write_tsv(CLUSTERS_TSV, report["top_loss_clusters"])
    _write_notes(NOTES_MD, report)
    _write_no_touch(NO_TOUCH_MD, report)
    print("rolling12m worst window pathology scan complete.")
    print(f"  Conclusion: {report['conclusion']['label']}")
    print(f"  Notes: {NOTES_MD}")
    print(f"  Regime/side: {REGIME_TSV}")
    print(f"  Transition attribution: {TRANSITION_TSV}")
    print(f"  Clusters: {CLUSTERS_TSV}")
    print(f"  No-touch: {NO_TOUCH_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
