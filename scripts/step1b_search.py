"""
Step 1b: Top 3 策略公平 IS/OOS 随机搜索对比。
不跑 GEPA，不写 checkpoint。每个策略同样的候选预算。
"""
import os, sys, time, random, itertools
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dex.data import list_crypto_files, load_crypto_data
from dex.strategies import (
    AdaptiveHybridStrategy, ChannelBreakoutTrendStrategy, HybridMeanRevMomentumStrategy,
)
from dex.strategies.base import StrategyEvaluator
from dex.strategy_signals import generate_strategy_signals

EVALUATOR = StrategyEvaluator()
N_SAMPLES = 800  # 每个策略随机采样数

# ---------------------------------------------------------------------------
# 参数空间（从 train_all.py 扩展）
# ---------------------------------------------------------------------------
STRATEGIES = [
    ("HybridMM", HybridMeanRevMomentumStrategy, {
        "rsi_period":     [5, 7, 10, 14],
        "rsi_low":        [20, 25, 28, 30, 35],
        "rsi_high":       [65, 70, 72, 75, 78, 80],
        "ma_period":      [10, 15, 20, 25, 30, 40, 50],
        "atr_period":     [7, 10, 12, 14],
        "atr_multiplier": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
        "max_hold_bars":  [6, 12, 18, 24, 36, 48],
        "enable_short":   [True],
    }),
    ("Adaptive", AdaptiveHybridStrategy, {
        "rsi_period":     [14],
        "rsi_low":        [20, 25, 28, 30, 35],
        "rsi_high":       [65, 68, 70, 72, 75, 78],
        "ma_period":      [10, 15, 20, 25],
        "trend_long_ma":  [50, 80, 100, 150, 200],
        "trend_pull_ma":  [8, 10, 12, 15, 20],
        "adx_threshold":  [18, 20, 22, 25, 28, 30],
        "adx_period":     [14],
        "atr_period":     [7, 10, 14],
        "atr_multiplier": [1.5, 2.0, 2.5, 3.0, 3.5],
        "max_hold_bars":  [12, 18, 24, 36, 48],
    }),
    ("ChannelBreakout", ChannelBreakoutTrendStrategy, {
        "entry_lookback":      [200, 250, 300, 350, 375, 400, 425, 450, 475, 500,
                                525, 550, 576, 600, 700, 800, 1000, 2000, 4000, 8000],
        "min_hold_bars":       [0, 72, 144, 288, 432, 576, 720, 1008],
        "cooldown_bars":       [0],
        "emergency_stop_pct":  [0.0],
        "enable_long":         [True],
        "enable_short":        [True],
    }),
]

# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def relaxed_score(equity, trades, prices):
    if len(equity) == 0 or not np.all(np.isfinite(equity)):
        return 0.0, {}
    metrics = EVALUATOR.compute_metrics(equity, trades)
    ret, dd = metrics["total_return"], abs(metrics["max_drawdown"])
    sharpe = max(-3.0, min(5.0, metrics["sharpe_ratio"]))
    wr = metrics["win_rate"]
    n_trades = len([t for t in trades if t.get("pnl") is not None])
    if ret <= -0.95 or n_trades < 3:
        return 0.0, metrics
    mkt = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0
    excess = max(-0.50, min(2.0, ret - mkt))
    return (
        max(0, min(1, (excess + 0.10) / 0.30)) * 0.30 +
        max(0, min(1, (sharpe + 1.0) / 4.0)) * 0.20 +
        max(0, min(1, 1.0 - dd / 0.50)) * 0.20 +
        max(0, min(1, (wr - 0.35) / 0.30)) * 0.15 +
        min(1, n_trades / 20.0) * 0.15
    ), metrics

def evaluate(params, cls, df):
    try:
        s = cls(**{k: v for k, v in params.items()
                    if k in cls.__init__.__code__.co_varnames})
    except Exception:
        return 0.0, {"total_return": -1, "sharpe_ratio": -10, "max_drawdown": -1, "win_rate": 0}, 0
    signals = generate_strategy_signals(s, df, enable_short=True)
    warmup = max(1, int(getattr(s, "warmup_bars", getattr(s, "window", 20))))
    if len(df) <= warmup + 50:
        return 0.0, {"total_return": -1}, 0
    valid_signals = signals[warmup:]
    prices = df["close"].to_numpy(dtype=float)[warmup:]
    try:
        equity, trades = EVALUATOR.simulate(valid_signals, prices)
    except Exception:
        return 0.0, {"total_return": -1}, 0
    score, metrics = relaxed_score(equity, trades, prices)
    n_trades = len([t for t in trades if t.get("pnl") is not None])
    return score, metrics, n_trades

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
print("=" * 80)
print("Step 1b: Top 3 公平 IS/OOS 随机搜索")
print(f"预算: 每策略 {N_SAMPLES} 组随机参数")
print("=" * 80)

# 加载 2600d
files = [f for f in list_crypto_files() if "ETHUSDT" in os.path.basename(f).upper() and "_5m" in os.path.basename(f)]
data_file = [f for f in files if "2600d" in f][0]
df = load_crypto_data(data_file)
df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)

# 70/30 拆分
n = len(df)
split = int(n * 0.7)
df_is = df.iloc[:split].reset_index(drop=True)
df_oos = df.iloc[split:].reset_index(drop=True)

is_mkt = (df_is["close"].iloc[-1] / df_is["close"].iloc[0] - 1) * 100
oos_mkt = (df_oos["close"].iloc[-1] / df_oos["close"].iloc[0] - 1) * 100
print(f"\nIS:  {len(df_is)} bars, {df_is['datetime'].iloc[0]} ~ {df_is['datetime'].iloc[-1]}, 买持 {is_mkt:+.1f}%")
print(f"OOS: {len(df_oos)} bars, {df_oos['datetime'].iloc[0]} ~ {df_oos['datetime'].iloc[-1]}, 买持 {oos_mkt:+.1f}%")

# 对每个策略做随机搜索
print(f"\n{'策略':<20} {'IS收益%':>10} {'IS夏普':>8} {'IS回撤%':>9} {'OOS收益%':>10} {'OOS夏普':>8} {'OOS回撤%':>9} {'OOS交易':>8} {'胜率%':>7}")
print("-" * 95)

for name, cls, space in STRATEGIES:
    keys = list(space.keys())
    total_space = 1
    for v in space.values():
        total_space *= len(v)
    n_sample = min(N_SAMPLES, total_space)

    # 生成随机组合（保证唯一）
    seen = set()
    samples = []
    while len(samples) < n_sample:
        combo = tuple(random.choice(space[k]) for k in keys)
        if combo not in seen:
            seen.add(combo)
            samples.append(dict(zip(keys, combo)))

    t0 = time.time()
    best_is_score = -1
    best_params = None

    for params in samples:
        score, _, _ = evaluate(params, cls, df_is)
        if score > best_is_score:
            best_is_score = score
            best_params = params

    # IS 冠军 → OOS 盲测
    score_is, m_is, t_is = evaluate(best_params, cls, df_is)
    score_oos, m_oos, t_oos = evaluate(best_params, cls, df_oos)

    elapsed = time.time() - t0
    print(
        f"{name:<20} {m_is['total_return']*100:>10.2f} {m_is['sharpe_ratio']:>8.4f} "
        f"{m_is['max_drawdown']*100:>9.2f} {m_oos['total_return']*100:>10.2f} "
        f"{m_oos['sharpe_ratio']:>8.4f} {m_oos['max_drawdown']*100:>9.2f} "
        f"{t_oos:>8} {m_oos['win_rate']*100:>7.1f}  ({elapsed:.0f}s, {n_sample} trials)"
    )

print(f"\nIS 买持: {is_mkt:+.1f}%  |  OOS 买持: {oos_mkt:+.1f}%")
