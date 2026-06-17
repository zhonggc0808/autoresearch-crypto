"""Phase 0 MAE/MFE diagnostics for channel_breakout_375_432."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, SLIPPAGE
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategies.trade_ledger import enrich_trade_ledger

CHECKPOINT = "checkpoints/channel_breakout_375_432.pt"
DATA_FILE = Path("data/crypto/ETHUSDT_5m_2600d.parquet")
OOS_START = "2024-06-06 14:25:00"
OOS_END = "2026-06-12 02:55:00"
OUTPUT_DIR = Path("research_workspace/diagnostics")
OUTPUT_PREFIX = "channel_breakout_375_432_v2_oos_2600d"
TIMEFRAME_MINUTES = 5
SCRIPT_VERSION = "2026-06-17.mae_mfe.v2"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_oos = _load_oos_data()
    strategy = ChannelBreakoutTrendStrategy(entry_lookback=375, min_hold_bars=432)
    signals = strategy.generate_signals(df_oos)
    prices = df_oos["close"].to_numpy(dtype=float)

    evaluator = StrategyEvaluator()
    _, trades = evaluator.simulate(signals, prices, df_oos)
    ledger = enrich_trade_ledger(trades, df_oos, timeframe_minutes=TIMEFRAME_MINUTES)
    metadata = _run_metadata()
    for row in ledger:
        row.update(metadata)

    ledger_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_mae_mfe_ledger.csv"
    pd.DataFrame(ledger).to_csv(ledger_path, index=False)

    report_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_mae_mfe_report.md"
    report_path.write_text(generate_report(ledger, df_oos), encoding="utf-8")
    print(f"Wrote {ledger_path}")
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


def generate_report(trades: list[dict], price_df: pd.DataFrame) -> str:
    if not trades:
        return "# MAE/MFE Diagnostic Report\n\nNo closed trades.\n"

    pnls = np.array([float(t["pnl"]) for t in trades])
    winners = pnls[pnls > 0]
    losers = pnls[pnls < 0]
    winner_p80 = np.percentile(winners, 80) if len(winners) else 0.0
    loser_p20 = np.percentile(losers, 20) if len(losers) else 0.0
    big_winners = [t for t in trades if t["pnl"] > 0 and t["pnl"] >= winner_p80]
    big_losers = [t for t in trades if t["pnl"] < 0 and t["pnl"] <= loser_p20]
    all_losers = [t for t in trades if t["pnl"] < 0]

    lines = [
        "# MAE/MFE Diagnostic Report",
        "",
        f"Generated: {datetime.now().isoformat()}",
        f"Checkpoint: {CHECKPOINT}",
        f"Data: {DATA_FILE}",
        f"OOS: {OOS_START} to {OOS_END}",
        f"Git commit: {_git_commit()}",
        f"Script version: {SCRIPT_VERSION}",
        f"Fee/slippage: commission={COMMISSION}, slippage={SLIPPAGE}",
        f"Closed trades: {len(trades)}",
        "",
        "## Q1: Big Winners MAE",
        _mae_table(big_winners, "big winners"),
        "## Q2: Big Losers MAE",
        _mae_table(big_losers, "big losers"),
        "## Q3: Big Losers Time To MAE",
        _median_line(big_losers, "time_to_mae_bars", "bars"),
        _median_line(big_losers, "time_to_mae_hours", "hours"),
        "",
        "## Q4: Big Winners Time To MFE",
        _median_line(big_winners, "time_to_mfe_bars", "bars"),
        _median_line(big_winners, "time_to_mfe_hours", "hours"),
        "",
        "## Q5: Horizon Unrealized PnL",
        _horizon_report(trades, price_df),
        "## Q6: Break-Even Stop Killed Big Winners",
        _be_report(big_winners, price_df),
        "## Q7: Top Loss Contribution",
        _top_loss_report(all_losers),
        "## Gate 0",
        _gate0_report(big_winners, big_losers, price_df),
    ]
    return "\n".join(lines)


def _mae_table(trades: list[dict], label: str) -> str:
    rows = ["", "| Threshold | Count | Share |", "|---|---:|---:|"]
    for threshold in [-0.04, -0.05, -0.06, -0.07]:
        count = sum(1 for trade in trades if trade["mae_pct"] < threshold)
        share = count / len(trades) if trades else 0.0
        rows.append(f"| MAE < {threshold:.0%} | {count} | {share:.1%} |")
    return "\n".join(rows) + f"\n\nSample: {len(trades)} {label}\n"


def _median_line(trades: list[dict], field: str, unit: str) -> str:
    values = [float(t[field]) for t in trades]
    value = float(np.median(values)) if values else 0.0
    return f"- Median {field}: {value:.2f} {unit}"


def _horizon_report(trades: list[dict], price_df: pd.DataFrame) -> str:
    close = price_df["close"].to_numpy(dtype=float)
    lines = []
    for hours in [48, 72, 96]:
        horizon_bars = int(hours * 60 / TIMEFRAME_MINUTES)
        lines.append(f"### {hours}h")
        for threshold in [0.0, -0.01, -0.02, -0.03]:
            final_pnls = []
            for trade in trades:
                horizon_step = int(trade["entry_step"]) + horizon_bars
                if horizon_step >= int(trade["exit_step"]) or horizon_step >= len(close):
                    continue
                ret = _return_at(trade["side"], trade["entry_price"], close[horizon_step])
                if ret < threshold:
                    final_pnls.append(float(trade["pnl"]))
            median = float(np.median(final_pnls)) if final_pnls else 0.0
            lines.append(
                f"- Unrealized < {threshold:.0%}: {len(final_pnls)} trades, "
                f"median final pnl {median:.2f}"
            )
        lines.append("")
    return "\n".join(lines)


def _be_report(big_winners: list[dict], price_df: pd.DataFrame) -> str:
    lines = []
    for trigger_mfe in [0.03, 0.04]:
        for stop_level in [0.0, 0.005]:
            killed = _count_be_killed(big_winners, trigger_mfe, stop_level, price_df)
            share = killed / len(big_winners) if big_winners else 0.0
            lines.append(
                f"- trigger {trigger_mfe:.1%}, stop {stop_level:.1%}: "
                f"{killed}/{len(big_winners)} ({share:.1%})"
            )
    return "\n".join(lines) + "\n"


def _top_loss_report(all_losers: list[dict]) -> str:
    total_loss = abs(sum(float(t["pnl"]) for t in all_losers))
    if total_loss <= 0:
        return "- No losing trades.\n"
    sorted_losers = sorted(all_losers, key=lambda t: t["pnl"])
    top20 = abs(sum(float(t["pnl"]) for t in sorted_losers[:20])) / total_loss
    top50 = abs(sum(float(t["pnl"]) for t in sorted_losers[:50])) / total_loss
    return f"- Top 20 losses: {top20:.1%}\n- Top 50 losses: {top50:.1%}\n"


def _gate0_report(big_winners: list[dict], big_losers: list[dict], price_df: pd.DataFrame) -> str:
    mae5 = _share(big_winners, lambda t: t["mae_pct"] < -0.05)
    mae7 = _share(big_winners, lambda t: t["mae_pct"] < -0.07)
    loser_tmae = [float(t["time_to_mae_hours"]) for t in big_losers]
    med_loser_tmae = float(np.median(loser_tmae)) if loser_tmae else 0.0
    be_kill = _share(big_winners, lambda _: False)
    if big_winners:
        be_kill = _count_be_killed(big_winners, 0.03, 0.0, price_df) / len(big_winners)
    return "\n".join(
        [
            f"- Big winners MAE < -5%: {mae5:.1%}",
            f"- Big winners MAE < -7%: {mae7:.1%}",
            f"- Big losers median time_to_mae_hours: {med_loser_tmae:.1f}",
            f"- Tight BE killed big winners: {be_kill:.1%}",
            "",
        ]
    )


def _count_be_killed(
    trades: list[dict],
    trigger_mfe: float,
    stop_level: float,
    price_df: pd.DataFrame,
) -> int:
    high = price_df["high"].to_numpy(dtype=float)
    low = price_df["low"].to_numpy(dtype=float)
    close = price_df["close"].to_numpy(dtype=float)
    killed = 0
    for trade in trades:
        active = False
        entry_price = float(trade["entry_price"])
        for step in range(int(trade["entry_step"]) + 1, int(trade["exit_step"]) + 1):
            if step >= len(close):
                break
            if trade["side"] == "long":
                active = active or high[step] / entry_price - 1.0 >= trigger_mfe
            else:
                active = active or 1.0 - low[step] / entry_price >= trigger_mfe
            if active and _return_at(trade["side"], entry_price, close[step]) <= stop_level:
                killed += 1
                break
    return killed


def _return_at(side: str, entry_price: float, price: float) -> float:
    return price / entry_price - 1.0 if side == "long" else 1.0 - price / entry_price


def _share(trades: list[dict], predicate) -> float:
    return sum(1 for trade in trades if predicate(trade)) / len(trades) if trades else 0.0


def _run_metadata() -> dict[str, str | float]:
    return {
        "git_commit": _git_commit(),
        "checkpoint": CHECKPOINT,
        "data_file": str(DATA_FILE),
        "oos_start": OOS_START,
        "oos_end": OOS_END,
        "commission": COMMISSION,
        "slippage": SLIPPAGE,
        "script_version": SCRIPT_VERSION,
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
