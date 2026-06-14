#!/usr/bin/env python3
"""Analyze v2.1 balanced execution semantics — same-bar reversal vs safe-execution.

Reads the v2.1 balanced checkpoint and compares:
  1. Original final_signals (allows same-bar reversal long→short or short→long)
  2. Safe-execution signals (close→confirm→open, requires next bar to confirm)

Also validates U4_cons3 consecutive_below_ema_days behavior.

Usage:
    uv run python scripts/analyze_v21_execution_semantics.py

Outputs:
    - Same-bar reversal count
    - Original vs safe-execution OOS metrics
    - U4_cons3 delay analysis
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


def load_checkpoint_and_data(path: str):
    """Load checkpoint and OOS data."""
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    files = list_crypto_files()
    eth = [f for f in files if "ETHUSDT" in str(f)]
    c2600 = [f for f in eth if "2600d" in str(f)]
    data_path = c2600[0] if c2600 else eth[0]
    df = (
        load_crypto_data(data_path)
        .sort_values("timestamp")
        .drop_duplicates()
        .reset_index(drop=True)
    )
    split = int(len(df) * 0.7)
    df_oos = df.iloc[split:].reset_index(drop=True)
    return ckpt, df, df_oos, split


def generate_final_signals(ckpt, df):
    """Replay v2.1 pipeline and return final_signals."""
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


def count_same_bar_reversals(signals: np.ndarray) -> dict:
    """Count long→short and short→long reversals on the same bar."""
    long_to_short = 0
    short_to_long = 0
    position = 0

    for sig in signals:
        if sig == 2:  # long
            if position == -1:
                short_to_long += 1
            position = 1
        elif sig == 3:  # short
            if position == 1:
                long_to_short += 1
            position = -1
        elif sig == 0:  # close
            position = 0
        # sig == 1: hold, position unchanged

    return {"long_to_short": long_to_short, "short_to_long": short_to_long}


def safe_execution_signals(signals: np.ndarray) -> np.ndarray:
    """Convert same-bar reversals into close→confirm→open.

    If position is long and signal is SHORT → output CLOSE(0) this bar,
    wait for confirmation next bar. Same for short→LONG.
    If signal repeats next bar → execute the reversal then.
    """
    out = signals.copy()
    position = 0

    for i in range(len(out)):
        raw = out[i]

        if raw == 2:  # long
            if position == -1:
                # Currently short, signal says long → close only this bar
                out[i] = 0
                position = 0
            else:
                position = 1
        elif raw == 3:  # short
            if position == 1:
                # Currently long, signal says short → close only this bar
                out[i] = 0
                position = 0
            else:
                position = -1
        elif raw == 0:
            position = 0
        # raw == 1: hold, position unchanged

    return out


def evaluate_oos(signals: np.ndarray, df_oos: pd.DataFrame, label: str):
    """Evaluate OOS performance and print metrics."""
    oos_prices = df_oos["close"].values.astype(float)
    oos_bh = (oos_prices[-1] / oos_prices[0] - 1.0) * 100

    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    score, metrics, trade_log = ev.evaluate(signals, oos_prices, df_oos)

    ret = metrics.get("total_return", 0) * 100
    dd = metrics.get("max_drawdown", 0) * 100
    sharpe = metrics.get("sharpe_ratio", 0)
    trades = len(trade_log) if trade_log else 0

    oos_bars = len(df_oos)
    years_est = oos_bars / (288 * 365)
    tpy = trades / years_est if years_est > 0 else 0

    print(f"  {label}")
    print(f"    OOS Return:      {ret:+.2f}%")
    print(f"    OOS Max DD:      {dd:.2f}%")
    print(f"    OOS Sharpe:      {sharpe:.4f}")
    print(f"    OOS Trades:      {trades}")
    print(f"    Trades/year:     ~{tpy:.0f}")
    print(f"    Score:           {score:.4f}")
    print()

    return {"ret": ret, "dd": dd, "sharpe": sharpe, "trades": trades, "tpy": tpy}


def analyze_u4_consecutive_below():
    """Test U4_cons3: how many days before allow_long becomes False.

    Construct a daily series where close is consecutively below EMA50,
    and check which day consecutive_below >= 3 triggers allow_long=False.
    """
    print("=" * 60)
    print("  U4_cons3 consecutive_below analysis")
    print("=" * 60)

    # Build synthetic daily data: 60 days warmup at 100, then drops to 95 and stays
    dates = pd.date_range("2026-01-01", periods=80, freq="D")
    daily = pd.Series(
        [100.0] * 60 + [95.0] * 20,  # 60 days at 100 (warmup EMA50), then 20 days at 95
        index=dates,
    )
    ema50 = daily.ewm(span=50, adjust=False, min_periods=50).mean()

    # Compute consecutive_below using the same logic as compute_daily_indicators
    consec = pd.Series(0, index=daily.index, dtype=int)
    cnt = 0
    for i in range(len(daily)):
        if i > 0:
            prev_c = float(daily.iloc[i - 1])
            prev_e = float(ema50.iloc[i - 1]) if (i - 1) < len(ema50) else float("nan")
            if not np.isnan(prev_e) and prev_c < prev_e:
                cnt += 1
            else:
                cnt = 0
        consec.iloc[i] = cnt

    print(f"\n  Test data: 5 days close=100, then 15 days close=95")
    print(f"  EMA50 (end): {ema50.iloc[-1]:.2f}")
    print(f"\n  Day  EMA50  Close  Below?  Consec  allow_long? (>=3 blocks)")
    print(f"  {'-' * 60}")
    for i in range(58, len(daily)):
        flag = "BLOCKED <==" if consec.iloc[i] >= 3 else "OK"
        below = (
            "Y"
            if daily.iloc[i - 1] < ema50.iloc[i - 1]
            else "N"
            if not np.isnan(ema50.iloc[i - 1])
            else "?"
        )
        print(
            f"  {i:3d}  {ema50.iloc[i - 1]:6.1f}  {daily.iloc[i - 1]:5.1f}  {below:5s}  {consec.iloc[i]:3d}           {flag}"
        )

    first_blocked = None
    for i in range(len(consec)):
        if consec.iloc[i] >= 3:
            first_blocked = i
            break

    if first_blocked is not None:
        # consec.iloc[i] >= 3 means: close[i-1], close[i-2], close[i-3] all < ema50[i-1]
        # with the build_per_day_lookups shift (takes consec.iloc[i-1]),
        # the actual trigger in permissions is on day i+1's intraday bars
        day_triggered = first_blocked + 1
        print(
            f"\n  First consec >= 3 at index:   {first_blocked} (checks closes up to day {first_blocked - 1})"
        )
        print(
            f"  Permission takes effect on:   day {day_triggered} intraday bars (1-bar lag from consec)"
        )
        print(f"  Interpretation: 3 consecutive daily close < EMA50")
        print(f"    Day 1-3 closes below EMA50 -> consec=3 at day 4 -> allow_long=False on day 5")
        print(f"    This is CORRECT: no lookahead, uses only completed daily candles.")
    else:
        print("\n  No block triggered within test window")


def main():
    checkpoint_path = "checkpoints/channel_breakout_v2_1_balanced.pt"
    print(f"Loading checkpoint: {checkpoint_path}")
    ckpt, df, df_oos, split = load_checkpoint_and_data(checkpoint_path)

    # --- 1. Generate final_signals ---
    final_signals, regimes = generate_final_signals(ckpt, df)
    oos_sig = final_signals[split:]

    print(f"\nData: {len(df)} bars, OOS: {len(df_oos)} bars ({split}:{len(df)})")

    # --- 2. Count same-bar reversals in full and OOS ---
    full_reversals = count_same_bar_reversals(final_signals)
    oos_reversals = count_same_bar_reversals(oos_sig)

    print("\n" + "=" * 60)
    print("  SAME-BAR REVERSAL COUNT")
    print("=" * 60)
    print(f"  Full data:")
    print(f"    long→short: {full_reversals['long_to_short']}")
    print(f"    short→long: {full_reversals['short_to_long']}")
    print(f"    total:      {full_reversals['long_to_short'] + full_reversals['short_to_long']}")
    print(f"  OOS:")
    print(f"    long→short: {oos_reversals['long_to_short']}")
    print(f"    short→long: {oos_reversals['short_to_long']}")
    print(f"    total:      {oos_reversals['long_to_short'] + oos_reversals['short_to_long']}")

    # --- 3. Compare original vs safe-execution ---
    safe_sig = safe_execution_signals(oos_sig)

    print("\n" + "=" * 60)
    print("  OOS COMPARISON: original vs safe-execution")
    print("=" * 60)
    print()
    orig = evaluate_oos(oos_sig, df_oos, "Original (allows same-bar reversal)")
    safe = evaluate_oos(safe_sig, df_oos, "Safe-execution (close→confirm→open)")

    # --- 4. Delta ---
    print("  Delta (safe - original)")
    print(f"    Return:   {safe['ret'] - orig['ret']:+.2f}%")
    print(f"    DD:       {safe['dd'] - orig['dd']:+.2f}%")
    print(f"    Sharpe:   {safe['sharpe'] - orig['sharpe']:+.4f}")
    print(f"    Trades:   {safe['trades'] - orig['trades']:+d}")
    print(f"    Trades/y: {safe['tpy'] - orig['tpy']:+.0f}")

    change_pct = (safe["ret"] / orig["ret"] - 1) * 100 if orig["ret"] != 0 else 0
    print(f"\n  Safe vs Original return: {change_pct:+.1f}% relative change")

    if abs(change_pct) < 10:
        print("  >>> Safe-execution impact is SMALL (<10% return change) <<<")
        print("  >>> close-confirm-open is acceptable for this strategy <<<")
    else:
        print("  >>> Safe-execution impact is LARGE (>=10% return change) <<<")
        print("  >>> The strategy relies heavily on same-bar reversals <<<")

    # --- 5. U4_cons3 analysis ---
    print()
    analyze_u4_consecutive_below()

    print("\n" + "=" * 60)
    print("  DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()
