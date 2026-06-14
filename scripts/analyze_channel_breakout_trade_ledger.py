"""ChannelBreakout trade ledger — per-trade attribution for drawdown analysis.

Loads 2600d ETH data + 375/432 checkpoint, generates v2 regime-filter signals,
simulates bar-by-bar, and outputs a per-trade CSV with regime, direction, PnL,
MAE, MFE and auxiliary indicators.

Usage:
    uv run python scripts/analyze_channel_breakout_trade_ledger.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure project root is on sys.path when run from scripts/ subdirectory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dex.checkpoints import build_strategy_from_checkpoint, load_checkpoint
from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.data import list_crypto_files, load_crypto_data
from dex.indicators import compute_adx, compute_atr, compute_ema
from dex.regime_filter import apply_regime_short_filter, build_daily_regime_labels
from dex.strategy_signals import generate_strategy_signals

# ── paths ───────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path("search_results")
OUTPUT_DIR.mkdir(exist_ok=True)
LEDGER_CSV = OUTPUT_DIR / "channel_breakout_trade_ledger_2600d_v2.csv"
SUMMARY_JSON = OUTPUT_DIR / "channel_breakout_trade_ledger_2600d_v2_summary.json"

# ── helpers ─────────────────────────────────────────────────────────────────


def _compute_ema_slope(close: np.ndarray, period: int = 50) -> np.ndarray:
    """Per-bar EMA slope (fractional change over `period` bars)."""
    ema = compute_ema(close, period)
    slope = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        prev = ema[max(0, i - period)]
        slope[i] = (ema[i] / prev - 1.0) if prev > 0 else 0.0
    return slope


@dataclass
class TradeRecord:
    entry_bar: int = 0
    exit_bar: int = 0
    entry_time: str = ""
    exit_time: str = ""
    entry_regime: str = ""
    exit_regime: str = ""
    direction: str = ""  # LONG or SHORT
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    return_pct: float = 0.0
    mae: float = 0.0  # max adverse excursion (worst floating loss)
    mfe: float = 0.0  # max favourable excursion (best floating profit)
    bars_held: int = 0
    entry_adx: float = float("nan")
    entry_atr: float = float("nan")
    entry_ema_slope: float = float("nan")
    exit_reason: str = ""
    equity_before: float = 0.0
    equity_after: float = 0.0
    peak_equity: float = 0.0
    dd_from_peak: float = 0.0


@dataclass
class SimulationState:
    capital: float = INITIAL_CAPITAL
    shares: float = 0.0
    position: int = 0  # 0=flat, 1=long, -1=short
    entry_bar: int = 0
    entry_price: float = 0.0
    entry_cost: float = 0.0
    equity: float = INITIAL_CAPITAL
    peak_equity: float = INITIAL_CAPITAL
    trades: list = field(default_factory=list)


# ── main ────────────────────────────────────────────────────────────────────


def main() -> None:
    # 1. load data
    files = list_crypto_files()
    eth = [f for f in files if "ETHUSDT" in str(f) and "2600d" in str(f)]
    if not eth:
        eth = [f for f in files if "ETHUSDT" in str(f) and "5m" in str(f)]
    df = load_crypto_data(eth[0])
    df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    print(f"Data: {len(df)} bars, {df.iloc[0]['datetime']} ~ {df.iloc[-1]['datetime']}")

    # 2. load strategy
    ckpt = load_checkpoint("checkpoints/channel_breakout_375_432.pt")
    strategy = build_strategy_from_checkpoint(ckpt)
    print(f"Strategy: entry={strategy.entry_lookback}, min_hold={strategy.min_hold_bars}")

    # 3. signals + regime filter (v2: BULL+NEUTRAL 禁空)
    raw = generate_strategy_signals(strategy, df, enable_short=True)
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    signals, filter_stats = apply_regime_short_filter(raw, regimes, df)
    print(
        f"Regime: BULL={(regimes=='BULL').sum()} "
        f"BEAR={(regimes=='BEAR').sum()} "
        f"NEUTRAL={(regimes=='NEUTRAL').sum()}"
    )
    print(f"Filter: blocked={filter_stats.blocked_short_signals} closed={filter_stats.closed_short_positions}")

    # 4. auxiliary indicators
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    dts = df["datetime"].values
    adx_series, plus_di, minus_di = compute_adx(df, 14)
    atr_series = compute_atr(df, 14)
    ema_slope = _compute_ema_slope(close, 50)

    # 5. simulate
    state = SimulationState()
    in_trade = False
    current_entry_record: TradeRecord | None = None
    all_trades: list[TradeRecord] = []
    equity_curve = np.full(len(signals), np.nan)
    window = strategy.window

    for i in range(window, len(signals)):
        signal = int(signals[i])
        price = close[i]
        regime = str(regimes[i])

        # compute current equity
        if state.position == 1:
            state.equity = state.capital + state.shares * price
        elif state.position == -1:
            state.equity = state.capital + abs(state.shares) * (state.entry_price - price)
        else:
            state.equity = state.capital

        equity_curve[i] = state.equity
        state.peak_equity = max(state.peak_equity, state.equity)

        # determine target position
        if signal == 2:
            target = 1
        elif signal == 3:
            target = -1
        elif signal == 0:
            target = 0
        else:
            target = state.position

        # track MAE/MFE for open position
        if current_entry_record is not None and state.position != 0:
            if state.position == 1:
                floating_pnl = (price - state.entry_price) / state.entry_price
                current_entry_record.mfe = max(current_entry_record.mfe, floating_pnl)
                current_entry_record.mae = min(current_entry_record.mae, floating_pnl)
            elif state.position == -1:
                floating_pnl = (state.entry_price - price) / state.entry_price
                current_entry_record.mfe = max(current_entry_record.mfe, floating_pnl)
                current_entry_record.mae = min(current_entry_record.mae, floating_pnl)

        # position change → close + open
        if target != state.position:
            # --- close existing ---
            if state.position != 0 and current_entry_record is not None:
                rec = current_entry_record
                rec.exit_bar = i
                rec.exit_time = str(dts[i])
                rec.exit_regime = regime

                if state.position == 1:
                    exec_price = price * (1.0 - SLIPPAGE)
                    gross = state.shares * exec_price
                    cost = gross * COMMISSION
                    state.capital = gross - cost
                    rec.pnl = state.capital - rec.equity_before
                    rec.return_pct = (rec.pnl / rec.equity_before) * 100
                elif state.position == -1:
                    exec_price = price * (1.0 + SLIPPAGE)
                    buy_cost = abs(state.shares) * exec_price
                    buy_cost_total = buy_cost * (1.0 + COMMISSION)
                    state.capital = max(0.0, state.entry_cost + (state.entry_cost - buy_cost_total))
                    rec.pnl = state.capital - rec.equity_before
                    rec.return_pct = (rec.pnl / rec.equity_before) * 100

                rec.equity_after = state.capital
                rec.bars_held = i - rec.entry_bar
                rec.exit_reason = "signal_change"
                rec.dd_from_peak = (state.peak_equity - state.capital) / state.peak_equity * 100
                rec.peak_equity = state.peak_equity
                all_trades.append(rec)
                current_entry_record = None
                state.shares = 0.0
                state.position = 0

            # --- open new ---
            if target in {1, -1} and state.capital > 1.0:
                rec = TradeRecord()
                rec.entry_bar = i
                rec.entry_time = str(dts[i])
                rec.entry_regime = regime
                rec.direction = "LONG" if target == 1 else "SHORT"
                rec.equity_before = state.capital
                rec.peak_equity = state.peak_equity
                rec.entry_adx = float(adx_series[i]) if not np.isnan(adx_series[i]) else float("nan")
                rec.entry_atr = float(atr_series[i]) if not np.isnan(atr_series[i]) else float("nan")
                rec.entry_ema_slope = float(ema_slope[i]) if not np.isnan(ema_slope[i]) else float("nan")

                if target == 1:
                    exec_price = price * (1.0 + SLIPPAGE)
                    rec.entry_price = exec_price
                    rec.shares = state.capital * (1.0 - COMMISSION) / exec_price
                    state.entry_cost = state.capital
                    state.shares = rec.shares  # type: ignore[assignment]
                else:
                    exec_price = price * (1.0 - SLIPPAGE)
                    rec.entry_price = exec_price
                    state.shares = -(state.capital * (1.0 - COMMISSION) / exec_price)  # type: ignore[assignment]
                    state.entry_cost = state.capital

                state.entry_price = exec_price
                state.position = target
                state.entry_bar = i
                state.capital = 0.0
                current_entry_record = rec

    # close final position
    if state.position != 0 and current_entry_record is not None:
        i = len(signals) - 1
        rec = current_entry_record
        rec.exit_bar = i
        rec.exit_time = str(dts[i])
        rec.exit_regime = str(regimes[i])
        rec.exit_reason = "final"
        price = close[i]
        if state.position == 1:
            exec_price = price * (1.0 - SLIPPAGE)
            gross = state.shares * exec_price
            cost = gross * COMMISSION
            state.capital = gross - cost
        else:
            exec_price = price * (1.0 + SLIPPAGE)
            buy_cost = abs(state.shares) * exec_price
            buy_cost_total = buy_cost * (1.0 + COMMISSION)
            state.capital = max(0.0, state.entry_cost + (state.entry_cost - buy_cost_total))
        rec.pnl = state.capital - rec.equity_before
        rec.return_pct = (rec.pnl / rec.equity_before) * 100
        rec.equity_after = state.capital
        rec.bars_held = i - rec.entry_bar
        rec.dd_from_peak = (state.peak_equity - state.capital) / state.peak_equity * 100
        rec.peak_equity = state.peak_equity
        all_trades.append(rec)

    # 6. output CSV
    rows = []
    for t in all_trades:
        rows.append(
            {
                "entry_bar": t.entry_bar,
                "exit_bar": t.exit_bar,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_regime": t.entry_regime,
                "exit_regime": t.exit_regime,
                "direction": t.direction,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "pnl": round(t.pnl, 2),
                "return_pct": round(t.return_pct, 2),
                "mae_pct": round(t.mae * 100, 2),
                "mfe_pct": round(t.mfe * 100, 2),
                "bars_held": t.bars_held,
                "entry_adx": round(t.entry_adx, 2) if not np.isnan(t.entry_adx) else "",
                "entry_atr": round(t.entry_atr, 2) if not np.isnan(t.entry_atr) else "",
                "entry_ema_slope": round(t.entry_ema_slope, 6) if not np.isnan(t.entry_ema_slope) else "",
                "exit_reason": t.exit_reason,
                "equity_before": round(t.equity_before, 2),
                "equity_after": round(t.equity_after, 2),
                "dd_from_peak_pct": round(t.dd_from_peak, 2),
            }
        )
    ledger_df = pd.DataFrame(rows)
    ledger_df.to_csv(LEDGER_CSV, index=False)
    print(f"\nTrade ledger saved: {LEDGER_CSV} ({len(all_trades)} trades)")

    # 7. summary
    # 7a. by regime × direction
    print("\n" + "=" * 80)
    print("Per regime × direction summary")
    print("=" * 80)
    for regime in ["BULL", "BEAR", "NEUTRAL"]:
        for direction in ["LONG", "SHORT"]:
            subset = [t for t in all_trades if t.entry_regime == regime and t.direction == direction]
            if not subset:
                continue
            n = len(subset)
            total_pnl = sum(t.pnl for t in subset)
            avg_pnl = total_pnl / n
            wins = sum(1 for t in subset if t.pnl > 0)
            avg_mae = sum(t.mae for t in subset) / n * 100
            worst_mae = min(t.mae for t in subset) * 100
            avg_mfe = sum(t.mfe for t in subset) / n * 100
            print(
                f"  {regime:8s} {direction:5s}: "
                f"n={n:3d}  total_pnl={total_pnl:+.0f}  avg={avg_pnl:+.0f}  "
                f"win_rate={wins/n*100:.0f}%  avg_mae={avg_mae:+.1f}%  "
                f"worst_mae={worst_mae:+.1f}%  avg_mfe={avg_mfe:+.1f}%"
            )

    # 7b. top 10 DD-contributing trades
    print("\n" + "=" * 80)
    print("Top 10 worst return trades")
    print("=" * 80)
    sorted_trades = sorted(all_trades, key=lambda t: t.return_pct)
    for i, t in enumerate(sorted_trades[:10]):
        print(
            f"  {i+1:2d}. {t.entry_regime:8s} {t.direction:5s} "
            f"pnl={t.pnl:+.0f} ({t.return_pct:+.1f}%) "
            f"mae={t.mae*100:+.1f}%  held={t.bars_held}d "
            f"entry_adx={t.entry_adx:.1f}  slope={t.entry_ema_slope:.4f}"
        )

    # 7c. DD period analysis
    # find max DD period in equity curve
    peak_idx = window
    max_dd = 0.0
    max_dd_start = window
    max_dd_end = window
    peak_val = INITIAL_CAPITAL
    for i in range(window, len(equity_curve)):
        if np.isnan(equity_curve[i]):
            continue
        if equity_curve[i] > peak_val:
            peak_val = equity_curve[i]
            peak_idx = i
        dd = (peak_val - equity_curve[i]) / peak_val
        if dd > max_dd:
            max_dd = dd
            max_dd_start = peak_idx
            max_dd_end = i

    print("\n" + "=" * 80)
    print(f"Max DD period: {max_dd*100:.1f}% from bar {max_dd_start} to {max_dd_end}")
    print(f"  {dts[max_dd_start]} ~ {dts[max_dd_end]}")
    dd_trades = [t for t in all_trades if max_dd_start <= t.entry_bar <= max_dd_end or max_dd_start <= t.exit_bar <= max_dd_end]
    dd_long_pnl = sum(t.pnl for t in dd_trades if t.direction == "LONG")
    dd_short_pnl = sum(t.pnl for t in dd_trades if t.direction == "SHORT")
    print(f"  Trades in period: {len(dd_trades)}")
    print(f"    LONG  total pnl: {dd_long_pnl:+.0f}")
    print(f"    SHORT total pnl: {dd_short_pnl:+.0f}")
    for regime in ["BULL", "BEAR", "NEUTRAL"]:
        r_trades = [t for t in dd_trades if t.entry_regime == regime]
        if r_trades:
            pnl = sum(t.pnl for t in r_trades)
            print(f"    {regime}: {len(r_trades)} trades, pnl={pnl:+.0f}")

    # 8. JSON summary
    summary = {
        "total_trades": len(all_trades),
        "final_equity": round(state.equity, 2),
        "total_return_pct": round((state.equity / INITIAL_CAPITAL - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_dd_start_bar": int(max_dd_start),
        "max_dd_end_bar": int(max_dd_end),
        "max_dd_start_time": str(dts[max_dd_start]),
        "max_dd_end_time": str(dts[max_dd_end]),
        "by_regime_direction": {},
    }
    for regime in ["BULL", "BEAR", "NEUTRAL"]:
        for direction in ["LONG", "SHORT"]:
            subset = [t for t in all_trades if t.entry_regime == regime and t.direction == direction]
            if subset:
                key = f"{regime}_{direction}"
                summary["by_regime_direction"][key] = {
                    "count": len(subset),
                    "total_pnl": round(sum(t.pnl for t in subset), 2),
                    "avg_pnl": round(sum(t.pnl for t in subset) / len(subset), 2),
                    "win_rate": round(sum(1 for t in subset if t.pnl > 0) / len(subset), 4),
                    "avg_mae_pct": round(sum(t.mae for t in subset) / len(subset) * 100, 2),
                    "worst_mae_pct": round(min(t.mae for t in subset) * 100, 2),
                }

    with open(SUMMARY_JSON, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSummary saved: {SUMMARY_JSON}")


if __name__ == "__main__":
    main()
