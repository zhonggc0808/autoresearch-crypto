#!/usr/bin/env python3
"""Create exp_0012 checkpoint for OKX demo (signal-only)."""
import json, torch
from pathlib import Path

exp = json.loads(Path("research_workspace/candidates/exp_0012.json").read_text())["params"]

ckpt = {
    "strategy_type": "regime_permission_channel_breakout",
    "version": "2.1",
    "variant": "exp_0012_L250_ADX20",
    "regime_change_policy": "permission_based",
    "regime_filter": {"fast_days": 50, "slow_days": 200},
    "data": {"symbol": "ETHUSDT", "interval": "5m", "data_source": "oracle_candidate_exp_0012"},
    "status": "demo_candidate_signal_only",
    "note": "ADX filter is oracle-only. Demo runs L250 core (no ADX).",
}

for regime in ["bull", "bear", "neutral"]:
    ckpt[regime] = {
        "candidate": exp[regime]["candidate"],
        "description": exp[regime].get("description", ""),
        "strategy_params": exp[regime]["strategy_params"],
        "permission": exp[regime]["permission"],
    }

torch.save(ckpt, "checkpoints/channel_breakout_exp_0012.pt")
print("Created checkpoints/channel_breakout_exp_0012.pt")
