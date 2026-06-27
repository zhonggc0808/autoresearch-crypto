"""Small EV-rank shadow matrix for exp_0069.

Blocks the worst-N ChannelBreakout decisions by forecast EV score.
Research-only: no live/core/checkpoint changes.
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
from scripts.research_oracle import (
    _generate_v21_signals,
    _load_and_split_data,
    _position_sizes_from_config,
)
from scripts.run_timesfm_breakout_filter_experiment import (
    DEFAULT_CHECKPOINT,
    collect_decision_indices,
    forecast_decisions,
    signal_target,
)
from scripts.run_tsfm_ev_veto_filter_experiment import (
    DEFAULT_CANDIDATE,
    DEFAULT_MODEL,
    blocked_attribution,
    ev_veto_decision,
    summarize,
)


def parse_ints(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_floats(raw: str, default: float) -> list[float]:
    if not raw.strip():
        return [default]
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def load_candidate(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def gate_params(spec: dict[str, Any]) -> dict[str, float]:
    params = spec.get("params", {})
    return {
        "ev_threshold": float(params.get("ev_threshold", 0.001)),
        "extra_edge_buffer": float(params.get("extra_edge_buffer", 0.001)),
        "max_uncertainty": float(params.get("max_uncertainty", 0.08)),
        "max_tail_risk": float(params.get("max_tail_risk", 0.05)),
        "uncertainty_penalty": float(params.get("uncertainty_penalty", 0.25)),
        "tail_risk_penalty": float(params.get("tail_risk_penalty", 0.5)),
    }


def cache_path(args: argparse.Namespace, data_path: Path) -> Path:
    model_tag = str(args.model).replace("\\", "_").replace("/", "_").replace(":", "_")
    return Path(
        f"research_workspace/diagnostics/exp_0069_{args.model_family}_{model_tag}_"
        f"c{args.context}_h{args.horizon}_{data_path.stem}_cache.json"
    )


def rank_decisions(
    signals: np.ndarray,
    decision_indices: list[int],
    forecasts: dict[int, dict[str, float]],
    params: dict[str, float],
) -> list[dict[str, Any]]:
    idxset = set(decision_indices)
    position = 0
    ranked = []
    for i, raw in enumerate(signals.astype(int)):
        if i not in idxset:
            if raw == 2:
                position = 1
            elif raw == 3:
                position = -1
            elif raw == 0:
                position = 0
            continue
        target = signal_target(int(raw), position)
        decision = ev_veto_decision(target, forecasts[i], **params)
        ranked.append(
            {
                "bar": i,
                "direction": "long" if target > 0 else "short",
                "score": float(decision["expected_edge_after_fee"]),
                "reason": decision["reason"],
            }
        )
        if target != position:
            position = target
    return sorted(ranked, key=lambda x: x["score"])


def apply_rank_blocks(
    signals: np.ndarray,
    block_set: set[int],
    score_by_bar: dict[int, dict[str, Any]],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    filtered = np.ones(len(signals), dtype=int)
    position = 0
    blocked_dir = 0
    blocked = []
    for i, raw in enumerate(signals.astype(int)):
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
            continue
        if i in block_set:
            filtered[i] = 0 if position else 1
            row = {
                "bar": i,
                "direction": "long" if target > 0 else "short",
                "kind": "rank_reversal_to_flat" if position else "rank_new_entry",
                **score_by_bar[i],
            }
            blocked.append(row)
            if position:
                position = 0
            blocked_dir = target
            continue
        filtered[i] = int(raw)
        position = target
    return filtered, blocked


def row_passes(row: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        row["oos_return"] >= baseline["oos"]["return"] * 0.90
        and row["full_dd"] > baseline["full"]["dd"]
        and row["rolling_12m_min_return"] > baseline["rolling_12m_min_return"]
        and row["blocked_original_pnl"] < 0
        and row["blocked_losers"] > row["blocked_winners"]
        and row["trade_reduction"] <= 0.35
    )


def write_outputs(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = output.with_suffix(".csv")
    rows = report["rows"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# exp_0069 TSFM EV Rank Matrix",
        "",
        f"- checkpoint: `{report['checkpoint']}`",
        f"- data: `{report['data']}`",
        f"- model: `{report['model']}`",
        "",
        "| exec | block_n | pass | oos_ret | full_dd | roll12m | trades | losers/winners | blocked_pnl |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['execution_price']} up={r['uncertainty_penalty']:g} "
            f"tp={r['tail_risk_penalty']:g} | {r['block_worst_n']} | "
            f"{str(r['passes']).lower()} | "
            f"{r['oos_return']:.2%} | {r['full_dd']:.2%} | "
            f"{r['rolling_12m_min_return']:.2%} | {r['trades']} | "
            f"{r['blocked_losers']}/{r['blocked_winners']} | "
            f"{r['blocked_original_pnl']:.2f} |"
        )
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def self_test() -> None:
    signals = np.array([1, 2, 2, 0, 3])
    score_by_bar = {
        1: {"score": -0.1, "reason": "x"},
        4: {"score": -0.2, "reason": "y"},
    }
    filtered, blocked = apply_rank_blocks(signals, {4}, score_by_bar)
    assert filtered.tolist() == [1, 2, 2, 0, 1]
    assert blocked[0]["bar"] == 4
    assert parse_ints("10, 20") == [10, 20]
    print("self-test: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--data", default="data/crypto/ETHUSDT_5m_2600d.parquet")
    parser.add_argument("--model-family", default="timesfm")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context", type=int, default=1024)
    parser.add_argument("--horizon", type=int, default=72)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--block-worst-n", default="10,20,30,50")
    parser.add_argument("--execution-prices", default="legacy_close,signal_bar_open")
    parser.add_argument("--uncertainty-penalties", default="")
    parser.add_argument("--tail-risk-penalties", default="")
    parser.add_argument(
        "--output",
        default="research_workspace/diagnostics/exp_0069_tsfm_ev_rank_matrix_2600d.json",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    if args.model_family != "timesfm":
        raise SystemExit("rank matrix currently supports only timesfm")

    spec = load_candidate(args.candidate)
    base_params = gate_params(spec)
    param_grid = []
    for uncertainty_penalty in parse_floats(
        args.uncertainty_penalties,
        base_params["uncertainty_penalty"],
    ):
        for tail_risk_penalty in parse_floats(
            args.tail_risk_penalties,
            base_params["tail_risk_penalty"],
        ):
            param_grid.append(
                {
                    **base_params,
                    "uncertainty_penalty": uncertainty_penalty,
                    "tail_risk_penalty": tail_risk_penalty,
                }
            )
    data_path = Path(args.data)
    checkpoint = load_checkpoint(args.checkpoint)
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    signals = _generate_v21_signals(checkpoint, df_full)
    decision_indices = collect_decision_indices(signals)
    forecasts = forecast_decisions(
        df_full,
        decision_indices,
        args.context,
        args.horizon,
        args.batch_size,
        cache_path(args, data_path),
        args.model,
    )
    position_sizes = _position_sizes_from_config(checkpoint, len(signals), df_full)

    rows = []
    baselines = {}
    for execution_price in [x.strip() for x in args.execution_prices.split(",") if x.strip()]:
        baseline = summarize(signals, df_full, split_idx, position_sizes, execution_price)
        baselines[execution_price] = baseline
        for params in param_grid:
            ranked = rank_decisions(signals, decision_indices, forecasts, params)
            score_by_bar = {int(x["bar"]): x for x in ranked}
            for n in parse_ints(args.block_worst_n):
                filtered, blocked = apply_rank_blocks(
                    signals,
                    {int(x["bar"]) for x in ranked[:n]},
                    score_by_bar,
                )
                summary = summarize(filtered, df_full, split_idx, position_sizes, execution_price)
                attr = blocked_attribution(
                    signals,
                    df_full,
                    blocked,
                    position_sizes,
                    execution_price,
                )["summary"]
                row = {
                    "execution_price": execution_price,
                    "uncertainty_penalty": params["uncertainty_penalty"],
                    "tail_risk_penalty": params["tail_risk_penalty"],
                    "block_worst_n": n,
                    "oos_return": summary["oos"]["return"],
                    "full_return": summary["full"]["return"],
                    "full_dd": summary["full"]["dd"],
                    "rolling_12m_min_return": summary["rolling_12m_min_return"],
                    "trades": summary["full"]["trades"],
                    "trade_reduction": (
                        1 - summary["full"]["trades"] / baseline["full"]["trades"]
                        if baseline["full"]["trades"]
                        else 1
                    ),
                    "blocked_losers": attr["blocked_losers"],
                    "blocked_winners": attr["blocked_winners"],
                    "blocked_original_pnl": attr["blocked_original_pnl"],
                    "net_block_benefit": attr["net_block_benefit"],
                    "fee_10bp_return": summary["fee_10bp_return"],
                }
                row["passes"] = row_passes(row, baseline)
                rows.append(row)

    report = {
        "candidate": spec,
        "checkpoint": args.checkpoint,
        "data": str(data_path),
        "model": args.model,
        "context": args.context,
        "horizon": args.horizon,
        "gate_params": base_params,
        "baselines": {
            k: {
                "oos_return": v["oos"]["return"],
                "full_dd": v["full"]["dd"],
                "rolling_12m_min_return": v["rolling_12m_min_return"],
                "trades": v["full"]["trades"],
            }
            for k, v in baselines.items()
        },
        "rows": rows,
    }
    write_outputs(report, Path(args.output))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
