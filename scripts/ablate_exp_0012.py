#!/usr/bin/env python3
"""ADX ablation: compare exp_0012 L250 with and without ADX 20 filter."""
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
prices_full = df_full["close"].values.astype(float)
prices_is = df_is["close"].values.astype(float)
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

# No ADX (threshold=0 disables filter)
signals_no_adx = apply_adx_filter(signals, adx, threshold=0, apply_to=["neutral", "bear"], regimes=regimes)
# ADX 20 (exp_0012)
signals_adx = apply_adx_filter(signals, adx, threshold=20, apply_to=["neutral", "bear"], regimes=regimes)

def compute_metrics(sig, prices, label):
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    score, metrics, trades = ev.evaluate(sig, prices)
    equity, trade_log = ev.simulate(sig, prices)
    trade_pnls = [t for t in trade_log if t.get("pnl") is not None]
    return {
        "return": metrics["total_return"],
        "dd": metrics["max_drawdown"],
        "sharpe": metrics["sharpe_ratio"],
        "win_rate": metrics["win_rate"],
        "trades": len(trade_pnls),
        "score": score,
    }

ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)

# OOS comparison
print("=" * 70)
print("  ADX ABLATION: L250 without ADX vs L250 + ADX20")
print("=" * 70)

for label, sig_suffix in [("OOS", prices_oos)]:
    sig_no = signals_no_adx[split:]
    sig_with = signals_adx[split:]
    m_no = compute_metrics(sig_no, prices_oos, "OOS")
    m_with = compute_metrics(sig_with, prices_oos, "OOS")

    print(f"\n  OOS (no ADX):        return={m_no['return']*100:+7.2f}%  DD={m_no['dd']*100:.2f}%  "
          f"Sharpe={m_no['sharpe']:.4f}  WR={m_no['win_rate']*100:.1f}%  trades={m_no['trades']}")
    print(f"  OOS (+ADX20):        return={m_with['return']*100:+7.2f}%  DD={m_with['dd']*100:.2f}%  "
          f"Sharpe={m_with['sharpe']:.4f}  WR={m_with['win_rate']*100:.1f}%  trades={m_with['trades']}")
    delta_r = m_with['return'] - m_no['return']
    delta_s = m_with['sharpe'] - m_no['sharpe']
    print(f"  ADX contribution:    return delta={delta_r*100:+.2f}%  Sharpe delta={delta_s:+.4f}")
    print()

# Blocked trades analysis
print("=" * 70)
print("  ADX BLOCKED TRADES ANALYSIS")
print("=" * 70)

diff_mask = signals_no_adx != signals_adx
n_blocked = diff_mask.sum()
blocked_indices = np.where(diff_mask)[0]
print(f"  Total bars where ADX blocked entry: {n_blocked}")
print(f"  All blocked at bars: {list(blocked_indices)}")

# Simulate what would happen if those blocked entries were taken
# For each blocked bar, replay no-ADX and ADX
eq_no, trades_no = ev.simulate(signals_no_adx, prices_full)
eq_with, trades_with = ev.simulate(signals_adx, prices_full)
trade_pnls_no = [t for t in trades_no if t.get("pnl") is not None]
trade_pnls_with = [t for t in trades_with if t.get("pnl") is not None]

print(f"\n  No-ADX trades: {len(trade_pnls_no)}  ADX trades: {len(trade_pnls_with)}")
print(f"  No-ADX total PnL: {sum(t['pnl'] for t in trade_pnls_no):+.2f}")
print(f"  ADX total PnL:    {sum(t['pnl'] for t in trade_pnls_with):+.2f}")
print(f"  PnL difference:   {sum(t['pnl'] for t in trade_pnls_with) - sum(t['pnl'] for t in trade_pnls_no):+.2f}")

# Find trades that differ between the two
# These are trades that happen in no-ADX but don't happen in ADX
no_trade_steps = set()
for t in trade_pnls_no:
    no_trade_steps.add(t.get("step", -1))
with_trade_steps = set()
for t in trade_pnls_with:
    with_trade_steps.add(t.get("step", -1))

blocked_trades_steps = no_trade_steps - with_trade_steps
new_trades_steps = with_trade_steps - no_trade_steps

print(f"\n  Trades blocked by ADX: {len(blocked_trades_steps)}")
print(f"  New trades in ADX path: {len(new_trades_steps)}")

# Show PnL of blocked trades
blocked_pnls = [t["pnl"] for t in trade_pnls_no if t.get("step") in blocked_trades_steps]
if blocked_pnls:
    print(f"  Blocked trades PnLs: {[f'{p:+.2f}' for p in blocked_pnls]}")
    print(f"  Sum of blocked PnLs: {sum(blocked_pnls):+.2f}")
    profitable = sum(1 for p in blocked_pnls if p > 0)
    print(f"  Profitable: {profitable}/{len(blocked_pnls)}")

print()
print(f"  No-ADX OOS return: {m_no['return']*100:.2f}%")
print(f"  ADX   OOS return:  {m_with['return']*100:.2f}%")
print(f"  Delta:             {(m_with['return']-m_no['return'])*100:+.2f}%")
print()
print("Done.")
