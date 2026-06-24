"""Sweep TimesFM gate thresholds first, then horizons."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.checkpoints import load_checkpoint
from dex.config import COMMISSION, SLIPPAGE
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    _generate_v21_signals,
    _load_and_split_data,
    _position_sizes_from_config,
)
from scripts.run_timesfm_breakout_filter_experiment import (
    DEFAULT_CHECKPOINT,
    apply_timesfm_gate,
    collect_decision_indices,
    forecast_decisions,
    summarize,
)


def parse_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def parse_ints(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def rank_key(row: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(row["rolling_12m_min_return"]),
        float(row["full_dd"]),
        float(row["oos_return"]),
    )


def cache_path(args: argparse.Namespace, horizon: int, context: int) -> Path:
    data_name = Path(args.data).stem
    if context == 1024 and horizon == 72:
        old = Path("research_workspace/diagnostics/timesfm_v22_m375_bbm375_h72_2600d_cache.json")
        if "2600d" in data_name and old.exists():
            return old
        old = Path("research_workspace/diagnostics/timesfm_v22_m375_bbm375_h72_cache.json")
        if "2600d" not in data_name and old.exists():
            return old
    if context == 1024:
        return Path(args.cache_dir) / f"timesfm_v22_m375_bbm375_h{horizon}_{data_name}_cache.json"
    return (
        Path(args.cache_dir)
        / f"timesfm_v22_m375_bbm375_c{context}_h{horizon}_{data_name}_cache.json"
    )


def blocked_stats(
    signals,
    prices,
    position_sizes,
    blocked_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    _, _, trades = ev.evaluate(signals, prices, position_sizes=position_sizes)
    by_entry = {
        int(t["entry_step"]): float(t.get("pnl") or 0.0)
        for t in trades
        if "entry_step" in t
    }
    pnl = [by_entry[i] for i in [int(x["bar"]) for x in blocked_entries] if i in by_entry]
    return {
        "blocked_original_pnl": round(sum(pnl), 2),
        "blocked_losers": sum(1 for x in pnl if x < 0),
        "blocked_winners": sum(1 for x in pnl if x > 0),
    }


def evaluate_row(
    signals,
    df_full: pd.DataFrame,
    split_idx: int,
    position_sizes,
    forecasts: dict[int, dict[str, float]],
    min_edge: float,
    risk_floor: float,
    *,
    context: int,
    horizon: int,
    stage: str,
) -> dict[str, Any]:
    print(
        f"[{stage}] h={horizon} min_edge={min_edge:g} risk_floor={risk_floor:g}",
        flush=True,
    )
    filtered, diag = apply_timesfm_gate(signals, forecasts, min_edge, risk_floor)
    summary = summarize("timesfm_filtered", filtered, df_full, split_idx, position_sizes)
    blocked = blocked_stats(
        signals,
        df_full["close"].to_numpy(dtype=float),
        position_sizes,
        diag["blocked_entries"],
    )
    return {
        "stage": stage,
        "context": context,
        "horizon": horizon,
        "min_edge_pct": min_edge,
        "risk_floor_pct": risk_floor,
        "decision_points": diag["decision_points"],
        "blocked": diag["blocked_long"] + diag["blocked_short"],
        "blocked_long": diag["blocked_long"],
        "blocked_short": diag["blocked_short"],
        "signals_changed": diag["signals_changed"],
        "is_return": summary["is"]["return"],
        "is_dd": summary["is"]["dd"],
        "is_sharpe": summary["is"]["sharpe"],
        "oos_return": summary["oos"]["return"],
        "oos_dd": summary["oos"]["dd"],
        "oos_sharpe": summary["oos"]["sharpe"],
        "full_return": summary["full"]["return"],
        "full_dd": summary["full"]["dd"],
        "full_sharpe": summary["full"]["sharpe"],
        "rolling_12m_min_return": summary["rolling_12m_min_return"],
        "fee_10bp_return_is": summary["fee_10bp_return_is"],
        **blocked,
    }


def write_outputs(report: dict[str, Any], rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    md_path = output.with_suffix(".md")
    top = report["best"]
    best_context = top.get("context") or {"context": report["context"]}
    lines = [
        "# TimesFM Gate Matrix",
        "",
        f"- data: `{report['data']}`",
        f"- checkpoint: `{report['checkpoint']}`",
        "- rank: rolling12m_min_return -> full_dd -> oos_return",
        "",
        "## Best",
        "",
        f"- threshold: min_edge={top['threshold']['min_edge_pct']}, "
        f"risk_floor={top['threshold']['risk_floor_pct']}",
        f"- horizon: {top['horizon']['horizon']}",
        f"- context: {best_context['context']}",
        "",
        "## Top threshold rows",
        "",
        "| min_edge | risk_floor | blocked | roll12m | full_dd | oos_ret | blocked_pnl |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["threshold_top10"]:
        lines.append(
            f"| {row['min_edge_pct']:.4f} | {row['risk_floor_pct']:.4f} | "
            f"{row['blocked']} | {row['rolling_12m_min_return']:.2%} | "
            f"{row['full_dd']:.2%} | {row['oos_return']:.2%} | "
            f"{row['blocked_original_pnl']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Horizon rows",
            "",
            "| horizon | blocked | roll12m | full_dd | oos_ret | blocked_pnl |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["horizon_rows"]:
        lines.append(
            f"| {row['horizon']} | {row['blocked']} | "
            f"{row['rolling_12m_min_return']:.2%} | {row['full_dd']:.2%} | "
            f"{row['oos_return']:.2%} | {row['blocked_original_pnl']:.2f} |"
        )
    if report.get("context_rows"):
        lines.extend(
            [
                "",
                "## Context rows",
                "",
                "| context | blocked | roll12m | full_dd | oos_ret | blocked_pnl |",
                "|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in report["context_rows"]:
            lines.append(
                f"| {row['context']} | {row['blocked']} | "
                f"{row['rolling_12m_min_return']:.2%} | {row['full_dd']:.2%} | "
                f"{row['oos_return']:.2%} | {row['blocked_original_pnl']:.2f} |"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def self_test() -> None:
    rows = [
        {"rolling_12m_min_return": 0.1, "full_dd": -0.4, "oos_return": 1.0},
        {"rolling_12m_min_return": 0.1, "full_dd": -0.3, "oos_return": 0.5},
        {"rolling_12m_min_return": 0.2, "full_dd": -0.5, "oos_return": 0.1},
    ]
    assert max(rows, key=rank_key) is rows[2]
    assert max(rows[:2], key=rank_key) is rows[1]
    args = argparse.Namespace(data="data/crypto/ETHUSDT_5m_2600d.parquet", cache_dir="x")
    assert "c512" in str(cache_path(args, 72, 512))
    print("self-test: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--data", default="data/crypto/ETHUSDT_5m_2600d.parquet")
    parser.add_argument("--model", default="research_workspace/diagnostics/timesfm_local_model")
    parser.add_argument("--context", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threshold-horizon", type=int, default=72)
    parser.add_argument("--min-edges", default="-0.02,-0.015,-0.01,-0.005,0")
    parser.add_argument("--risk-floors", default="0.03,0.05,0.07,0.10")
    parser.add_argument("--horizons", default="24,48,72,144")
    parser.add_argument("--contexts", default="", help="Optional context sweep, e.g. 512,1024,2048")
    parser.add_argument("--cache-dir", default="research_workspace/diagnostics")
    parser.add_argument(
        "--output",
        default="research_workspace/diagnostics/timesfm_gate_matrix_2600d.json",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return

    checkpoint = load_checkpoint(args.checkpoint)
    df_is, df_oos, split_idx = _load_and_split_data(Path(args.data))
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    signals = _generate_v21_signals(checkpoint, df_full)
    decision_indices = collect_decision_indices(signals)
    position_sizes = _position_sizes_from_config(checkpoint, len(signals), df_full)
    baseline = summarize("baseline", signals, df_full, split_idx, position_sizes)

    threshold_forecasts = forecast_decisions(
        df_full,
        decision_indices,
        args.context,
        args.threshold_horizon,
        args.batch_size,
        cache_path(args, args.threshold_horizon, args.context),
        args.model,
    )
    threshold_rows = [
        evaluate_row(
            signals,
            df_full,
            split_idx,
            position_sizes,
            {i: threshold_forecasts[i] for i in decision_indices},
            min_edge,
            risk_floor,
            context=args.context,
            horizon=args.threshold_horizon,
            stage="threshold",
        )
        for min_edge in parse_floats(args.min_edges)
        for risk_floor in parse_floats(args.risk_floors)
    ]
    best_threshold = max(threshold_rows, key=rank_key)

    horizon_rows = []
    for horizon in parse_ints(args.horizons):
        forecasts = forecast_decisions(
            df_full,
            decision_indices,
            args.context,
            horizon,
            args.batch_size,
            cache_path(args, horizon, args.context),
            args.model,
        )
        horizon_rows.append(
            evaluate_row(
                signals,
                df_full,
                split_idx,
                position_sizes,
                {i: forecasts[i] for i in decision_indices},
                best_threshold["min_edge_pct"],
                best_threshold["risk_floor_pct"],
                context=args.context,
                horizon=horizon,
                stage="horizon",
            )
        )
    best_horizon = max(horizon_rows, key=rank_key)
    context_rows = []
    if args.contexts:
        for context in parse_ints(args.contexts):
            horizon = int(best_horizon["horizon"])
            forecasts = forecast_decisions(
                df_full,
                decision_indices,
                context,
                horizon,
                args.batch_size,
                cache_path(args, horizon, context),
                args.model,
            )
            context_rows.append(
                evaluate_row(
                    signals,
                    df_full,
                    split_idx,
                    position_sizes,
                    {i: forecasts[i] for i in decision_indices},
                    best_threshold["min_edge_pct"],
                    best_threshold["risk_floor_pct"],
                    context=context,
                    horizon=horizon,
                    stage="context",
                )
            )
    best_context = max(context_rows, key=rank_key) if context_rows else None
    rows = threshold_rows + horizon_rows + context_rows
    report = {
        "checkpoint": args.checkpoint,
        "data": args.data,
        "context": args.context,
        "baseline": {
            "is_return": baseline["is"]["return"],
            "oos_return": baseline["oos"]["return"],
            "full_dd": baseline["full"]["dd"],
            "rolling_12m_min_return": baseline["rolling_12m_min_return"],
        },
        "best": {
            "threshold": best_threshold,
            "horizon": best_horizon,
            "context": best_context,
        },
        "threshold_top10": sorted(threshold_rows, key=rank_key, reverse=True)[:10],
        "horizon_rows": sorted(horizon_rows, key=lambda x: x["horizon"]),
        "context_rows": sorted(context_rows, key=lambda x: x["context"]),
        "rows": rows,
    }
    write_outputs(report, rows, Path(args.output))
    print(json.dumps(report["best"], ensure_ascii=False, indent=2))
    print(f"wrote: {args.output}")


if __name__ == "__main__":
    main()
