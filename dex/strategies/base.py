"""Base strategy classes and evaluation utilities for the dex trading framework."""

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from dex.config import (
    BARS_PER_YEAR,
    COMMISSION,
    EVAL_MAX_DRAWDOWN,
    EVAL_MIN_EQUITY_RATIO,
    EVAL_MIN_RETURN,
    EVAL_MIN_TRADES,
    INITIAL_CAPITAL,
    SLIPPAGE,
)


def _ema(series: np.ndarray, window: int) -> np.ndarray:
    """Compute exponential moving average of a numpy array.

    Args:
        series: 1-D array of values.
        window: EMA lookback window (the smoothing factor is 2/(window+1)).

    Returns:
        1-D array of EMA values, same length as ``series``.
    """
    alpha = 2 / (window + 1)
    ema = np.zeros(len(series), dtype=np.float64)
    ema[0] = series[0]
    for i in range(1, len(series)):
        ema[i] = alpha * series[i] + (1 - alpha) * ema[i - 1]
    return ema.astype(np.float32)


def _df_value(df: pd.DataFrame, column: str, step: int) -> float | None:
    if column not in df.columns or not 0 <= step < len(df):
        return None
    value = float(df[column].iloc[step])
    return value if np.isfinite(value) else None


def _lagged_channel_upper(df: pd.DataFrame, step: int, config: Dict[str, Any]) -> float | None:
    for column in ("donchian_upper_lagged", "channel_upper_lagged"):
        value = _df_value(df, column, step)
        if value is not None:
            return value
    if "donchian_upper" not in df.columns:
        return None
    source_step = step - 1 if config.get("channel_upper_lagged", True) else step
    return _df_value(df, "donchian_upper", source_step)


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    Subclasses must implement ``generate_signals`` which accepts a DataFrame
    of OHLCV bars and returns integer-coded trading signals.
    """

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> np.ndarray:
        """Generate integer-coded trading signals from market data.

        Args:
            df: DataFrame with columns ``open``, ``high``, ``low``, ``close``,
                ``volume``, indexed by timestamp.

        Returns:
            1-D array of integer signals with the same length as ``df``.
            Signal encoding:
                - 0 = flat / close position
                - 1 = hold current position
                - 2 = enter / maintain long
                - 3 = enter / maintain short
        """
        ...


class StrategyEvaluator:
    """Strategy performance evaluator that simulates long/short trading.

    Attributes:
        initial_capital: Starting capital for the simulation.
        commission: Trading fee rate (e.g. 0.0002 for 0.02%).
        slippage: Slippage rate applied to execution prices.
    """

    def __init__(
        self,
        initial_capital: float = INITIAL_CAPITAL,
        commission: float = COMMISSION,
        slippage: float = SLIPPAGE,
    ) -> None:
        """Initialize the evaluator with capital and cost parameters.

        Args:
            initial_capital: Starting capital (default from config).
            commission: Fee rate per trade (default from config).
            slippage: Slippage rate (default from config).
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def simulate(
        self,
        signals: np.ndarray,
        prices: np.ndarray,
        df: pd.DataFrame | None = None,
        position_sizes: np.ndarray | None = None,
        stop_config: Dict[str, Any] | None = None,
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        """Simulate trading using signal and price arrays.

        Supports long (signal=2), short (signal=3), hold (1), and flat (0).

        Args:
            signals: Integer array of trading signals (0-3).
            prices: Array of close prices matching signal length.
            df: Optional DataFrame (unused; kept for API compatibility).
            position_sizes: Optional per-bar entry size multipliers in [0, 1].
            stop_config: Optional Phase 2 risk overlay config.

        Returns:
            A tuple ``(equity_curve, trades)`` where ``equity_curve`` is a
            1-D array of account equity at each step and ``trades`` is a list
            of trade event dicts.
        """
        if len(signals) != len(prices):
            raise ValueError("signals and prices must have the same length")
        if stop_config is not None:
            return self._simulate_with_stop_config(signals, prices, df, position_sizes, stop_config)
        if position_sizes is None:
            size_multipliers = np.ones(len(signals), dtype=float)
        else:
            size_multipliers = np.asarray(position_sizes, dtype=float)
            if len(size_multipliers) != len(signals):
                raise ValueError("position_sizes must match signals and prices length")
            if (
                not np.all(np.isfinite(size_multipliers))
                or np.any(size_multipliers < 0)
                or np.any(size_multipliers > 1)
            ):
                raise ValueError("position_sizes must be finite values in [0, 1]")

        capital = self.initial_capital
        shares = 0.0  # positive = long, negative = short
        position = 0  # 1 = long, -1 = short, 0 = flat
        equity: List[float] = []
        trades: List[Dict[str, Any]] = []
        entry_cost_basis = 0.0
        entry_price = 0.0
        entry_step = -1
        entry_size_multiplier = 0.0

        for i in range(len(signals)):
            signal = signals[i]
            price = prices[i]
            size_multiplier = float(size_multipliers[i])

            # Resolve signal to target position
            if signal == 2:
                target_pos = 1
            elif signal == 3:
                target_pos = -1
            elif signal == 0:
                target_pos = 0
            else:
                target_pos = position

            if target_pos != position:
                # Close existing long position
                if position == 1 and target_pos <= 0:
                    exec_price = price * (1 - self.slippage)
                    gross = shares * exec_price
                    cost = gross * self.commission
                    proceeds = gross - cost
                    capital += proceeds
                    pnl = proceeds - entry_cost_basis
                    trades.append(
                        {
                            "type": "sell",
                            "step": i,
                            "entry_step": entry_step,
                            "entry_size": entry_size_multiplier,
                            "pnl": float(pnl),
                            "entry_price": float(entry_price),
                            "exit_price": float(exec_price),
                            "entry_notional": float(entry_cost_basis),
                        }
                    )
                    shares = 0.0
                    position = 0

                # Close existing short position
                elif position == -1 and target_pos >= 0:
                    exec_price = price * (1 + self.slippage)
                    buy_cost = abs(shares) * exec_price
                    buy_cost_total = buy_cost * (1 + self.commission)
                    pnl = entry_cost_basis - buy_cost_total
                    capital = capital + pnl
                    # Guard: prevent negative capital
                    if capital < 0:
                        capital = 0
                    trades.append(
                        {
                            "type": "buy_cover",
                            "step": i,
                            "entry_step": entry_step,
                            "entry_size": entry_size_multiplier,
                            "pnl": float(pnl),
                            "entry_price": float(entry_price),
                            "exit_price": float(exec_price),
                            "entry_notional": float(entry_cost_basis),
                        }
                    )
                    shares = 0.0
                    position = 0

                # Open new long (skip if insufficient capital)
                if target_pos == 1 and position == 0 and capital > 0 and size_multiplier > 0:
                    deploy = capital * size_multiplier
                    exec_price = price * (1 + self.slippage)
                    shares = deploy * (1 - self.commission) / exec_price
                    entry_cost_basis = deploy
                    entry_price = exec_price
                    capital -= deploy
                    entry_step = i
                    entry_size_multiplier = size_multiplier
                    trades.append(
                        {
                            "type": "buy",
                            "step": i,
                            "entry_size": entry_size_multiplier,
                            "entry_price": float(entry_price),
                            "entry_notional": float(deploy),
                        }
                    )
                    position = 1

                # Open new short
                elif target_pos == -1 and position == 0 and capital > 0 and size_multiplier > 0:
                    deploy = capital * size_multiplier
                    exec_price = price * (1 - self.slippage)
                    shares = -(deploy * (1 - self.commission) / exec_price)
                    entry_cost_basis = deploy
                    entry_price = exec_price
                    capital -= deploy * self.commission
                    entry_step = i
                    entry_size_multiplier = size_multiplier
                    trades.append(
                        {
                            "type": "sell_short",
                            "step": i,
                            "entry_size": entry_size_multiplier,
                            "entry_price": float(entry_price),
                            "entry_notional": float(deploy),
                        }
                    )
                    position = -1

            # Compute current equity
            if position == 1:
                current_equity = capital + shares * price
            elif position == -1:
                current_equity = capital + abs(shares) * (entry_price - price)
            else:
                current_equity = capital

            # Guard: clip abnormal equity
            if not np.isfinite(current_equity) or current_equity > 1e15 or current_equity < 0:
                equity.append(max(0, current_equity) if np.isfinite(current_equity) else 0)
                break
            equity.append(current_equity)

        # Settle open positions at the last price
        if position == 1:
            exec_price = prices[-1] * (1 - self.slippage)
            gross = shares * exec_price
            cost = gross * self.commission
            proceeds = gross - cost
            capital += proceeds
            pnl = proceeds - entry_cost_basis
            trades.append(
                {
                    "type": "sell_final",
                    "step": len(signals) - 1,
                    "entry_step": entry_step,
                    "entry_size": entry_size_multiplier,
                    "pnl": float(pnl),
                    "entry_price": float(entry_price),
                    "exit_price": float(exec_price),
                    "entry_notional": float(entry_cost_basis),
                }
            )
            equity[-1] = capital
        elif position == -1:
            exec_price = prices[-1] * (1 + self.slippage)
            buy_cost = abs(shares) * exec_price * (1 + self.commission)
            pnl = entry_cost_basis - buy_cost
            capital = capital + pnl
            trades.append(
                {
                    "type": "buy_cover_final",
                    "step": len(signals) - 1,
                    "entry_step": entry_step,
                    "entry_size": entry_size_multiplier,
                    "pnl": float(pnl),
                    "entry_price": float(entry_price),
                    "exit_price": float(exec_price),
                    "entry_notional": float(entry_cost_basis),
                }
            )
            equity[-1] = capital

        return np.array(equity), trades

    def _simulate_with_stop_config(
        self,
        signals: np.ndarray,
        prices: np.ndarray,
        df: pd.DataFrame | None,
        position_sizes: np.ndarray | None,
        stop_config: Dict[str, Any],
    ) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
        if position_sizes is not None:
            sizes = np.asarray(position_sizes, dtype=float)
            if len(sizes) != len(signals):
                raise ValueError("position_sizes must match signals and prices length")
            if not np.allclose(sizes, 1.0):
                raise ValueError("stop_config.base_size is the only Phase 2 position source")

        base_size = float(stop_config.get("base_size", 1.0))
        if not np.isfinite(base_size) or base_size < 0 or base_size > 1:
            raise ValueError("stop_config.base_size must be finite and in [0, 1]")

        capital = self.initial_capital
        shares = 0.0
        position = 0
        equity: List[float] = []
        trades: List[Dict[str, Any]] = []
        entry_cost_basis = 0.0
        original_entry_notional = 0.0
        entry_price = 0.0
        entry_step = -1
        entry_size_multiplier = 0.0
        parent_trade_id = -1
        remaining_fraction = 0.0
        adverse_reduced = False
        time_loss_fired: set[int] = set()
        break_even_fired = False
        mfe_since_entry = 0.0
        trade_id = 0
        peak_equity = self.initial_capital

        def mark_equity(price: float) -> float:
            if position == 1:
                return capital + shares * price
            if position == -1:
                return capital + abs(shares) * (entry_price - price)
            return capital

        def target_from_signal(signal: int) -> int:
            if signal == 2:
                return 1
            if signal == 3:
                return -1
            if signal == 0:
                return 0
            return position

        def dd_entry_size(dd_abs: float) -> float:
            dd_config = stop_config.get("equity_dd_sizing", {})
            if not dd_config.get("enabled", False):
                return base_size
            multiplier = 1.0
            for upper, tier_multiplier in sorted(dd_config.get("tiers", [])):
                multiplier = float(tier_multiplier)
                if dd_abs < float(upper):
                    break
            return base_size * multiplier

        def dd_blocks(dd_abs: float) -> tuple[bool, bool]:
            dd_config = stop_config.get("equity_dd_sizing", {})
            if not dd_config.get("enabled", False):
                return False, False
            no_new = dd_abs >= float(dd_config.get("no_new_entry_dd", 1.0))
            kill = dd_abs >= float(dd_config.get("kill_switch_dd", 1.0))
            return no_new, kill

        def next_trade_id() -> int:
            nonlocal trade_id
            trade_id += 1
            return trade_id

        def open_position(target_pos: int, i: int, price: float, size_multiplier: float) -> None:
            nonlocal adverse_reduced, break_even_fired, capital, entry_cost_basis, entry_price
            nonlocal entry_size_multiplier, mfe_since_entry
            nonlocal entry_step, original_entry_notional, parent_trade_id, position
            nonlocal remaining_fraction, shares

            if capital <= 0 or size_multiplier <= 0:
                return
            deploy = capital * size_multiplier
            tid = next_trade_id()
            if target_pos == 1:
                exec_price = price * (1 + self.slippage)
                shares = deploy * (1 - self.commission) / exec_price
                capital -= deploy
                event_type = "buy"
            else:
                exec_price = price * (1 - self.slippage)
                shares = -(deploy * (1 - self.commission) / exec_price)
                capital -= deploy * self.commission
                event_type = "sell_short"
            entry_cost_basis = deploy
            original_entry_notional = deploy
            entry_price = exec_price
            entry_step = i
            entry_size_multiplier = size_multiplier
            parent_trade_id = tid
            remaining_fraction = 1.0
            adverse_reduced = False
            break_even_fired = False
            mfe_since_entry = 0.0
            time_loss_fired.clear()
            position = target_pos
            trades.append(
                {
                    "type": event_type,
                    "step": i,
                    "entry_size": entry_size_multiplier,
                    "entry_price": float(entry_price),
                    "entry_notional": float(deploy),
                    "trade_id": tid,
                    "parent_trade_id": tid,
                }
            )

        def close_position(
            i: int,
            price: float,
            reason: str,
            fraction: float = 1.0,
            final: bool = False,
        ) -> None:
            nonlocal adverse_reduced, break_even_fired, capital, entry_cost_basis, entry_price
            nonlocal entry_size_multiplier, mfe_since_entry
            nonlocal entry_step, original_entry_notional, parent_trade_id, position
            nonlocal remaining_fraction, shares

            if position == 0 or fraction <= 0:
                return
            fraction = min(1.0, fraction)
            closed_fraction = remaining_fraction * fraction
            new_remaining = max(0.0, remaining_fraction - closed_fraction)
            if position == 1:
                exec_price = price * (1 - self.slippage)
                close_shares = shares * fraction
                gross = close_shares * exec_price
                proceeds = gross - gross * self.commission
                cost_basis_closed = entry_cost_basis * fraction
                pnl = proceeds - cost_basis_closed
                capital += proceeds
                shares -= close_shares
                event_type = "sell_final" if final else "sell"
            else:
                exec_price = price * (1 + self.slippage)
                close_shares = abs(shares) * fraction
                buy_cost = close_shares * exec_price * (1 + self.commission)
                cost_basis_closed = entry_cost_basis * fraction
                pnl = cost_basis_closed - buy_cost
                capital += pnl
                shares += close_shares
                event_type = "buy_cover_final" if final else "buy_cover"
            if capital < 0:
                capital = 0.0
            entry_cost_basis -= cost_basis_closed
            logical_closed = new_remaining <= 1e-12 or abs(shares) <= 1e-12
            trades.append(
                {
                    "type": event_type,
                    "step": i,
                    "entry_step": entry_step,
                    "entry_size": entry_size_multiplier,
                    "pnl": float(pnl),
                    "entry_price": float(entry_price),
                    "exit_price": float(exec_price),
                    "entry_notional": float(original_entry_notional),
                    "trade_id": next_trade_id(),
                    "parent_trade_id": parent_trade_id,
                    "is_partial": not logical_closed,
                    "exit_reason": reason,
                    "closed_fraction": float(closed_fraction),
                    "remaining_fraction": float(0.0 if logical_closed else new_remaining),
                    "logical_trade_closed": logical_closed,
                }
            )
            remaining_fraction = new_remaining
            if logical_closed:
                shares = 0.0
                position = 0
                entry_cost_basis = 0.0
                original_entry_notional = 0.0
                entry_price = 0.0
                entry_step = -1
                entry_size_multiplier = 0.0
                parent_trade_id = -1
                remaining_fraction = 0.0
                adverse_reduced = False
                break_even_fired = False
                mfe_since_entry = 0.0
                time_loss_fired.clear()

        def unrealized_return(price: float) -> float:
            return price / entry_price - 1.0 if position == 1 else 1.0 - price / entry_price

        def update_mfe(i: int, price: float) -> None:
            nonlocal mfe_since_entry
            if i <= entry_step:
                return
            high_value = _df_value(df, "high", i) if df is not None else None
            low_value = _df_value(df, "low", i) if df is not None else None
            if position == 1:
                favorable_price = high_value if high_value is not None else price
                mfe_since_entry = max(mfe_since_entry, favorable_price / entry_price - 1.0)
            elif position == -1:
                favorable_price = low_value if low_value is not None else price
                mfe_since_entry = max(mfe_since_entry, 1.0 - favorable_price / entry_price)

        def maybe_risk_stop(i: int, price: float) -> bool:
            nonlocal adverse_reduced, break_even_fired
            if i <= entry_step:
                return False
            unrealized = unrealized_return(price)
            actions: list[tuple[int, str, str, float, int | None]] = []

            config = stop_config.get("adverse_stop", {})
            if config.get("enabled", False) and unrealized <= float(
                config.get("threshold_pct", -0.05)
            ):
                action = str(config.get("action", "reduce_half"))
                if action == "close" or adverse_reduced:
                    actions.append((0, "close", "adverse_stop", 1.0, None))
                elif action == "reduce_quarter":
                    actions.append((2, "reduce_quarter", "adverse_stop", 0.25, None))
                else:
                    actions.append((1, "reduce_half", "adverse_stop", 0.5, None))

            time_config = stop_config.get("time_in_loss_stop", {})
            if time_config.get("enabled", False):
                bars_since_entry = i - entry_step
                for tier_index, tier in enumerate(time_config.get("tiers", [])):
                    if tier_index in time_loss_fired:
                        continue
                    bars, threshold, action = tier
                    if bars_since_entry < int(bars) or unrealized > float(threshold):
                        continue
                    action = str(action)
                    if action == "close":
                        actions.append((0, "close", "time_in_loss_stop", 1.0, tier_index))
                    elif action == "reduce_quarter":
                        actions.append((2, "reduce_quarter", "time_in_loss_stop", 0.25, tier_index))
                    else:
                        actions.append((1, "reduce_half", "time_in_loss_stop", 0.5, tier_index))

            squeeze_config = stop_config.get("squeeze_stop", {})
            if squeeze_config.get("enabled", False) and position == -1 and df is not None:
                atr_value = _df_value(df, "atr", i)
                high_value = _df_value(df, "high", i)
                if atr_value is not None and high_value is not None:
                    atr_multiple = float(squeeze_config.get("atr_multiple", 2.5))
                    squeeze_move = high_value - entry_price > atr_multiple * atr_value
                    channel_ok = True
                    if squeeze_config.get("require_channel_break", True):
                        upper = _lagged_channel_upper(df, i, squeeze_config)
                        channel_ok = upper is not None and price > upper
                    if squeeze_move and channel_ok:
                        action = str(squeeze_config.get("action", "reduce_half"))
                        if action == "close":
                            actions.append((0, "close", "squeeze_stop", 1.0, None))
                        elif action == "reduce_quarter":
                            actions.append((2, "reduce_quarter", "squeeze_stop", 0.25, None))
                        else:
                            actions.append((1, "reduce_half", "squeeze_stop", 0.5, None))

            be_config = stop_config.get("break_even_stop", {})
            if be_config.get("enabled", False) and not break_even_fired:
                trigger_mfe = float(be_config.get("trigger_mfe_pct", 0.03))
                stop_level = float(be_config.get("stop_level_pct", 0.0))
                if mfe_since_entry >= trigger_mfe and unrealized <= stop_level:
                    action = str(be_config.get("action", "reduce_half"))
                    if action == "close":
                        actions.append((0, "close", "break_even_stop", 1.0, None))
                    elif action == "reduce_quarter":
                        actions.append((2, "reduce_quarter", "break_even_stop", 0.25, None))
                    else:
                        actions.append((1, "reduce_half", "break_even_stop", 0.5, None))

            if not actions:
                return False

            _, action, reason, fraction, tier_index = sorted(actions, key=lambda item: item[0])[0]
            close_position(i, price, reason, fraction=fraction)
            if reason == "adverse_stop" and action != "close":
                adverse_reduced = True
            if reason == "break_even_stop" and action != "close":
                break_even_fired = True
            if reason == "time_in_loss_stop" and tier_index is not None:
                time_loss_fired.add(tier_index)
            return True

        for i in range(len(signals)):
            signal = int(signals[i])
            price = float(prices[i])
            target_pos = target_from_signal(signal)
            marked_equity = mark_equity(price)
            if marked_equity > peak_equity:
                peak_equity = marked_equity
            dd_abs = (peak_equity - marked_equity) / peak_equity if peak_equity > 0 else 0.0
            no_new_entry, kill_switch = dd_blocks(dd_abs)

            if position != 0:
                if kill_switch:
                    close_position(i, price, "kill_switch")
                elif (position == 1 and target_pos <= 0) or (position == -1 and target_pos >= 0):
                    close_position(i, price, "signal")
                else:
                    update_mfe(i, price)
                    maybe_risk_stop(i, price)

            if signal in (2, 3) and position == 0 and not kill_switch and not no_new_entry:
                open_position(1 if signal == 2 else -1, i, price, dd_entry_size(dd_abs))

            current_equity = mark_equity(price)
            if not np.isfinite(current_equity) or current_equity > 1e15 or current_equity < 0:
                equity.append(max(0, current_equity) if np.isfinite(current_equity) else 0)
                break
            equity.append(current_equity)

        if position == 1:
            close_position(len(signals) - 1, float(prices[-1]), "signal", final=True)
            equity[-1] = capital
        elif position == -1:
            close_position(len(signals) - 1, float(prices[-1]), "signal", final=True)
            equity[-1] = capital

        return np.array(equity), trades

    def compute_metrics(
        self,
        equity_curve: np.ndarray,
        trades: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """Compute performance metrics from an equity curve and trade list.

        Args:
            equity_curve: Account equity at each simulation step.
            trades: List of trade event dicts from ``simulate``.

        Returns:
            Dict with keys: ``total_return``, ``annualized_return``,
            ``annualized_vol``, ``sharpe_ratio``, ``max_drawdown``,
            ``win_rate``.
        """
        equity = equity_curve
        returns = np.diff(equity) / equity[:-1]

        total_return: float = (equity[-1] / equity[0]) - 1

        n_steps = len(equity)
        years = n_steps / BARS_PER_YEAR
        if years < 0.01:
            years = 0.01
        # Clamp to prevent overflow
        total_return = max(-1.0, min(100.0, total_return))
        try:
            annualized_return: float = (1 + total_return) ** (1 / years) - 1
            annualized_return = max(-10.0, min(10.0, annualized_return))
        except (OverflowError, ValueError):
            annualized_return = 0.0

        annualized_vol: float = np.std(returns) * math.sqrt(288 * 365) if len(returns) > 0 else 0
        sharpe: float = annualized_return / annualized_vol if annualized_vol > 0 else 0

        peak = equity[0]
        max_drawdown: float = 0.0
        for e in equity:
            if e > peak:
                peak = e
            dd = (e - peak) / peak
            if dd < max_drawdown:
                max_drawdown = dd

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        total_trades = len(trade_pnls)
        winning_trades = len([t for t in trade_pnls if t["pnl"] > 0])
        win_rate: float = winning_trades / total_trades if total_trades > 0 else 0.5

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
        }

    def evaluate(
        self,
        signals: np.ndarray,
        prices: np.ndarray,
        df: pd.DataFrame | None = None,
        position_sizes: np.ndarray | None = None,
    ) -> Tuple[float, Dict[str, float], List[Dict[str, Any]]]:
        """Evaluate a set of signals and return a composite score.

        Guards against: invalid equity curves, drawdown > 30%, equity < 70%
        of initial capital, and negative total return. Penalises large
        drawdowns and low trade counts.

        Args:
            signals: Integer array of trading signals.
            prices: Array of close prices.
            df: Optional DataFrame (unused; kept for API compatibility).
            position_sizes: Optional per-bar entry size multipliers.

        Returns:
            A tuple ``(score, metrics, trades)`` where ``score`` is a
            composite float in [0, 1], ``metrics`` is the dict from
            ``compute_metrics``, and ``trades`` is the trade list.
        """
        equity, trades = self.simulate(signals, prices, df, position_sizes=position_sizes)

        # Guard: invalid equity curve
        if len(equity) == 0 or not np.all(np.isfinite(equity)):
            return (
                0.0,
                {
                    "total_return": 0,
                    "annualized_return": 0,
                    "annualized_vol": 0,
                    "sharpe_ratio": 0,
                    "max_drawdown": -0.99,
                    "win_rate": 0,
                },
                [],
            )

        metrics = self.compute_metrics(equity, trades)

        # Guard: invalid metrics
        if not np.isfinite(metrics["sharpe_ratio"]) or not np.isfinite(metrics["total_return"]):
            return 0.0, metrics, trades

        # Guard: drawdown exceeds 30%
        if metrics["max_drawdown"] < -EVAL_MAX_DRAWDOWN:
            return 0.0, metrics, trades

        # Guard: final equity below 70% of initial
        if equity[-1] < self.initial_capital * EVAL_MIN_EQUITY_RATIO:
            return 0.0, metrics, trades

        # Guard: must be profitable (or near break-even)
        if metrics["total_return"] <= EVAL_MIN_RETURN:
            return 0.0, metrics, trades

        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)

        # Penalty for large drawdown
        dd_penalty = (
            max(0, 1 - abs(metrics["max_drawdown"]) / 0.20) if metrics["max_drawdown"] < 0 else 1.0
        )

        # Penalty for insufficient trades
        min_trade_penalty = (
            min(1.0, n_trades / float(EVAL_MIN_TRADES)) if n_trades < EVAL_MIN_TRADES else 1.0
        )

        # Clamp metrics to reasonable ranges
        sharpe_clamped = max(0, min(5.0, metrics["sharpe_ratio"]))
        return_clamped = max(0, min(2.0, metrics["total_return"]))
        win_rate_clamped = max(0, min(1.0, metrics["win_rate"]))

        score = (
            sharpe_clamped * 0.25
            + return_clamped * 0.15
            + win_rate_clamped * 0.10
            + dd_penalty * (1 + metrics["max_drawdown"]) * 0.15
            + min(1.0, n_trades / 40.0) * 0.25
            + min_trade_penalty * 0.10
        )

        return score, metrics, trades


def scalp_evaluate(
    signals: np.ndarray,
    prices: np.ndarray,
    evaluator: StrategyEvaluator,
    min_trades: int = 50,
) -> Tuple[float, Dict[str, float], List[Dict[str, Any]]]:
    """Score function specialised for scalping (high-frequency) strategies.

    Rewards trade volume, consistency of per-trade PnL, and moderate returns
    rather than chasing high Sharpe ratios.  Requires at least ``min_trades``
    profitable trades to produce a non-zero score.

    Args:
        signals: Integer array of trading signals.
        prices: Array of close prices.
        evaluator: Pre-configured ``StrategyEvaluator`` instance.
        min_trades: Minimum number of trades required (default 50).

    Returns:
        A tuple ``(score, metrics, trades)``, same shape as
        ``StrategyEvaluator.evaluate``.
    """
    equity, trades = evaluator.simulate(signals, prices)

    if len(equity) == 0 or not np.all(np.isfinite(equity)):
        return (
            0.0,
            {
                "total_return": 0,
                "annualized_return": 0,
                "annualized_vol": 0,
                "sharpe_ratio": 0,
                "max_drawdown": -0.99,
                "win_rate": 0,
            },
            [],
        )

    metrics = evaluator.compute_metrics(equity, trades)

    if not np.isfinite(metrics["sharpe_ratio"]) or not np.isfinite(metrics["total_return"]):
        return 0.0, metrics, trades
    if metrics["max_drawdown"] < -EVAL_MAX_DRAWDOWN:
        return 0.0, metrics, trades
    if equity[-1] < evaluator.initial_capital * EVAL_MIN_EQUITY_RATIO:
        return 0.0, metrics, trades

    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)

    if n_trades < min_trades:
        return 0.0, metrics, trades

    # Trade-count score (30%): 100 trades = full points
    trade_count_score = min(1.0, n_trades / 100.0)

    # Consistency score (25%): mean PnL / std of PnL
    pnls = [t["pnl"] for t in trade_pnls]
    pnl_mean = np.mean(pnls)
    pnl_std = np.std(pnls)
    consistency = pnl_mean / pnl_std if pnl_std > 0 else 0
    consistency_score = max(0, min(1.0, consistency / 2.0))

    # Win-rate score (20%): 40% base, 80% max
    win_rate_score = max(0, min(1.0, (metrics["win_rate"] - 0.40) / 0.40))

    # Return score (15%): 5% return = full points
    return_score = max(0, min(1.0, metrics["total_return"] / 0.05))

    # Drawdown score (10%)
    dd_score = max(0, 1 + metrics["max_drawdown"]) if metrics["max_drawdown"] < 0 else 1.0

    score = (
        trade_count_score * 0.30
        + consistency_score * 0.25
        + win_rate_score * 0.20
        + return_score * 0.15
        + dd_score * 0.10
    )

    return score, metrics, trades


def analyze_market_regime(
    df: pd.DataFrame,
) -> Tuple[str, Dict[str, Any]]:
    """Quickly classify the current market trend using EMA and ADX.

    Uses EMA-50 / EMA-200 cross and a simplified 14-period ADX to determine
    whether the market is trending strongly, weakly, or ranging.

    Args:
        df: DataFrame with columns ``open``, ``high``, ``low``, ``close``,
            ``volume``, indexed by timestamp.

    Returns:
        A tuple ``(regime_name, info_dict)`` where ``regime_name`` is one
        of ``"strong_uptrend"``, ``"weak_uptrend"``, ``"ranging"``,
        ``"weak_downtrend"``, ``"strong_downtrend"`` and ``info_dict``
        contains ``regime``, ``adx``, ``ema50_vs_ema200``,
        ``price_vs_ema200_pct``, and ``volatility_annualized``.
    """
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    n = len(close)

    # EMA-50 / EMA-200
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)

    # Simplified ADX (14-period)
    period = 14
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0
        minus_dm[i] = down if down > up and down > 0 else 0

    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]
    atr = np.zeros(n)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    for i in range(period, n):
        if atr[i] > 0:
            plus_di[i] = 100 * np.mean(plus_dm[i - period + 1 : i + 1]) / atr[i]
            minus_di[i] = 100 * np.mean(minus_dm[i - period + 1 : i + 1]) / atr[i]

    dx = np.zeros(n)
    for i in range(period, n):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum

    adx = np.zeros(n)
    adx[period * 2 - 1] = np.mean(dx[period : period * 2])
    for i in range(period * 2, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    adx_val = adx[-1]
    is_uptrend = ema50[-1] > ema200[-1]
    is_downtrend = ema50[-1] < ema200[-1]
    price_dev = (close[-1] - ema200[-1]) / ema200[-1] * 100

    # 7-day annualised volatility
    returns = np.diff(close) / close[:-1]
    if len(returns) >= 288 * 7:
        vol = np.std(returns[-288 * 7 :]) * np.sqrt(288 * 365)
    else:
        vol = np.std(returns) * np.sqrt(288 * 365)

    # Trend classification
    if adx_val > 25:
        if is_uptrend:
            regime = "strong_uptrend"
        elif is_downtrend:
            regime = "strong_downtrend"
        else:
            regime = "strong_uptrend" if price_dev > 0 else "strong_downtrend"
    elif adx_val > 15:
        if is_uptrend:
            regime = "weak_uptrend"
        elif is_downtrend:
            regime = "weak_downtrend"
        else:
            regime = "weak_uptrend" if price_dev > 0 else "weak_downtrend"
    else:
        regime = "ranging"

    info: Dict[str, Any] = {
        "regime": regime,
        "adx": float(adx_val),
        "ema50_vs_ema200": (
            "uptrend" if is_uptrend else "downtrend" if is_downtrend else "neutral"
        ),
        "price_vs_ema200_pct": float(price_dev),
        "volatility_annualized": float(vol),
    }
    return regime, info
