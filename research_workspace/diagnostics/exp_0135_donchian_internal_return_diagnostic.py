from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.regime_filter import build_daily_regime_labels

EXP0110_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0110_v22_moirai_reversal_confirmation_delay.py"
spec0110 = importlib.util.spec_from_file_location("exp0110_helper", EXP0110_PATH)
exp0110 = importlib.util.module_from_spec(spec0110)
sys.modules["exp0110_helper"] = exp0110
assert spec0110.loader is not None
spec0110.loader.exec_module(exp0110)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0135_donchian_internal_return_diagnostic"

DONCHIAN_WINDOW = 375
BB_WINDOW = 375
BB_STD_DEV = 1.5
TOLERANCES = (0.0, 0.005)
POST_SIGNAL_HORIZONS = (6, 12, 24, 72, 144, 288)
BP = 10_000


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{value * 100:.2f}%"


def money(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{value:.2f}"


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if np.isfinite(out) else float("nan")


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


def target_position(signal: int, current_position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return current_position


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns:
        if "timestamp" not in out.columns:
            raise ValueError("OHLCV data must contain datetime or timestamp")
        out["datetime"] = pd.to_datetime(out["timestamp"], errors="coerce")
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates(subset="datetime")
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            raise ValueError(f"OHLCV data must contain {col}")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    return out.reset_index(drop=True)


def build_channel_features(
    df: pd.DataFrame,
    donchian_window: int = DONCHIAN_WINDOW,
    bb_window: int = BB_WINDOW,
    bb_std_dev: float = BB_STD_DEV,
) -> pd.DataFrame:
    bars = clean_ohlcv(df)
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)

    donchian_upper_prev = high.rolling(donchian_window, min_periods=donchian_window).max().shift(1)
    donchian_lower_prev = low.rolling(donchian_window, min_periods=donchian_window).min().shift(1)
    bb_mid = close.rolling(bb_window, min_periods=bb_window).mean()
    bb_std = close.rolling(bb_window, min_periods=bb_window).std()
    bb_upper_prev = (bb_mid + bb_std_dev * bb_std).shift(1)
    bb_lower_prev = (bb_mid - bb_std_dev * bb_std).shift(1)

    return pd.DataFrame(
        {
            "datetime": bars["datetime"],
            "donchian_upper_prev": donchian_upper_prev.to_numpy(dtype=float),
            "donchian_lower_prev": donchian_lower_prev.to_numpy(dtype=float),
            "bb_upper_prev": bb_upper_prev.to_numpy(dtype=float),
            "bb_lower_prev": bb_lower_prev.to_numpy(dtype=float),
        }
    )


def internal_return_signal(position: int, close: float, upper_prev: float, lower_prev: float, tol: float) -> bool:
    if position > 0:
        return np.isfinite(upper_prev) and close <= upper_prev * (1.0 - tol)
    if position < 0:
        return np.isfinite(lower_prev) and close >= lower_prev * (1.0 + tol)
    return False


def also_inside_bb(close: float, bb_upper_prev: float, bb_lower_prev: float) -> bool:
    return bool(np.isfinite(bb_upper_prev) and np.isfinite(bb_lower_prev) and bb_lower_prev <= close <= bb_upper_prev)


def directional_return(position: int, start_price: float, end_price: float) -> float:
    if not np.isfinite(start_price) or not np.isfinite(end_price) or start_price <= 0 or end_price <= 0:
        return float("nan")
    if position > 0:
        return end_price / start_price - 1.0
    if position < 0:
        return start_price / end_price - 1.0
    return float("nan")


def trade_direction(trade: dict[str, Any], fallback_position: int) -> str:
    if str(trade.get("type", "")).startswith("sell"):
        return "long"
    if str(trade.get("type", "")).startswith("buy"):
        return "short"
    return "long" if fallback_position > 0 else "short"


def trade_at_bar(trades: list[dict[str, Any]], bar: int) -> dict[str, Any] | None:
    for trade in trades:
        if int(trade["entry_step"]) <= bar <= int(trade["step"]):
            return trade
    return None


def trade_for_position_at_bar(trades: list[dict[str, Any]], bar: int, position: int) -> dict[str, Any] | None:
    expected = "long" if position > 0 else "short"
    for trade in trades:
        if int(trade["entry_step"]) <= bar <= int(trade["step"]) and trade_direction(trade, position) == expected:
            return trade
    return None


def post_signal_path(close: np.ndarray, bar: int, position: int) -> dict[str, float]:
    base_price = float(close[bar])
    out: dict[str, float] = {}
    for horizon in POST_SIGNAL_HORIZONS:
        target = bar + horizon
        out[f"post_return_{horizon}"] = (
            directional_return(position, base_price, float(close[target])) if target < len(close) else float("nan")
        )
    return out


def signal_return_from_entry(position: int, entry_price: float, signal_close: float) -> float:
    return directional_return(position, entry_price, signal_close)


def giveback_capture_ratio(signal_return: float, baseline_return: float, life_mfe: float) -> float:
    if not np.isfinite(signal_return) or not np.isfinite(baseline_return) or not np.isfinite(life_mfe):
        return float("nan")
    giveback_room = life_mfe - baseline_return
    if giveback_room <= 0:
        return float("nan")
    return (signal_return - baseline_return) / giveback_room


def top_and_worst_sets(trades: list[dict[str, Any]]) -> tuple[float, set[int]]:
    top_winners = sorted([float(t["pnl"]) for t in trades if float(t["pnl"]) > 0], reverse=True)[:20]
    top20_cutoff = min(top_winners) if top_winners else float("inf")
    worst_losers = sorted([t for t in trades if float(t["pnl"]) < 0], key=lambda t: float(t["pnl"]))[:20]
    worst20_entries = {int(t["entry_step"]) for t in worst_losers}
    return top20_cutoff, worst20_entries


def build_trade_feature_index(
    trades: list[dict[str, Any]],
    signals: np.ndarray,
    df: pd.DataFrame,
) -> dict[int, dict[str, Any]]:
    rows = exp0110.helper0108.trade_features(trades, signals, df)
    return {int(row["entry_bar"]): row for row in rows}


def collect_internal_return_events(
    signals: np.ndarray,
    df: pd.DataFrame,
    features: pd.DataFrame,
    regimes: np.ndarray,
    trades: list[dict[str, Any]],
    trade_features_by_entry: dict[int, dict[str, Any]],
    top20_cutoff: float,
    worst20_entries: set[int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    bars = clean_ohlcv(df)
    close = bars["close"].to_numpy(dtype=float)
    pos = 0
    current_trade_entry = -1
    active_by_tol = {tol: False for tol in TOLERANCES}
    events: list[dict[str, Any]] = []
    diagnostics = {"baseline_conflict_skipped": 0}

    for i in range(len(signals)):
        prev_signal = int(signals[i - 1]) if i else 1
        target = target_position(prev_signal, pos)
        if target != pos:
            pos = target
            current_trade_entry = -1
            active_by_tol = {tol: False for tol in TOLERANCES}
            if pos != 0:
                trade = trade_for_position_at_bar(trades, i, pos)
                current_trade_entry = int(trade["entry_step"]) if trade else i

        decision = int(signals[i])
        raw_target = target_position(decision, pos)
        if pos == 0:
            continue
        if raw_target != pos:
            diagnostics["baseline_conflict_skipped"] += 1
            active_by_tol = {tol: False for tol in TOLERANCES}
            continue

        trade = trade_for_position_at_bar(trades, i, pos)
        if trade is None:
            continue
        entry_bar = int(trade["entry_step"])
        if current_trade_entry != entry_bar:
            current_trade_entry = entry_bar
            active_by_tol = {tol: False for tol in TOLERANCES}

        entry_price = float(trade.get("entry_price") or np.nan)
        base_pnl = float(trade.get("pnl") or 0.0)
        entry_notional = float(trade.get("entry_notional") or 0.0)
        trade_feature = trade_features_by_entry.get(entry_bar, {})
        baseline_return = safe_float(trade_feature.get("realized_return"))
        life_mfe = safe_float(trade_feature.get("life_mfe"))
        direction = trade_direction(trade, pos)
        upper_prev = safe_float(features.at[i, "donchian_upper_prev"])
        lower_prev = safe_float(features.at[i, "donchian_lower_prev"])
        bb_upper_prev = safe_float(features.at[i, "bb_upper_prev"])
        bb_lower_prev = safe_float(features.at[i, "bb_lower_prev"])

        for tol in TOLERANCES:
            fired = internal_return_signal(pos, float(close[i]), upper_prev, lower_prev, tol)
            if not fired:
                active_by_tol[tol] = False
                continue
            if active_by_tol[tol]:
                continue
            active_by_tol[tol] = True

            sig_return = signal_return_from_entry(pos, entry_price, float(close[i]))
            delta_vs_baseline = sig_return - baseline_return
            event = {
                "bar": i,
                "time": str(pd.Timestamp(bars["datetime"].iloc[i])),
                "direction": direction,
                "position": pos,
                "tol": tol,
                "tol_bp": int(round(tol * BP)),
                "regime": str(regimes[i]).upper() if i < len(regimes) else "NEUTRAL",
                "also_inside_bb": also_inside_bb(float(close[i]), bb_upper_prev, bb_lower_prev),
                "is_top20": base_pnl >= top20_cutoff,
                "is_worst20": entry_bar in worst20_entries,
                "entry_bar": entry_bar,
                "entry_time": str(pd.Timestamp(bars["datetime"].iloc[entry_bar])),
                "exit_bar": int(trade["step"]),
                "exit_time": str(pd.Timestamp(bars["datetime"].iloc[int(trade["step"])])),
                "lead_time_bars": int(trade["step"]) - i,
                "bars_held_at_signal": i - entry_bar,
                "entry_price": entry_price,
                "signal_close": float(close[i]),
                "donchian_upper_prev": upper_prev,
                "donchian_lower_prev": lower_prev,
                "bb_upper_prev": bb_upper_prev,
                "bb_lower_prev": bb_lower_prev,
                "base_trade_pnl": base_pnl,
                "base_trade_entry_notional": entry_notional,
                "baseline_realized_return": baseline_return,
                "life_mfe": life_mfe,
                "signal_return_from_entry": sig_return,
                "signal_vs_baseline_return_delta": delta_vs_baseline,
                "giveback_capture_ratio": giveback_capture_ratio(sig_return, baseline_return, life_mfe),
                "signal_mfe_capture_ratio": sig_return / life_mfe
                if np.isfinite(sig_return) and np.isfinite(life_mfe) and life_mfe > 0
                else float("nan"),
                "attribution_only": True,
            }
            event.update(post_signal_path(close, i, pos))
            events.append(event)
    return events, diagnostics


def aggregate_buckets(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []
    df = pd.DataFrame(events)
    group_cols = ["regime", "direction", "tol_bp", "also_inside_bb", "is_top20"]
    rows: list[dict[str, Any]] = []
    for key, group in df.groupby(group_cols, dropna=False, sort=True):
        key_values = dict(zip(group_cols, key, strict=True))
        key_values["also_inside_bb"] = bool(key_values["also_inside_bb"])
        key_values["is_top20"] = bool(key_values["is_top20"])
        row: dict[str, Any] = {
            **key_values,
            "events": int(len(group)),
            "unique_trades": int(group["entry_bar"].nunique()),
            "top20_events": int(group["is_top20"].sum()),
            "worst20_events": int(group["is_worst20"].sum()),
            "avg_lead_time_bars": float(group["lead_time_bars"].mean()),
            "median_lead_time_bars": float(group["lead_time_bars"].median()),
            "avg_bars_held_at_signal": float(group["bars_held_at_signal"].mean()),
            "avg_signal_return_from_entry": float(group["signal_return_from_entry"].mean()),
            "avg_baseline_realized_return": float(group["baseline_realized_return"].mean()),
            "avg_signal_vs_baseline_return_delta": float(group["signal_vs_baseline_return_delta"].mean()),
            "positive_delta_rate": float((group["signal_vs_baseline_return_delta"] > 0).mean()),
            "avg_giveback_capture_ratio": float(group["giveback_capture_ratio"].mean()),
            "avg_signal_mfe_capture_ratio": float(group["signal_mfe_capture_ratio"].mean()),
            "base_trade_pnl_sum": float(group.drop_duplicates("entry_bar")["base_trade_pnl"].sum()),
        }
        for horizon in POST_SIGNAL_HORIZONS:
            col = f"post_return_{horizon}"
            valid = group[col].dropna()
            row[f"avg_{col}"] = float(valid.mean()) if len(valid) else float("nan")
            row[f"adverse_rate_{horizon}"] = float((valid < 0.0).mean()) if len(valid) else float("nan")
        rows.append(row)
    return sorted(
        rows,
        key=lambda item: (
            item["regime"],
            item["direction"],
            item["tol_bp"],
            bool(item["also_inside_bb"]),
            bool(item["is_top20"]),
        ),
    )


def aggregate_direction_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []
    df = pd.DataFrame(events)
    rows: list[dict[str, Any]] = []
    for key, group in df.groupby(["regime", "direction", "tol_bp"], dropna=False, sort=True):
        regime, direction, tol_bp = key
        row: dict[str, Any] = {
            "regime": regime,
            "direction": direction,
            "tol_bp": int(tol_bp),
            "events": int(len(group)),
            "unique_trades": int(group["entry_bar"].nunique()),
            "top20_events": int(group["is_top20"].sum()),
            "inside_bb_rate": float(group["also_inside_bb"].mean()),
            "avg_lead_time_bars": float(group["lead_time_bars"].mean()),
            "avg_delta": float(group["signal_vs_baseline_return_delta"].mean()),
            "positive_delta_rate": float((group["signal_vs_baseline_return_delta"] > 0).mean()),
            "avg_giveback_capture_ratio": float(group["giveback_capture_ratio"].mean()),
        }
        for horizon in (24, 72, 144, 288):
            valid = group[f"post_return_{horizon}"].dropna()
            row[f"avg_post_return_{horizon}"] = float(valid.mean()) if len(valid) else float("nan")
            row[f"adverse_rate_{horizon}"] = float((valid < 0.0).mean()) if len(valid) else float("nan")
        rows.append(row)
    return sorted(rows, key=lambda item: (item["regime"], item["direction"], item["tol_bp"]))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = sorted({k for row in rows for k in row}) if rows else ["empty"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def verdict_from_direction_rows(direction_rows: list[dict[str, Any]]) -> tuple[str, str]:
    useful = [
        row
        for row in direction_rows
        if row["events"] >= 5
        and row["top20_events"] == 0
        and row["avg_delta"] > 0.0
        and row.get("adverse_rate_72", 0.0) >= 0.55
    ]
    if not useful:
        return "REJECT", "no stable regime_direction bucket met Stage-2 exploration criteria"
    return "OBSERVE", "Stage-1 found at least one low-top20 adverse-followthrough bucket for review"


def run_stage1() -> dict[str, Any]:
    df, base_signals, scope = exp0110.load_base()
    df = clean_ohlcv(df)
    split_idx = int(scope["split_idx"])
    features = build_channel_features(df)
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    base_eval = exp0110.evaluate(base_signals, df, split_idx)
    _, base_trades = exp0110.helper0108.next_open_trades(base_signals, df)
    top20_cutoff, worst20_entries = top_and_worst_sets(base_trades)
    trade_features_by_entry = build_trade_feature_index(base_trades, base_signals, df)

    events, diagnostics = collect_internal_return_events(
        base_signals,
        df,
        features,
        regimes,
        base_trades,
        trade_features_by_entry,
        top20_cutoff,
        worst20_entries,
    )
    bucket_rows = aggregate_buckets(events)
    direction_rows = aggregate_direction_rows(events)
    verdict, verdict_reason = verdict_from_direction_rows(direction_rows)

    report = {
        "scope": {
            "experiment_id": "exp_0135",
            "stage": "stage_1_diagnostic_only",
            "lineage": "native channel-structure diagnostic; market-signal exit line remains frozen",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "data": str(exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
            "split_idx": split_idx,
            "donchian_window": DONCHIAN_WINDOW,
            "tol_values": list(TOLERANCES),
            "bb_window": BB_WINDOW,
            "bb_std_dev": BB_STD_DEV,
            "event_policy": "rising-edge internal-return events while baseline position remains unchanged",
            "lookahead_guard": "Donchian and Bollinger features use rolling values shifted by one completed 5m bar",
            "post_signal_path": "attribution-only future close path; not usable for live signal generation",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "moirai_blocked": int(scope["moirai_blocked"]),
            **diagnostics,
        },
        "baseline": {
            "raw_next_open": base_eval["summary"],
            "metrics": base_eval["metrics"],
            "trade_count": len(base_trades),
            "top20_cutoff_pnl": top20_cutoff,
        },
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "events": events,
        "buckets": bucket_rows,
        "direction_rows": direction_rows,
    }
    OUT.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )
    write_csv(OUT.with_suffix(".csv"), bucket_rows)
    write_csv(OUT.with_name(OUT.name + "_events").with_suffix(".csv"), events)
    write_csv(OUT.with_name(OUT.name + "_direction").with_suffix(".csv"), direction_rows)
    write_markdown(report)
    print(OUT.with_suffix(".md"))
    print(f"events {len(events)} buckets {len(bucket_rows)} verdict {verdict}")
    for row in sorted(direction_rows, key=lambda item: (item["avg_delta"], item["adverse_rate_72"]), reverse=True)[:8]:
        print(
            row["regime"],
            row["direction"],
            f"tol{row['tol_bp']}",
            "events",
            row["events"],
            "avg_delta",
            round(row["avg_delta"] * 100, 2),
            "adv72",
            round(row["adverse_rate_72"] * 100, 1),
            "top20",
            row["top20_events"],
        )
    return report


def write_markdown(report: dict[str, Any]) -> None:
    base = report["baseline"]
    rows = report["direction_rows"]
    buckets = report["buckets"]
    event_count = len(report["events"])
    verdict = report["verdict"]
    best_direction = max(rows, key=lambda item: item["avg_delta"]) if rows else None
    best_bucket = max(buckets, key=lambda item: item["avg_signal_vs_baseline_return_delta"]) if buckets else None
    md = [
        "# exp_0135 Donchian internal return diagnostic",
        "",
        "- diagnostic only; no trade action, live routing, checkpoint, config, oracle, or production strategy change",
        "- lineage: native channel-structure diagnostic; market-signal exit line remains frozen",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- signal: long `close <= donchian_upper_prev*(1-tol)`, short `close >= donchian_lower_prev*(1+tol)`",
        f"- Donchian: high/low rolling `{DONCHIAN_WINDOW}` shifted by one completed 5m bar",
        f"- tolerance: `{[int(t * BP) for t in TOLERANCES]}` bp",
        f"- Bollinger context: close rolling `{BB_WINDOW}`, std `{BB_STD_DEV}`, shifted by one completed 5m bar",
        "- buckets: regime x direction x tol x also_inside_bb x is_top20",
        "- event policy: rising-edge internal-return events only; same-bar baseline exits/reversals are skipped",
        "- post-signal path: attribution-only future close path, never a live signal input",
        f"- baseline OOS/full/DD/trades: {pct(base['raw_next_open']['oos']['return'])} / "
        f"{pct(base['metrics']['return'])} / {pct(base['metrics']['dd'])} / {base['metrics']['trades']}",
        f"- events/buckets/verdict: `{event_count}` / `{len(buckets)}` / `{verdict}`",
        "",
        "## Direction Summary",
        "",
        "| regime | direction | tol bp | events | unique trades | top20 | inside BB | avg delta | adv72 | adv288 | giveback capture |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda item: (item["regime"], item["direction"], item["tol_bp"])):
        md.append(
            f"| {row['regime']} | {row['direction']} | {row['tol_bp']} | {row['events']} | "
            f"{row['unique_trades']} | {row['top20_events']} | {pct(row['inside_bb_rate'])} | "
            f"{pct(row['avg_delta'])} | {pct(row['adverse_rate_72'])} | {pct(row['adverse_rate_288'])} | "
            f"{money(row['avg_giveback_capture_ratio'])} |"
        )
    md.extend(
        [
            "",
            "## Stage Read",
            "",
            f"- verdict: `{verdict}`",
            f"- reason: {report['verdict_reason']}",
            f"- best direction bucket: `{best_direction['regime']} {best_direction['direction']} tol{best_direction['tol_bp']}bp`, "
            f"avg delta `{pct(best_direction['avg_delta'])}`, top20 events `{best_direction['top20_events']}`, "
            f"adv72 `{pct(best_direction['adverse_rate_72'])}`."
            if best_direction
            else "- best direction bucket: none.",
            f"- best ex-post sub-bucket: `{best_bucket['regime']} {best_bucket['direction']} tol{best_bucket['tol_bp']}bp "
            f"inside_bb={best_bucket['also_inside_bb']} is_top20={best_bucket['is_top20']}`, "
            f"avg delta `{pct(best_bucket['avg_signal_vs_baseline_return_delta'])}`, "
            f"adv72 `{pct(best_bucket['adverse_rate_72'])}`."
            if best_bucket
            else "- best ex-post sub-bucket: none.",
            "- interpretation: positive non-top20 sub-buckets are ex-post only; `is_top20` cannot be known live, and follow-through is not strong enough to justify Stage 2.",
            "- Stage 2 remains unauthorized here; this file only identifies whether any regime x direction bucket deserves review.",
            "",
            "## Reporting Contract",
            "",
            f"- raw bucket result: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- event-level result: `{OUT.with_name(OUT.name + '_events').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- direction summary: `{OUT.with_name(OUT.name + '_direction').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            "- regime-permission result: not applicable; no signal stream is modified.",
            "- safe-execution result: not applicable; no execution signal is produced.",
            "- OOS return, max drawdown, rolling 12m, trade count: inherited baseline context only.",
            "- top-winner damage: diagnostic bucket field `is_top20`; no winner is cut because there is no trade action.",
            "- worst-loser field: event-level `is_worst20`; no loser is blocked because there is no trade action.",
            "- conclusion: research-only; no live/demo routing or checkpoint promotion is authorized.",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Donchian internal return Stage-1 diagnostic.")
    parser.add_argument("--mode", choices=["all"], default="all")
    parser.parse_args()
    run_stage1()


if __name__ == "__main__":
    main()
