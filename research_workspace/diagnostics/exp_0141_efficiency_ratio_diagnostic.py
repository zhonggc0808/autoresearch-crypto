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

EXP0139_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py"
spec0139 = importlib.util.spec_from_file_location("exp0139_risk_sizing", EXP0139_PATH)
exp0139 = importlib.util.module_from_spec(spec0139)
sys.modules[spec0139.name] = exp0139
assert spec0139.loader is not None
spec0139.loader.exec_module(exp0139)

exp0136 = exp0139.exp0136

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0141_efficiency_ratio_diagnostic"

ER_WINDOWS = (20, 50, 100)
DONCHIAN_WINDOW = 375
ROLLING12_DAYS = 365
Q_LABELS = ("Q1", "Q2", "Q3", "Q4", "Q5", "missing")


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


def shifted_efficiency_ratio(df: pd.DataFrame, window: int) -> pd.Series:
    """Kaufman-style ER at bar i, using only bars completed before bar i."""
    close = pd.Series(df["close"], dtype=float)
    net_move = (close - close.shift(window)).abs()
    path_move = close.diff().abs().rolling(window, min_periods=window).sum()
    er = net_move / path_move.replace(0.0, np.nan)
    return er.shift(1).clip(lower=0.0, upper=1.0)


def decision_bar_breakout_strength(
    df: pd.DataFrame,
    trades: list[dict[str, Any]],
    atr: pd.Series,
    window: int = DONCHIAN_WINDOW,
) -> pd.Series:
    """Breakout strength known at entry open: decision close vs pre-decision channel."""
    high = pd.Series(df["high"], dtype=float)
    low = pd.Series(df["low"], dtype=float)
    close = pd.Series(df["close"], dtype=float)
    upper_prev_at_decision = high.rolling(window, min_periods=window).max().shift(2)
    lower_prev_at_decision = low.rolling(window, min_periods=window).min().shift(2)
    decision_close = close.shift(1)
    out = pd.Series(np.nan, index=range(len(trades)), dtype=float)
    for trade_id, trade in enumerate(trades):
        entry_step = int(trade["entry_step"])
        if entry_step < 0 or entry_step >= len(df):
            continue
        direction = exp0139.direction_from_trade(trade)
        atr_value = safe_float(atr.iloc[entry_step]) if entry_step < len(atr) else float("nan")
        if not np.isfinite(atr_value) or atr_value <= 0:
            continue
        close_value = safe_float(decision_close.iloc[entry_step])
        if direction > 0:
            upper = safe_float(upper_prev_at_decision.iloc[entry_step])
            strength = (close_value - upper) / atr_value if np.isfinite(upper) else float("nan")
        elif direction < 0:
            lower = safe_float(lower_prev_at_decision.iloc[entry_step])
            strength = (lower - close_value) / atr_value if np.isfinite(lower) else float("nan")
        else:
            strength = float("nan")
        out.iloc[trade_id] = max(0.0, strength) if np.isfinite(strength) else float("nan")
    return out


def enrich_trade_features(
    df: pd.DataFrame,
    trades: list[dict[str, Any]],
    trade_features: pd.DataFrame,
    er_by_window: dict[int, pd.Series],
    breakout_strength: pd.Series,
    regimes: np.ndarray,
) -> pd.DataFrame:
    out = trade_features.copy()
    if out.empty:
        return out
    entry_steps = pd.to_numeric(out["entry_step"], errors="coerce").astype(int)
    entry_times = pd.to_datetime(out["entry_time"], errors="coerce")
    out["side"] = np.where(pd.to_numeric(out["direction"], errors="coerce") > 0, "long", "short")
    out["entry_month"] = entry_times.dt.strftime("%Y-%m")
    out["entry_quarter"] = entry_times.dt.to_period("Q").astype(str)
    out["entry_year"] = entry_times.dt.year.astype("Int64").astype(str)
    out["regime"] = [
        str(regimes[step]).upper() if 0 <= step < len(regimes) else "NEUTRAL"
        for step in entry_steps.to_numpy(dtype=int)
    ]
    out["breakout_strength"] = breakout_strength.reindex(out.index).to_numpy(dtype=float)
    out["breakout_strength_bucket"] = exp0139.assign_quintile_labels(
        pd.to_numeric(out["breakout_strength"], errors="coerce")
    )
    for window, er in er_by_window.items():
        col = f"er{window}_at_entry"
        values = [
            safe_float(er.iloc[step]) if 0 <= step < len(er) else float("nan")
            for step in entry_steps.to_numpy(dtype=int)
        ]
        out[col] = values
        out[f"er{window}_bucket"] = exp0139.assign_quintile_labels(pd.Series(values, index=out.index, dtype=float))
    _ = trades
    return out


def total_extreme_pnl(trades: pd.DataFrame) -> tuple[float, float]:
    top_total = float(pd.to_numeric(trades.loc[trades["is_top20_winner"], "pnl"], errors="coerce").sum())
    worst_total = float(pd.to_numeric(trades.loc[trades["is_worst20_loser"], "pnl"], errors="coerce").sum())
    return top_total, worst_total


def summarize_group(group: pd.DataFrame, total_top20_pnl: float, total_worst20_loss_abs: float) -> dict[str, Any]:
    pnls = pd.to_numeric(group.get("pnl", pd.Series(dtype=float)), errors="coerce").replace([np.inf, -np.inf], np.nan)
    returns = pd.to_numeric(group.get("trade_return", pd.Series(dtype=float)), errors="coerce").replace(
        [np.inf, -np.inf],
        np.nan,
    )
    winners = pnls[pnls > 0]
    losers = pnls[pnls < 0]
    gross_winner = float(winners.sum()) if len(winners) else 0.0
    gross_loser = float(losers.sum()) if len(losers) else 0.0
    top20_mask = group["is_top20_winner"] if "is_top20_winner" in group else pd.Series(False, index=group.index)
    worst20_mask = group["is_worst20_loser"] if "is_worst20_loser" in group else pd.Series(False, index=group.index)
    top20_pnl = float(pd.to_numeric(group.loc[top20_mask, "pnl"], errors="coerce").sum()) if len(group) else 0.0
    worst20_loss = float(pd.to_numeric(group.loc[worst20_mask, "pnl"], errors="coerce").sum()) if len(group) else 0.0
    return {
        "trade_count": int(len(group)),
        "oos_trade_count": int(group["is_oos"].sum()) if len(group) and "is_oos" in group else 0,
        "avg_trade_pnl": float(pnls.mean()) if len(pnls.dropna()) else 0.0,
        "median_trade_pnl": float(pnls.median()) if len(pnls.dropna()) else 0.0,
        "avg_trade_return": float(returns.mean()) if len(returns.dropna()) else 0.0,
        "median_trade_return": float(returns.median()) if len(returns.dropna()) else 0.0,
        "trade_pnl_std": float(pnls.std(ddof=0)) if len(pnls.dropna()) else 0.0,
        "win_rate": float(group["win"].mean()) if len(group) and "win" in group else 0.0,
        "gross_winner": gross_winner,
        "gross_loser": gross_loser,
        "profit_factor": gross_winner / abs(gross_loser) if gross_loser < 0 else 0.0,
        "top20_winner_count": int(top20_mask.sum()) if len(group) else 0,
        "top20_winner_pnl": top20_pnl,
        "top20_winner_pnl_share": top20_pnl / total_top20_pnl if total_top20_pnl > 0 else 0.0,
        "worst20_loser_count": int(worst20_mask.sum()) if len(group) else 0,
        "worst20_loser_loss": worst20_loss,
        "worst20_loser_loss_share": abs(worst20_loss) / total_worst20_loss_abs if total_worst20_loss_abs > 0 else 0.0,
        "avg_mae": float(pd.to_numeric(group.get("mae", pd.Series(dtype=float)), errors="coerce").mean())
        if len(group)
        else 0.0,
        "avg_mfe": float(pd.to_numeric(group.get("mfe", pd.Series(dtype=float)), errors="coerce").mean())
        if len(group)
        else 0.0,
    }


def er_bucket_summary(trades: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total_top20_pnl, total_worst20_loss = total_extreme_pnl(trades)
    total_worst20_loss_abs = abs(total_worst20_loss)
    for window in ER_WINDOWS:
        bucket_col = f"er{window}_bucket"
        for bucket in Q_LABELS:
            group = trades.loc[trades[bucket_col] == bucket] if bucket_col in trades else pd.DataFrame()
            rows.append(
                {
                    "er_window": f"ER{window}",
                    "bucket": bucket,
                    **summarize_group(group, total_top20_pnl, total_worst20_loss_abs),
                }
            )
    return rows


def er_oos_summary(trades: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    oos = trades.loc[trades["is_oos"]].copy() if "is_oos" in trades else pd.DataFrame()
    total_top20_pnl, total_worst20_loss = total_extreme_pnl(trades)
    total_worst20_loss_abs = abs(total_worst20_loss)
    for window in ER_WINDOWS:
        bucket_col = f"er{window}_bucket"
        for bucket in Q_LABELS:
            group = oos.loc[oos[bucket_col] == bucket] if bucket_col in oos else pd.DataFrame()
            rows.append(
                {
                    "er_window": f"ER{window}",
                    "bucket": bucket,
                    **summarize_group(group, total_top20_pnl, total_worst20_loss_abs),
                }
            )
    return rows


def er_top_worst_summary(bucket_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "er_window": row["er_window"],
            "bucket": row["bucket"],
            "trade_count": row["trade_count"],
            "top20_winner_count": row["top20_winner_count"],
            "top20_winner_pnl": row["top20_winner_pnl"],
            "top20_winner_pnl_share": row["top20_winner_pnl_share"],
            "worst20_loser_count": row["worst20_loser_count"],
            "worst20_loser_loss": row["worst20_loser_loss"],
            "worst20_loser_loss_share": row["worst20_loser_loss_share"],
            "trade_pnl_std": row["trade_pnl_std"],
        }
        for row in bucket_rows
    ]


def q1_cross_stats(bucket_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in bucket_rows:
        if row["bucket"] != "Q1":
            continue
        rows.append(
            {
                "er_window": row["er_window"],
                "Q1_lowest_ER_top20_count": row["top20_winner_count"],
                "Q1_lowest_ER_top20_pnl": row["top20_winner_pnl"],
                "Q1_lowest_ER_top20_pnl_share": row["top20_winner_pnl_share"],
                "Q1_lowest_ER_worst20_count": row["worst20_loser_count"],
                "Q1_lowest_ER_worst20_loss": row["worst20_loser_loss"],
                "Q1_lowest_ER_worst20_loss_share": row["worst20_loser_loss_share"],
            }
        )
    return rows


def rolling12_contribution_rows(
    trades: pd.DataFrame,
    baseline_daily: pd.Series,
) -> list[dict[str, Any]]:
    worst = exp0136.rolling_worst_period(baseline_daily, ROLLING12_DAYS)
    start = pd.Timestamp(worst["start"])
    end = pd.Timestamp(worst["end"])
    entry_times = pd.to_datetime(trades["entry_time"], errors="coerce")
    in_window = trades.loc[(entry_times >= start) & (entry_times <= end)]
    total_window_pnl = float(pd.to_numeric(in_window.get("pnl", pd.Series(dtype=float)), errors="coerce").sum())
    total_window_loss_abs = abs(
        float(
            pd.to_numeric(
                in_window.loc[pd.to_numeric(in_window.get("pnl", pd.Series(dtype=float)), errors="coerce") < 0, "pnl"],
                errors="coerce",
            ).sum()
        )
    )
    rows: list[dict[str, Any]] = []
    for window in ER_WINDOWS:
        bucket_col = f"er{window}_bucket"
        for bucket in Q_LABELS:
            group = in_window.loc[in_window[bucket_col] == bucket] if bucket_col in in_window else pd.DataFrame()
            pnl_sum = float(pd.to_numeric(group.get("pnl", pd.Series(dtype=float)), errors="coerce").sum())
            loss_sum = float(
                pd.to_numeric(
                    group.loc[pd.to_numeric(group.get("pnl", pd.Series(dtype=float)), errors="coerce") < 0, "pnl"],
                    errors="coerce",
                ).sum()
            ) if len(group) else 0.0
            rows.append(
                {
                    "er_window": f"ER{window}",
                    "bucket": bucket,
                    "baseline_worst_rolling12_start": str(start),
                    "baseline_worst_rolling12_end": str(end),
                    "baseline_worst_rolling12_return": safe_float(worst["return"]),
                    "trade_count_in_window": int(len(group)),
                    "pnl_in_window": pnl_sum,
                    "pnl_share_of_window": pnl_sum / total_window_pnl if total_window_pnl else 0.0,
                    "loss_in_window": loss_sum,
                    "loss_share_of_window": abs(loss_sum) / total_window_loss_abs if total_window_loss_abs else 0.0,
                }
            )
    return rows


def q1_extreme_distribution_rows(trades: pd.DataFrame) -> list[dict[str, Any]]:
    dimensions = [
        "entry_month",
        "entry_quarter",
        "entry_year",
        "side",
        "regime",
        "realized_vol_bucket",
        "breakout_strength_bucket",
    ]
    rows: list[dict[str, Any]] = []
    for window in ER_WINDOWS:
        q1 = trades.loc[trades[f"er{window}_bucket"] == "Q1"]
        groups = [
            ("top20", q1.loc[q1["is_top20_winner"]]),
            ("worst20", q1.loc[q1["is_worst20_loser"]]),
        ]
        for extreme_group, frame in groups:
            for dim in dimensions:
                if dim not in frame:
                    continue
                for value, group in frame.groupby(dim, dropna=False, sort=True):
                    rows.append(
                        {
                            "er_window": f"ER{window}",
                            "extreme_group": extreme_group,
                            "dimension": dim,
                            "value": str(value),
                            "count": int(len(group)),
                            "pnl_sum": float(pd.to_numeric(group["pnl"], errors="coerce").sum()),
                        }
                    )
    return rows


def q1_separability_rows(distribution_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dist = pd.DataFrame(distribution_rows)
    if dist.empty:
        return []
    rows: list[dict[str, Any]] = []
    for er_window in sorted(dist["er_window"].unique()):
        er_frame = dist.loc[dist["er_window"] == er_window]
        for dimension in sorted(er_frame["dimension"].unique()):
            dim_frame = er_frame.loc[er_frame["dimension"] == dimension]
            top = dim_frame.loc[dim_frame["extreme_group"] == "top20"]
            worst = dim_frame.loc[dim_frame["extreme_group"] == "worst20"]
            top_values = {str(value) for value in top["value"].tolist()}
            worst_values = {str(value) for value in worst["value"].tolist()}
            overlap = sorted(top_values & worst_values)
            top_count = int(pd.to_numeric(top.get("count", pd.Series(dtype=int)), errors="coerce").sum())
            worst_count = int(pd.to_numeric(worst.get("count", pd.Series(dtype=int)), errors="coerce").sum())
            top_dominant = ""
            worst_dominant = ""
            if not top.empty:
                top_dominant = str(top.sort_values(["count", "pnl_sum"], ascending=[False, False]).iloc[0]["value"])
            if not worst.empty:
                worst_dominant = str(worst.sort_values(["count", "pnl_sum"], ascending=[False, True]).iloc[0]["value"])
            rows.append(
                {
                    "er_window": er_window,
                    "dimension": dimension,
                    "top20_count": top_count,
                    "worst20_count": worst_count,
                    "top20_unique_values": len(top_values),
                    "worst20_unique_values": len(worst_values),
                    "overlap_value_count": len(overlap),
                    "overlap_values": ";".join(overlap[:20]),
                    "top20_dominant_value": top_dominant,
                    "worst20_dominant_value": worst_dominant,
                    "same_dominant_value": bool(top_dominant and worst_dominant and top_dominant == worst_dominant),
                    "separable_hint": bool(top_count > 0 and worst_count > 0 and len(overlap) == 0),
                }
            )
    return rows


def stage0_verdict_rows(
    bucket_rows: list[dict[str, Any]],
    rolling_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_window_bucket = {(row["er_window"], row["bucket"]): row for row in bucket_rows}
    rolling_by_window_bucket = {(row["er_window"], row["bucket"]): row for row in rolling_rows}
    rows: list[dict[str, Any]] = []
    for window in ER_WINDOWS:
        er_label = f"ER{window}"
        q1 = by_window_bucket.get((er_label, "Q1"), {})
        q2 = by_window_bucket.get((er_label, "Q2"), {})
        q3_q5 = [by_window_bucket.get((er_label, label), {}) for label in ("Q3", "Q4", "Q5")]
        q1_q2 = [row for row in (q1, q2) if row]
        high = [row for row in q3_q5 if row and row.get("trade_count", 0) > 0]
        q1_q2_median = float(np.median([safe_float(row.get("median_trade_return")) for row in q1_q2])) if q1_q2 else 0.0
        q3_q5_median = float(np.median([safe_float(row.get("median_trade_return")) for row in high])) if high else 0.0
        q1_q2_win = float(np.mean([safe_float(row.get("win_rate")) for row in q1_q2])) if q1_q2 else 0.0
        q3_q5_win = float(np.mean([safe_float(row.get("win_rate")) for row in high])) if high else 0.0
        q1_q2_worst = int(sum(int(row.get("worst20_loser_count", 0)) for row in q1_q2))
        q1_q2_top = int(sum(int(row.get("top20_winner_count", 0)) for row in q1_q2))
        q1_q2_top_share = float(sum(safe_float(row.get("top20_winner_pnl_share")) for row in q1_q2))
        q1_q2_worst_share = float(sum(safe_float(row.get("worst20_loser_loss_share")) for row in q1_q2))
        q1_roll = rolling_by_window_bucket.get((er_label, "Q1"), {})
        q2_roll = rolling_by_window_bucket.get((er_label, "Q2"), {})
        low_er_rolling_loss_share = safe_float(q1_roll.get("loss_share_of_window", 0.0)) + safe_float(
            q2_roll.get("loss_share_of_window", 0.0)
        )
        quality_worse = bool(q1_q2_median < q3_q5_median and q1_q2_win < q3_q5_win)
        worst_concentrated = bool(q1_q2_worst >= 8 or q1_q2_worst_share >= 0.40)
        top_not_concentrated = bool(q1_q2_top <= 3 and q1_q2_top_share <= 0.15)
        mixed_extremes = bool(
            (int(q1.get("top20_winner_count", 0)) >= 3 or safe_float(q1.get("top20_winner_pnl_share")) >= 0.15)
            and (
                int(q1.get("worst20_loser_count", 0)) >= 3
                or safe_float(q1.get("worst20_loser_loss_share")) >= 0.15
            )
        )
        wide_distribution = bool(safe_float(q1.get("trade_pnl_std")) > safe_float(q2.get("trade_pnl_std")))
        if quality_worse and worst_concentrated and top_not_concentrated and low_er_rolling_loss_share >= 0.25:
            verdict = "CLEAN_LOW_QUALITY_BUCKET"
            next_allowed = "Stage1 may test Q1 size 50%, Q2-Q5 100%; no skip"
        elif mixed_extremes or (q1_q2_top >= 4 and q1_q2_worst >= 4 and wide_distribution):
            verdict = "MIXED_HIGH_VARIANCE_BUCKET"
            next_allowed = "ER single-variable sizing frozen; only Q1 separability review allowed"
        else:
            verdict = "NO_SIGNAL"
            next_allowed = "ER direction frozen"
        rows.append(
            {
                "er_window": er_label,
                "stage0_verdict": verdict,
                "quality_worse_low_er": quality_worse,
                "worst20_concentrated_low_er": worst_concentrated,
                "top20_not_concentrated_low_er": top_not_concentrated,
                "mixed_extremes_in_q1": mixed_extremes,
                "wide_q1_distribution": wide_distribution,
                "q1_top20_count": int(q1.get("top20_winner_count", 0)),
                "q1_top20_pnl_share": safe_float(q1.get("top20_winner_pnl_share", 0.0)),
                "q1_worst20_count": int(q1.get("worst20_loser_count", 0)),
                "q1_worst20_loss_share": safe_float(q1.get("worst20_loser_loss_share", 0.0)),
                "q1_q2_top20_count": q1_q2_top,
                "q1_q2_top20_pnl_share": q1_q2_top_share,
                "q1_q2_worst20_count": q1_q2_worst,
                "q1_q2_worst20_loss_share": q1_q2_worst_share,
                "q1_q2_median_return": q1_q2_median,
                "q3_q5_median_return": q3_q5_median,
                "q1_q2_win_rate": q1_q2_win,
                "q3_q5_win_rate": q3_q5_win,
                "low_er_rolling12_loss_share": low_er_rolling_loss_share,
                "next_allowed": next_allowed,
            }
        )
    return rows


def overall_stage0_verdict(verdict_rows: list[dict[str, Any]]) -> str:
    verdicts = [row["stage0_verdict"] for row in verdict_rows]
    if "CLEAN_LOW_QUALITY_BUCKET" in verdicts:
        return "CLEAN_LOW_QUALITY_BUCKET"
    if "MIXED_HIGH_VARIANCE_BUCKET" in verdicts:
        return "MIXED_HIGH_VARIANCE_BUCKET"
    return "NO_SIGNAL"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in rows for key in row}) if rows else ["empty"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(payload: dict[str, Any]) -> None:
    OUT.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), payload["summary_rows"])
    write_csv(OUT.with_name(OUT.name + "_bucket_summary.csv"), payload["bucket_summary"])
    write_csv(OUT.with_name(OUT.name + "_oos_summary.csv"), payload["oos_summary"])
    write_csv(OUT.with_name(OUT.name + "_top_worst_summary.csv"), payload["top_worst_summary"])
    write_csv(OUT.with_name(OUT.name + "_q1_cross_stats.csv"), payload["q1_cross_stats"])
    write_csv(OUT.with_name(OUT.name + "_rolling12_contribution.csv"), payload["rolling12_contribution"])
    write_csv(OUT.with_name(OUT.name + "_q1_extreme_distribution.csv"), payload["q1_extreme_distribution"])
    write_csv(OUT.with_name(OUT.name + "_q1_separability.csv"), payload["q1_separability"])
    write_csv(OUT.with_name(OUT.name + "_stage0_verdict.csv"), payload["stage0_verdict_rows"])
    write_csv(OUT.with_name(OUT.name + "_trades.csv"), payload["trade_features"])
    write_report(payload)


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# exp_0141 efficiency ratio diagnostic",
        "",
        "- Stage 0 only",
        "- no trading action, no sizing, no skip rule",
        "- no live/checkpoint/config/oracle/production strategy change",
        "- low ER is pre-registered as either low quality, mixed high variance, or no signal",
        f"- overall verdict: `{payload['overall_stage0_verdict']}`",
        "",
        "## Baseline",
        "",
        "| full | OOS | DD | rolling12 | trades |",
        "|---:|---:|---:|---:|---:|",
    ]
    baseline = payload["baseline_row"]
    lines.append(
        f"| {pct(baseline['full_return'])} | {pct(baseline['oos_return'])} | "
        f"{pct(baseline['max_dd'])} | {pct(baseline['rolling12_min'])} | {baseline['trade_count']} |"
    )
    lines.extend(
        [
            "",
            "## Stage 0 Verdict",
            "",
            "| ER | verdict | Q1 top20 | Q1 top20 share | Q1 worst20 | Q1 worst share | Q1/Q2 median | Q3-Q5 median | rolling12 low-ER loss share |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["stage0_verdict_rows"]:
        lines.append(
            f"| {row['er_window']} | {row['stage0_verdict']} | {row['q1_top20_count']} | "
            f"{pct(row['q1_top20_pnl_share'])} | {row['q1_worst20_count']} | "
            f"{pct(row['q1_worst20_loss_share'])} | {pct(row['q1_q2_median_return'])} | "
            f"{pct(row['q3_q5_median_return'])} | {pct(row['low_er_rolling12_loss_share'])} |"
        )
    lines.extend(
        [
            "",
            "## ER Bucket Summary",
            "",
            "| ER | bucket | trades | OOS | avg ret | median ret | win | PF | top20 | top20 share | worst20 | worst share | pnl std |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["bucket_summary"]:
        lines.append(
            f"| {row['er_window']} | {row['bucket']} | {row['trade_count']} | {row['oos_trade_count']} | "
            f"{pct(row['avg_trade_return'])} | {pct(row['median_trade_return'])} | "
            f"{pct(row['win_rate'])} | {row['profit_factor']:.2f} | "
            f"{row['top20_winner_count']} | {pct(row['top20_winner_pnl_share'])} | "
            f"{row['worst20_loser_count']} | {pct(row['worst20_loser_loss_share'])} | "
            f"{row['trade_pnl_std']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Q1 Cross Stats",
            "",
            "| ER | Q1 top20 | Q1 top20 pnl | Q1 top20 share | Q1 worst20 | Q1 worst loss | Q1 worst share |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["q1_cross_stats"]:
        lines.append(
            f"| {row['er_window']} | {row['Q1_lowest_ER_top20_count']} | "
            f"{row['Q1_lowest_ER_top20_pnl']:.2f} | {pct(row['Q1_lowest_ER_top20_pnl_share'])} | "
            f"{row['Q1_lowest_ER_worst20_count']} | {row['Q1_lowest_ER_worst20_loss']:.2f} | "
            f"{pct(row['Q1_lowest_ER_worst20_loss_share'])} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- `CLEAN_LOW_QUALITY_BUCKET` is the only outcome that would permit a later lightweight ER sizing check.",
            "- `MIXED_HIGH_VARIANCE_BUCKET` means low ER carries both large winners and large losers; single-variable ER sizing is frozen.",
            "- `NO_SIGNAL` means ER buckets did not separate trade quality enough to justify Stage 1.",
            "- ER bucket thresholds here are full-sample diagnostic thresholds. Any later live-like sizing test must recompute train-only thresholds.",
            "",
            "## Evidence",
            "",
            f"- summary: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- bucket summary: `{OUT.with_name(OUT.name + '_bucket_summary.csv').relative_to(PROJECT_ROOT)}`",
            f"- OOS summary: `{OUT.with_name(OUT.name + '_oos_summary.csv').relative_to(PROJECT_ROOT)}`",
            f"- top/worst summary: `{OUT.with_name(OUT.name + '_top_worst_summary.csv').relative_to(PROJECT_ROOT)}`",
            f"- Q1 cross stats: `{OUT.with_name(OUT.name + '_q1_cross_stats.csv').relative_to(PROJECT_ROOT)}`",
            f"- rolling12 contribution: `{OUT.with_name(OUT.name + '_rolling12_contribution.csv').relative_to(PROJECT_ROOT)}`",
            f"- Q1 extreme distribution: `{OUT.with_name(OUT.name + '_q1_extreme_distribution.csv').relative_to(PROJECT_ROOT)}`",
            f"- Q1 separability: `{OUT.with_name(OUT.name + '_q1_separability.csv').relative_to(PROJECT_ROOT)}`",
            f"- stage0 verdict: `{OUT.with_name(OUT.name + '_stage0_verdict.csv').relative_to(PROJECT_ROOT)}`",
            f"- trade features: `{OUT.with_name(OUT.name + '_trades.csv').relative_to(PROJECT_ROOT)}`",
            f"- json: `{OUT.with_suffix('.json').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_all() -> dict[str, Any]:
    df, base_signals, scope = exp0136.exp0110.load_base()
    df = exp0136.clean_ohlcv(df)
    signals = base_signals.astype(int)
    split_time = exp0139.OOS_SPLIT_TIME

    baseline_spec = exp0139.RiskVariantSpec("baseline_100", "baseline_reference", "constant", constant_size=1.0)
    baseline_sizes = exp0139.constant_size_values(len(df), 1.0)
    baseline_result = exp0139.row_for_variant(baseline_spec, df, signals, baseline_sizes, split_time, baseline_row=None)

    atr = exp0139.shifted_atr(df)
    realized_vol = exp0139.shifted_realized_vol(df)
    er_by_window = {window: shifted_efficiency_ratio(df, window) for window in ER_WINDOWS}
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    raw_trade_features = exp0139.baseline_trade_features(df, baseline_result.trades, atr, realized_vol, split_time)
    breakout_strength = decision_bar_breakout_strength(df, baseline_result.trades, atr)
    trade_features = enrich_trade_features(
        df,
        baseline_result.trades,
        raw_trade_features,
        er_by_window,
        breakout_strength,
        regimes,
    )

    bucket_rows = er_bucket_summary(trade_features)
    oos_rows = er_oos_summary(trade_features)
    top_worst_rows = er_top_worst_summary(bucket_rows)
    q1_rows = q1_cross_stats(bucket_rows)
    baseline_daily = exp0136.daily_equity(df, baseline_result.equity)
    rolling_rows = rolling12_contribution_rows(trade_features, baseline_daily)
    distribution_rows = q1_extreme_distribution_rows(trade_features)
    separability_rows = q1_separability_rows(distribution_rows)
    verdict_rows = stage0_verdict_rows(bucket_rows, rolling_rows)
    overall = overall_stage0_verdict(verdict_rows)

    baseline_row = {
        "full_return": baseline_result.row["full_return"],
        "oos_return": baseline_result.row["oos_return"],
        "max_dd": baseline_result.row["max_dd"],
        "rolling12_min": baseline_result.row["rolling12_min"],
        "trade_count": baseline_result.row["trade_count"],
        "oos_trade_count": baseline_result.row["oos_trade_count"],
    }
    summary_rows = [
        {
            "experiment_id": "exp_0141_efficiency_ratio_diagnostic",
            "overall_stage0_verdict": overall,
            **baseline_row,
            "er_windows": ",".join(str(window) for window in ER_WINDOWS),
            "stage": "stage0_only",
            "trading_action": "none",
            "sizing_action": "none",
            "skip_action": "none",
        }
    ]
    payload = {
        "experiment_id": "exp_0141_efficiency_ratio_diagnostic",
        "overall_stage0_verdict": overall,
        "scope": {
            "research_only": True,
            "stage0_only": True,
            "attribution_only": True,
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "data": str(exp0136.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
            "split_time": str(split_time),
            "scope_split_time": str(pd.Timestamp(df["datetime"].iloc[int(scope["split_idx"])])),
            "er_windows": list(ER_WINDOWS),
            "donchian_window_for_breakout_strength": DONCHIAN_WINDOW,
            "execution": "no new execution; baseline trades only; completed-bar ER/features at entry",
            "excluded": "trading actions, sizing, skip rules, live routing, checkpoint/config/oracle/production changes",
            "future_stage_warning": "Stage0 full-sample ER buckets are diagnostic only; Stage1 would require train-only thresholds",
        },
        "baseline_row": baseline_row,
        "summary_rows": summary_rows,
        "bucket_summary": bucket_rows,
        "oos_summary": oos_rows,
        "top_worst_summary": top_worst_rows,
        "q1_cross_stats": q1_rows,
        "rolling12_contribution": rolling_rows,
        "q1_extreme_distribution": distribution_rows,
        "q1_separability": separability_rows,
        "stage0_verdict_rows": verdict_rows,
        "trade_features": trade_features.to_dict(orient="records") if not trade_features.empty else [],
    }
    write_outputs(payload)
    print("overall_stage0_verdict", overall)
    for row in verdict_rows:
        print(
            row["er_window"],
            row["stage0_verdict"],
            "q1_top20",
            row["q1_top20_count"],
            "q1_worst20",
            row["q1_worst20_count"],
            "q1_top_share",
            round(row["q1_top20_pnl_share"] * 100, 2),
            "q1_worst_share",
            round(row["q1_worst20_loss_share"] * 100, 2),
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Efficiency ratio Stage 0 diagnostic.")
    parser.add_argument("--mode", choices=["all", "stage0"], default="all")
    _ = parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
