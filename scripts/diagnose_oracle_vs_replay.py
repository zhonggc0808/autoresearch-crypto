#!/usr/bin/env python3
"""Diagnose discrepancy between replay and oracle metric baselines.

Replay: +252% OOS / -33.6% DD (on 2600d data)
Oracle: +97.9% OOS / -45.8% DD (on 1300d data)

This script isolates each difference: data range, split point, signal
generation, evaluation engine, and safe-execution semantics.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE, BARS_PER_DAY_5M
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


# ---------------------------------------------------------------------------
# Replay pipeline (from analyze_v21_execution_semantics.py)
# ---------------------------------------------------------------------------

def replay_v21_pipeline(ckpt, df):
    """Exact v2.1 signal pipeline as used in analyze_v21_execution_semantics.py."""
    bull_s = ChannelBreakoutTrendStrategy(**ckpt["bull"]["strategy_params"])
    bear_s = ChannelBreakoutTrendStrategy(**ckpt["bear"]["strategy_params"])
    neutral_s = ChannelBreakoutTrendStrategy(**ckpt["neutral"]["strategy_params"])

    bull_cfg = RiskOffConfig(**ckpt["bull"]["permission"])
    bear_cfg = RiskOffConfig(**ckpt["bear"]["permission"])
    neutral_cfg = RiskOffConfig(**ckpt["neutral"]["permission"])

    policy = ckpt.get("regime_change_policy", "permission_based")

    bull_raw = generate_strategy_signals(bull_s, df, enable_short=bull_s.enable_short)
    bear_raw = generate_strategy_signals(bear_s, df, enable_short=bear_s.enable_short)
    neutral_raw = generate_strategy_signals(neutral_s, df, enable_short=neutral_s.enable_short)

    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)

    routed = route_regime_signals(
        bull_raw, bear_raw, neutral_raw, regimes, regime_change_policy=policy
    )
    al, as_arr, ff, eo = build_permission_arrays(
        df, regimes, bull_cfg, bear_cfg, neutral_cfg, daily_ctx, adx_full
    )
    final_signals = apply_permission_arrays(routed, al, as_arr, ff, eo)
    return final_signals, regimes


def safe_execution_signals(signals):
    out = signals.copy()
    position = 0
    for i in range(len(out)):
        raw = out[i]
        if raw == 2:
            if position == -1:
                out[i] = 0; position = 0
            else:
                position = 1
        elif raw == 3:
            if position == 1:
                out[i] = 0; position = 0
            else:
                position = -1
        elif raw == 0:
            position = 0
    return out


def evaluate_signals(signals, prices, label=""):
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    score, metrics, trades = ev.evaluate(signals, prices)
    equity, _ = ev.simulate(signals, prices)
    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n = len(trade_pnls)
    n_bars = len(signals)
    years = n_bars / (288 * 365) if n_bars > 0 else 0.01
    tpy = n / years if years > 0 else 0

    print(f"  {label}")
    print(f"    Return:  {metrics['total_return']*100:+.2f}%")
    print(f"    DD:      {metrics['max_drawdown']*100:.2f}%")
    print(f"    Sharpe:  {metrics['sharpe_ratio']:.4f}")
    print(f"    Trades:  {n} ({tpy:.0f}/yr)")
    print(f"    Score:   {score:.4f}")
    print()
    return metrics


def load_data_safe(filepath):
    """Load parquet, sort, deduplicate."""
    import pyarrow.parquet as pq
    table = pq.read_table(str(filepath))
    df = table.to_pandas()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ckpt_path = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    ckpt = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)

    data_dir = PROJECT_DIR / "data" / "crypto"

    # ---------------------------------------------------------------
    # Test 1: Compare data files
    # ---------------------------------------------------------------
    print("=" * 60)
    print("  TEST 1: Data file comparison")
    print("=" * 60)

    for tag in ["1300d", "2600d"]:
        f = data_dir / f"ETHUSDT_5m_{tag}.parquet"
        if f.exists():
            df = load_data_safe(f)
            print(f"  {tag}: {len(df)} bars, "
                  f"{df.iloc[0].get('datetime','?')} → {df.iloc[-1].get('datetime','?')}")
        else:
            print(f"  {tag}: NOT FOUND")

    # ---------------------------------------------------------------
    # Test 2: Same split ratio, different data length
    # ---------------------------------------------------------------
    print()
    print("=" * 60)
    print("  TEST 2: OOS evaluation on same 70/30 split, different data")
    print("=" * 60)

    for tag in ["1300d", "2600d"]:
        f = data_dir / f"ETHUSDT_5m_{tag}.parquet"
        if not f.exists():
            continue
        df = load_data_safe(f)
        split = int(len(df) * 0.7)
        df_oos = df.iloc[split:].reset_index(drop=True)
        prices_oos = df_oos["close"].values.astype(float)

        signals = replay_v21_pipeline(ckpt, df_oos)[0]
        safe_sigs = safe_execution_signals(signals)

        print(f"--- {tag} OOS (raw) ---")
        evaluate_signals(signals, prices_oos, "raw")

        print(f"--- {tag} OOS (safe-execution) ---")
        evaluate_signals(safe_sigs, prices_oos, "safe-exec")

        # Show time range
        print(f"  OOS range: {df_oos.iloc[0].get('datetime','?')} → {df_oos.iloc[-1].get('datetime','?')}")
        print(f"  OOS bars: {len(df_oos)}")
        print()

    # ---------------------------------------------------------------
    # Test 3: IS vs OOS DD breakdown
    # ---------------------------------------------------------------
    print("=" * 60)
    print("  TEST 3: IS vs OOS DD breakdown (1300d)")
    print("=" * 60)

    df = load_data_safe(data_dir / "ETHUSDT_5m_1300d.parquet")
    split = int(len(df) * 0.7)
    df_is = df.iloc[:split].reset_index(drop=True)
    df_oos = df.iloc[split:].reset_index(drop=True)

    sigs_is = replay_v21_pipeline(ckpt, df_is)[0]
    sigs_oos = replay_v21_pipeline(ckpt, df_oos)[0]
    sigs_full = np.concatenate([sigs_is, sigs_oos])
    prices_full = np.concatenate([df_is["close"].values, df_oos["close"].values])

    # IS
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    _, m_is, _ = ev.evaluate(sigs_is, df_is["close"].values.astype(float))
    print(f"  IS   return={m_is['total_return']*100:+.2f}%  DD={m_is['max_drawdown']*100:.2f}%  "
          f"sharpe={m_is['sharpe_ratio']:.4f}")

    # OOS
    _, m_oos, _ = ev.evaluate(sigs_oos, df_oos["close"].values.astype(float))
    print(f"  OOS  return={m_oos['total_return']*100:+.2f}%  DD={m_oos['max_drawdown']*100:.2f}%  "
          f"sharpe={m_oos['sharpe_ratio']:.4f}")

    # Full
    _, m_full, _ = ev.evaluate(sigs_full, prices_full)
    print(f"  FULL return={m_full['total_return']*100:+.2f}%  DD={m_full['max_drawdown']*100:.2f}%  "
          f"sharpe={m_full['sharpe_ratio']:.4f}")

    # DD_OVER_50 check
    print(f"\n  DD_OVER_50 flag applies to IS data only.")
    print(f"  IS DD = {m_is['max_drawdown']*100:.1f}% {'→ REJECT' if m_is['max_drawdown'] < -0.50 else '→ OK'}")
    print(f"  OOS DD = {m_oos['max_drawdown']*100:.1f}%")

    # ---------------------------------------------------------------
    # Test 4: Find worst rolling 6m window
    # ---------------------------------------------------------------
    print()
    print("=" * 60)
    print("  TEST 4: Worst rolling 6-month windows")
    print("=" * 60)

    window_bars = 6 * 30 * BARS_PER_DAY_5M
    step = window_bars // 2
    worst_return = float("inf")
    worst_info = None
    all_returns = []

    for start in range(0, len(sigs_full) - window_bars, step):
        end = start + window_bars
        win_sigs = sigs_full[start:end]
        win_prices = prices_full[start:end]
        _, wm, _ = ev.evaluate(win_sigs, win_prices)
        ret = wm["total_return"]
        all_returns.append((start, end, ret))
        if ret < worst_return:
            worst_return = ret
            worst_info = (start, end, ret, wm)

    # Print top 3 worst
    all_returns.sort(key=lambda x: x[2])
    for i, (s, e, r) in enumerate(all_returns[:3]):
        t_start = df.iloc[s].get("datetime", "?") if s < len(df) else "?"
        t_end = df.iloc[min(e, len(df)-1)].get("datetime", "?")
        regimes = build_daily_regime_labels(df.iloc[s:e], fast_days=50, slow_days=200)
        bull_pct = (regimes == "BULL").mean() * 100
        bear_pct = (regimes == "BEAR").mean() * 100
        neutral_pct = (regimes == "NEUTRAL").mean() * 100
        print(f"  #{i+1}: {t_start} → {t_end}")
        print(f"       return={r*100:+.1f}%  DD={all_returns[i][3].get('max_drawdown',0)*100 if len(all_returns[i])>3 else 0:.1f}%")
        print(f"       regime BULL={bull_pct:.0f}% BEAR={bear_pct:.0f}% NEUTRAL={neutral_pct:.0f}% (bars={e-s})")

    print()

    # ---------------------------------------------------------------
    # Test 5: Compare evaluator settings
    # ---------------------------------------------------------------
    print("=" * 60)
    print("  TEST 5: Evaluator settings comparison")
    print("=" * 60)
    print(f"  COMMISSION:      {COMMISSION} ({COMMISSION*10000:.0f}bp)")
    print(f"  SLIPPAGE:        {SLIPPAGE} ({SLIPPAGE*10000:.0f}bp)")
    print(f"  INITIAL_CAPITAL: {INITIAL_CAPITAL}")
    print(f"  Evaluator:       StrategyEvaluator.simulate() + evaluate()")
    print(f"  Compounding:     full (reinvest all capital each trade)")
    print()

    # ---------------------------------------------------------------
    # Test 6: Signal identity check (are replay and oracle producing same signals?)
    # ---------------------------------------------------------------
    print("=" * 60)
    print("  TEST 6: Signal identity check")
    print("=" * 60)

    df1300 = load_data_safe(data_dir / "ETHUSDT_5m_1300d.parquet")
    sigs_from_script = replay_v21_pipeline(ckpt, df1300)[0]

    # Also run oracle's _generate_v21_signals
    from scripts.research_oracle import _generate_v21_signals as oracle_v21
    sigs_from_oracle = oracle_v21(ckpt, df1300)

    mismatch = (sigs_from_script != sigs_from_oracle).sum()
    print(f"  Signal mismatch: {mismatch} / {len(sigs_from_script)} bars")
    if mismatch == 0:
        print("  → Signals IDENTICAL. Discrepancy is NOT from signal generation.")
    else:
        print(f"  → {mismatch} differences found! Signal generation diverges.")
        # Show first few differences
        diff_idx = np.where(sigs_from_script != sigs_from_oracle)[0][:5]
        for idx in diff_idx:
            print(f"    bar {idx}: replay={sigs_from_script[idx]}, oracle={sigs_from_oracle[idx]}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
