#!/usr/bin/env python3
"""Diagnose exp_0012 (ADX 20, neutral+bear) — regime decomposition + ROLLING_NEGATIVE analysis."""
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

# Load data
df = pd.read_parquet(str(PRJ / "data" / "crypto" / "ETHUSDT_5m_1300d.parquet"))
df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
split = int(len(df) * 0.70)
df_is, df_oos = df.iloc[:split].reset_index(drop=True), df.iloc[split:].reset_index(drop=True)
df_full = pd.concat([df_is, df_oos], ignore_index=True)

prices_full = df_full["close"].values.astype(float)

# Generate signals (same as exp_0012 pipeline)
def gen_v21(checkpoint, df):
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

signals, regimes, adx = gen_v21(cand, df_full)
signals_raw = signals.copy()

# Apply ADX filter (mirrors exp_0012 config)
signals_filtered = apply_adx_filter(signals, adx, threshold=20, apply_to=["neutral", "bear"], regimes=regimes)

ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)

# =============================================
# 1. ADX ACTIVATION STATS
# =============================================
print("=" * 70)
print("  1. ADX FILTER ACTIVATION STATS")
print("=" * 70)

for regime_label in ["BULL", "BEAR", "NEUTRAL"]:
    mask = regimes == regime_label
    n_total = int(mask.sum())
    n_adx_low = int((mask & (adx < 20)).sum())
    # Count entries in raw signals (2 or 3) that got blocked (changed to 1)
    raw_entries = (signals_raw == 2) | (signals_raw == 3)
    filtered_entries = (signals_filtered == 2) | (signals_filtered == 3)
    n_entries_raw = int((raw_entries & mask).sum())
    n_entries_final = int((filtered_entries & mask).sum())
    n_blocked = int(((raw_entries & ~filtered_entries) & mask).sum())
    pass_rate = adx[mask].mean()
    print(f"  {regime_label}:")
    print(f"    bars={n_total}  ADX<20 bars={n_adx_low} ({n_adx_low/n_total*100:.1f}%)")
    print(f"    entries raw={n_entries_raw}  after_filter={n_entries_final}  blocked={n_blocked}")
    print(f"    mean ADX={pass_rate:.1f}")

# =============================================
# 2. REGIME DECOMPOSITION: PnL by regime
# =============================================
print()
print("=" * 70)
print("  2. REGIME PnL DECOMPOSITION")
print("=" * 70)

# Full equity curve
equity_full, trades_full = ev.simulate(signals_filtered, prices_full)
returns = np.diff(equity_full) / equity_full[:-1]
bar_returns = np.zeros(len(signals_filtered))
bar_returns[1:] = returns

for regime_label in ["BULL", "BEAR", "NEUTRAL"]:
    mask = regimes == regime_label
    regime_signals = np.where(mask, signals_filtered, 1)
    eq, trades = ev.simulate(regime_signals, prices_full)
    total_ret = (eq[-1] / eq[0]) - 1 if len(eq) > 0 else 0
    peak = eq[0]
    dd = 0.0
    for e in eq:
        if e > peak: peak = e
        d = (e - peak) / peak
        if d < dd: dd = d
    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)
    profit_trades = sum(1 for t in trade_pnls if t["pnl"] > 0)
    wr = profit_trades / n_trades if n_trades > 0 else 0
    avg_pnl = np.mean([t["pnl"] for t in trade_pnls]) if trade_pnls else 0
    print(f"  {regime_label}:")
    print(f"    return={total_ret*100:+7.2f}%  DD={dd*100:.2f}%  trades={n_trades}  WR={wr*100:.1f}%  avg_pnl={avg_pnl:+.2f}")

# =============================================
# 3. ROLLING_NEGATIVE — find all negative 6m windows
# =============================================
print()
print("=" * 70)
print("  3. ROLLING 6-MONTH WINDOW ANALYSIS")
print("=" * 70)

window_bars = 6 * 30 * BARS_PER_DAY_5M
step = window_bars // 2
windows = []
for start in range(0, len(signals_filtered) - window_bars, step):
    end = start + window_bars
    win_sigs = signals_filtered[start:end]
    win_prices = prices_full[start:end]
    _, wm, _ = ev.evaluate(win_sigs, win_prices)
    ret = wm["total_return"]
    dd = wm["max_drawdown"]
    win_regimes = regimes[start:end]
    bull_pct = float((win_regimes == "BULL").mean())
    bear_pct = float((win_regimes == "BEAR").mean())
    neut_pct = float((win_regimes == "NEUTRAL").mean())
    dt_start = df_full.iloc[start].get("datetime", "?") if start < len(df_full) else "?"
    dt_end = df_full.iloc[min(end, len(df_full)-1)].get("datetime", "?")
    windows.append((ret, dd, bull_pct, bear_pct, neut_pct, dt_start, dt_end))

windows.sort(key=lambda x: x[0])
print(f"  Worst 3 windows (out of {len(windows)} total):")
for i, (ret, dd, bp, bep, np_, ds, de) in enumerate(windows[:3]):
    print(f"  #{i+1}: {ds} -> {de}")
    print(f"       return={ret*100:+7.1f}%  DD={dd*100:.1f}%  regime: BULL={bp*100:.0f}% BEAR={bep*100:.0f}% NEUTRAL={np_*100:.0f}%")

# =============================================
# 4. BASELINE VS FILTERED: per-regime trade comparison
# =============================================
print()
print("=" * 70)
print("  4. RAW vs FILTERED: trade comparison")
print("=" * 70)

_, trades_raw = ev.simulate(signals_raw, prices_full)
_, trades_filt = ev.simulate(signals_filtered, prices_full)
print(f"  Raw trades: {len([t for t in trades_raw if t.get('pnl')])}")
print(f"  Filtered trades: {len([t for t in trades_filt if t.get('pnl')])}")
print(f"  Difference: {len([t for t in trades_filt if t.get('pnl')]) - len([t for t in trades_raw if t.get('pnl')])}")

# PnL comparison
raw_pnls = [t["pnl"] for t in trades_raw if t.get("pnl")]
filt_pnls = [t["pnl"] for t in trades_filt if t.get("pnl")]
if raw_pnls:
    print(f"  Raw avg PnL: {np.mean(raw_pnls):+.2f}  median: {np.median(raw_pnls):+.2f}")
if filt_pnls:
    print(f"  Filtered avg PnL: {np.mean(filt_pnls):+.2f}  median: {np.median(filt_pnls):+.2f}")

print()
print("Done.")
