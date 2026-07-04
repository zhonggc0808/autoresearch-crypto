"""Research-only Fibonacci entry confirmation probe for v2.2 + TimesFM.

The experiment approximates "replace Bollinger confirmation with Fibonacci"
without touching production strategy code:

1. Build the current v2.2 baseline and the no-Bollinger-confirmation variant.
2. Apply deterministic Fibonacci extension windows to no-Bollinger decisions.
3. Apply the existing TimesFM exp_0068 gate after the Fibonacci gate.
4. Report full replay metrics plus top/worst normalized trade attribution.

No live/config/checkpoint/oracle/strategy/scoring/execution behavior is changed.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.checkpoints import load_checkpoint  # noqa: E402
from research_workspace.diagnostics.exp_0205_live_component_v1_standard_audit import (  # noqa: E402
    experiment as exp0205,
)
from scripts.research_oracle import (  # noqa: E402
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
)
from scripts.run_timesfm_breakout_filter_experiment import (  # noqa: E402
    apply_timesfm_gate,
    collect_decision_indices,
    forecast_decisions,
    load_forecast_cache,
)

EXPERIMENT_ID = "exp_0218"
CHECKPOINT = PROJECT_ROOT / "checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json"
DATA = PROJECT_ROOT / "data/crypto/ETHUSDT_5m_2600d.parquet"
TIMESFM_CACHE = PROJECT_ROOT / "research_workspace/diagnostics/timesfm_v22_m375_bbm375_h72_2600d_cache.json"
TIMESFM_MODEL = PROJECT_ROOT / "research_workspace/diagnostics/timesfm_local_model"
OUT_DIR = PROJECT_ROOT / "research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation"

CONTEXT = 1024
HORIZON = 72
MIN_EDGE_PCT = -0.01
RISK_FLOOR_PCT = 0.05
FIB_LOOKBACK = 375

FIB_WINDOWS = [
    {"variant": "fib_ext_1p000_1p618", "min": 1.000, "max": 1.618},
    {"variant": "fib_ext_1p000_1p382", "min": 1.000, "max": 1.382},
    {"variant": "fib_ext_1p000_1p236", "min": 1.000, "max": 1.236},
    {"variant": "fib_ext_1p236_1p618", "min": 1.236, "max": 1.618},
    {"variant": "fib_not_over_1p618", "min": 0.000, "max": 1.618},
]


def safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if np.isfinite(out) else default


def json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return str(value)
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    raise TypeError(f"unsupported json type: {type(value)!r}")


def pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return ""
    return f"{value * 100:.2f}%"


def no_bollinger_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    variant = deepcopy(checkpoint)
    for regime in ("bull", "bear", "neutral"):
        params = variant[regime]["strategy_params"]
        params["bollinger_breakout_enabled"] = False
    variant["variant"] = f"{variant.get('variant', 'unknown')}_no_bollinger_confirm"
    return variant


def build_fib_features(df: pd.DataFrame, lookback: int = FIB_LOOKBACK) -> pd.DataFrame:
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prior_high = high.shift(1).rolling(lookback, min_periods=lookback).max()
    prior_low = low.shift(1).rolling(lookback, min_periods=lookback).min()
    range_width = prior_high - prior_low
    return pd.DataFrame(
        {
            "prior_high": prior_high,
            "prior_low": prior_low,
            "range_width": range_width,
            "close": close,
            "long_extension": (close - prior_low) / range_width.replace(0.0, np.nan),
            "short_extension": (prior_high - close) / range_width.replace(0.0, np.nan),
            "long_overshoot": (close - prior_high) / range_width.replace(0.0, np.nan),
            "short_overshoot": (prior_low - close) / range_width.replace(0.0, np.nan),
        }
    )


def signal_target(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def fib_extension_at(features: pd.DataFrame, index: int, target: int) -> float:
    row = features.iloc[index]
    if target > 0:
        return safe_float(row["long_extension"])
    if target < 0:
        return safe_float(row["short_extension"])
    return float("nan")


def apply_fib_gate(
    signals: np.ndarray,
    features: pd.DataFrame,
    *,
    min_extension: float,
    max_extension: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    filtered = np.ones(len(signals), dtype=int)
    position = 0
    blocked_dir = 0
    diag: dict[str, Any] = {
        "decision_points": 0,
        "allowed_long": 0,
        "allowed_short": 0,
        "blocked_long": 0,
        "blocked_short": 0,
        "blocked_entries": [],
        "signals_changed": 0,
        "min_extension": min_extension,
        "max_extension": max_extension,
    }

    for i, raw_value in enumerate(signals.astype(int)):
        if raw_value == 1:
            filtered[i] = 1
            continue
        target = signal_target(int(raw_value), position)
        if target != blocked_dir:
            blocked_dir = 0
        if target == 0:
            filtered[i] = 0
            position = 0
            continue
        if target == position:
            filtered[i] = int(raw_value)
            continue
        if blocked_dir == target:
            filtered[i] = 0 if position else 1
            continue

        extension = fib_extension_at(features, i, target)
        allowed = np.isfinite(extension) and min_extension <= extension <= max_extension
        diag["decision_points"] += 1
        if allowed:
            filtered[i] = int(raw_value)
            position = target
            diag["allowed_long" if target > 0 else "allowed_short"] += 1
        else:
            filtered[i] = 0 if position else 1
            if position:
                position = 0
            blocked_dir = target
            diag["blocked_long" if target > 0 else "blocked_short"] += 1
            diag["blocked_entries"].append(
                {
                    "bar": i,
                    "direction": "long" if target > 0 else "short",
                    "extension": extension,
                }
            )

    diag["signals_changed"] = int(np.sum(filtered != signals))
    return filtered, diag


def metric_row(label: str, signals: np.ndarray, df: pd.DataFrame, split_idx: int) -> dict[str, Any]:
    row = exp0205.evaluate_variant(label, signals.astype(int), df, split_idx)
    fee10 = exp0205.evaluate_variant(f"{label}_fee10", signals.astype(int), df, split_idx, commission=0.001)
    row["fee10_oos_return"] = fee10["oos_return"]
    return row


def simulate_trades(signals: np.ndarray, df: pd.DataFrame) -> list[dict[str, Any]]:
    _, trades = exp0205.simulate_next_open(signals.astype(int), df)
    return trades


def normalized_top_worst(trades: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    rows: list[tuple[int, float]] = []
    for idx, trade in enumerate(trades):
        notional = safe_float(trade.get("entry_notional"))
        pnl = safe_float(trade.get("pnl"))
        if notional > 0 and np.isfinite(pnl):
            rows.append((idx, pnl / notional))
    top = sorted([item for item in rows if item[1] > 0], key=lambda item: item[1], reverse=True)
    worst = sorted([item for item in rows if item[1] < 0], key=lambda item: item[1])
    return {idx for idx, _ in top[:20]}, {idx for idx, _ in worst[:20]}


def paired_block_rows(
    component: str,
    diag: dict[str, Any],
    base_trades: list[dict[str, Any]],
    df: pd.DataFrame,
    split_idx: int,
) -> list[dict[str, Any]]:
    by_decision = {int(trade["entry_step"]) - 1: (idx, trade) for idx, trade in enumerate(base_trades)}
    top, worst = normalized_top_worst(base_trades)
    rows: list[dict[str, Any]] = []
    for entry in diag["blocked_entries"]:
        decision_bar = int(entry["bar"])
        row: dict[str, Any] = {
            "component": component,
            "decision_bar": decision_bar,
            "decision_time": str(pd.Timestamp(df["datetime"].iloc[decision_bar])),
            "direction": entry["direction"],
            "extension": safe_float(entry.get("extension")),
            "affected": False,
        }
        match = by_decision.get(decision_bar)
        if match is not None:
            trade_id, trade = match
            pnl = safe_float(trade.get("pnl"))
            notional = safe_float(trade.get("entry_notional"))
            baseline_return = pnl / notional if notional > 0 else float("nan")
            entry_step = int(trade["entry_step"])
            row.update(
                {
                    "affected": True,
                    "trade_id": trade_id,
                    "entry_step": entry_step,
                    "entry_time": str(pd.Timestamp(df["datetime"].iloc[entry_step])),
                    "exit_step": int(trade["step"]),
                    "baseline_return_pct": baseline_return,
                    "variant_return_pct": 0.0,
                    "delta_pct": -baseline_return,
                    "saved_loss": max(0.0, -baseline_return),
                    "missed_profit": max(0.0, baseline_return),
                    "is_top20_norm": trade_id in top,
                    "is_worst20_norm": trade_id in worst,
                    "in_oos": entry_step >= split_idx,
                    "year": int(pd.Timestamp(df["datetime"].iloc[entry_step]).year),
                }
            )
        rows.append(row)
    return rows


def summarize_blocks(rows: list[dict[str, Any]], component: str) -> dict[str, Any]:
    affected = [row for row in rows if row.get("affected")]
    deltas = [safe_float(row.get("delta_pct")) for row in affected]
    deltas = [value for value in deltas if np.isfinite(value)]
    saved = sum(max(0.0, safe_float(row.get("saved_loss", 0.0), 0.0)) for row in affected)
    missed = sum(max(0.0, safe_float(row.get("missed_profit", 0.0), 0.0)) for row in affected)
    by_year: dict[int, float] = {}
    for row in affected:
        year = int(row["year"])
        by_year[year] = by_year.get(year, 0.0) + safe_float(row.get("delta_pct"), 0.0)
    return {
        "variant": component,
        "fib_block_N": len(affected),
        "normalized_net_action_value": float(sum(deltas)),
        "saved_loss_sum": float(saved),
        "missed_profit_sum": float(missed),
        "saved_loss_to_missed_profit_ratio": saved / missed if missed > 0 else (float("inf") if saved > 0 else 0.0),
        "hurt_top20": int(sum(bool(row.get("is_top20_norm")) for row in affected)),
        "rescued_worst20": int(sum(bool(row.get("is_worst20_norm")) for row in affected)),
        "positive_years": int(sum(value > 0 for value in by_year.values())),
        "negative_years": int(sum(value < 0 for value in by_year.values())),
        "flat_years": int(sum(abs(value) <= 1e-12 for value in by_year.values())),
    }


def event_census(
    trades: list[dict[str, Any]],
    features: pd.DataFrame,
    df: pd.DataFrame,
) -> list[dict[str, Any]]:
    top, worst = normalized_top_worst(trades)
    rows = []
    labels = {
        "fib_extension_lt_1p236": lambda x: x < 1.236,
        "fib_extension_1p236_1p618": lambda x: 1.236 <= x <= 1.618,
        "fib_extension_gt_1p618": lambda x: x > 1.618,
    }
    entry_rows: list[dict[str, Any]] = []
    for trade_id, trade in enumerate(trades):
        decision_bar = int(trade["entry_step"]) - 1
        direction = exp0205.direction_from_trade(trade)
        target = 1 if direction == "long" else -1 if direction == "short" else 0
        extension = fib_extension_at(features, decision_bar, target)
        notional = safe_float(trade.get("entry_notional"))
        pnl = safe_float(trade.get("pnl"))
        entry_rows.append(
            {
                "trade_id": trade_id,
                "decision_bar": decision_bar,
                "entry_step": int(trade["entry_step"]),
                "entry_time": str(pd.Timestamp(df["datetime"].iloc[int(trade["entry_step"])])),
                "year": int(pd.Timestamp(df["datetime"].iloc[int(trade["entry_step"])]).year),
                "direction": direction,
                "extension": extension,
                "pnl_pct_entry_notional": pnl / notional if notional > 0 else float("nan"),
                "is_top20_norm": trade_id in top,
                "is_worst20_norm": trade_id in worst,
            }
        )
    entries = pd.DataFrame(entry_rows)
    for label, predicate in labels.items():
        mask = entries["extension"].map(lambda value: bool(np.isfinite(value) and predicate(float(value))))
        hit = entries.loc[mask]
        non = entries.loc[~mask]
        n = int(len(hit))
        rows.append(
            {
                "label": label,
                "N": n,
                "N_pct": n / len(entries) if len(entries) else 0.0,
                "years_covered": int(hit["year"].nunique()) if n else 0,
                "top20_hit": int(hit["is_top20_norm"].sum()) if n else 0,
                "worst20_hit": int(hit["is_worst20_norm"].sum()) if n else 0,
                "pnl_pct_sum": float(pd.to_numeric(hit["pnl_pct_entry_notional"], errors="coerce").sum()),
                "mean_return": float(pd.to_numeric(hit["pnl_pct_entry_notional"], errors="coerce").mean()) if n else float("nan"),
                "non_event_mean_return": float(pd.to_numeric(non["pnl_pct_entry_notional"], errors="coerce").mean()) if len(non) else float("nan"),
                "verdict": "OBSERVE" if n >= 10 else "REJECT",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def verdict(row: dict[str, Any]) -> str:
    if row["oos_delta_vs_baseline"] > 0 and row["rolling12_delta_vs_baseline"] >= 0:
        if row["hurt_top20"] <= 2 and row["rescued_worst20"] >= max(3, 3 * row["hurt_top20"]):
            return "PHASE2_MANUAL_REVIEW_REQUIRED"
        return "OBSERVE_WINNER_RISK"
    return "REJECT"


def report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# exp_0218 Fibonacci Entry Confirmation",
        "",
        "Scope: research-only probe. It approximates replacing v2.2 Bollinger",
        "breakout confirmation with causal Fibonacci extension windows, then",
        "applies the existing TimesFM exp_0068 gate. No live/config/checkpoint/",
        "oracle/strategy/scoring/execution behavior changed.",
        "",
        "## Replay Matrix",
        "",
        "| variant | raw OOS | TimesFM OOS | TimesFM DD | rolling12 | trades | fib blocks | top20 | worst20 | net action | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["summary"]:
        lines.append(
            f"| {row['variant']} | {pct(row.get('raw_oos_return'))} | "
            f"{pct(row.get('timesfm_oos_return'))} | {pct(row.get('timesfm_max_dd'))} | "
            f"{pct(row.get('timesfm_rolling12_min'))} | {row.get('timesfm_trade_count')} | "
            f"{row.get('fib_block_N')} | {row.get('hurt_top20')} | {row.get('rescued_worst20')} | "
            f"{pct(row.get('normalized_net_action_value'))} | {row.get('verdict')} |"
        )
    lines.extend(
        [
            "",
            "## Event Census On Current Baseline Trades",
            "",
            "| label | N | N% | years | top20 | worst20 | pnl sum | mean return | verdict |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["event_census"]:
        lines.append(
            f"| {row['label']} | {row['N']} | {row['N_pct']:.2%} | {row['years_covered']} | "
            f"{row['top20_hit']} | {row['worst20_hit']} | {pct(row['pnl_pct_sum'])} | "
            f"{pct(row['mean_return'])} | {row['verdict']} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `no_bollinger_raw` is the control showing what happens when BB confirmation is removed.",
            "- A Fibonacci replacement must beat the current BB+TimesFM baseline on OOS/rolling12 and avoid top-winner damage.",
            "- These rows are research diagnostics only; they do not authorize checkpoint/config/live changes.",
            "",
            "Evidence:",
            "",
            "- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_summary.csv`",
            "- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_events.csv`",
            "- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_paired_blocks.csv`",
            "- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_experiment.json`",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    checkpoint = load_checkpoint(CHECKPOINT)
    no_boll = no_bollinger_checkpoint(checkpoint)
    data_path = DATA if DATA.exists() else _find_eth_data()
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df = pd.concat([df_is, df_oos], ignore_index=True)
    if "datetime" not in df:
        df["datetime"] = pd.to_datetime(df["timestamp"], errors="coerce")
    else:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.reset_index(drop=True)
    features = build_fib_features(df)

    baseline_signals = _generate_v21_signals(checkpoint, df).astype(int)
    no_boll_signals = _generate_v21_signals(no_boll, df).astype(int)
    candidate_signals: dict[str, tuple[np.ndarray, dict[str, Any]]] = {}
    for spec in FIB_WINDOWS:
        candidate_signals[spec["variant"]] = apply_fib_gate(
            no_boll_signals,
            features,
            min_extension=spec["min"],
            max_extension=spec["max"],
        )

    all_decisions = set(collect_decision_indices(baseline_signals))
    all_decisions.update(collect_decision_indices(no_boll_signals))
    for signals, _ in candidate_signals.values():
        all_decisions.update(collect_decision_indices(signals))
    cache = load_forecast_cache(TIMESFM_CACHE)
    missing = sorted(index for index in all_decisions if index not in cache)
    forecasts = forecast_decisions(
        df,
        sorted(all_decisions),
        CONTEXT,
        HORIZON,
        16,
        TIMESFM_CACHE,
        str(TIMESFM_MODEL),
    )

    baseline_timesfm, baseline_timesfm_diag = apply_timesfm_gate(
        baseline_signals,
        {index: forecasts[index] for index in collect_decision_indices(baseline_signals)},
        MIN_EDGE_PCT,
        RISK_FLOOR_PCT,
    )
    no_boll_timesfm, no_boll_timesfm_diag = apply_timesfm_gate(
        no_boll_signals,
        {index: forecasts[index] for index in collect_decision_indices(no_boll_signals)},
        MIN_EDGE_PCT,
        RISK_FLOOR_PCT,
    )

    base_metric = metric_row("baseline_bb_timesfm", baseline_timesfm, df, split_idx)
    baseline_trades = simulate_trades(baseline_timesfm, df)
    no_boll_base_trades = simulate_trades(no_boll_signals, df)
    event_rows = event_census(baseline_trades, features, df)

    summary: list[dict[str, Any]] = []
    paired: list[dict[str, Any]] = []
    controls = [
        ("baseline_bb_timesfm", baseline_signals, baseline_timesfm, baseline_timesfm_diag, {"blocked_entries": []}),
        ("no_bollinger_raw", no_boll_signals, no_boll_timesfm, no_boll_timesfm_diag, {"blocked_entries": []}),
    ]
    for name, raw_signals, timesfm_signals, timesfm_diag, fib_diag in controls:
        raw_metric = metric_row(f"{name}_raw", raw_signals, df, split_idx)
        timesfm_metric = metric_row(f"{name}_timesfm", timesfm_signals, df, split_idx)
        row = {
            "variant": name,
            "parameter_set": "control",
            "raw_oos_return": raw_metric["oos_return"],
            "raw_full_return": raw_metric["full_return"],
            "raw_max_dd": raw_metric["max_dd"],
            "raw_rolling12_min": raw_metric["rolling12_min"],
            "raw_trade_count": raw_metric["trade_count"],
            "timesfm_oos_return": timesfm_metric["oos_return"],
            "timesfm_full_return": timesfm_metric["full_return"],
            "timesfm_max_dd": timesfm_metric["max_dd"],
            "timesfm_rolling12_min": timesfm_metric["rolling12_min"],
            "timesfm_trade_count": timesfm_metric["trade_count"],
            "timesfm_blocks": len(timesfm_diag["blocked_entries"]),
            "fib_block_N": 0,
            "normalized_net_action_value": 0.0,
            "saved_loss_to_missed_profit_ratio": 0.0,
            "hurt_top20": 0,
            "rescued_worst20": 0,
            "oos_delta_vs_baseline": timesfm_metric["oos_return"] - base_metric["oos_return"],
            "rolling12_delta_vs_baseline": timesfm_metric["rolling12_min"] - base_metric["rolling12_min"],
            "verdict": "BASELINE" if name == "baseline_bb_timesfm" else "CONTROL",
        }
        summary.append(row)

    for spec in FIB_WINDOWS:
        name = spec["variant"]
        fib_signals, fib_diag = candidate_signals[name]
        fib_timesfm, timesfm_diag = apply_timesfm_gate(
            fib_signals,
            {index: forecasts[index] for index in collect_decision_indices(fib_signals)},
            MIN_EDGE_PCT,
            RISK_FLOOR_PCT,
        )
        raw_metric = metric_row(f"{name}_raw", fib_signals, df, split_idx)
        timesfm_metric = metric_row(f"{name}_timesfm", fib_timesfm, df, split_idx)
        block_rows = paired_block_rows(name, fib_diag, no_boll_base_trades, df, split_idx)
        paired.extend(block_rows)
        block_summary = summarize_blocks(block_rows, name)
        row = {
            **block_summary,
            "parameter_set": json.dumps({"fib_lookback": FIB_LOOKBACK, "min": spec["min"], "max": spec["max"]}, sort_keys=True),
            "raw_oos_return": raw_metric["oos_return"],
            "raw_full_return": raw_metric["full_return"],
            "raw_max_dd": raw_metric["max_dd"],
            "raw_rolling12_min": raw_metric["rolling12_min"],
            "raw_trade_count": raw_metric["trade_count"],
            "timesfm_oos_return": timesfm_metric["oos_return"],
            "timesfm_full_return": timesfm_metric["full_return"],
            "timesfm_max_dd": timesfm_metric["max_dd"],
            "timesfm_rolling12_min": timesfm_metric["rolling12_min"],
            "timesfm_trade_count": timesfm_metric["trade_count"],
            "timesfm_blocks": len(timesfm_diag["blocked_entries"]),
            "oos_delta_vs_baseline": timesfm_metric["oos_return"] - base_metric["oos_return"],
            "rolling12_delta_vs_baseline": timesfm_metric["rolling12_min"] - base_metric["rolling12_min"],
            "dd_delta_vs_baseline": timesfm_metric["max_dd"] - base_metric["max_dd"],
            "fee10_oos_delta_vs_baseline": timesfm_metric["fee10_oos_return"] - base_metric["fee10_oos_return"],
        }
        row["verdict"] = verdict(row)
        summary.append(row)

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "scope": {
            "research_only": True,
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + TimesFM exp_0068",
            "replacement_approximation": "disable Bollinger confirmation in checkpoint copy, then apply causal Fibonacci extension gate before TimesFM",
            "data": str(data_path.relative_to(PROJECT_ROOT)),
            "data_window": f"{df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
            "split_idx": split_idx,
            "split_time": str(pd.Timestamp(df["datetime"].iloc[split_idx])),
            "timesfm_cache": str(TIMESFM_CACHE.relative_to(PROJECT_ROOT)),
            "timesfm_missing_forecasts_generated": len(missing),
        },
        "baseline": base_metric,
        "summary": summary,
        "event_census": event_rows,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUT_DIR / "exp_0218_summary.csv", summary)
    write_csv(OUT_DIR / "exp_0218_events.csv", event_rows)
    write_csv(OUT_DIR / "exp_0218_paired_blocks.csv", paired)
    (OUT_DIR / "exp_0218_experiment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )
    (OUT_DIR / "exp_0218_report.md").write_text(report_markdown(payload), encoding="utf-8")
    return payload


def main() -> None:
    payload = run()
    for row in payload["summary"]:
        print(
            row["variant"],
            row["verdict"],
            pct(row.get("timesfm_oos_return")),
            pct(row.get("timesfm_rolling12_min")),
            row.get("hurt_top20"),
            row.get("rescued_worst20"),
        )


if __name__ == "__main__":
    main()
