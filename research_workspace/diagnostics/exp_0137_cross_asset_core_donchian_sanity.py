from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXP0136_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py"
spec0136 = importlib.util.spec_from_file_location("exp0136_multitimeframe", EXP0136_PATH)
exp0136 = importlib.util.module_from_spec(spec0136)
sys.modules["exp0136_multitimeframe"] = exp0136
assert spec0136.loader is not None
spec0136.loader.exec_module(exp0136)

DATA_DIR = PROJECT_ROOT / "data/crypto"
OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity"

ASSETS = ("BTCUSDT", "SOLUSDT")
ENTRY_LOOKBACK = 375
MIN_HOLD_BARS = 432
SPLIT_RATIO = 0.70
DD_GATE = -0.85


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{value * 100:.2f}%"


def json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return str(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def parse_window_days(path: Path) -> int:
    match = re.search(r"_5m_(\d+)d\.parquet$", path.name)
    if not match:
        return -1
    return int(match.group(1))


def select_longest_5m_file(symbol: str, data_dir: Path = DATA_DIR) -> Path:
    candidates = [path for path in data_dir.glob(f"{symbol}_5m_*d.parquet") if parse_window_days(path) > 0]
    if not candidates:
        raise FileNotFoundError(f"No dated 5m parquet found for {symbol} under {data_dir}")
    return max(candidates, key=parse_window_days)


def load_asset_df(path: Path) -> pd.DataFrame:
    return exp0136.clean_ohlcv(pd.read_parquet(path))


def split_time_for(df: pd.DataFrame, split_ratio: float = SPLIT_RATIO) -> tuple[int, pd.Timestamp]:
    if len(df) < ENTRY_LOOKBACK + MIN_HOLD_BARS + 2:
        raise ValueError("Data window is too short for the configured Donchian sanity check")
    split_idx = int(len(df) * split_ratio)
    split_idx = min(max(split_idx, 1), len(df) - 1)
    return split_idx, pd.Timestamp(df["datetime"].iloc[split_idx])


def gate_flags(max_dd: float, oos_return: float) -> dict[str, bool]:
    dd_gate_pass = max_dd > DD_GATE
    oos_positive = oos_return > 0.0
    return {
        "dd_gate_pass": dd_gate_pass,
        "oos_positive": oos_positive,
        "passes_sanity": dd_gate_pass and oos_positive,
    }


def evaluate_asset(symbol: str, path: Path) -> dict[str, Any]:
    df = load_asset_df(path)
    split_idx, split_time = split_time_for(df)
    signals = exp0136.core_donchian_signals(df, ENTRY_LOOKBACK, MIN_HOLD_BARS)
    equity, trades = exp0136.evaluate_next_open(signals, df)
    oos_df, oos_signals = exp0136.slice_by_time(df, signals, split_time)
    oos_equity, oos_trades = exp0136.evaluate_next_open(oos_signals, oos_df)

    full_metrics = exp0136.equity_metrics(equity)
    oos_metrics = exp0136.equity_metrics(oos_equity)
    daily = exp0136.daily_equity(df, equity)
    flags = gate_flags(full_metrics["dd"], oos_metrics["return"])

    return {
        "symbol": symbol,
        "data_file": str(path.relative_to(PROJECT_ROOT)),
        "window_days_from_filename": parse_window_days(path),
        "rows": len(df),
        "start_time": str(pd.Timestamp(df["datetime"].iloc[0])),
        "end_time": str(pd.Timestamp(df["datetime"].iloc[-1])),
        "split_ratio": SPLIT_RATIO,
        "split_idx": split_idx,
        "split_time": str(split_time),
        "entry_lookback": ENTRY_LOOKBACK,
        "min_hold_bars": MIN_HOLD_BARS,
        "full_return": full_metrics["return"],
        "oos_return": oos_metrics["return"],
        "max_dd": full_metrics["dd"],
        "rolling12_min": exp0136.rolling_min_return(daily, 365),
        "trade_count": len(trades),
        "trades_per_year": len(trades) / exp0136.duration_years(df),
        "oos_trade_count": len(oos_trades),
        "oos_trades_per_year": len(oos_trades) / exp0136.duration_years(oos_df),
        **flags,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(payload: dict[str, Any]) -> None:
    rows = payload["rows"]
    OUT.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), rows)

    lines = [
        "# exp_0137 cross-asset core Donchian sanity",
        "",
        "- research-only; no live/checkpoint/config/oracle/production strategy change",
        "- question: do BTC/SOL core Donchian sleeves clear a minimal standalone sanity gate before a full cross-asset exp0137?",
        f"- strategy: core Donchian `m={ENTRY_LOOKBACK}`, `min_hold_bars={MIN_HOLD_BARS}`; no Moirai, no BB, no regime split, no MTG/BCD",
        "- data: each asset uses its longest available dated 5m parquet",
        "- execution: completed-bar signal, next 5m open fill",
        f"- sanity gate: `max_dd > {pct(DD_GATE)}` and `OOS_return > 0`",
        f"- verdict: `{payload['verdict']}`",
        "",
        "## Results",
        "",
        "| symbol | data | full | OOS | DD | trades | trades/year | OOS trades | DD gate | OOS > 0 | pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['symbol']} | {row['window_days_from_filename']}d | "
            f"{pct(row['full_return'])} | {pct(row['oos_return'])} | {pct(row['max_dd'])} | "
            f"{row['trade_count']} | {row['trades_per_year']:.2f} | {row['oos_trade_count']} | "
            f"{row['dd_gate_pass']} | {row['oos_positive']} | {row['passes_sanity']} |"
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Both assets must pass before running a full cross-asset exp0137.",
            "- If either asset fails, do not optimize this core Donchian cross-asset line from these parameters.",
            "- This diagnostic does not authorize live/demo routing or checkpoint promotion.",
            "",
            "## Evidence",
            "",
            f"- CSV: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- JSON: `{OUT.with_suffix('.json').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_all() -> dict[str, Any]:
    rows = [evaluate_asset(symbol, select_longest_5m_file(symbol)) for symbol in ASSETS]
    all_pass = all(row["passes_sanity"] for row in rows)
    payload = {
        "experiment_id": "exp_0137_cross_asset_core_donchian_sanity",
        "verdict": "OBSERVE_READY_FOR_FULL_EXP0137" if all_pass else "REJECT",
        "ready_for_full_exp0137": all_pass,
        "params": {
            "assets": ASSETS,
            "entry_lookback": ENTRY_LOOKBACK,
            "min_hold_bars": MIN_HOLD_BARS,
            "split_ratio": SPLIT_RATIO,
            "dd_gate": DD_GATE,
            "oos_gate": "> 0",
        },
        "rows": rows,
    }
    write_outputs(payload)
    print("verdict", payload["verdict"])
    for row in rows:
        print(
            row["symbol"],
            "full",
            round(row["full_return"] * 100, 2),
            "oos",
            round(row["oos_return"] * 100, 2),
            "dd",
            round(row["max_dd"] * 100, 2),
            "trades/y",
            round(row["trades_per_year"], 2),
            "pass",
            row["passes_sanity"],
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all"], default="all")
    parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
