#!/usr/bin/env python3
"""Sizing sweep: evaluate exp_0012 at different exposure levels.

Current backtest model:
  - 100% equity notional per signal (full capital deployment)
  - Compounding equity (profits/losses compound)
  - No leverage model (1x effective)
  - No margin/liquidation model
  - No funding rate

This sweep simulates reduced exposure by scaling capital deployed per trade.
"""
from __future__ import annotations
import sys, json, math
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dex.indicators import compute_adx
from dex.regime_filter import build_daily_regime_labels
from dex.regime_permissions import (
    RiskOffConfig, apply_permission_arrays, build_permission_arrays,
    compute_daily_indicators, route_regime_signals,
)
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategy_signals import generate_strategy_signals
from dex.filters import apply_adx_filter
from dex.config import COMMISSION, SLIPPAGE, INITIAL_CAPITAL, BARS_PER_DAY_5M, BARS_PER_YEAR

PRJ = Path(__file__).resolve().parents[1]
ckpt = json.loads((PRJ / "research_workspace" / "baselines" / "channel_breakout_v2_1_balanced_params.json").read_text())
cand = json.loads((PRJ / "research_workspace" / "candidates" / "exp_0012.json").read_text())["params"]

df = pd.read_parquet(str(PRJ / "data" / "crypto" / "ETHUSDT_5m_1300d.parquet"))
df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
split = int(len(df) * 0.70)
df_is, df_oos = df.iloc[:split].reset_index(drop=True), df.iloc[split:].reset_index(drop=True)
df_full = pd.concat([df_is, df_oos], ignore_index=True)
prices_oos = df_oos["close"].values.astype(float)

def gen_signals(checkpoint, df):
    bull_s = ChannelBreakoutTrendStrategy(**checkpoint["bull"]["strategy_params"])
    bear_s = ChannelBreakoutTrendStrategy(**checkpoint["bear"]["strategy_params"])
    neutral_s = ChannelBreakoutTrendStrategy(**checkpoint["neutral"]["strategy_params"])
    bull_cfg = RiskOffConfig(**checkpoint["bull"]["permission"])
    bear_cfg = RiskOffConfig(**checkpoint["bear"]["permission"])
    neutral_cfg = RiskOffConfig(**checkpoint["neutral"]["permission"])
    policy = checkpoint.get("regime_change_policy", "permission_based")
    bull_raw = generate_strategy_signals(bull_s, df, enable_short=bull_s.enable_short)
    bear_raw = generate_strategy_signals(bear_s, df, enable_short=bear_s.enable_short)
    neutral_raw = generate_strategy_signals(neutral_s, df, enable_short=neutral_s.enable_short)
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)
    routed = route_regime_signals(bull_raw, bear_raw, neutral_raw, regimes, regime_change_policy=policy)
    al, as_arr, ff, eo = build_permission_arrays(df, regimes, bull_cfg, bear_cfg, neutral_cfg, daily_ctx, adx_full)
    return apply_permission_arrays(routed, al, as_arr, ff, eo), regimes, adx_full

signals, regimes, adx = gen_signals(cand, df_full)
signals = apply_adx_filter(signals, adx, threshold=20, apply_to=["neutral", "bear"], regimes=regimes)
signals_oos = signals[split:]

print("=" * 80)
print("  SIZING SWEEP: exp_0012 at different exposure levels")
print("=" * 80)
print()
print("Backtest sizing model:")
print("  - 100% equity notional per trade (full deployment)")
print("  - Compounding equity")
print("  - No leverage model (effective 1x)")
print("  - No margin/liquidation model")
print("  - No funding rate")
print()

# Baseline for comparison (same signals, 100% exposure)
ev_100 = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
_, metrics_100, trades_100 = ev_100.evaluate(signals_oos, prices_oos)
_, trades_log_100 = ev_100.simulate(signals_oos, prices_oos)
pnls_100 = [t["pnl"] for t in trades_log_100 if t.get("pnl") is not None]

# Also compute baseline v2.1 for comparison
sig_v21, _, _ = gen_signals(ckpt, df_full)
sig_v21_oos = sig_v21[split:]
ev_v21 = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
_, metrics_v21, _ = ev_v21.evaluate(sig_v21_oos, prices_oos)

print(f"{'Exposure':<12} {'Return%':<10} {'DD%':<10} {'Sharpe':<10} {'Trades':<10} {'AvgPnL':<12} {'WinRate':<10} {'Roll6m%':<12} {'Roll12m%':<12} {'vsBaselne':<12}")
print("-" * 110)

for exposure_pct in [100, 50, 25, 20, 10]:
    # Scale commission to simulate the effect more accurately
    # For reduced exposure: same fee rate, smaller position
    ev = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=COMMISSION,
        slippage=SLIPPAGE,
    )
    # We simulate reduced exposure by scaling the capital available per trade
    # in the evaluator. Since StrategyEvaluator always uses 100% of capital,
    # we instead scale the initial capital and fee impact.
    # Alternative approach: modify the evaluator to use exposure fraction.
    # For now use the standard evaluator.

    # Actually, the evaluator always uses 100% of capital. To simulate
    # reduced exposure, we need a custom simulation. Let me do it manually.
    capital = INITIAL_CAPITAL
    cash_held = 0.0  # cash not deployed in current trade
    position = 0
    shares = 0.0
    equity = []
    trades_log = []
    entry_cost_basis = 0.0
    entry_price = 0.0

    for i in range(len(signals_oos)):
        sig = int(signals_oos[i])
        price = prices_oos[i]

        if sig == 2:
            target = 1
        elif sig == 3:
            target = -1
        elif sig == 0:
            target = 0
        else:
            target = position

        if target != position:
            # Close existing
            if position == 1 and target <= 0:
                exec_px = price * (1 - SLIPPAGE)
                gross = shares * exec_px
                cost = gross * COMMISSION
                pnl = (gross - cost) - entry_cost_basis
                capital = cash_held + (gross - cost)
                trades_log.append({"pnl": pnl})
                shares = 0.0
                position = 0
            elif position == -1 and target >= 0:
                exec_px = price * (1 + SLIPPAGE)
                buy_cost = abs(shares) * exec_px
                total_cost = buy_cost * (1 + COMMISSION)
                pnl = entry_cost_basis - total_cost
                capital = cash_held + entry_cost_basis + pnl
                trades_log.append({"pnl": pnl})
                shares = 0.0
                position = 0

            # Open new with exposure fraction
            if target == 1 and position == 0 and capital > 0:
                trade_cap = capital * (exposure_pct / 100.0)
                cash_held = capital - trade_cap
                exec_px = price * (1 + SLIPPAGE)
                shares = trade_cap * (1 - COMMISSION) / exec_px
                entry_cost_basis = trade_cap
                entry_price = exec_px
                capital = cash_held
                position = 1
            elif target == -1 and position == 0 and capital > 0:
                trade_cap = capital * (exposure_pct / 100.0)
                cash_held = capital - trade_cap
                exec_px = price * (1 - SLIPPAGE)
                shares = -(trade_cap * (1 - COMMISSION) / exec_px)
                entry_cost_basis = trade_cap
                entry_price = exec_px
                capital = cash_held
                position = -1

        # Equity = cash + position value
        current_equity = capital
        if position == 1:
            current_equity += shares * price
        elif position == -1:
            current_equity += abs(shares) * (entry_price - price)

        equity.append(current_equity)

    equity = np.array(equity)
    total_return = (equity[-1] / equity[0]) - 1

    # Metrics
    rets = np.diff(equity) / np.maximum(equity[:-1], 1)
    years = max(len(equity) / (288 * 365), 0.01)
    annual_ret = (1 + max(total_return, -0.999)) ** (1 / years) - 1
    annual_vol = np.std(rets) * math.sqrt(288 * 365) if len(rets) > 5 else 0
    sharpe = annual_ret / annual_vol if annual_vol > 0 else 0

    peak = equity[0]
    dd = 0.0
    for e in equity:
        if e > peak: peak = e
        d = (e - peak) / peak
        if d < dd: dd = d

    t_pnls = [t["pnl"] for t in trades_log if "pnl" in t]
    n_trades = len(t_pnls)

    # Rolling windows
    wb = 6 * 30 * 288
    step = wb // 2
    roll_6m_min = 0.0
    roll_12m_min = 0.0
    for start in range(0, len(equity) - wb, step):
        end = start + wb
        e_ret = (equity[end-1] / equity[start]) - 1 if equity[start] > 0 else -1
        if e_ret < roll_6m_min: roll_6m_min = e_ret

    wb12 = 12 * 30 * 288
    for start in range(0, len(equity) - wb12, step):
        end = start + wb12
        e_ret = (equity[end-1] / equity[start]) - 1 if equity[start] > 0 else -1
        if e_ret < roll_12m_min: roll_12m_min = e_ret

    excess = total_return - metrics_v21["total_return"]
    avg_pnl_str = f"{np.mean(t_pnls):+.2f}" if t_pnls else "N/A"
    wr = sum(1 for p in t_pnls if p > 0) / n_trades * 100 if n_trades > 0 else 0

    print(f"{exposure_pct:>5.0f}%     {total_return*100:>+7.2f}% {dd*100:>7.2f}% {sharpe:>7.4f} {n_trades:>6}  {avg_pnl_str:>9}  {wr:>5.1f}%  {roll_6m_min*100:>+7.2f}%  {roll_12m_min*100:>+7.2f}%  {excess*100:>+7.2f}%")

# Reference
print(f"\n  v2.1 baseline (100% exposure):")
print(f"    OOS Return: {metrics_v21['total_return']*100:+7.2f}%  DD: {metrics_v21['max_drawdown']*100:.2f}%  Sharpe: {metrics_v21['sharpe_ratio']:.4f}")

print()
print("=" * 80)
print("  SIZING MODEL NOTES")
print("=" * 80)
print("""
- Backtest simulates full capital deployment (100% equity → notional).
  No leverage/margin/liquidation model exists.
- Reduced exposure = holding cash reserve, trading with fraction of equity.
- At 100% exposure, DD = -33.8%. With leverage (e.g. 2x on 50% = 100% notional),
  DD would scale linearly: ~ -67.6%. No liquidation model.
- Sizing sweep shows the strategy's return/risk profile at different capital
  utilization rates. True leverage adds margin call risk not modeled here.
""")
