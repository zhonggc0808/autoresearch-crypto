#!/usr/bin/env python3
"""Generate exp_0013_adaptive_lb_v0.pt checkpoint.

Parameters:
  - BULL:       entry_lookback=375 (standard, long-only)
  - BEAR:       entry_lookback=250 (faster, proven alpha source)
  - NEUTRAL:    entry_lookback=500 (wider, reduce chop in ranging)
  - min_hold:   432 (from v2.1 balanced)
  - permissions: inherited from v2.1 balanced

This is NOT a regime_permission strategy — it uses the
AdaptiveChannelBreakoutTrendStrategy which handles per-regime
lookback internally via the daily EMA50/200 regime labels.
"""
import torch
from pathlib import Path

ckpt = {
    "strategy": "adaptive_channel_breakout",
    "params": {
        "entry_lookback": 375,           # default (fallback)
        "bull_entry_lookback": 375,      # BULL: standard
        "bear_entry_lookback": 250,      # BEAR: fast (exp_0012 finding)
        "neutral_entry_lookback": 500,   # NEUTRAL: wide (reduce chop)
        "min_hold_bars": 432,
        "enable_long": True,
        "enable_short": True,
        "emergency_stop_pct": 0.0,
        "breakout_buffer_pct": 0.0,
        "breakout_atr_buffer": 0.0,
        "take_profit_pct": 0.0,
        "stop_loss_pct": 0.0,
    },
    "description": "exp_0013_adaptive_lb_v0 — adaptive lookback per regime",
    "status": "research_candidate",
    "note": "Phase 3C candidate. BEAR=250, BULL=375, NEUTRAL=500. Validates regime-adaptive lookback concept.",
}

torch.save(ckpt, "checkpoints/exp_0013_adaptive_lb_v0.pt")
print(f"Created checkpoints/exp_0013_adaptive_lb_v0.pt")
