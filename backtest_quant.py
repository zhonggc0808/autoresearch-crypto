"""
布林带均值回归策略 Walk-Forward 回测脚本。
下载一段历史数据，逐根 K 线用策略生成信号并模拟交易。

Usage:
    uv run python backtest_quant.py --symbol BTCUSDT --interval 5m --days 7
    uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 14
"""

import argparse
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from dex.checkpoints import (
    build_strategy_from_checkpoint,
    load_checkpoint,
)
from dex.config import (
    COMMISSION,
    INITIAL_CAPITAL,
    PROJECT_DIR,
    SLIPPAGE,
)
from dex.data import list_crypto_files, load_crypto_data
from dex.drawdown_guard import DrawdownGuardStats, apply_drawdown_guard
from dex.market_intel import (
    MarketIntelOverlayStats,
    RandomEntryControlStats,
    build_market_intel_overlay,
    build_random_entry_control,
    load_market_intel,
)
from dex.regime_filter import (
    RegimeFilterStats,
    apply_regime_short_filter,
    build_daily_regime_labels,
)
from dex.strategies.base import StrategyEvaluator
from dex.strategy_signals import generate_strategy_signals


class SimpleBacktest:
    """简化版回测引擎（只做多/空仓，支持手续费和滑点）"""

    def __init__(self, initial_capital=10000.0, commission=0.001, slippage=0.0005):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def run(self, df, signals):
        """
        Walk-forward 逐K线模拟交易
        """
        capital = self.initial_capital
        position = 0  # 0=空仓, 1=多头
        shares = 0.0
        equity_curve = []
        trades = []

        closes = df["close"].values
        datetimes = df["datetime"].values

        for i in range(len(signals)):
            signal = signals[i]
            price = closes[i]

            target_pos = position
            if signal == 2:
                target_pos = 1
            elif signal == 0:
                target_pos = 0
            elif signal == 1:
                target_pos = position

            if target_pos != position:
                if target_pos == 1 and position == 0:
                    exec_price = price * (1 + self.slippage)
                    shares = capital * (1 - self.commission) / exec_price
                    cost = capital * self.commission
                    capital = 0.0
                    trades.append(
                        {
                            "step": i,
                            "time": datetimes[i],
                            "type": "BUY",
                            "price": price,
                            "exec_price": exec_price,
                            "shares": shares,
                            "cost": cost,
                        }
                    )
                    position = 1

                elif target_pos == 0 and position == 1:
                    exec_price = price * (1 - self.slippage)
                    gross = shares * exec_price
                    cost = gross * self.commission
                    capital = gross - cost
                    trades.append(
                        {
                            "step": i,
                            "time": datetimes[i],
                            "type": "SELL",
                            "price": price,
                            "exec_price": exec_price,
                            "shares": shares,
                            "cost": cost,
                            "capital_after": capital,
                        }
                    )
                    shares = 0.0
                    position = 0

            if position == 1:
                current_equity = shares * price
            else:
                current_equity = capital

            equity_curve.append(current_equity)

        if position == 1:
            exec_price = closes[-1] * (1 - self.slippage)
            gross = shares * exec_price
            cost = gross * self.commission
            capital = gross - cost
            trades.append(
                {
                    "step": len(signals) - 1,
                    "time": datetimes[-1],
                    "type": "SELL (Final)",
                    "price": closes[-1],
                    "exec_price": exec_price,
                    "shares": shares,
                    "cost": cost,
                    "capital_after": capital,
                }
            )
            equity_curve[-1] = capital
            position = 0
            shares = 0.0

        results_df = pd.DataFrame(
            {
                "datetime": datetimes[: len(signals)],
                "close": closes[: len(signals)],
                "signal": signals,
                "position": [1 if s == 2 else 0 for s in signals],
                "equity": equity_curve,
            }
        )

        metrics = self._compute_metrics(equity_curve)
        return results_df, trades, metrics

    def _compute_metrics(self, equity_curve):
        equity = np.array(equity_curve)
        returns = np.diff(equity) / equity[:-1]

        total_return = (equity[-1] / equity[0]) - 1

        n_steps = len(equity)
        years = n_steps * 5 / (288 * 365)
        if years < 0.01:
            years = 0.01
        annualized_return = (1 + total_return) ** (1 / years) - 1
        annualized_return = max(-10.0, min(10.0, annualized_return))
        annualized_vol = np.std(returns) * math.sqrt(288 * 365) if len(returns) > 0 else 0
        sharpe = annualized_return / annualized_vol if annualized_vol > 0 else 0

        peak = equity[0]
        max_drawdown = 0
        for e in equity:
            if e > peak:
                peak = e
            dd = (e - peak) / peak
            if dd < max_drawdown:
                max_drawdown = dd

        total_trades = len([r for r in returns if abs(r) > 1e-10])
        winning_trades = len([r for r in returns if r > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "final_equity": equity[-1],
            "initial_equity": equity[0],
        }


def backtest(strategy, df):
    """Walk-forward 回测：逐根 K 线生成信号"""
    signals = generate_strategy_signals(strategy, df, enable_short=True)
    # 去掉前 window 条无法有效计算布林带的数据
    valid_start = strategy.window
    df_aligned = df.iloc[valid_start:].reset_index(drop=True)
    signals_aligned = signals[valid_start:]
    return signals_aligned, df_aligned


def normalize_signals_for_position_mode(signals: np.ndarray, long_only: bool = False) -> np.ndarray:
    """Return signals adjusted for the requested position mode."""
    normalized = np.asarray(signals, dtype=int).copy()
    if long_only:
        normalized[normalized == 3] = 1
    return normalized


def buy_hold_signals(length: int) -> np.ndarray:
    """Return a buy-once-then-hold signal array for benchmark comparison."""
    signals = np.ones(max(0, length), dtype=int)
    if length > 0:
        signals[0] = 2
    return signals


def print_regime_filter_stats(
    stats: RegimeFilterStats,
    fast_days: int,
    slow_days: int,
) -> None:
    """Print deterministic historical regime filter diagnostics."""
    bull_pct = stats.bullish_bars / stats.total_bars * 100 if stats.total_bars else 0.0
    bear_pct = stats.bearish_bars / stats.total_bars * 100 if stats.total_bars else 0.0
    neutral_pct = stats.neutral_bars / stats.total_bars * 100 if stats.total_bars else 0.0
    print("=" * 60)
    print("Regime 过滤（日线EMA，使用前一日确认状态）")
    print("=" * 60)
    print(f"EMA参数:      fast={fast_days}d slow={slow_days}d")
    print(f"BULL K线:     {stats.bullish_bars}/{stats.total_bars} ({bull_pct:.2f}%)")
    print(f"BEAR K线:     {stats.bearish_bars}/{stats.total_bars} ({bear_pct:.2f}%)")
    print(f"NEUTRAL K线:  {stats.neutral_bars}/{stats.total_bars} ({neutral_pct:.2f}%)")
    print(f"BULL首尾:     {stats.first_bull_time or '无'} ~ {stats.last_bull_time or '无'}")
    print(f"BEAR首尾:     {stats.first_bear_time or '无'} ~ {stats.last_bear_time or '无'}")
    print("过滤规则:      BULL/NEUTRAL 禁空，BEAR 多空都允许")
    print(f"非BEAR屏蔽SHORT信号: {stats.blocked_short_signals}")
    print(f"非BEAR平空触发:      {stats.closed_short_positions}")
    print()


def print_drawdown_guard_stats(stats: DrawdownGuardStats) -> None:
    """Print drawdown guard diagnostics."""
    pct = stats.guarded_bars / stats.total_bars * 100 if stats.total_bars else 0.0
    print("=" * 60)
    print("策略自身回撤熔断")
    print("=" * 60)
    print(f"触发阈值:      {stats.max_dd_guard * 100:.2f}%")
    print(f"恢复阈值:      {stats.recovery_dd * 100:.2f}%")
    print(f"触发次数:      {stats.guard_entries}")
    print(f"恢复次数:      {stats.guard_exits}")
    print(f"熔断K线:       {stats.guarded_bars}/{stats.total_bars} ({pct:.2f}%)")
    print(f"强制平仓:      {stats.forced_closes}")
    print(f"内部最大回撤:  {stats.max_observed_drawdown * 100:.2f}%")
    print(f"熔断首尾:      {stats.first_guard_time or '无'} ~ {stats.last_guard_time or '无'}")
    print()


def print_market_intel_stats(
    stats: MarketIntelOverlayStats,
    baseline_trades: list[dict],
) -> None:
    """Print frozen market-intel overlay diagnostics."""
    print("=" * 60)
    print("Market Intelligence Overlay（冻结情报表）")
    print("=" * 60)
    print(f"K线数:          {stats.total_bars}")
    for mode, count in stats.mode_counts.items():
        pct = count / stats.total_bars * 100 if stats.total_bars else 0.0
        print(f"{mode:18s}: {count:5d} ({pct:5.2f}%)")
    print(f"拦截新开仓:     {stats.vetoed_new_entries}")
    print(f"半仓新开仓:     {stats.half_size_entries}")
    print(f"情报过期K线:    {stats.expired_intel_bars}")
    print(f"情报缺失K线:    {stats.missing_intel_bars}")

    close_trades = [t for t in baseline_trades if t.get("pnl") is not None]
    veto_pnls = _entry_pnls(close_trades, stats.vetoed_entry_steps)
    half_pnls = _entry_pnls(close_trades, stats.half_size_entry_steps)
    print(f"被拦截交易事后PnL: {_pnl_summary(veto_pnls)}")
    print(f"半仓交易事后PnL:   {_pnl_summary(half_pnls)}")
    print()


def print_random_control_stats(
    stats: RandomEntryControlStats,
    baseline_trades: list[dict],
) -> None:
    """Print random entry-control diagnostics."""
    print("=" * 60)
    print("Random Veto Control（同等数量随机拦截/半仓）")
    print("=" * 60)
    print(f"seed:           {stats.seed}")
    print(f"可选新开仓数:   {stats.available_entry_count}")
    print(f"随机拦截新开仓: {stats.vetoed_new_entries}")
    print(f"随机半仓新开仓: {stats.half_size_entries}")

    close_trades = [t for t in baseline_trades if t.get("pnl") is not None]
    veto_pnls = _entry_pnls(close_trades, stats.vetoed_entry_steps)
    half_pnls = _entry_pnls(close_trades, stats.half_size_entry_steps)
    print(f"随机拦截交易事后PnL: {_pnl_summary(veto_pnls)}")
    print(f"随机半仓交易事后PnL: {_pnl_summary(half_pnls)}")
    print()


def _entry_pnls(trades: list[dict], entry_steps: tuple[int, ...]) -> list[float]:
    wanted = set(entry_steps)
    return [float(t["pnl"]) for t in trades if t.get("entry_step") in wanted]


def _pnl_summary(pnls: list[float]) -> str:
    if not pnls:
        return "无"
    return f"n={len(pnls)} avg={np.mean(pnls):+.2f} sum={np.sum(pnls):+.2f} USDT"


def build_market_intel_position_sizes(
    valid_df: pd.DataFrame,
    valid_signals: np.ndarray,
    path: str,
    max_age_hours: float,
    unknown_mode: str,
) -> tuple[np.ndarray, MarketIntelOverlayStats]:
    """Load market-intel file and return per-bar entry multipliers."""
    _, position_sizes, stats = build_market_intel_overlay(
        valid_df,
        load_market_intel(path),
        signals=valid_signals,
        max_age_hours=max_age_hours,
        unknown_mode=unknown_mode,
    )
    return position_sizes, stats


def _bar_datetimes(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"], errors="coerce")
    raw = df["timestamp"]
    unit = "ms" if float(np.nanmax(np.abs(raw.to_numpy(dtype=float)))) > 10_000_000_000 else "s"
    return pd.to_datetime(raw, unit=unit, errors="coerce")


def _funding_day_tag(path: Path) -> int:
    match = re.search(r"_(\d+)d\.parquet$", path.name)
    return int(match.group(1)) if match else -1


def _find_funding_file(symbol: str, days: int = 0, root: Path | None = None) -> Path | None:
    root = root or PROJECT_DIR / "data" / "market_intel"
    candidates = sorted(root.glob(f"{symbol.upper()}*derivatives*.parquet"))
    if not candidates:
        return None
    if days > 0:
        exact = [path for path in candidates if _funding_day_tag(path) == days]
        if exact:
            return exact[-1]
        covering = [path for path in candidates if _funding_day_tag(path) >= days]
        if covering:
            return min(covering, key=_funding_day_tag)
    return max(candidates, key=_funding_day_tag)


def _funding_range(path: str | Path) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    funding = pd.read_parquet(path) if str(path).endswith(".parquet") else pd.read_csv(path)
    if "available_at" not in funding.columns:
        return None
    times = pd.to_datetime(funding["available_at"], errors="coerce").dropna()
    if times.empty:
        return None
    return times.min(), times.max()


def add_funding_events(df: pd.DataFrame, funding_path: str | Path) -> tuple[pd.DataFrame, int]:
    funding = (
        pd.read_parquet(funding_path)
        if str(funding_path).endswith(".parquet")
        else pd.read_csv(funding_path)
    )
    if "available_at" not in funding.columns or "funding_rate" not in funding.columns:
        raise ValueError("funding data must contain available_at and funding_rate")

    events = funding[["available_at", "funding_rate"]].copy()
    events["available_at"] = pd.to_datetime(events["available_at"], errors="coerce")
    events["funding_rate"] = pd.to_numeric(events["funding_rate"], errors="coerce")
    events = events.dropna().sort_values("available_at")
    events = events[events["funding_rate"].ne(events["funding_rate"].shift())]

    out = df.copy()
    out["funding_rate"] = 0.0
    bar_times = _bar_datetimes(out).to_numpy()
    event_times = events["available_at"].to_numpy()
    indexes = np.searchsorted(bar_times, event_times, side="left")
    count = 0
    for idx, rate in zip(indexes, events["funding_rate"].to_numpy()):
        if 0 <= idx < len(out):
            out.at[int(idx), "funding_rate"] += float(rate)
            count += 1
    return out, count


def main():
    parser = argparse.ArgumentParser(description="布林带均值回归 Walk-Forward 回测")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="交易对")
    parser.add_argument("--interval", type=str, default="5m", help="K线周期")
    parser.add_argument("--days", type=int, default=7, help="回测多少天的数据")
    parser.add_argument(
        "--checkpoint", type=str, default="checkpoints/quant_model.pt", help="策略参数路径"
    )
    parser.add_argument(
        "--output", type=str, default="backtest_result.csv", help="回测结果输出文件"
    )
    parser.add_argument(
        "--long-only",
        action="store_true",
        help="强制只做多；覆盖 checkpoint 中的 enable_short 并移除做空信号",
    )
    parser.add_argument(
        "--bull-regime-filter",
        action="store_true",
        dest="legacy_bull_regime_filter",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--regime-filter",
        action="store_true",
        help="启用日线EMA regime过滤：BULL/NEUTRAL禁空，BEAR多空都允许",
    )
    parser.add_argument(
        "--regime-fast-days",
        dest="regime_fast_days",
        type=int,
        default=50,
        help="regime过滤快EMA天数",
    )
    parser.add_argument(
        "--regime-slow-days",
        dest="regime_slow_days",
        type=int,
        default=200,
        help="regime过滤慢EMA天数",
    )
    parser.add_argument(
        "--bull-fast-days",
        dest="regime_fast_days",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--bull-slow-days",
        dest="regime_slow_days",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-dd-guard",
        nargs="?",
        const=0.25,
        default=None,
        type=float,
        help="启用策略自身回撤熔断；不填数值时默认0.25，不传则关闭",
    )
    parser.add_argument(
        "--recovery-dd",
        type=float,
        default=0.15,
        help="回撤熔断恢复阈值，默认0.15",
    )
    parser.add_argument("--market-intel", type=str, default=None, help="冻结市场情报表路径")
    parser.add_argument(
        "--market-intel-max-age-hours",
        type=float,
        default=3.0,
        help="market-intel 最大有效小时数，默认3",
    )
    parser.add_argument(
        "--market-intel-unknown-mode",
        type=str,
        default="cautious",
        help="情报缺失/过期时使用的 mode，默认 cautious",
    )
    parser.add_argument(
        "--market-intel-random-control",
        action="store_true",
        help="启用同等 veto/半仓数量的随机对照",
    )
    parser.add_argument(
        "--market-intel-random-seed",
        type=int,
        default=42,
        help="market-intel 随机对照 seed，默认42",
    )
    parser.add_argument(
        "--funding-data",
        type=str,
        default=None,
        help="资金费率事件 CSV/Parquet；默认自动找 data/market_intel/*derivatives*.parquet",
    )
    parser.add_argument("--no-funding", action="store_true", help="关闭资金费率扣减")
    args = parser.parse_args()

    # 加载策略参数
    print("=" * 60)
    print("加载策略参数...")
    print("=" * 60)
    if not os.path.exists(args.checkpoint):
        print(f"错误: 未找到 {args.checkpoint}")
        sys.exit(1)

    checkpoint = load_checkpoint(args.checkpoint)
    params = checkpoint.get("params", {})
    strategy_type = checkpoint.get("strategy", "bollinger_trend_filter")
    strategy = build_strategy_from_checkpoint(checkpoint)
    print(f"策略类型: {strategy_type} | 参数: {params}")
    print()

    # 加载数据（从本地 parquet）
    print("=" * 60)
    print(f"加载 {args.symbol} {args.interval} 数据（最近 {args.days} 天）...")
    print("=" * 60)

    # list_crypto_files 已按 (symbol, interval) 分组去重
    all_files = list_crypto_files()
    data_files = [f for f in all_files if args.symbol.upper() in os.path.basename(f).upper()]
    # 优先匹配 interval
    interval_match = [f for f in data_files if f"_{args.interval}" in os.path.basename(f)]
    if interval_match:
        data_files = interval_match
    if not data_files:
        data_files = [
            f for f in list_crypto_files() if args.symbol.upper() in os.path.basename(f).upper()
        ]
    if not data_files:
        print(f"错误: 未找到 {args.symbol} 数据文件")
        sys.exit(1)

    df = load_crypto_data(data_files[0])
    df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    n_bars = args.days * 288
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].reset_index(drop=True)
    if not args.no_funding:
        funding_path = (
            Path(args.funding_data)
            if args.funding_data
            else _find_funding_file(args.symbol, args.days)
        )
        if funding_path and funding_path.exists():
            funding_range = _funding_range(funding_path)
            bar_times = _bar_datetimes(df)
            if funding_range and (
                funding_range[0] > bar_times.min() or funding_range[1] < bar_times.max()
            ):
                print(
                    "警告: funding 数据未覆盖完整回测区间 "
                    f"({funding_range[0]} ~ {funding_range[1]})"
                )
            df, funding_events = add_funding_events(df, funding_path)
            print(f"资金费率: 已载入 {funding_events} 个事件 ({funding_path})")
        else:
            print("警告: 未找到资金费率数据；本次回测不会扣 funding")
    print(f"数据量: {len(df)} 条K线, 价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}")
    print()

    # 执行回测
    enable_short = bool(params.get("enable_short", True)) and not args.long_only
    long_only = not enable_short
    if hasattr(strategy, "enable_short"):
        strategy.enable_short = enable_short

    print("=" * 60)
    mode_label = "只做多" if long_only else "多空双向"
    print(f"执行回测（{mode_label}，手续费+滑点模拟）...")
    print("=" * 60)
    raw_signals = generate_strategy_signals(strategy, df, enable_short=enable_short)
    signals = raw_signals.copy()
    regime_filter_stats = None
    if args.regime_filter or args.legacy_bull_regime_filter:
        regimes = build_daily_regime_labels(
            df, fast_days=args.regime_fast_days, slow_days=args.regime_slow_days
        )
        signals, regime_filter_stats = apply_regime_short_filter(signals, regimes, df)

    signals = normalize_signals_for_position_mode(signals, long_only=long_only)

    min_idx = getattr(strategy, "window", getattr(strategy, "long_ma_period", 20))
    prices = df["close"].values[min_idx:]
    valid_signals = signals[min_idx:]
    valid_df = df.iloc[min_idx:].reset_index(drop=True)
    drawdown_guard_stats = None
    if args.max_dd_guard is not None:
        valid_signals, drawdown_guard_stats = apply_drawdown_guard(
            valid_signals,
            prices,
            valid_df,
            max_dd_guard=args.max_dd_guard,
            recovery_dd=args.recovery_dd,
            initial_capital=INITIAL_CAPITAL,
            commission=COMMISSION,
            slippage=SLIPPAGE,
        )
        signals[min_idx:] = valid_signals

    market_intel_stats = None
    position_sizes = None
    if args.market_intel:
        position_sizes, market_intel_stats = build_market_intel_position_sizes(
            valid_df,
            valid_signals,
            args.market_intel,
            args.market_intel_max_age_hours,
            args.market_intel_unknown_mode,
        )

    evaluator = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL, commission=COMMISSION, slippage=SLIPPAGE
    )
    score, metrics, trades = evaluator.evaluate(valid_signals, prices, valid_df)
    n_trades = len([t for t in trades if t.get("pnl") is not None])
    funding_pnl = sum(float(t.get("funding_pnl", 0.0)) for t in trades)
    overlay_score = None
    overlay_metrics = None
    overlay_trades = None
    overlay_n_trades = None
    random_score = None
    random_metrics = None
    random_trades = None
    random_n_trades = None
    random_control_stats = None
    if position_sizes is not None:
        overlay_score, overlay_metrics, overlay_trades = evaluator.evaluate(
            valid_signals,
            prices,
            valid_df,
            position_sizes=position_sizes,
        )
        overlay_n_trades = len([t for t in overlay_trades if t.get("pnl") is not None])
        if args.market_intel_random_control and market_intel_stats is not None:
            random_position_sizes, random_control_stats = build_random_entry_control(
                valid_signals,
                veto_count=market_intel_stats.vetoed_new_entries,
                half_count=market_intel_stats.half_size_entries,
                seed=args.market_intel_random_seed,
            )
            random_score, random_metrics, random_trades = evaluator.evaluate(
                valid_signals,
                prices,
                valid_df,
                position_sizes=random_position_sizes,
            )
            random_n_trades = len([t for t in random_trades if t.get("pnl") is not None])

    benchmark_signals = buy_hold_signals(len(valid_signals))
    _, benchmark_metrics, benchmark_trades = evaluator.evaluate(benchmark_signals, prices, valid_df)
    benchmark_n_trades = len([t for t in benchmark_trades if t.get("pnl") is not None])
    excess_return = metrics["total_return"] - benchmark_metrics["total_return"]

    # 输出结果
    print()
    print("=" * 60)
    print("回测结果（使用训练评分器）")
    print("=" * 60)
    print(f"策略类型:    {strategy_type}")
    print(f"初始资金:    {INITIAL_CAPITAL:.2f} USDT")
    print(f"最终权益:    {INITIAL_CAPITAL * (1 + metrics['total_return']):.2f} USDT")
    print(f"综合评分:    {score:.4f}")
    print(f"总收益率:    {metrics['total_return'] * 100:.2f}%")
    if "funding_rate" in valid_df.columns:
        print(f"资金费率PnL: {funding_pnl:+.2f} USDT")
    print(f"年化收益率:  {metrics['annualized_return'] * 100:.2f}%")
    print(f"年化波动率:  {metrics['annualized_vol'] * 100:.2f}%")
    print(f"夏普比率:    {metrics['sharpe_ratio']:.4f}")
    print(f"最大回撤:    {metrics['max_drawdown'] * 100:.2f}%")
    print(f"胜率:        {metrics['win_rate'] * 100:.1f}%")
    print(f"交易次数:    {n_trades}")
    print()

    if overlay_metrics is not None:
        print("=" * 60)
        print("Overlay 回测结果（同一组信号 + market-intel 仓位倍率）")
        print("=" * 60)
        print(f"最终权益:    {INITIAL_CAPITAL * (1 + overlay_metrics['total_return']):.2f} USDT")
        print(f"综合评分:    {overlay_score:.4f}")
        print(f"总收益率:    {overlay_metrics['total_return'] * 100:.2f}%")
        print(f"年化收益率:  {overlay_metrics['annualized_return'] * 100:.2f}%")
        print(f"年化波动率:  {overlay_metrics['annualized_vol'] * 100:.2f}%")
        print(f"夏普比率:    {overlay_metrics['sharpe_ratio']:.4f}")
        print(f"最大回撤:    {overlay_metrics['max_drawdown'] * 100:.2f}%")
        print(f"胜率:        {overlay_metrics['win_rate'] * 100:.1f}%")
        print(f"交易次数:    {overlay_n_trades}")
        print()

    if market_intel_stats is not None:
        print_market_intel_stats(market_intel_stats, trades)

    if random_metrics is not None:
        print("=" * 60)
        print("随机对照回测结果（同一组信号 + 随机仓位倍率）")
        print("=" * 60)
        print(f"最终权益:    {INITIAL_CAPITAL * (1 + random_metrics['total_return']):.2f} USDT")
        print(f"综合评分:    {random_score:.4f}")
        print(f"总收益率:    {random_metrics['total_return'] * 100:.2f}%")
        print(f"年化收益率:  {random_metrics['annualized_return'] * 100:.2f}%")
        print(f"年化波动率:  {random_metrics['annualized_vol'] * 100:.2f}%")
        print(f"夏普比率:    {random_metrics['sharpe_ratio']:.4f}")
        print(f"最大回撤:    {random_metrics['max_drawdown'] * 100:.2f}%")
        print(f"胜率:        {random_metrics['win_rate'] * 100:.1f}%")
        print(f"交易次数:    {random_n_trades}")
        print()

    if random_control_stats is not None:
        print_random_control_stats(random_control_stats, trades)

    if regime_filter_stats is not None:
        print_regime_filter_stats(
            regime_filter_stats,
            fast_days=args.regime_fast_days,
            slow_days=args.regime_slow_days,
        )

    if drawdown_guard_stats is not None:
        print_drawdown_guard_stats(drawdown_guard_stats)

    print("=" * 60)
    print("买入持有基准（同一有效区间，含手续费+滑点）")
    print("=" * 60)
    print(f"基准收益率:  {benchmark_metrics['total_return'] * 100:.2f}%")
    print(f"基准年化:    {benchmark_metrics['annualized_return'] * 100:.2f}%")
    print(f"基准回撤:    {benchmark_metrics['max_drawdown'] * 100:.2f}%")
    print(f"基准交易:    {benchmark_n_trades}")
    print(f"超额收益:    {excess_return * 100:+.2f}%")
    print()

    if trades:
        trade_pnls = [
            (t.get("pnl"), t.get("type", "?")) for t in trades if t.get("pnl") is not None
        ]
        print("=" * 60)
        print(f"交易记录 ({len(trade_pnls)} 笔)")
        print("=" * 60)
        for i, (pnl, ttype) in enumerate(trade_pnls[-20:]):
            print(f"  [{i + 1:3d}] {ttype:15s} PnL={pnl:+.2f} USDT")
        print()

    signal_counts = pd.Series(signals).value_counts().sort_index()
    print()
    print("=" * 60)
    print("信号统计")
    print("=" * 60)
    labels = {0: "平仓 (CLOSE)", 1: "持有 (HOLD)", 2: "做多 (LONG)", 3: "做空 (SHORT)"}
    for sid, count in signal_counts.items():
        pct = count / len(signals) * 100
        print(f"  {labels.get(sid, f'未知({sid})'):18s}: {count:5d} 次 ({pct:5.2f}%)")
    print()

    return overlay_metrics or metrics


if __name__ == "__main__":
    result = main()
