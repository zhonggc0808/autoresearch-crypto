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

EXP0110_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0110_v22_moirai_reversal_confirmation_delay.py"
spec0110 = importlib.util.spec_from_file_location("exp0110_helper", EXP0110_PATH)
exp0110 = importlib.util.module_from_spec(spec0110)
sys.modules["exp0110_helper"] = exp0110
assert spec0110.loader is not None
spec0110.loader.exec_module(exp0110)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic"

INITIAL = exp0110.helper0108.INITIAL_CAPITAL
COMMISSION = exp0110.helper0108.COMMISSION
SLIPPAGE = exp0110.helper0108.SLIPPAGE
FEE10_COMMISSION = 0.001
TRADING_DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class SleeveSpec:
    name: str
    timeframe: str
    entry_lookback: int
    min_hold_bars: int
    role: str
    baseline: bool = False


@dataclass
class SleeveResult:
    spec: SleeveSpec
    df: pd.DataFrame
    signals: np.ndarray
    equity: np.ndarray
    trades: list[dict[str, Any]]
    daily_equity: pd.Series
    row: dict[str, Any]


SLEEVES = [
    SleeveSpec("5m_v22_moirai_baseline", "5min", 375, 432, "primary_reference", baseline=True),
    SleeveSpec("5m_core_donchian", "5min", 375, 432, "same_cadence_core_control"),
    SleeveSpec("15m_core_donchian", "15min", 125, 144, "time_equivalent_mid_frequency"),
    SleeveSpec("1h_core_donchian", "1h", 32, 36, "time_equivalent_low_frequency"),
    SleeveSpec("1h_core_donchian_fast", "1h", 32, 18, "non_proportional_low_frequency_fast_hold"),
]

CORRELATION_PAIRS = [
    ("5m_v22_moirai_baseline", "15m_core_donchian"),
    ("5m_v22_moirai_baseline", "1h_core_donchian"),
    ("5m_v22_moirai_baseline", "1h_core_donchian_fast"),
    ("5m_core_donchian", "15m_core_donchian"),
    ("15m_core_donchian", "1h_core_donchian"),
]

COMBO_SPECS = [
    ("combo_70_20_10_v22_15m_1h", {"5m_v22_moirai_baseline": 0.70, "15m_core_donchian": 0.20, "1h_core_donchian": 0.10}),
    ("combo_60_20_20_v22_15m_1h", {"5m_v22_moirai_baseline": 0.60, "15m_core_donchian": 0.20, "1h_core_donchian": 0.20}),
    ("combo_80_0_20_v22_1h_fast", {"5m_v22_moirai_baseline": 0.80, "1h_core_donchian_fast": 0.20}),
]


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


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns:
        if "timestamp" not in out.columns:
            raise ValueError("OHLCV data must contain datetime or timestamp")
        out["datetime"] = pd.to_datetime(out["timestamp"], errors="coerce")
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates(subset="datetime")
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    return out.reset_index(drop=True)


def resample_ohlcv_completed(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    src = clean_ohlcv(df)
    if freq in {"5m", "5min"}:
        return src.copy()
    out = (
        src.set_index("datetime")
        .resample(freq, label="right", closed="right")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return out.reset_index(drop=True)


def core_donchian_signals(df: pd.DataFrame, entry_lookback: int, min_hold_bars: int) -> np.ndarray:
    high = pd.Series(df["high"], dtype=float)
    low = pd.Series(df["low"], dtype=float)
    close = pd.Series(df["close"], dtype=float)
    rolling_high = high.rolling(entry_lookback, min_periods=entry_lookback).max().shift(1)
    rolling_low = low.rolling(entry_lookback, min_periods=entry_lookback).min().shift(1)
    out = np.ones(len(df), dtype=int)
    position = 0
    entry_bar = -1
    for i in range(len(df)):
        price = float(close.iloc[i])
        upper = safe_float(rolling_high.iloc[i])
        lower = safe_float(rolling_low.iloc[i])
        long_break = np.isfinite(upper) and price > upper
        short_break = np.isfinite(lower) and price < lower
        if position == 0:
            if long_break:
                position = 1
                entry_bar = i
            elif short_break:
                position = -1
                entry_bar = i
        else:
            can_flip = i - entry_bar >= min_hold_bars
            if can_flip and position > 0 and short_break:
                position = -1
                entry_bar = i
            elif can_flip and position < 0 and long_break:
                position = 1
                entry_bar = i
        out[i] = 2 if position > 0 else 3 if position < 0 else 1
    return out


def shifted_next_open_signals(signals: np.ndarray) -> np.ndarray:
    shifted = np.ones(len(signals), dtype=int)
    if len(signals) > 1:
        shifted[1:] = np.asarray(signals, dtype=int)[:-1]
    return shifted


def evaluate_next_open(
    signals: np.ndarray,
    df: pd.DataFrame,
    *,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    evaluator = exp0110.helper0108.StrategyEvaluator(
        initial_capital=INITIAL,
        commission=commission,
        slippage=slippage,
        execution_price="signal_bar_open",
    )
    equity, trades = evaluator.simulate(
        shifted_next_open_signals(signals),
        df["close"].to_numpy(dtype=float),
        df=df,
    )
    return equity, [trade for trade in trades if trade.get("pnl") is not None]


def equity_metrics(equity: np.ndarray) -> dict[str, float]:
    if len(equity) == 0:
        return {"return": 0.0, "dd": 0.0}
    peaks = np.maximum.accumulate(equity)
    dd = equity / np.where(peaks == 0, np.nan, peaks) - 1.0
    return {
        "return": float(equity[-1] / equity[0] - 1.0),
        "dd": float(np.nanmin(dd)),
    }


def daily_equity(df: pd.DataFrame, equity: np.ndarray) -> pd.Series:
    series = pd.Series(equity, index=pd.to_datetime(df["datetime"]))
    out = series.resample("1D").last().ffill().dropna()
    out.name = "equity"
    return out


def daily_returns(daily: pd.Series) -> pd.Series:
    return daily.pct_change().replace([np.inf, -np.inf], np.nan).dropna()


def period_return(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    if series.empty:
        return float("nan")
    window = series.loc[(series.index >= start) & (series.index <= end)]
    if len(window) < 2:
        return float("nan")
    return float(window.iloc[-1] / window.iloc[0] - 1.0)


def rolling_min_return(daily: pd.Series, window_days: int) -> float:
    if len(daily) <= window_days:
        return 0.0
    vals = daily.to_numpy(dtype=float)
    returns = vals[window_days:] / vals[:-window_days] - 1.0
    return float(np.nanmin(returns)) if len(returns) else 0.0


def rolling_worst_period(daily: pd.Series, window_days: int) -> dict[str, Any]:
    if len(daily) <= window_days:
        return {"start": daily.index[0], "end": daily.index[-1], "return": float("nan")}
    vals = daily.to_numpy(dtype=float)
    returns = vals[window_days:] / vals[:-window_days] - 1.0
    idx = int(np.nanargmin(returns))
    return {"start": daily.index[idx], "end": daily.index[idx + window_days], "return": float(returns[idx])}


def max_drawdown_period(daily: pd.Series) -> dict[str, Any]:
    vals = daily.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(vals)
    dd = vals / np.where(peaks == 0, np.nan, peaks) - 1.0
    trough = int(np.nanargmin(dd))
    peak = int(np.argmax(vals[: trough + 1]))
    return {
        "start": daily.index[peak],
        "end": daily.index[trough],
        "dd": float(dd[trough]),
        "return": float(vals[trough] / vals[peak] - 1.0) if vals[peak] else float("nan"),
    }


def overlap_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    start = max(pd.Timestamp(a["start"]), pd.Timestamp(b["start"]))
    end = min(pd.Timestamp(a["end"]), pd.Timestamp(b["end"]))
    overlap = max(0.0, (end - start).total_seconds())
    a_len = max(1.0, (pd.Timestamp(a["end"]) - pd.Timestamp(a["start"])).total_seconds())
    b_len = max(1.0, (pd.Timestamp(b["end"]) - pd.Timestamp(b["start"])).total_seconds())
    return float(overlap / min(a_len, b_len))


def duration_years(df: pd.DataFrame) -> float:
    start = pd.Timestamp(df["datetime"].iloc[0])
    end = pd.Timestamp(df["datetime"].iloc[-1])
    return max((end - start).total_seconds() / (86400.0 * TRADING_DAYS_PER_YEAR), 1e-9)


def trade_returns(trades: list[dict[str, Any]]) -> list[float]:
    vals = []
    for trade in trades:
        notional = safe_float(trade.get("entry_notional"))
        pnl = safe_float(trade.get("pnl"))
        if np.isfinite(notional) and notional > 0 and np.isfinite(pnl):
            vals.append(pnl / notional)
    return vals


def max_idle_days(df: pd.DataFrame, trades: list[dict[str, Any]]) -> float:
    times = pd.to_datetime(df["datetime"])
    if len(times) == 0:
        return 0.0
    if not trades:
        return float((times.iloc[-1] - times.iloc[0]).total_seconds() / 86400.0)
    ordered = sorted(trades, key=lambda trade: int(trade["entry_step"]))
    gaps: list[float] = []
    first_entry = min(max(int(ordered[0]["entry_step"]), 0), len(times) - 1)
    gaps.append((times.iloc[first_entry] - times.iloc[0]).total_seconds() / 86400.0)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        prev_exit = min(max(int(prev["step"]), 0), len(times) - 1)
        next_entry = min(max(int(nxt["entry_step"]), 0), len(times) - 1)
        gaps.append((times.iloc[next_entry] - times.iloc[prev_exit]).total_seconds() / 86400.0)
    last_exit = min(max(int(ordered[-1]["step"]), 0), len(times) - 1)
    gaps.append((times.iloc[-1] - times.iloc[last_exit]).total_seconds() / 86400.0)
    return float(max(gaps)) if gaps else 0.0


def top_worst_contribution(trades: list[dict[str, Any]]) -> dict[str, float]:
    pnls = [safe_float(trade.get("pnl")) for trade in trades]
    pnls = [p for p in pnls if np.isfinite(p)]
    total = sum(pnls)
    winners = sorted([p for p in pnls if p > 0], reverse=True)
    losers = sorted([p for p in pnls if p < 0])
    top20 = winners[:20]
    worst20 = losers[:20]
    gross_winners = sum(winners)
    gross_losers = abs(sum(losers))
    return {
        "top20_winner_pnl": float(sum(top20)),
        "top20_winner_contribution_to_total_pnl": float(sum(top20) / total) if total else 0.0,
        "top20_winner_contribution_to_gross_winners": float(sum(top20) / gross_winners) if gross_winners else 0.0,
        "worst20_loser_pnl": float(sum(worst20)),
        "worst20_loser_contribution_to_total_pnl": float(sum(worst20) / total) if total else 0.0,
        "worst20_loser_contribution_to_gross_losers": float(abs(sum(worst20)) / gross_losers) if gross_losers else 0.0,
    }


def year_wlf(daily: pd.Series) -> dict[str, Any]:
    rows = []
    for _, group in daily.groupby(daily.index.year):
        if len(group) < 2:
            continue
        rows.append(float(group.iloc[-1] / group.iloc[0] - 1.0))
    return {
        "year_wins": sum(1 for value in rows if value > 1e-12),
        "year_losses": sum(1 for value in rows if value < -1e-12),
        "year_flat": sum(1 for value in rows if abs(value) <= 1e-12),
        "year_min_return": min(rows) if rows else 0.0,
    }


def slice_by_time(df: pd.DataFrame, signals: np.ndarray, split_time: pd.Timestamp) -> tuple[pd.DataFrame, np.ndarray]:
    idx = int(np.searchsorted(pd.to_datetime(df["datetime"]).to_numpy(), np.datetime64(split_time), side="left"))
    return df.iloc[idx:].reset_index(drop=True), signals[idx:]


def evaluate_sleeve(spec: SleeveSpec, df5: pd.DataFrame, base_signals: np.ndarray, split_time: pd.Timestamp) -> SleeveResult:
    df = resample_ohlcv_completed(df5, spec.timeframe)
    signals = base_signals.astype(int).copy() if spec.baseline else core_donchian_signals(df, spec.entry_lookback, spec.min_hold_bars)
    equity, trades = evaluate_next_open(signals, df)
    daily = daily_equity(df, equity)
    oos_df, oos_signals = slice_by_time(df, signals, split_time)
    oos_equity, oos_trades = evaluate_next_open(oos_signals, oos_df)
    fee10_equity, _ = evaluate_next_open(signals, df, commission=FEE10_COMMISSION)
    fee10_oos_equity, _ = evaluate_next_open(oos_signals, oos_df, commission=FEE10_COMMISSION)
    full_m = equity_metrics(equity)
    oos_m = equity_metrics(oos_equity)
    fee10_m = equity_metrics(fee10_equity)
    fee10_oos_m = equity_metrics(fee10_oos_equity)
    returns = trade_returns(trades)
    hold_bars = [int(trade["step"]) - int(trade["entry_step"]) for trade in trades]
    row = {
        **asdict(spec),
        "full_return": full_m["return"],
        "oos_return": oos_m["return"],
        "max_dd": full_m["dd"],
        "rolling12_min": rolling_min_return(daily, 365),
        "trade_count": len(trades),
        "trades_per_year": len(trades) / duration_years(df),
        "oos_trade_count": len(oos_trades),
        "oos_trades_per_year": len(oos_trades) / duration_years(oos_df) if len(oos_df) else 0.0,
        "avg_trade_return": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return": float(np.median(returns)) if returns else 0.0,
        "avg_trade_pnl": float(np.mean([float(trade["pnl"]) for trade in trades])) if trades else 0.0,
        "median_trade_pnl": float(np.median([float(trade["pnl"]) for trade in trades])) if trades else 0.0,
        "avg_hold_bars": float(np.mean(hold_bars)) if hold_bars else 0.0,
        "median_hold_bars": float(np.median(hold_bars)) if hold_bars else 0.0,
        "max_idle_days": max_idle_days(df, trades),
        "fee10_full_return": fee10_m["return"],
        "fee10_full_dd": fee10_m["dd"],
        "fee10_oos_return": fee10_oos_m["return"],
        **top_worst_contribution(trades),
        **year_wlf(daily),
    }
    return SleeveResult(spec=spec, df=df, signals=signals, equity=equity, trades=trades, daily_equity=daily, row=row)


def aligned_daily_frame(results: dict[str, SleeveResult]) -> pd.DataFrame:
    frame = pd.concat({name: result.daily_equity for name, result in results.items()}, axis=1).ffill().dropna()
    return frame


def return_corr(a: pd.Series, b: pd.Series) -> float:
    joined = pd.concat([a, b], axis=1).dropna()
    if len(joined) < 3:
        return 0.0
    return float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))


def pair_correlations(daily_frame: pd.DataFrame) -> list[dict[str, Any]]:
    returns = daily_frame.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    monthly = daily_frame.resample("ME").last().pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    rows: list[dict[str, Any]] = []
    for left, right in CORRELATION_PAIRS:
        left_ret = returns[left]
        right_ret = returns[right]
        roll90 = left_ret.rolling(90).corr(right_ret).dropna()
        roll180 = left_ret.rolling(180).corr(right_ret).dropna()
        rows.append(
            {
                "left": left,
                "right": right,
                "daily_return_corr": return_corr(left_ret, right_ret),
                "rolling90_corr_mean": float(roll90.mean()) if len(roll90) else 0.0,
                "rolling90_corr_max": float(roll90.max()) if len(roll90) else 0.0,
                "rolling180_corr_mean": float(roll180.mean()) if len(roll180) else 0.0,
                "rolling180_corr_max": float(roll180.max()) if len(roll180) else 0.0,
                "monthly_return_corr": return_corr(monthly[left], monthly[right]) if left in monthly and right in monthly else 0.0,
            }
        )
    return rows


def dd_overlap_rows(results: dict[str, SleeveResult], daily_frame: pd.DataFrame) -> list[dict[str, Any]]:
    dd_periods = {name: max_drawdown_period(daily_frame[name]) for name in daily_frame.columns}
    worst30 = {name: rolling_worst_period(daily_frame[name], 30) for name in daily_frame.columns}
    worst90 = {name: rolling_worst_period(daily_frame[name], 90) for name in daily_frame.columns}
    rows: list[dict[str, Any]] = []
    anchors = ["5m_v22_moirai_baseline", "15m_core_donchian", "1h_core_donchian", "1h_core_donchian_fast"]
    for anchor in anchors:
        period = dd_periods[anchor]
        for name in daily_frame.columns:
            rows.append(
                {
                    "anchor": anchor,
                    "other": name,
                    "period": "max_dd",
                    "start": str(period["start"]),
                    "end": str(period["end"]),
                    "anchor_dd": period["dd"],
                    "other_return": period_return(daily_frame[name], period["start"], period["end"]),
                    "dd_overlap_ratio": overlap_ratio(period, dd_periods[name]),
                    "worst30_overlap_ratio": overlap_ratio(worst30[anchor], worst30[name]),
                    "worst90_overlap_ratio": overlap_ratio(worst90[anchor], worst90[name]),
                }
            )
    return rows


def top_winner_rows(results: dict[str, SleeveResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, result in results.items():
        top = sorted(
            [trade for trade in result.trades if safe_float(trade.get("pnl")) > 0.0],
            key=lambda trade: safe_float(trade.get("pnl")),
            reverse=True,
        )[:20]
        for rank, trade in enumerate(top, 1):
            entry = int(trade["entry_step"])
            exit_ = int(trade["step"])
            entry_time = pd.Timestamp(result.df["datetime"].iloc[min(entry, len(result.df) - 1)])
            exit_time = pd.Timestamp(result.df["datetime"].iloc[min(exit_, len(result.df) - 1)])
            rows.append(
                {
                    "sleeve": name,
                    "rank": rank,
                    "entry_bar": entry,
                    "exit_bar": exit_,
                    "entry_time": str(entry_time),
                    "exit_time": str(exit_time),
                    "month": str(entry_time.to_period("M")),
                    "quarter": str(entry_time.to_period("Q")),
                    "pnl": safe_float(trade.get("pnl")),
                    "entry_notional": safe_float(trade.get("entry_notional")),
                    "return": safe_float(trade.get("pnl")) / safe_float(trade.get("entry_notional"))
                    if safe_float(trade.get("entry_notional")) > 0
                    else float("nan"),
                }
            )
    return rows


def windows_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return pd.Timestamp(left["entry_time"]) <= pd.Timestamp(right["exit_time"]) and pd.Timestamp(
        right["entry_time"]
    ) <= pd.Timestamp(left["exit_time"])


def top_overlap_rows(top_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sleeve: dict[str, list[dict[str, Any]]] = {}
    for row in top_rows:
        by_sleeve.setdefault(str(row["sleeve"]), []).append(row)
    rows: list[dict[str, Any]] = []
    for left, right in CORRELATION_PAIRS:
        left_rows = by_sleeve.get(left, [])
        right_rows = by_sleeve.get(right, [])
        left_overlap = [row for row in left_rows if any(windows_overlap(row, other) for other in right_rows)]
        right_overlap = [row for row in right_rows if any(windows_overlap(row, other) for other in left_rows)]
        left_total = sum(safe_float(row["pnl"]) for row in left_rows) or 1.0
        right_total = sum(safe_float(row["pnl"]) for row in right_rows) or 1.0
        rows.append(
            {
                "left": left,
                "right": right,
                "top20_overlap_count_left": len(left_overlap),
                "top20_overlap_count_right": len(right_overlap),
                "top20_overlap_by_month": len({row["month"] for row in left_rows} & {row["month"] for row in right_rows}),
                "top20_overlap_by_quarter": len(
                    {row["quarter"] for row in left_rows} & {row["quarter"] for row in right_rows}
                ),
                "left_contribution_overlap": sum(safe_float(row["pnl"]) for row in left_overlap) / left_total,
                "right_contribution_overlap": sum(safe_float(row["pnl"]) for row in right_overlap) / right_total,
            }
        )
    return rows


def combo_daily_equity(daily_frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    returns = daily_frame[list(weights)].pct_change().fillna(0.0)
    combo_returns = sum(float(weight) * returns[name] for name, weight in weights.items())
    equity = INITIAL * (1.0 + combo_returns).cumprod()
    equity.name = "combo"
    return equity


def combo_rows(daily_frame: pd.DataFrame, base_oos_return: float, base_dd: float, split_time: pd.Timestamp) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, weights in COMBO_SPECS:
        daily = combo_daily_equity(daily_frame, weights)
        full = equity_metrics(daily.to_numpy(dtype=float))
        oos = daily.loc[daily.index >= split_time.floor("D")]
        oos_return = float(oos.iloc[-1] / oos.iloc[0] - 1.0) if len(oos) > 1 else 0.0
        row = {
            "combo": name,
            "weights": json.dumps(weights, sort_keys=True),
            "full_return": full["return"],
            "oos_return": oos_return,
            "max_dd": full["dd"],
            "dd_improve_vs_v22": (abs(base_dd) - abs(full["dd"])) / abs(base_dd) if base_dd else 0.0,
            "rolling12_min": rolling_min_return(daily, 365),
            "oos_not_worse_than_v22_by_20pct": oos_return >= base_oos_return * 0.80,
        }
        row["passes_combo_gate"] = row["dd_improve_vs_v22"] >= 0.15 and row["oos_not_worse_than_v22_by_20pct"]
        rows.append(row)
    return rows


def stage_gate_rows(
    sleeve_rows: list[dict[str, Any]],
    corr_rows: list[dict[str, Any]],
    dd_rows: list[dict[str, Any]],
    top_rows: list[dict[str, Any]],
    combo_summary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    corr_by = {(row["left"], row["right"]): row for row in corr_rows}
    dd_lookup = {(row["anchor"], row["other"]): row for row in dd_rows if row["period"] == "max_dd"}
    top_by = {(row["left"], row["right"]): row for row in top_rows}
    sleeve_by = {row["name"]: row for row in sleeve_rows}
    any_combo_pass = any(row["passes_combo_gate"] for row in combo_summary)
    rows: list[dict[str, Any]] = []
    for name in ("15m_core_donchian", "1h_core_donchian", "1h_core_donchian_fast"):
        sleeve = sleeve_by[name]
        corr = corr_by.get(("5m_v22_moirai_baseline", name), {})
        dd = dd_lookup.get(("5m_v22_moirai_baseline", name), {})
        top = top_by.get(("5m_v22_moirai_baseline", name), {})
        oos_positive = sleeve["oos_return"] > 0.0
        enough_sample = sleeve["oos_trade_count"] >= 20 or sleeve["trades_per_year"] >= 8.0
        low_corr = safe_float(corr.get("daily_return_corr")) < 0.75
        not_sync_deep_loss = safe_float(dd.get("other_return")) > -0.10
        top_misaligned = (
            safe_float(top.get("top20_overlap_count_left")) <= 5
            and safe_float(top.get("left_contribution_overlap")) < 0.35
        )
        stage2_ready = (
            oos_positive and enough_sample and low_corr and not_sync_deep_loss and top_misaligned and any_combo_pass
        )
        rows.append(
            {
                "sleeve": name,
                "oos_positive": oos_positive,
                "enough_sample": enough_sample,
                "low_corr_vs_v22": low_corr,
                "not_sync_deep_loss_in_v22_maxdd": not_sync_deep_loss,
                "top20_misaligned": top_misaligned,
                "any_fixed_combo_pass": any_combo_pass,
                "stage2_ready": stage2_ready,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = sorted({key for row in rows for key in row}) if rows else ["empty"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_all() -> dict[str, Any]:
    df5, base_signals, scope = exp0110.load_base()
    df5 = clean_ohlcv(df5)
    split_idx = int(scope["split_idx"])
    split_time = pd.Timestamp(df5["datetime"].iloc[split_idx])
    results: dict[str, SleeveResult] = {}
    for sleeve in SLEEVES:
        print(f"=== {sleeve.name} ===", flush=True)
        results[sleeve.name] = evaluate_sleeve(sleeve, df5, base_signals, split_time)

    sleeve_rows = [result.row for result in results.values()]
    daily_frame = aligned_daily_frame(results)
    corr_rows = pair_correlations(daily_frame)
    dd_rows = dd_overlap_rows(results, daily_frame)
    winners = top_winner_rows(results)
    top_overlap = top_overlap_rows(winners)
    base_row = results["5m_v22_moirai_baseline"].row
    combos = combo_rows(daily_frame, base_row["oos_return"], base_row["max_dd"], split_time)
    gate_rows = stage_gate_rows(sleeve_rows, corr_rows, dd_rows, top_overlap, combos)
    verdict = "OBSERVE" if any(row["stage2_ready"] for row in gate_rows) else "REJECT"
    report = {
        "scope": {
            "experiment_id": "exp_0136",
            "stage": "stage_1_multitimeframe_diversification_diagnostic",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "data": str(exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{df5['datetime'].iloc[0]} to {df5['datetime'].iloc[-1]}",
            "split_time": str(split_time),
            "one_hour_lookback_choice": "32 bars because 375*5m is 31.25h; rounded up for first diagnostic",
            "resample": "completed OHLCV bars with label=right, closed=right",
            "core_donchian": "close > shifted rolling high => long; close < shifted rolling low => short; next bar open execution",
            "excluded": "no regime split, BB confirmation, MTG, BCD, Moirai gate for non-baseline sleeves, or daily EMA permission",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "moirai_blocked": int(scope["moirai_blocked"]),
        },
        "verdict": verdict,
        "sleeves": sleeve_rows,
        "correlations": corr_rows,
        "dd_overlap": dd_rows,
        "top_winners": winners,
        "top_overlap": top_overlap,
        "combos": combos,
        "stage_gates": gate_rows,
    }
    OUT.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=json_default),
        encoding="utf-8",
    )
    write_csv(OUT.with_suffix(".csv"), sleeve_rows)
    write_csv(OUT.with_name(OUT.name + "_correlations").with_suffix(".csv"), corr_rows)
    write_csv(OUT.with_name(OUT.name + "_dd_overlap").with_suffix(".csv"), dd_rows)
    write_csv(OUT.with_name(OUT.name + "_top_winners").with_suffix(".csv"), winners)
    write_csv(OUT.with_name(OUT.name + "_top_overlap").with_suffix(".csv"), top_overlap)
    write_csv(OUT.with_name(OUT.name + "_combos").with_suffix(".csv"), combos)
    write_csv(OUT.with_name(OUT.name + "_stage_gates").with_suffix(".csv"), gate_rows)
    write_markdown(report)
    print(OUT.with_suffix(".md"))
    print("verdict", verdict)
    for row in sleeve_rows:
        print(
            row["name"],
            "oos",
            round(row["oos_return"] * 100, 2),
            "dd",
            round(row["max_dd"] * 100, 2),
            "trades/y",
            round(row["trades_per_year"], 2),
        )
    return report


def write_markdown(report: dict[str, Any]) -> None:
    sleeves = report["sleeves"]
    corr = report["correlations"]
    combos = report["combos"]
    gates = report["stage_gates"]
    verdict = report["verdict"]
    md = [
        "# exp_0136 v2.2 multitimeframe diversification diagnostic",
        "",
        "- research-only; no live/checkpoint/config/oracle/production strategy change",
        "- question: do core Donchian sleeves on 15m/1h diversify trend return, drawdown, and top winners versus the 5m v2.2+Moirai line?",
        "- non-baseline sleeves are core Donchian only: no regime split, BB confirmation, MTG, BCD, Moirai gate, or daily EMA permission",
        "- execution: completed bar signal, next bar open fill, normal fee/slippage plus 10bp fee stress",
        "- resample: 15m/1h from 5m using completed OHLCV bars",
        f"- verdict: `{verdict}`",
        "",
        "## Sleeves",
        "",
        "| sleeve | TF | lookback | min hold | full | OOS | DD | roll12 | trades | trades/year | OOS trades | max idle d | top20 gross | worst20 gross |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sleeves:
        md.append(
            f"| {row['name']} | {row['timeframe']} | {row['entry_lookback']} | {row['min_hold_bars']} | "
            f"{pct(row['full_return'])} | {pct(row['oos_return'])} | {pct(row['max_dd'])} | "
            f"{pct(row['rolling12_min'])} | {row['trade_count']} | {row['trades_per_year']:.2f} | "
            f"{row['oos_trade_count']} | {row['max_idle_days']:.1f} | "
            f"{pct(row['top20_winner_contribution_to_gross_winners'])} | "
            f"{pct(row['worst20_loser_contribution_to_gross_losers'])} |"
        )
    md.extend(
        [
            "",
            "## Correlations",
            "",
            "| left | right | daily | roll90 mean | roll180 mean | monthly |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in corr:
        md.append(
            f"| {row['left']} | {row['right']} | {row['daily_return_corr']:.2f} | "
            f"{row['rolling90_corr_mean']:.2f} | {row['rolling180_corr_mean']:.2f} | "
            f"{row['monthly_return_corr']:.2f} |"
        )
    md.extend(
        [
            "",
            "## Fixed Combos",
            "",
            "| combo | full | OOS | DD | DD improve | roll12 | OOS gate | combo gate |",
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in combos:
        md.append(
            f"| {row['combo']} | {pct(row['full_return'])} | {pct(row['oos_return'])} | "
            f"{pct(row['max_dd'])} | {pct(row['dd_improve_vs_v22'])} | {pct(row['rolling12_min'])} | "
            f"{row['oos_not_worse_than_v22_by_20pct']} | {row['passes_combo_gate']} |"
        )
    md.extend(
        [
            "",
            "## Stage Gates",
            "",
            "| sleeve | OOS > 0 | sample ok | corr ok | DD desync | top20 misaligned | combo pass | stage2 ready |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in gates:
        md.append(
            f"| {row['sleeve']} | {row['oos_positive']} | {row['enough_sample']} | "
            f"{row['low_corr_vs_v22']} | {row['not_sync_deep_loss_in_v22_maxdd']} | "
            f"{row['top20_misaligned']} | {row['any_fixed_combo_pass']} | {row['stage2_ready']} |"
        )
    md.extend(
        [
            "",
            "## Reporting Contract",
            "",
            f"- sleeve metrics: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- correlations: `{OUT.with_name(OUT.name + '_correlations').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- DD overlap: `{OUT.with_name(OUT.name + '_dd_overlap').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- top winners: `{OUT.with_name(OUT.name + '_top_winners').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- top overlap: `{OUT.with_name(OUT.name + '_top_overlap').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- combos: `{OUT.with_name(OUT.name + '_combos').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- stage gates: `{OUT.with_name(OUT.name + '_stage_gates').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            "- regime-permission result: intentionally excluded for non-baseline sleeves in Stage 1.",
            "- safe-execution result: all sleeve signal changes are evaluated at next bar open.",
            "- conclusion: research-only; no live/demo routing or checkpoint promotion is authorized.",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multitimeframe Donchian diversification diagnostic.")
    parser.add_argument("--mode", choices=["all"], default="all")
    parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
