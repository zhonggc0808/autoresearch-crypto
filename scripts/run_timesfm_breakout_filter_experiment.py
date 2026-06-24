"""Shadow-test a TimesFM quantile gate on v2.2 ChannelBreakout entries."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.checkpoints import load_checkpoint
from dex.config import COMMISSION, SLIPPAGE
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _compute_fee_sensitivity,
    _compute_rolling_metrics,
    _evaluate_signals,
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _position_sizes_from_config,
    _safe_execution_signals,
)

DEFAULT_CHECKPOINT = "checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json"
DEFAULT_MODEL = "google/timesfm-2.5-200m-pytorch"
BASE_VARIANT_CHECKPOINTS = {
    "channel_breakout_v2_2_m375_bbm375_1p5": DEFAULT_CHECKPOINT,
}


def signal_target(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def collect_decision_indices(signals: np.ndarray) -> list[int]:
    position = 0
    blocked_dir = 0
    out: list[int] = []
    for i, raw in enumerate(signals.astype(int)):
        if raw == 1:
            continue
        target = signal_target(int(raw), position)
        if target != blocked_dir:
            blocked_dir = 0
        if target == 0:
            position = 0
            continue
        if target == position:
            continue
        if blocked_dir == target:
            continue
        out.append(i)
        blocked_dir = target
        if position != 0:
            position = 0
    return out


def forecast_allows(
    direction: int,
    forecast: dict[str, float],
    min_edge_pct: float,
    risk_floor_pct: float,
) -> bool:
    median = forecast["median_return"]
    q10 = forecast["q10_return"]
    q90 = forecast["q90_return"]
    if direction > 0:
        return median > min_edge_pct and q10 > -risk_floor_pct
    return median < -min_edge_pct and q90 < risk_floor_pct


def apply_timesfm_gate(
    signals: np.ndarray,
    forecasts: dict[int, dict[str, float]],
    min_edge_pct: float,
    risk_floor_pct: float,
    stop_after_last_forecast: bool = False,
) -> tuple[np.ndarray, dict[str, int]]:
    filtered = np.ones(len(signals), dtype=int)
    position = 0
    blocked_dir = 0
    diag = {
        "decision_points": 0,
        "allowed_long": 0,
        "allowed_short": 0,
        "blocked_long": 0,
        "blocked_short": 0,
        "blocked_entries": [],
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
            continue

        forecast = forecasts.get(i)
        if forecast is None:
            raise KeyError(f"missing TimesFM forecast for decision index {i}")

        diag["decision_points"] += 1
        if forecast_allows(target, forecast, min_edge_pct, risk_floor_pct):
            filtered[i] = int(raw)
            position = target
            diag["allowed_long" if target > 0 else "allowed_short"] += 1
        else:
            filtered[i] = 0 if position else 1
            if position:
                position = 0
            blocked_dir = target
            diag["blocked_long" if target > 0 else "blocked_short"] += 1
            diag["blocked_entries"].append(
                {"bar": i, "direction": "long" if target > 0 else "short"}
            )

    diag["signals_changed"] = int(np.sum(filtered != signals))
    return filtered, diag


def load_forecast_cache(path: Path) -> dict[int, dict[str, float]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def save_forecast_cache(path: Path, forecasts: dict[int, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {str(k): v for k, v in sorted(forecasts.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_timesfm_model(model_id: str, context: int, horizon: int):
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    try:
        import torch
        # ponytail: current torch build cannot run RTX 5060 sm_120; CPU is enough for shadow tests.
        torch.cuda.is_available = lambda: False
        import timesfm
    except ImportError as exc:
        raise SystemExit(
            "TimesFM is not installed. Run: uv pip install 'timesfm[torch]'"
        ) from exc

    torch.set_float32_matmul_precision("high")
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(model_id)
    model.compile(
        timesfm.ForecastConfig(
            max_context=context,
            max_horizon=horizon,
            normalize_inputs=True,
            per_core_batch_size=1,
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
        )
    )
    return model


def forecast_decisions(
    df: pd.DataFrame,
    indices: list[int],
    context: int,
    horizon: int,
    batch_size: int,
    cache_path: Path,
    model_id: str,
) -> dict[int, dict[str, float]]:
    forecasts = load_forecast_cache(cache_path)
    missing = [i for i in indices if i not in forecasts and i >= 32]
    if not missing:
        return forecasts

    model = build_timesfm_model(model_id, context, horizon)
    close = df["close"].to_numpy(dtype=float)
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        inputs = [close[max(0, i - context + 1) : i + 1].astype(np.float32) for i in batch]
        point, quantiles = model.forecast(horizon=horizon, inputs=inputs)
        for row, i in enumerate(batch):
            spot = close[i]
            forecasts[i] = {
                "spot": float(spot),
                "median_return": float(point[row, horizon - 1] / spot - 1.0),
                "q10_return": float(quantiles[row, horizon - 1, 1] / spot - 1.0),
                "q90_return": float(quantiles[row, horizon - 1, 9] / spot - 1.0),
            }
        save_forecast_cache(cache_path, forecasts)
    return forecasts


def summarize(
    name: str,
    signals: np.ndarray,
    df_full: pd.DataFrame,
    split_idx: int,
    position_sizes: np.ndarray | None,
) -> dict[str, Any]:
    prices = df_full["close"].to_numpy(dtype=float)
    sizes_is = position_sizes[:split_idx] if position_sizes is not None else None
    sizes_oos = position_sizes[split_idx:] if position_sizes is not None else None
    sizes_full = position_sizes
    regimes = build_daily_regime_labels(df_full, fast_days=50, slow_days=200)
    safe = _safe_execution_signals(signals)
    rolling = _compute_rolling_metrics(
        signals,
        prices,
        [12],
        regimes=regimes,
        df=df_full,
        position_sizes=sizes_full,
    )
    return {
        "name": name,
        "is": _evaluate_signals(signals[:split_idx], prices[:split_idx], sizes_is),
        "oos": _evaluate_signals(signals[split_idx:], prices[split_idx:], sizes_oos),
        "full": _evaluate_signals(signals, prices, sizes_full),
        "safe_full": _evaluate_signals(safe, prices, sizes_full),
        "rolling_12m_min_return": rolling["12m_min_return"],
        "fee_10bp_return_is": _compute_fee_sensitivity(
            signals[:split_idx],
            prices[:split_idx],
            position_sizes=sizes_is,
        )["10bp"],
    }


def bar_time(df: pd.DataFrame, i: int) -> str:
    for col in ("datetime", "timestamp", "date"):
        if col in df.columns:
            return str(pd.Timestamp(df[col].iloc[i]))
    return str(i)


def worst_12m_window(signals: np.ndarray, prices: np.ndarray) -> dict[str, Any]:
    window = 12 * BARS_PER_MONTH
    step = window // 2
    worst = {"start": 0, "end": min(window, len(signals)), "return": float("inf")}
    if window >= len(signals):
        return worst
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    for start in range(0, len(signals) - window, step):
        end = start + window
        _, metrics, _ = ev.evaluate(signals[start:end], prices[start:end])
        ret = float(metrics.get("total_return", 0))
        if ret < worst["return"]:
            worst = {"start": start, "end": end, "return": ret}
    return worst


def blocked_trade_attribution(
    signals: np.ndarray,
    df_full: pd.DataFrame,
    forecasts: dict[int, dict[str, float]],
    blocked_entries: list[dict[str, Any]],
    position_sizes: np.ndarray | None,
) -> dict[str, Any]:
    prices = df_full["close"].to_numpy(dtype=float)
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    _, _, trades = ev.evaluate(signals, prices, position_sizes=position_sizes)
    exits = {int(t["entry_step"]): t for t in trades if t.get("pnl") is not None}
    worst = worst_12m_window(signals, prices)
    rows = []
    for entry in blocked_entries:
        i = int(entry["bar"])
        trade = exits.get(i)
        forecast = forecasts[i]
        row = {
            "entry_bar": i,
            "entry_time": bar_time(df_full, i),
            "direction": entry["direction"],
            "median_return": round(float(forecast["median_return"]), 6),
            "q10_return": round(float(forecast["q10_return"]), 6),
            "q90_return": round(float(forecast["q90_return"]), 6),
            "in_worst_12m": bool(worst["start"] <= i < worst["end"]),
        }
        if trade:
            notional = float(trade.get("entry_notional", 0.0))
            pnl = float(trade.get("pnl", 0.0))
            row.update(
                {
                    "exit_bar": int(trade["step"]),
                    "exit_time": bar_time(df_full, int(trade["step"])),
                    "bars_held": int(trade["step"]) - i,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl / notional, 6) if notional else None,
                }
            )
        rows.append(row)
    matched = [r for r in rows if "pnl" in r]
    return {
        "worst_12m": {
            "start_bar": worst["start"],
            "end_bar": worst["end"],
            "start_time": bar_time(df_full, int(worst["start"])),
            "end_time": bar_time(df_full, int(worst["end"] - 1)),
            "return": round(float(worst["return"]), 4),
        },
        "summary": {
            "blocked_entries": len(rows),
            "matched_trades": len(matched),
            "losers": sum(1 for r in matched if r["pnl"] < 0),
            "winners": sum(1 for r in matched if r["pnl"] > 0),
            "total_original_pnl": round(sum(r["pnl"] for r in matched), 2),
        },
        "trades": rows,
    }


def self_test() -> None:
    raw = np.array([1, 2, 2, 0, 3, 3, 0, 2])
    assert collect_decision_indices(raw) == [1, 4, 7]
    forecasts = {
        1: {"median_return": 0.0, "q10_return": -0.02, "q90_return": 0.01},
        4: {"median_return": -0.02, "q10_return": -0.03, "q90_return": 0.0},
        7: {"median_return": 0.02, "q10_return": 0.0, "q90_return": 0.03},
    }
    filtered, diag = apply_timesfm_gate(raw, forecasts, 0.001, 0.01)
    assert filtered.tolist() == [1, 1, 1, 0, 3, 3, 0, 2]
    assert diag["blocked_long"] == 1
    assert diag["allowed_short"] == 1
    assert diag["allowed_long"] == 1

    reversal = np.array([2, 2, 3, 3])
    forecasts = {
        0: {"median_return": 0.02, "q10_return": 0.0, "q90_return": 0.03},
        2: {"median_return": 0.0, "q10_return": -0.01, "q90_return": 0.02},
    }
    filtered, _ = apply_timesfm_gate(reversal, forecasts, 0.001, 0.01)
    assert filtered.tolist() == [2, 2, 0, 1]

    args = argparse.Namespace(
        candidate="research_workspace/llm_candidates/exp_0068.json",
        checkpoint="unused",
        data="",
        context=1,
        horizon=1,
        min_edge_pct=0.0,
        risk_floor_pct=0.0,
        cache="",
        output="",
    )
    spec = apply_candidate_config(args)
    assert spec["experiment_id"] == "exp_0068"
    assert args.checkpoint == DEFAULT_CHECKPOINT
    assert args.context == 1024
    assert args.horizon == 72
    assert args.min_edge_pct == -0.01
    assert args.risk_floor_pct == 0.05
    print("self-test: PASS")


def apply_candidate_config(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.candidate:
        args.cache = args.cache or "research_workspace/diagnostics/timesfm_v22_m375_bbm375_h72_cache.json"
        args.output = (
            args.output or "research_workspace/diagnostics/timesfm_v22_m375_bbm375_h72_report.json"
        )
        return None

    spec_path = Path(args.candidate)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    params = spec.get("params", {})
    if params.get("strategy_type") != "timesfm_quantile_gate":
        raise SystemExit(f"{spec_path} is not a timesfm_quantile_gate candidate")

    base_variant = params.get("base_variant")
    if base_variant not in BASE_VARIANT_CHECKPOINTS:
        raise SystemExit(f"Unsupported base_variant for TimesFM candidate: {base_variant}")

    args.checkpoint = BASE_VARIANT_CHECKPOINTS[base_variant]
    args.context = int(params["context"])
    args.horizon = int(params["horizon"])
    args.min_edge_pct = float(params["min_edge_pct"])
    args.risk_floor_pct = float(params["risk_floor_pct"])

    data_tag = Path(args.data).stem if args.data else "1300d"
    base = f"{spec.get('experiment_id', spec_path.stem)}_timesfm_h{args.horizon}_{data_tag}"
    args.cache = args.cache or f"research_workspace/diagnostics/{base}_cache.json"
    args.output = args.output or f"research_workspace/diagnostics/{base}_report.json"
    return spec


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--data", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--context", type=int, default=1024)
    parser.add_argument("--horizon", type=int, default=72)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--min-edge-pct", type=float, default=COMMISSION + SLIPPAGE + 0.001)
    parser.add_argument("--risk-floor-pct", type=float, default=0.01)
    parser.add_argument("--limit-decisions", type=int, default=0)
    parser.add_argument("--cache", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    candidate_spec = apply_candidate_config(args)

    checkpoint = load_checkpoint(args.checkpoint)
    data_path = Path(args.data) if args.data else _find_eth_data()
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
        Path(args.cache),
        args.model,
    )
    filtered, diag = apply_timesfm_gate(
        signals,
        {i: forecasts[i] for i in decision_indices},
        args.min_edge_pct,
        args.risk_floor_pct,
        stop_after_last_forecast=bool(args.limit_decisions),
    )
    position_sizes = _position_sizes_from_config(checkpoint, len(signals), df_full)
    report = {
        "candidate": {
            "experiment_id": candidate_spec.get("experiment_id"),
            "path": args.candidate,
            "strategy": candidate_spec.get("strategy"),
            "base": candidate_spec.get("base"),
        } if candidate_spec else None,
        "checkpoint": args.checkpoint,
        "data": str(data_path),
        "filter": {
            "model": "google/timesfm-2.5-200m-pytorch",
            "model_source": args.model,
            "context": args.context,
            "horizon": args.horizon,
            "min_edge_pct": args.min_edge_pct,
            "risk_floor_pct": args.risk_floor_pct,
            "cache": args.cache,
        },
        "diag": diag,
        "blocked_trade_attribution": blocked_trade_attribution(
            signals,
            df_full,
            forecasts,
            diag["blocked_entries"],
            position_sizes,
        ),
        "baseline": summarize("baseline", signals, df_full, split_idx, position_sizes),
        "timesfm_filtered": summarize(
            "timesfm_filtered",
            filtered,
            df_full,
            split_idx,
            position_sizes,
        ),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
