from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXP0138_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic.py"
spec0138 = importlib.util.spec_from_file_location("exp0138_position_sizing", EXP0138_PATH)
exp0138 = importlib.util.module_from_spec(spec0138)
sys.modules[spec0138.name] = exp0138
assert spec0138.loader is not None
spec0138.loader.exec_module(exp0138)

exp0136 = exp0138.exp0136

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic"

INITIAL = exp0138.INITIAL
COMMISSION = exp0138.COMMISSION
SLIPPAGE = exp0138.SLIPPAGE
FEE10_COMMISSION = exp0138.FEE10_COMMISSION
ATR_WINDOW_BARS = 576
REALIZED_VOL_WINDOW_BARS = 20 * 288
BARS_PER_YEAR = 365.25 * 288
OOS_SPLIT_TIME = pd.Timestamp("2024-06-06 14:25:00")
SIZE_MIN = 0.40
SIZE_MAX = 1.00


@dataclass(frozen=True)
class RiskVariantSpec:
    name: str
    role: str
    sizing_mode: str
    constant_size: float | None = None
    clip_min: float = SIZE_MIN
    clip_max: float = SIZE_MAX
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class RiskSimResult:
    spec: RiskVariantSpec
    df: pd.DataFrame
    signals: np.ndarray
    size_values: np.ndarray
    equity: np.ndarray
    trades: list[dict[str, Any]]
    events: list[dict[str, Any]]
    exposure: np.ndarray
    row: dict[str, Any]


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{value * 100:.2f}%"


def safe_float(value: Any) -> float:
    return exp0136.safe_float(value)


def json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return str(value)
    if isinstance(value, RiskVariantSpec):
        return asdict(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def true_range(df: pd.DataFrame) -> pd.Series:
    high = pd.Series(df["high"], dtype=float)
    low = pd.Series(df["low"], dtype=float)
    close = pd.Series(df["close"], dtype=float)
    prev_close = close.shift(1)
    parts = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return parts.max(axis=1)


def shifted_atr(df: pd.DataFrame, window: int = ATR_WINDOW_BARS) -> pd.Series:
    return true_range(df).rolling(window, min_periods=window).mean().shift(1)


def shifted_realized_vol(df: pd.DataFrame, window: int = REALIZED_VOL_WINDOW_BARS) -> pd.Series:
    returns = pd.Series(df["close"], dtype=float).pct_change()
    return returns.rolling(window, min_periods=window).std().shift(1) * np.sqrt(BARS_PER_YEAR)


def reference_close_for_entry(df: pd.DataFrame) -> pd.Series:
    close = pd.Series(df["close"], dtype=float)
    return close.shift(1)


def direction_from_trade(trade: dict[str, Any]) -> int:
    trade_type = str(trade.get("type", ""))
    if trade_type.startswith("sell"):
        return 1
    if trade_type.startswith("buy_cover"):
        return -1
    return 0


def trade_mae_mfe(df: pd.DataFrame, trade: dict[str, Any]) -> tuple[float, float]:
    entry_step = int(trade["entry_step"])
    exit_step = int(trade["step"])
    start = max(0, min(entry_step, len(df) - 1))
    end = max(start, min(exit_step, len(df) - 1))
    window = df.iloc[start : end + 1]
    entry_price = safe_float(trade.get("entry_price"))
    direction = direction_from_trade(trade)
    if not np.isfinite(entry_price) or entry_price <= 0 or direction == 0 or window.empty:
        return 0.0, 0.0
    highs = pd.Series(window["high"], dtype=float)
    lows = pd.Series(window["low"], dtype=float)
    if direction > 0:
        mfe = float((highs / entry_price - 1.0).max())
        mae = float((lows / entry_price - 1.0).min())
    else:
        mfe = float((entry_price / lows - 1.0).max())
        mae = float((1.0 - highs / entry_price).min())
    return mae, mfe


def label_top_worst_trades(trades: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
    indexed = [(idx, safe_float(trade.get("pnl"))) for idx, trade in enumerate(trades)]
    winners = sorted([(idx, pnl) for idx, pnl in indexed if np.isfinite(pnl) and pnl > 0], key=lambda item: item[1], reverse=True)
    losers = sorted([(idx, pnl) for idx, pnl in indexed if np.isfinite(pnl) and pnl < 0], key=lambda item: item[1])
    return {idx for idx, _ in winners[:20]}, {idx for idx, _ in losers[:20]}


def assign_quintile_labels(values: pd.Series) -> pd.Series:
    labels = pd.Series("missing", index=values.index, dtype=object)
    finite = values.replace([np.inf, -np.inf], np.nan).dropna()
    if finite.empty:
        return labels
    ranks = finite.rank(method="first", pct=True)
    bucket_idx = np.ceil(ranks * 5).astype(int).clip(1, 5)
    labels.loc[finite.index] = [f"Q{idx}" for idx in bucket_idx]
    return labels


def baseline_trade_features(
    df: pd.DataFrame,
    trades: list[dict[str, Any]],
    atr: pd.Series,
    realized_vol: pd.Series,
    split_time: pd.Timestamp,
) -> pd.DataFrame:
    ref_close = reference_close_for_entry(df)
    top20, worst20 = label_top_worst_trades(trades)
    rows: list[dict[str, Any]] = []
    for trade_id, trade in enumerate(trades):
        entry_step = int(trade["entry_step"])
        exit_step = int(trade["step"])
        if entry_step < 0 or entry_step >= len(df):
            continue
        entry_time = pd.Timestamp(df["datetime"].iloc[entry_step])
        exit_time = pd.Timestamp(df["datetime"].iloc[min(max(exit_step, 0), len(df) - 1)])
        pnl = safe_float(trade.get("pnl"))
        notional = safe_float(trade.get("entry_notional"))
        mae, mfe = trade_mae_mfe(df, trade)
        atr_value = safe_float(atr.iloc[entry_step]) if entry_step < len(atr) else float("nan")
        rv_value = safe_float(realized_vol.iloc[entry_step]) if entry_step < len(realized_vol) else float("nan")
        close_ref = safe_float(ref_close.iloc[entry_step]) if entry_step < len(ref_close) else float("nan")
        rows.append(
            {
                "trade_id": trade_id,
                "entry_step": entry_step,
                "exit_step": exit_step,
                "entry_time": str(entry_time),
                "exit_time": str(exit_time),
                "direction": direction_from_trade(trade),
                "pnl": pnl,
                "entry_notional": notional,
                "trade_return": pnl / notional if np.isfinite(notional) and notional > 0 else float("nan"),
                "win": pnl > 0 if np.isfinite(pnl) else False,
                "atr_at_entry": atr_value,
                "entry_ref_close": close_ref,
                "atr_pct_at_entry": atr_value / close_ref
                if np.isfinite(atr_value) and np.isfinite(close_ref) and close_ref > 0
                else float("nan"),
                "realized_vol_20d_at_entry": rv_value,
                "is_oos": entry_time >= split_time,
                "is_top20_winner": trade_id in top20,
                "is_worst20_loser": trade_id in worst20,
                "mae": mae,
                "mfe": mfe,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["atr_bucket"] = assign_quintile_labels(pd.to_numeric(out["atr_pct_at_entry"], errors="coerce"))
    out["realized_vol_bucket"] = assign_quintile_labels(pd.to_numeric(out["realized_vol_20d_at_entry"], errors="coerce"))
    return out


def bucket_summary(trade_features: pd.DataFrame, bucket_col: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    labels = ["Q1", "Q2", "Q3", "Q4", "Q5", "missing"]
    total_top20_pnl = float(trade_features.loc[trade_features["is_top20_winner"], "pnl"].sum()) if not trade_features.empty else 0.0
    for label in labels:
        group = trade_features.loc[trade_features[bucket_col] == label] if not trade_features.empty else pd.DataFrame()
        pnls = pd.to_numeric(group.get("pnl", pd.Series(dtype=float)), errors="coerce")
        returns = pd.to_numeric(group.get("trade_return", pd.Series(dtype=float)), errors="coerce")
        winners = pnls[pnls > 0]
        losers = pnls[pnls < 0]
        top20_pnl = float(group.loc[group.get("is_top20_winner", False), "pnl"].sum()) if not group.empty else 0.0
        gross_loser = float(losers.sum()) if len(losers) else 0.0
        rows.append(
            {
                "bucket": label,
                "trade_count": int(len(group)),
                "oos_trade_count": int(group["is_oos"].sum()) if not group.empty else 0,
                "avg_trade_pnl": float(pnls.mean()) if len(pnls.dropna()) else 0.0,
                "median_trade_pnl": float(pnls.median()) if len(pnls.dropna()) else 0.0,
                "avg_trade_return": float(returns.mean()) if len(returns.dropna()) else 0.0,
                "median_trade_return": float(returns.median()) if len(returns.dropna()) else 0.0,
                "win_rate": float(group["win"].mean()) if not group.empty else 0.0,
                "gross_winner": float(winners.sum()) if len(winners) else 0.0,
                "gross_loser": gross_loser,
                "profit_factor": float(winners.sum() / abs(gross_loser)) if gross_loser < 0 else 0.0,
                "top20_winner_count": int(group["is_top20_winner"].sum()) if not group.empty else 0,
                "worst20_loser_count": int(group["is_worst20_loser"].sum()) if not group.empty else 0,
                "top20_winner_pnl": top20_pnl,
                "top20_winner_pnl_share": top20_pnl / total_top20_pnl if total_top20_pnl > 0 else 0.0,
                "avg_mae": float(pd.to_numeric(group.get("mae", pd.Series(dtype=float)), errors="coerce").mean())
                if not group.empty
                else 0.0,
                "avg_mfe": float(pd.to_numeric(group.get("mfe", pd.Series(dtype=float)), errors="coerce").mean())
                if not group.empty
                else 0.0,
            }
        )
    return rows


def stage0_flags(atr_buckets: list[dict[str, Any]], vol_buckets: list[dict[str, Any]]) -> dict[str, Any]:
    atr_by = {row["bucket"]: row for row in atr_buckets}
    vol_by = {row["bucket"]: row for row in vol_buckets}
    low_mid = [atr_by[key] for key in ("Q1", "Q2", "Q3") if atr_by.get(key, {}).get("trade_count", 0) > 0]
    q5 = atr_by.get("Q5", {})
    q4 = atr_by.get("Q4", {})
    mid_avg_return = float(np.median([row["avg_trade_return"] for row in low_mid])) if low_mid else 0.0
    mid_median_return = float(np.median([row["median_trade_return"] for row in low_mid])) if low_mid else 0.0
    high_atr_quality_worse = bool(
        q5.get("trade_count", 0) > 0
        and q5.get("avg_trade_return", 0.0) < mid_avg_return
        and q5.get("median_trade_return", 0.0) < mid_median_return
    )
    high_atr_worst20_count = int(q5.get("worst20_loser_count", 0))
    high_atr_top20_pnl_share = float(q5.get("top20_winner_pnl_share", 0.0))
    high_atr_topwinner_risk = high_atr_top20_pnl_share > 0.50
    q4_q5_worst20 = int(q4.get("worst20_loser_count", 0)) + high_atr_worst20_count
    high_atr_worst_concentrated = high_atr_worst20_count >= 6 or q4_q5_worst20 >= 12
    atr_stage0_pass = bool((high_atr_quality_worse or high_atr_worst_concentrated) and not high_atr_topwinner_risk)
    high_vol_top20_pnl_share = float(vol_by.get("Q5", {}).get("top20_winner_pnl_share", 0.0))
    high_vol_topwinner_risk = high_vol_top20_pnl_share > 0.50
    return {
        "atr_stage0_pass": atr_stage0_pass,
        "high_atr_quality_worse": high_atr_quality_worse,
        "high_atr_worst_concentrated": high_atr_worst_concentrated,
        "high_atr_worst20_count": high_atr_worst20_count,
        "q4_q5_worst20_count": q4_q5_worst20,
        "high_atr_top20_pnl_share": high_atr_top20_pnl_share,
        "high_atr_topwinner_risk": high_atr_topwinner_risk,
        "vol_targeting_topwinner_risk": "high" if high_vol_topwinner_risk else "normal",
        "high_vol_top20_pnl_share": high_vol_top20_pnl_share,
    }


def median_positive(values: pd.Series) -> float:
    finite = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    finite = finite[finite > 0]
    if finite.empty:
        return 0.0
    return float(finite.median())


def clipped_inverse_size(feature: pd.Series, ref: float, clip_min: float = SIZE_MIN, clip_max: float = SIZE_MAX) -> np.ndarray:
    values = pd.to_numeric(feature, errors="coerce").to_numpy(dtype=float)
    out = np.ones(len(values), dtype=float)
    valid = np.isfinite(values) & (values > 0) & np.isfinite(ref) & (ref > 0)
    out[valid] = ref / values[valid]
    out = np.clip(out, clip_min, clip_max)
    out[~valid] = 1.0
    return out


def constant_size_values(length: int, size: float) -> np.ndarray:
    return np.full(length, float(size), dtype=float)


def make_entry_size_decisions(
    df: pd.DataFrame,
    signals: np.ndarray,
    size_values: np.ndarray,
) -> list[dict[str, Any] | None]:
    decisions: list[dict[str, Any] | None] = [None for _ in range(len(df))]
    position = 0
    pending_action: dict[str, Any] | None = None
    for i in range(len(df)):
        action = pending_action
        pending_action = None
        if action is not None and action["kind"] == "target":
            position = int(action["target"])
        target = exp0138.target_from_signal(int(signals[i]), position)
        decision: dict[str, Any] | None = None
        if target != position:
            exec_idx = min(i + 1, len(size_values) - 1)
            size = 0.0 if target == 0 else safe_float(size_values[exec_idx])
            if target != 0 and (not np.isfinite(size) or size <= 0):
                size = 1.0
            decision = {"kind": "target", "target": target, "size": size, "decision_step": i, "exec_step": exec_idx}
        decisions[i] = decision
        pending_action = decision
    return decisions


def simulate_entry_fixed_sizing(
    spec: RiskVariantSpec,
    df: pd.DataFrame,
    signals: np.ndarray,
    size_values: np.ndarray,
    *,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]], np.ndarray]:
    decisions = make_entry_size_decisions(df, signals, size_values)
    actions = exp0138.shifted_next_open_actions(decisions)
    cash = INITIAL
    shares = 0.0
    cost_basis = 0.0
    entry_price = 0.0
    entry_step = -1
    position = 0
    exposure_fraction = 0.0
    equity: list[float] = []
    exposure: list[float] = []
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for i in range(len(df)):
        open_price = float(df["open"].iloc[i])
        close_price = float(df["close"].iloc[i])
        time = pd.Timestamp(df["datetime"].iloc[i])
        action = actions[i]
        if action is not None and action["kind"] == "target":
            target = int(action["target"])
            if position != 0 and target != position:
                old_position = position
                exit_cash, pnl, exit_price = exp0138.close_position(
                    position=position,
                    cash=cash,
                    shares=shares,
                    cost_basis=cost_basis,
                    entry_price=entry_price,
                    exit_price_base=open_price,
                    commission=commission,
                    slippage=slippage,
                )
                cash = exit_cash
                trade = {
                    "variant": spec.name,
                    "type": "sell" if old_position > 0 else "buy_cover",
                    "step": i,
                    "entry_step": entry_step,
                    "entry_size": exposure_fraction,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "entry_notional": cost_basis,
                    "pnl": pnl,
                }
                trades.append(trade)
                events.append({**trade, "event": "close", "time": str(time)})
                shares = 0.0
                position = 0
                cost_basis = 0.0
                entry_price = 0.0
                exposure_fraction = 0.0
            if target != 0 and position == 0:
                cash, shares, cost_basis, entry_price, exposure_fraction = exp0138.open_position(
                    target=target,
                    fraction=float(action["size"]),
                    cash=cash,
                    open_price=open_price,
                    commission=commission,
                    slippage=slippage,
                )
                if shares != 0:
                    position = target
                    entry_step = i
                    events.append(
                        {
                            "variant": spec.name,
                            "event": "open",
                            "time": str(time),
                            "step": i,
                            "position": position,
                            "size": exposure_fraction,
                            "requested_size": safe_float(action.get("size")),
                            "price": entry_price,
                            "notional": cost_basis,
                        }
                    )
        equity_close = exp0138.current_equity(cash, position, shares, entry_price, close_price)
        equity.append(equity_close if np.isfinite(equity_close) else 0.0)
        exposure.append(exposure_fraction if position != 0 else 0.0)

    if position != 0 and len(df):
        i = len(df) - 1
        close_price = float(df["close"].iloc[i])
        time = pd.Timestamp(df["datetime"].iloc[i])
        old_position = position
        cash, pnl, exit_price = exp0138.close_position(
            position=position,
            cash=cash,
            shares=shares,
            cost_basis=cost_basis,
            entry_price=entry_price,
            exit_price_base=close_price,
            commission=commission,
            slippage=slippage,
        )
        trade = {
            "variant": spec.name,
            "type": "sell_final" if old_position > 0 else "buy_cover_final",
            "step": i,
            "entry_step": entry_step,
            "entry_size": exposure_fraction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_notional": cost_basis,
            "pnl": pnl,
        }
        trades.append(trade)
        events.append({**trade, "event": "close_final", "time": str(time)})
        if equity:
            equity[-1] = cash
            exposure[-1] = 0.0

    return np.asarray(equity, dtype=float), trades, events, np.asarray(exposure, dtype=float)


def slice_by_time_with_size(
    df: pd.DataFrame,
    signals: np.ndarray,
    size_values: np.ndarray,
    split_time: pd.Timestamp,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    idx = int(np.searchsorted(pd.to_datetime(df["datetime"]).to_numpy(), np.datetime64(split_time), side="left"))
    return df.iloc[idx:].reset_index(drop=True), signals[idx:], size_values[idx:]


def skipped_variant_row(spec: RiskVariantSpec, baseline_row: dict[str, Any]) -> dict[str, Any]:
    row = {
        **asdict(spec),
        "skipped": True,
        "full_return": float("nan"),
        "oos_return": float("nan"),
        "max_dd": float("nan"),
        "dd_improve_vs_baseline": float("nan"),
        "rolling12_min": float("nan"),
        "trade_count": 0,
        "oos_trade_count": 0,
        "avg_size": float("nan"),
        "median_size": float("nan"),
        "min_size": float("nan"),
        "time_weighted_exposure": float("nan"),
        "return_dd_ratio": float("nan"),
        "oos_keep_ratio_vs_baseline": float("nan"),
        "top20_winner_damage_vs_baseline": float("nan"),
        "worst20_improve_vs_baseline": float("nan"),
        "fee10_oos_return": float("nan"),
        "fee10_dd": float("nan"),
        "year_wins": 0,
        "year_losses": 0,
    }
    _ = baseline_row
    return row


def row_for_variant(
    spec: RiskVariantSpec,
    df: pd.DataFrame,
    signals: np.ndarray,
    size_values: np.ndarray,
    split_time: pd.Timestamp,
    baseline_row: dict[str, Any] | None = None,
    *,
    commission: float = COMMISSION,
) -> RiskSimResult:
    equity, trades, events, exposure = simulate_entry_fixed_sizing(spec, df, signals, size_values, commission=commission)
    daily = exp0136.daily_equity(df, equity)
    oos_df, oos_signals, oos_sizes = slice_by_time_with_size(df, signals, size_values, split_time)
    oos_equity, oos_trades, _, _ = simulate_entry_fixed_sizing(
        spec,
        oos_df,
        oos_signals,
        oos_sizes,
        commission=commission,
    )
    fee10_equity, _, _, _ = simulate_entry_fixed_sizing(spec, df, signals, size_values, commission=FEE10_COMMISSION)
    fee10_oos_equity, _, _, _ = simulate_entry_fixed_sizing(
        spec,
        oos_df,
        oos_signals,
        oos_sizes,
        commission=FEE10_COMMISSION,
    )
    full_m = exp0136.equity_metrics(equity)
    oos_m = exp0136.equity_metrics(oos_equity)
    fee10_m = exp0136.equity_metrics(fee10_equity)
    fee10_oos_m = exp0136.equity_metrics(fee10_oos_equity)
    returns = exp0136.trade_returns(trades)
    hold_bars = [int(trade["step"]) - int(trade["entry_step"]) for trade in trades]
    contribution = exp0136.top_worst_contribution(trades)
    open_sizes = [safe_float(event.get("size")) for event in events if event.get("event") == "open"]
    open_sizes = [value for value in open_sizes if np.isfinite(value)]
    row = {
        **asdict(spec),
        "skipped": False,
        "full_return": full_m["return"],
        "oos_return": oos_m["return"],
        "max_dd": full_m["dd"],
        "return_dd_ratio": full_m["return"] / abs(full_m["dd"]) if full_m["dd"] else 0.0,
        "rolling12_min": exp0136.rolling_min_return(daily, 365),
        "trade_count": len([trade for trade in trades if trade.get("pnl") is not None]),
        "oos_trade_count": len(oos_trades),
        "oos_trades_per_year": len(oos_trades) / exp0136.duration_years(oos_df) if len(oos_df) else 0.0,
        "trades_per_year": len(trades) / exp0136.duration_years(df),
        "avg_trade_return": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return": float(np.median(returns)) if returns else 0.0,
        "avg_trade_pnl": float(np.mean([safe_float(trade.get("pnl")) for trade in trades])) if trades else 0.0,
        "median_trade_pnl": float(np.median([safe_float(trade.get("pnl")) for trade in trades])) if trades else 0.0,
        "avg_hold_bars": float(np.mean(hold_bars)) if hold_bars else 0.0,
        "median_hold_bars": float(np.median(hold_bars)) if hold_bars else 0.0,
        "avg_size": float(np.mean(open_sizes)) if open_sizes else 0.0,
        "median_size": float(np.median(open_sizes)) if open_sizes else 0.0,
        "min_size": float(np.min(open_sizes)) if open_sizes else 0.0,
        "time_weighted_exposure": float(np.mean(exposure)) if len(exposure) else 0.0,
        "fee10_oos_return": fee10_oos_m["return"],
        "fee10_dd": fee10_m["dd"],
        "fee10_full_return": fee10_m["return"],
        **exp0138.exposure_stats(exposure),
        **contribution,
        **exp0136.year_wlf(daily),
    }
    if baseline_row is None:
        row["dd_improve_vs_baseline"] = 0.0
        row["oos_keep_ratio_vs_baseline"] = 1.0
        row["top20_winner_damage_vs_baseline"] = 0.0
        row["worst20_improve_vs_baseline"] = 0.0
        row["fee10_dd_improve_vs_baseline"] = 0.0
    else:
        row["dd_improve_vs_baseline"] = 1.0 - abs(row["max_dd"]) / abs(float(baseline_row["max_dd"]))
        row["oos_keep_ratio_vs_baseline"] = (
            row["oos_return"] / float(baseline_row["oos_return"]) if float(baseline_row["oos_return"]) else 0.0
        )
        row["top20_winner_damage_vs_baseline"] = (
            (float(baseline_row["top20_winner_pnl"]) - row["top20_winner_pnl"])
            / abs(float(baseline_row["top20_winner_pnl"]))
            if float(baseline_row["top20_winner_pnl"])
            else 0.0
        )
        row["worst20_improve_vs_baseline"] = (
            (abs(float(baseline_row["worst20_loser_pnl"])) - abs(row["worst20_loser_pnl"]))
            / abs(float(baseline_row["worst20_loser_pnl"]))
            if float(baseline_row["worst20_loser_pnl"])
            else 0.0
        )
        row["fee10_dd_improve_vs_baseline"] = 1.0 - abs(row["fee10_dd"]) / abs(float(baseline_row["fee10_dd"]))
    return RiskSimResult(
        spec=spec,
        df=df,
        signals=signals,
        size_values=size_values,
        equity=equity,
        trades=trades,
        events=events,
        exposure=exposure,
        row=row,
    )


def stage_gate_rows(rows: list[dict[str, Any]], stage0: dict[str, Any]) -> list[dict[str, Any]]:
    by_name = {row["name"]: row for row in rows}
    baseline = by_name["baseline_100"]
    v3 = by_name["V3_constant_75"]
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["name"] == "baseline_100":
            continue
        if row.get("skipped"):
            out.append(
                {
                    "variant": row["name"],
                    "skipped": True,
                    "skip_reason": row.get("skip_reason", ""),
                    "stage0_atr_pass": stage0["atr_stage0_pass"],
                    "observe": False,
                    "shadow_candidate": False,
                }
            )
            continue
        dd_ok_observe = row["max_dd"] >= -0.40 or row["dd_improve_vs_baseline"] >= 0.20
        observe = (
            row["role"] == "risk_based_candidate"
            and dd_ok_observe
            and row["oos_return"] >= 4.0
            and row["top20_winner_damage_vs_baseline"] <= 0.35
            and row["rolling12_min"] > baseline["rolling12_min"]
            and row["fee10_oos_return"] > 0.0
            and row["return_dd_ratio"] > v3["return_dd_ratio"]
        )
        shadow = (
            observe
            and row["dd_improve_vs_baseline"] >= 0.20
            and row["oos_keep_ratio_vs_baseline"] >= 0.75
            and row["top20_winner_damage_vs_baseline"] <= 0.20
            and row["worst20_improve_vs_baseline"] > 0.0
            and row["rolling12_min"] > baseline["rolling12_min"]
            and row["fee10_oos_return"] > 0.0
            and row["fee10_dd_improve_vs_baseline"] >= 0.20
            and row["return_dd_ratio"] > baseline["return_dd_ratio"]
            and row["return_dd_ratio"] > v3["return_dd_ratio"]
        )
        out.append(
            {
                "variant": row["name"],
                "skipped": False,
                "stage0_atr_pass": stage0["atr_stage0_pass"],
                "vol_targeting_topwinner_risk": stage0["vol_targeting_topwinner_risk"],
                "dd_ok_observe": dd_ok_observe,
                "dd_improve_ge20": row["dd_improve_vs_baseline"] >= 0.20,
                "oos_ge400": row["oos_return"] >= 4.0,
                "oos_keep_75": row["oos_keep_ratio_vs_baseline"] >= 0.75,
                "top20_damage_le35": row["top20_winner_damage_vs_baseline"] <= 0.35,
                "top20_damage_le20": row["top20_winner_damage_vs_baseline"] <= 0.20,
                "worst20_improved": row["worst20_improve_vs_baseline"] > 0.0,
                "rolling12_improved": row["rolling12_min"] > baseline["rolling12_min"],
                "fee10_positive": row["fee10_oos_return"] > 0.0,
                "return_dd_beats_v3": row["return_dd_ratio"] > v3["return_dd_ratio"],
                "return_dd_beats_baseline": row["return_dd_ratio"] > baseline["return_dd_ratio"],
                "observe": observe,
                "shadow_candidate": shadow,
            }
        )
    return out


def verdict_from_gates(gates: list[dict[str, Any]]) -> str:
    if any(row.get("shadow_candidate") for row in gates):
        return "SHADOW_CANDIDATE"
    if any(row.get("observe") for row in gates):
        return "OBSERVE"
    return "REJECT"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in rows for key in row}) if rows else ["empty"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(payload: dict[str, Any]) -> None:
    OUT.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), payload["variants"])
    write_csv(OUT.with_name(OUT.name + "_stage0_atr_buckets.csv"), payload["stage0_atr_buckets"])
    write_csv(OUT.with_name(OUT.name + "_stage0_realized_vol_buckets.csv"), payload["stage0_realized_vol_buckets"])
    write_csv(OUT.with_name(OUT.name + "_stage0_trades.csv"), payload["stage0_trades"])
    write_csv(OUT.with_name(OUT.name + "_stage_gates.csv"), payload["stage_gates"])
    write_csv(OUT.with_name(OUT.name + "_events.csv"), payload["events"])
    write_csv(OUT.with_name(OUT.name + "_trades.csv"), payload["trades"])
    write_report(payload)


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# exp_0139 v2.2 risk-based position sizing diagnostic",
        "",
        "- research-only",
        "- risk-based sizing diagnostic only",
        "- no live/checkpoint/config/oracle/production strategy change",
        "- first pass uses entry-fixed sizing only; no intratrade dynamic rebalance",
        f"- verdict: `{payload['verdict']}`",
        "",
        "## Stage 0 ATR Entry Buckets",
        "",
        "| bucket | trades | OOS trades | avg pnl | median pnl | avg ret | median ret | win | PF | top20 | worst20 | avg MAE | avg MFE | top20 pnl share |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["stage0_atr_buckets"]:
        lines.append(
            f"| {row['bucket']} | {row['trade_count']} | {row['oos_trade_count']} | "
            f"{row['avg_trade_pnl']:.2f} | {row['median_trade_pnl']:.2f} | "
            f"{pct(row['avg_trade_return'])} | {pct(row['median_trade_return'])} | "
            f"{pct(row['win_rate'])} | {row['profit_factor']:.2f} | "
            f"{row['top20_winner_count']} | {row['worst20_loser_count']} | "
            f"{pct(row['avg_mae'])} | {pct(row['avg_mfe'])} | {pct(row['top20_winner_pnl_share'])} |"
        )
    lines.extend(
        [
            "",
            "## Stage 0 Realized Vol Buckets",
            "",
            "| bucket | trades | OOS trades | avg ret | median ret | top20 | worst20 | top20 pnl share |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["stage0_realized_vol_buckets"]:
        lines.append(
            f"| {row['bucket']} | {row['trade_count']} | {row['oos_trade_count']} | "
            f"{pct(row['avg_trade_return'])} | {pct(row['median_trade_return'])} | "
            f"{row['top20_winner_count']} | {row['worst20_loser_count']} | "
            f"{pct(row['top20_winner_pnl_share'])} |"
        )
    flags = payload["stage0_flags"]
    lines.extend(
        [
            "",
            "## Stage 0 Flags",
            "",
            f"- ATR stage0 pass: `{flags['atr_stage0_pass']}`",
            f"- high ATR quality worse: `{flags['high_atr_quality_worse']}`",
            f"- high ATR worst concentrated: `{flags['high_atr_worst_concentrated']}`",
            f"- high ATR top20 pnl share: `{pct(flags['high_atr_top20_pnl_share'])}`",
            f"- vol targeting top-winner risk: `{flags['vol_targeting_topwinner_risk']}` "
            f"({pct(flags['high_vol_top20_pnl_share'])})",
            "",
            "## Variants",
            "",
            "| variant | mode | skipped | avg size | min size | OOS | DD | DD improve | roll12 | top20 damage | worst20 improve | return/DD | fee10 OOS | fee10 DD |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["variants"]:
        lines.append(
            f"| {row['name']} | {row['sizing_mode']} | {row.get('skipped', False)} | "
            f"{pct(row.get('avg_size'))} | {pct(row.get('min_size'))} | "
            f"{pct(row.get('oos_return'))} | {pct(row.get('max_dd'))} | "
            f"{pct(row.get('dd_improve_vs_baseline'))} | {pct(row.get('rolling12_min'))} | "
            f"{pct(row.get('top20_winner_damage_vs_baseline'))} | "
            f"{pct(row.get('worst20_improve_vs_baseline'))} | "
            f"{safe_float(row.get('return_dd_ratio')):.2f} | "
            f"{pct(row.get('fee10_oos_return'))} | {pct(row.get('fee10_dd'))} |"
        )
    lines.extend(
        [
            "",
            "## Stage Gates",
            "",
            "| variant | skipped | DD ok | OOS >= 400 | top20 <=35 | roll12 | fee10 | return/DD > V3 | observe | shadow |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in payload["stage_gates"]:
        lines.append(
            f"| {row['variant']} | {row.get('skipped', False)} | {row.get('dd_ok_observe', False)} | "
            f"{row.get('oos_ge400', False)} | {row.get('top20_damage_le35', False)} | "
            f"{row.get('rolling12_improved', False)} | {row.get('fee10_positive', False)} | "
            f"{row.get('return_dd_beats_v3', False)} | {row.get('observe', False)} | "
            f"{row.get('shadow_candidate', False)} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- V0 and V3 are recomputed constant-sizing benchmarks in the same script and should be treated as linear controls.",
            "- V2 ATR risk parity is only run when Stage 0 shows high-ATR entry quality is genuinely worse without high top-winner overlap.",
            "- Top20 and worst20 labels are attribution-only and are never used by sizing logic.",
            "- No result here authorizes live position sizing, exchange routing, checkpoint promotion, or production strategy changes.",
            "",
            "## Evidence",
            "",
            f"- variants: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- ATR buckets: `{OUT.with_name(OUT.name + '_stage0_atr_buckets.csv').relative_to(PROJECT_ROOT)}`",
            f"- realized vol buckets: `{OUT.with_name(OUT.name + '_stage0_realized_vol_buckets.csv').relative_to(PROJECT_ROOT)}`",
            f"- trade features: `{OUT.with_name(OUT.name + '_stage0_trades.csv').relative_to(PROJECT_ROOT)}`",
            f"- stage gates: `{OUT.with_name(OUT.name + '_stage_gates.csv').relative_to(PROJECT_ROOT)}`",
            f"- events: `{OUT.with_name(OUT.name + '_events.csv').relative_to(PROJECT_ROOT)}`",
            f"- trades: `{OUT.with_name(OUT.name + '_trades.csv').relative_to(PROJECT_ROOT)}`",
            f"- json: `{OUT.with_suffix('.json').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_all() -> dict[str, Any]:
    df, base_signals, scope = exp0136.exp0110.load_base()
    df = exp0136.clean_ohlcv(df)
    split_time = OOS_SPLIT_TIME
    signals = base_signals.astype(int)
    atr = shifted_atr(df)
    ref_close = reference_close_for_entry(df)
    atr_pct = atr / ref_close
    realized_vol = shifted_realized_vol(df)

    baseline_spec = RiskVariantSpec("baseline_100", "baseline_reference", "constant", constant_size=1.0)
    baseline_sizes = constant_size_values(len(df), 1.0)
    baseline_result = row_for_variant(baseline_spec, df, signals, baseline_sizes, split_time, baseline_row=None)
    baseline_trades = baseline_result.trades
    trade_features = baseline_trade_features(df, baseline_trades, atr, realized_vol, split_time)
    atr_buckets = bucket_summary(trade_features, "atr_bucket")
    vol_buckets = bucket_summary(trade_features, "realized_vol_bucket")
    flags = stage0_flags(atr_buckets, vol_buckets)

    is_entries = trade_features.loc[pd.to_datetime(trade_features["entry_time"]) < split_time]
    vol_ref = median_positive(is_entries["realized_vol_20d_at_entry"])
    atr_ref = median_positive(is_entries["atr_pct_at_entry"])
    vol_sizes = clipped_inverse_size(realized_vol, vol_ref)
    atr_sizes = clipped_inverse_size(atr_pct, atr_ref)

    results: list[RiskSimResult] = [baseline_result]
    baseline_row = baseline_result.row
    variant_specs_and_sizes: list[tuple[RiskVariantSpec, np.ndarray | None]] = [
        (RiskVariantSpec("V0_constant_50", "linear_constant_control", "constant", constant_size=0.5), constant_size_values(len(df), 0.5)),
        (RiskVariantSpec("V3_constant_75", "linear_constant_control", "constant", constant_size=0.75), constant_size_values(len(df), 0.75)),
        (
            RiskVariantSpec("V1_vol_target_20d_clip_0p4_1p0", "risk_based_candidate", "vol_target_20d"),
            vol_sizes,
        ),
    ]
    if flags["atr_stage0_pass"]:
        variant_specs_and_sizes.append(
            (
                RiskVariantSpec("V2_atr_risk_parity_clip_0p4_1p0", "risk_based_candidate", "atr_risk_parity"),
                atr_sizes,
            )
        )
    else:
        variant_specs_and_sizes.append(
            (
                RiskVariantSpec(
                    "V2_atr_risk_parity_clip_0p4_1p0",
                    "risk_based_candidate",
                    "atr_risk_parity",
                    skipped=True,
                    skip_reason="skipped_by_stage0",
                ),
                None,
            )
        )

    skipped_rows: list[dict[str, Any]] = []
    for spec, sizes in variant_specs_and_sizes:
        print(f"=== {spec.name} ===", flush=True)
        if sizes is None:
            skipped_rows.append(skipped_variant_row(spec, baseline_row))
            continue
        results.append(row_for_variant(spec, df, signals, sizes, split_time, baseline_row=baseline_row))

    rows = [result.row for result in results] + skipped_rows
    gates = stage_gate_rows(rows, flags)
    verdict = verdict_from_gates(gates)
    payload = {
        "experiment_id": "exp_0139_v22_risk_based_position_sizing_diagnostic",
        "verdict": verdict,
        "scope": {
            "research_only": True,
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "data": str(exp0136.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
            "split_time": str(split_time),
            "scope_split_time": str(pd.Timestamp(df["datetime"].iloc[int(scope["split_idx"])])),
            "atr_window_bars": ATR_WINDOW_BARS,
            "realized_vol_window_bars": REALIZED_VOL_WINDOW_BARS,
            "vol_ref_from_is_entries": vol_ref,
            "atr_ref_from_is_entries": atr_ref,
            "clip": [SIZE_MIN, SIZE_MAX],
            "execution": "completed-bar baseline signal, size fixed at next entry open, baseline exits unchanged",
            "excluded": "live routing, checkpoint/config/oracle/production changes, fixed-R profit taking, intratrade rebalance",
        },
        "stage0_flags": flags,
        "stage0_atr_buckets": atr_buckets,
        "stage0_realized_vol_buckets": vol_buckets,
        "stage0_trades": trade_features.to_dict(orient="records") if not trade_features.empty else [],
        "variants": rows,
        "stage_gates": gates,
        "events": [event for result in results for event in result.events],
        "trades": [trade for result in results for trade in result.trades],
    }
    write_outputs(payload)
    print("verdict", verdict)
    print("stage0 ATR pass", flags["atr_stage0_pass"], "vol risk", flags["vol_targeting_topwinner_risk"])
    for row in rows:
        print(
            row["name"],
            "skipped",
            row.get("skipped", False),
            "oos",
            round(safe_float(row.get("oos_return")) * 100, 2),
            "dd",
            round(safe_float(row.get("max_dd")) * 100, 2),
            "avg_size",
            round(safe_float(row.get("avg_size")) * 100, 2),
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Risk-based position sizing diagnostic.")
    parser.add_argument("--mode", choices=["all"], default="all")
    parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
