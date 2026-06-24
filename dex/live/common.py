"""
Shared utilities for live trading scripts.

Extracted from live_nado_quant.py and live_okx_quant.py to eliminate
~300 lines of duplicated code.
"""

import functools
import json
import math
import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from email.message import EmailMessage
from typing import Any, Callable, Optional, Tuple

import pandas as pd

from dex.regime_filter import apply_regime_short_filter, build_daily_regime_labels
from dex.strategy_signals import generate_strategy_signals


@dataclass(frozen=True)
class EntryOrderPlan:
    """Pure decision output for opening a new live position."""

    action: str
    side: Optional[str] = None
    position_side: Optional[str] = None
    size: float = 0.0
    price: Optional[float] = None
    notional: float = 0.0
    target_position: int = 0
    trade_type: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class CloseOrderPlan:
    """Pure decision output for closing an existing live position."""

    action: str
    side: Optional[str] = None
    position_side: Optional[str] = None
    size: float = 0.0
    price: Optional[float] = None
    pnl_pct: float = 0.0
    trade_type: Optional[str] = None
    reason: Optional[str] = None


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def send_email_notification(
    subject: str,
    body: str,
    *,
    to_addr: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> bool:
    """Send a best-effort SMTP notification without interrupting trading."""
    recipient = to_addr or os.environ.get("TRADE_NOTIFY_EMAIL_TO", "")
    if not recipient:
        return False

    smtp_user = os.environ.get("TRADE_NOTIFY_SMTP_USER", "")
    smtp_password = os.environ.get("TRADE_NOTIFY_SMTP_PASSWORD", "")
    if not smtp_user or not smtp_password:
        if log_fn:
            log_fn("[邮件通知跳过] 未配置 TRADE_NOTIFY_SMTP_USER / TRADE_NOTIFY_SMTP_PASSWORD")
        return False

    smtp_host = os.environ.get("TRADE_NOTIFY_SMTP_HOST", "smtp.163.com")
    smtp_port = int(os.environ.get("TRADE_NOTIFY_SMTP_PORT", "465"))
    smtp_from = os.environ.get("TRADE_NOTIFY_EMAIL_FROM", smtp_user)
    timeout = float(os.environ.get("TRADE_NOTIFY_SMTP_TIMEOUT", "10"))
    use_ssl = env_bool("TRADE_NOTIFY_SMTP_SSL", smtp_port == 465)
    use_starttls = env_bool("TRADE_NOTIFY_SMTP_STARTTLS", not use_ssl)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = recipient
    msg.set_content(body)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout) as smtp:
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as smtp:
                if use_starttls:
                    smtp.starttls()
                smtp.login(smtp_user, smtp_password)
                smtp.send_message(msg)
        if log_fn:
            log_fn(f"[邮件通知] 已发送: {subject} -> {recipient}")
        return True
    except Exception as exc:
        if log_fn:
            log_fn(f"[邮件通知失败] {subject}: {exc}")
        return False


def send_trade_notification(
    action: str,
    details: dict[str, Any],
    *,
    to_addr: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> bool:
    symbol = details.get("symbol", "")
    mode = details.get("mode", "")
    subject_parts = ["AutoCrypto"]
    if mode:
        subject_parts.append(str(mode))
    if symbol:
        subject_parts.append(str(symbol))
    subject_parts.append(action)
    subject = " | ".join(subject_parts)

    lines = [f"动作: {action}", f"时间: {datetime.now().isoformat(timespec='seconds')}"]
    for key, value in details.items():
        if value is None:
            continue
        lines.append(f"{key}: {value}")
    body = "\n".join(lines)
    return send_email_notification(subject, body, to_addr=to_addr, log_fn=log_fn)


# ---------------------------------------------------------------------------
# Process lock (prevents duplicate instances)
# ---------------------------------------------------------------------------


def acquire_lock(lock_path: str) -> int:
    """Acquire an exclusive file lock to prevent duplicate process instances.

    Args:
        lock_path: Path to the lock file.

    Returns:
        File descriptor for the lock.

    Raises:
        SystemExit: If another instance is already running.
    """
    try:
        import msvcrt

        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            print("错误: 已有另一个实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd
    except ImportError:
        import fcntl

        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            print("错误: 已有另一个实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(path: str) -> Optional[dict]:
    """Load trading state from a JSON file.

    Args:
        path: Path to the state JSON file.

    Returns:
        State dict, or None if the file does not exist.
    """
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state: dict, path: str) -> None:
    """Save trading state to a JSON file.

    Args:
        state: State dictionary to persist.
        path: Path to the state JSON file.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


FEE_FIELD_NAMES = (
    "fee",
    "feeCcy",
    "fillFee",
    "fillFeeCcy",
    "follFee",
    "tradeFee",
    "tradeFeeCcy",
    "rebate",
    "rebateCcy",
    "pnl",
)


def extract_fee_fields(order: Any) -> dict[str, Any]:
    """Keep exchange fee fields from an order/fill response."""
    if not isinstance(order, dict):
        return {}

    fee_fields: dict[str, Any] = {}
    sources = [order]
    info = order.get("info")
    if isinstance(info, dict):
        sources.append(info)

    for source in sources:
        for key in FEE_FIELD_NAMES:
            value = source.get(key)
            if value not in (None, ""):
                fee_fields[key] = value

    fees = order.get("fees")
    if fees:
        fee_fields["fees"] = fees
    return fee_fields


def attach_fee_fields_to_trade(
    trades: list[dict[str, Any]], order_id: Any, fee_fields: dict[str, Any]
) -> bool:
    """Merge fee fields into the newest trade with the same order id."""
    if not order_id or not fee_fields:
        return False

    target = str(order_id)
    for trade in reversed(trades):
        if str(trade.get("orderId")) == target:
            trade.update(fee_fields)
            return True
    return False


def append_funding_fee_record(
    state: dict[str, Any], record: dict[str, Any], max_records: int = 100
) -> bool:
    record_id = record.get("id")
    if not record_id:
        return False

    records = state.setdefault("funding_fee_records", [])
    target = str(record_id)
    if any(str(item.get("id")) == target for item in records):
        return False

    records.append(record)
    if len(records) > max_records:
        del records[:-max_records]
    return True


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TradeLogger:
    """Simple file + console logger for live trading."""

    def __init__(self, log_path: str):
        """Initialize logger with a file path.

        Args:
            log_path: Path to the log file (appended mode).
        """
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    def log(self, msg: str) -> None:
        """Write a timestamped message to console and log file.

        Args:
            msg: Message string.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {msg}"
        print(line)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------


def retry_on_exception(
    max_retries: int = 3,
    delay: float = 1.0,
    exceptions: Tuple[type, ...] = (Exception,),
    logger: Optional[TradeLogger] = None,
) -> Callable:
    """Decorator: retry a function on specified exceptions.

    Args:
        max_retries: Maximum number of attempts.
        delay: Seconds to wait between retries.
        exceptions: Tuple of exception classes to catch.
        logger: Optional TradeLogger for retry messages.

    Returns:
        Decorated function wrapper.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    msg = f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}，{delay}s 后重试..."
                    if logger:
                        logger.log(msg)
                    else:
                        print(msg)
                    time.sleep(delay)
            return None

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Time alignment
# ---------------------------------------------------------------------------


def align_next_wake_time(interval_seconds: int, offset_seconds: int = 15) -> datetime:
    """Calculate the next wake time aligned to interval boundaries.

    Args:
        interval_seconds: Interval in seconds (e.g. 300 for 5m).
        offset_seconds: Extra seconds to add after the boundary.

    Returns:
        Datetime of the next wake time.
    """
    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake_ts = next_boundary + offset_seconds
    return epoch + timedelta(seconds=next_wake_ts)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


def apply_signal_filter(
    signals,
    df: pd.DataFrame,
    signal_filter: dict,
) -> Tuple[Any, dict]:
    filter_type = str(signal_filter.get("type", ""))
    if filter_type != "regime_short_filter":
        raise ValueError(f"Unsupported signal_filter type: {filter_type}")

    fast_days = int(signal_filter.get("fast_days", 50))
    slow_days = int(signal_filter.get("slow_days", 200))
    regimes = build_daily_regime_labels(df, fast_days=fast_days, slow_days=slow_days)
    filtered, _ = apply_regime_short_filter(signals, regimes, df)
    i = len(filtered) - 1
    return filtered, {
        "signal_filter": filter_type,
        "regime": str(regimes[i]),
        "raw_signal": int(signals[i]),
        "filtered_signal": int(filtered[i]),
        "regime_fast_days": fast_days,
        "regime_slow_days": slow_days,
    }


def predict_signal(
    strategy: Any,
    df: pd.DataFrame,
    enable_short: bool = False,
    signal_filter: Optional[dict] = None,
) -> Tuple[int, dict]:
    """Generate trading signal and strategy display info.

    Args:
        strategy: Strategy object with ``generate_signals`` and ``window``,
                  ``std_dev`` attributes.
        df: OHLCV DataFrame with at least a 'close' column.
        enable_short: Whether short signals are allowed.

    Returns:
        Tuple of (signal_id, signal_info). signal_id: 0=close, 1=hold,
        2=long, 3=short.
    """
    signals = generate_strategy_signals(strategy, df, enable_short=enable_short)
    filter_info = {}
    if signal_filter:
        signals, filter_info = apply_signal_filter(signals, df, signal_filter)
    signal_id = int(signals[-1])

    close = df["close"].values
    if hasattr(strategy, "entry_lookback") and {"high", "low"}.issubset(df.columns):
        window = max(1, int(getattr(strategy, "entry_lookback")))
        high = df["high"].values
        low = df["low"].values
        upper = pd.Series(high).rolling(window=window, min_periods=window).max().shift(1)
        lower = pd.Series(low).rolling(window=window, min_periods=window).min().shift(1)
        mid = (upper + lower) / 2.0
        label = "Donchian通道"
    else:
        window = max(1, int(strategy.window))
        mid = pd.Series(close).rolling(window=window, min_periods=window).mean()
        rolling_std = pd.Series(close).rolling(window=window, min_periods=window).std()
        upper = mid + strategy.std_dev * rolling_std
        lower = mid - strategy.std_dev * rolling_std
        label = "布林带"

    signal_info = {
        "label": label,
        "price": float(close[-1]),
        "upper": float(upper.iloc[-1]),
        "mid": float(mid.iloc[-1]),
        "lower": float(lower.iloc[-1]),
    }
    signal_info.update(filter_info)
    return signal_id, signal_info


# ---------------------------------------------------------------------------
# Price utilities
# ---------------------------------------------------------------------------


def round_to_tick(price: float, tick_size: float) -> float:
    """Round a price to the nearest valid tick increment.

    Args:
        price: Raw price value.
        tick_size: Minimum price increment.

    Returns:
        Price rounded to the nearest tick.
    """
    ticks = round(float(price) / float(tick_size))
    return float(Decimal(ticks) * Decimal(str(tick_size)))


def compute_order_price(
    side: str, best_bid: Optional[float], best_ask: Optional[float], tick_size: float
) -> Optional[float]:
    """Compute a POST_ONLY limit order price at the best bid/ask.

    Args:
        side: 'buy' or 'sell'.
        best_bid: Current best bid price.
        best_ask: Current best ask price.
        tick_size: Minimum price increment.

    Returns:
        Limit order price aligned to tick_size.
    """
    if side == "buy":
        if best_bid is None:
            return None
        price = best_bid
        if best_ask is not None and price >= best_ask:
            price = best_ask - float(tick_size)
    else:
        if best_ask is None:
            return None
        price = best_ask
        if best_bid is not None and price <= best_bid:
            price = best_bid + float(tick_size)
    return round_to_tick(price, tick_size)


def compute_ioc_price(
    side: str, best_bid: float, best_ask: float, tick_size: float
) -> Optional[float]:
    """Compute an IOC (Taker) order price that crosses the spread.

    Args:
        side: 'buy' or 'sell'.
        best_bid: Current best bid price.
        best_ask: Current best ask price.
        tick_size: Minimum price increment.

    Returns:
        IOC order price, or None if prices are unavailable.
    """
    if side == "buy":
        price = best_ask + float(tick_size) * 2 if best_ask else None
    else:
        price = best_bid - float(tick_size) * 2 if best_bid else None
    if price is not None:
        price = round_to_tick(price, tick_size)
    return price


def target_position_from_signal(signal_id: int, current_position: int) -> int:
    """Map strategy signal IDs to target position direction."""
    if signal_id == 2:
        return 1
    if signal_id == 3:
        return -1
    if signal_id == 0:
        return 0
    return current_position


def quantize_order_size(
    capital_per_trade: float, current_price: float, size_increment: float
) -> float:
    """Floor order size to the exchange size increment."""
    if capital_per_trade <= 0 or current_price <= 0 or size_increment <= 0:
        return 0.0

    raw_size = Decimal(str(capital_per_trade)) / Decimal(str(current_price))
    units = int(raw_size / Decimal(str(size_increment)))
    return float(units * Decimal(str(size_increment)))


def quantize_position_size(size: float, size_increment: float) -> float:
    """Floor an existing position size to the exchange size increment."""
    if size <= 0 or size_increment <= 0:
        return 0.0
    units = int(Decimal(str(size)) / Decimal(str(size_increment)))
    return float(units * Decimal(str(size_increment)))


def plan_entry_order(
    *,
    target_position: int,
    current_position: int,
    current_price: float,
    capital_per_trade: float,
    size_increment: float,
    tick_size: float,
    best_bid: Optional[float],
    best_ask: Optional[float],
    force_ioc: bool = False,
    min_notional: float = 0.0,
    min_maker_notional: float = 0.0,
    fallback_to_current_price: bool = False,
) -> EntryOrderPlan:
    """Plan a new-position order without calling any exchange API."""
    if target_position not in {-1, 1} or current_position != 0:
        return EntryOrderPlan(action="skip", reason="no_entry_required")
    if current_price <= 0:
        return EntryOrderPlan(action="skip", reason="invalid_price")

    size = quantize_order_size(capital_per_trade, current_price, size_increment)
    notional = size * current_price
    if size <= 0:
        return EntryOrderPlan(action="skip", reason="zero_size", notional=notional)
    if min_notional > 0 and notional < min_notional:
        return EntryOrderPlan(
            action="skip",
            size=size,
            notional=notional,
            target_position=target_position,
            reason="notional_below_minimum",
        )

    side = "buy" if target_position == 1 else "sell"
    position_side = "long" if target_position == 1 else "short"
    use_ioc = force_ioc or (min_maker_notional > 0 and notional < min_maker_notional)
    action = "ioc" if use_ioc else "maker"

    if use_ioc:
        price = (
            compute_ioc_price(side, best_bid, best_ask, tick_size)
            if best_bid is not None and best_ask is not None
            else None
        )
        if price is None and fallback_to_current_price:
            multiplier = 1.001 if side == "buy" else 0.999
            price = round_to_tick(current_price * multiplier, tick_size)
        trade_type = "BUY_OPEN_IOC_FALLBACK" if target_position == 1 else "SELL_SHORT_IOC_FALLBACK"
    else:
        price = compute_order_price(side, best_bid, best_ask, tick_size)
        if price is None and fallback_to_current_price:
            multiplier = 0.999 if side == "buy" else 1.001
            price = round_to_tick(current_price * multiplier, tick_size)
        trade_type = "BUY_OPEN_MAKER" if target_position == 1 else "SELL_SHORT_MAKER"

    if price is None:
        return EntryOrderPlan(
            action="skip",
            side=side,
            position_side=position_side,
            size=size,
            notional=notional,
            target_position=target_position,
            reason="missing_quote",
        )

    return EntryOrderPlan(
        action=action,
        side=side,
        position_side=position_side,
        size=size,
        price=price,
        notional=notional,
        target_position=target_position,
        trade_type=trade_type,
    )


def plan_close_order(
    *,
    current_position: int,
    target_position: int,
    strategy_size: float,
    actual_position: float,
    entry_price: float,
    current_price: float,
    size_increment: float,
    tick_size: float,
    best_bid: Optional[float],
    best_ask: Optional[float],
    fallback_to_current_price: bool = False,
) -> CloseOrderPlan:
    """Plan an existing-position close order without calling any exchange API."""
    if current_position == 1 and target_position <= 0:
        side = "sell"
        position_side = "long"
        raw_size = (
            min(strategy_size, abs(actual_position)) if actual_position > 0 else strategy_size
        )
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
        prefix = "CLOSE_LONG"
    elif current_position == -1 and target_position >= 0:
        side = "buy"
        position_side = "short"
        raw_size = (
            min(strategy_size, abs(actual_position)) if actual_position < 0 else strategy_size
        )
        pnl_pct = (entry_price - current_price) / entry_price if entry_price > 0 else 0.0
        prefix = "CLOSE_SHORT"
    else:
        return CloseOrderPlan(action="skip", reason="no_close_required")

    size = quantize_position_size(raw_size, size_increment)
    if size <= 0:
        return CloseOrderPlan(
            action="skip",
            side=side,
            position_side=position_side,
            pnl_pct=round(pnl_pct, 10),
            reason="zero_size",
        )

    close_as_maker = pnl_pct > 0
    action = "maker" if close_as_maker else "taker"
    close_type = "Maker(TP)" if close_as_maker else "Taker(SL)"
    if close_as_maker:
        price = compute_order_price(side, best_bid, best_ask, tick_size)
    else:
        price = (
            compute_ioc_price(side, best_bid, best_ask, tick_size)
            if best_bid is not None and best_ask is not None
            else None
        )

    if price is None and fallback_to_current_price:
        multiplier = 1.001 if side == "buy" else 0.999
        price = round_to_tick(current_price * multiplier, tick_size)

    if price is None:
        return CloseOrderPlan(
            action="skip",
            side=side,
            position_side=position_side,
            size=size,
            pnl_pct=round(pnl_pct, 10),
            reason="missing_quote",
        )

    return CloseOrderPlan(
        action=action,
        side=side,
        position_side=position_side,
        size=size,
        price=price,
        pnl_pct=round(pnl_pct, 10),
        trade_type=f"{prefix}_{close_type}",
    )


# ---------------------------------------------------------------------------
# Stop-loss / time-exit check
# ---------------------------------------------------------------------------


def check_stop_loss(
    state: dict,
    current_price: float,
    kline_low: Optional[float] = None,
    kline_high: Optional[float] = None,
    stop_loss_pct: float = 0.03,
    max_hold_bars: int = 48,
    logger: Optional[TradeLogger] = None,
) -> Tuple[bool, str]:
    """Check if stop-loss or time-based exit conditions are triggered.

    Supports both long and short positions. Uses K-line high/low for
    intra-bar penetration detection before falling back to close price.

    Args:
        state: Trading state dict (position, entry_price, entry_bar,
               confirmed_entry_bar, bar_count, pending_close).
        current_price: Current bar close price.
        kline_low: Current bar low (for long stop detection).
        kline_high: Current bar high (for short stop detection).
        stop_loss_pct: Stop-loss percentage as decimal (e.g. 0.03 = 3%).
        max_hold_bars: Maximum number of bars to hold before time exit.
        logger: Optional TradeLogger for anomaly warnings.

    Returns:
        Tuple of (should_exit: bool, reason: str).
    """
    pos = state.get("position", 0)
    if pos == 0:
        return False, ""

    if state.get("pending_close"):
        return False, ""

    entry_price = state.get("entry_price", 0)
    entry_bar = state.get("confirmed_entry_bar", 0) or state.get("entry_bar", 0)
    current_bar = state.get("bar_count", 0)

    if pos == 1:
        if entry_price > 0:
            stop_price = entry_price * (1 - stop_loss_pct)
            if kline_low is not None and kline_low <= stop_price:
                return True, f"多头止损: K线low={kline_low:.2f} <= 止损价={stop_price:.2f}"
            if (entry_price - current_price) / entry_price >= stop_loss_pct:
                return (
                    True,
                    f"多头止损: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
                )
    elif pos == -1:
        if entry_price > 0:
            stop_price = entry_price * (1 + stop_loss_pct)
            if kline_high is not None and kline_high >= stop_price:
                return True, f"空头止损: K线high={kline_high:.2f} >= 止损价={stop_price:.2f}"
            if (current_price - entry_price) / entry_price >= stop_loss_pct:
                return (
                    True,
                    f"空头止损: 涨幅 {(current_price - entry_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
                )

    bars_held = current_bar - entry_bar
    if entry_bar > 0 and bars_held > max_hold_bars * 2:
        if logger:
            logger.log(
                f"[异常报警] 持仓 K 线数 {bars_held} 超过 2*max_hold={max_hold_bars * 2}，跳过时间退出判断"
            )
        return False, ""

    if bars_held >= max_hold_bars:
        pos_name = "多头" if pos == 1 else "空头"
        return True, f"{pos_name}时间退出: 持仓 {bars_held} 根K线 >= {max_hold_bars}"

    return False, ""
