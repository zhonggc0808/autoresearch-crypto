#!/usr/bin/env python3
"""Attribute baseline trades to frozen market-intel conditions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_quant import normalize_signals_for_position_mode
from dex.checkpoints import build_strategy_from_checkpoint, load_checkpoint
from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.data import list_crypto_files, load_crypto_data
from dex.market_intel import load_market_intel
from dex.strategies.base import StrategyEvaluator
from dex.strategy_signals import generate_strategy_signals

FEATURES = [
    "funding_rate",
    "funding_zscore",
    "open_interest",
    "oi_change_1h",
    "oi_change_4h",
    "oi_zscore",
    "price_return_1h",
    "realized_vol_1h",
    "mode",
    "reason",
]


def build_attribution(
    symbol: str,
    interval: str,
    days: int,
    checkpoint_path: str,
    market_intel_path: str,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Return one row per baseline closed trade with entry-time intel features."""
    checkpoint = load_checkpoint(checkpoint_path)
    strategy = build_strategy_from_checkpoint(checkpoint)
    enable_short = bool(checkpoint.get("params", {}).get("enable_short", True))

    df = load_crypto_data(_find_data_file(symbol, interval))
    df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    if days > 0:
        df = df.iloc[-days * 288 :].reset_index(drop=True)

    signals = generate_strategy_signals(strategy, df, enable_short=enable_short)
    signals = normalize_signals_for_position_mode(signals.copy(), long_only=not enable_short)
    min_idx = getattr(strategy, "window", getattr(strategy, "long_ma_period", 20))
    valid_df = df.iloc[min_idx:].reset_index(drop=True)
    valid_signals = signals[min_idx:]
    prices = df["close"].to_numpy(dtype=float)[min_idx:]

    evaluator = StrategyEvaluator(INITIAL_CAPITAL, COMMISSION, SLIPPAGE)
    _, metrics, trades = evaluator.evaluate(valid_signals, prices, valid_df)

    intel = load_market_intel(market_intel_path).copy()
    intel["available_at"] = pd.to_datetime(intel["available_at"], errors="coerce")
    intel = intel.dropna(subset=["available_at"]).sort_values("available_at")
    entries = []
    for trade in trades:
        if trade.get("pnl") is None or trade.get("entry_step") is None:
            continue
        entry_step = int(trade["entry_step"])
        close_step = int(trade["step"])
        entry_time = pd.Timestamp(valid_df["datetime"].iloc[entry_step])
        intel_row = _latest_intel(intel, entry_time)
        row = {
            "entry_step": entry_step,
            "close_step": close_step,
            "entry_time": entry_time,
            "close_time": pd.Timestamp(valid_df["datetime"].iloc[close_step]),
            "side": _side_from_close_type(str(trade.get("type", ""))),
            "pnl": float(trade["pnl"]),
            "holding_bars": close_step - entry_step,
        }
        for feature in FEATURES:
            row[feature] = intel_row.get(feature, np.nan) if intel_row is not None else np.nan
        entries.append(row)
    return pd.DataFrame(entries), metrics


def write_report(
    df: pd.DataFrame, metrics: dict[str, float], output_md: Path, output_csv: Path
) -> None:
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)

    baseline_win_rate = float((df["pnl"] > 0).mean()) if len(df) else 0.0
    sections = [
        "# Market Intel Rule Attribution",
        "",
        "Analyzed baseline strategy closed trades only. Each trade is labeled with the latest "
        "market-intel row where `available_at <= entry_time`.",
        "",
        "## Baseline",
        "",
        f"- trades: `{len(df)}`",
        f"- total_return: `{metrics['total_return'] * 100:.2f}%`",
        f"- max_drawdown: `{metrics['max_drawdown'] * 100:.2f}%`",
        f"- trade win_rate: `{baseline_win_rate * 100:.1f}%`",
        "",
    ]
    for side in ["long", "short", "all"]:
        side_df = df if side == "all" else df[df["side"] == side]
        sections.extend([f"## Side: {side}", ""])
        sections.append(_summary_table("mode", side_df))
        sections.append(
            _bucket_table("funding_zscore_bucket", _add_bucket(side_df, "funding_zscore"))
        )
        sections.append(_bucket_table("oi_change_1h_bucket", _add_bucket(side_df, "oi_change_1h")))
        sections.append(_bucket_table("oi_change_4h_bucket", _add_bucket(side_df, "oi_change_4h")))
        sections.append(_combo_table(side_df))
        sections.append("")

    output_md.write_text("\n".join(sections), encoding="utf-8")


def _summary_table(group_col: str, df: pd.DataFrame) -> str:
    if df.empty or group_col not in df.columns:
        return "_No rows._\n"
    return _format_table(_stats(df, group_col))


def _bucket_table(bucket_col: str, df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._\n"
    return _format_table(_stats(df, bucket_col))


def _combo_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._\n"
    work = df.copy()
    work["oi_price_combo"] = np.select(
        [
            (work["oi_change_1h"] > 0) & (work["price_return_1h"] > 0),
            (work["oi_change_1h"] > 0) & (work["price_return_1h"] <= 0),
            (work["oi_change_1h"] <= 0) & (work["price_return_1h"] > 0),
        ],
        ["oi_up_price_up", "oi_up_price_down", "oi_down_price_up"],
        default="oi_down_price_down",
    )
    return _format_table(_stats(work, "oi_price_combo"))


def _stats(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    grouped = df.groupby(group_col, dropna=False, observed=False)["pnl"]
    out = grouped.agg(count="count", mean="mean", median="median", total="sum")
    out["win_rate"] = grouped.apply(lambda x: float((x > 0).mean()))
    out["avg_holding_bars"] = df.groupby(group_col, dropna=False, observed=False)[
        "holding_bars"
    ].mean()
    return out.reset_index()


def _format_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._\n"
    formatted = df.copy()
    for col in ["mean", "median", "total", "win_rate", "avg_holding_bars"]:
        if col in formatted.columns:
            formatted[col] = formatted[col].map(lambda x: f"{x:.4f}")
    columns = list(formatted.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in formatted.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines) + "\n"


def _add_bucket(df: pd.DataFrame, col: str) -> pd.DataFrame:
    work = df.copy()
    bins = [-np.inf, -2, -1, 0, 1, 2, np.inf]
    if col.startswith("oi_change"):
        bins = [-np.inf, -0.05, -0.02, 0, 0.02, 0.05, np.inf]
    labels = [f"{bins[i]}..{bins[i + 1]}" for i in range(len(bins) - 1)]
    work[f"{col}_bucket"] = pd.cut(work[col].astype(float), bins=bins, labels=labels)
    return work


def _latest_intel(intel: pd.DataFrame, entry_time: pd.Timestamp) -> pd.Series | None:
    matched = intel[intel["available_at"] <= entry_time]
    if matched.empty:
        return None
    return matched.iloc[-1]


def _side_from_close_type(trade_type: str) -> str:
    if trade_type.startswith("sell"):
        return "long"
    if trade_type.startswith("buy_cover"):
        return "short"
    return "unknown"


def _find_data_file(symbol: str, interval: str) -> str:
    matches = [
        f
        for f in list_crypto_files()
        if symbol.upper() in Path(f).name.upper() and f"_{interval}" in Path(f).name
    ]
    if not matches:
        raise FileNotFoundError(f"No {symbol} {interval} parquet found")
    return max(matches, key=lambda p: Path(p).stat().st_size)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze market-intel attribution on baseline trades"
    )
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--days", type=int, default=25)
    parser.add_argument("--checkpoint", default="checkpoints/channel_breakout_v2_1_balanced.pt")
    parser.add_argument("--market-intel", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args()

    df, metrics = build_attribution(
        args.symbol,
        args.interval,
        args.days,
        args.checkpoint,
        args.market_intel,
    )
    write_report(df, metrics, Path(args.output_md), Path(args.output_csv))
    print(f"Wrote {len(df)} trade rows to {args.output_csv}")
    print(f"Wrote report to {args.output_md}")


if __name__ == "__main__":
    main()
