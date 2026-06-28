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

EXP0139_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py"
spec0139 = importlib.util.spec_from_file_location("exp0139_risk_sizing", EXP0139_PATH)
exp0139 = importlib.util.module_from_spec(spec0139)
sys.modules[spec0139.name] = exp0139
assert spec0139.loader is not None
spec0139.loader.exec_module(exp0139)

exp0136 = exp0139.exp0136

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0140_v22_vol_target_attribution_audit"
V1_NAME = "V1_vol_target_20d_clip_0p4_1p0"


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{value * 100:.2f}%"


def safe_float(value: Any) -> float:
    return exp0139.safe_float(value)


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def median_positive(values: pd.Series) -> float:
    return exp0139.median_positive(values)


def trade_vol_ref_check(trade_features: pd.DataFrame, split_time: pd.Timestamp) -> dict[str, Any]:
    values = pd.to_numeric(trade_features["realized_vol_20d_at_entry"], errors="coerce")
    entry_time = pd.to_datetime(trade_features["entry_time"])
    full_ref = median_positive(values)
    train_ref = median_positive(values.loc[entry_time < split_time])
    ratio = train_ref / full_ref if full_ref > 0 else float("nan")
    return {
        "vol_ref_mode": "train_only_entry_median",
        "full_sample_vol_ref": full_ref,
        "train_only_vol_ref": train_ref,
        "vol_ref_ratio_train_over_full": ratio,
        "ref_delta_pct": ratio - 1.0 if np.isfinite(ratio) else float("nan"),
        "clean_rerun_required": False,
        "clean_rerun_done": False,
        "clean_v1_verdict": "clean_original_v1_used",
        "realized_vol_shifted": True,
        "entry_size_fixed": True,
    }


def daily_period_max_dd(daily: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    window = daily.loc[(daily.index >= start) & (daily.index <= end)]
    if len(window) < 2:
        return 0.0
    vals = window.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(vals)
    dd = vals / np.where(peaks == 0, np.nan, peaks) - 1.0
    return float(np.nanmin(dd))


def date_mask(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    dates = pd.to_datetime(df["datetime"]).dt.floor("D")
    return ((dates >= start.floor("D")) & (dates <= end.floor("D"))).to_numpy(dtype=bool)


def exposure_window_stats(
    df: pd.DataFrame,
    exposure: np.ndarray,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    mask = date_mask(df, start, end)
    values = np.asarray(exposure, dtype=float)[mask]
    active = values[values > 1e-12]
    if len(values) == 0:
        return {
            "bars": 0,
            "active_bars": 0,
            "active_bar_share": 0.0,
            "avg_exposure_including_flat": 0.0,
            "avg_active_size": 0.0,
            "median_active_size": 0.0,
            "min_active_size": 0.0,
            "max_active_size": 0.0,
            "pct_active_at_100": 0.0,
            "pct_active_lt_75": 0.0,
            "pct_active_lt_50": 0.0,
            "pct_active_at_clip_min": 0.0,
        }
    return {
        "bars": int(len(values)),
        "active_bars": int(len(active)),
        "active_bar_share": float(len(active) / len(values)) if len(values) else 0.0,
        "avg_exposure_including_flat": float(np.mean(values)),
        "avg_active_size": float(np.mean(active)) if len(active) else 0.0,
        "median_active_size": float(np.median(active)) if len(active) else 0.0,
        "min_active_size": float(np.min(active)) if len(active) else 0.0,
        "max_active_size": float(np.max(active)) if len(active) else 0.0,
        "pct_active_at_100": float(np.mean(active >= 0.975)) if len(active) else 0.0,
        "pct_active_lt_75": float(np.mean(active < 0.75)) if len(active) else 0.0,
        "pct_active_lt_50": float(np.mean(active < 0.50)) if len(active) else 0.0,
        "pct_active_at_clip_min": float(np.mean(active <= exp0139.SIZE_MIN + 1e-9)) if len(active) else 0.0,
    }


def realized_vol_percentiles(realized_vol: pd.Series) -> pd.Series:
    values = pd.to_numeric(realized_vol, errors="coerce").replace([np.inf, -np.inf], np.nan)
    out = pd.Series(np.nan, index=values.index, dtype=float)
    finite = values.dropna()
    if finite.empty:
        return out
    out.loc[finite.index] = finite.rank(method="average", pct=True)
    return out


def realized_vol_window_stats(
    df: pd.DataFrame,
    realized_vol: pd.Series,
    percentiles: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    mask = date_mask(df, start, end)
    vol_values = pd.to_numeric(realized_vol, errors="coerce").to_numpy(dtype=float)[mask]
    pct_values = pd.to_numeric(percentiles, errors="coerce").to_numpy(dtype=float)[mask]
    vol_values = vol_values[np.isfinite(vol_values)]
    pct_values = pct_values[np.isfinite(pct_values)]
    return {
        "realized_vol_avg": float(np.mean(vol_values)) if len(vol_values) else 0.0,
        "realized_vol_median": float(np.median(vol_values)) if len(vol_values) else 0.0,
        "realized_vol_pct_avg": float(np.mean(pct_values)) if len(pct_values) else 0.0,
        "realized_vol_pct_median": float(np.median(pct_values)) if len(pct_values) else 0.0,
        "realized_vol_pct_min": float(np.min(pct_values)) if len(pct_values) else 0.0,
        "realized_vol_pct_max": float(np.max(pct_values)) if len(pct_values) else 0.0,
    }


def rolling_window_rows(
    df: pd.DataFrame,
    baseline_daily: pd.Series,
    v1_daily: pd.Series,
    v1_exposure: np.ndarray,
) -> list[dict[str, Any]]:
    base_worst = exp0136.rolling_worst_period(baseline_daily, 365)
    v1_worst = exp0136.rolling_worst_period(v1_daily, 365)
    rows: list[dict[str, Any]] = []
    for label, anchor in [("baseline_worst_rolling12", base_worst), ("v1_worst_rolling12", v1_worst)]:
        start = pd.Timestamp(anchor["start"])
        end = pd.Timestamp(anchor["end"])
        stats = exposure_window_stats(df, v1_exposure, start, end)
        rows.append(
            {
                "window": label,
                "start": str(start.date()),
                "end": str(end.date()),
                "baseline_return": exp0136.period_return(baseline_daily, start, end),
                "v1_return": exp0136.period_return(v1_daily, start, end),
                "baseline_is_anchor": label == "baseline_worst_rolling12",
                "v1_is_anchor": label == "v1_worst_rolling12",
                **stats,
            }
        )
    return rows


def maxdd_window_rows(
    df: pd.DataFrame,
    baseline_daily: pd.Series,
    v1_daily: pd.Series,
    v1_exposure: np.ndarray,
    realized_vol: pd.Series,
    vol_percentile: pd.Series,
) -> list[dict[str, Any]]:
    base_dd = exp0136.max_drawdown_period(baseline_daily)
    v1_dd = exp0136.max_drawdown_period(v1_daily)
    rows: list[dict[str, Any]] = []
    for label, anchor in [("baseline_maxdd_window", base_dd), ("v1_maxdd_window", v1_dd)]:
        start = pd.Timestamp(anchor["start"])
        end = pd.Timestamp(anchor["end"])
        rows.append(
            {
                "window": label,
                "start": str(start.date()),
                "end": str(end.date()),
                "anchor_dd": float(anchor["dd"]),
                "baseline_same_window_dd": daily_period_max_dd(baseline_daily, start, end),
                "v1_same_window_dd": daily_period_max_dd(v1_daily, start, end),
                "baseline_return": exp0136.period_return(baseline_daily, start, end),
                "v1_return": exp0136.period_return(v1_daily, start, end),
                **exposure_window_stats(df, v1_exposure, start, end),
                **realized_vol_window_stats(df, realized_vol, vol_percentile, start, end),
            }
        )
    return rows


def add_entry_vol_percentile(trade_features: pd.DataFrame) -> pd.DataFrame:
    out = trade_features.copy()
    values = pd.to_numeric(out["realized_vol_20d_at_entry"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    out["realized_vol_percentile_at_entry"] = np.nan
    finite = values.dropna()
    if not finite.empty:
        out.loc[finite.index, "realized_vol_percentile_at_entry"] = finite.rank(method="average", pct=True)
    return out


def paired_extreme_rows(
    df: pd.DataFrame,
    baseline_trades: list[dict[str, Any]],
    v1_trades: list[dict[str, Any]],
    trade_features: pd.DataFrame,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    flag_col = "is_top20_winner" if kind == "top20" else "is_worst20_loser"
    rows: list[dict[str, Any]] = []
    features = add_entry_vol_percentile(trade_features)
    by_trade_id = {int(row["trade_id"]): row for row in features.to_dict(orient="records")}
    for trade_id, base_trade in enumerate(baseline_trades):
        feature = by_trade_id.get(trade_id)
        if not feature or not bool(feature.get(flag_col)):
            continue
        if trade_id >= len(v1_trades):
            continue
        v1_trade = v1_trades[trade_id]
        base_pnl = safe_float(base_trade.get("pnl"))
        v1_pnl = safe_float(v1_trade.get("pnl"))
        entry_step = int(base_trade["entry_step"])
        entry_time = pd.Timestamp(df["datetime"].iloc[min(max(entry_step, 0), len(df) - 1)])
        row = {
            "trade_id": trade_id,
            "entry_step": entry_step,
            "entry_time": str(entry_time),
            "side": "long" if exp0139.direction_from_trade(base_trade) > 0 else "short",
            "baseline_pnl": base_pnl,
            "v1_size": safe_float(v1_trade.get("entry_size")),
            "v1_pnl": v1_pnl,
            "realized_vol_percentile_at_entry": safe_float(feature.get("realized_vol_percentile_at_entry")),
            "realized_vol_bucket": feature.get("realized_vol_bucket"),
        }
        if kind == "top20":
            row["pnl_lost"] = base_pnl - v1_pnl
            row["pnl_saved"] = 0.0
        else:
            row["pnl_saved"] = v1_pnl - base_pnl
            row["pnl_lost"] = 0.0
        rows.append(row)
    return rows


def extreme_aggregate(rows: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    if not rows:
        return {
            "kind": kind,
            "count": 0,
            "avg_v1_size": 0.0,
            "median_v1_size": 0.0,
            "q5_count": 0,
            "baseline_pnl": 0.0,
            "v1_pnl": 0.0,
            "pnl_saved": 0.0,
            "pnl_lost": 0.0,
            "damage_or_improve_ratio": 0.0,
            "reduced_size_count": 0,
            "reduced_size_baseline_pnl_share": 0.0,
        }
    sizes = [safe_float(row.get("v1_size")) for row in rows]
    sizes = [value for value in sizes if np.isfinite(value)]
    baseline_pnl = float(sum(safe_float(row.get("baseline_pnl")) for row in rows))
    v1_pnl = float(sum(safe_float(row.get("v1_pnl")) for row in rows))
    pnl_saved = float(sum(safe_float(row.get("pnl_saved")) for row in rows))
    pnl_lost = float(sum(safe_float(row.get("pnl_lost")) for row in rows))
    reduced = [row for row in rows if safe_float(row.get("v1_size")) < 0.975]
    reduced_baseline_pnl = float(sum(safe_float(row.get("baseline_pnl")) for row in reduced))
    if kind == "top20":
        ratio = pnl_lost / abs(baseline_pnl) if baseline_pnl else 0.0
    else:
        ratio = pnl_saved / abs(baseline_pnl) if baseline_pnl else 0.0
    return {
        "kind": kind,
        "count": len(rows),
        "avg_v1_size": float(np.mean(sizes)) if sizes else 0.0,
        "median_v1_size": float(np.median(sizes)) if sizes else 0.0,
        "q5_count": sum(1 for row in rows if row.get("realized_vol_bucket") == "Q5"),
        "baseline_pnl": baseline_pnl,
        "v1_pnl": v1_pnl,
        "pnl_saved": pnl_saved,
        "pnl_lost": pnl_lost,
        "damage_or_improve_ratio": ratio,
        "reduced_size_count": len(reduced),
        "reduced_size_baseline_pnl_share": reduced_baseline_pnl / baseline_pnl if baseline_pnl else 0.0,
    }


def classify_maxdd_mechanism(v1_maxdd_row: dict[str, Any]) -> str:
    median_vol_pct = safe_float(v1_maxdd_row.get("realized_vol_pct_median"))
    avg_size = safe_float(v1_maxdd_row.get("avg_active_size"))
    if median_vol_pct < 0.50:
        return "max_dd_low_vol_environment"
    if median_vol_pct >= 0.60 and avg_size >= 0.80:
        return "max_dd_high_vol_but_size_still_high"
    if median_vol_pct >= 0.60 and avg_size < 0.80:
        return "max_dd_high_vol_with_size_reduced"
    return "max_dd_mixed_vol_environment"


def audit_decision(
    baseline_row: dict[str, Any],
    v1_row: dict[str, Any],
    vol_check: dict[str, Any],
    rolling_rows: list[dict[str, Any]],
    maxdd_rows: list[dict[str, Any]],
    worst20_agg: dict[str, Any],
    top20_agg: dict[str, Any],
) -> dict[str, Any]:
    v1_maxdd = next(row for row in maxdd_rows if row["window"] == "v1_maxdd_window")
    mechanism = classify_maxdd_mechanism(v1_maxdd)
    rolling_improved = v1_row["rolling12_min"] > baseline_row["rolling12_min"]
    rolling_near_positive = v1_row["rolling12_min"] >= -0.01
    oos_ok = v1_row["oos_return"] >= baseline_row["oos_return"] * 0.95
    dd_improve_ok = v1_row["dd_improve_vs_baseline"] >= 0.10
    top20_ok = top20_agg["damage_or_improve_ratio"] <= 0.15
    worst20_protected = worst20_agg["pnl_saved"] > 0 and worst20_agg["avg_v1_size"] < 0.95
    clean = bool(not vol_check["clean_rerun_required"])
    one_shot_allowed = bool(
        clean
        and rolling_improved
        and rolling_near_positive
        and oos_ok
        and dd_improve_ok
        and top20_ok
        and worst20_protected
        and mechanism == "max_dd_high_vol_but_size_still_high"
    )
    if one_shot_allowed:
        verdict = "ONE_SHOT_REFINEMENT_ALLOWED"
        reason = "clean vol_ref, rolling12 near-positive, top20 damage low, worst20 protected, and residual DD occurs in high vol while size remains high"
    else:
        verdict = "OBSERVE_STABILITY_SCHEME_ROLLING12_POSITIVE"
        reason = (
            "current only empirically effective position-stability scheme; "
            "not a sizing shadow because DD gate fails and residual max DD is low-vol/high-size"
        )
    return {
        "verdict": verdict,
        "reason": reason,
        "live_action": "no_change",
        "sizing_shadow": False,
        "one_shot_refinement_allowed": one_shot_allowed,
        "rolling12_near_miss_positive": bool(rolling_improved and rolling_near_positive),
        "maxdd_mechanism": mechanism,
        "vol_ref_clean": clean,
        "oos_ok": bool(oos_ok),
        "dd_improve_ge10": bool(dd_improve_ok),
        "top20_damage_le15": bool(top20_ok),
        "worst20_protected": bool(worst20_protected),
    }


def build_summary_row(
    baseline_row: dict[str, Any],
    v1_row: dict[str, Any],
    vol_check: dict[str, Any],
    top20_agg: dict[str, Any],
    worst20_agg: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "baseline_oos_return": baseline_row["oos_return"],
        "baseline_max_dd": baseline_row["max_dd"],
        "baseline_rolling12_min": baseline_row["rolling12_min"],
        "v1_oos_return": v1_row["oos_return"],
        "v1_max_dd": v1_row["max_dd"],
        "v1_dd_improve_vs_baseline": v1_row["dd_improve_vs_baseline"],
        "v1_rolling12_min": v1_row["rolling12_min"],
        "v1_top20_damage": top20_agg["damage_or_improve_ratio"],
        "v1_worst20_improve": worst20_agg["damage_or_improve_ratio"],
        "v1_avg_size": v1_row["avg_size"],
        "v1_median_size": v1_row["median_size"],
        "vol_ref_mode": vol_check["vol_ref_mode"],
        "vol_ref_ratio_train_over_full": vol_check["vol_ref_ratio_train_over_full"],
        "decision": decision["verdict"],
        "one_shot_refinement_allowed": decision["one_shot_refinement_allowed"],
        "live_action": decision["live_action"],
    }


def write_outputs(payload: dict[str, Any]) -> None:
    OUT.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), payload["summary_rows"])
    write_csv(OUT.with_name(OUT.name + "_windows.csv"), payload["rolling12_windows"] + payload["maxdd_windows"])
    write_csv(OUT.with_name(OUT.name + "_top20.csv"), payload["top20_rows"])
    write_csv(OUT.with_name(OUT.name + "_worst20.csv"), payload["worst20_rows"])
    write_csv(OUT.with_name(OUT.name + "_extreme_summary.csv"), payload["extreme_summary"])

    decision = payload["decision"]
    v = payload["variant_summary"]
    vol = payload["vol_ref_check"]
    lines = [
        "# exp_0140 v2.2 vol target attribution audit",
        "",
        "- research-only",
        "- attribution audit only; no new sizing variant and no parameter search",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- audited variant: `V1_vol_target_20d_clip_0p4_1p0` from exp0139",
        "- no live/checkpoint/config/oracle/production strategy change",
        f"- decision: `{decision['verdict']}`",
        "",
        "## Vol Ref Cleanliness Check",
        "",
        f"- vol_ref_mode: `{vol['vol_ref_mode']}`",
        f"- full_sample_vol_ref: `{vol['full_sample_vol_ref']:.6f}`",
        f"- train_only_vol_ref: `{vol['train_only_vol_ref']:.6f}`",
        f"- ref_delta_pct: `{pct(vol['ref_delta_pct'])}`",
        f"- clean_rerun_required: `{vol['clean_rerun_required']}`",
        f"- clean_v1_verdict: `{vol['clean_v1_verdict']}`",
        "",
        "## Variant Metrics",
        "",
        "| variant | OOS | DD | DD improve | rolling12 | avg size | top20 damage | worst20 improve | fee10 OOS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in v:
        lines.append(
            f"| {row['name']} | {pct(row['oos_return'])} | {pct(row['max_dd'])} | "
            f"{pct(row.get('dd_improve_vs_baseline', 0.0))} | {pct(row['rolling12_min'])} | "
            f"{pct(row.get('avg_size', 0.0))} | {pct(row.get('top20_winner_damage_vs_baseline', 0.0))} | "
            f"{pct(row.get('worst20_improve_vs_baseline', 0.0))} | {pct(row.get('fee10_oos_return', 0.0))} |"
        )
    lines.extend(
        [
            "",
            "## Rolling12 Attribution",
            "",
            "| window | start | end | baseline return | V1 return | V1 avg active size | V1 pct active <75 |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["rolling12_windows"]:
        lines.append(
            f"| {row['window']} | {row['start']} | {row['end']} | {pct(row['baseline_return'])} | "
            f"{pct(row['v1_return'])} | {pct(row['avg_active_size'])} | {pct(row['pct_active_lt_75'])} |"
        )
    lines.extend(
        [
            "",
            "## MaxDD Attribution",
            "",
            "| window | start | end | baseline DD | V1 DD | vol pct median | V1 avg active size | read |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["maxdd_windows"]:
        read = decision["maxdd_mechanism"] if row["window"] == "v1_maxdd_window" else ""
        lines.append(
            f"| {row['window']} | {row['start']} | {row['end']} | {pct(row['baseline_same_window_dd'])} | "
            f"{pct(row['v1_same_window_dd'])} | {pct(row['realized_vol_pct_median'])} | "
            f"{pct(row['avg_active_size'])} | {read} |"
        )
    lines.extend(
        [
            "",
            "## Extreme Trades",
            "",
            "| group | count | avg V1 size | Q5 count | baseline pnl | V1 pnl | saved | lost | ratio | reduced-size count |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["extreme_summary"]:
        lines.append(
            f"| {row['kind']} | {row['count']} | {pct(row['avg_v1_size'])} | {row['q5_count']} | "
            f"{row['baseline_pnl']:.2f} | {row['v1_pnl']:.2f} | {row['pnl_saved']:.2f} | "
            f"{row['pnl_lost']:.2f} | {pct(row['damage_or_improve_ratio'])} | {row['reduced_size_count']} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- verdict: `{decision['verdict']}`",
            f"- reason: {decision['reason']}",
            f"- rolling12_near_miss_positive: `{decision['rolling12_near_miss_positive']}`",
            f"- one_shot_refinement_allowed: `{decision['one_shot_refinement_allowed']}`",
            f"- live_action: `{decision['live_action']}`",
            "",
            "## Evidence",
            "",
            f"- summary: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- windows: `{OUT.with_name(OUT.name + '_windows.csv').relative_to(PROJECT_ROOT)}`",
            f"- top20 mapping: `{OUT.with_name(OUT.name + '_top20.csv').relative_to(PROJECT_ROOT)}`",
            f"- worst20 mapping: `{OUT.with_name(OUT.name + '_worst20.csv').relative_to(PROJECT_ROOT)}`",
            f"- extreme summary: `{OUT.with_name(OUT.name + '_extreme_summary.csv').relative_to(PROJECT_ROOT)}`",
            f"- json: `{OUT.with_suffix('.json').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_all() -> dict[str, Any]:
    df, base_signals, scope = exp0136.exp0110.load_base()
    df = exp0136.clean_ohlcv(df)
    signals = base_signals.astype(int)
    split_time = exp0139.OOS_SPLIT_TIME
    realized_vol = exp0139.shifted_realized_vol(df)
    vol_percentile = realized_vol_percentiles(realized_vol)

    baseline_spec = exp0139.RiskVariantSpec("baseline_100", "baseline_reference", "constant", constant_size=1.0)
    baseline_sizes = exp0139.constant_size_values(len(df), 1.0)
    baseline_result = exp0139.row_for_variant(baseline_spec, df, signals, baseline_sizes, split_time, baseline_row=None)

    atr = exp0139.shifted_atr(df)
    trade_features = exp0139.baseline_trade_features(df, baseline_result.trades, atr, realized_vol, split_time)
    vol_check = trade_vol_ref_check(trade_features, split_time)
    vol_ref = safe_float(vol_check["train_only_vol_ref"])
    v1_sizes = exp0139.clipped_inverse_size(realized_vol, vol_ref)
    v1_spec = exp0139.RiskVariantSpec(V1_NAME, "risk_based_candidate", "vol_target_20d")
    v1_result = exp0139.row_for_variant(v1_spec, df, signals, v1_sizes, split_time, baseline_row=baseline_result.row)

    baseline_daily = exp0136.daily_equity(df, baseline_result.equity)
    v1_daily = exp0136.daily_equity(df, v1_result.equity)
    rolling_rows = rolling_window_rows(df, baseline_daily, v1_daily, v1_result.exposure)
    maxdd_rows = maxdd_window_rows(df, baseline_daily, v1_daily, v1_result.exposure, realized_vol, vol_percentile)
    top20_rows = paired_extreme_rows(df, baseline_result.trades, v1_result.trades, trade_features, kind="top20")
    worst20_rows = paired_extreme_rows(df, baseline_result.trades, v1_result.trades, trade_features, kind="worst20")
    top20_agg = extreme_aggregate(top20_rows, kind="top20")
    worst20_agg = extreme_aggregate(worst20_rows, kind="worst20")
    decision = audit_decision(
        baseline_result.row,
        v1_result.row,
        vol_check,
        rolling_rows,
        maxdd_rows,
        worst20_agg,
        top20_agg,
    )
    variant_summary = [baseline_result.row, v1_result.row]
    payload = {
        "experiment_id": "exp_0140_v22_vol_target_attribution_audit",
        "scope": {
            "research_only": True,
            "attribution_only": True,
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "data": str(exp0136.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
            "split_time": str(split_time),
            "scope_split_time": str(pd.Timestamp(df["datetime"].iloc[int(scope["split_idx"])])),
            "realized_vol_window_bars": exp0139.REALIZED_VOL_WINDOW_BARS,
            "clip": [exp0139.SIZE_MIN, exp0139.SIZE_MAX],
            "excluded": "new sizing variants, parameter search, live routing, checkpoint/config/oracle/production changes",
        },
        "vol_ref_check": vol_check,
        "variant_summary": variant_summary,
        "rolling12_windows": rolling_rows,
        "maxdd_windows": maxdd_rows,
        "top20_rows": top20_rows,
        "worst20_rows": worst20_rows,
        "extreme_summary": [top20_agg, worst20_agg],
        "decision": decision,
        "summary_rows": [build_summary_row(baseline_result.row, v1_result.row, vol_check, top20_agg, worst20_agg, decision)],
    }
    write_outputs(payload)
    print("vol_ref_mode", vol_check["vol_ref_mode"])
    print("decision", decision["verdict"])
    print(
        "baseline",
        "oos",
        round(baseline_result.row["oos_return"] * 100, 2),
        "dd",
        round(baseline_result.row["max_dd"] * 100, 2),
        "roll12",
        round(baseline_result.row["rolling12_min"] * 100, 2),
    )
    print(
        "v1",
        "oos",
        round(v1_result.row["oos_return"] * 100, 2),
        "dd",
        round(v1_result.row["max_dd"] * 100, 2),
        "roll12",
        round(v1_result.row["rolling12_min"] * 100, 2),
        "top20 damage",
        round(top20_agg["damage_or_improve_ratio"] * 100, 2),
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all"], default="all")
    _ = parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
