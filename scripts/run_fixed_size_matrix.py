"""Phase 1 fixed position size matrix for channel_breakout_375_432."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy

CHECKPOINT = "checkpoints/channel_breakout_375_432.pt"
DATA_FILE = Path("data/crypto/ETHUSDT_5m_2600d.parquet")
OOS_START = "2024-06-06 14:25:00"
OOS_END = "2026-06-12 02:55:00"
OUTPUT_DIR = Path("research_workspace/diagnostics")
OUTPUT_PREFIX = "channel_breakout_375_432_v2_oos_2600d"
SIZES = [1.0, 0.50, 0.475, 0.45, 0.425, 0.40]
BLOCK_BARS = 288


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mc-sims", type=int, default=2000)
    parser.add_argument("--mc-seed", type=int, default=2202)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_oos = _load_oos_data()
    strategy = ChannelBreakoutTrendStrategy(entry_lookback=375, min_hold_bars=432)
    signals = strategy.generate_signals(df_oos)
    prices = df_oos["close"].to_numpy(dtype=float)

    results = [_run_size(signals, prices, df_oos, size, args.mc_sims, args.mc_seed) for size in SIZES]
    csv_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_fixed_size_matrix.csv"
    pd.DataFrame(results).to_csv(csv_path, index=False)

    report_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_fixed_size_baseline.md"
    report_path.write_text(_baseline_report(results, args.mc_sims), encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")


def _load_oos_data() -> pd.DataFrame:
    if not DATA_FILE.exists():
        raise FileNotFoundError(DATA_FILE)
    df = pd.read_parquet(DATA_FILE)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "datetime" in df.columns:
            df.index = pd.to_datetime(df["datetime"])
        elif "timestamp" in df.columns:
            df.index = pd.to_datetime(df["timestamp"], unit="ms")
        else:
            df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df_oos = df[(df.index >= OOS_START) & (df.index <= OOS_END)].copy()
    if df_oos.empty:
        raise RuntimeError(f"OOS slice is empty: {OOS_START} to {OOS_END}")
    return df_oos


def _run_size(
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
    size: float,
    mc_sims: int,
    mc_seed: int,
) -> dict:
    evaluator = StrategyEvaluator()
    position_sizes = np.full(len(signals), size, dtype=float)
    equity, trades = evaluator.simulate(signals, prices, df, position_sizes=position_sizes)
    metrics = evaluator.compute_metrics(equity, trades)
    pnls = [float(t["pnl"]) for t in trades if t.get("pnl") is not None]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl < 0]
    row = {
        "target_size": size,
        "total_return": metrics["total_return"],
        "annualized_return": metrics["annualized_return"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "trade_count": len(pnls),
        "avg_win": float(np.mean(winners)) if winners else 0.0,
        "avg_loss": float(np.mean(losers)) if losers else 0.0,
        "profit_factor": _profit_factor(winners, losers),
        "max_single_loss": min(pnls) if pnls else 0.0,
        "top20_loss_contribution": _top_loss_contribution(losers, 20),
        "top50_loss_contribution": _top_loss_contribution(losers, 50),
        "max_underwater_days": _max_underwater_bars(equity) * 5 / 1440,
        "git_commit": _git_commit(),
        "checkpoint": CHECKPOINT,
        "data_file": str(DATA_FILE),
        "oos_start": OOS_START,
        "oos_end": OOS_END,
        "commission": COMMISSION,
        "slippage": SLIPPAGE,
    }
    row.update(_monte_carlo(equity, mc_sims, mc_seed))
    return row


def _baseline_report(results: list[dict], mc_sims: int) -> str:
    selection = _select_baselines(results)
    lines = [
        "# Fixed Size Matrix - Baseline Selection",
        "",
        f"Generated: {datetime.now().isoformat()}",
        f"Checkpoint: {CHECKPOINT}",
        f"Data: {DATA_FILE}",
        f"OOS: {OOS_START} to {OOS_END}",
        f"Git commit: {_git_commit()}",
        f"Fee/slippage: commission={COMMISSION}, slippage={SLIPPAGE}",
        f"MC simulations: {mc_sims}",
        "",
        "| Size | Return | MaxDD | Sharpe | Trades | MC DD<-30% | MC loss |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        lines.append(
            f"| {row['target_size']:.3f} | {row['total_return']:.1%} | "
            f"{row['max_drawdown']:.1%} | {row['sharpe_ratio']:.2f} | "
            f"{row['trade_count']} | {row['mc_dd30_probability']:.1%} | "
            f"{row['mc_loss_probability']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Gate 1",
            "",
            f"- safe_baseline: {selection['safe_baseline']}",
            f"- balanced_baseline: {selection['balanced_baseline']}",
            f"- aggressive_candidate: {selection['aggressive_candidate']}",
            f"- note: {selection['note']}",
            "",
        ]
    )
    return "\n".join(lines)


def _select_baselines(results: list[dict]) -> dict[str, str]:
    by_size = {row["target_size"]: row for row in results}
    qualified = [
        row
        for row in results
        if row["target_size"] < 1.0
        and row["mc_dd30_probability"] < 0.15
        and row["mc_loss_probability"] < 0.05
    ]
    promotable = [row for row in qualified if not 0.14 <= row["mc_dd30_probability"] <= 0.16]
    if not qualified:
        return {
            "safe_baseline": "0.40x fallback",
            "balanced_baseline": "0.40x fallback",
            "aggressive_candidate": "0.45x prior",
            "note": "no size passed hard MC filters",
        }
    safe = min(qualified, key=lambda row: row["target_size"])
    balanced_pool = promotable or qualified
    balanced = max(balanced_pool, key=lambda row: row["total_return"])
    aggressive = by_size.get(0.45)
    aggressive_note = ""
    if aggressive is not None and 0.14 <= aggressive["mc_dd30_probability"] <= 0.16:
        aggressive_note = " (borderline; rerun 5000 sims or alternate seed before promotion)"
    return {
        "safe_baseline": f"{safe['target_size']:.3f}x",
        "balanced_baseline": f"{balanced['target_size']:.3f}x",
        "aggressive_candidate": f"0.450x{aggressive_note}" if aggressive is not None else "n/a",
        "note": "selection uses MC DD<-30% <15% and MC loss <5%",
    }


def _profit_factor(winners: list[float], losers: list[float]) -> float:
    loss_sum = sum(losers)
    return abs(sum(winners) / loss_sum) if loss_sum else float("inf")


def _top_loss_contribution(losers: list[float], n: int) -> float:
    total = abs(sum(losers))
    if total <= 0:
        return 0.0
    return abs(sum(sorted(losers)[:n])) / total


def _max_underwater_bars(equity: np.ndarray) -> int:
    peak = float(equity[0])
    current = 0
    longest = 0
    for value in equity:
        if value >= peak:
            peak = float(value)
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _monte_carlo(equity: np.ndarray, sims: int, seed: int) -> dict[str, float | int]:
    daily_returns = _daily_returns(equity)
    rng = np.random.default_rng(seed)
    returns = np.empty(sims)
    drawdowns = np.empty(sims)
    days = len(daily_returns)
    for sim in range(sims):
        sampled = daily_returns[rng.integers(0, days, size=days)]
        path = INITIAL_CAPITAL * np.cumprod(1.0 + sampled)
        returns[sim] = path[-1] / INITIAL_CAPITAL - 1.0
        drawdowns[sim] = _max_drawdown(path)
    return {
        "mc_sims": sims,
        "mc_seed": seed,
        "mc_return_p5": float(np.percentile(returns, 5)),
        "mc_return_p50": float(np.percentile(returns, 50)),
        "mc_return_p95": float(np.percentile(returns, 95)),
        "mc_dd_p5": float(np.percentile(drawdowns, 5)),
        "mc_dd_p50": float(np.percentile(drawdowns, 50)),
        "mc_dd_p95": float(np.percentile(drawdowns, 95)),
        "mc_loss_probability": float(np.mean(returns < 0)),
        "mc_dd30_probability": float(np.mean(drawdowns < -0.30)),
    }


def _daily_returns(equity: np.ndarray) -> np.ndarray:
    usable = (len(equity) // BLOCK_BARS) * BLOCK_BARS
    if usable <= BLOCK_BARS:
        return np.array([equity[-1] / equity[0] - 1.0], dtype=float)
    sampled = equity[:usable].reshape(-1, BLOCK_BARS)
    return sampled[:, -1] / sampled[:, 0] - 1.0


def _max_drawdown(equity: np.ndarray) -> float:
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        max_dd = min(max_dd, (value - peak) / peak)
    return float(max_dd)


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
