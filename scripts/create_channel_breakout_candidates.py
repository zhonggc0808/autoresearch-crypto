"""Create deployable ChannelBreakout candidate checkpoints from local ETH data."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategy_signals import generate_strategy_signals


DEFAULT_CANDIDATES = {
    "channel_breakout_375_432": {"entry_lookback": 375, "min_hold_bars": 432},
    "channel_breakout_500_432": {"entry_lookback": 500, "min_hold_bars": 432},
}


def base_params(entry_lookback: int, min_hold_bars: int) -> dict[str, Any]:
    """Return runtime-safe ChannelBreakout params for candidate checkpoints."""
    return {
        "exit_lookback": 0,
        "breakout_buffer_pct": 0.0,
        "breakout_atr_buffer": 0.0,
        "atr_period": 14,
        "trend_ma_period": 0,
        "trend_slope_lookback": 0,
        "min_trend_slope": 0.0,
        "trend_buffer_pct": 0.0,
        "adx_period": 14,
        "adx_threshold": 0.0,
        "require_di_alignment": False,
        "entry_lookback": entry_lookback,
        "min_hold_bars": min_hold_bars,
        "cooldown_bars": 0,
        "emergency_stop_pct": 0.0,
        "enable_long": True,
        "enable_short": True,
        "take_profit_pct": 0.0,
        "stop_loss_pct": 0.0,
        "max_hold_bars": 0,
    }


def load_data(path: Path) -> pd.DataFrame:
    """Load OHLCV parquet data sorted by timestamp."""
    df = pd.read_parquet(path)
    return df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)


def evaluate(
    df: pd.DataFrame, params: dict[str, Any], evaluator: StrategyEvaluator
) -> dict[str, Any]:
    """Evaluate one params set on the provided dataframe."""
    strategy = ChannelBreakoutTrendStrategy(**params)
    signals = generate_strategy_signals(
        strategy, df, enable_short=bool(params.get("enable_short", True))
    )
    start = int(getattr(strategy, "warmup_bars", strategy.window))
    if len(df) <= start + 10:
        raise ValueError(f"Not enough rows for warmup={start}: got {len(df)}")

    valid_signals = signals[start:]
    prices = df["close"].to_numpy(dtype=float)[start:]
    valid_df = df.iloc[start:].reset_index(drop=True)

    equity, trades = evaluator.simulate(valid_signals, prices, valid_df)
    metrics = evaluator.compute_metrics(equity, trades)
    trade_pnls = [float(t["pnl"]) for t in trades if t.get("pnl") is not None]

    benchmark_signals = np.ones(len(valid_signals), dtype=int)
    benchmark_signals[0] = 2
    benchmark_equity, benchmark_trades = evaluator.simulate(benchmark_signals, prices, valid_df)
    benchmark_metrics = evaluator.compute_metrics(benchmark_equity, benchmark_trades)

    return {
        "rows": int(len(df)),
        "start": str(df["datetime"].iloc[0]),
        "end": str(df["datetime"].iloc[-1]),
        "warmup_bars": start,
        "metrics": {
            "total_return": float(metrics["total_return"]),
            "annualized_return": float(metrics["annualized_return"]),
            "annualized_vol": float(metrics["annualized_vol"]),
            "sharpe_ratio": float(metrics["sharpe_ratio"]),
            "max_drawdown": float(metrics["max_drawdown"]),
            "win_rate": float(metrics["win_rate"]),
        },
        "benchmark": {
            "buy_hold_return": float(benchmark_metrics["total_return"]),
            "buy_hold_drawdown": float(benchmark_metrics["max_drawdown"]),
            "buy_hold_trades": len([t for t in benchmark_trades if t.get("pnl") is not None]),
            "excess_return": float(metrics["total_return"] - benchmark_metrics["total_return"]),
        },
        "trades": len(trade_pnls),
        "trade_pnl_total": float(sum(trade_pnls)),
    }


def candidate_score(
    full: dict[str, Any], oos: dict[str, Any], windows: list[dict[str, Any]]
) -> float:
    """Small deployability score for sorting candidate reports."""
    full_m = full["metrics"]
    oos_m = oos["metrics"]
    part_rets = [w["metrics"]["total_return"] for w in windows]
    positive_parts = sum(ret > 0 for ret in part_rets)
    worst_part = min(part_rets)
    dd_penalty = max(0.0, abs(full_m["max_drawdown"]) - 0.45)
    dd_penalty += max(0.0, abs(oos_m["max_drawdown"]) - 0.35)
    trade_score = min(oos["trades"], 100) / 100.0
    return float(
        oos_m["total_return"] * 1.4
        + oos["benchmark"]["excess_return"] * 0.8
        + full_m["total_return"] * 0.35
        + worst_part * 0.4
        + positive_parts * 0.1
        + trade_score * 0.15
        - dd_penalty * 1.1
    )


def build_checkpoint(
    name: str,
    params: dict[str, Any],
    score: float,
    full: dict[str, Any],
    train: dict[str, Any],
    oos: dict[str, Any],
    windows: list[dict[str, Any]],
    data_file: Path,
) -> dict[str, Any]:
    """Build a checkpoint compatible with dex.checkpoints loaders."""
    return {
        "strategy": "channelbreakout",
        "params": params,
        "score": score,
        "wf_score": score,
        "metrics": full["metrics"],
        "benchmark": full["benchmark"],
        "validation": {
            "train_70pct": train,
            "oos_30pct": oos,
            "chronological_windows": windows,
        },
        "selection": "channel_breakout_candidate_730d_oos",
        "candidate_name": name,
        "data_file": str(data_file),
        "timestamp": datetime.now().isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create ChannelBreakout candidate checkpoints")
    parser.add_argument(
        "--data",
        type=Path,
        default=PROJECT_DIR / "data" / "crypto" / "ETHUSDT_5m_730d.parquet",
        help="Local OHLCV parquet file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "checkpoints",
        help="Directory for .pt candidate checkpoints",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_DIR / "search_results" / "channel_breakout_candidates_730d.json",
        help="JSON report path",
    )
    args = parser.parse_args()

    df = load_data(args.data)
    evaluator = StrategyEvaluator()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    n = len(df)
    train_df = df.iloc[: int(n * 0.70)].reset_index(drop=True)
    oos_df = df.iloc[int(n * 0.70) :].reset_index(drop=True)
    seg = n // 4
    window_dfs = [
        df.iloc[i * seg : (i + 1) * seg if i < 3 else n].reset_index(drop=True) for i in range(4)
    ]

    report: dict[str, Any] = {
        "data_file": str(args.data),
        "rows": int(len(df)),
        "start": str(df["datetime"].iloc[0]),
        "end": str(df["datetime"].iloc[-1]),
        "created_at": datetime.now().isoformat(),
        "candidates": [],
    }

    for name, seed in DEFAULT_CANDIDATES.items():
        params = base_params(seed["entry_lookback"], seed["min_hold_bars"])
        full = evaluate(df, params, evaluator)
        train = evaluate(train_df, params, evaluator)
        oos = evaluate(oos_df, params, evaluator)
        windows = [evaluate(part, params, evaluator) for part in window_dfs]
        score = candidate_score(full, oos, windows)
        checkpoint = build_checkpoint(name, params, score, full, train, oos, windows, args.data)

        checkpoint_path = args.output_dir / f"{name}.pt"
        torch.save(checkpoint, checkpoint_path)

        item = {
            "name": name,
            "checkpoint": str(checkpoint_path),
            "params": params,
            "score": score,
            "full": full,
            "train_70pct": train,
            "oos_30pct": oos,
            "chronological_windows": windows,
        }
        report["candidates"].append(item)

        print(
            f"{name}: full={full['metrics']['total_return'] * 100:+.2f}% "
            f"dd={full['metrics']['max_drawdown'] * 100:+.2f}% "
            f"trades={full['trades']} | "
            f"oos={oos['metrics']['total_return'] * 100:+.2f}% "
            f"dd={oos['metrics']['max_drawdown'] * 100:+.2f}% "
            f"trades={oos['trades']} -> {checkpoint_path}"
        )

    report["candidates"].sort(key=lambda item: item["score"], reverse=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report saved: {args.report}")


if __name__ == "__main__":
    main()
