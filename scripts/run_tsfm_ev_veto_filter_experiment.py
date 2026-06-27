"""Shadow-test an EV/veto TSFM gate on ChannelBreakout entries.

Research-only: no strategy, checkpoint, scoring, registry, or live path changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.checkpoints import load_checkpoint
from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _position_sizes_from_config,
)
from scripts.run_timesfm_breakout_filter_experiment import (
    DEFAULT_CHECKPOINT,
    bar_time,
    collect_decision_indices,
    forecast_decisions,
    signal_target,
)

DEFAULT_MODEL = "research_workspace/diagnostics/timesfm_local_model"
DEFAULT_CANDIDATE = "research_workspace/llm_candidates/exp_0069_tsfm_ev_veto_shadow.json"


def ev_veto_decision(
    direction: int,
    forecast: dict[str, float],
    *,
    ev_threshold: float,
    extra_edge_buffer: float,
    max_uncertainty: float,
    max_tail_risk: float,
    uncertainty_penalty: float,
    tail_risk_penalty: float,
) -> dict[str, Any]:
    q10 = float(forecast["q10_return"])
    q50 = float(forecast["median_return"])
    q90 = float(forecast["q90_return"])
    median_edge = direction * q50
    uncertainty = max(0.0, q90 - q10)
    tail_risk = max(0.0, -q10 if direction > 0 else q90)
    cost_buffer = 2 * (COMMISSION + SLIPPAGE) + extra_edge_buffer
    expected_edge_after_fee = (
        median_edge
        - cost_buffer
        - uncertainty_penalty * uncertainty
        - tail_risk_penalty * tail_risk
    )
    reasons = []
    if median_edge <= 0:
        reasons.append("wrong_direction")
    if expected_edge_after_fee <= ev_threshold:
        reasons.append("low_ev")
    if uncertainty >= max_uncertainty:
        reasons.append("wide_uncertainty")
    if tail_risk >= max_tail_risk:
        reasons.append("tail_risk")
    return {
        "allowed": not reasons,
        "reason": "allowed" if not reasons else "+".join(reasons),
        "median_edge": median_edge,
        "uncertainty": uncertainty,
        "tail_risk": tail_risk,
        "expected_edge_after_fee": expected_edge_after_fee,
        "cost_buffer": cost_buffer,
    }


def apply_ev_veto_gate(
    signals: np.ndarray,
    forecasts: dict[int, dict[str, float]],
    stop_after_last_forecast: bool = False,
    **gate_params: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    filtered = np.ones(len(signals), dtype=int)
    position = 0
    blocked_dir = 0
    diag: dict[str, Any] = {
        "decision_points": 0,
        "allowed_long": 0,
        "allowed_short": 0,
        "blocked_long": 0,
        "blocked_short": 0,
        "blocked_new_entry": 0,
        "blocked_reversal_to_flat": 0,
        "blocked_reentry_after_flat": 0,
        "blocked_entries": [],
        "decisions": [],
        "signals_changed": 0,
    }

    for i, raw in enumerate(signals.astype(int)):
        if stop_after_last_forecast and forecasts and i > max(forecasts):
            filtered[i] = int(raw)
            continue
        if raw == 1:
            filtered[i] = 1
            continue
        target = signal_target(int(raw), position)
        if target != blocked_dir:
            blocked_dir = 0
        if target == 0:
            filtered[i] = 0
            position = 0
            continue
        if target == position:
            filtered[i] = int(raw)
            continue
        if blocked_dir == target:
            filtered[i] = 0 if position else 1
            diag["blocked_reentry_after_flat"] += int(position == 0)
            continue

        forecast = forecasts.get(i)
        if forecast is None:
            raise KeyError(f"missing forecast for decision index {i}")

        decision = ev_veto_decision(target, forecast, **gate_params)
        kind = "reversal_to_flat" if position else "new_entry"
        row = {
            "bar": i,
            "direction": "long" if target > 0 else "short",
            "kind": kind,
            **{k: float(forecast[k]) for k in ("median_return", "q10_return", "q90_return")},
            **{
                k: decision[k]
                for k in (
                    "reason",
                    "median_edge",
                    "uncertainty",
                    "tail_risk",
                    "expected_edge_after_fee",
                )
            },
            "allowed": bool(decision["allowed"]),
        }
        diag["decisions"].append(row)
        diag["decision_points"] += 1

        if decision["allowed"]:
            filtered[i] = int(raw)
            position = target
            diag["allowed_long" if target > 0 else "allowed_short"] += 1
            continue

        filtered[i] = 0 if position else 1
        if position:
            position = 0
            diag["blocked_reversal_to_flat"] += 1
        else:
            diag["blocked_new_entry"] += 1
        blocked_dir = target
        diag["blocked_long" if target > 0 else "blocked_short"] += 1
        diag["blocked_entries"].append(row)

    diag["signals_changed"] = int(np.sum(filtered != signals))
    return filtered, diag


def shift_for_next_open(
    signals: np.ndarray,
    position_sizes: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray | None]:
    shifted = np.ones(len(signals), dtype=int)
    shifted[1:] = signals[:-1]
    if position_sizes is None:
        return shifted, None
    shifted_sizes = np.zeros(len(position_sizes), dtype=float)
    shifted_sizes[1:] = np.asarray(position_sizes, dtype=float)[:-1]
    return shifted, shifted_sizes


def execution_inputs(
    signals: np.ndarray,
    position_sizes: np.ndarray | None,
    execution_price: str,
) -> tuple[np.ndarray, np.ndarray | None]:
    if execution_price == "next_bar_open":
        # ponytail: next-open is a one-bar delay; move into StrategyEvaluator only if reused wider.
        return shift_for_next_open(signals, position_sizes)
    return signals, position_sizes


def prices_for(df: pd.DataFrame, execution_price: str) -> tuple[np.ndarray, str]:
    if execution_price == "legacy_close":
        return df["close"].to_numpy(dtype=float), "close"
    if execution_price in {"signal_bar_open", "next_bar_open"}:
        return df["close"].to_numpy(dtype=float), "signal_bar_open"
    raise ValueError("execution_price must be legacy_close, signal_bar_open, or next_bar_open")


def evaluate_signals(
    signals: np.ndarray,
    df: pd.DataFrame,
    position_sizes: np.ndarray | None,
    execution_price: str,
    *,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> dict[str, Any]:
    prices, evaluator_execution = prices_for(df, execution_price)
    exec_signals, exec_sizes = execution_inputs(signals, position_sizes, execution_price)
    ev = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=commission,
        slippage=slippage,
        execution_price=evaluator_execution,
    )
    _, metrics, trades = ev.evaluate(
        exec_signals,
        prices,
        df=df if evaluator_execution != "close" else None,
        position_sizes=exec_sizes,
    )
    closed = [t for t in trades if t.get("pnl") is not None]
    return {
        "return": float(metrics.get("total_return", 0.0)),
        "dd": float(metrics.get("max_drawdown", 0.0)),
        "sharpe": float(metrics.get("sharpe_ratio", 0.0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "trades": len(closed),
    }


def rolling_12m_min_return(
    signals: np.ndarray,
    df: pd.DataFrame,
    position_sizes: np.ndarray | None,
    execution_price: str,
) -> float | None:
    window = 12 * BARS_PER_MONTH
    if window >= len(signals) // 2:
        return None
    step = max(1, window // 2)
    worst = float("inf")
    for start in range(0, len(signals) - window, step):
        end = start + window
        sizes = position_sizes[start:end] if position_sizes is not None else None
        ret = evaluate_signals(signals[start:end], df.iloc[start:end], sizes, execution_price)[
            "return"
        ]
        worst = min(worst, float(ret))
    return worst


def summarize(
    signals: np.ndarray,
    df_full: pd.DataFrame,
    split_idx: int,
    position_sizes: np.ndarray | None,
    execution_price: str,
) -> dict[str, Any]:
    sizes_is = position_sizes[:split_idx] if position_sizes is not None else None
    sizes_oos = position_sizes[split_idx:] if position_sizes is not None else None
    return {
        "is": evaluate_signals(
            signals[:split_idx], df_full.iloc[:split_idx], sizes_is, execution_price
        ),
        "oos": evaluate_signals(
            signals[split_idx:], df_full.iloc[split_idx:], sizes_oos, execution_price
        ),
        "full": evaluate_signals(signals, df_full, position_sizes, execution_price),
        "rolling_12m_min_return": rolling_12m_min_return(
            signals,
            df_full,
            position_sizes,
            execution_price,
        ),
        "fee_10bp_return": evaluate_signals(
            signals,
            df_full,
            position_sizes,
            execution_price,
            commission=0.001,
        )["return"],
    }


def blocked_attribution(
    signals: np.ndarray,
    df_full: pd.DataFrame,
    blocked_entries: list[dict[str, Any]],
    position_sizes: np.ndarray | None,
    execution_price: str,
) -> dict[str, Any]:
    prices, evaluator_execution = prices_for(df_full, execution_price)
    exec_signals, exec_sizes = execution_inputs(signals, position_sizes, execution_price)
    ev = StrategyEvaluator(
        commission=COMMISSION, slippage=SLIPPAGE, execution_price=evaluator_execution
    )
    _, _, trades = ev.evaluate(
        exec_signals,
        prices,
        df=df_full if evaluator_execution != "close" else None,
        position_sizes=exec_sizes,
    )
    closed = [t for t in trades if t.get("pnl") is not None]
    by_entry = {int(t["entry_step"]) - int(execution_price == "next_bar_open"): t for t in closed}
    blocked_rows = []
    blocked_pnl = []
    blocked_bars = {int(x["bar"]) for x in blocked_entries}
    total_winner_pnl = sum(float(t["pnl"]) for t in closed if float(t["pnl"]) > 0)
    kept_winner_pnl = sum(
        float(t["pnl"])
        for t in closed
        if float(t["pnl"]) > 0
        and int(t.get("entry_step", -1)) - int(execution_price == "next_bar_open")
        not in blocked_bars
    )
    for row in blocked_entries:
        i = int(row["bar"])
        trade = by_entry.get(i)
        out = {**row, "signal_time": bar_time(df_full, i), "entry_time": bar_time(df_full, i)}
        if trade:
            pnl = float(trade["pnl"])
            blocked_pnl.append(pnl)
            entry_step = int(trade["entry_step"])
            out.update(
                {
                    "entry_bar": entry_step,
                    "entry_time": bar_time(df_full, entry_step),
                    "exit_bar": int(trade["step"]),
                    "exit_time": bar_time(df_full, int(trade["step"])),
                    "bars_held": int(trade["step"]) - entry_step,
                    "pnl": pnl,
                }
            )
        blocked_rows.append(out)
    return {
        "summary": {
            "blocked_entries": len(blocked_entries),
            "blocked_losers": sum(1 for x in blocked_pnl if x < 0),
            "blocked_winners": sum(1 for x in blocked_pnl if x > 0),
            "blocked_original_pnl": sum(blocked_pnl),
            "net_block_benefit": -sum(blocked_pnl),
            "pass_through_profit_retention": (
                kept_winner_pnl / total_winner_pnl if total_winner_pnl else None
            ),
        },
        "rows": blocked_rows,
    }


def cache_path(args: argparse.Namespace, data_path: Path) -> Path:
    if args.cache:
        return Path(args.cache)
    model_tag = str(args.model).replace("\\", "_").replace("/", "_").replace(":", "_")
    return Path(
        f"research_workspace/diagnostics/exp_0069_{args.model_family}_{model_tag}_"
        f"c{args.context}_h{args.horizon}_{data_path.stem}_cache.json"
    )


def load_candidate(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.candidate:
        return None
    spec = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    params = spec.get("params", {})
    if params.get("strategy_type") != "tsfm_ev_veto_gate":
        raise SystemExit(f"{args.candidate} is not a tsfm_ev_veto_gate candidate")
    args.model_family = params.get("model_families", ["timesfm"])[0]
    args.model = params.get("model_id", args.model)
    args.context = int(params["context"])
    args.horizon = int(params["horizon"])
    args.ev_threshold = float(params["ev_threshold"])
    args.extra_edge_buffer = float(params["extra_edge_buffer"])
    args.max_uncertainty = float(params["max_uncertainty"])
    args.max_tail_risk = float(params["max_tail_risk"])
    args.uncertainty_penalty = float(params["uncertainty_penalty"])
    args.tail_risk_penalty = float(params["tail_risk_penalty"])
    return spec


def write_outputs(report: dict[str, Any], rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output.with_suffix(".csv")
    fieldnames = sorted({k for row in rows for k in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    baseline = report["baseline"]["full"]
    filtered = report["filtered"]["full"]
    blocked = report["blocked_trade_attribution"]["summary"]
    md = [
        "# exp_0069 TSFM EV/Veto Shadow Filter",
        "",
        f"- checkpoint: `{report['checkpoint']}`",
        f"- data: `{report['data']}`",
        f"- execution_price: `{report['execution_price']}`",
        f"- model: `{report['filter']['model_family']}:{report['filter']['model']}`",
        "",
        "## Full-period result",
        "",
        "| variant | return | max_dd | sharpe | trades |",
        "|---|---:|---:|---:|---:|",
        (
            f"| baseline | {baseline['return']:.2%} | {baseline['dd']:.2%} | "
            f"{baseline['sharpe']:.2f} | {baseline['trades']} |"
        ),
        (
            f"| filtered | {filtered['return']:.2%} | {filtered['dd']:.2%} | "
            f"{filtered['sharpe']:.2f} | {filtered['trades']} |"
        ),
        "",
        "## Block attribution",
        "",
        f"- blocked_entries: {blocked['blocked_entries']}",
        f"- blocked_losers / winners: {blocked['blocked_losers']} / {blocked['blocked_winners']}",
        f"- blocked_original_pnl: {blocked['blocked_original_pnl']:.2f}",
        f"- net_block_benefit: {blocked['net_block_benefit']:.2f}",
    ]
    output.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def self_test() -> None:
    good = {"median_return": 0.03, "q10_return": 0.01, "q90_return": 0.05}
    bad = {"median_return": 0.01, "q10_return": -0.10, "q90_return": 0.12}
    params = {
        "ev_threshold": 0.001,
        "extra_edge_buffer": 0.001,
        "max_uncertainty": 0.08,
        "max_tail_risk": 0.05,
        "uncertainty_penalty": 0.25,
        "tail_risk_penalty": 0.5,
    }
    assert ev_veto_decision(1, good, **params)["allowed"]
    assert not ev_veto_decision(1, bad, **params)["allowed"]
    raw = np.array([1, 2, 2, 0, 3])
    shifted, shifted_sizes = shift_for_next_open(raw, np.array([0.0, 0.2, 0.3, 0.4, 0.5]))
    assert shifted.tolist() == [1, 1, 2, 2, 0]
    assert shifted_sizes is not None
    assert shifted_sizes.tolist() == [0.0, 0.0, 0.2, 0.3, 0.4]
    forecasts = {1: good, 4: {"median_return": 0.02, "q10_return": -0.01, "q90_return": 0.03}}
    filtered, diag = apply_ev_veto_gate(raw, forecasts, **params)
    assert filtered.tolist() == [1, 2, 2, 0, 1]
    assert diag["allowed_long"] == 1
    assert diag["blocked_short"] == 1
    print("self-test: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--data", default="data/crypto/ETHUSDT_5m_2600d.parquet")
    parser.add_argument("--model-family", default="timesfm")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context", type=int, default=1024)
    parser.add_argument("--horizon", type=int, default=72)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--ev-threshold", type=float, default=0.001)
    parser.add_argument("--extra-edge-buffer", type=float, default=0.001)
    parser.add_argument("--max-uncertainty", type=float, default=0.08)
    parser.add_argument("--max-tail-risk", type=float, default=0.05)
    parser.add_argument("--uncertainty-penalty", type=float, default=0.25)
    parser.add_argument("--tail-risk-penalty", type=float, default=0.50)
    parser.add_argument(
        "--execution-price",
        choices=["legacy_close", "signal_bar_open", "next_bar_open"],
        default="legacy_close",
    )
    parser.add_argument("--limit-decisions", type=int, default=0)
    parser.add_argument("--cache", default="")
    parser.add_argument(
        "--output",
        default="research_workspace/diagnostics/exp_0069_tsfm_ev_veto_2600d.json",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    candidate_spec = load_candidate(args)
    if args.model_family != "timesfm":
        raise SystemExit("First shadow runner only supports --model-family timesfm")

    data_path = Path(args.data) if args.data else _find_eth_data()
    checkpoint = load_checkpoint(args.checkpoint)
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    signals = _generate_v21_signals(checkpoint, df_full)
    decision_indices = collect_decision_indices(signals)
    if args.limit_decisions:
        decision_indices = decision_indices[: args.limit_decisions]

    forecasts = forecast_decisions(
        df_full,
        decision_indices,
        args.context,
        args.horizon,
        args.batch_size,
        cache_path(args, data_path),
        args.model,
    )
    gate_params = {
        "ev_threshold": args.ev_threshold,
        "extra_edge_buffer": args.extra_edge_buffer,
        "max_uncertainty": args.max_uncertainty,
        "max_tail_risk": args.max_tail_risk,
        "uncertainty_penalty": args.uncertainty_penalty,
        "tail_risk_penalty": args.tail_risk_penalty,
    }
    filtered, diag = apply_ev_veto_gate(
        signals,
        {i: forecasts[i] for i in decision_indices},
        stop_after_last_forecast=bool(args.limit_decisions),
        **gate_params,
    )
    position_sizes = _position_sizes_from_config(checkpoint, len(signals), df_full)
    attribution = blocked_attribution(
        signals,
        df_full,
        diag["blocked_entries"],
        position_sizes,
        args.execution_price,
    )
    report = {
        "candidate": candidate_spec,
        "checkpoint": args.checkpoint,
        "data": str(data_path),
        "execution_price": args.execution_price,
        "filter": {
            "strategy": "tsfm_ev_veto_gate",
            "model_family": args.model_family,
            "model": args.model,
            "context": args.context,
            "horizon": args.horizon,
            **gate_params,
        },
        "diag": {k: v for k, v in diag.items() if k != "decisions"},
        "blocked_trade_attribution": attribution,
        "baseline": summarize(signals, df_full, split_idx, position_sizes, args.execution_price),
        "filtered": summarize(filtered, df_full, split_idx, position_sizes, args.execution_price),
    }
    write_outputs(report, attribution["rows"], Path(args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
