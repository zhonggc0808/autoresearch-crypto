#!/usr/bin/env python3
"""Diagnose exp_0014: why transition guard works in OOS but fails in IS."""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dex.regime_filter import build_daily_regime_labels
from dex.config import COMMISSION, SLIPPAGE

PRJ = Path(__file__).resolve().parents[1]
df = pd.read_parquet(str(PRJ / "data" / "crypto" / "ETHUSDT_5m_1300d.parquet"))
df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
split = int(len(df) * 0.70)

regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
regimes_is = regimes[:split]
regimes_oos = regimes[split:]

# Regime transition analysis
print("=" * 70)
print("  REGIME TRANSITION DISTRIBUTION")
print("=" * 70)
for reg_seg, seg_name in [(regimes_is, "IS"), (regimes_oos, "OOS")]:
    transitions = 0
    flip_bars = []
    prev = str(reg_seg[0]) if len(reg_seg) > 0 else "NEUTRAL"
    for i in range(1, len(reg_seg)):
        curr = str(reg_seg[i])
        if curr != prev:
            transitions += 1
            flip_bars.append(i)
            prev = curr
    bars = len(reg_seg)
    bull = (reg_seg == "BULL").mean() * 100
    bear = (reg_seg == "BEAR").mean() * 100
    neut = (reg_seg == "NEUTRAL").mean() * 100
    print(f"\n  {seg_name}: BULL={bull:.0f}% BEAR={bear:.0f}% NEUTRAL={neut:.0f}%")
    print(f"    Transitions: {transitions} over {bars/(288*365):.1f} yr")
    print(f"    Avg between flips: {bars/max(transitions,1)/288:.1f} days")
    print(f"    Flip density: {transitions/bars*10000:.2f} per 10K bars")

    # Transition direction counts
    dirs = {}
    prev = str(reg_seg[0])
    for i in range(1, len(reg_seg)):
        curr = str(reg_seg[i])
        if curr != prev:
            pair = f"{prev}->{curr}"
            dirs[pair] = dirs.get(pair, 0) + 1
            prev = curr
    for pair, count in sorted(dirs.items()):
        print(f"    {pair:20s}: {count}")

print()
print("=" * 70)
print("  SIGNAL QUALITY NEAR REGIME FLIPS")
print("=" * 70)

import torch
from dex.checkpoints import load_checkpoint, build_strategy_from_checkpoint
from dex.strategies.base import StrategyEvaluator

ckpt = torch.load(str(PRJ / "checkpoints" / "exp_0014_adaptive_lb_transition_guard.pt"), map_location="cpu", weights_only=False)
strategy = build_strategy_from_checkpoint(ckpt)
sig_full = strategy.generate_signals(df)
sig_is = sig_full[:split]
sig_oos = sig_full[split:]

ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)

for sig_seg, reg_seg, prices, seg_name in [
    (sig_is, regimes_is, df.iloc[:split]["close"].values.astype(float), "IS"),
    (sig_oos, regimes_oos, df.iloc[split:]["close"].values.astype(float), "OOS"),
]:
    print(f"\n  {seg_name}:")

    # Find regime flip bars
    flip_bars = []
    prev = str(reg_seg[0])
    for i in range(1, len(reg_seg)):
        curr = str(reg_seg[i])
        if curr != prev:
            flip_bars.append(i)
            prev = curr

    # Classify trades
    eq, trades = ev.simulate(sig_seg, prices)
    trade_pnls = [t for t in trades if t.get("pnl") is not None]

    flip_trades = [t for t in trade_pnls if any(abs(t.get("step", -1) - fb) <= 48 for fb in flip_bars)]
    normal_trades = [t for t in trade_pnls if not any(abs(t.get("step", -1) - fb) <= 48 for fb in flip_bars)]

    for tlist, label in [(flip_trades, "Flip"), (normal_trades, "Normal")]:
        pnls = [t["pnl"] for t in tlist]
        if pnls:
            wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            print(f"    {label} trades: {len(tlist)}  WR={wr:.0f}%  "
                  f"Avg={np.mean(pnls):+.2f}  Total={sum(pnls):+.2f}  "
                  f"Med={np.median(pnls):+.2f}")
        else:
            print(f"    {label} trades: 0")

    # Trade density around flips
    if flip_trades:
        steps = [t.get("step", 0) for t in flip_trades]
        density = len(steps) / max(len(flip_bars), 1)
        print(f"    Flip trade density: {density:.2f} trades per flip event")

print()
print("Done.")
