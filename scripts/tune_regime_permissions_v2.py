"""Regime permission overlay combo search for ETH ChannelBreakout v2.1.

Pre-computes unique ChannelBreakout signal arrays and daily indicators once,
then for each (BULL x BEAR x NEUTRAL) combo routes signals per slow-regime label
and applies the fast risk-off permission layer.  Evaluates on OOS split with the
full StrategyEvaluator.

All permission logic is delegated to ``dex.regime_permissions`` — the single
source of truth shared with backtest and live paths.

Usage:
    uv run python scripts/tune_regime_permissions_v2.py              # full 192 combos
    uv run python scripts/tune_regime_permissions_v2.py --limit 10   # smoke run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

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

# ── regime change policy (configurable) ──────────────────────────────────────
REGIME_CHANGE_POLICY = "permission_based"
# Alternatives: "always_close" (close on every regime transition),
#               "never_close" (never force close on transition)

# ── baseline identity ────────────────────────────────────────────────────────
BASELINE = ("U0_base", "K0_base", "N1_lonly")  # current v2: BULL long-only, BEAR dual, NEUTRAL long-only


# ── candidate definitions ────────────────────────────────────────────────────


@dataclass
class RegimeCandidate:
    name: str
    strategy_params: dict
    permission: RiskOffConfig | None = None


BULL_CANDIDATES = [
    RegimeCandidate("U0_base", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False}),
    RegimeCandidate("U1_ema50", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, close_below_ema_disables_long=True, ema_fast=50)),
    RegimeCandidate("U2_slope", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, ema_slope_negative_disables_long=True, ema_fast=50, ema_slope_days=5)),
    RegimeCandidate("U3_ema100", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, close_below_ema_slow_disables_long=True, ema_slow=100)),
    RegimeCandidate("U4_cons3", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, close_below_ema_disables_long=True, ema_fast=50, consecutive_below_ema_days=3)),
    RegimeCandidate("U5_dd15", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, max_dd_from_peak_pct=15.0)),
    RegimeCandidate("U6_ema50_adx20", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, close_below_ema_disables_long=True, ema_fast=50,
                                  adx_force_flat_below=20, adx_entry_min=20)),
    RegimeCandidate("U7_cons3_adx20", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False},
                    RiskOffConfig(allow_long=True, allow_short=False, close_below_ema_disables_long=True, ema_fast=50,
                                  consecutive_below_ema_days=3, adx_force_flat_below=20, adx_entry_min=20)),
]

BEAR_CANDIDATES = [
    RegimeCandidate("K0_base", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True}),
    RegimeCandidate("K1_atr25", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True, "breakout_atr_buffer": 0.25}),
    RegimeCandidate("K2_adx18", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True, "adx_threshold": 18}),
]

NEUTRAL_CANDIDATES = [
    RegimeCandidate("N0_flat", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True},
                    RiskOffConfig(force_flat=True)),
    RegimeCandidate("N1_lonly", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": False}),
    RegimeCandidate("N2_shortif", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True},
                    RiskOffConfig(allow_long=True, allow_short=False, short_if_below_ema=True, ema_fast=50, ema_slope_days=5)),
    RegimeCandidate("N3_dir", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True},
                    RiskOffConfig(allow_long=True, allow_short=True, directional_only=True, ema_fast=50, ema_slope_days=5)),
    RegimeCandidate("N4_sdual", {"entry_lookback": 4000, "min_hold_bars": 576, "exit_lookback": 2880, "enable_long": True, "enable_short": True}),
    RegimeCandidate("N5_sdir", {"entry_lookback": 4000, "min_hold_bars": 576, "exit_lookback": 2880, "enable_long": True, "enable_short": True},
                    RiskOffConfig(allow_long=True, allow_short=True, directional_only=True, ema_fast=50, ema_slope_days=5)),
    RegimeCandidate("N6_adxdir", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True},
                    RiskOffConfig(allow_long=True, allow_short=True, directional_only=True, ema_fast=50, ema_slope_days=5,
                                  adx_force_flat_below=22, adx_entry_min=22)),
    RegimeCandidate("N7_adx_gated", {"entry_lookback": 375, "min_hold_bars": 432, "enable_long": True, "enable_short": True},
                    RiskOffConfig(allow_long=True, allow_short=True, directional_only=True, ema_fast=50, ema_slope_days=5,
                                  adx_force_flat_below=18, adx_entry_min=22)),
]


# ── helpers ──────────────────────────────────────────────────────────────────


def _key(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def precompute_signals(candidates: List[RegimeCandidate], df: pd.DataFrame) -> Dict[str, np.ndarray]:
    cache: Dict[str, np.ndarray] = {}
    seen: Dict[str, ChannelBreakoutTrendStrategy] = {}
    from dex.strategy_signals import generate_strategy_signals

    for c in candidates:
        k = _key(c.strategy_params)
        if k not in cache:
            s = seen.setdefault(k, ChannelBreakoutTrendStrategy(**c.strategy_params))
            cache[k] = generate_strategy_signals(s, df, enable_short=c.strategy_params.get("enable_short", True))
    return cache


# ── DD contribution analysis ─────────────────────────────────────────────────


def analyse_dd_contributions(
    equity_curve: np.ndarray,
    trade_log: list[dict],
    regimes: np.ndarray,
    dts: np.ndarray,
    window: int,
) -> dict:
    """Attribute max drawdown PnL by regime x direction using the evaluator's trade log.

    Uses the **authoritative** equity curve and trade log from StrategyEvaluator.simulate().
    Enriches trades with entry_regime/direction, then filters to those closing
    within the max DD window.
    """
    n = len(equity_curve)

    # 1. find max DD window from the authoritative equity curve
    peak_idx = window
    max_dd = 0.0
    max_dd_start = window
    max_dd_end = window
    peak_val = float(equity_curve[window]) if not np.isnan(equity_curve[window]) else INITIAL_CAPITAL
    for i in range(window, n):
        if np.isnan(equity_curve[i]):
            continue
        if equity_curve[i] > peak_val:
            peak_val = float(equity_curve[i])
            peak_idx = i
        dd = (peak_val - equity_curve[i]) / peak_val if peak_val > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            max_dd_start = peak_idx
            max_dd_end = i

    # 2. recovery time
    trough_val = peak_val
    trough_bar = max_dd_start
    recovered_bar = max_dd_end
    for i in range(max_dd_start, n):
        if np.isnan(equity_curve[i]):
            continue
        if equity_curve[i] < trough_val:
            trough_val = float(equity_curve[i])
            trough_bar = i
        if equity_curve[i] >= peak_val:
            recovered_bar = i
            break
    recovery_bars = max(0, recovered_bar - trough_bar) if recovered_bar > trough_bar else n - trough_bar

    # 3. enrich trade log with regime/direction at entry time
    #    trade_log from StrategyEvaluator.simulate() has:
    #      {"type": "buy"|"sell_short", "step": N}           — open
    #      {"type": "sell"|"buy_cover"|"sell_final", "step": N, "pnl": X}  — close
    enriched: list[dict] = []
    open_stack: list[dict] = []  # FIFO queue

    for t in trade_log:
        ttype = t.get("type", "")
        step = int(t.get("step", 0))
        regime = str(regimes[step]) if step < len(regimes) else "UNKNOWN"

        if ttype in ("buy",):
            open_stack.append({"entry_step": step, "entry_regime": regime, "direction": "LONG"})
        elif ttype in ("sell_short",):
            open_stack.append({"entry_step": step, "entry_regime": regime, "direction": "SHORT"})
        elif ttype in ("sell", "buy_cover", "sell_final"):
            pnl = float(t.get("pnl", 0.0))
            if open_stack:
                entry = open_stack.pop(0)  # FIFO
                enriched.append({
                    "entry_regime": entry["entry_regime"],
                    "direction": entry["direction"],
                    "pnl": pnl,
                    "entry_step": entry["entry_step"],
                    "exit_step": step,
                })
            else:
                enriched.append({
                    "entry_regime": "UNKNOWN", "direction": "UNKNOWN",
                    "pnl": pnl, "entry_step": step, "exit_step": step,
                })

    # 4. filter to trades with ANY overlap with the DD window
    #    (entry before window + close after → floating DD; close inside → realised DD)
    dd_trades = [t for t in enriched
                 if t["entry_step"] <= max_dd_end and t["exit_step"] >= max_dd_start]

    # 5. aggregate by regime x direction
    by_regime_dir: dict[str, float] = {}
    by_regime: dict[str, float] = {}
    for t in dd_trades:
        key = f"{t['entry_regime']}_{t['direction']}"
        by_regime_dir[key] = by_regime_dir.get(key, 0.0) + t["pnl"]
        by_regime[t["entry_regime"]] = by_regime.get(t["entry_regime"], 0.0) + t["pnl"]

    return {
        "max_dd_pct": round(max_dd * 100, 2),
        "dd_start_time": str(dts[max_dd_start])[:10],
        "dd_end_time": str(dts[max_dd_end])[:10],
        "recovery_bars": int(recovery_bars),
        "recovery_days_est": round(recovery_bars / 288),
        "dd_trades": len(dd_trades),
        "contribution_by_regime_dir": {k: round(v, 2) for k, v in sorted(by_regime_dir.items())},
        "contribution_by_regime": {k: round(v, 2) for k, v in sorted(by_regime.items())},
        "bull_long_contribution": round(by_regime_dir.get("BULL_LONG", 0.0), 2),
        "neutral_long_contribution": round(by_regime_dir.get("NEUTRAL_LONG", 0.0), 2),
        "bear_total_contribution": round(by_regime.get("BEAR", 0.0), 2),
    }


def classify_tier(r: "ComboResult") -> str:
    """Assign tier based on OOS max DD."""
    dd = abs(r.oos_dd)
    if dd < 30:
        return "stable"
    elif dd < 40:
        return "balanced"
    elif dd < 50:
        return "aggressive"
    else:
        return "rejected"


def tier_sort_key(r: "ComboResult"):
    """Sort within tier: prefer high return/low DD ratio, then high excess."""
    dd = max(abs(r.oos_dd), 0.01)
    return (r.oos_return / dd, r.oos_excess)


# ── output helpers ───────────────────────────────────────────────────────────


def _tier_header(tier: str, label: str, dd_limit: str) -> str:
    return f"\n{'='*110}\n  {tier.upper()} ({label}) — OOS maxDD < {dd_limit}\n{'='*110}"


def _print_candidate(i: int, r: "ComboResult", show_dd_details: bool = False):
    dd = r.dd_analysis
    print(f"#{i} {r.bull_name} / {r.bear_name} / {r.neutral_name}")
    print(f"   ret={r.oos_return:+.2f}%  dd={r.oos_dd:.2f}%  sharpe={r.oos_sharpe:.2f}  "
          f"trades={r.oos_trades}  excess={r.oos_excess:+.2f}%  ret/dd={r.oos_return/max(abs(r.oos_dd),0.01):.2f}")
    if show_dd_details:
        print(f"   maxDD window: {dd['max_dd_pct']:.1f}% ({dd['dd_start_time']} ~ {dd['dd_end_time']}) "
              f"recovery~{dd['recovery_days_est']}d")
        print(f"   BULL_LONG={dd['bull_long_contribution']:+.0f}  "
              f"NEUTRAL_LONG={dd['neutral_long_contribution']:+.0f}  "
              f"BEAR={dd['bear_total_contribution']:+.0f}")


# ── main ─────────────────────────────────────────────────────────────────────


@dataclass
class ComboResult:
    bull_name: str = ""
    bear_name: str = ""
    neutral_name: str = ""
    oos_return: float = 0.0
    oos_dd: float = 0.0
    oos_sharpe: float = 0.0
    oos_trades: int = 0
    oos_excess: float = 0.0
    score: float = 0.0
    is_regime_dist: dict = field(default_factory=dict)
    oos_regime_dist: dict = field(default_factory=dict)
    dd_analysis: dict = field(default_factory=dict)
    policy: str = ""
    tier: str = ""


def run(limit: int = 0, policy_override: str | None = None) -> None:
    """Main entry point.

    Parameters
    ----------
    limit:
        If > 0, only evaluate the first N combos (for smoke testing).
    policy_override:
        Override REGIME_CHANGE_POLICY from CLI.
    """
    policy = policy_override or REGIME_CHANGE_POLICY
    # ── output directory ──────────────────────────────────────────────────
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("search_results") / f"regime_permissions_v2_{run_ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir}")

    # 1. load data
    files = list_crypto_files()
    eth = [f for f in files if "ETHUSDT" in str(f)]
    c2600 = [f for f in eth if "2600d" in str(f)]
    path = c2600[0] if c2600 else eth[0]
    df = load_crypto_data(path).sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    print(f"Data: {len(df)} bars, {df.iloc[0]['datetime']} ~ {df.iloc[-1]['datetime']}")

    split = int(len(df) * 0.7)
    df_is = df.iloc[:split]
    df_oos = df.iloc[split:].reset_index(drop=True)
    print(f"IS: {len(df_is)} bars | OOS: {len(df_oos)} bars")

    # 2. precompute
    t0 = time.time()
    print("Precomputing signals + daily indicators + regimes + ADX...")
    all_c = BULL_CANDIDATES + BEAR_CANDIDATES + NEUTRAL_CANDIDATES
    sigs = precompute_signals(all_c, df)
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)
    print(f"  {len(sigs)} unique signals + daily indicators + regimes + ADX ({time.time()-t0:.1f}s)")

    # 3. regime distribution
    is_regimes = regimes[:split]
    oos_regimes = regimes[split:]
    is_dist = {
        "BULL": int((is_regimes == "BULL").sum()),
        "BEAR": int((is_regimes == "BEAR").sum()),
        "NEUTRAL": int((is_regimes == "NEUTRAL").sum()),
    }
    oos_dist = {
        "BULL": int((oos_regimes == "BULL").sum()),
        "BEAR": int((oos_regimes == "BEAR").sum()),
        "NEUTRAL": int((oos_regimes == "NEUTRAL").sum()),
    }
    is_total = sum(is_dist.values())
    oos_total = sum(oos_dist.values())
    print(f"IS regime:  BULL={is_dist['BULL']/is_total*100:.0f}%  BEAR={is_dist['BEAR']/is_total*100:.0f}%  NEUTRAL={is_dist['NEUTRAL']/is_total*100:.0f}%")
    print(f"OOS regime: BULL={oos_dist['BULL']/oos_total*100:.0f}%  BEAR={oos_dist['BEAR']/oos_total*100:.0f}%  NEUTRAL={oos_dist['NEUTRAL']/oos_total*100:.0f}%")

    # 4. evaluator
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    oos_prices = df_oos["close"].values.astype(float)
    oos_bh = (oos_prices[-1] / oos_prices[0] - 1.0) * 100

    # 5. enumerate combos
    n_combos = len(BULL_CANDIDATES) * len(BEAR_CANDIDATES) * len(NEUTRAL_CANDIDATES)
    effective = min(n_combos, limit) if limit > 0 else n_combos
    print(f"\nEvaluating {effective}/{n_combos} combos (policy={policy})...")

    results: list[ComboResult] = []
    baseline_result: ComboResult | None = None
    t0 = time.time()
    combo_count = 0

    default_bull = RiskOffConfig(allow_long=True, allow_short=False)
    default_bear = RiskOffConfig(allow_long=True, allow_short=True)
    default_neutral_long = RiskOffConfig(allow_long=True, allow_short=False)

    for bull_c in BULL_CANDIDATES:
        bull_raw = sigs[_key(bull_c.strategy_params)]
        b_cfg = bull_c.permission or default_bull
        for bear_c in BEAR_CANDIDATES:
            bear_raw = sigs[_key(bear_c.strategy_params)]
            be_cfg = bear_c.permission or default_bear
            for neutral_c in NEUTRAL_CANDIDATES:
                combo_count += 1
                if limit > 0 and combo_count > limit:
                    break

                neutral_raw = sigs[_key(neutral_c.strategy_params)]
                n_cfg = neutral_c.permission or (
                    default_neutral_long if not neutral_c.strategy_params.get("enable_short")
                    else RiskOffConfig(allow_long=True, allow_short=True)
                )

                # route signals per regime
                routed = route_regime_signals(
                    bull_raw, bear_raw, neutral_raw, regimes,
                    regime_change_policy=policy,
                )

                # build permission arrays
                al, as_arr, ff, eo = build_permission_arrays(
                    df, regimes, b_cfg, be_cfg, n_cfg, daily_ctx, adx_full,
                )

                # apply permissions
                final_signals = apply_permission_arrays(routed, al, as_arr, ff, eo)

                # signal stats for smoke diagnostics
                is_baseline = (bull_c.name, bear_c.name, neutral_c.name) == BASELINE

                if is_baseline:
                    oos_full = final_signals
                    print(f"\n  [baseline {BASELINE}]")
                    print(f"    full signals: 0={int((final_signals==0).sum())} 1={int((final_signals==1).sum())} "
                          f"2={int((final_signals==2).sum())} 3={int((final_signals==3).sum())}")
                    print(f"    force_flat: {int(ff.sum())}  exit_only: {int(eo.sum())}")

                # OOS evaluation
                oos_sig = final_signals[split:]
                score, metrics, trade_log = ev.evaluate(oos_sig, oos_prices, df_oos)
                ret = metrics.get("total_return", 0) * 100
                dd = metrics.get("max_drawdown", 0) * 100
                sharpe = metrics.get("sharpe_ratio", 0)
                trades = len(trade_log) if trade_log else 0

                if is_baseline:
                    print(f"    OOS: ret={ret:+.2f}% dd={dd:.2f}% sharpe={sharpe:.2f} trades={trades} excess={ret-oos_bh:+.2f}%")

                if trades < 10:
                    continue

                # DD contribution analysis
                oos_close = df_oos["close"].values.astype(float)
                oos_dts_arr = df_oos["datetime"].values
                # DD contribution: use evaluator's authoritative equity curve + trade log
                oos_equity, oos_full_trades = ev.simulate(oos_sig, oos_prices)
                dd_info = analyse_dd_contributions(
                    oos_equity, oos_full_trades, oos_regimes, oos_dts_arr, window=375,
                )

                cr = ComboResult(
                    bull_name=bull_c.name, bear_name=bear_c.name, neutral_name=neutral_c.name,
                    oos_return=round(ret, 2), oos_dd=round(dd, 2), oos_sharpe=round(sharpe, 4),
                    oos_trades=trades, oos_excess=round(ret - oos_bh, 2), score=round(score, 4),
                    is_regime_dist=is_dist, oos_regime_dist=oos_dist,
                    dd_analysis=dd_info, policy=policy,
                    tier=classify_tier(ComboResult.__new__(ComboResult)),  # placeholder, set below
                )
                cr.tier = classify_tier(cr)

                if is_baseline:
                    baseline_result = cr

                results.append(cr)

            if limit > 0 and combo_count >= limit:
                break
        if limit > 0 and combo_count >= limit:
            break

    elapsed = time.time() - t0
    n_passed = len(results)
    n_dropped = combo_count - n_passed
    print(f"\n  {combo_count} combos in {elapsed:.1f}s | {n_passed} pass (trades≥10) | {n_dropped} dropped")

    if baseline_result:
        bl = baseline_result
        print(f"\n  BASELINE ({BASELINE}): ret={bl.oos_return:+.2f}% dd={bl.oos_dd:.2f}% "
              f"sharpe={bl.oos_sharpe:.2f} trades={bl.oos_trades} tier={bl.tier}")

    if not results:
        print("\n[WARN] No combos passed the filter. Check signal routing.")
        return

    # 6. save all results CSV
    csv_rows = []
    for r in results:
        dd = r.dd_analysis
        csv_rows.append({
            "bull": r.bull_name, "bear": r.bear_name, "neutral": r.neutral_name,
            "oos_return": r.oos_return, "oos_dd": r.oos_dd, "oos_sharpe": r.oos_sharpe,
            "oos_trades": r.oos_trades, "oos_excess": r.oos_excess, "score": r.score,
            "tier": r.tier,
            "dd_window_pct": dd["max_dd_pct"],
            "dd_recovery_days": dd["recovery_days_est"],
            "bull_long_contrib": dd["bull_long_contribution"],
            "neutral_long_contrib": dd["neutral_long_contribution"],
            "bear_total_contrib": dd["bear_total_contribution"],
        })
    pd.DataFrame(csv_rows).to_csv(out_dir / "all_results.csv", index=False)

    # 7. tier classification and output
    tiers = {"stable": [], "balanced": [], "aggressive": [], "rejected": []}
    for r in results:
        tiers[r.tier].append(r)

    for tier_name in ["stable", "balanced", "aggressive"]:
        tiers[tier_name].sort(key=tier_sort_key, reverse=True)

    # config
    config = {
        "run_ts": run_ts,
        "policy": policy,
        "baseline": list(BASELINE),
        "is_regime_dist": is_dist,
        "oos_regime_dist": oos_dist,
        "oos_bh_return": round(oos_bh, 2),
        "total_combos": n_combos,
        "evaluated": combo_count,
        "passed_filter": n_passed,
        "bull_candidates": [c.name for c in BULL_CANDIDATES],
        "bear_candidates": [c.name for c in BEAR_CANDIDATES],
        "neutral_candidates": [c.name for c in NEUTRAL_CANDIDATES],
    }
    with open(out_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # regime distribution
    with open(out_dir / "regime_distribution.json", "w") as f:
        json.dump({"IS": is_dist, "OOS": oos_dist}, f, indent=2)

    # 8. print tier reports
    tier_configs = [
        ("stable", "可小资金 demo 长期观察", 30, 25, True),
        ("balanced", "可 signal-only 或极小仓 demo", 40, 25, True),
        ("aggressive", "signal-only 研究候选，暂不实盘", 50, 25, True),
    ]

    top_picks: dict = {}

    for tier_name, label, dd_limit, min_trades, show_dd in tier_configs:
        candidates = [r for r in tiers[tier_name] if r.oos_return > 0 and r.oos_excess > 0 and r.oos_trades >= min_trades]
        print(_tier_header(tier_name, label, str(dd_limit)))
        print(f"  {len(candidates)} candidates meet: DD<{dd_limit}%, return>0, excess>buy_hold, trades≥{min_trades}")

        if not candidates:
            print("  (none)")
            continue

        if baseline_result and baseline_result.tier == tier_name:
            print(f"  BASELINE: ret={baseline_result.oos_return:+.2f}% dd={baseline_result.oos_dd:.2f}% "
                  f"tier={baseline_result.tier}")

        top_n = min(10, len(candidates))
        print(f"\n  Top {top_n} (by return/|DD|):")
        print(f"  {'#':<3} {'BULL':<16} {'BEAR':<14} {'NEUTRAL':<16} {'Ret':>8} {'DD':>8} {'Shp':>6} {'Trd':>5} {'Exc':>8} {'R/DD':>6}")
        print(f"  {'-'*100}")
        for i, r in enumerate(candidates[:top_n]):
            rdd = r.oos_return / max(abs(r.oos_dd), 0.01)
            print(f"  {i+1:<3} {r.bull_name:<16} {r.bear_name:<14} {r.neutral_name:<16} "
                  f"{r.oos_return:>+7.2f}% {r.oos_dd:>7.2f}% {r.oos_sharpe:>5.2f} {r.oos_trades:>5} "
                  f"{r.oos_excess:>+7.2f}% {rdd:>5.2f}")

        # DD contribution detail for top 5
        print(f"\n  Top 5 — DD contribution vs baseline:")
        print(f"  {'#':<3} {'combo':<50} {'ret':>8} {'DD':>8} {'BULL_L':>8} {'NEU_L':>8} {'BEAR':>8}")
        print(f"  {'-'*100}")
        if baseline_result:
            bdd = baseline_result.dd_analysis
            print(f"  {'BL':<3} {'BASELINE':<50} {baseline_result.oos_return:>+7.2f}% {baseline_result.oos_dd:>7.2f}% "
                  f"{bdd['bull_long_contribution']:>+8.0f} {bdd['neutral_long_contribution']:>+8.0f} {bdd['bear_total_contribution']:>+8.0f}")
        for i, r in enumerate(candidates[:5]):
            dd = r.dd_analysis
            label = f"{r.bull_name}/{r.bear_name}/{r.neutral_name}"
            print(f"  {i+1:<3} {label:<50} {r.oos_return:>+7.2f}% {r.oos_dd:>7.2f}% "
                  f"{dd['bull_long_contribution']:>+8.0f} {dd['neutral_long_contribution']:>+8.0f} {dd['bear_total_contribution']:>+8.0f}")

        # save tier JSON
        tier_top = []
        for i, r in enumerate(candidates[:top_n]):
            dd = r.dd_analysis
            entry = {
                "rank": i + 1,
                "bull": r.bull_name, "bear": r.bear_name, "neutral": r.neutral_name,
                "oos_return": r.oos_return, "oos_dd": r.oos_dd, "oos_sharpe": r.oos_sharpe,
                "oos_trades": r.oos_trades, "oos_excess": r.oos_excess,
                "ret_div_dd": round(r.oos_return / max(abs(r.oos_dd), 0.01), 2),
                "dd_analysis": dd,
            }
            tier_top.append(entry)
        top_picks[tier_name] = tier_top

        with open(out_dir / f"top_{tier_name}.json", "w") as f:
            json.dump({"tier": tier_name, "label": label, "dd_limit": dd_limit,
                        "candidates": tier_top}, f, indent=2, ensure_ascii=False)

    # 9. DD contribution comparison report
    if baseline_result:
        with open(out_dir / "dd_contribution_report.json", "w") as f:
            report = {"baseline": {
                "combo": list(BASELINE),
                "oos_return": baseline_result.oos_return,
                "oos_dd": baseline_result.oos_dd,
                "dd_analysis": baseline_result.dd_analysis,
            }}
            for tier_name in ["stable", "balanced", "aggressive"]:
                if tier_name in top_picks and top_picks[tier_name]:
                    best = top_picks[tier_name][0]
                    bdd = baseline_result.dd_analysis
                    cdd = best["dd_analysis"]
                    report[f"{tier_name}_best"] = {
                        "combo": [best["bull"], best["bear"], best["neutral"]],
                        "oos_return": best["oos_return"],
                        "oos_dd": best["oos_dd"],
                        "dd_analysis": best["dd_analysis"],
                        "delta_vs_baseline": {
                            "return": round(best["oos_return"] - baseline_result.oos_return, 2),
                            "dd": round(best["oos_dd"] - baseline_result.oos_dd, 2),
                            "bull_long": round(cdd["bull_long_contribution"] - bdd["bull_long_contribution"], 2),
                            "neutral_long": round(cdd["neutral_long_contribution"] - bdd["neutral_long_contribution"], 2),
                            "bear_total": round(cdd["bear_total_contribution"] - bdd["bear_total_contribution"], 2),
                        },
                    }
            json.dump(report, f, indent=2, ensure_ascii=False)

    # 10. summary
    print(f"\n{'='*110}")
    print(f"  SUMMARY")
    print(f"{'='*110}")
    print(f"  OOS B&H: {oos_bh:+.2f}%")
    if baseline_result:
        print(f"  Baseline ({BASELINE}): ret={baseline_result.oos_return:+.2f}% dd={baseline_result.oos_dd:.2f}% "
              f"tier={baseline_result.tier}")
    for tier_name in ["stable", "balanced", "aggressive"]:
        n = len(tiers[tier_name])
        top_n = min(3, n)
        top_list = [f"{r.bull_name}/{r.bear_name}/{r.neutral_name} ({r.oos_return:+.1f}%/{r.oos_dd:.1f}%)"
                    for r in tiers[tier_name][:top_n]]
        print(f"  {tier_name} ({n}): {', '.join(top_list) if top_list else '(none)'}")
    print(f"\n  All outputs → {out_dir}")
    print(f"    all_results.csv        — every combo")
    print(f"    top_stable.json        — DD<30%  best picks")
    print(f"    top_balanced.json      — DD<40%  best picks")
    print(f"    top_aggressive.json    — DD<50%  best picks")
    print(f"    dd_contribution_report.json — baseline vs best delta")
    print(f"    config.json            — run parameters")
    print(f"    regime_distribution.json — IS/OOS regime split")


# ── equity curve builder ─────────────────────────────────────────────────────


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Regime permission overlay combo search v2.1")
    p.add_argument("--limit", type=int, default=0,
                   help="Limit to first N combos (smoke test). 0 = full run.")
    p.add_argument("--policy", type=str, default=REGIME_CHANGE_POLICY,
                   choices=["always_close", "permission_based", "never_close"],
                   help="Regime change policy (default: permission_based)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(limit=args.limit, policy_override=args.policy)
