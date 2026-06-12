"""Focused directional trend tuning for the ETH optimal checkpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Iterable

import numpy as np
import torch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backtest_quant import buy_hold_signals
from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.data import list_crypto_files, load_crypto_data
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategies.hybrid_mm import HybridMeanRevMomentumStrategy
from dex.strategies.long_bias import LongBiasTrendStrategy
from dex.strategies.trend import TrendStrategy
from dex.strategies.trend_follow import TrendFollowStrategy
from dex.strategy_signals import generate_strategy_signals


@dataclass(frozen=True)
class CandidateResult:
    name: str
    strategy: str
    params: dict
    total_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    trades: int
    score: float


def passes_benchmark_gate(
    candidate: CandidateResult,
    benchmark_return: float,
    min_edge: float,
    min_trades: int,
) -> bool:
    """Return whether a candidate is strong enough to replace the active checkpoint."""
    return (
        candidate.trades >= min_trades
        and candidate.total_return >= benchmark_return + min_edge
        and candidate.max_drawdown > -0.30
    )


def build_checkpoint(
    candidate: CandidateResult,
    benchmark_return: float,
    source: str,
) -> dict:
    """Build a checkpoint dict compatible with the runtime loaders."""
    params = dict(candidate.params)
    return {
        "strategy": candidate.strategy,
        "params": params,
        "score": candidate.score,
        "wf_score": candidate.score,
        "metrics": {
            "total_return": candidate.total_return,
            "sharpe_ratio": candidate.sharpe,
            "max_drawdown": candidate.max_drawdown,
            "win_rate": candidate.win_rate,
        },
        "benchmark": {
            "buy_hold_return": benchmark_return,
            "excess_return": candidate.total_return - benchmark_return,
        },
        "selection": "long_only_benchmark_gate",
        "source": source,
        "timestamp": datetime.now().isoformat(),
    }


def load_market_data(symbol: str, interval: str, days: int):
    """Load the newest local parquet data matching symbol/interval."""
    all_files = list_crypto_files()
    data_files = [f for f in all_files if symbol.upper() in os.path.basename(f).upper()]
    interval_match = [f for f in data_files if f"_{interval}" in os.path.basename(f)]
    if interval_match:
        data_files = interval_match
    if not data_files:
        raise FileNotFoundError(f"No local data file found for {symbol} {interval}")

    df = load_crypto_data(data_files[0])
    df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    n_bars = days * 288
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].reset_index(drop=True)
    return df, data_files[0]


def iter_candidates() -> Iterable[tuple[str, str, type, dict]]:
    """Yield directional trend candidate strategy definitions."""
    for entry_lookback, min_hold_bars, emergency_stop_pct in product(
        [576, 1000, 2000, 4000, 8000],
        [0, 288, 576],
        [0.0, 0.30, 0.40],
    ):
        params = {
            "entry_lookback": entry_lookback,
            "min_hold_bars": min_hold_bars,
            "cooldown_bars": 0,
            "emergency_stop_pct": emergency_stop_pct,
            "enable_long": True,
            "enable_short": True,
        }
        yield "ChannelBreakout", "channel_breakout", ChannelBreakoutTrendStrategy, params

    for fast_ma, slow_ma, pullback_ma, breakout, atr_mult, adx_threshold, exit_ma_buffer in product(
        [20, 50, 80],
        [100, 150, 200, 300, 400],
        [10, 20, 50],
        [24, 48, 96],
        [2.0, 2.5, 3.0, 3.5],
        [12.0, 18.0, 25.0],
        [0.002, 0.004, 0.008],
    ):
        if pullback_ma >= slow_ma or fast_ma >= slow_ma:
            continue
        params = {
            "fast_ma_period": fast_ma,
            "slow_ma_period": slow_ma,
            "macro_ma_period": 400,
            "pullback_ma_period": pullback_ma,
            "breakout_lookback": breakout,
            "adx_period": 14,
            "adx_threshold": adx_threshold,
            "slope_lookback": 12,
            "macro_slope_lookback": 48,
            "min_trend_slope": 0.0,
            "min_macro_slope": -0.001,
            "require_macro_alignment": True,
            "rsi_period": 14,
            "entry_rsi_min": 42.0,
            "entry_rsi_max": 88.0,
            "exit_rsi": 35.0,
            "pullback_zone": 0.006,
            "breakout_buffer": 0.001,
            "atr_period": 14,
            "atr_multiplier": atr_mult,
            "hard_stop_pct": 0.05,
            "exit_ma_buffer": exit_ma_buffer,
            "max_hold_bars": 0,
            "cooldown_bars": 12,
            "enable_short": True,
        }
        yield "DirectionalTrend", "directional_trend", LongBiasTrendStrategy, params

    for long_ma, pull_ma, atr_mult, hold, entry_zone in product(
        [100, 150, 200, 300, 400],
        [10, 20, 50, 100],
        [1.5, 2.0, 2.5, 3.0],
        [24, 48, 96, 192],
        [0.001, 0.002, 0.005, 0.01],
    ):
        if pull_ma >= long_ma:
            continue
        params = {
            "long_ma_period": long_ma,
            "pull_ma_period": pull_ma,
            "atr_period": 14,
            "atr_multiplier": atr_mult,
            "max_hold_bars": hold,
            "entry_zone": entry_zone,
            "enable_short": True,
        }
        yield "TrendFollow", "trend_follow", TrendFollowStrategy, params

    for window, std_dev, rsi, hold, trend_filter in product(
        [10, 20, 30, 50],
        [2.0, 2.5, 3.0, 3.5],
        [15, 20, 25, 30],
        [12, 24, 48, 96],
        [False, True],
    ):
        params = {
            "window": window,
            "std_dev": std_dev,
            "rsi_threshold": rsi,
            "max_hold_bars": hold,
            "use_trend_filter": trend_filter,
        }
        yield "TrendStrategy", "trend", TrendStrategy, params

    for rsi_low, rsi_high, ma, hold, atr_mult in product(
        [15, 20, 25, 30, 35],
        [65, 70, 75, 80, 85],
        [10, 20, 50],
        [12, 24, 48, 96],
        [1.5, 2.0, 2.5, 3.0],
    ):
        params = {
            "rsi_period": 14,
            "rsi_low": rsi_low,
            "rsi_high": rsi_high,
            "ma_period": ma,
            "atr_period": 14,
            "atr_multiplier": atr_mult,
            "max_hold_bars": hold,
            "enable_short": True,
        }
        yield "HybridMM", "hybrid_mm", HybridMeanRevMomentumStrategy, params


def evaluate_candidate(
    name: str,
    strategy_alias: str,
    strategy_cls: type,
    params: dict,
    df,
    warmup: int,
    evaluator: StrategyEvaluator,
) -> CandidateResult | None:
    """Evaluate one directional candidate on a common warmup-trimmed window."""
    strategy = strategy_cls(**params)
    enable_short = bool(params.get("enable_short", True))
    if hasattr(strategy, "enable_short"):
        strategy.enable_short = enable_short

    signals = generate_strategy_signals(strategy, df, enable_short=enable_short)
    if warmup >= len(signals) - 1:
        return None

    valid_signals = signals[warmup:]
    prices = df["close"].values[warmup:]
    valid_df = df.iloc[warmup:].reset_index(drop=True)
    score, metrics, trades = evaluator.evaluate(valid_signals, prices, valid_df)
    trade_count = len([t for t in trades if t.get("pnl") is not None])
    return CandidateResult(
        name=name,
        strategy=strategy_alias,
        params=params,
        total_return=float(metrics["total_return"]),
        sharpe=float(metrics["sharpe_ratio"]),
        max_drawdown=float(metrics["max_drawdown"]),
        win_rate=float(metrics["win_rate"]),
        trades=trade_count,
        score=float(score),
    )


def buy_hold_benchmark(df, warmup: int, evaluator: StrategyEvaluator) -> float:
    """Evaluate buy-and-hold on the same warmup-trimmed interval."""
    prices = df["close"].values[warmup:]
    valid_df = df.iloc[warmup:].reset_index(drop=True)
    _, metrics, _ = evaluator.evaluate(buy_hold_signals(len(prices)), prices, valid_df)
    return float(metrics["total_return"])


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune ETH directional trend candidates")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=400)
    parser.add_argument("--min-edge", type=float, default=0.005)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--output", default="checkpoints/eth_optimal.pt")
    parser.add_argument("--report", default="search_results/eth_directional_trend_report.json")
    parser.add_argument("--dry-run", action="store_true", help="Do not write checkpoint")
    args = parser.parse_args()

    df, data_file = load_market_data(args.symbol, args.interval, args.days)
    warmup = min(args.warmup, max(0, len(df) - 2))
    evaluator = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=COMMISSION,
        slippage=SLIPPAGE,
    )
    benchmark_return = buy_hold_benchmark(df, warmup, evaluator)

    results: list[CandidateResult] = []
    for name, alias, strategy_cls, params in iter_candidates():
        candidate = evaluate_candidate(name, alias, strategy_cls, params, df, warmup, evaluator)
        if candidate is not None:
            results.append(candidate)

    results.sort(
        key=lambda item: (
            item.total_return - benchmark_return,
            item.total_return,
            item.sharpe,
            item.trades,
        ),
        reverse=True,
    )
    best = results[0]
    passed = passes_benchmark_gate(best, benchmark_return, args.min_edge, args.min_trades)

    print("=" * 72)
    print("ETH directional trend tuning")
    print("=" * 72)
    print(f"data: {data_file}")
    print(f"rows: {len(df)} | warmup: {warmup}")
    if "datetime" in df.columns:
        print(f"range: {df['datetime'].min()} -> {df['datetime'].max()}")
    print(f"buy_hold: {fmt_pct(benchmark_return)}")
    print(f"gate: candidate >= buy_hold + {fmt_pct(args.min_edge)} and trades >= {args.min_trades}")
    print()
    print(
        f"{'rank':>4} {'strategy':<14} {'return':>9} {'excess':>9} {'sharpe':>8} {'dd':>9} {'trades':>6}"
    )
    print("-" * 72)
    for idx, item in enumerate(results[:15], start=1):
        print(
            f"{idx:>4} {item.strategy:<14} {fmt_pct(item.total_return):>9} "
            f"{fmt_pct(item.total_return - benchmark_return):>9} {item.sharpe:>8.2f} "
            f"{fmt_pct(item.max_drawdown):>9} {item.trades:>6}"
        )

    report = {
        "data_file": data_file,
        "rows": len(df),
        "warmup": warmup,
        "benchmark_return": benchmark_return,
        "min_edge": args.min_edge,
        "min_trades": args.min_trades,
        "passed": passed,
        "best": asdict(best),
        "top": [asdict(item) for item in results[:25]],
        "timestamp": datetime.now().isoformat(),
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {report_path}")

    if not passed:
        print("checkpoint: not overwritten; best candidate did not beat the benchmark gate")
        return 0

    checkpoint = build_checkpoint(best, benchmark_return, source=str(data_file))
    if args.dry_run:
        print("checkpoint: dry-run, not written")
        return 0

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, output_path)
    print(f"checkpoint: saved {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
