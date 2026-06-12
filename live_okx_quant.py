"""
OKX Agent Trade Kit 实盘/模拟盘交易脚本（永续合约 SWAP）。
基于混合费率执行策略，每 5 分钟获取信号并自动下单。

混合费率执行策略:
    - 开仓: POST_ONLY (Maker) -- 挂限价单，享Maker低费率
    - 止盈: POST_ONLY (Maker) -- 自动挂止盈限价单，价格到达即成交
    - 止损: IOC (Taker) -- 必须保证成交，付Taker费率
    - 超时: POST_ONLY (Maker) -- 挂限价单平仓

Usage:
    # Demo Trading aggressive ChannelBreakout v2 preset
    powershell -ExecutionPolicy Bypass -File scripts\run_okx_channel_breakout_v2_demo.ps1

    # Demo Trading one-shot check before keeping it running
    powershell -ExecutionPolicy Bypass -File scripts\run_okx_channel_breakout_v2_demo.ps1 -Once

    # 实盘交易（真钱！确认策略稳定后再用）
    export OKX_API_KEY="your_api_key"
    export OKX_API_SECRET="your_api_secret"
    export OKX_PASSPHRASE="your_passphrase"
    uv run python live_okx_quant.py --symbol BTC-USDT-SWAP --interval 5m --live --capital 500 --leverage 2

注意：
    - 默认使用 Demo Trading（--demo），不会损失真实资金
    - 切换到实盘前，务必先用 Demo 跑至少 1-2 天验证信号和下单逻辑
    - 本脚本交易 OKX 永续合约（SWAP），支持做多/做空双向
"""

import argparse
import functools
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
from okx.Account import AccountAPI
from okx.MarketData import MarketAPI
from okx.PublicData import PublicAPI
from okx.Trade import TradeAPI

from dex.checkpoints import (
    build_strategy_from_checkpoint,
    describe_strategy,
    load_checkpoint,
)
from dex.data import list_crypto_files, load_crypto_data
from dex.live.common import (
    compute_order_price,
    plan_close_order,
    plan_entry_order,
    predict_signal,
    round_to_tick,
    send_trade_notification,
)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
STATE_FILE = os.path.join(LOG_DIR, "live_okx_state.json")
LOG_FILE = os.path.join(LOG_DIR, "live_okx_log.txt")
LOCK_FILE = os.path.join(LOG_DIR, "live_okx_quant.lock")

OKX_STRATEGY_PROFILES = {
    "channel_breakout_v2": "checkpoints/channel_breakout_375_432.pt",
    "channel_breakout": "checkpoints/eth_optimal.pt",
    "hybrid_mm": "checkpoints/quant_model.pt",
}

INTERVAL_SECONDS_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

try:
    import msvcrt

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            print("错误: 已有另一个 live_okx_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd
except ImportError:
    # Unix fallback (just in case)
    import fcntl

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            print("错误: 已有另一个 live_okx_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def resolve_checkpoint_path(checkpoint_path: str | None, strategy_profile: str) -> str:
    """Resolve the checkpoint used by the OKX live entrypoint."""
    if checkpoint_path:
        return checkpoint_path
    try:
        return OKX_STRATEGY_PROFILES[strategy_profile]
    except KeyError as exc:
        known = ", ".join(sorted(OKX_STRATEGY_PROFILES))
        raise ValueError(f"Unknown strategy profile {strategy_profile!r}; expected one of: {known}") from exc


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log_message(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify_trade_action(action, *, notify_email_to=None, mode=None, symbol=None, **details):
    if not notify_email_to:
        return False
    payload = {
        "exchange": "OKX",
        "mode": mode,
        "symbol": symbol,
        **details,
    }
    return send_trade_notification(
        action,
        payload,
        to_addr=notify_email_to,
        log_fn=log_message,
    )


def okx_float(value, default=0.0):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def retry_on_exception(max_retries=3, delay=1.0, exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    log_message(
                        f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}，{delay}s 后重试..."
                    )
                    time.sleep(delay)
            return None

        return wrapper

    return decorator


def align_next_wake_time(interval_seconds, offset_seconds=10):
    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake_ts = next_boundary + offset_seconds
    return epoch + timedelta(seconds=next_wake_ts)


# ---------------------------------------------------------------------------
# OKX API 初始化
# ---------------------------------------------------------------------------


def get_okx_credentials():
    """从环境变量读取 API 凭证"""
    key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_API_SECRET", "")
    passphrase = os.environ.get("OKX_PASSPHRASE", "")
    return key, secret, passphrase


def init_okx_api(flag="1"):
    """初始化 OKX API"""
    key, secret, passphrase = get_okx_credentials()
    if not all([key, secret, passphrase]):
        log_message("错误: 未设置 OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE 环境变量")
        sys.exit(1)

    proxy = os.environ.get("OKX_HTTP_PROXY") or None
    account_api = AccountAPI(key, secret, passphrase, flag=flag, debug=False, proxy=proxy)
    trade_api = TradeAPI(key, secret, passphrase, flag=flag, debug=False, proxy=proxy)
    market_api = MarketAPI(flag=flag, debug=False, proxy=proxy)
    public_api = PublicAPI(debug=False, proxy=proxy)
    log_message(f"使用 HTTP 代理: {proxy if proxy else '未启用'}")
    return account_api, trade_api, market_api, public_api


def init_okx_public_api(flag="1"):
    """初始化只读 OKX 公共行情 API，用于 signal-only 观察模式。"""
    proxy = os.environ.get("OKX_HTTP_PROXY") or None
    market_api = MarketAPI(flag=flag, debug=False, proxy=proxy)
    public_api = PublicAPI(debug=False, proxy=proxy)
    log_message(f"使用 HTTP 代理: {proxy if proxy else '未启用'}")
    return market_api, public_api


# ---------------------------------------------------------------------------
# OKX API 封装
# ---------------------------------------------------------------------------


@retry_on_exception(max_retries=3, delay=1.0)
def get_balance(account_api, ccy="USDT"):
    """查询指定币种余额"""
    resp = account_api.get_account_balance(ccy=ccy)
    if resp.get("code") == "0" and resp.get("data"):
        details = resp["data"][0].get("details", [])
        for item in details:
            if item.get("ccy") == ccy:
                avail = float(item.get("availBal", 0))
                eq = float(item.get("eq", 0))
                return avail, eq
    return 0.0, 0.0


@retry_on_exception(max_retries=3, delay=1.0)
def get_position(account_api, inst_id):
    """获取永续合约持仓（正=多, 负=空, 0=无）"""
    resp = account_api.get_positions(instType="SWAP", instId=inst_id)
    if resp.get("code") == "0" and resp.get("data"):
        pos = resp["data"][0]
        pos_side = pos.get("posSide", "net")
        pos_val = float(pos.get("pos", "0"))
        if pos_side == "short":
            pos_val = -abs(pos_val)
        return pos_val
    return 0.0


@retry_on_exception(max_retries=3, delay=1.0)
def set_leverage(account_api, inst_id, lever, mgn_mode="cross"):
    """设置合约杠杆"""
    resp = account_api.set_leverage(instId=inst_id, lever=str(lever), mgnMode=mgn_mode)
    if resp.get("code") == "0":
        log_message(f"杠杆设置成功: {inst_id} {mgn_mode} {lever}x")
        return True
    else:
        log_message(f"杠杆设置失败: {resp}")
        return False


@retry_on_exception(max_retries=3, delay=1.0)
def get_instrument_info(public_api, inst_id):
    """获取交易对信息 (tickSz, lotSz, minSz)"""
    resp = public_api.get_instruments(instType="SWAP", instId=inst_id)
    if resp.get("code") == "0" and resp.get("data"):
        info = resp["data"][0]
        return {
            "tickSz": float(info.get("tickSz", "0.01")),
            "lotSz": float(info.get("lotSz", "0.00001")),
            "minSz": float(info.get("minSz", "0.00001")),
            "ctVal": float(info.get("ctVal", "1")),
        }
    return {"tickSz": 0.01, "lotSz": 0.00001, "minSz": 0.00001, "ctVal": 1}


@retry_on_exception(max_retries=3, delay=1.0)
def fetch_candles(market_api, inst_id, bar="5m", limit=300):
    """获取 OKX K 线数据"""
    page_limit = 300
    target_limit = max(1, int(limit))
    candles = []
    cursor_after = None
    seen = set()

    while len(candles) < target_limit:
        this_limit = min(page_limit, target_limit - len(candles))
        kwargs = {"instId": inst_id, "bar": bar, "limit": str(this_limit)}
        if cursor_after is not None:
            kwargs["after"] = str(cursor_after)

        method = market_api.get_candlesticks
        if cursor_after is not None and hasattr(market_api, "get_history_candlesticks"):
            method = market_api.get_history_candlesticks

        try:
            resp = method(**kwargs)
        except TypeError:
            kwargs.pop("after", None)
            resp = method(**kwargs)

        if resp.get("code") != "0":
            log_message(f"获取K线失败: {resp}")
            break

        batch = resp.get("data", [])
        if not batch:
            break

        added = 0
        for candle in batch:
            ts = int(candle[0])
            if ts in seen:
                continue
            seen.add(ts)
            candles.append(candle)
            added += 1

        oldest_ts = min(int(c[0]) for c in batch)
        if added == 0 or len(batch) < this_limit or cursor_after == oldest_ts:
            break
        cursor_after = oldest_ts

    if not candles:
        return None

    records = []
    for c in sorted(candles, key=lambda item: int(item[0])):
        records.append(
            {
                "timestamp": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "quote_volume": float(c[6]),
                "datetime": pd.to_datetime(int(c[0]), unit="ms"),
            }
        )

    df = pd.DataFrame(records)
    return df


def _okx_symbol_to_local_symbol(inst_id):
    return inst_id.upper().replace("-SWAP", "").replace("-", "")


def load_local_candles(inst_id, bar="5m", limit=300):
    """从本地 Parquet 回退加载历史 K 线，用于长窗口策略冷启动。"""
    symbol = _okx_symbol_to_local_symbol(inst_id)
    files = [
        f
        for f in list_crypto_files()
        if symbol in os.path.basename(f).upper() and f"_{bar}" in os.path.basename(f)
    ]
    if not files:
        return None

    df = load_crypto_data(files[0])
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    needed_cols = ["timestamp", "open", "high", "low", "close", "volume", "datetime"]
    for col in needed_cols:
        if col not in df.columns:
            return None
    return df[needed_cols + [c for c in ["quote_volume"] if c in df.columns]].tail(limit)


def merge_candle_history(*frames, limit=300):
    valid_frames = [frame for frame in frames if frame is not None and len(frame) > 0]
    if not valid_frames:
        return None
    df = pd.concat(valid_frames, ignore_index=True)
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    return df.tail(limit).reset_index(drop=True)


@retry_on_exception(max_retries=3, delay=1.0)
def get_orderbook(market_api, inst_id, depth=5):
    """获取订单簿，返回 (best_bid, best_ask) 浮点数"""
    resp = market_api.get_orderbook(instId=inst_id, sz=str(depth))
    if resp.get("code") == "0" and resp.get("data"):
        data = resp["data"][0]
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        best_bid = float(bids[0][0]) if bids else None
        best_ask = float(asks[0][0]) if asks else None
        return best_bid, best_ask
    return None, None


@retry_on_exception(max_retries=3, delay=1.0)
def get_order_status(trade_api, inst_id, order_id):
    """获取订单状态，返回 (state, fill_sz, avg_px)"""
    resp = trade_api.get_order(instId=inst_id, ordId=order_id)
    if resp.get("code") == "0" and resp.get("data"):
        order = resp["data"][0]
        state = order.get("state")
        fill_sz = okx_float(order.get("fillSz") or order.get("accFillSz"))
        avg_px = okx_float(order.get("avgPx"))
        return state, fill_sz, avg_px
    return None, 0, 0


@retry_on_exception(max_retries=3, delay=1.0)
def get_open_orders(trade_api, inst_id):
    """获取当前挂单列表"""
    resp = trade_api.get_order_list(instId=inst_id, state="live")
    if resp.get("code") == "0":
        return resp.get("data", [])
    return []


def cancel_all_orders(trade_api, inst_id):
    """取消指定交易对的所有挂单"""
    try:
        orders = get_open_orders(trade_api, inst_id)
        for order in orders:
            ord_id = order.get("ordId")
            if ord_id:
                try:
                    trade_api.cancel_order(instId=inst_id, ordId=ord_id)
                except Exception as e:
                    log_message(f"取消订单失败: {ord_id}, {e}")
        if orders:
            log_message(f"已取消 {len(orders)} 个挂单: {inst_id}")
        return True
    except Exception as e:
        log_message(f"取消所有挂单失败: {e}")
        return False


@retry_on_exception(max_retries=3, delay=1.0)
def place_market_order(trade_api, inst_id, side, pos_side, sz, td_mode="cross"):
    """
    下市价单 (Taker)。
    side: buy / sell
    pos_side: long / short
    sz: 数量（张数或币数）
    td_mode: cross(全仓) / isolated(逐仓)
    """
    resp = trade_api.place_order(
        instId=inst_id,
        tdMode=td_mode,
        side=side,
        posSide=pos_side,
        ordType="market",
        sz=str(sz),
    )

    if resp.get("code") == "0":
        order_id = resp["data"][0].get("ordId")
        log_message(f"市价单成功 [{side.upper()} {pos_side}] 订单ID: {order_id}")
        return order_id
    else:
        log_message(f"市价单失败: {resp}")
        return None


@retry_on_exception(max_retries=3, delay=1.0)
def place_limit_order(trade_api, inst_id, side, pos_side, sz, px, td_mode="cross"):
    """下限价单 (Maker)"""
    resp = trade_api.place_order(
        instId=inst_id,
        tdMode=td_mode,
        side=side,
        posSide=pos_side,
        ordType="limit",
        px=str(px),
        sz=str(sz),
    )
    if resp.get("code") == "0":
        order_id = resp["data"][0].get("ordId")
        log_message(
            f"限价单成功 [{side.upper()} {pos_side}] 订单ID: {order_id} 价格={px} 数量={sz}"
        )
        return order_id
    else:
        log_message(f"限价单失败: {resp}")
        return None


# ---------------------------------------------------------------------------
# 价格/数量精度处理
# ---------------------------------------------------------------------------


def round_to_size(size, lot_size):
    """将数量对齐到 lot_size"""
    units = int(Decimal(str(size)) / Decimal(str(lot_size)))
    return float(units * Decimal(str(lot_size)))


# ---------------------------------------------------------------------------
# 交易执行
# ---------------------------------------------------------------------------


def execute_trade(
    signal_id,
    trade_api,
    account_api,
    inst_id,
    tick_sz,
    lot_sz,
    capital_per_trade,
    state,
    current_price,
    best_bid,
    best_ask,
    td_mode="cross",
    force_ioc=False,
    contract_value=1.0,
    notify_email_to=None,
    mode_name=None,
):
    """
    根据信号执行交易（支持多空双向）
    signal_id: 0=平仓, 1=持有, 2=做多, 3=做空
    force_ioc: True=强制用市价单(兜底模式), False=先尝试Maker挂单
    """
    position = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    trades = state.get("trades", [])

    log_message(f"[交易] signal={signal_id} position={position} target计算中...")

    # 先取消所有现有挂单
    cancel_all_orders(trade_api, inst_id)

    # 同步实际持仓到 state
    actual_pos = get_position(account_api, inst_id)
    if abs(actual_pos) < lot_sz * 0.5:
        if position != 0:
            log_message(f"[持仓同步] 实际持仓为0，重置状态 (原state: {position})")
            state["position"] = 0
            state["strategy_size"] = 0.0
            state["entry_bar"] = 0
            position = 0
            strategy_size = 0.0
    else:
        actual_dir = 1 if actual_pos > 0 else -1
        if position != actual_dir:
            log_message(f"[持仓同步] 修正持仓方向: state={position} -> actual={actual_dir}")
            state["position"] = actual_dir
            state["strategy_size"] = abs(actual_pos)
            position = actual_dir
            strategy_size = abs(actual_pos)

    # 解析目标仓位
    target_pos = position
    if signal_id == 2:
        target_pos = 1
    elif signal_id == 3:
        target_pos = -1
    elif signal_id == 0:
        target_pos = 0

    log_message(f"[交易] target_pos={target_pos} position={position}")

    if target_pos == position:
        log_message("[交易] 无需换仓，跳过")
        state["last_signal"] = signal_id
        state["last_update"] = datetime.now().isoformat()
        return state

    # --- 平掉当前仓位 ---
    if (position == 1 and target_pos <= 0) or (position == -1 and target_pos >= 0):
        actual_position = get_position(account_api, inst_id)
        close_plan = plan_close_order(
            current_position=position,
            target_position=target_pos,
            strategy_size=strategy_size,
            actual_position=actual_position,
            entry_price=state.get("entry_price", 0),
            current_price=current_price,
            size_increment=lot_sz,
            tick_size=tick_sz,
            best_bid=best_bid,
            best_ask=best_ask,
        )
        close_label = "平多" if position == 1 else "平空"
        close_type = "Maker(TP)" if close_plan.action == "maker" else "Taker(SL)"
        if close_plan.action == "maker":
            order_id = place_limit_order(
                trade_api,
                inst_id,
                close_plan.side,
                close_plan.position_side,
                close_plan.size,
                close_plan.price,
                td_mode,
            )
            order_price = close_plan.price
        elif close_plan.action == "taker":
            order_id = place_market_order(
                trade_api,
                inst_id,
                close_plan.side,
                close_plan.position_side,
                close_plan.size,
                td_mode,
            )
            order_price = current_price
        else:
            order_id = None
            order_price = close_plan.price

        if order_id:
            trades.append(
                {
                    "time": datetime.now().isoformat(),
                    "type": close_plan.trade_type,
                    "instId": inst_id,
                    "orderId": order_id,
                    "size": close_plan.size,
                    "price": order_price,
                }
            )
            log_message(
                f"[{close_label}{close_type}] pnl={close_plan.pnl_pct * 100:+.2f}% 下单{close_plan.side} size={close_plan.size:.8f} price={order_price}"
            )
            notify_trade_action(
                f"{close_label}{close_type}",
                notify_email_to=notify_email_to,
                mode=mode_name,
                symbol=inst_id,
                order_id=order_id,
                trade_type=close_plan.trade_type,
                side=close_plan.side,
                pos_side=close_plan.position_side,
                size=close_plan.size,
                price=order_price,
                pnl_pct=f"{close_plan.pnl_pct * 100:+.2f}%",
                signal=signal_id,
            )
        else:
            log_message(f"[{close_label}失败] {close_type}单未成交")
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0

    # --- 开新仓 ---
    log_message(
        f"[交易] 检查开仓: target_pos={target_pos} state_position={state.get('position', 0)} force_ioc={force_ioc}"
    )
    if target_pos in {1, -1} and state.get("position", 0) == 0:
        sizing_price = current_price * max(float(contract_value), 1e-12)
        plan = plan_entry_order(
            target_position=target_pos,
            current_position=state.get("position", 0),
            current_price=sizing_price,
            capital_per_trade=capital_per_trade,
            size_increment=lot_sz,
            tick_size=tick_sz,
            best_bid=best_bid,
            best_ask=best_ask,
            force_ioc=force_ioc,
        )
        open_label = "开多" if target_pos == 1 else "开空"
        if plan.action == "skip":
            if plan.reason == "invalid_price":
                log_message(f"[跳过{open_label}] 价格无效")
            elif plan.reason == "missing_quote":
                log_message(f"[{open_label}失败] 无有效盘口价格")
            else:
                log_message(f"[跳过{open_label}] {plan.reason}")
        elif plan.action == "ioc":
            order_id = place_market_order(
                trade_api, inst_id, plan.side, plan.position_side, plan.size, td_mode
            )
            if order_id:
                trades.append(
                    {
                        "time": datetime.now().isoformat(),
                        "type": plan.trade_type,
                        "instId": inst_id,
                        "orderId": order_id,
                        "size": plan.size,
                        "price": current_price,
                    }
                )
                state["position"] = target_pos
                state["strategy_size"] = plan.size
                state["entry_price"] = current_price
                state["entry_bar"] = state.get("bar_count", 0)
                log_message(
                    f"[{open_label}市价兜底] size={plan.size:.8f} price={current_price:.2f}"
                )
                notify_trade_action(
                    f"{open_label}市价兜底",
                    notify_email_to=notify_email_to,
                    mode=mode_name,
                    symbol=inst_id,
                    order_id=order_id,
                    trade_type=plan.trade_type,
                    side=plan.side,
                    pos_side=plan.position_side,
                    size=plan.size,
                    price=current_price,
                    notional=plan.notional,
                    signal=signal_id,
                )
            else:
                log_message(f"[{open_label}市价失败] 兜底单也未成交")
        else:
            order_id = place_limit_order(
                trade_api,
                inst_id,
                plan.side,
                plan.position_side,
                plan.size,
                plan.price,
                td_mode,
            )
            if order_id:
                trades.append(
                    {
                        "time": datetime.now().isoformat(),
                        "type": plan.trade_type,
                        "instId": inst_id,
                        "orderId": order_id,
                        "size": plan.size,
                        "price": plan.price,
                    }
                )
                state["pending_open"] = True
                state["pending_order_id"] = order_id
                state["pending_open_signal"] = 2 if target_pos == 1 else 3
                state["pending_open_price"] = plan.price
                state["pending_open_size"] = plan.size
                log_message(
                    f"[{open_label}Maker] 挂单{plan.side} size={plan.size:.8f} price={plan.price:.2f} notional={plan.notional:.2f}"
                )
                notify_trade_action(
                    f"{open_label}Maker挂单",
                    notify_email_to=notify_email_to,
                    mode=mode_name,
                    symbol=inst_id,
                    order_id=order_id,
                    trade_type=plan.trade_type,
                    side=plan.side,
                    pos_side=plan.position_side,
                    size=plan.size,
                    price=plan.price,
                    notional=plan.notional,
                    signal=signal_id,
                )
            else:
                log_message(f"[{open_label}失败] 限价单被拒绝")

    state["trades"] = trades
    state["last_signal"] = signal_id
    state["last_update"] = datetime.now().isoformat()
    # 平仓后清理 TP 状态
    if state.get("position", 0) == 0:
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def manage_tp_order(
    trade_api,
    inst_id,
    tick_sz,
    lot_sz,
    strategy,
    state,
    td_mode="cross",
    notify_email_to=None,
    mode_name=None,
):
    """
    管理止盈限价单 (Maker)。
    当持仓存在且无 TP 单时，自动挂止盈限价单。
    """
    pos = state.get("position", 0)
    entry_price = state.get("entry_price", 0)
    size = state.get("strategy_size", 0)
    tp_order_id = state.get("tp_order_id")

    if pos == 0 or entry_price == 0 or size == 0:
        if tp_order_id:
            try:
                trade_api.cancel_order(instId=inst_id, ordId=tp_order_id)
            except Exception:
                pass
            state["tp_order_id"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        return state

    # 计算止盈价格和方向
    tp_pct = getattr(strategy, "take_profit_pct", 0.02)
    if tp_pct <= 0:
        return cancel_tp_order(trade_api, inst_id, state)
    if pos == 1:
        tp_price = round_to_tick(entry_price * (1 + tp_pct), tick_sz)
        tp_side = "sell"
        tp_pos_side = "long"
    else:
        tp_price = round_to_tick(entry_price * (1 - tp_pct), tick_sz)
        tp_side = "buy"
        tp_pos_side = "short"

    # 已有正确的 TP 单则跳过
    if tp_order_id and state.get("tp_price") == tp_price and state.get("tp_side") == tp_side:
        return state

    # 取消旧 TP 单
    if tp_order_id:
        try:
            trade_api.cancel_order(instId=inst_id, ordId=tp_order_id)
        except Exception:
            pass
        state["tp_order_id"] = None

    # 对齐 size
    order_size = round_to_size(size, lot_sz)
    if order_size <= 0:
        return state

    # 挂止盈限价单 (Maker)
    order_id = place_limit_order(
        trade_api, inst_id, tp_side, tp_pos_side, order_size, tp_price, td_mode
    )
    if order_id:
        state["tp_order_id"] = order_id
        state["tp_price"] = tp_price
        state["tp_side"] = tp_side
        log_message(
            f"[TP挂单] {tp_side.upper()} {tp_pos_side} size={order_size:.8f} @ {tp_price:.2f} (入场={entry_price:.2f}, TP={tp_pct * 100:.1f}%)"
        )
        notify_trade_action(
            "TP止盈挂单",
            notify_email_to=notify_email_to,
            mode=mode_name,
            symbol=inst_id,
            order_id=order_id,
            side=tp_side,
            pos_side=tp_pos_side,
            size=order_size,
            price=tp_price,
            entry_price=entry_price,
            take_profit_pct=f"{tp_pct * 100:.2f}%",
        )

    return state


def cancel_tp_order(trade_api, inst_id, state):
    """取消止盈限价单"""
    tp_order_id = state.get("tp_order_id")
    if tp_order_id:
        try:
            trade_api.cancel_order(instId=inst_id, ordId=tp_order_id)
        except Exception:
            pass
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
    return state


def check_stop_loss(state, current_price, stop_loss_pct=0.03, max_hold_bars=48):
    """检查止损和时间退出条件（支持多空双向）"""
    pos = state.get("position", 0)
    if pos == 0:
        return False, ""

    entry_price = state.get("entry_price", 0)
    entry_bar = state.get("entry_bar", 0)
    current_bar = state.get("bar_count", 0)

    if stop_loss_pct > 0 and pos == 1:
        # 多头止损：价格下跌超过阈值
        if entry_price > 0 and (entry_price - current_price) / entry_price >= stop_loss_pct:
            return (
                True,
                f"多头止损: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
            )
    elif stop_loss_pct > 0 and pos == -1:
        # 空头止损：价格上涨超过阈值
        if entry_price > 0 and (current_price - entry_price) / entry_price >= stop_loss_pct:
            return (
                True,
                f"空头止损: 涨幅 {(current_price - entry_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
            )

    if max_hold_bars > 0 and current_bar - entry_bar >= max_hold_bars:
        pos_name = "多头" if pos == 1 else "空头"
        return True, f"{pos_name}时间退出: 持仓 {current_bar - entry_bar} 根K线 >= {max_hold_bars}"

    return False, ""


def force_close(
    trade_api,
    account_api,
    inst_id,
    state,
    current_price,
    best_bid,
    best_ask,
    tick_sz,
    lot_sz,
    reason,
    use_maker=False,
    td_mode="cross",
    notify_email_to=None,
    mode_name=None,
):
    """
    强制平仓。
    use_maker=False: 市价单 (Taker) -- 止损场景，必须保证成交
    use_maker=True: 限价单 (Maker) -- 超时场景，可挂单等成交
    """
    pos = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    if pos == 0 or strategy_size <= 0:
        return state

    # 取消 TP 单和所有挂单
    cancel_tp_order(trade_api, inst_id, state)
    cancel_all_orders(trade_api, inst_id)

    actual_position = get_position(account_api, inst_id)
    close_size = (
        min(strategy_size, abs(actual_position)) if abs(actual_position) > 0 else strategy_size
    )
    if close_size > 0:
        close_size = round_to_size(close_size, lot_sz)
    if close_size <= 0:
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0
        return state

    # 确定平仓方向
    if pos == 1:
        side = "sell"
        pos_side = "long"
    elif pos == -1:
        side = "buy"
        pos_side = "short"
    else:
        return state

    # 根据场景选择订单类型
    if use_maker:
        order_price = compute_order_price(side, best_bid, best_ask, tick_sz)
        if order_price:
            order_id = place_limit_order(
                trade_api, inst_id, side, pos_side, close_size, order_price, td_mode
            )
        else:
            order_id = None
        fee_label = "Maker"
    else:
        order_id = place_market_order(trade_api, inst_id, side, pos_side, close_size, td_mode)
        order_price = current_price
        fee_label = "Taker"

    if order_id:
        trades = state.get("trades", [])
        pos_name = "多头" if pos == 1 else "空头"
        trades.append(
            {
                "time": datetime.now().isoformat(),
                "type": f"FORCE_CLOSE_{fee_label}",
                "instId": inst_id,
                "orderId": order_id,
                "size": close_size,
                "price": order_price,
                "reason": reason,
            }
        )
        state["trades"] = trades
        state["position"] = 0
        state["strategy_size"] = 0.0
        state["entry_bar"] = 0
        state["last_signal"] = 0
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None
        log_message(
            f"[强制平仓-{fee_label}] {reason}，{pos_name}{side} {close_size:.8f} @ {order_price}"
        )
        notify_trade_action(
            f"强制平仓-{fee_label}",
            notify_email_to=notify_email_to,
            mode=mode_name,
            symbol=inst_id,
            order_id=order_id,
            trade_type=f"FORCE_CLOSE_{fee_label}",
            side=side,
            pos_side=pos_side,
            size=close_size,
            price=order_price,
            reason=reason,
        )

    state["last_update"] = datetime.now().isoformat()
    return state


def print_status(account_api, inst_id, state, leverage=1.0, contract_value=1.0):
    """打印账户状态（合约模式）"""
    avail_usdt, eq_usdt = get_balance(account_api, "USDT")

    # 获取合约持仓
    try:
        position = get_position(account_api, inst_id)
    except Exception:
        position = 0.0

    signal_names = {0: "平仓 (FLAT)", 1: "持有 (HOLD)", 2: "做多 (BUY)", 3: "做空 (SELL)"}
    signal_str = signal_names.get(state.get("last_signal", 1), "未知")

    pos = state.get("position", 0)
    if pos == 1:
        pos_str = "多头"
    elif pos == -1:
        pos_str = "空头"
    elif state.get("pending_open"):
        pos_str = "等待Maker成交"
    else:
        pos_str = "空仓"

    # 计算未实现盈亏
    entry_price = state.get("entry_price", 0)
    last_price = state.get("last_price", 0)
    unrealized_pnl = 0.0
    if abs(position) > 0 and entry_price > 0 and last_price > 0:
        base_size = abs(position) * max(float(contract_value), 1e-12)
        if position > 0:
            unrealized_pnl = (last_price - entry_price) * base_size
        else:
            unrealized_pnl = (entry_price - last_price) * base_size

    log_message("-" * 50)
    log_message(f"当前信号: {signal_str}")
    log_message(f"持仓状态: {pos_str}")
    if state.get("pending_open"):
        pending_dir = "做多" if state.get("pending_open_signal") == 2 else "做空"
        log_message(f"挂单方向: {pending_dir}")
        log_message(f"挂单价格: {state.get('pending_open_price', 0):.2f}")
    log_message(f"合约持仓: {position:.6f} 张 | 合约面值: {contract_value:g} | 杠杆: {leverage}x")
    if abs(position) > 0 and entry_price > 0:
        log_message(f"入场价格: {entry_price:.2f}")
        log_message(f"未实现盈亏: {unrealized_pnl:.2f} USDT")
    log_message(f"USDT 余额: {eq_usdt:.2f} (可用: {avail_usdt:.2f})")
    log_message(f"交易次数: {len(state.get('trades', []))}")
    log_message("-" * 50)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------


def main():
    acquire_lock()
    parser = argparse.ArgumentParser(description="OKX Agent Trade Kit 永续合约混合费率实盘交易")
    parser.add_argument(
        "--symbol",
        type=str,
        default="ETH-USDT-SWAP",
        help="交易对，如 BTC-USDT-SWAP, ETH-USDT-SWAP",
    )
    parser.add_argument(
        "--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1H, 4H, 1D"
    )
    parser.add_argument(
        "--strategy-profile",
        type=str,
        default="channel_breakout_v2",
        choices=sorted(OKX_STRATEGY_PROFILES),
        help="内置策略档案；默认 channel_breakout_v2",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=None,
        help="自定义策略参数路径；不填时使用 --strategy-profile 对应的 checkpoint",
    )
    parser.add_argument("--capital", type=float, default=100.0, help="每次交易保证金（USDT）")
    parser.add_argument("--leverage", type=float, default=1.0, help="杠杆倍数")
    parser.add_argument(
        "--margin-mode",
        type=str,
        default="cross",
        choices=["cross", "isolated"],
        help="保证金模式: cross(全仓) / isolated(逐仓)",
    )
    parser.add_argument("--demo", action="store_true", help="Demo Trading 模拟盘（默认）")
    parser.add_argument("--live", action="store_true", help="实盘交易（真钱！）")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出")
    parser.add_argument(
        "--signal-only",
        "--dry-run",
        action="store_true",
        dest="signal_only",
        help="只观察行情和策略信号，不执行持仓同步、撤单、下单或状态保存",
    )
    parser.add_argument(
        "--notify-email-to",
        type=str,
        default=os.environ.get("TRADE_NOTIFY_EMAIL_TO", ""),
        help="交易动作邮件通知收件人；也可用 TRADE_NOTIFY_EMAIL_TO 配置",
    )
    parser.add_argument("--stop-loss", type=float, default=0.03, help="止损百分比（默认 3%%）")
    parser.add_argument("--max-hold", type=int, default=48, help="最大持仓K线数（默认 48）")
    parser.add_argument("--short", action="store_true", default=True, help="启用做空（默认开启）")
    parser.add_argument("--long-only", action="store_true", help="只做多，不做空")
    args = parser.parse_args()

    if args.live:
        flag = "0"
        mode_name = "实盘交易"
    else:
        flag = "1"
        mode_name = "Demo Trading 模拟盘"

    interval_seconds = INTERVAL_SECONDS_MAP.get(args.interval, 300)
    enable_short = not args.long_only
    mode_str = "多空双向" if enable_short else "只做多"
    args.checkpoint = resolve_checkpoint_path(args.checkpoint, args.strategy_profile)

    # 加载策略参数
    log_message("=" * 50)
    log_message(f"启动 {mode_name} ({mode_str})")
    log_message(f"策略档案: {args.strategy_profile}")
    log_message(f"Checkpoint: {args.checkpoint}")
    if args.signal_only:
        log_message("Signal-Only: 只生成信号，不执行任何账户/订单操作")
    else:
        log_message("混合费率: 开仓=Maker, 止盈=Maker, 止损=Taker, 超时=Maker")
    if args.notify_email_to:
        log_message(f"邮件通知: 已启用 -> {args.notify_email_to}")
    else:
        log_message("邮件通知: 未启用")
    log_message(f"保证金模式: {args.margin_mode}, 杠杆: {args.leverage}x")
    if not os.path.exists(args.checkpoint):
        log_message(f"错误: 未找到 {args.checkpoint}，请先运行 train_quant.py 训练策略")
        sys.exit(1)

    checkpoint = load_checkpoint(args.checkpoint)
    params = dict(checkpoint.get("params") or {})
    strategy_type = checkpoint.get("strategy", "bollinger_trend_filter")
    strategy_key = str(strategy_type).lower()
    if "rsi_low" in params:
        params["rsi_low"] = max(params["rsi_low"], 30)
    params["enable_short"] = enable_short
    if strategy_key not in {"scalp", "channelbreakout", "channel_breakout"}:
        params.setdefault("max_hold_bars", args.max_hold)
    checkpoint = dict(checkpoint)
    checkpoint["params"] = params
    strategy = build_strategy_from_checkpoint(checkpoint)
    for line in describe_strategy(strategy, strategy_type):
        log_message(line)

    required_bars = int(getattr(strategy, "warmup_bars", getattr(strategy, "window", 300))) + 10
    candle_limit = max(500, required_bars)
    log_message(f"K线需求: strategy.window={getattr(strategy, 'window', 0)}, 拉取={candle_limit}")

    # 初始化 OKX API
    if args.signal_only:
        account_api = None
        trade_api = None
        market_api, public_api = init_okx_public_api(flag=flag)
        log_message("OKX 公共行情 API 连接成功")
    else:
        account_api, trade_api, market_api, public_api = init_okx_api(flag=flag)
        log_message("OKX API 连接成功")

    # 设置杠杆
    if not args.signal_only:
        set_leverage(account_api, args.symbol, args.leverage, args.margin_mode)

    # 获取交易对信息
    inst_info = get_instrument_info(public_api, args.symbol)
    tick_sz = inst_info["tickSz"]
    lot_sz = inst_info["lotSz"]
    min_sz = inst_info["minSz"]
    ct_val = inst_info.get("ctVal", 1)
    log_message(
        f"交易对信息: {args.symbol}, Tick: {tick_sz}, Lot: {lot_sz}, Min: {min_sz}, CtVal: {ct_val}"
    )

    # 加载或初始化状态
    if args.signal_only:
        state = {
            "symbol": args.symbol,
            "interval": args.interval,
            "position": 0,
            "strategy_size": 0.0,
            "trades": [],
            "last_signal": 1,
            "last_price": 0.0,
            "bar_count": 0,
            "entry_price": 0.0,
            "entry_bar": 0,
            "pending_open": False,
        }
        log_message("Signal-Only 使用临时内存状态，不读取/写入交易状态文件")
    else:
        state = load_state()

    if state is None:
        # 首次运行：用当前 USDT 权益作为本金基准
        avail_usdt, eq_usdt = get_balance(account_api, "USDT")
        ticker_resp = market_api.get_ticker(instId=args.symbol)
        last_px = float(ticker_resp["data"][0]["last"]) if ticker_resp.get("code") == "0" else 0.0

        state = {
            "symbol": args.symbol,
            "interval": args.interval,
            "initial_capital": args.capital,
            "initial_equity": eq_usdt,
            "position": 0,
            "strategy_size": 0.0,
            "trades": [],
            "last_signal": 1,
            "last_price": last_px,
            "bar_count": 0,
            "entry_price": 0.0,
            "entry_bar": 0,
            "tp_order_id": None,
            "tp_price": 0.0,
            "tp_side": None,
            "pending_open": False,
            "pending_order_id": None,
            "pending_open_signal": 0,
            "pending_open_price": 0.0,
            "pending_open_size": 0.0,
        }
        log_message(
            f"初始化账户，保证金: {args.capital:.2f} USDT, 杠杆: {args.leverage}x, "
            f"实际名义价值: {args.capital * args.leverage:.2f} USDT"
        )
    elif not args.signal_only:
        log_message("恢复上一次交易状态")
        # 兼容旧状态文件
        state.setdefault("strategy_size", 0.0)
        state.setdefault("bar_count", 0)
        state.setdefault("entry_price", 0.0)
        state.setdefault("entry_bar", 0)
        state.setdefault("tp_order_id", None)
        state.setdefault("tp_price", 0.0)
        state.setdefault("tp_side", None)
        state.setdefault("pending_open", False)
        state.setdefault("pending_order_id", None)
        state.setdefault("pending_open_signal", 0)
        state.setdefault("pending_open_price", 0.0)
        state.setdefault("pending_open_size", 0.0)
        if "strategy_btc" in state and "strategy_size" not in state:
            state["strategy_size"] = state.pop("strategy_btc")
        if "initial_equity" not in state:
            _, eq_usdt = get_balance(account_api, "USDT")
            state["initial_equity"] = eq_usdt
            log_message(f"补录初始权益基准: {eq_usdt:.2f} USDT")

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                # 0. 同步实际持仓（防止过期 state 导致误判）
                if not args.signal_only:
                    actual_pos = get_position(account_api, args.symbol)
                    stale_pos = state.get("position", 0)
                    if abs(actual_pos) < lot_sz * 0.5 and stale_pos != 0:
                        if state.get("tp_order_id"):
                            # 有 TP 单且持仓归零 -> TP 可能真的成交了
                            entry_p = state.get("entry_price", 0)
                            if stale_pos == 1 and entry_p > 0:
                                tp_pnl = (state.get("last_price", 0) - entry_p) / entry_p * 100
                            elif stale_pos == -1 and entry_p > 0:
                                tp_pnl = (entry_p - state.get("last_price", 0)) / entry_p * 100
                            else:
                                tp_pnl = 0
                            if abs(tp_pnl) > 0.5:
                                log_message(f"[TP成交] 止盈限价单已成交! 盈亏={tp_pnl:+.2f}%")
                            else:
                                log_message(
                                    f"[持仓同步] 实际持仓=0, state={stale_pos}, PnL={tp_pnl:+.2f}% -> 可能挂单未成交，重置状态"
                                )
                        else:
                            log_message(f"[持仓同步] 实际持仓=0, state={stale_pos} -> 重置状态")
                        state["position"] = 0
                        state["strategy_size"] = 0.0
                        state["entry_bar"] = 0
                        state["tp_order_id"] = None
                        state["tp_price"] = 0.0
                        state["tp_side"] = None
                        state["pending_open"] = False
                    elif abs(actual_pos) >= lot_sz * 0.5 and state.get("pending_open"):
                        # pending_open 挂单成交了（但还没被主循环检测到）
                        pos_dir = 1 if actual_pos > 0 else -1
                        log_message(f"[持仓同步] 检测到挂单已成交: 实际={actual_pos:.6f}")
                        state["position"] = pos_dir
                        state["strategy_size"] = abs(actual_pos)
                        if state.get("entry_price", 0) == 0:
                            state["entry_price"] = state.get(
                                "pending_open_price", state.get("last_price", 0)
                            )
                        if state.get("entry_bar", 0) == 0:
                            state["entry_bar"] = state.get("bar_count", 0) - 1
                        state["pending_open"] = False
                        state["pending_order_id"] = None

                # 1. 获取 K 线数据
                df_api = fetch_candles(
                    market_api, args.symbol, bar=args.interval, limit=candle_limit
                )
                if df_api is None or len(df_api) < required_bars:
                    df_local = load_local_candles(
                        args.symbol, bar=args.interval, limit=candle_limit
                    )
                    df = merge_candle_history(df_local, df_api, limit=candle_limit)
                    if df_local is not None:
                        log_message(
                            f"K线API不足，合并本地历史: api={0 if df_api is None else len(df_api)} local={len(df_local)} merged={0 if df is None else len(df)}"
                        )
                    else:
                        df = df_api
                else:
                    df = df_api

                if df is None or len(df) < required_bars:
                    log_message(
                        f"数据不足，跳过本轮: need={required_bars}, got={0 if df is None else len(df)}"
                    )
                else:
                    # 2. 生成信号
                    signal_id, bb_info = predict_signal(strategy, df, enable_short=enable_short)
                    current_price = bb_info["price"]
                    current_time = df.iloc[-1]["datetime"]
                    state["last_price"] = current_price

                    log_message(f"K线时间: {current_time} | 价格: {current_price:.2f}")
                    info_label = bb_info.get("label", "布林带")
                    log_message(
                        f"{info_label}: 上沿={bb_info['upper']:.2f} 中线={bb_info['mid']:.2f} 下沿={bb_info['lower']:.2f}"
                    )

                    state["bar_count"] = state.get("bar_count", 0) + 1

                    # 3. 检查止损/时间退出
                    should_exit, exit_reason = check_stop_loss(
                        state,
                        current_price,
                        stop_loss_pct=getattr(strategy, "stop_loss_pct", args.stop_loss),
                        max_hold_bars=getattr(strategy, "max_hold_bars", args.max_hold),
                    )

                    # 4. 获取盘口数据
                    best_bid, best_ask = get_orderbook(market_api, args.symbol, depth=1)
                    if best_bid:
                        log_message(f"盘口: bid={best_bid:.2f} ask={best_ask:.2f}")
                    else:
                        log_message("盘口数据不可用")

                    if args.signal_only:
                        signal_labels = {
                            0: "平仓 (CLOSE)",
                            1: "持有 (HOLD)",
                            2: "做多 (LONG)",
                            3: "做空 (SHORT)",
                        }
                        state["last_signal"] = signal_id
                        state["last_update"] = datetime.now().isoformat()
                        log_message(
                            f"[Signal-Only] signal={signal_id} {signal_labels.get(signal_id, '未知')} | "
                            "不执行持仓同步、撤单、下单或状态保存"
                        )
                    elif should_exit:
                        # 超时 -> Maker, 止损 -> Taker
                        is_timeout = "时间退出" in exit_reason
                        state["pending_open"] = False
                        state = force_close(
                            trade_api,
                            account_api,
                            args.symbol,
                            state,
                            current_price,
                            best_bid,
                            best_ask,
                            tick_sz,
                            lot_sz,
                            exit_reason,
                            use_maker=is_timeout,
                            td_mode=args.margin_mode,
                            notify_email_to=args.notify_email_to,
                            mode_name=mode_name,
                        )
                    else:
                        # === 检查 pending_open 状态 ===
                        pending = state.get("pending_open", False)
                        pending_order_id = state.get("pending_order_id")
                        if pending and pending_order_id:
                            # 检查挂单是否成交
                            order_state, fill_sz, avg_px = get_order_status(
                                trade_api, args.symbol, pending_order_id
                            )
                            if order_state == "filled":
                                # Maker 挂单成交了!
                                pos_dir = 1 if fill_sz > 0 else -1  # 根据 pending 信号判断方向
                                pending_signal = state.get("pending_open_signal", 2)
                                pos_dir = 1 if pending_signal == 2 else -1
                                state["position"] = pos_dir
                                state["strategy_size"] = (
                                    abs(fill_sz)
                                    if fill_sz != 0
                                    else state.get("pending_open_size", 0)
                                )
                                state["entry_price"] = (
                                    avg_px
                                    if avg_px > 0
                                    else state.get("pending_open_price", current_price)
                                )
                                state["entry_bar"] = state.get("bar_count", 0) - 1
                                state["pending_open"] = False
                                state["pending_order_id"] = None
                                pos_name = "多" if pos_dir == 1 else "空"
                                log_message(
                                    f"[Maker成交] 入场成功 {pos_name} @{state['entry_price']:.2f} size={state['strategy_size']:.8f}"
                                )
                                notify_trade_action(
                                    "Maker入场成交",
                                    notify_email_to=args.notify_email_to,
                                    mode=mode_name,
                                    symbol=args.symbol,
                                    order_id=pending_order_id,
                                    side="buy" if pos_dir == 1 else "sell",
                                    pos_side="long" if pos_dir == 1 else "short",
                                    size=state["strategy_size"],
                                    price=state["entry_price"],
                                    signal=pending_signal,
                                )
                                # 挂 TP 单
                                state = manage_tp_order(
                                    trade_api,
                                    args.symbol,
                                    tick_sz,
                                    lot_sz,
                                    strategy,
                                    state,
                                    td_mode=args.margin_mode,
                                    notify_email_to=args.notify_email_to,
                                    mode_name=mode_name,
                                )
                            elif order_state in ("live", "partially_filled"):
                                # Maker 没成交完 -> IOC 兜底
                                pending_signal = state.get("pending_open_signal", 0)
                                cancel_all_orders(trade_api, args.symbol)
                                state["pending_open"] = False
                                state["pending_order_id"] = None

                                if signal_id == pending_signal:
                                    log_message(
                                        f"[市价兜底] Maker未成交，切换市价单 signal={signal_id}"
                                    )
                                    state = execute_trade(
                                        signal_id,
                                        trade_api,
                                        account_api,
                                        args.symbol,
                                        tick_sz,
                                        lot_sz,
                                        args.capital * args.leverage,
                                        state,
                                        current_price,
                                        best_bid,
                                        best_ask,
                                        td_mode=args.margin_mode,
                                        force_ioc=True,
                                        contract_value=ct_val,
                                        notify_email_to=args.notify_email_to,
                                        mode_name=mode_name,
                                    )
                                    if state.get("position", 0) != 0:
                                        state = manage_tp_order(
                                            trade_api,
                                            args.symbol,
                                            tick_sz,
                                            lot_sz,
                                            strategy,
                                            state,
                                            td_mode=args.margin_mode,
                                            notify_email_to=args.notify_email_to,
                                            mode_name=mode_name,
                                        )
                                else:
                                    log_message(
                                        f"[信号变化] 挂单期间信号改变 ({pending_signal}->{signal_id})，取消挂单"
                                    )
                                    state = execute_trade(
                                        signal_id,
                                        trade_api,
                                        account_api,
                                        args.symbol,
                                        tick_sz,
                                        lot_sz,
                                        args.capital * args.leverage,
                                        state,
                                        current_price,
                                        best_bid,
                                        best_ask,
                                        td_mode=args.margin_mode,
                                        contract_value=ct_val,
                                        notify_email_to=args.notify_email_to,
                                        mode_name=mode_name,
                                    )
                            elif order_state == "canceled":
                                # 订单已被取消
                                state["pending_open"] = False
                                state["pending_order_id"] = None
                                log_message("[挂单已取消] 外部取消或已处理")
                                # 重新执行当前信号
                                state = execute_trade(
                                    signal_id,
                                    trade_api,
                                    account_api,
                                    args.symbol,
                                    tick_sz,
                                    lot_sz,
                                    args.capital * args.leverage,
                                    state,
                                    current_price,
                                    best_bid,
                                    best_ask,
                                    td_mode=args.margin_mode,
                                    contract_value=ct_val,
                                    notify_email_to=args.notify_email_to,
                                    mode_name=mode_name,
                                )
                                if state.get("position", 0) != 0:
                                    state = manage_tp_order(
                                        trade_api,
                                        args.symbol,
                                        tick_sz,
                                        lot_sz,
                                        strategy,
                                        state,
                                        td_mode=args.margin_mode,
                                        notify_email_to=args.notify_email_to,
                                        mode_name=mode_name,
                                    )
                        else:
                            # === 正常流程 (无 pending) ===
                            # 检查 TP 单是否已成交
                            if state.get("position", 0) != 0 and state.get("tp_order_id"):
                                tp_order_id = state["tp_order_id"]
                                tp_state, _, _ = get_order_status(
                                    trade_api, args.symbol, tp_order_id
                                )
                                if tp_state == "filled":
                                    entry_p = state.get("entry_price", 0)
                                    pos_dir = state.get("position", 0)
                                    if pos_dir == 1 and entry_p > 0:
                                        tp_pnl = (current_price - entry_p) / entry_p * 100
                                    elif pos_dir == -1 and entry_p > 0:
                                        tp_pnl = (entry_p - current_price) / entry_p * 100
                                    else:
                                        tp_pnl = 0
                                    if abs(tp_pnl) > 0.5:
                                        log_message(
                                            f"[TP成交] 止盈限价单已成交! 盈亏={tp_pnl:+.2f}%"
                                        )
                                        notify_trade_action(
                                            "TP止盈成交",
                                            notify_email_to=args.notify_email_to,
                                            mode=mode_name,
                                            symbol=args.symbol,
                                            order_id=tp_order_id,
                                            side="sell" if pos_dir == 1 else "buy",
                                            pos_side="long" if pos_dir == 1 else "short",
                                            entry_price=entry_p,
                                            price=current_price,
                                            pnl_pct=f"{tp_pnl:+.2f}%",
                                        )
                                    else:
                                        log_message(
                                            f"[状态修正] 持仓已归零但PnL={tp_pnl:+.2f}%异常，重置状态"
                                        )
                                    state["position"] = 0
                                    state["strategy_size"] = 0.0
                                    state["entry_bar"] = 0
                                    state["tp_order_id"] = None
                                    state["tp_price"] = 0.0
                                    state["tp_side"] = None
                                elif tp_state == "canceled":
                                    log_message("[TP取消] 止盈单被外部取消")
                                    state["tp_order_id"] = None

                            # 正常交易 (先尝试 Maker)
                            state = execute_trade(
                                signal_id,
                                trade_api,
                                account_api,
                                args.symbol,
                                tick_sz,
                                lot_sz,
                                args.capital * args.leverage,
                                state,
                                current_price,
                                best_bid,
                                best_ask,
                                td_mode=args.margin_mode,
                                contract_value=ct_val,
                                notify_email_to=args.notify_email_to,
                                mode_name=mode_name,
                            )

                            # 挂单等待 or 已成交 -> 管理 TP
                            if state.get("pending_open"):
                                log_message("[挂单等待] Maker单已挂出，下轮检查成交")
                            elif state.get("position", 0) != 0:
                                state = manage_tp_order(
                                    trade_api,
                                    args.symbol,
                                    tick_sz,
                                    lot_sz,
                                    strategy,
                                    state,
                                    td_mode=args.margin_mode,
                                    notify_email_to=args.notify_email_to,
                                    mode_name=mode_name,
                                )

                    # 6. 打印状态
                    if not args.signal_only:
                        print_status(
                            account_api,
                            args.symbol,
                            state,
                            leverage=args.leverage,
                            contract_value=ct_val,
                        )
                        save_state(state)

            except Exception as e:
                log_message(f"本轮执行异常: {e}")
                import traceback

                log_message(traceback.format_exc())

            if args.once:
                log_message("--once 模式，运行一次后退出")
                break

            next_wake = align_next_wake_time(interval_seconds, offset_seconds=15)
            sleep_seconds = (next_wake - datetime.now()).total_seconds()
            if sleep_seconds > 0:
                log_message(
                    f"下次运行时间: {next_wake.strftime('%H:%M:%S')}，休眠 {sleep_seconds:.0f} 秒"
                )
                time.sleep(sleep_seconds)
            else:
                log_message("处理耗时较长，立即进入下一轮")

    except KeyboardInterrupt:
        if not args.signal_only:
            log_message("收到中断信号，保存状态并退出...")
            save_state(state)
        else:
            log_message("收到中断信号，Signal-Only 不保存交易状态，退出...")
        sys.exit(0)


if __name__ == "__main__":
    main()
