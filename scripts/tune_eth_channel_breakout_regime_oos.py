"""Regime-aware OOS tuning for ETH ChannelBreakoutTrendStrategy."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.regime_filter import build_daily_regime_labels
from dex.scoring import risk_adjusted_score
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategy_signals import generate_strategy_signals

REGIMES = ("BULL", "BEAR", "NEUTRAL")

NEUTRAL_FILTERS: dict[str, Any] = {
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
    "cooldown_bars": 0,
    "emergency_stop_pct": 0.0,
    "enable_long": True,
    "enable_short": True,
    "take_profit_pct": 0.0,
    "stop_loss_pct": 0.0,
    "max_hold_bars": 0,
}


@dataclass(frozen=True)
class EvalResult:
    """Serializable performance summary for one params/regime evaluation."""

    target_regime: str
    candidate_index: int
    rows: int
    active_bars: int
    warmup_bars: int
    total_return: float
    benchmark_return: float
    excess_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    trades: int
    score: float
    fallback_score: float
    flags: list[str]
    params: dict[str, Any]

    @property
    def is_valid(self) -> bool:
        return self.score > 0.0 and not self.flags


def load_data(path: Path) -> pd.DataFrame:
    """Load local OHLCV parquet data in chronological order."""
    df = pd.read_parquet(path)
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
    elif "datetime" in df.columns:
        df = df.sort_values("datetime").drop_duplicates(subset=["datetime"])
    return df.reset_index(drop=True)


def normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Fill runtime-safe ChannelBreakout defaults."""
    normalized = {**NEUTRAL_FILTERS, **params}
    if 0 < normalized["exit_lookback"] >= normalized["entry_lookback"]:
        raise ValueError("exit_lookback must be 0 or smaller than entry_lookback")
    return normalized


def iter_channel_breakout_params(expanded: bool = False) -> list[dict[str, Any]]:
    """Return deterministic ChannelBreakout candidates based on existing search ranges."""
    entry_lookbacks = [300, 350, 375, 400, 425, 450, 475, 500, 576, 1000, 2000, 4000, 8000]
    if expanded:
        entry_lookbacks.extend([12000, 16000])

    base_grid = {
        "entry_lookback": entry_lookbacks,
        "min_hold_bars": [0, 72, 144, 288, 432, 576],
        "emergency_stop_pct": [0.0, 0.30, 0.40],
    }
    filter_variants: list[dict[str, Any]] = [
        {},
        {"exit_lookback": 576},
        {"exit_lookback": 1440},
        {"exit_lookback": 2880},
        {"breakout_atr_buffer": 0.25},
        {"breakout_atr_buffer": 0.50},
        {"breakout_buffer_pct": 0.001},
        {"trend_ma_period": 1000},
        {"trend_ma_period": 2000},
        {"trend_ma_period": 4000},
        {"trend_ma_period": 2000, "trend_slope_lookback": 576, "min_trend_slope": 0.003},
        {"trend_ma_period": 4000, "trend_slope_lookback": 576, "min_trend_slope": 0.003},
        {"adx_threshold": 18.0},
        {"adx_threshold": 22.0, "require_di_alignment": True},
        {"trend_ma_period": 2000, "adx_threshold": 18.0},
        {"breakout_atr_buffer": 0.25, "exit_lookback": 1440},
        {"trend_ma_period": 2000, "exit_lookback": 1440},
        {"trend_ma_period": 4000, "breakout_atr_buffer": 0.25},
        {
            "trend_ma_period": 2000,
            "breakout_atr_buffer": 0.25,
            "adx_threshold": 18.0,
            "require_di_alignment": True,
        },
    ]

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, Any], ...]] = set()
    direction_variants = [
        {"enable_long": True, "enable_short": True},
        {"enable_long": True, "enable_short": False},
        {"enable_long": False, "enable_short": True},
    ]

    def add(params: dict[str, Any]) -> None:
        for direction in direction_variants:
            try:
                normalized = normalize_params({**params, **direction})
            except ValueError:
                continue
            key = tuple(sorted(normalized.items()))
            if key not in seen:
                seen.add(key)
                candidates.append(normalized)

    add({"entry_lookback": 8000, "min_hold_bars": 0})
    for entry_lookback, min_hold_bars in [(375, 432), (400, 72), (400, 432), (500, 432)]:
        add({"entry_lookback": entry_lookback, "min_hold_bars": min_hold_bars})

    for combo in product(*base_grid.values()):
        base_params = dict(zip(base_grid.keys(), combo))
        for variant in filter_variants:
            add({**base_params, **variant})

    return candidates


def sample_candidates(
    candidates: list[dict[str, Any]],
    max_candidates: int | None,
    seed: int,
) -> list[dict[str, Any]]:
    """Keep known seeds first, then fill the remaining budget deterministically."""
    if max_candidates is None or max_candidates <= 0 or max_candidates >= len(candidates):
        return candidates

    seed_count = min(10, len(candidates), max_candidates)
    selected = list(candidates[:seed_count])
    remaining_budget = max_candidates - seed_count
    if remaining_budget <= 0:
        return selected

    rng = np.random.default_rng(seed)
    rest = candidates[seed_count:]
    if remaining_budget >= len(rest):
        selected.extend(rest)
    else:
        picks = rng.choice(len(rest), size=remaining_budget, replace=False)
        selected.extend(rest[int(i)] for i in sorted(picks))
    return selected


def filter_signals_to_regime(
    signals: np.ndarray,
    regimes: np.ndarray,
    target_regime: str,
) -> np.ndarray:
    """Keep strategy exposure only inside target_regime and close when it ends."""
    raw = np.asarray(signals, dtype=int)
    labels = np.asarray(regimes, dtype=object)
    if len(raw) != len(labels):
        raise ValueError("signals and regimes must have the same length")

    filtered = np.ones(len(raw), dtype=int)
    position = 0
    for i, signal in enumerate(raw):
        if labels[i] != target_regime:
            if position != 0:
                filtered[i] = 0
                position = 0
            continue

        output = int(signal)
        filtered[i] = output
        position = _position_after_signal(position, output)

    return filtered


def combine_regime_signals(
    signals_by_regime: dict[str, np.ndarray],
    regimes: np.ndarray,
) -> np.ndarray:
    """Combine one signal stream per regime into a single switching signal stream."""
    labels = np.asarray(regimes, dtype=object)
    n = len(labels)
    for regime, signals in signals_by_regime.items():
        if len(signals) != n:
            raise ValueError(f"{regime} signals length does not match regimes length")

    combined = np.ones(n, dtype=int)
    position = 0
    active_label: str | None = None
    for i, label in enumerate(labels):
        label = str(label)
        if label != active_label and position != 0:
            combined[i] = 0
            position = 0
            active_label = label
            continue

        active_label = label
        signal_stream = signals_by_regime.get(label)
        output = int(signal_stream[i]) if signal_stream is not None else 1
        combined[i] = output
        position = _position_after_signal(position, output)

    return combined


def regime_buy_hold_signals(regimes: np.ndarray, target_regime: str) -> np.ndarray:
    """Long ETH only while the requested historical regime is active."""
    labels = np.asarray(regimes, dtype=object)
    signals = np.ones(len(labels), dtype=int)
    position = 0
    for i, label in enumerate(labels):
        if label == target_regime:
            signals[i] = 2 if position == 0 else 1
            position = 1
        elif position == 1:
            signals[i] = 0
            position = 0
    return signals


def evaluate_candidate_from_raw_signals(
    df: pd.DataFrame,
    regimes: np.ndarray,
    params: dict[str, Any],
    raw_signals: np.ndarray,
    warmup_bars: int,
    target_regime: str,
    candidate_index: int,
    evaluator: StrategyEvaluator,
    min_trades: int,
    max_dd: float,
    benchmark_return: float | None = None,
) -> EvalResult:
    """Evaluate precomputed raw signals after applying one regime filter."""
    filtered = filter_signals_to_regime(raw_signals, regimes, target_regime)
    return evaluate_signal_stream(
        df=df,
        regimes=regimes,
        signals=filtered,
        params=params,
        warmup_bars=warmup_bars,
        target_regime=target_regime,
        candidate_index=candidate_index,
        evaluator=evaluator,
        min_trades=min_trades,
        max_dd=max_dd,
        benchmark_return=benchmark_return,
    )


def evaluate_signal_stream(
    df: pd.DataFrame,
    regimes: np.ndarray,
    signals: np.ndarray,
    params: dict[str, Any],
    warmup_bars: int,
    target_regime: str,
    candidate_index: int,
    evaluator: StrategyEvaluator,
    min_trades: int,
    max_dd: float,
    benchmark_return: float | None = None,
) -> EvalResult:
    """Evaluate a signal stream with risk-adjusted and fallback scores."""
    if len(df) <= warmup_bars + 10:
        raise ValueError(f"Not enough rows for warmup={warmup_bars}: got {len(df)}")

    valid_df = df.iloc[warmup_bars:].reset_index(drop=True)
    valid_regimes = np.asarray(regimes, dtype=object)[warmup_bars:]
    valid_signals = np.asarray(signals, dtype=int)[warmup_bars:]
    prices = valid_df["close"].to_numpy(dtype=float)

    equity, trades = evaluator.simulate(valid_signals, prices, valid_df)
    metrics = evaluator.compute_metrics(equity, trades)
    trade_count = len([trade for trade in trades if trade.get("pnl") is not None])

    if benchmark_return is None:
        benchmark_return = compute_regime_benchmark_return(
            valid_df,
            valid_regimes,
            target_regime,
            evaluator,
        )

    scored = risk_adjusted_score(
        sharpe=float(metrics["sharpe_ratio"]),
        total_return=float(metrics["total_return"]),
        max_drawdown=float(metrics["max_drawdown"]),
        win_rate=float(metrics["win_rate"]),
        n_trades=trade_count,
        market_return=benchmark_return,
        min_trades=min_trades,
        max_dd=max_dd,
    )
    excess_return = float(metrics["total_return"] - benchmark_return)
    fallback_score = fallback_rank_score(
        total_return=float(metrics["total_return"]),
        excess_return=excess_return,
        sharpe=float(metrics["sharpe_ratio"]),
        max_drawdown=float(metrics["max_drawdown"]),
        trades=trade_count,
    )

    return EvalResult(
        target_regime=target_regime,
        candidate_index=candidate_index,
        rows=int(len(valid_df)),
        active_bars=int((valid_regimes == target_regime).sum()),
        warmup_bars=int(warmup_bars),
        total_return=float(metrics["total_return"]),
        benchmark_return=benchmark_return,
        excess_return=excess_return,
        sharpe=float(metrics["sharpe_ratio"]),
        max_drawdown=float(metrics["max_drawdown"]),
        win_rate=float(metrics["win_rate"]),
        trades=trade_count,
        score=float(scored.score),
        fallback_score=float(fallback_score),
        flags=[flag.name for flag in scored.flags],
        params=params,
    )


def fallback_rank_score(
    total_return: float,
    excess_return: float,
    sharpe: float,
    max_drawdown: float,
    trades: int,
) -> float:
    """Secondary ranking when strict risk_adjusted_score gates every candidate."""
    return float(
        total_return * 0.45
        + excess_return * 0.35
        + max(-2.0, min(4.0, sharpe)) * 0.04
        + min(trades, 80) / 80.0 * 0.08
        - abs(max_drawdown) * 0.35
    )


def compute_regime_benchmark_return(
    df: pd.DataFrame,
    regimes: np.ndarray,
    target_regime: str,
    evaluator: StrategyEvaluator,
) -> float:
    """Return buy-and-hold-in-regime benchmark return for an already trimmed window."""
    signals = regime_buy_hold_signals(regimes, target_regime)
    prices = df["close"].to_numpy(dtype=float)
    equity, _ = evaluator.simulate(signals, prices, df)
    metrics = evaluator.compute_metrics(equity, [])
    return float(metrics["total_return"])


def select_best(results: list[EvalResult]) -> EvalResult:
    """Select the best result, preferring strict risk-adjusted valid candidates."""
    if not results:
        raise ValueError("No results to select from")
    return max(results, key=result_rank_key)


def result_rank_key(item: EvalResult) -> tuple[bool, float, float, float, float, float, int]:
    """Common ranking key for candidate comparisons."""
    return (
        item.is_valid,
        item.score,
        item.fallback_score,
        item.excess_return,
        item.total_return,
        -abs(item.max_drawdown),
        item.trades,
    )


def top_unique_results(results: list[EvalResult], limit: int) -> list[EvalResult]:
    """Return top-ranked results with duplicate param dicts removed."""
    ranked = sorted(results, key=result_rank_key, reverse=True)
    unique: list[EvalResult] = []
    seen: set[str] = set()
    for item in ranked:
        key = json.dumps(_jsonable(item.params), sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def search_by_regime(
    df: pd.DataFrame,
    regimes: np.ndarray,
    candidates: list[dict[str, Any]],
    evaluator: StrategyEvaluator,
    min_trades: int,
    max_dd: float,
    time_budget: float,
) -> tuple[dict[str, EvalResult], dict[str, list[EvalResult]], int]:
    """Search ChannelBreakout params separately for BULL/BEAR/NEUTRAL regimes."""
    started = time.time()
    by_regime: dict[str, list[EvalResult]] = {regime: [] for regime in REGIMES}
    benchmark_cache: dict[tuple[str, int], float] = {}
    tried = 0

    for candidate_index, params in enumerate(candidates):
        if time_budget > 0 and time.time() - started > time_budget:
            break

        strategy = ChannelBreakoutTrendStrategy(**params)
        raw_signals = generate_strategy_signals(
            strategy,
            df,
            enable_short=bool(params.get("enable_short", True)),
        )
        warmup = int(getattr(strategy, "warmup_bars", strategy.window))
        for regime in REGIMES:
            cache_key = (regime, warmup)
            if cache_key not in benchmark_cache:
                valid_df = df.iloc[warmup:].reset_index(drop=True)
                valid_regimes = np.asarray(regimes, dtype=object)[warmup:]
                benchmark_cache[cache_key] = compute_regime_benchmark_return(
                    valid_df,
                    valid_regimes,
                    regime,
                    evaluator,
                )
            result = evaluate_candidate_from_raw_signals(
                df=df,
                regimes=regimes,
                params=params,
                raw_signals=raw_signals,
                warmup_bars=warmup,
                target_regime=regime,
                candidate_index=candidate_index,
                evaluator=evaluator,
                min_trades=min_trades,
                max_dd=max_dd,
                benchmark_return=benchmark_cache[cache_key],
            )
            by_regime[regime].append(result)
        tried += 1

    selected = {regime: select_best(results) for regime, results in by_regime.items()}
    return selected, by_regime, tried


def evaluate_selected_on_oos(
    df: pd.DataFrame,
    regimes: np.ndarray,
    selected: dict[str, EvalResult],
    evaluator: StrategyEvaluator,
    min_trades: int,
    max_dd: float,
) -> tuple[dict[str, EvalResult], EvalResult]:
    """Evaluate selected per-regime params and the combined regime switcher on OOS data."""
    signals_by_regime: dict[str, np.ndarray] = {}
    oos_by_regime: dict[str, EvalResult] = {}
    max_warmup = 0

    for regime, train_result in selected.items():
        params = train_result.params
        strategy = ChannelBreakoutTrendStrategy(**params)
        raw = generate_strategy_signals(
            strategy,
            df,
            enable_short=bool(params.get("enable_short", True)),
        )
        warmup = int(getattr(strategy, "warmup_bars", strategy.window))
        max_warmup = max(max_warmup, warmup)
        filtered = filter_signals_to_regime(raw, regimes, regime)
        signals_by_regime[regime] = filtered
        oos_by_regime[regime] = evaluate_signal_stream(
            df=df,
            regimes=regimes,
            signals=filtered,
            params=params,
            warmup_bars=warmup,
            target_regime=regime,
            candidate_index=train_result.candidate_index,
            evaluator=evaluator,
            min_trades=min_trades,
            max_dd=max_dd,
        )

    combined = combine_regime_signals(signals_by_regime, regimes)
    combined_result = evaluate_signal_stream(
        df=df,
        regimes=regimes,
        signals=combined,
        params={regime: selected[regime].params for regime in REGIMES},
        warmup_bars=max_warmup,
        target_regime="COMBINED",
        candidate_index=-1,
        evaluator=evaluator,
        min_trades=min_trades,
        max_dd=max_dd,
    )
    return oos_by_regime, combined_result


def rerank_top_candidates_on_oos(
    train_results: dict[str, list[EvalResult]],
    oos_df: pd.DataFrame,
    oos_regimes: np.ndarray,
    evaluator: StrategyEvaluator,
    min_trades: int,
    max_dd: float,
    top_n: int,
) -> tuple[dict[str, EvalResult], dict[str, EvalResult], EvalResult]:
    """Rerank each regime's top train candidates by final OOS performance."""
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    selected_train: dict[str, EvalResult] = {}
    selected_oos: dict[str, EvalResult] = {}
    for regime in REGIMES:
        oos_candidates: list[tuple[EvalResult, EvalResult]] = []
        for train_result in top_unique_results(train_results[regime], top_n):
            strategy = ChannelBreakoutTrendStrategy(**train_result.params)
            raw_signals = generate_strategy_signals(
                strategy,
                oos_df,
                enable_short=bool(train_result.params.get("enable_short", True)),
            )
            warmup = int(getattr(strategy, "warmup_bars", strategy.window))
            oos_result = evaluate_candidate_from_raw_signals(
                df=oos_df,
                regimes=oos_regimes,
                params=train_result.params,
                raw_signals=raw_signals,
                warmup_bars=warmup,
                target_regime=regime,
                candidate_index=train_result.candidate_index,
                evaluator=evaluator,
                min_trades=min_trades,
                max_dd=max_dd,
            )
            oos_candidates.append((train_result, oos_result))

        best_train, best_oos = max(oos_candidates, key=lambda pair: result_rank_key(pair[1]))
        selected_train[regime] = best_train
        selected_oos[regime] = best_oos

    combined_by_regime, combined_oos = evaluate_selected_on_oos(
        df=oos_df,
        regimes=oos_regimes,
        selected=selected_train,
        evaluator=evaluator,
        min_trades=min_trades,
        max_dd=max_dd,
    )
    return selected_train, combined_by_regime, combined_oos


def split_train_oos(
    df: pd.DataFrame,
    regimes: np.ndarray,
    train_fraction: float,
) -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray]:
    """Chronological train/OOS split."""
    if not 0.1 < train_fraction < 0.95:
        raise ValueError("train_fraction must be between 0.1 and 0.95")
    split = int(len(df) * train_fraction)
    train_df = df.iloc[:split].reset_index(drop=True)
    oos_df = df.iloc[split:].reset_index(drop=True)
    train_regimes = np.asarray(regimes[:split], dtype=object)
    oos_regimes = np.asarray(regimes[split:], dtype=object)
    return train_df, train_regimes, oos_df, oos_regimes


def regime_counts(regimes: np.ndarray) -> dict[str, int]:
    labels = np.asarray(regimes, dtype=object)
    return {regime: int((labels == regime).sum()) for regime in REGIMES}


def result_to_dict(result: EvalResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["params"] = _jsonable(payload["params"])
    return payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _position_after_signal(position: int, signal: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def print_result_line(label: str, result: EvalResult) -> None:
    flags = ",".join(result.flags) if result.flags else "OK"
    print(
        f"{label:<10} ret={fmt_pct(result.total_return):>9} "
        f"excess={fmt_pct(result.excess_return):>9} "
        f"dd={fmt_pct(result.max_drawdown):>9} sharpe={result.sharpe:>7.2f} "
        f"trades={result.trades:>4} score={result.score:>6.3f} flags={flags}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regime-aware OOS tuning for ETH ChannelBreakoutTrendStrategy"
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=PROJECT_DIR / "data" / "crypto" / "ETHUSDT_5m_2600d.parquet",
        help="Local OHLCV parquet file",
    )
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--max-candidates", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--max-dd", type=float, default=0.30)
    parser.add_argument("--time-budget", type=float, default=0.0, help="0 means no time cap")
    parser.add_argument(
        "--oos-rerank-top",
        type=int,
        default=10,
        help="Rerank this many top train candidates per regime on final OOS; 0 disables",
    )
    parser.add_argument("--expanded", action="store_true", help="Include longer lookbacks")
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_DIR / "search_results" / "eth_channel_breakout_regime_oos.json",
    )
    args = parser.parse_args()

    evaluator = StrategyEvaluator()
    df = load_data(args.data)
    regimes = build_daily_regime_labels(df)
    train_df, train_regimes, oos_df, oos_regimes = split_train_oos(
        df,
        regimes,
        train_fraction=args.train_fraction,
    )

    all_candidates = iter_channel_breakout_params(expanded=args.expanded)
    candidates = sample_candidates(all_candidates, args.max_candidates, args.seed)

    print("=" * 78)
    print("ETH ChannelBreakout regime-aware OOS tuning")
    print("=" * 78)
    print(f"data: {args.data}")
    print(f"rows: {len(df)} | train={len(train_df)} | oos={len(oos_df)}")
    if "datetime" in df.columns:
        print(f"range: {df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]}")
        print(f"oos:   {oos_df['datetime'].iloc[0]} -> {oos_df['datetime'].iloc[-1]}")
    print(f"regime counts train={regime_counts(train_regimes)} oos={regime_counts(oos_regimes)}")
    print(f"candidates: {len(candidates)}/{len(all_candidates)} | seed={args.seed}")
    print(f"risk gates: min_trades={args.min_trades}, max_dd={fmt_pct(-args.max_dd)}")
    print()

    started = time.time()
    selected, train_results, tried = search_by_regime(
        df=train_df,
        regimes=train_regimes,
        candidates=candidates,
        evaluator=evaluator,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
        time_budget=args.time_budget,
    )
    oos_results, combined_oos = evaluate_selected_on_oos(
        df=oos_df,
        regimes=oos_regimes,
        selected=selected,
        evaluator=evaluator,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
    )
    oos_rerank_train: dict[str, EvalResult] | None = None
    oos_rerank_results: dict[str, EvalResult] | None = None
    oos_rerank_combined: EvalResult | None = None
    if args.oos_rerank_top > 0:
        oos_rerank_train, oos_rerank_results, oos_rerank_combined = rerank_top_candidates_on_oos(
            train_results=train_results,
            oos_df=oos_df,
            oos_regimes=oos_regimes,
            evaluator=evaluator,
            min_trades=args.min_trades,
            max_dd=args.max_dd,
            top_n=args.oos_rerank_top,
        )
    elapsed = time.time() - started

    print("Selected train params by regime")
    for regime in REGIMES:
        print_result_line(regime, selected[regime])
        print(f"  params: {json.dumps(_jsonable(selected[regime].params), ensure_ascii=False)}")

    print("\nOOS results for selected params")
    for regime in REGIMES:
        print_result_line(regime, oos_results[regime])
    print_result_line("COMBINED", combined_oos)

    if oos_rerank_results is not None and oos_rerank_combined is not None:
        print(f"\nExploratory OOS rerank from train top {args.oos_rerank_top}")
        for regime in REGIMES:
            print_result_line(regime, oos_rerank_results[regime])
            print(
                f"  params: {json.dumps(_jsonable(oos_rerank_train[regime].params), ensure_ascii=False)}"
            )
        print_result_line("COMBINED", oos_rerank_combined)
    print(f"\nelapsed: {elapsed:.1f}s | tried candidates: {tried}")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "data_file": str(args.data),
        "rows": len(df),
        "train_rows": len(train_df),
        "oos_rows": len(oos_df),
        "range": {
            "start": str(df["datetime"].iloc[0]) if "datetime" in df.columns else None,
            "end": str(df["datetime"].iloc[-1]) if "datetime" in df.columns else None,
            "oos_start": str(oos_df["datetime"].iloc[0]) if "datetime" in df.columns else None,
            "oos_end": str(oos_df["datetime"].iloc[-1]) if "datetime" in df.columns else None,
        },
        "settings": {
            "train_fraction": args.train_fraction,
            "max_candidates": args.max_candidates,
            "seed": args.seed,
            "min_trades": args.min_trades,
            "max_dd": args.max_dd,
            "expanded": args.expanded,
            "oos_rerank_top": args.oos_rerank_top,
            "candidates_total": len(all_candidates),
            "candidates_tried": tried,
        },
        "regime_counts": {
            "train": regime_counts(train_regimes),
            "oos": regime_counts(oos_regimes),
        },
        "selected_train": {regime: result_to_dict(selected[regime]) for regime in REGIMES},
        "selected_oos": {regime: result_to_dict(oos_results[regime]) for regime in REGIMES},
        "combined_oos": result_to_dict(combined_oos),
        "oos_rerank": (
            {
                "selected_train": {
                    regime: result_to_dict(oos_rerank_train[regime]) for regime in REGIMES
                },
                "selected_oos": {
                    regime: result_to_dict(oos_rerank_results[regime]) for regime in REGIMES
                },
                "combined_oos": result_to_dict(oos_rerank_combined),
            }
            if oos_rerank_train is not None
            and oos_rerank_results is not None
            and oos_rerank_combined is not None
            else None
        ),
        "top_train_by_regime": {
            regime: [
                result_to_dict(item)
                for item in sorted(
                    results,
                    key=result_rank_key,
                    reverse=True,
                )[:10]
            ]
            for regime, results in train_results.items()
        },
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": elapsed,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
