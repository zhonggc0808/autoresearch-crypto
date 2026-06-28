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

EXP0136_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py"
spec0136 = importlib.util.spec_from_file_location("exp0136_multitimeframe", EXP0136_PATH)
exp0136 = importlib.util.module_from_spec(spec0136)
sys.modules[spec0136.name] = exp0136
assert spec0136.loader is not None
spec0136.loader.exec_module(exp0136)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0138_v22_position_sizing_reclaim_add_diagnostic"

INITIAL = exp0136.INITIAL
COMMISSION = exp0136.COMMISSION
SLIPPAGE = exp0136.SLIPPAGE
FEE10_COMMISSION = exp0136.FEE10_COMMISSION
ADD_LOOKBACK = 72
TRADING_DAYS_PER_YEAR = exp0136.TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class VariantSpec:
    name: str
    initial_size: float
    add_sizes: tuple[float, ...]
    dd_throttle: bool
    role: str


@dataclass
class SimResult:
    spec: VariantSpec
    df: pd.DataFrame
    signals: np.ndarray
    equity: np.ndarray
    trades: list[dict[str, Any]]
    events: list[dict[str, Any]]
    exposure: np.ndarray
    row: dict[str, Any]


VARIANTS = [
    VariantSpec("baseline_100", 1.00, (), False, "baseline_reference"),
    VariantSpec("V0_constant_50", 0.50, (), False, "linear_constant_control"),
    VariantSpec("V1_50_reclaim_add2", 0.50, (0.25, 0.25), False, "reclaim_add_increment"),
    VariantSpec("V2_50_reclaim_add2_dd_throttle", 0.50, (0.25, 0.25), True, "reclaim_add_plus_dd_throttle"),
    VariantSpec("V3_constant_75", 0.75, (), False, "linear_constant_control"),
    VariantSpec("V4_75_reclaim_add1", 0.75, (0.25,), False, "reclaim_add_increment"),
    VariantSpec("V5_75_reclaim_add1_dd_throttle", 0.75, (0.25,), True, "reclaim_add_plus_dd_throttle"),
]

ATTRIBUTION_PAIRS = [
    ("V0_minus_baseline", "V0_constant_50", "baseline_100"),
    ("V1_minus_V0", "V1_50_reclaim_add2", "V0_constant_50"),
    ("V2_minus_V1", "V2_50_reclaim_add2_dd_throttle", "V1_50_reclaim_add2"),
    ("V3_minus_baseline", "V3_constant_75", "baseline_100"),
    ("V4_minus_V3", "V4_75_reclaim_add1", "V3_constant_75"),
    ("V5_minus_V4", "V5_75_reclaim_add1_dd_throttle", "V4_75_reclaim_add1"),
]


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
    if isinstance(value, VariantSpec):
        return asdict(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def target_from_signal(signal: int, current_position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return current_position


def dd_throttle_multiplier(drawdown: float) -> float:
    if drawdown < 0.15:
        return 1.0
    if drawdown < 0.25:
        return 0.75
    if drawdown < 0.40:
        return 0.50
    return 0.25


def shifted_next_open_actions(decisions: list[dict[str, Any] | None]) -> list[dict[str, Any] | None]:
    out: list[dict[str, Any] | None] = [None for _ in decisions]
    for i in range(1, len(decisions)):
        out[i] = decisions[i - 1]
    return out


def current_equity(cash: float, position: int, shares: float, entry_price: float, close_price: float) -> float:
    if position > 0:
        return cash + shares * close_price
    if position < 0:
        return cash + abs(shares) * (entry_price - close_price)
    return cash


def after_cost_open_pnl(position: int, shares: float, cost_basis: float, close_price: float, commission: float, slippage: float) -> float:
    if position > 0:
        exec_price = close_price * (1.0 - slippage)
        proceeds = shares * exec_price * (1.0 - commission)
        return proceeds - cost_basis
    if position < 0:
        exec_price = close_price * (1.0 + slippage)
        cover_total = abs(shares) * exec_price * (1.0 + commission)
        return cost_basis - cover_total
    return 0.0


def avg_entry_price(position: int, old_shares: float, old_entry_price: float, add_shares: float, add_exec_price: float) -> float:
    total = abs(old_shares) + abs(add_shares)
    if total <= 0:
        return 0.0
    if position == 0 or old_shares == 0:
        return add_exec_price
    return (abs(old_shares) * old_entry_price + abs(add_shares) * add_exec_price) / total


def open_position(
    *,
    target: int,
    fraction: float,
    cash: float,
    open_price: float,
    commission: float,
    slippage: float,
) -> tuple[float, float, float, float, float]:
    fraction = min(max(float(fraction), 0.0), 1.0)
    if cash <= 0 or target == 0 or fraction <= 0:
        return cash, 0.0, 0.0, 0.0, 0.0
    deploy = cash * fraction
    if target > 0:
        exec_price = open_price * (1.0 + slippage)
        shares = deploy * (1.0 - commission) / exec_price
        return cash - deploy, shares, deploy, exec_price, deploy / cash if cash else 0.0
    exec_price = open_price * (1.0 - slippage)
    shares = -(deploy * (1.0 - commission) / exec_price)
    return cash - deploy * commission, shares, deploy, exec_price, deploy / cash if cash else 0.0


def add_position(
    *,
    position: int,
    requested_fraction: float,
    cash: float,
    shares: float,
    cost_basis: float,
    entry_price: float,
    exposure_fraction: float,
    equity_at_open: float,
    open_price: float,
    commission: float,
    slippage: float,
) -> tuple[float, float, float, float, float, float]:
    available_fraction = max(0.0, 1.0 - exposure_fraction)
    fraction = min(max(float(requested_fraction), 0.0), available_fraction)
    if position == 0 or fraction <= 0 or equity_at_open <= 0:
        return cash, shares, cost_basis, entry_price, exposure_fraction, 0.0
    deploy = equity_at_open * fraction
    if position > 0:
        deploy = min(deploy, max(cash, 0.0))
        if deploy <= 0:
            return cash, shares, cost_basis, entry_price, exposure_fraction, 0.0
        exec_price = open_price * (1.0 + slippage)
        add_shares = deploy * (1.0 - commission) / exec_price
        new_entry = avg_entry_price(position, shares, entry_price, add_shares, exec_price)
        actual_fraction = deploy / equity_at_open
        return cash - deploy, shares + add_shares, cost_basis + deploy, new_entry, exposure_fraction + actual_fraction, deploy
    exec_price = open_price * (1.0 - slippage)
    add_shares = -(deploy * (1.0 - commission) / exec_price)
    new_entry = avg_entry_price(position, shares, entry_price, add_shares, exec_price)
    return (
        cash - deploy * commission,
        shares + add_shares,
        cost_basis + deploy,
        new_entry,
        exposure_fraction + fraction,
        deploy,
    )


def close_position(
    *,
    position: int,
    cash: float,
    shares: float,
    cost_basis: float,
    entry_price: float,
    exit_price_base: float,
    commission: float,
    slippage: float,
) -> tuple[float, float, float]:
    if position > 0:
        exec_price = exit_price_base * (1.0 - slippage)
        proceeds = shares * exec_price * (1.0 - commission)
        pnl = proceeds - cost_basis
        return cash + proceeds, pnl, exec_price
    if position < 0:
        exec_price = exit_price_base * (1.0 + slippage)
        cover_total = abs(shares) * exec_price * (1.0 + commission)
        pnl = cost_basis - cover_total
        return max(0.0, cash + pnl), pnl, exec_price
    return cash, 0.0, exit_price_base


def recent_reclaim_breakout(df: pd.DataFrame, entry_step: int, i: int, position: int) -> bool:
    if position == 0 or i <= entry_step:
        return False
    start = max(entry_step, i - ADD_LOOKBACK)
    if start >= i:
        return False
    close = float(df["close"].iloc[i])
    if position > 0:
        prev_high = float(pd.to_numeric(df["high"].iloc[start:i], errors="coerce").max())
        return np.isfinite(prev_high) and close > prev_high
    prev_low = float(pd.to_numeric(df["low"].iloc[start:i], errors="coerce").min())
    return np.isfinite(prev_low) and close < prev_low


def make_decisions(
    spec: VariantSpec,
    df: pd.DataFrame,
    signals: np.ndarray,
    *,
    commission: float,
    slippage: float,
) -> list[dict[str, Any] | None]:
    decisions: list[dict[str, Any] | None] = [None for _ in range(len(df))]
    cash = INITIAL
    shares = 0.0
    cost_basis = 0.0
    entry_price = 0.0
    entry_step = -1
    position = 0
    exposure_fraction = 0.0
    add_count = 0
    peak_equity = INITIAL
    pending_action: dict[str, Any] | None = None

    for i in range(len(df)):
        open_price = float(df["open"].iloc[i])
        close_price = float(df["close"].iloc[i])
        action = pending_action
        pending_action = None
        if action is not None:
            kind = str(action["kind"])
            if kind == "target":
                target = int(action["target"])
                if position != 0 and target != position:
                    exit_cash, pnl, exit_price = close_position(
                        position=position,
                        cash=cash,
                        shares=shares,
                        cost_basis=cost_basis,
                        entry_price=entry_price,
                        exit_price_base=open_price,
                        commission=commission,
                        slippage=slippage,
                    )
                    _ = pnl, exit_price
                    cash = exit_cash
                    shares = 0.0
                    position = 0
                    cost_basis = 0.0
                    entry_price = 0.0
                    exposure_fraction = 0.0
                    add_count = 0
                if target != 0 and position == 0:
                    cash, shares, cost_basis, entry_price, exposure_fraction = open_position(
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
                        add_count = 0
            elif kind == "add" and position != 0:
                equity_at_open = current_equity(cash, position, shares, entry_price, open_price)
                cash, shares, cost_basis, entry_price, exposure_fraction, deploy = add_position(
                    position=position,
                    requested_fraction=float(action["size"]),
                    cash=cash,
                    shares=shares,
                    cost_basis=cost_basis,
                    entry_price=entry_price,
                    exposure_fraction=exposure_fraction,
                    equity_at_open=equity_at_open,
                    open_price=open_price,
                    commission=commission,
                    slippage=slippage,
                )
                if deploy > 0:
                    add_count += 1

        equity_close = current_equity(cash, position, shares, entry_price, close_price)
        peak_equity = max(peak_equity, equity_close)
        drawdown = 1.0 - equity_close / peak_equity if peak_equity > 0 else 0.0
        throttle = dd_throttle_multiplier(drawdown) if spec.dd_throttle else 1.0
        target = target_from_signal(int(signals[i]), position)
        decision: dict[str, Any] | None = None
        if target != position:
            size = spec.initial_size * throttle if target != 0 else 0.0
            decision = {"kind": "target", "target": target, "size": size, "drawdown": drawdown, "throttle": throttle}
        elif position != 0 and add_count < len(spec.add_sizes):
            pnl_after_cost = after_cost_open_pnl(position, shares, cost_basis, close_price, commission, slippage)
            if pnl_after_cost > 0.0 and recent_reclaim_breakout(df, entry_step, i, position):
                size = spec.add_sizes[add_count] * throttle
                decision = {
                    "kind": "add",
                    "target": position,
                    "size": size,
                    "add_number": add_count + 1,
                    "drawdown": drawdown,
                    "throttle": throttle,
                    "pnl_after_cost": pnl_after_cost,
                }
        decisions[i] = decision
        pending_action = decision
    return decisions


def simulate_sizing(
    spec: VariantSpec,
    df: pd.DataFrame,
    signals: np.ndarray,
    *,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]], np.ndarray]:
    decisions = make_decisions(spec, df, signals, commission=commission, slippage=slippage)
    actions = shifted_next_open_actions(decisions)
    cash = INITIAL
    shares = 0.0
    cost_basis = 0.0
    entry_price = 0.0
    entry_step = -1
    position = 0
    exposure_fraction = 0.0
    add_count = 0
    equity: list[float] = []
    exposure: list[float] = []
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    open_add_events: list[dict[str, Any]] = []

    for i in range(len(df)):
        open_price = float(df["open"].iloc[i])
        close_price = float(df["close"].iloc[i])
        time = pd.Timestamp(df["datetime"].iloc[i])
        action = actions[i]
        if action is not None:
            kind = str(action["kind"])
            if kind == "target":
                target = int(action["target"])
                if position != 0 and target != position:
                    old_position = position
                    exit_cash, pnl, exit_price = close_position(
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
                        "add_count": add_count,
                    }
                    trades.append(trade)
                    events.append({**trade, "event": "close", "time": str(time)})
                    shares = 0.0
                    position = 0
                    cost_basis = 0.0
                    entry_price = 0.0
                    exposure_fraction = 0.0
                    add_count = 0
                    open_add_events = []
                if target != 0 and position == 0:
                    cash, shares, cost_basis, entry_price, exposure_fraction = open_position(
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
                        add_count = 0
                        event = {
                            "variant": spec.name,
                            "event": "open",
                            "time": str(time),
                            "step": i,
                            "position": position,
                            "size": exposure_fraction,
                            "price": entry_price,
                            "notional": cost_basis,
                            "drawdown": safe_float(action.get("drawdown")),
                            "throttle": safe_float(action.get("throttle")),
                        }
                        events.append(event)
                        open_add_events = [event]
            elif kind == "add" and position != 0:
                equity_at_open = current_equity(cash, position, shares, entry_price, open_price)
                before_exposure = exposure_fraction
                cash, shares, cost_basis, entry_price, exposure_fraction, deploy = add_position(
                    position=position,
                    requested_fraction=float(action["size"]),
                    cash=cash,
                    shares=shares,
                    cost_basis=cost_basis,
                    entry_price=entry_price,
                    exposure_fraction=exposure_fraction,
                    equity_at_open=equity_at_open,
                    open_price=open_price,
                    commission=commission,
                    slippage=slippage,
                )
                if deploy > 0:
                    add_count += 1
                    exec_price = open_price * (1.0 + slippage if position > 0 else 1.0 - slippage)
                    event = {
                        "variant": spec.name,
                        "event": "add",
                        "time": str(time),
                        "step": i,
                        "position": position,
                        "add_number": int(action.get("add_number", add_count)),
                        "size": exposure_fraction - before_exposure,
                        "price": exec_price,
                        "notional": deploy,
                        "drawdown": safe_float(action.get("drawdown")),
                        "throttle": safe_float(action.get("throttle")),
                    }
                    events.append(event)
                    open_add_events.append(event)

        equity_close = current_equity(cash, position, shares, entry_price, close_price)
        equity.append(equity_close if np.isfinite(equity_close) else 0.0)
        exposure.append(exposure_fraction if position != 0 else 0.0)

    if position != 0 and len(df):
        i = len(df) - 1
        close_price = float(df["close"].iloc[i])
        time = pd.Timestamp(df["datetime"].iloc[i])
        old_position = position
        cash, pnl, exit_price = close_position(
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
            "add_count": add_count,
        }
        trades.append(trade)
        events.append({**trade, "event": "close_final", "time": str(time)})
        if equity:
            equity[-1] = cash
            exposure[-1] = 0.0

    return np.asarray(equity, dtype=float), trades, events, np.asarray(exposure, dtype=float)


def slice_by_time(df: pd.DataFrame, signals: np.ndarray, split_time: pd.Timestamp) -> tuple[pd.DataFrame, np.ndarray]:
    return exp0136.slice_by_time(df, signals, split_time)


def exposure_stats(exposure: np.ndarray) -> dict[str, float]:
    if len(exposure) == 0:
        return {
            "avg_exposure": 0.0,
            "median_exposure": 0.0,
            "max_exposure": 0.0,
            "time_at_50": 0.0,
            "time_at_75": 0.0,
            "time_at_100": 0.0,
        }
    return {
        "avg_exposure": float(np.mean(exposure)),
        "median_exposure": float(np.median(exposure)),
        "max_exposure": float(np.max(exposure)),
        "time_at_50": float(np.mean(np.isclose(exposure, 0.50, atol=0.025))),
        "time_at_75": float(np.mean(np.isclose(exposure, 0.75, atol=0.025))),
        "time_at_100": float(np.mean(exposure >= 0.975)),
    }


def row_for_result(
    spec: VariantSpec,
    df: pd.DataFrame,
    signals: np.ndarray,
    split_time: pd.Timestamp,
    baseline_row: dict[str, Any] | None = None,
    *,
    commission: float = COMMISSION,
) -> SimResult:
    equity, trades, events, exposure = simulate_sizing(spec, df, signals, commission=commission)
    daily = exp0136.daily_equity(df, equity)
    oos_df, oos_signals = slice_by_time(df, signals, split_time)
    oos_equity, oos_trades, _, _ = simulate_sizing(spec, oos_df, oos_signals, commission=commission)
    fee10_equity, _, _, _ = simulate_sizing(spec, df, signals, commission=FEE10_COMMISSION)
    fee10_oos_equity, _, _, _ = simulate_sizing(spec, oos_df, oos_signals, commission=FEE10_COMMISSION)
    full_m = exp0136.equity_metrics(equity)
    oos_m = exp0136.equity_metrics(oos_equity)
    fee10_m = exp0136.equity_metrics(fee10_equity)
    fee10_oos_m = exp0136.equity_metrics(fee10_oos_equity)
    returns = exp0136.trade_returns(trades)
    hold_bars = [int(trade["step"]) - int(trade["entry_step"]) for trade in trades]
    contribution = exp0136.top_worst_contribution(trades)
    add_events = [event for event in events if event.get("event") == "add"]
    add1 = [event for event in add_events if int(event.get("add_number", 0)) == 1]
    add2 = [event for event in add_events if int(event.get("add_number", 0)) == 2]
    row = {
        **asdict(spec),
        "add_sizes": json.dumps(list(spec.add_sizes)),
        "full_return": full_m["return"],
        "oos_return": oos_m["return"],
        "max_dd": full_m["dd"],
        "return_dd_ratio": full_m["return"] / abs(full_m["dd"]) if full_m["dd"] else 0.0,
        "rolling12_min": exp0136.rolling_min_return(daily, 365),
        "trade_count": len([trade for trade in trades if trade.get("pnl") is not None]),
        "add_count": len(add_events),
        "add1_count": len(add1),
        "add2_count": len(add2),
        "avg_trade_return": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return": float(np.median(returns)) if returns else 0.0,
        "avg_trade_pnl": float(np.mean([safe_float(trade.get("pnl")) for trade in trades])) if trades else 0.0,
        "median_trade_pnl": float(np.median([safe_float(trade.get("pnl")) for trade in trades])) if trades else 0.0,
        "avg_hold_bars": float(np.mean(hold_bars)) if hold_bars else 0.0,
        "median_hold_bars": float(np.median(hold_bars)) if hold_bars else 0.0,
        "oos_trade_count": len(oos_trades),
        "oos_trades_per_year": len(oos_trades) / exp0136.duration_years(oos_df) if len(oos_df) else 0.0,
        "trades_per_year": len(trades) / exp0136.duration_years(df),
        "fee10_oos_return": fee10_oos_m["return"],
        "fee10_dd": fee10_m["dd"],
        "fee10_full_return": fee10_m["return"],
        **exposure_stats(exposure),
        **contribution,
        **exp0136.year_wlf(daily),
    }
    if baseline_row is None:
        row["dd_improve_vs_baseline"] = 0.0
        row["top20_winner_damage_vs_baseline"] = 0.0
        row["worst20_improve_vs_baseline"] = 0.0
        row["fee10_dd_improve_vs_baseline"] = 0.0
    else:
        row["dd_improve_vs_baseline"] = 1.0 - abs(row["max_dd"]) / abs(float(baseline_row["max_dd"]))
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
    return SimResult(spec=spec, df=df, signals=signals, equity=equity, trades=trades, events=events, exposure=exposure, row=row)


def attribution_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {row["name"]: row for row in rows}
    out: list[dict[str, Any]] = []
    for label, child, parent in ATTRIBUTION_PAIRS:
        child_row = by_name[child]
        parent_row = by_name[parent]
        out.append(
            {
                "comparison": label,
                "child": child,
                "parent": parent,
                "full_return_delta": child_row["full_return"] - parent_row["full_return"],
                "oos_return_delta": child_row["oos_return"] - parent_row["oos_return"],
                "max_dd_delta": child_row["max_dd"] - parent_row["max_dd"],
                "dd_improve_vs_baseline_delta": child_row["dd_improve_vs_baseline"]
                - parent_row["dd_improve_vs_baseline"],
                "rolling12_min_delta": child_row["rolling12_min"] - parent_row["rolling12_min"],
                "return_dd_ratio_delta": child_row["return_dd_ratio"] - parent_row["return_dd_ratio"],
                "top20_winner_pnl_delta": child_row["top20_winner_pnl"] - parent_row["top20_winner_pnl"],
                "worst20_loser_pnl_delta": child_row["worst20_loser_pnl"] - parent_row["worst20_loser_pnl"],
                "add_count_delta": child_row["add_count"] - parent_row["add_count"],
            }
        )
    return out


def stage_gate_rows(rows: list[dict[str, Any]], attribution: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {row["name"]: row for row in rows}
    baseline = by_name["baseline_100"]
    attr_by_child = {item["child"]: item for item in attribution}
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["name"] == "baseline_100":
            continue
        observe = (
            row["dd_improve_vs_baseline"] >= 0.20
            and row["oos_return"] >= baseline["oos_return"] * 0.65
            and row["rolling12_min"] > baseline["rolling12_min"]
            and row["fee10_oos_return"] > 0.0
        )
        parent_attr = attr_by_child.get(row["name"])
        parent_dd_not_worse = True
        nonlinear = False
        if parent_attr is not None and row["add_sizes"] != "[]":
            parent = by_name[str(parent_attr["parent"])]
            if bool(row["dd_throttle"]):
                nonlinear = bool(
                    parent_attr["dd_improve_vs_baseline_delta"] > 0.0
                    and row["oos_return"] >= parent["oos_return"] * 0.80
                )
            else:
                parent_dd_not_worse = abs(row["max_dd"]) <= abs(parent["max_dd"]) * 1.10
                nonlinear = bool(parent_attr["oos_return_delta"] > 0.0 and parent_dd_not_worse)
        shadow = (
            observe
            and row["dd_improve_vs_baseline"] >= 0.25
            and row["oos_return"] >= baseline["oos_return"] * 0.75
            and row["return_dd_ratio"] > baseline["return_dd_ratio"]
            and row["top20_winner_damage_vs_baseline"] <= 0.20
            and row["worst20_improve_vs_baseline"] > 0.0
            and row["year_losses"] <= baseline["year_losses"]
            and row["fee10_dd_improve_vs_baseline"] >= 0.20
            and (nonlinear if row["add_sizes"] != "[]" else False)
        )
        out.append(
            {
                "variant": row["name"],
                "dd_improve_ge20": row["dd_improve_vs_baseline"] >= 0.20,
                "dd_improve_ge25": row["dd_improve_vs_baseline"] >= 0.25,
                "oos_keep_65": row["oos_return"] >= baseline["oos_return"] * 0.65,
                "oos_keep_75": row["oos_return"] >= baseline["oos_return"] * 0.75,
                "rolling12_improved": row["rolling12_min"] > baseline["rolling12_min"],
                "fee10_positive": row["fee10_oos_return"] > 0.0,
                "return_dd_improved": row["return_dd_ratio"] > baseline["return_dd_ratio"],
                "top20_damage_le20": row["top20_winner_damage_vs_baseline"] <= 0.20,
                "worst20_improved": row["worst20_improve_vs_baseline"] > 0.0,
                "year_losses_not_increase": row["year_losses"] <= baseline["year_losses"],
                "nonlinear_evidence": nonlinear,
                "observe": observe,
                "shadow_candidate": shadow,
            }
        )
    return out


def verdict_from_gates(gates: list[dict[str, Any]]) -> str:
    if any(row["shadow_candidate"] for row in gates):
        return "SHADOW_CANDIDATE"
    if any(row["observe"] for row in gates):
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
    write_csv(OUT.with_name(OUT.name + "_attribution.csv"), payload["attribution"])
    write_csv(OUT.with_name(OUT.name + "_stage_gates.csv"), payload["stage_gates"])
    write_csv(OUT.with_name(OUT.name + "_events.csv"), payload["events"])
    write_csv(OUT.with_name(OUT.name + "_trades.csv"), payload["trades"])
    write_report(payload)


def write_report(payload: dict[str, Any]) -> None:
    lines = [
        "# exp_0138 v2.2 position sizing reclaim-add diagnostic",
        "",
        "- research-only",
        "- position sizing diagnostic only",
        "- no live/checkpoint/config/oracle/production strategy change",
        "- no fixed-R profit taking",
        "- DD throttle controls new entries/adds only; it does not reduce existing positions",
        f"- verdict: `{payload['verdict']}`",
        "",
        "## Variants",
        "",
        "| variant | initial | adds | throttle | full | OOS | DD | DD improve | roll12 | adds | avg exp | max exp | top20 damage | worst20 improve | fee10 OOS | fee10 DD | W/L |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["variants"]:
        lines.append(
            f"| {row['name']} | {pct(row['initial_size'])} | {row['add_sizes']} | {row['dd_throttle']} | "
            f"{pct(row['full_return'])} | {pct(row['oos_return'])} | {pct(row['max_dd'])} | "
            f"{pct(row['dd_improve_vs_baseline'])} | {pct(row['rolling12_min'])} | {row['add_count']} | "
            f"{pct(row['avg_exposure'])} | {pct(row['max_exposure'])} | "
            f"{pct(row['top20_winner_damage_vs_baseline'])} | {pct(row['worst20_improve_vs_baseline'])} | "
            f"{pct(row['fee10_oos_return'])} | {pct(row['fee10_dd'])} | {row['year_wins']}/{row['year_losses']} |"
        )
    lines.extend(
        [
            "",
            "## Attribution",
            "",
            "| comparison | dOOS | dDD | dRoll12 | dReturn/DD | dTop20 | dWorst20 | dAdds |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["attribution"]:
        lines.append(
            f"| {row['comparison']} | {pct(row['oos_return_delta'])} | {pct(row['max_dd_delta'])} | "
            f"{pct(row['rolling12_min_delta'])} | {row['return_dd_ratio_delta']:.2f} | "
            f"{row['top20_winner_pnl_delta']:.2f} | {row['worst20_loser_pnl_delta']:.2f} | {row['add_count_delta']} |"
        )
    lines.extend(
        [
            "",
            "## Stage Gates",
            "",
            "| variant | DD20 | OOS65 | roll12 | fee10 | nonlinear | observe | shadow |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in payload["stage_gates"]:
        lines.append(
            f"| {row['variant']} | {row['dd_improve_ge20']} | {row['oos_keep_65']} | "
            f"{row['rolling12_improved']} | {row['fee10_positive']} | {row['nonlinear_evidence']} | "
            f"{row['observe']} | {row['shadow_candidate']} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            "- Interpret V0/V3 as linear constant-sizing controls before giving credit to reclaim-add variants.",
            "- If V1/V2 do not beat V0, or V4/V5 do not beat V3, the conclusion is simple sizing rather than complex add logic.",
            "- No result here authorizes live position sizing, exchange routing, checkpoint promotion, or production strategy changes.",
            "",
            "## Evidence",
            "",
            f"- variants: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"- attribution: `{OUT.with_name(OUT.name + '_attribution.csv').relative_to(PROJECT_ROOT)}`",
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
    split_time = pd.Timestamp(df["datetime"].iloc[int(scope["split_idx"])])
    results: list[SimResult] = []
    baseline_result = row_for_result(VARIANTS[0], df, base_signals.astype(int), split_time, baseline_row=None)
    baseline_row = baseline_result.row
    results.append(baseline_result)
    for spec in VARIANTS[1:]:
        print(f"=== {spec.name} ===", flush=True)
        results.append(row_for_result(spec, df, base_signals.astype(int), split_time, baseline_row=baseline_row))

    rows = [result.row for result in results]
    attribution = attribution_rows(rows)
    gates = stage_gate_rows(rows, attribution)
    verdict = verdict_from_gates(gates)
    payload = {
        "experiment_id": "exp_0138_v22_position_sizing_reclaim_add_diagnostic",
        "verdict": verdict,
        "scope": {
            "research_only": True,
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "data": str(exp0136.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}",
            "split_idx": int(scope["split_idx"]),
            "split_time": str(pd.Timestamp(df["datetime"].iloc[int(scope["split_idx"])])),
            "moirai_blocked": int(scope["moirai_blocked"]),
            "throttle_tiers": "DD<15%=1.0x, 15-25%=0.75x, 25-40%=0.5x, >40%=0.25x",
            "reclaim_add": "after-cost open profit > 0 and close breaks prior 72-bar high/low since entry; completed bar then next open",
            "excluded": "fixed-R profit taking, live routing, checkpoint/config/oracle/production changes",
        },
        "variants": rows,
        "attribution": attribution,
        "stage_gates": gates,
        "events": [event for result in results for event in result.events],
        "trades": [trade for result in results for trade in result.trades],
    }
    write_outputs(payload)
    print("verdict", verdict)
    for row in rows:
        print(
            row["name"],
            "oos",
            round(row["oos_return"] * 100, 2),
            "dd",
            round(row["max_dd"] * 100, 2),
            "adds",
            row["add_count"],
            "ddImp",
            round(row["dd_improve_vs_baseline"] * 100, 2),
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all"], default="all")
    parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
