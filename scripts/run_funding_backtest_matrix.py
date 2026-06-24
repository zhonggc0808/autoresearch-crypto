#!/usr/bin/env python3
"""Run funding-aware full/OOS/Monte-Carlo backtests for live v2 profiles.

Default run:
    uv run python scripts/run_funding_backtest_matrix.py
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backtest_quant import _find_funding_file as find_funding_file
from backtest_quant import add_funding_events, normalize_signals_for_position_mode
from dex.checkpoints import (
    build_channel_breakout_strategy_from_checkpoint,
    build_strategy_from_checkpoint,
    is_regime_channel_breakout_checkpoint,
    load_checkpoint,
)
from dex.config import BARS_PER_YEAR, COMMISSION, DATA_DIR, INITIAL_CAPITAL
from dex.data import load_crypto_data
from dex.exit_overlays import apply_exit_overlays
from dex.indicators import compute_adx
from dex.live.profiles import LIVE_STRATEGY_PROFILES, get_live_strategy_profile
from dex.regime_filter import apply_regime_short_filter, build_daily_regime_labels
from dex.regime_permissions import (
    RiskOffConfig,
    apply_permission_arrays,
    build_permission_arrays,
    compute_daily_indicators,
    route_regime_signals,
)
from dex.scoring import risk_adjusted_score
from dex.strategies.base import StrategyEvaluator
from dex.strategies.trade_ledger import build_logical_trade_ledger
from dex.strategy_signals import generate_strategy_signals

DEFAULT_PROFILES = [
    "channel_breakout_v2",
    "channel_breakout_v2_regime_filter_50_200",
    "channel_breakout_v2_1_balanced",
    "channel_breakout_v2_2_mtg_bcd",
    "channel_breakout_v2_3_combo_balanced",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Funding-aware v2 profile backtest matrix")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--days", default="1300,2600", help="Comma-separated day tags")
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--oos-ratio", type=float, default=0.30)
    parser.add_argument("--mc-runs", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--commission-bps", type=float, default=COMMISSION * 10000)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--no-funding", action="store_true")
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--output-dir", default="research_workspace/funding_backtests")
    return parser.parse_args()


def find_data_file(symbol: str, interval: str, days: int) -> Path:
    exact = DATA_DIR / f"{symbol.upper()}_{interval}_{days}d.parquet"
    if exact.exists():
        return exact
    candidates = sorted(DATA_DIR.glob(f"{symbol.upper()}_{interval}_*{days}d*.parquet"))
    if candidates:
        return candidates[-1]
    raise FileNotFoundError(f"No data file found for {symbol} {interval} {days}d in {DATA_DIR}")


def load_market_data(symbol: str, interval: str, days: int, with_funding: bool) -> dict[str, Any]:
    data_path = find_data_file(symbol, interval, days)
    df = (
        load_crypto_data(str(data_path))
        .sort_values("timestamp")
        .drop_duplicates(subset="timestamp")
        .reset_index(drop=True)
    )
    funding_path = None
    funding_events = 0
    if with_funding:
        funding_path = find_funding_file(symbol, days)
        if funding_path is not None:
            df, funding_events = add_funding_events(df, funding_path)
    return {
        "days": days,
        "data_path": data_path,
        "funding_path": funding_path,
        "funding_events": funding_events,
        "df": df,
    }


def generate_profile_signals(
    profile_name: str,
    df: pd.DataFrame,
    *,
    long_only: bool = False,
) -> tuple[np.ndarray, int, dict[str, Any]]:
    profile = get_live_strategy_profile(profile_name)
    checkpoint = load_checkpoint(PROJECT_DIR / profile.checkpoint)
    if is_regime_channel_breakout_checkpoint(checkpoint):
        signals = generate_regime_channel_breakout_signals(checkpoint, df, long_only=long_only)
        warmup = infer_regime_warmup(checkpoint)
    else:
        strategy = build_strategy_from_checkpoint(checkpoint)
        params = checkpoint.get("params") or {}
        enable_short = bool(params.get("enable_short", getattr(strategy, "enable_short", True)))
        enable_short = enable_short and not long_only
        if hasattr(strategy, "enable_short"):
            strategy.enable_short = enable_short
        signals = generate_strategy_signals(strategy, df, enable_short=enable_short)
        signals = apply_checkpoint_signal_filter(checkpoint, signals, df)
        signals = normalize_signals_for_position_mode(signals, long_only=not enable_short)
        warmup = infer_strategy_warmup(strategy, checkpoint)

    risk_config = None
    if profile.risk_profile != "none":
        risk_config = copy.deepcopy(profile.risk_config or {})
    meta = {
        "checkpoint": profile.checkpoint,
        "strategy_version": profile.strategy_version,
        "risk_profile": profile.risk_profile,
        "risk_config": risk_config,
    }
    return signals, warmup, meta


def generate_regime_channel_breakout_signals(
    checkpoint: dict[str, Any],
    df: pd.DataFrame,
    *,
    long_only: bool = False,
) -> np.ndarray:
    bull_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bull")
    bear_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bear")
    neutral_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "neutral")

    bull_raw = generate_strategy_signals(
        bull_s, df, enable_short=getattr(bull_s, "enable_short", True) and not long_only
    )
    bear_raw = generate_strategy_signals(
        bear_s, df, enable_short=getattr(bear_s, "enable_short", True) and not long_only
    )
    neutral_raw = generate_strategy_signals(
        neutral_s, df, enable_short=getattr(neutral_s, "enable_short", True) and not long_only
    )

    regime_filter = checkpoint.get("regime_filter") or {}
    fast_days = int(regime_filter.get("fast_days", 50))
    slow_days = int(regime_filter.get("slow_days", 200))
    regimes = build_daily_regime_labels(df, fast_days=fast_days, slow_days=slow_days)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)
    policy = checkpoint.get("regime_change_policy", "permission_based")

    routed = route_regime_signals(
        bull_raw, bear_raw, neutral_raw, regimes, regime_change_policy=policy
    )
    allow_long, allow_short, force_flat, exit_only = build_permission_arrays(
        df,
        regimes,
        RiskOffConfig(**checkpoint["bull"]["permission"]),
        RiskOffConfig(**checkpoint["bear"]["permission"]),
        RiskOffConfig(**checkpoint["neutral"]["permission"]),
        daily_ctx,
        adx_full,
    )
    signals = apply_permission_arrays(routed, allow_long, allow_short, force_flat, exit_only)
    signals = apply_exit_overlays(signals, df, regimes, checkpoint.get("exit_logic"))
    return normalize_signals_for_position_mode(signals, long_only=long_only)


def apply_checkpoint_signal_filter(
    checkpoint: dict[str, Any],
    signals: np.ndarray,
    df: pd.DataFrame,
) -> np.ndarray:
    signal_filter = checkpoint.get("signal_filter")
    if not isinstance(signal_filter, dict):
        return signals
    if signal_filter.get("type") != "regime_short_filter":
        return signals
    regimes = build_daily_regime_labels(
        df,
        fast_days=int(signal_filter.get("fast_days", 50)),
        slow_days=int(signal_filter.get("slow_days", 200)),
    )
    filtered, _ = apply_regime_short_filter(signals, regimes, df)
    return filtered


def infer_strategy_warmup(strategy: Any, checkpoint: dict[str, Any]) -> int:
    params = checkpoint.get("params") or {}
    values = [
        getattr(strategy, "window", 0),
        getattr(strategy, "entry_lookback", 0),
        getattr(strategy, "long_ma_period", 0),
        getattr(strategy, "slow_ma_period", 0),
        getattr(strategy, "trend_ma_period", 0),
        params.get("entry_lookback", 0),
        params.get("window", 0),
    ]
    return max(0, int(max(float(v or 0) for v in values)))


def infer_regime_warmup(checkpoint: dict[str, Any]) -> int:
    values = []
    for regime in ("bull", "bear", "neutral"):
        params = checkpoint.get(regime, {}).get("strategy_params", {})
        values.extend([params.get("entry_lookback", 0), params.get("window", 0)])
    return max(0, int(max(float(v or 0) for v in values)))


def evaluate_period(
    signals: np.ndarray,
    df: pd.DataFrame,
    *,
    period: str,
    profile_name: str,
    days: int,
    risk_config: dict[str, Any] | None,
    mc_runs: int,
    seed: int,
    commission: float,
    slippage: float,
) -> dict[str, Any]:
    prices = df["close"].to_numpy(dtype=float)
    evaluator = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=commission,
        slippage=slippage,
    )
    if risk_config:
        equity, trades = evaluator.simulate(signals, prices, df=df, stop_config=risk_config)
        metrics = evaluator.compute_metrics(equity, trades)
    else:
        equity, trades = evaluator.simulate(signals, prices, df=df)
        metrics = evaluator.compute_metrics(equity, trades)

    closed_pnls = closed_trade_pnls(trades)
    total_return = float(equity[-1] / equity[0] - 1.0) if len(equity) > 0 else 0.0
    market_return = float(prices[-1] / prices[0] - 1.0) if len(prices) > 1 else 0.0
    scored = risk_adjusted_score(
        float(metrics["sharpe_ratio"]),
        total_return,
        float(metrics["max_drawdown"]),
        float(metrics["win_rate"]),
        len(closed_pnls),
        market_return=market_return,
    )
    mc = monte_carlo(closed_pnls, INITIAL_CAPITAL, runs=mc_runs, seed=seed)
    funding_pnl = sum(float(t.get("funding_pnl", 0.0)) for t in trades)
    years = len(signals) / BARS_PER_YEAR if len(signals) else 0.0

    return {
        "days": days,
        "profile": profile_name,
        "period": period,
        "start": datetime_at(df, 0),
        "end": datetime_at(df, len(df) - 1),
        "bars": int(len(signals)),
        "years": years,
        "total_return": total_return,
        "max_drawdown": float(metrics["max_drawdown"]),
        "sharpe": float(metrics["sharpe_ratio"]),
        "win_rate": float(metrics["win_rate"]),
        "trades": int(len(closed_pnls)),
        "trades_per_year": float(len(closed_pnls) / years) if years > 0 else 0.0,
        "funding_pnl": float(funding_pnl),
        "market_return": market_return,
        "excess_return": total_return - market_return,
        "risk_score": float(scored.score),
        **mc,
    }


def closed_trade_pnls(trades: list[dict[str, Any]]) -> list[float]:
    logical = build_logical_trade_ledger(trades)
    if logical:
        return [float(row["total_pnl"]) for row in logical]
    return [float(t["pnl"]) for t in trades if t.get("pnl") is not None]


def monte_carlo(
    pnls: list[float],
    initial_capital: float,
    *,
    runs: int,
    seed: int,
) -> dict[str, float | None]:
    keys = {
        "mc_return_p05": None,
        "mc_return_median": None,
        "mc_return_p95": None,
        "mc_max_dd_p05": None,
        "mc_max_dd_median": None,
        "mc_loss_prob": None,
    }
    if runs <= 0 or not pnls:
        return keys

    rng = np.random.default_rng(seed)
    samples = rng.choice(np.asarray(pnls, dtype=float), size=(runs, len(pnls)), replace=True)
    paths = np.empty((runs, len(pnls) + 1), dtype=float)
    paths[:, 0] = initial_capital
    alive = np.ones(runs, dtype=bool)
    for idx in range(samples.shape[1]):
        next_equity = paths[:, idx] + samples[:, idx]
        next_equity = np.where(alive, next_equity, 0.0)
        busted = next_equity <= 0.0
        next_equity = np.where(busted, 0.0, next_equity)
        paths[:, idx + 1] = next_equity
        alive &= ~busted
    peaks = np.maximum.accumulate(paths, axis=1)
    drawdowns = (paths - peaks) / peaks
    returns = paths[:, -1] / initial_capital - 1.0
    max_dd = drawdowns.min(axis=1)
    return {
        "mc_return_p05": float(np.quantile(returns, 0.05)),
        "mc_return_median": float(np.quantile(returns, 0.50)),
        "mc_return_p95": float(np.quantile(returns, 0.95)),
        "mc_max_dd_p05": float(np.quantile(max_dd, 0.05)),
        "mc_max_dd_median": float(np.quantile(max_dd, 0.50)),
        "mc_loss_prob": float(np.mean(returns < 0.0)),
    }


def datetime_at(df: pd.DataFrame, index: int) -> str:
    if len(df) == 0:
        return ""
    col = "datetime" if "datetime" in df.columns else "timestamp"
    return str(pd.Timestamp(df[col].iloc[index]))


def format_pct(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.2f}%"


def write_report(
    rows: list[dict[str, Any]],
    run_meta: dict[str, Any],
    output_dir: Path,
    stamp: str,
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"funding_backtest_matrix_{stamp}.csv"
    json_path = output_dir / f"funding_backtest_matrix_{stamp}.json"
    md_path = output_dir / f"funding_backtest_matrix_{stamp}.md"

    frame = pd.DataFrame(rows)
    frame.to_csv(csv_path, index=False, encoding="utf-8")
    json_path.write_text(
        json.dumps({"meta": run_meta, "rows": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(frame, run_meta), encoding="utf-8")
    return csv_path, json_path, md_path


def render_markdown(frame: pd.DataFrame, run_meta: dict[str, Any]) -> str:
    lines = [
        "# Funding Backtest Matrix",
        "",
        f"- generated_at: {run_meta['generated_at']}",
        f"- symbol: {run_meta['symbol']}",
        f"- interval: {run_meta['interval']}",
        f"- oos_ratio: {run_meta['oos_ratio']}",
        f"- commission_bps: {run_meta['commission_bps']}",
        f"- slippage_bps: {run_meta['slippage_bps']}",
        f"- funding: {run_meta['with_funding']}",
        f"- long_only: {run_meta['long_only']}",
        f"- mc_runs: {run_meta['mc_runs']}",
        "",
        "Monte Carlo uses bootstrap resampling of closed-trade PnL, not signal replay.",
        "",
    ]
    for data_item in run_meta["datasets"]:
        lines.extend(
            [
                f"## {data_item['days']}d Data",
                "",
                f"- data: `{data_item['data_path']}`",
                f"- funding: `{data_item['funding_path'] or ''}`",
                f"- funding_events: {data_item['funding_events']}",
                "",
                "| profile | period | return | max_dd | sharpe | trades | funding_pnl | MC p05/med/p95 | MC loss |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        sub = frame[frame["days"] == data_item["days"]]
        for _, row in sub.iterrows():
            mc = "/".join(
                [
                    format_pct(row["mc_return_p05"]),
                    format_pct(row["mc_return_median"]),
                    format_pct(row["mc_return_p95"]),
                ]
            )
            lines.append(
                "| {profile} | {period} | {ret} | {dd} | {sharpe:.3f} | {trades} | "
                "{funding:.2f} | {mc} | {loss} |".format(
                    profile=row["profile"],
                    period=row["period"],
                    ret=format_pct(row["total_return"]),
                    dd=format_pct(row["max_drawdown"]),
                    sharpe=row["sharpe"],
                    trades=int(row["trades"]),
                    funding=row["funding_pnl"],
                    mc=mc,
                    loss=format_pct(row["mc_loss_prob"]),
                )
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    days_values = [int(item.strip()) for item in args.days.split(",") if item.strip()]
    profiles = [item.strip() for item in args.profiles.split(",") if item.strip()]
    commission = args.commission_bps / 10000.0
    slippage = args.slippage_bps / 10000.0
    unknown = [name for name in profiles if name not in LIVE_STRATEGY_PROFILES]
    if unknown:
        raise ValueError(f"Unknown profiles: {', '.join(unknown)}")

    rows: list[dict[str, Any]] = []
    datasets_meta: list[dict[str, Any]] = []

    for days in days_values:
        data = load_market_data(args.symbol, args.interval, days, with_funding=not args.no_funding)
        df = data["df"]
        split_idx = int(len(df) * (1.0 - args.oos_ratio))
        datasets_meta.append(
            {
                "days": days,
                "data_path": str(data["data_path"]),
                "funding_path": str(data["funding_path"]) if data["funding_path"] else None,
                "funding_events": data["funding_events"],
                "start": datetime_at(df, 0),
                "end": datetime_at(df, len(df) - 1),
            }
        )
        print(
            f"{days}d: {len(df)} bars, funding_events={data['funding_events']}, "
            f"oos_start={datetime_at(df, split_idx)}"
        )

        for profile_name in profiles:
            print(f"  running {profile_name} ...", flush=True)
            signals, warmup, meta = generate_profile_signals(
                profile_name, df, long_only=args.long_only
            )
            periods = {
                "full": (max(0, warmup), len(df)),
                "oos": (split_idx, len(df)),
            }
            for period, (start, end) in periods.items():
                period_df = df.iloc[start:end].reset_index(drop=True)
                period_signals = signals[start:end]
                row = evaluate_period(
                    period_signals,
                    period_df,
                    period=period,
                    profile_name=profile_name,
                    days=days,
                    risk_config=meta["risk_config"],
                    mc_runs=args.mc_runs,
                    seed=args.seed + days + len(rows),
                    commission=commission,
                    slippage=slippage,
                )
                row.update(
                    {
                        "checkpoint": meta["checkpoint"],
                        "strategy_version": meta["strategy_version"],
                        "risk_profile": meta["risk_profile"],
                        "warmup_bars": warmup,
                    }
                )
                rows.append(row)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": args.symbol.upper(),
        "interval": args.interval,
        "profiles": profiles,
        "oos_ratio": args.oos_ratio,
        "commission_bps": args.commission_bps,
        "slippage_bps": args.slippage_bps,
        "mc_runs": args.mc_runs,
        "seed": args.seed,
        "with_funding": not args.no_funding,
        "long_only": args.long_only,
        "datasets": datasets_meta,
    }
    csv_path, json_path, md_path = write_report(
        rows, run_meta, PROJECT_DIR / args.output_dir, stamp
    )
    print(f"\nWrote:\n  {csv_path}\n  {json_path}\n  {md_path}")


if __name__ == "__main__":
    main()
