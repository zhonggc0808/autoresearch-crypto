"""Replay a v2.1 regime_permission checkpoint against full data.

Verifies that backtest metrics match the search results saved in the checkpoint.

Usage:
    uv run python scripts/replay_v2_1_checkpoint.py \
        --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.data import list_crypto_files, load_crypto_data
from dex.indicators import compute_adx
from dex.regime_filter import build_daily_regime_labels
from dex.regime_permissions import (
    RiskOffConfig,
    apply_permission_arrays,
    build_permission_arrays,
    compute_daily_indicators,
    route_regime_signals,
)
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategy_signals import generate_strategy_signals


def _build_strategy(params: dict) -> ChannelBreakoutTrendStrategy:
    return ChannelBreakoutTrendStrategy(**params)


def run(checkpoint_path: str) -> None:
    # 1. load checkpoint
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    print(f"Checkpoint: {Path(checkpoint_path).name}")
    print(f"  type: {ckpt.get('strategy_type', 'unknown')}")
    print(f"  version: {ckpt.get('version', '?')}")
    print(f"  variant: {ckpt.get('variant', '?')}")
    print(f"  status: {ckpt.get('status', '?')}")

    expected_metrics = ckpt.get("metrics", {})
    if expected_metrics:
        print(f"  expected: ret={expected_metrics['oos_return_pct']:.1f}% "
              f"dd={expected_metrics['oos_max_drawdown_pct']:.1f}% "
              f"sharpe={expected_metrics['oos_sharpe']:.4f} "
              f"trades={expected_metrics['oos_trades']}")

    # 2. load data
    files = list_crypto_files()
    eth = [f for f in files if "ETHUSDT" in str(f)]
    c2600 = [f for f in eth if "2600d" in str(f)]
    path = c2600[0] if c2600 else eth[0]
    df = load_crypto_data(path).sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    print(f"\nData: {len(df)} bars, {df.iloc[0]['datetime']} ~ {df.iloc[-1]['datetime']}")

    split = int(len(df) * 0.7)
    df_oos = df.iloc[split:].reset_index(drop=True)
    print(f"IS: {split} bars | OOS: {len(df_oos)} bars")

    # 3. build strategies from checkpoint
    bull_params = ckpt["bull"]["strategy_params"]
    bear_params = ckpt["bear"]["strategy_params"]
    neutral_params = ckpt["neutral"]["strategy_params"]

    bull_cfg = RiskOffConfig(**ckpt["bull"]["permission"])
    bear_cfg = RiskOffConfig(**ckpt["bear"]["permission"])
    neutral_cfg = RiskOffConfig(**ckpt["neutral"]["permission"])

    policy = ckpt.get("regime_change_policy", "permission_based")

    print(f"\nBULL:  {ckpt['bull']['candidate']} — {ckpt['bull']['description']}")
    print(f"BEAR:  {ckpt['bear']['candidate']} — {ckpt['bear']['description']}")
    print(f"NEUTRAL: {ckpt['neutral']['candidate']} — {ckpt['neutral']['description']}")
    print(f"Policy: {policy}")

    # 4. generate per-regime signals
    bull_s = _build_strategy(bull_params)
    bear_s = _build_strategy(bear_params)
    neutral_s = _build_strategy(neutral_params)

    bull_raw = generate_strategy_signals(bull_s, df, enable_short=bull_params.get("enable_short", True))
    bear_raw = generate_strategy_signals(bear_s, df, enable_short=bear_params.get("enable_short", True))
    neutral_raw = generate_strategy_signals(neutral_s, df, enable_short=neutral_params.get("enable_short", True))

    # 5. regime labels + daily indicators
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)

    # 6. route + permissions
    routed = route_regime_signals(bull_raw, bear_raw, neutral_raw, regimes, regime_change_policy=policy)
    al, as_arr, ff, eo = build_permission_arrays(df, regimes, bull_cfg, bear_cfg, neutral_cfg, daily_ctx, adx_full)
    final_signals = apply_permission_arrays(routed, al, as_arr, ff, eo)

    # signal stats
    print(f"\nFull signals: 0={int((final_signals==0).sum())} 1={int((final_signals==1).sum())} "
          f"2={int((final_signals==2).sum())} 3={int((final_signals==3).sum())}")
    print(f"force_flat: {int(ff.sum())}  exit_only: {int(eo.sum())}")

    # 7. evaluate OOS
    oos_sig = final_signals[split:]
    oos_prices = df_oos["close"].values.astype(float)
    oos_bh = (oos_prices[-1] / oos_prices[0] - 1.0) * 100

    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    score, metrics, trade_log = ev.evaluate(oos_sig, oos_prices, df_oos)

    ret = metrics.get("total_return", 0) * 100
    dd = metrics.get("max_drawdown", 0) * 100
    sharpe = metrics.get("sharpe_ratio", 0)
    trades = len(trade_log) if trade_log else 0
    oos_bars = len(df_oos)

    # trades per year estimate
    years_est = oos_bars / (288 * 365)
    tpy = trades / years_est if years_est > 0 else 0

    print(f"\n{'='*60}")
    print(f"  REPLAY RESULT")
    print(f"{'='*60}")
    print(f"  OOS Return:     {ret:+.2f}%")
    print(f"  OOS Max DD:     {dd:.2f}%")
    print(f"  OOS Sharpe:     {sharpe:.4f}")
    print(f"  OOS Trades:     {trades}")
    print(f"  OOS Excess:     {ret - oos_bh:+.2f}%")
    print(f"  OOS B&H:        {oos_bh:+.2f}%")
    print(f"  Trades/year:    ~{tpy:.0f}")
    print(f"  Score:          {score:.4f}")

    # 8. compare with expected
    if expected_metrics:
        print(f"\n{'='*60}")
        print(f"  DELTA vs EXPECTED")
        print(f"{'='*60}")
        exp_ret = expected_metrics["oos_return_pct"]
        exp_dd = expected_metrics["oos_max_drawdown_pct"]
        exp_sharpe = expected_metrics["oos_sharpe"]
        exp_trades = expected_metrics["oos_trades"]

        ret_delta = ret - exp_ret
        dd_delta = dd - exp_dd
        sharpe_delta = sharpe - exp_sharpe
        trades_delta = trades - exp_trades

        print(f"  Return delta:  {ret_delta:+.2f}% {'PASS' if abs(ret_delta) < 2 else 'FAIL'}")
        print(f"  DD delta:      {dd_delta:+.2f}% {'PASS' if abs(dd_delta) < 2 else 'FAIL'}")
        print(f"  Sharpe delta:  {sharpe_delta:+.4f} {'PASS' if abs(sharpe_delta) < 0.05 else 'WARN'}")
        print(f"  Trades delta:  {trades_delta:+d} {'PASS' if abs(trades_delta) < 10 else 'FAIL'}")

        all_ok = (abs(ret_delta) < 2 and abs(dd_delta) < 2 and abs(trades_delta) < 10)
        if all_ok:
            print(f"\n  PASS — metrics consistent with search results")
        else:
            print(f"\n  FAIL — significant deviation from expected metrics")
            print(f"     Check: data split, regime labels, permission routing")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", type=str, default="checkpoints/channel_breakout_v2_1_balanced.pt")
    args = p.parse_args()
    run(args.checkpoint)
