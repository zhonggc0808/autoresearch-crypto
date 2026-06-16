"""
Bitget USDT-M Futures 实盘/模拟盘交易脚本（永续合约）。
基于混合费率执行策略，每 5 分钟获取信号并自动下单。

混合费率执行策略:
    - 开仓: POST_ONLY (Maker) -- 挂限价单，享Maker低费率
    - 止盈: POST_ONLY (Maker) -- 自动挂止盈限价单，价格到达即成交
    - 止损: IOC (Taker) -- 必须保证成交，付Taker费率
    - 超时: POST_ONLY (Maker) -- 挂限价单平仓

Usage:
    # Sandbox 模拟盘（推荐先用这个测试）
    export BITGET_API_KEY="your_api_key"
    export BITGET_API_SECRET="your_api_secret"
    export BITGET_PASSPHRASE="your_passphrase"
    uv run python live_bitget_quant.py --symbol ETHUSDT --interval 5m --demo --capital 100

    # 实盘交易（真钱！确认策略稳定后再用）
    export BITGET_API_KEY="your_api_key"
    export BITGET_API_SECRET="your_api_secret"
    export BITGET_PASSPHRASE="your_passphrase"
    uv run python live_bitget_quant.py --symbol ETHUSDT --interval 5m --live --capital 500 --leverage 2

注意：
    - 默认需要 --demo 或 --live 明确指定
    - 切换到实盘前，务必先用 Sandbox 跑至少 1-2 天验证信号和下单逻辑
    - 本脚本交易 Bitget USDT-M 永续合约，支持做多/做空双向
    - Bitget K线单次限制 200 根，长通道策略会自动分页拉取
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

import ccxt
import pandas as pd

from dex.checkpoints import (
    build_channel_breakout_strategy_from_checkpoint,
    build_strategy_from_checkpoint,
    describe_strategy,
    is_regime_channel_breakout_checkpoint,
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
from dex.live.signals import generate_live_regime_channel_breakout_signal
from dex.regime_permissions import (
    RiskOffConfig,
)

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
STATE_FILE = os.path.join(LOG_DIR, "live_bitget_state.json")
LOG_FILE = os.path.join(LOG_DIR, "live_bitget_log.txt")
LOCK_FILE = os.path.join(LOG_DIR, "live_bitget_quant.lock")
CACHE_DIR = "data/live_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

INTERVAL_SECONDS_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

BITGET_STRATEGY_PROFILES = {
    "channel_breakout_v2_1_balanced": "checkpoints/channel_breakout_v2_1_balanced.pt",
    "channel_breakout_v2_2_mtg_bcd": "checkpoints/channel_breakout_v2_2_mtg_bcd.json",
}


def resolve_checkpoint_path(checkpoint_path: str | None, strategy_profile: str) -> str:
    """Resolve the checkpoint used by the Bitget live entrypoint."""
    if checkpoint_path:
        return checkpoint_path
    try:
        return BITGET_STRATEGY_PROFILES[strategy_profile]
    except KeyError as exc:
        known = ", ".join(sorted(BITGET_STRATEGY_PROFILES))
        raise ValueError(f"未知策略档案 {strategy_profile!r}; 可选: {known}") from exc

CCXT_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


try:
    import msvcrt

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            print("错误: 已有另一个 live_bitget_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd
except ImportError:
    import fcntl

    def acquire_lock():
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            print("错误: 已有另一个 live_bitget_quant 实例在运行，请先停止后再启动")
            sys.exit(1)
        return fd


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    state["last_update"] = datetime.now().isoformat()
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
        "exchange": "Bitget",
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


def normalize_symbol(symbol: str) -> str:
    """把用户输入统一为 ccxt unified symbol。

    支持格式:
        - BTCUSDT        -> BTC/USDT:USDT
        - BTC/USDT:USDT  -> BTC/USDT:USDT
        - BTC-USDT-SWAP  -> BTC/USDT:USDT
    """
    s = symbol.strip().upper()
    if "/" in s and ":" in s:
        return s
    if "-" in s:
        # BTC-USDT-SWAP -> BTC/USDT:USDT
        parts = s.split("-")
        if len(parts) >= 2:
            base = parts[0]
            quote = parts[1]
            return f"{base}/{quote}:{quote}"
    # BTCUSDT -> BTC/USDT:USDT
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}/USDT:USDT"
    if s.endswith("USD"):
        base = s[:-3]
        return f"{base}/USD:USD"
    # 无法识别时原样返回，依赖 ccxt 自行处理
    return s


# ---------------------------------------------------------------------------
# Bitget API 初始化
# ---------------------------------------------------------------------------


def get_bitget_credentials():
    """从环境变量读取 API 凭证"""
    key = os.environ.get("BITGET_API_KEY", "")
    secret = os.environ.get("BITGET_API_SECRET", "")
    passphrase = os.environ.get("BITGET_PASSPHRASE", "")
    return key, secret, passphrase


@retry_on_exception(max_retries=3, delay=1.0)
def init_bitget_api(demo=False):
    """初始化 ccxt Bitget USDT-M Futures API（永续合约）"""
    key, secret, passphrase = get_bitget_credentials()
    if not all([key, secret, passphrase]):
        log_message("错误: 未设置 BITGET_API_KEY / BITGET_API_SECRET / BITGET_PASSPHRASE 环境变量")
        sys.exit(1)

    config = {
        "apiKey": key,
        "secret": secret,
        "password": passphrase,
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        },
    }

    proxy = os.environ.get("BITGET_HTTP_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy:
        config["proxies"] = {
            "http": proxy,
            "https": proxy,
        }
        log_message(f"使用 HTTP 代理: {proxy}")

    exchange = ccxt.bitget(config)

    if demo:
        exchange.enable_demo_trading(True)
        log_message("已启用 Bitget Demo Trading 模拟盘 (PAPTRADING header)")

    exchange.load_markets()
    log_message("Bitget API 连接成功")
    return exchange


# ---------------------------------------------------------------------------
# Bitget API 封装
# ---------------------------------------------------------------------------


@retry_on_exception(max_retries=3, delay=1.0)
def get_balance(exchange, asset="USDT"):
    """查询指定币种余额，返回 (available, equity)"""
    balance = exchange.fetch_balance()
    free = float(balance.get(asset, {}).get("free", 0.0))
    total = float(balance.get(asset, {}).get("total", 0.0))
    # futures wallet fallback — Bitget returns info as a list
    if free == 0 and total == 0 and "info" in balance:
        info = balance["info"]
        if isinstance(info, dict):
            assets = info.get("assets", info.get("data", []))
        elif isinstance(info, list):
            assets = info
        else:
            assets = []
        for item in assets:
            if not isinstance(item, dict):
                continue
            coin = (
                item.get("coinName")
                or item.get("marginCoin")
                or item.get("asset")
                or item.get("currency", "")
            )
            if coin == asset:
                free = float(
                    item.get("available", item.get("availableBalance", item.get("free", 0.0)))
                    or 0.0
                )
                total = float(
                    item.get("equity", item.get("walletBalance", item.get("total", 0.0))) or 0.0
                )
                break
    return free, total


@retry_on_exception(max_retries=3, delay=1.0)
def get_position(exchange, symbol):
    """获取永续合约持仓（正=多, 负=空, 0=无）"""
    positions = exchange.fetch_positions([symbol])
    for pos in positions:
        if pos.get("symbol") == symbol:
            contracts = float(pos.get("contracts", 0.0) or 0.0)
            side = pos.get("side")
            if side == "short":
                contracts = -abs(contracts)
            return contracts
    return 0.0


@retry_on_exception(max_retries=3, delay=1.0)
def set_leverage(exchange, symbol, lever):
    """设置合约杠杆"""
    try:
        exchange.set_leverage(int(lever), symbol)
        log_message(f"杠杆设置成功: {symbol} {lever}x")
        return True
    except Exception as e:
        # 已设置相同杠杆时会报错，忽略
        if "leverage" in str(e).lower():
            log_message(f"杠杆设置: {symbol} {lever}x (可能已相同)")
            return True
        log_message(f"杠杆设置失败: {e}")
        return False


def set_hedged_mode(exchange, symbol):
    """统一使用单向持仓模式 (one-way)，buy=做多，sell=做空。

    Bitget Demo 默认单向持仓。双向持仓 (hedged) 在 ccxt 中需要每个订单
    显式设置 holdSide，但 ccxt 的普通限价单路径不会自动设该字段导致 40774。
    因此统一使用单向模式，更简单可靠。
    """
    try:
        exchange.set_position_mode(False, symbol)
    except Exception:
        pass  # 可能已是单向，忽略

    exchange.options["hedged"] = False
    log_message(f"持仓模式: {symbol} 单向持仓 (one-way)，buy=多 sell=空")
    return True


def _precision_to_step(precision):
    """把 ccxt precision 转为最小步长。"""
    if precision is None:
        return 0.001
    p = float(precision)
    if 0 < p < 1:
        return p
    if p >= 1:
        return 10 ** (-int(p))
    return 0.001


@retry_on_exception(max_retries=3, delay=1.0)
def get_instrument_info(exchange, symbol):
    """获取交易对信息 (tickSize, stepSize, minQty, contractSize)"""
    market = exchange.market(symbol)
    precision = market.get("precision", {})
    limits = market.get("limits", {})

    tick_size = _precision_to_step(precision.get("price"))
    step_size = _precision_to_step(precision.get("amount"))

    min_amount_limit = limits.get("amount", {}).get("min")
    min_cost_limit = limits.get("cost", {}).get("min")

    return {
        "tickSz": float(tick_size),
        "lotSz": float(step_size),
        "minSz": float(min_amount_limit) if min_amount_limit else float(step_size),
        "minCost": float(min_cost_limit) if min_cost_limit else 5.0,
        "ctVal": float(market.get("contractSize", 1.0) or 1.0),
    }


# ═════════════════════════════════════════════════════════════════════════
# K线本地缓存 — 避免每轮重复拉取大量历史数据
# ═════════════════════════════════════════════════════════════════════════


def _cache_path(symbol: str, interval: str) -> str:
    """获取本地 K 线缓存路径。"""
    norm_symbol = symbol.replace("/", "_").replace(":", "_")
    return os.path.join(CACHE_DIR, f"bitget_{norm_symbol}_{interval}.parquet")


def _load_klines_cache(symbol: str, interval: str):
    """从本地 parquet 加载缓存的 K 线数据，没有缓存则返回 None。"""
    path = _cache_path(symbol, interval)
    if os.path.exists(path):
        try:
            df = pd.read_parquet(path)
            if not df.empty and "timestamp" in df.columns:
                log_message(f"K线缓存加载: {path} ({len(df)} bars)")
                return df
        except Exception as e:
            log_message(f"K线缓存读取失败: {e}，重新获取")
    return None


def _save_klines_cache(df, symbol: str, interval: str):
    """将 K 线数据保存到本地 parquet 缓存。"""
    if df is None or df.empty:
        return
    path = _cache_path(symbol, interval)
    df.to_parquet(path, index=False)
    log_message(f"K线缓存保存: {path} ({len(df)} bars)")


def _merge_klines(df_new, df_cache):
    """合并新获取的 K 线数据和缓存，去重排序后返回。"""
    if df_cache is not None:
        combined = pd.concat([df_cache, df_new], ignore_index=True)
    else:
        combined = df_new
    combined = (
        combined.drop_duplicates(subset=["timestamp"], keep="last")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return combined


def _seed_from_local_parquet(symbol: str, interval: str):
    """从本地 data/crypto/ parquet 文件加载历史 K 线来预填充缓存。

    当 Bitget API 没有足够历史数据时（通常只有最近 30-60 天），
    用本地已有 parquet 文件作为历史数据源。
    """
    try:
        # 提取交易对名称：ETH/USDT:USDT -> ETHUSDT 或 BTC/USDT:USDT -> BTCUSDT
        base = symbol.split("/")[0].upper() if "/" in symbol else symbol.upper().replace("USDT", "")
        if not base.endswith("USDT"):
            base = base + "USDT"  # ccxt unified: BTC/USDT:USDT -> BTC, needs USDT suffix
        # 从 symbol 提取裸交易对名
        if "/" in symbol:
            parts = symbol.split("/")
            raw_symbol = parts[0] + parts[1].split(":")[0]  # BTC/USDT:USDT -> BTCUSDT
        else:
            raw_symbol = symbol.upper()

        files = list_crypto_files()
        matches = [
            f for f in files if raw_symbol.upper() in str(f).upper() and f"_{interval}" in str(f)
        ]
        if not matches:
            log_message(f"本地 parquet 未找到匹配: {raw_symbol} {interval}")
            return None

        # 选最大的文件（最多 bar）
        matches.sort(key=lambda p: os.path.getsize(p), reverse=True)
        best = matches[0]
        log_message(f"本地 parquet 命中: {best}")

        raw_df = load_crypto_data(best)
        if raw_df is None or raw_df.empty:
            return None

        # 确保列名一致
        if "timestamp" not in raw_df.columns and "datetime" in raw_df.columns:
            raw_df["timestamp"] = pd.to_datetime(raw_df["datetime"]).astype("int64") // 10**6
        elif "timestamp" in raw_df.columns:
            # 确保 timestamp 是 int64（毫秒）
            raw_df["timestamp"] = pd.to_numeric(raw_df["timestamp"], errors="coerce").astype(
                "Int64"
            )

        # 确保 datetime 列存在
        if "datetime" not in raw_df.columns:
            raw_df["datetime"] = pd.to_datetime(raw_df["timestamp"], unit="ms")

        log_message(
            f"本地 parquet 加载: {len(raw_df)} bars, {raw_df.iloc[0]['datetime']} ~ {raw_df.iloc[-1]['datetime']}"
        )
        return raw_df
    except Exception as e:
        log_message(f"本地 parquet 加载失败: {e}")
        return None


def ensure_klines(
    exchange,
    symbol: str,
    interval: str,
    required_bars: int,
    fetch_limit: int = 500,
) -> pd.DataFrame:
    """确保有足够的 K 线历史数据，优先使用本地缓存，不足时补充 API 拉取。

    流程:
        1. 从本地 parquet 加载缓存
        2. 拉取最近 fetch_limit 根 K 线
        3. 合并去重
        4. 如果仍不足 required_bars，用 pagination 补充历史
        5. 保存回缓存
        6. 如果缓存 < required_bars 则打印警告
    """
    cache = _load_klines_cache(symbol, interval)
    recent = fetch_candles(exchange, symbol, bar=interval, limit=fetch_limit)
    if recent is None:
        if cache is not None and len(cache) >= required_bars * 0.5:
            log_message(
                f"警告: API 拉取失败，使用不完整缓存 ({len(cache)} bars, 需要 {required_bars})"
            )
            return cache
        return cache

    df = _merge_klines(recent, cache)

    # 补充历史（首次运行或缓存不足时）
    if len(df) < required_bars:
        log_message(f"K线不足: {len(df)} < {required_bars}，尝试分页补充历史...")
        more = fetch_candles(
            exchange, symbol, bar=interval, limit=required_bars - len(df) + fetch_limit
        )
        if more is not None:
            df = _merge_klines(more, df)

    # 如果 API 仍无法提供足够历史，尝试从本地 parquet 预缓存
    if len(df) < required_bars:
        local_df = _seed_from_local_parquet(symbol, interval)
        if local_df is not None:
            log_message(f"本地 parquet 加载: {len(local_df)} bars，合并中...")
            df = _merge_klines(df, local_df)

    _save_klines_cache(df, symbol, interval)

    if len(df) < required_bars:
        log_message(
            f"警告: K线缓存仍不足 required_bars ({len(df)} < {required_bars})。"
            f"regime/permission 计算可能不可靠。"
        )
    else:
        log_message(f"K线就绪: {len(df)} bars (需要 {required_bars})")

    return df


@retry_on_exception(max_retries=3, delay=1.0)
def fetch_candles(exchange, symbol, bar="5m", limit=500):
    """获取 Bitget K 线数据（自动分页，单次上限 200 根）。

    Bitget 单次 fetch_ohlcv 最多返回 200 条。对于需要更多历史数据的
    长窗口策略（如 ChannelBreakout entry_lookback=8000），该函数会循环
    拉取直到达到 limit 或没有更多数据。

    ccxt 的 since 参数 = Bitget API 的 startTime，含义是"从这个时间往后取"。
    因此分页时需把 since 设到已有最早 K 线之前，让 API 返回更早的数据，
    再过滤掉重叠部分。
    """
    BITGET_MAX_LIMIT = 200
    ccxt_interval = CCXT_INTERVAL_MAP.get(bar, bar)
    interval_ms_map = {
        "1m": 60000,
        "5m": 300000,
        "15m": 900000,
        "1h": 3600000,
        "4h": 14400000,
        "1d": 86400000,
    }
    interval_ms = interval_ms_map.get(bar, 300000)

    all_records = []
    since = None
    max_pages = (limit // BITGET_MAX_LIMIT) + 3  # 最多请求页数保底

    for _ in range(max_pages):
        if len(all_records) >= limit:
            break

        fetch_limit = min(BITGET_MAX_LIMIT, limit)
        try:
            ohlcv = exchange.fetch_ohlcv(
                symbol, timeframe=ccxt_interval, limit=fetch_limit, since=since
            )
        except Exception:
            ohlcv = None

        if not ohlcv:
            break

        records = []
        for c in ohlcv:
            records.append(
                {
                    "timestamp": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                    "datetime": pd.to_datetime(int(c[0]), unit="ms"),
                }
            )

        if not all_records:
            all_records = records
        else:
            oldest_ts = all_records[0]["timestamp"]
            new_records = [r for r in records if r["timestamp"] < oldest_ts]
            if not new_records:
                break
            all_records = new_records + all_records
            # 已经拿到了更早的数据，如果新数据量不足说明到头了
            if len(new_records) < BITGET_MAX_LIMIT * 0.5:
                break

        # since = startTime: 下次请求比当前最早K线更早的数据；
        # 往前推 (fetch_limit+10) 根 K 线的时间跨度，留裕量避免卡在边界
        since = all_records[0]["timestamp"] - (fetch_limit + 10) * interval_ms
        if since < 0:
            since = 0

    if not all_records:
        log_message("获取K线失败: 返回为空")
        return None

    df = pd.DataFrame(all_records)
    log_message(f"K线加载完成: {len(df)} 根 (目标 {limit})")
    return df


@retry_on_exception(max_retries=3, delay=1.0)
def get_orderbook(exchange, symbol, depth=5):
    """获取订单簿，返回 (best_bid, best_ask) 浮点数"""
    book = exchange.fetch_order_book(symbol, limit=depth)
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    best_bid = float(bids[0][0]) if bids else None
    best_ask = float(asks[0][0]) if asks else None
    return best_bid, best_ask


@retry_on_exception(max_retries=3, delay=1.0)
def get_order_status(exchange, symbol, order_id):
    """获取订单状态，返回 (state, fill_sz, avg_px)

    ccxt status -> 内部状态映射:
        open           -> live
        closed         -> filled
        canceled       -> canceled
        (部分成交 ccxt 通常报 open + filled amount)
    """
    order = exchange.fetch_order(order_id, symbol)
    ccxt_status = order.get("status")
    filled = float(order.get("filled", 0.0) or 0.0)
    avg_price = float(order.get("average", 0.0) or 0.0)

    state_map = {
        "open": "live",
        "closed": "filled",
        "canceled": "canceled",
        "cancelled": "canceled",
        "expired": "canceled",
        "rejected": "canceled",
    }
    state = state_map.get(ccxt_status, ccxt_status)
    # 部分成交也按 live 处理（后续重试会检查 filled）
    if filled > 0 and state == "live":
        state = "partially_filled"
    return state, filled, avg_price


@retry_on_exception(max_retries=3, delay=1.0)
def get_open_orders(exchange, symbol):
    """获取当前挂单列表"""
    return exchange.fetch_open_orders(symbol)


def cancel_all_orders(exchange, symbol):
    """取消指定交易对的所有挂单"""
    try:
        orders = get_open_orders(exchange, symbol)
        for order in orders:
            order_id = order.get("id")
            if order_id:
                try:
                    exchange.cancel_order(order_id, symbol)
                except Exception as e:
                    log_message(f"取消订单失败: {order_id}, {e}")
        if orders:
            log_message(f"已取消 {len(orders)} 个挂单: {symbol}")
        return True
    except Exception as e:
        log_message(f"取消所有挂单失败: {e}")
        return False


@retry_on_exception(max_retries=3, delay=1.0)
def place_market_order(exchange, symbol, side, pos_side, sz, reduce_only=False):
    """
    下市价单 (Taker)。
    side: buy / sell
    pos_side: long / short (仅用于日志)
    sz: 数量（币数）
    reduce_only: True=只允许平仓，禁止反向开仓
    """
    params = {}
    if reduce_only:
        params["reduceOnly"] = True
    order = (
        exchange.create_market_buy_order(symbol, sz, params)
        if side == "buy"
        else exchange.create_market_sell_order(symbol, sz, params)
    )
    order_id = order.get("id")
    ro_tag = " REDUCE_ONLY" if reduce_only else ""
    log_message(f"市价单成功 [{side.upper()} {pos_side}{ro_tag}] 订单ID: {order_id}")
    return order_id


@retry_on_exception(max_retries=3, delay=1.0)
def place_limit_order(
    exchange, symbol, side, pos_side, sz, px, post_only=True, time_in_force=None, reduce_only=False
):
    """下限价单 (Maker 或 IOC)"""
    params = {}
    if post_only:
        params["postOnly"] = True
    if time_in_force:
        params["timeInForce"] = time_in_force
    if reduce_only:
        params["reduceOnly"] = True

    order_type = "limit"
    order = exchange.create_order(symbol, order_type, side, sz, px, params)
    order_id = order.get("id")
    tif = "POST_ONLY" if post_only else (time_in_force or "LIMIT")
    ro_tag = " REDUCE_ONLY" if reduce_only else ""
    log_message(
        f"限价单成功 [{side.upper()} {pos_side}{ro_tag}] 订单ID: {order_id} 价格={px} 数量={sz} TIF={tif}"
    )
    return order_id


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
    exchange,
    symbol,
    tick_sz,
    lot_sz,
    capital_per_trade,
    state,
    current_price,
    best_bid,
    best_ask,
    force_ioc=False,
):
    """
    根据信号执行交易（支持多空双向）
    signal_id: 0=平仓, 1=持有, 2=做多, 3=做空
    force_ioc: True=强制用市价单(兜底模式), False=先尝试Maker挂单
    """
    # pending_close 保护：平仓未确认前不准开新仓
    if state.get("pending_close"):
        log_message(
            f"[跳过交易] 等待 pending_close 成交 "
            f"(order_id={state.get('pending_close_order_id')}), signal={signal_id}"
        )
        state["last_signal"] = signal_id
        state["last_update"] = datetime.now().isoformat()
        return state

    position = state.get("position", 0)
    strategy_size = state.get("strategy_size", 0.0)
    trades = state.get("trades", [])
    MIN_ORDER_USDT = 5.0

    log_message(f"[交易] signal={signal_id} position={position} target计算中...")

    # 先取消所有现有挂单
    cancel_all_orders(exchange, symbol)

    # 同步实际持仓到 state
    actual_pos = get_position(exchange, symbol)
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
        return state

    # ── 检测方向翻转 (FLIP) ────────────────────────────────────────
    is_flip = (position == 1 and target_pos == -1) or (position == -1 and target_pos == 1)
    if is_flip:
        log_message(f"[FLIP_START] {'多→空' if position == 1 else '空→多'} 平旧开新")
        _ne = state.get("notify_email_to")
        _mn = state.get("mode_name")
        if _ne:
            notify_trade_action(
                "FLIP_START",
                notify_email_to=_ne,
                mode=_mn,
                symbol=symbol,
                flip_direction="多→空" if position == 1 else "空→多",
                entry_price=state.get("entry_price", 0),
                current_price=current_price,
            )

    # --- 平掉当前仓位 ---
    if (position == 1 and target_pos <= 0) or (position == -1 and target_pos >= 0):
        actual_position = get_position(exchange, symbol)
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
                exchange,
                symbol,
                close_plan.side,
                close_plan.position_side,
                close_plan.size,
                close_plan.price,
                post_only=True,
                reduce_only=True,
            )
            order_price = close_plan.price
        elif close_plan.action == "taker":
            order_id = place_market_order(
                exchange,
                symbol,
                close_plan.side,
                close_plan.position_side,
                close_plan.size,
                reduce_only=True,
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
                    "symbol": symbol,
                    "orderId": order_id,
                    "size": close_plan.size,
                    "price": order_price,
                }
            )
            flip_close_sub_tag = " [FLIP_CLOSE_SUBMITTED]" if is_flip else ""
            log_message(
                f"[{close_label}{close_type}] pnl={close_plan.pnl_pct * 100:+.2f}% 下单{close_plan.side} size={close_plan.size:.8f} price={order_price}{flip_close_sub_tag}"
            )

            if close_plan.action == "maker":
                # Maker 平仓：挂单未成交前不清 position，设 pending_close
                state["pending_close"] = True
                state["pending_close_order_id"] = order_id
                state["pending_close_reason"] = close_label
                state["pending_close_target"] = target_pos
                state["pending_close_created_at"] = datetime.now().isoformat()
                flip_tag = " [FLIP_CLOSE_SUBMITTED]" if is_flip else ""
                log_message(
                    f"[PENDING_CLOSE] 挂出平仓maker单等待成交, order_id={order_id}{flip_tag}"
                )
                state["trades"] = trades
                state["last_signal"] = signal_id
                state["last_update"] = datetime.now().isoformat()
                state["tp_order_id"] = None
                state["tp_price"] = 0.0
                state["tp_side"] = None
                return state

            elif close_plan.action == "taker":
                # Taker 平仓：再查询实际持仓确认
                actual_after = get_position(exchange, symbol)
                flip_close_filled_tag = " [FLIP_CLOSE_FILLED]" if is_flip else ""
                if abs(actual_after) < lot_sz * 0.5:
                    state["position"] = 0
                    state["strategy_size"] = 0.0
                    state["entry_bar"] = 0
                    log_message(f"[平仓确认] 市价单已成交, 持仓归零{flip_close_filled_tag}")
                else:
                    state["position"] = 1 if actual_after > 0 else -1
                    state["strategy_size"] = abs(actual_after)
                    state["pending_close"] = True
                    state["pending_close_order_id"] = order_id
                    state["pending_close_reason"] = close_label
                    state["pending_close_target"] = target_pos
                    state["pending_close_created_at"] = datetime.now().isoformat()
                    log_message(
                        f"[PENDING_CLOSE] 市价单未完全成交: actual_after={actual_after:.6f}"
                    )
                state["trades"] = trades
                state["last_signal"] = signal_id
                state["last_update"] = datetime.now().isoformat()
                state["tp_order_id"] = None
                state["tp_price"] = 0.0
                state["tp_side"] = None
                return state

        else:
            log_message(f"[{close_label}失败] {close_type}单未成交")

        # 平仓失败: 仍保留 position，不清仓也不开反向
        state["trades"] = trades
        state["last_signal"] = signal_id
        state["last_update"] = datetime.now().isoformat()
        return state

    # --- 开新仓（仅当无 pending_close 且 position==target_pos=0 时到达此处） ---
    assert not state.get("pending_close"), "pending_close 应已在 execute_trade 入口拦截"
    log_message(
        f"[交易] 检查开仓: target_pos={target_pos} state_position={state.get('position', 0)} force_ioc={force_ioc}"
    )
    if target_pos in {1, -1} and state.get("position", 0) == 0:
        plan = plan_entry_order(
            target_position=target_pos,
            current_position=state.get("position", 0),
            current_price=current_price,
            capital_per_trade=capital_per_trade,
            size_increment=lot_sz,
            tick_size=tick_sz,
            best_bid=best_bid,
            best_ask=best_ask,
            force_ioc=force_ioc,
            min_notional=MIN_ORDER_USDT,
        )
        open_label = "开多" if target_pos == 1 else "开空"

        # 下单前打印 size 计算明细
        raw_size = capital_per_trade / current_price if current_price > 0 else 0
        log_message(
            f"[{open_label}计算] raw_size={raw_size:.6f} "
            f"lot_sz={lot_sz} min_notional={MIN_ORDER_USDT} "
            f"capital={capital_per_trade:.2f} price={current_price:.2f} "
            f"notional_est={raw_size * current_price:.2f}"
        )
        if plan.action == "skip":
            if plan.reason == "zero_size":
                log_message(
                    f"[跳过{open_label}] 计算大小为0: "
                    f"raw_size={raw_size:.6f} lot_sz={lot_sz} "
                    f"capital={capital_per_trade:.2f} price={current_price:.2f}"
                )
            elif plan.reason == "invalid_price":
                log_message(f"[跳过{open_label}] 价格无效")
            elif plan.reason == "notional_below_minimum":
                log_message(
                    f"[跳过{open_label}] 名义价值 {plan.notional:.2f} < 最小下单 {MIN_ORDER_USDT} USDT"
                )
            elif plan.reason == "missing_quote":
                log_message(f"[{open_label}失败] 无有效盘口价格")
            else:
                log_message(f"[跳过{open_label}] {plan.reason}")
        elif plan.action == "ioc":
            order_id = place_market_order(
                exchange, symbol, plan.side, plan.position_side, plan.size, reduce_only=False
            )
            if order_id:
                trades.append(
                    {
                        "time": datetime.now().isoformat(),
                        "type": plan.trade_type,
                        "symbol": symbol,
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
            else:
                log_message(f"[{open_label}市价失败] 兜底单也未成交")
        else:
            order_id = place_limit_order(
                exchange,
                symbol,
                plan.side,
                plan.position_side,
                plan.size,
                plan.price,
                post_only=True,
                reduce_only=False,
            )
            if order_id:
                trades.append(
                    {
                        "time": datetime.now().isoformat(),
                        "type": plan.trade_type,
                        "symbol": symbol,
                        "orderId": order_id,
                        "size": plan.size,
                        "price": plan.price,
                    }
                )
                state["pending_open"] = True
                state["pending_order_id"] = order_id
                state["pending_open_signal"] = 2 if target_pos == 1 else 3
                state["pending_open_price"] = current_price
                state["pending_open_size"] = plan.size
                state["pending_open_is_flip"] = is_flip
                flip_tag = " [FLIP_OPEN_SUBMITTED]" if is_flip else ""
                log_message(
                    f"[{open_label}Maker] 挂单{plan.side} size={plan.size:.8f} price={plan.price:.2f} notional={plan.notional:.2f}{flip_tag}"
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


def manage_tp_order(exchange, symbol, tick_sz, lot_sz, strategy, state):
    """
    管理止盈限价单 (Maker)。
    当持仓存在且无 TP 单时，自动挂止盈限价单。

    Security: TP size is clamped to min(state.strategy_size, abs(actual_position))
    to prevent 40804 "exceed held positions" errors when actual position
    is smaller than strategy_size (e.g. after partial fill).
    """
    pos = state.get("position", 0)
    entry_price = state.get("entry_price", 0)
    state_size = state.get("strategy_size", 0)
    tp_order_id = state.get("tp_order_id")

    if pos == 0 or entry_price == 0 or state_size == 0:
        if tp_order_id:
            try:
                exchange.cancel_order(tp_order_id, symbol)
            except Exception:
                pass
            state["tp_order_id"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        return state

    # 查询实际持仓，避免 state.strategy_size 与交易所不一致
    try:
        actual_pos = get_position(exchange, symbol)
    except Exception as e:
        log_message(f"[TP_SKIP_REASON] get_position失败: {e}")
        return state

    # 检查仓位方向一致
    actual_dir = 1 if actual_pos > 0 else (-1 if actual_pos < 0 else 0)
    if actual_dir != pos:
        log_message(
            f"[TP_SKIP_POSITION_MISMATCH] state_pos={pos} actual_pos={actual_pos:.6f}"
        )
        if tp_order_id:
            try:
                exchange.cancel_order(tp_order_id, symbol)
            except Exception:
                pass
            state["tp_order_id"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        return state

    # 实际无仓位，清理 TP
    if abs(actual_pos) < lot_sz * 0.5:
        log_message(f"[TP_SKIP_NO_POSITION] state_size={state_size} actual={actual_pos:.6f}")
        if tp_order_id:
            try:
                exchange.cancel_order(tp_order_id, symbol)
            except Exception:
                pass
            state["tp_order_id"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        return state

    # 计算止盈价格和方向
    if strategy is None:
        tp_pct = 0.0
    else:
        tp_pct = getattr(strategy, "take_profit_pct", 0.0)
    if tp_pct <= 0:
        if state.get("tp_order_id"):
            try:
                exchange.cancel_order(state["tp_order_id"], symbol)
            except Exception:
                pass
            state["tp_order_id"] = None
            state["tp_price"] = 0.0
            state["tp_side"] = None
        log_message(
            f"[TP_SKIP_REASON] tp_pct={tp_pct} entry={entry_price} "
            f"actual_pos={actual_pos:.6f} state_size={state_size} "
            f"reason=TP_pct_0_or_unset"
        )
        return state
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
            exchange.cancel_order(tp_order_id, symbol)
        except Exception:
            pass
        state["tp_order_id"] = None

    # 对齐 size: 取 state_size 和 actual_pos 的较小值，防止 40804
    safe_size = min(state_size, abs(actual_pos))
    order_size = round_to_size(safe_size, lot_sz)
    if order_size <= 0:
        log_message(
            f"[TP_SKIP_REASON] order_size=0 safe_size={safe_size:.6f} "
            f"state_size={state_size} actual={abs(actual_pos):.6f}"
        )
        return state

    # 挂止盈限价单 (Maker, reduce-only)
    try:
        order_id = place_limit_order(
            exchange,
            symbol,
            tp_side,
            tp_pos_side,
            order_size,
            tp_price,
            post_only=True,
            reduce_only=True,
        )
        if order_id:
            state["tp_order_id"] = order_id
            state["tp_price"] = tp_price
            state["tp_side"] = tp_side
            log_message(
                f"[TP挂单] {tp_side.upper()} {tp_pos_side} size={order_size:.8f} @ {tp_price:.2f} "
                f"(入场={entry_price:.2f}, TP={tp_pct * 100:.1f}%)"
            )
    except Exception as e:
        err_str = str(e)
        if "40804" in err_str:
            log_message(
                f"[TP_SIZE_MISMATCH] exchange拒绝: size={order_size:.6f} "
                f"actual={abs(actual_pos):.6f} state={state_size}. 跳过本轮TP."
            )
        else:
            log_message(f"[TP_SKIP_REASON] 挂单异常: {e}")
        # 不抛出异常，继续后续流程
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None

    return state


def cancel_tp_order(exchange, symbol, state):
    """取消止盈限价单"""
    tp_order_id = state.get("tp_order_id")
    if tp_order_id:
        try:
            exchange.cancel_order(tp_order_id, symbol)
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

    if stop_loss_pct > 0:
        if pos == 1:
            # 多头止损：价格下跌超过阈值
            if entry_price > 0 and (entry_price - current_price) / entry_price >= stop_loss_pct:
                return (
                    True,
                    f"多头止损: 跌幅 {(entry_price - current_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
                )
        elif pos == -1:
            # 空头止损：价格上涨超过阈值
            if entry_price > 0 and (current_price - entry_price) / entry_price >= stop_loss_pct:
                return (
                    True,
                    f"空头止损: 涨幅 {(current_price - entry_price) / entry_price * 100:.2f}% >= {stop_loss_pct * 100:.0f}%",
                )

    if current_bar - entry_bar >= max_hold_bars:
        pos_name = "多头" if pos == 1 else "空头"
        return True, f"{pos_name}时间退出: 持仓 {current_bar - entry_bar} 根K线 >= {max_hold_bars}"

    return False, ""


def force_close(
    exchange,
    symbol,
    state,
    current_price,
    best_bid,
    best_ask,
    tick_sz,
    lot_sz,
    reason,
    use_maker=False,
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
    cancel_tp_order(exchange, symbol, state)
    cancel_all_orders(exchange, symbol)

    actual_position = get_position(exchange, symbol)
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
                exchange,
                symbol,
                side,
                pos_side,
                close_size,
                order_price,
                post_only=True,
                reduce_only=True,
            )
        else:
            order_id = None
        fee_label = "Maker"
    else:
        order_id = place_market_order(
            exchange, symbol, side, pos_side, close_size, reduce_only=True
        )
        order_price = current_price
        fee_label = "Taker"

    if order_id:
        trades = state.get("trades", [])
        pos_name = "多头" if pos == 1 else "空头"
        trades.append(
            {
                "time": datetime.now().isoformat(),
                "type": f"FORCE_CLOSE_{fee_label}",
                "symbol": symbol,
                "orderId": order_id,
                "size": close_size,
                "price": order_price,
                "reason": reason,
            }
        )
        state["trades"] = trades

        if use_maker:
            # Maker 强制平仓：设 pending_close，不清 position
            state["pending_close"] = True
            state["pending_close_order_id"] = order_id
            state["pending_close_reason"] = f"FORCE_CLOSE_{fee_label}: {reason}"
            state["pending_close_target"] = 0
            state["pending_close_created_at"] = datetime.now().isoformat()
            log_message(
                f"[PENDING_CLOSE] 强制平仓-maker挂单等待成交: "
                f"{pos_name}{side} {close_size:.8f} @ {order_price}"
            )
        else:
            # Taker 强制平仓：再查询实际持仓确认
            actual_after = get_position(exchange, symbol)
            if abs(actual_after) < lot_sz * 0.5:
                state["position"] = 0
                state["strategy_size"] = 0.0
                state["entry_bar"] = 0
                state["last_signal"] = 0
                log_message(
                    f"[强制平仓-{fee_label}] {reason}，{pos_name}{side} {close_size:.8f} @ {order_price}"
                )
            else:
                state["position"] = 1 if actual_after > 0 else -1
                state["strategy_size"] = abs(actual_after)
                state["pending_close"] = True
                state["pending_close_order_id"] = order_id
                state["pending_close_reason"] = f"FORCE_CLOSE_{fee_label}: {reason}"
                state["pending_close_target"] = 0
                state["pending_close_created_at"] = datetime.now().isoformat()
                log_message(
                    f"[PENDING_CLOSE] 强制平仓-taker未完全成交: actual_after={actual_after:.6f}"
                )
        state["tp_order_id"] = None
        state["tp_price"] = 0.0
        state["tp_side"] = None

    state["last_update"] = datetime.now().isoformat()
    return state


def print_status(exchange, symbol, state, leverage=1.0):
    """打印账户状态（合约模式）"""
    avail_usdt, eq_usdt = get_balance(exchange, "USDT")

    # 获取合约持仓
    try:
        position = get_position(exchange, symbol)
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
        if position > 0:
            unrealized_pnl = (last_price - entry_price) * abs(position)
        else:
            unrealized_pnl = (entry_price - last_price) * abs(position)

    log_message("-" * 50)
    log_message(f"当前信号: {signal_str}")
    # v2.1 permission overlay info
    if state.get("v21_regime"):
        log_message(
            f"v2.1: regime={state['v21_regime']} | "
            f"perm={state.get('v21_permission_reason', '?')} | "
            f"allow_L={state.get('v21_allow_long', '?')} "
            f"allow_S={state.get('v21_allow_short', '?')} | "
            f"raw={state.get('v21_raw_signal', '?')} "
            f"permission={state.get('v21_permission_signal', '?')} "
            f"routed={state.get('v21_routed_signal', '?')} | "
            f"overlay={state.get('v21_exit_overlays_enabled', False)} "
            f"overlay_changed={state.get('v21_exit_overlay_changed', False)}"
        )
    log_message(f"持仓状态: {pos_str}")
    if state.get("pending_open"):
        pending_dir = "做多" if state.get("pending_open_signal") == 2 else "做空"
        log_message(f"挂单方向: {pending_dir}")
        log_message(f"挂单价格: {state.get('pending_open_price', 0):.2f}")
    log_message(f"合约持仓: {position:.6f} | 杠杆: {leverage}x")
    if abs(position) > 0 and entry_price > 0:
        log_message(f"入场价格: {entry_price:.2f}")
        log_message(f"未实现盈亏: {unrealized_pnl:.2f} USDT")
    log_message(f"USDT 余额: {eq_usdt:.2f} (可用: {avail_usdt:.2f})")
    log_message(f"交易次数: {len(state.get('trades', []))}")
    log_message("-" * 50)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------


# ── v2.1 regime-permission signal generation ──────────────────────────────


def _v21_generate_signal(
    df,
    bull_s,
    bear_s,
    neutral_s,
    bull_cfg: RiskOffConfig,
    bear_cfg: RiskOffConfig,
    neutral_cfg: RiskOffConfig,
    policy: str,
    regime_fast: int,
    regime_slow: int,
    enable_short: bool,
    exit_logic: dict | None = None,
    exit_overlays_enabled: bool = True,
) -> tuple:
    """Generate signal through v2.1 regime-permission pipeline.

    Returns (final_signal, diagnostic_dict).
    """
    return generate_live_regime_channel_breakout_signal(
        df,
        bull_s,
        bear_s,
        neutral_s,
        bull_cfg,
        bear_cfg,
        neutral_cfg,
        policy,
        regime_fast,
        regime_slow,
        exit_logic=exit_logic,
        exit_overlays_enabled=exit_overlays_enabled,
    )


def _signal_name(sig: int) -> str:
    return {0: "FLAT", 1: "HOLD", 2: "LONG", 3: "SHORT"}.get(sig, f"UNKNOWN({sig})")


def _sleep_until_next(interval_seconds: int, offset_seconds: int = 15):
    """Sleep until the next K-line boundary + offset."""
    import math

    now = datetime.now()
    epoch = datetime(1970, 1, 1)
    now_ts = (now - epoch).total_seconds()
    next_boundary = math.ceil(now_ts / interval_seconds) * interval_seconds
    next_wake = epoch + timedelta(seconds=next_boundary + offset_seconds)
    sleep_secs = (next_wake - now).total_seconds()
    if sleep_secs > 0:
        time.sleep(sleep_secs)


# ═════════════════════════════════════════════════════════════════════════
# 冷启动 state 对齐 — 以交易所实际持仓为准
# ═════════════════════════════════════════════════════════════════════════


def reconcile_state_on_startup(exchange, symbol, state, args, lot_sz, is_signal_only=False):
    """冷启动时以交易所真实持仓为准，对齐本地 state。

    规则:
        1. 检查 state 的 symbol/interval 是否匹配当前参数
        2. 查询交易所实际持仓
        3. 如果实际无仓: 清除所有仓位/挂单/TP/entry 状态
        4. 如果实际有仓: 以交易所方向/大小为准，entry_price 保留或 fallback
        5. 取消过期挂单
        6. 返回更新后的 state 或 None（触发重建）
    """
    # ── 1. 检查 symbol/interval/checkpoint 一致性 ──────────────────────
    stale_mismatch = False
    if state.get("ccxt_symbol") or state.get("symbol"):
        st_sym = normalize_symbol(str(state.get("ccxt_symbol", state.get("symbol", ""))))
        if st_sym != normalize_symbol(symbol):
            stale_mismatch = True
    if state.get("interval", "") != args.interval:
        stale_mismatch = True

    if stale_mismatch and os.path.exists(STATE_FILE):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stale_path = os.path.join(LOG_DIR, f"live_bitget_state.stale_{ts}.json")
        try:
            import shutil

            shutil.move(STATE_FILE, stale_path)
            log_message(f"[STARTUP_RECONCILE] 旧 state 参数不匹配，已备份 -> {stale_path}")
        except Exception as e:
            log_message(f"[STARTUP_RECONCILE] 备份旧 state 失败: {e}")
        return None  # 触发调用方重建

    # ── 2. 查询交易所实际持仓 ─────────────────────────────────────────
    try:
        actual_pos = get_position(exchange, symbol)
    except Exception as e:
        log_message(f"[STARTUP_RECONCILE] 获取实际持仓失败: {e}，跳过")
        return state

    # 取消旧挂单（非 signal-only）
    if not is_signal_only and not stale_mismatch:
        try:
            orders = get_open_orders(exchange, symbol)
            if orders:
                cancel_all_orders(exchange, symbol)
                log_message(f"[STARTUP_RECONCILE] 已取消 {len(orders)} 个旧挂单")
        except Exception as e:
            log_message(f"[STARTUP_RECONCILE] 取消旧挂单失败: {e}")

    # ── 辅助: 清空本地仓位相关字段 ────────────────────────────────────
    def _clear_position(state_d):
        state_d["position"] = 0
        state_d["strategy_size"] = 0.0
        state_d["entry_price"] = 0.0
        state_d["entry_bar"] = 0
        state_d["pending_open"] = False
        state_d["pending_order_id"] = None
        state_d["pending_open_signal"] = 0
        state_d["pending_open_price"] = 0.0
        state_d["pending_open_size"] = 0.0
        state_d["pending_open_is_flip"] = False
        state_d["pending_close"] = False
        state_d["pending_close_order_id"] = None
        state_d["pending_close_reason"] = ""
        state_d["pending_close_target"] = 0
        state_d["pending_close_created_at"] = ""
        state_d["tp_order_id"] = None
        state_d["tp_price"] = 0.0
        state_d["tp_side"] = None
        state_d["last_signal"] = 1

    # ── 3. 实际无仓 ────────────────────────────────────────────────────
    if abs(actual_pos) < lot_sz * 0.5:
        stale_pos = state.get("position", 0)
        stale_pc = state.get("pending_close", False)
        if stale_pos != 0 or stale_pc:
            log_message(
                f"[STARTUP_RECONCILE] 交易所无仓，清除本地仓位状态 "
                f"(原state: pos={stale_pos} pending_close={stale_pc})"
            )
        _clear_position(state)
        log_message("[STARTUP_RECONCILE] actual flat -> local state reset")
        return state

    # ── 4. 实际有仓 ────────────────────────────────────────────────────
    actual_dir = 1 if actual_pos > 0 else -1
    state_dir = state.get("position", 0)

    log_message(
        f"[STARTUP_RECONCILE] 检测到实际持仓: {actual_pos:.6f} (state记录: {state_dir}) -> 对齐"
    )

    state["position"] = actual_dir
    state["strategy_size"] = abs(actual_pos)
    state["pending_open"] = False
    state["pending_order_id"] = None
    state["pending_open_signal"] = 0
    state["pending_open_price"] = 0.0
    state["pending_open_size"] = 0.0
    state["pending_close"] = False
    state["pending_close_order_id"] = None
    state["pending_close_reason"] = ""
    state["pending_close_target"] = 0
    state["pending_close_created_at"] = ""
    state["tp_order_id"] = None
    state["tp_price"] = 0.0
    state["tp_side"] = None

    # entry_price: 方向一致且有效则保留，否则 fallback
    reconciled = False
    if state_dir == actual_dir and state.get("entry_price", 0) > 0:
        log_message(f"[STARTUP_RECONCILE] 保留原有 entry_price: {state['entry_price']:.2f}")
    else:
        # 优先从交易所持仓的 avgOpenPrice 获取入场价
        fb = 0.0
        try:
            positions = exchange.fetch_positions([symbol])
            for p in positions:
                if p.get("symbol") == symbol:
                    ep = float(p.get("entryPrice", 0) or 0)
                    if ep > 0:
                        fb = ep
                        log_message(f"[STARTUP_RECONCILE] 从持仓接口获取 entry_price: {fb:.2f}")
                        break
        except Exception:
            pass
        if fb <= 0:
            try:
                ticker = exchange.fetch_ticker(symbol)
                fb = float(ticker.get("last", 0.0))
            except Exception:
                fb = 0.0
            if fb > 0:
                log_message(
                    f"[STARTUP_RECONCILE] 持仓 avgOpenPrice 不可用，回退到 ticker: {fb:.2f}"
                )
        if fb > 0:
            state["entry_price"] = fb
            reconciled = True
        else:
            state["entry_price"] = 0.0
            log_message("[STARTUP_RECONCILE] 无法获取 entry_price，设为 0")

    state["entry_bar"] = 0  # 重启后 bar_count 不连续
    state["entry_price_reconciled"] = reconciled

    log_message(
        f"[STARTUP_RECONCILE] actual position found -> state aligned ("
        f"pos={actual_dir} size={abs(actual_pos):.6f} entry={state['entry_price']:.2f})"
    )
    return state


def main():
    lock_fd = acquire_lock()
    parser = argparse.ArgumentParser(description="Bitget USDT-M Futures 混合费率实盘交易")
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="交易对，如 BTCUSDT, ETHUSDT，或 ccxt unified BTC/USDT:USDT",
    )
    parser.add_argument(
        "--interval", type=str, default="5m", help="K线周期: 1m, 5m, 15m, 1h, 4h, 1d"
    )
    parser.add_argument(
        "--strategy-profile",
        type=str,
        default="channel_breakout_v2_1_balanced",
        choices=sorted(BITGET_STRATEGY_PROFILES),
        help="内置策略档案；默认 channel_breakout_v2_1_balanced",
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
        "--demo",
        action="store_true",
        help="使用 Bitget Sandbox 模拟盘（推荐先用这个测试）",
    )
    parser.add_argument("--live", action="store_true", help="实盘交易（真钱！确认策略稳定后再用）")
    parser.add_argument("--once", action="store_true", help="只运行一次然后退出")
    parser.add_argument("--stop-loss", type=float, default=0.0, help="止损百分比（默认关闭）")
    parser.add_argument("--max-hold", type=int, default=48, help="最大持仓K线数（默认 48）")
    parser.add_argument("--short", action="store_true", default=True, help="启用做空（默认开启）")
    parser.add_argument("--long-only", action="store_true", help="只做多，不做空")
    parser.add_argument(
        "--signal-only",
        action="store_true",
        help="只输出信号和 permission 诊断信息，不下单（用于 v2.1 regime_permission checkpoint 观察）",
    )
    parser.add_argument(
        "--disable-exit-overlays",
        action="store_true",
        help="关闭 v2.2 exit overlays，用于快速回退到 v2.1-like 信号语义",
    )
    parser.add_argument(
        "--notify-email-to",
        type=str,
        default=os.environ.get("TRADE_NOTIFY_EMAIL_TO"),
        help="交易通知邮件地址（默认读取 TRADE_NOTIFY_EMAIL_TO 环境变量）",
    )
    args = parser.parse_args()

    if not args.demo and not args.live:
        log_message("错误: 必须指定 --demo (Sandbox 模拟盘) 或 --live (实盘)")
        sys.exit(1)

    if args.live:
        mode_name = "实盘交易"
    else:
        mode_name = "Bitget Sandbox 模拟盘"

    symbol = normalize_symbol(args.symbol)
    interval_seconds = INTERVAL_SECONDS_MAP.get(args.interval, 300)
    enable_short = not args.long_only
    mode_str = "多空双向" if enable_short else "只做多"
    checkpoint_override = args.checkpoint is not None
    args.checkpoint = resolve_checkpoint_path(args.checkpoint, args.strategy_profile)

    # 加载策略参数
    log_message("=" * 50)
    log_message(f"启动 {mode_name} ({mode_str})")
    profile_note = " (overridden by --checkpoint)" if checkpoint_override else ""
    log_message(f"策略档案: {args.strategy_profile}{profile_note}")
    log_message(f"Checkpoint: {args.checkpoint}")
    log_message("混合费率: 开仓=Maker, 止盈=Maker, 止损=Taker, 超时=Maker")
    log_message(f"杠杆: {args.leverage}x")
    log_message(f"交易对: {args.symbol} -> {symbol}")
    if not os.path.exists(args.checkpoint):
        log_message(f"错误: 未找到 {args.checkpoint}，请先运行 train_quant.py 训练策略")
        sys.exit(1)

    checkpoint = load_checkpoint(args.checkpoint)

    # ── detect v2.1/v2.2 regime_permission-compatible checkpoint ──────────
    is_v21 = is_regime_channel_breakout_checkpoint(checkpoint)

    if is_v21:
        log_message(f"检测到 v2.1-compatible checkpoint: {checkpoint.get('strategy_type')}")
        log_message(f"  variant: {checkpoint.get('variant', '?')}")
        log_message(f"  status: {checkpoint.get('status', '?')}")

        # build per-regime strategies
        v21_bull_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bull")
        v21_bear_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bear")
        v21_neutral_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "neutral")

        v21_bull_cfg = RiskOffConfig(**checkpoint["bull"]["permission"])
        v21_bear_cfg = RiskOffConfig(**checkpoint["bear"]["permission"])
        v21_neutral_cfg = RiskOffConfig(**checkpoint["neutral"]["permission"])
        v21_policy = checkpoint.get("regime_change_policy", "permission_based")
        v21_regime_fast = checkpoint.get("regime_filter", {}).get("fast_days", 50)
        v21_regime_slow = checkpoint.get("regime_filter", {}).get("slow_days", 200)
        v21_exit_logic = checkpoint.get("exit_logic")
        v21_exit_overlays_enabled = bool(v21_exit_logic) and not args.disable_exit_overlays

        strategy = None  # v2.1 doesn't use a single strategy
        strategy_type = checkpoint.get("strategy_type", "regime_permission_channel_breakout")
        log_message(f"BULL:  {checkpoint['bull']['candidate']}")
        log_message(f"BEAR:  {checkpoint['bear']['candidate']}")
        log_message(f"NEUTRAL: {checkpoint['neutral']['candidate']}")
        log_message(f"Policy: {v21_policy}")
        log_message(
            "Exit overlays: "
            + ("enabled" if v21_exit_overlays_enabled else "disabled")
            + ("" if v21_exit_logic else " (no exit_logic)")
        )
    else:
        # ── legacy single-strategy checkpoint ─────────────────────────────
        params = dict(checkpoint.get("params") or {})
        if "rsi_low" in params:
            params["rsi_low"] = max(params["rsi_low"], 30)
        params["enable_short"] = enable_short
        if checkpoint.get("strategy") != "scalp":
            params.setdefault("max_hold_bars", args.max_hold)
        checkpoint = dict(checkpoint)
        checkpoint["params"] = params
        strategy_type = checkpoint.get("strategy", "bollinger_trend_filter")
        strategy = build_strategy_from_checkpoint(checkpoint)
        for line in describe_strategy(strategy, strategy_type):
            log_message(line)

        v21_bull_s = v21_bear_s = v21_neutral_s = None
        v21_bull_cfg = v21_bear_cfg = v21_neutral_cfg = None
        v21_policy = ""
        v21_regime_fast = 50
        v21_regime_slow = 200
        v21_exit_logic = None
        v21_exit_overlays_enabled = False

    # 初始化 Bitget API
    exchange = init_bitget_api(demo=args.demo)

    # 设置杠杆 + 双向持仓模式（Bitget 默认单向，需要切换）
    set_leverage(exchange, symbol, args.leverage)
    set_hedged_mode(exchange, symbol)

    # 获取交易对信息
    inst_info = get_instrument_info(exchange, symbol)
    tick_sz = inst_info["tickSz"]
    lot_sz = inst_info["lotSz"]
    min_sz = inst_info["minSz"]
    ct_val = inst_info.get("ctVal", 1)
    log_message(
        f"交易对信息: {symbol}, Tick: {tick_sz}, Lot: {lot_sz}, Min: {min_sz}, CtVal: {ct_val}"
    )

    # 加载或初始化状态
    state = load_state()
    if state is None:
        # 首次运行：用当前 USDT 权益作为本金基准
        avail_usdt, eq_usdt = get_balance(exchange, "USDT")
        ticker = exchange.fetch_ticker(symbol)
        last_px = float(ticker.get("last", 0.0)) if ticker else 0.0

        state = {
            "symbol": args.symbol,
            "ccxt_symbol": symbol,
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
            "pending_open_is_flip": False,
            # v2.1 fields
            "v21_regime": "",
            "v21_permission_reason": "",
            "v21_allow_long": True,
            "v21_allow_short": True,
            "v21_force_flat": False,
            "v21_exit_only": False,
            "v21_raw_signal": 1,
            "v21_permission_signal": 1,
            "v21_routed_signal": 1,
            "v21_exit_overlays_enabled": False,
            "v21_exit_overlay_changed": False,
            # pending_close fields
            "pending_close": False,
            "pending_close_order_id": None,
            "pending_close_reason": "",
            "pending_close_target": 0,
            "pending_close_created_at": "",
            "notify_email_to": None,
            "mode_name": "",
        }
        log_message(
            f"初始化账户，保证金: {args.capital:.2f} USDT, 杠杆: {args.leverage}x, "
            f"实际名义价值: {args.capital * args.leverage:.2f} USDT"
        )
    else:
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
        state.setdefault("pending_open_is_flip", False)
        state.setdefault("ccxt_symbol", symbol)
        # v2.1 fields
        state.setdefault("v21_regime", "")
        state.setdefault("v21_permission_reason", "")
        state.setdefault("v21_allow_long", True)
        state.setdefault("v21_allow_short", True)
        state.setdefault("v21_force_flat", False)
        state.setdefault("v21_exit_only", False)
        state.setdefault("v21_raw_signal", 1)
        state.setdefault("v21_permission_signal", 1)
        state.setdefault("v21_routed_signal", 1)
        state.setdefault("v21_exit_overlays_enabled", False)
        state.setdefault("v21_exit_overlay_changed", False)
        state.setdefault("pending_close", False)
        state.setdefault("pending_close_order_id", None)
        state.setdefault("pending_close_reason", "")
        state.setdefault("pending_close_target", 0)
        state.setdefault("pending_close_created_at", "")
        state.setdefault("notify_email_to", None)
        state.setdefault("mode_name", "")
        if "strategy_btc" in state and "strategy_size" not in state:
            state["strategy_size"] = state.pop("strategy_btc")
        if "initial_equity" not in state:
            _, eq_usdt = get_balance(exchange, "USDT")
            state["initial_equity"] = eq_usdt
            log_message(f"补录初始权益基准: {eq_usdt:.2f} USDT")

    # ── 冷启动 state 对齐 ─────────────────────────────────────────────
    state = reconcile_state_on_startup(
        exchange,
        symbol,
        state,
        args,
        lot_sz,
        is_signal_only=args.signal_only,
    )
    if state is None:
        log_message("state 不匹配需重建，重置为新 state")
        avail_usdt, eq_usdt = get_balance(exchange, "USDT")
        ticker = exchange.fetch_ticker(symbol)
        last_px = float(ticker.get("last", 0.0)) if ticker else 0.0
        state = {
            "symbol": args.symbol,
            "ccxt_symbol": symbol,
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
            "v21_regime": "",
            "v21_permission_reason": "",
            "v21_allow_long": True,
            "v21_allow_short": True,
            "v21_force_flat": False,
            "v21_exit_only": False,
            "v21_raw_signal": 1,
            "v21_permission_signal": 1,
            "v21_routed_signal": 1,
            "v21_exit_overlays_enabled": False,
            "v21_exit_overlay_changed": False,
            "pending_close": False,
            "pending_close_order_id": None,
            "pending_close_reason": "",
            "pending_close_target": 0,
            "pending_close_created_at": "",
        }
        log_message("已重置 state（空仓初始状态）")
    # 注入通知配置到 state（供 execute_trade 等函数读取）
    state["notify_email_to"] = args.notify_email_to
    state["mode_name"] = mode_name
    save_state(state)

    try:
        while True:
            cycle_start = datetime.now()
            log_message(f"开始新一轮推理: {cycle_start.strftime('%H:%M:%S')}")

            try:
                # 0. 同步实际持仓（防止过期 state 导致误判）
                actual_pos = get_position(exchange, symbol)
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
                    state["entry_price"] = 0.0
                    state["entry_bar"] = 0
                    state["tp_order_id"] = None
                    state["tp_price"] = 0.0
                    state["tp_side"] = None
                    state["pending_open"] = False
                elif abs(actual_pos) >= lot_sz * 0.5 and state.get("pending_open"):
                    # pending_open 挂单成交了（但还没被主循环检测到）
                    pos_dir = 1 if actual_pos > 0 else -1
                    flip_open_filled_tag_sync = " [FLIP_OPEN_FILLED]" if state.get("pending_open_is_flip") else ""
                    log_message(f"[持仓同步] 检测到挂单已成交: 实际={actual_pos:.6f}{flip_open_filled_tag_sync}")
                    if state.get("pending_open_is_flip"):
                        state["pending_open_is_flip"] = False
                    state["position"] = pos_dir
                    state["strategy_size"] = abs(actual_pos)
                    # 无条件覆盖 entry_price（flip 场景：旧方向被平仓后可能残留旧 entry）
                    state["entry_price"] = state.get(
                        "pending_open_price", state.get("last_price", 0)
                    )
                    if state.get("entry_bar", 0) == 0:
                        state["entry_bar"] = state.get("bar_count", 0) - 1
                    state["pending_open"] = False
                    state["pending_order_id"] = None

                # ── pending_close 检查 ─────────────────────────────────────
                if state.get("pending_close"):
                    actual_pos = get_position(exchange, symbol)
                    pc_order_id = state.get("pending_close_order_id")

                    if abs(actual_pos) < lot_sz * 0.5:
                        # 平仓已完成（交易所持仓归零）
                        pc_target = state.get("pending_close_target", 0)
                        flip_tag = " [FLIP_CLOSE_FILLED]" if pc_target in (1, -1) else ""
                        log_message(
                            f"[PENDING_CLOSE_FILLED] 持仓已归零, 清除 pending_close "
                            f"(order_id={pc_order_id}){flip_tag}"
                        )
                        state["position"] = 0
                        state["strategy_size"] = 0.0
                        state["entry_bar"] = 0
                        state["tp_order_id"] = None
                        state["tp_price"] = 0.0
                        state["tp_side"] = None
                        state["pending_close"] = False
                        state["pending_close_order_id"] = None
                        close_reason = state.get("pending_close_reason", "close")
                        state["pending_close_reason"] = ""
                        state["pending_close_target"] = 0
                        state["pending_close_created_at"] = ""
                        if state.get("notify_email_to"):
                            notify_trade_action(
                                "CLOSE_FILLED",
                                notify_email_to=state["notify_email_to"],
                                mode=state.get("mode_name"),
                                symbol=symbol,
                                reason=close_reason,
                            )
                        save_state(state)
                        if args.once:
                            break
                        _sleep_until_next(interval_seconds)
                        continue

                    # 仍有仓位，检查订单状态
                    if pc_order_id:
                        try:
                            order_state, fill_sz, avg_px = get_order_status(
                                exchange, symbol, pc_order_id
                            )
                        except Exception:
                            order_state = None

                        if order_state == "filled":
                            log_message(
                                f"[PENDING_CLOSE_FILLED] 平仓单已成交, 持仓更新 "
                                f"actual_pos={actual_pos:.6f}"
                            )
                            if abs(actual_pos) < lot_sz * 0.5:
                                state["position"] = 0
                                state["strategy_size"] = 0.0
                                state["entry_bar"] = 0
                            else:
                                state["position"] = 1 if actual_pos > 0 else -1
                                state["strategy_size"] = abs(actual_pos)
                            state["pending_close"] = False
                            state["pending_close_order_id"] = None
                            state["pending_close_reason"] = ""
                            state["pending_close_target"] = 0
                            state["pending_close_created_at"] = ""
                            save_state(state)
                            if args.once:
                                break
                            _sleep_until_next(interval_seconds)
                            continue

                        elif order_state in ("live", "partially_filled"):
                            if abs(actual_pos) > 0:
                                state["strategy_size"] = abs(actual_pos)
                            log_message(
                                f"[PENDING_CLOSE] 平仓单未完成: "
                                f"actual_pos={actual_pos:.6f} order_state={order_state}"
                            )
                            save_state(state)
                            if args.once:
                                break
                            _sleep_until_next(interval_seconds)
                            continue

                        else:
                            # canceled / expired / rejected
                            log_message(
                                f"[PENDING_CLOSE_CANCELED] 平仓单取消/过期: "
                                f"order_state={order_state}"
                            )
                            state["pending_close"] = False
                            state["pending_close_order_id"] = None
                            state["pending_close_reason"] = ""
                            state["pending_close_target"] = 0
                            state["pending_close_created_at"] = ""
                            if abs(actual_pos) >= lot_sz * 0.5:
                                state["position"] = 1 if actual_pos > 0 else -1
                                state["strategy_size"] = abs(actual_pos)
                            save_state(state)
                            if args.once:
                                break
                            _sleep_until_next(interval_seconds)
                            continue
                    else:
                        # 没有 order_id 但 pending_close=True（异常恢复）
                        log_message(
                            "[PENDING_CLOSE_CANCELED] pending_close 异常: 无 order_id，清除标记"
                        )
                        state["pending_close"] = False
                        state["pending_close_order_id"] = None
                        state["pending_close_reason"] = ""
                        state["pending_close_target"] = 0
                        state["pending_close_created_at"] = ""

                # ── pending_open 优先检查 ──────────────────────────────────
                # 在获取新 K 线和生成信号前，先确认上一轮的挂单状态
                if state.get("pending_open") and state.get("pending_order_id"):
                    pc_id = state["pending_order_id"]
                    try:
                        o_state, fill_sz, avg_px = get_order_status(exchange, symbol, pc_id)
                    except Exception:
                        o_state = None
                        fill_sz = 0
                        avg_px = 0.0

                    if o_state == "filled":
                        # 挂单已成交 → 更新 state
                        p_sig = state.get("pending_open_signal", 2)
                        pos_dir = 1 if p_sig == 2 else -1
                        state["position"] = pos_dir
                        state["strategy_size"] = (
                            abs(fill_sz) if fill_sz != 0 else state.get("pending_open_size", 0)
                        )
                        state["entry_price"] = (
                            avg_px if avg_px > 0 else state.get("pending_open_price", 0)
                        )
                        state["entry_bar"] = state.get("bar_count", 0) - 1
                        state["pending_open"] = False
                        state["pending_order_id"] = None
                        # 用显式 flip 字段判断（提交开仓时已记录 is_flip）
                        was_flip = state.get("pending_open_is_flip", False)
                        flip_tag = " [FLIP_OPEN_FILLED]" if was_flip else ""
                        state["pending_open_is_flip"] = False
                        log_message(
                            f"[PENDING_OPEN_FILLED] 入场成功 {pos_dir:+d} "
                            f"@{state['entry_price']:.2f} size={state['strategy_size']:.6f}{flip_tag}"
                        )
                        if state.get("notify_email_to"):
                            action = "FLIP_OPEN_FILLED" if was_flip else "OPEN_FILLED"
                            notify_trade_action(
                                action,
                                notify_email_to=state["notify_email_to"],
                                mode=state.get("mode_name"),
                                symbol=symbol,
                                direction=pos_dir,
                                price=state["entry_price"],
                                size=state["strategy_size"],
                            )
                    elif o_state in ("live", "partially_filled"):
                        log_message(f"[PENDING_OPEN] 挂单未成交，保持等待: order_status={o_state}")
                        # 本轮跳过后续逻辑，等待下一轮
                        save_state(state)
                        if args.once:
                            break
                        _sleep_until_next(interval_seconds)
                        continue
                    else:
                        # canceled / expired / rejected
                        log_message(f"[PENDING_OPEN_CANCELED] 挂单取消/过期: state={o_state}")
                        state["pending_open"] = False
                        state["pending_order_id"] = None
                        state["pending_open_signal"] = 0

                # 1. 获取 K 线数据
                if is_v21:
                    # 根据 regime slow_days 和策略窗口计算所需历史量
                    bars_per_day = 86400 // interval_seconds
                    max_strategy_window = (
                        max(
                            v21_bull_s.entry_lookback,
                            v21_bull_s.exit_lookback,
                            v21_bear_s.entry_lookback,
                            v21_bear_s.exit_lookback,
                            v21_neutral_s.entry_lookback,
                            v21_neutral_s.exit_lookback,
                        )
                        if hasattr(v21_bull_s, "entry_lookback")
                        else 8000
                    )
                    required_bars = max(
                        max_strategy_window + 500,
                        (v21_regime_slow + 60) * bars_per_day,
                    )
                    log_message(
                        f"[K线需求] max_window={max_strategy_window} "
                        f"regime_slow={v21_regime_slow} "
                        f"required_bars={required_bars}"
                    )
                    df = ensure_klines(exchange, symbol, args.interval, required_bars)
                else:
                    needed_bars = max(500, strategy.window + 100)
                    df = fetch_candles(exchange, symbol, bar=args.interval, limit=needed_bars)

                # ── closed-candle-only guard (v2.1) ─────────────────────────
                # 丢弃最后一根可能还在形成的 K 线，确保信号基于已收盘 K 线，
                # 与回测/replay 行为一致。
                if is_v21 and df is not None and not df.empty:
                    interval_ms = interval_seconds * 1000
                    safety_delay_ms = 60000  # 60s 安全裕量
                    last_ts = int(df["timestamp"].iloc[-1])
                    now_ms = int(time.time() * 1000)
                    if last_ts + interval_ms + safety_delay_ms > now_ms:
                        n_before = len(df)
                        df = df.iloc[:-1].reset_index(drop=True)
                        used_ts = df["timestamp"].iloc[-1] if not df.empty else None
                        log_message(
                            f"[已收盘K线] 丢弃未完成K线: last_bar_ts={last_ts} "
                            f"now_ms={now_ms} | {n_before} -> {len(df)} bars | "
                            f"used_last_closed={used_ts}"
                        )

                min_bars_ok = (
                    (df is not None and len(df) >= (max_strategy_window + 10))
                    if is_v21
                    else (df is not None and len(df) >= (strategy.window + 10 if strategy else 100))
                )
                if not min_bars_ok:
                    log_message("数据不足，跳过本轮")
                else:
                    # v2.1: use ticker for accurate real-time price
                    # (Bitget demo sandbox K-lines may have stale/simulated prices)
                    if is_v21:
                        ticker = exchange.fetch_ticker(symbol)
                        current_price = float(ticker.get("last", float(df.iloc[-1]["close"])))
                    else:
                        current_price = float(df.iloc[-1]["close"])
                    current_time = df.iloc[-1]["datetime"]
                    state["last_price"] = current_price
                    state["bar_count"] = state.get("bar_count", 0) + 1

                    # 2. 生成信号
                    if is_v21:
                        # ── v2.1 regime-permission signal pipeline ─────────
                        signal_id, v21_diag = _v21_generate_signal(
                            df,
                            v21_bull_s,
                            v21_bear_s,
                            v21_neutral_s,
                            v21_bull_cfg,
                            v21_bear_cfg,
                            v21_neutral_cfg,
                            v21_policy,
                            v21_regime_fast,
                            v21_regime_slow,
                            enable_short,
                            exit_logic=v21_exit_logic,
                            exit_overlays_enabled=v21_exit_overlays_enabled,
                        )
                        # store permission info in state for logging
                        state["v21_regime"] = v21_diag["regime"]
                        state["v21_permission_reason"] = v21_diag["permission_reason"]
                        state["v21_allow_long"] = v21_diag["allow_long"]
                        state["v21_allow_short"] = v21_diag["allow_short"]
                        state["v21_force_flat"] = v21_diag["force_flat"]
                        state["v21_exit_only"] = v21_diag["exit_only"]
                        state["v21_raw_signal"] = v21_diag["raw_signal"]
                        state["v21_permission_signal"] = v21_diag["permission_signal"]
                        state["v21_routed_signal"] = v21_diag["routed_signal"]
                        state["v21_exit_overlays_enabled"] = bool(
                            v21_diag.get("exit_overlays_enabled", False)
                        )
                        state["v21_exit_overlay_changed"] = bool(
                            v21_diag.get("exit_overlay_changed", False)
                        )

                        log_message(
                            f"K线: {current_time} | 价格: {current_price:.2f} | "
                            f"regime={v21_diag['regime']} | "
                            f"signal={signal_id} ({_signal_name(signal_id)}) | "
                            f"perm={v21_diag['permission_reason']} | "
                            f"overlay={v21_diag.get('exit_overlays_enabled', False)}"
                        )

                        if args.signal_only:
                            log_message(
                                f"[SIGNAL-ONLY] raw={v21_diag['raw_signal']} "
                                f"permission={v21_diag['permission_signal']} "
                                f"routed={v21_diag['routed_signal']} "
                                f"allow_long={v21_diag['allow_long']} "
                                f"allow_short={v21_diag['allow_short']} "
                                f"force_flat={v21_diag['force_flat']} "
                                f"exit_only={v21_diag['exit_only']} "
                                f"overlay={v21_diag.get('exit_overlays_enabled', False)} "
                                f"overlay_changed={v21_diag.get('exit_overlay_changed', False)}"
                            )
                            # skip all trading logic for signal-only
                            save_state(state)
                            if args.once:
                                break
                            _sleep_until_next(interval_seconds)
                            continue

                    else:
                        # ── legacy single-strategy signal ──────────────────
                        signal_id, bb_info = predict_signal(strategy, df, enable_short=enable_short)
                        current_price = bb_info["price"]
                        state["last_price"] = current_price

                        log_message(f"K线时间: {current_time} | 价格: {current_price:.2f}")
                        log_message(
                            f"布林带: 上轨={bb_info['upper']:.2f} 中轨={bb_info['mid']:.2f} 下轨={bb_info['lower']:.2f}"
                        )

                    # 3. 检查止损/时间退出 (v2.1 uses permission layer instead)
                    if is_v21:
                        # v2.1: permission layer handles exits; force_flat → close immediately
                        if v21_diag["force_flat"]:
                            should_exit = True
                            exit_reason = f"force_flat ({v21_diag['permission_reason']})"
                        elif state.get("position", 0) == 1 and not v21_diag["allow_long"]:
                            should_exit = True
                            exit_reason = f"BULL risk-off: long no longer allowed ({v21_diag['permission_reason']})"
                        elif state.get("position", 0) == -1 and not v21_diag["allow_short"]:
                            should_exit = True
                            exit_reason = f"permission: short no longer allowed ({v21_diag['permission_reason']})"
                        else:
                            # also check basic stop-loss
                            should_exit, exit_reason = check_stop_loss(
                                state,
                                current_price,
                                stop_loss_pct=args.stop_loss,
                                max_hold_bars=99999,  # v2.1 doesn't use time-based exit
                            )
                    else:
                        should_exit, exit_reason = check_stop_loss(
                            state,
                            current_price,
                            stop_loss_pct=args.stop_loss,
                            max_hold_bars=strategy.max_hold_bars,
                        )

                    # 4. 获取盘口数据
                    best_bid, best_ask = get_orderbook(exchange, symbol, depth=1)
                    if best_bid:
                        log_message(f"盘口: bid={best_bid:.2f} ask={best_ask:.2f}")
                    else:
                        log_message("盘口数据不可用")

                    if should_exit:
                        # force_flat / risk-off / permission 禁止→taker reduce-only
                        is_risk_off = any(
                            kw in exit_reason.lower()
                            for kw in ["force_flat", "risk-off", "not allowed", "permission"]
                        )
                        is_timeout = "时间退出" in exit_reason
                        if is_risk_off:
                            log_message(
                                f"[FORCE_FLAT_CLOSE] {exit_reason} -> taker reduce-only (风控退出)"
                            )
                            state["pending_open"] = False
                            state = force_close(
                                exchange,
                                symbol,
                                state,
                                current_price,
                                best_bid,
                                best_ask,
                                tick_sz,
                                lot_sz,
                                exit_reason,
                                use_maker=False,
                            )
                        else:
                            state["pending_open"] = False
                            state = force_close(
                                exchange,
                                symbol,
                                state,
                                current_price,
                                best_bid,
                                best_ask,
                                tick_sz,
                                lot_sz,
                                exit_reason,
                                use_maker=is_timeout,
                            )
                        # 强制平仓通知
                        if state.get("notify_email_to"):
                            notify_trade_action(
                                "FORCE_CLOSE",
                                notify_email_to=state["notify_email_to"],
                                mode=state.get("mode_name"),
                                symbol=symbol,
                                reason=exit_reason,
                            )
                    else:
                        # === 检查 pending_open 状态 ===
                        pending = state.get("pending_open", False)
                        pending_order_id = state.get("pending_order_id")
                        if pending and pending_order_id:
                            # 检查挂单是否成交
                            order_state, fill_sz, avg_px = get_order_status(
                                exchange, symbol, pending_order_id
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
                                flip_open_filled_tag = " [FLIP_OPEN_FILLED]" if state.get("pending_open_is_flip") else ""
                                log_message(
                                    f"[Maker成交] 入场成功 {pos_name} @{state['entry_price']:.2f} size={state['strategy_size']:.8f}{flip_open_filled_tag}"
                                )
                                if state.get("pending_open_is_flip"):
                                    state["pending_open_is_flip"] = False
                                # 挂 TP 单
                                state = manage_tp_order(
                                    exchange,
                                    symbol,
                                    tick_sz,
                                    lot_sz,
                                    strategy,
                                    state,
                                )
                            elif order_state in ("live", "partially_filled"):
                                # Maker 没成交完 -> IOC 兜底
                                pending_signal = state.get("pending_open_signal", 0)
                                cancel_all_orders(exchange, symbol)
                                state["pending_open"] = False
                                state["pending_order_id"] = None

                                if signal_id == pending_signal:
                                    log_message(
                                        f"[市价兜底] Maker未成交，切换市价单 signal={signal_id}"
                                    )
                                    state = execute_trade(
                                        signal_id,
                                        exchange,
                                        symbol,
                                        tick_sz,
                                        lot_sz,
                                        args.capital * args.leverage,
                                        state,
                                        current_price,
                                        best_bid,
                                        best_ask,
                                        force_ioc=True,
                                    )
                                    if state.get("position", 0) != 0:
                                        state = manage_tp_order(
                                            exchange,
                                            symbol,
                                            tick_sz,
                                            lot_sz,
                                            strategy,
                                            state,
                                        )
                                else:
                                    log_message(
                                        f"[信号变化] 挂单期间信号改变 ({pending_signal}->{signal_id})，取消挂单"
                                    )
                                    state = execute_trade(
                                        signal_id,
                                        exchange,
                                        symbol,
                                        tick_sz,
                                        lot_sz,
                                        args.capital * args.leverage,
                                        state,
                                        current_price,
                                        best_bid,
                                        best_ask,
                                    )
                            elif order_state == "canceled":
                                # 订单已被取消
                                state["pending_open"] = False
                                state["pending_order_id"] = None
                                log_message("[挂单已取消] 外部取消或已处理")
                                # 重新执行当前信号
                                state = execute_trade(
                                    signal_id,
                                    exchange,
                                    symbol,
                                    tick_sz,
                                    lot_sz,
                                    args.capital * args.leverage,
                                    state,
                                    current_price,
                                    best_bid,
                                    best_ask,
                                )
                                if state.get("position", 0) != 0:
                                    state = manage_tp_order(
                                        exchange,
                                        symbol,
                                        tick_sz,
                                        lot_sz,
                                        strategy,
                                        state,
                                    )
                        else:
                            # === 正常流程 (无 pending) ===
                            # 检查 TP 单是否已成交
                            if state.get("position", 0) != 0 and state.get("tp_order_id"):
                                tp_order_id = state["tp_order_id"]
                                tp_state, _, _ = get_order_status(exchange, symbol, tp_order_id)
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
                                exchange,
                                symbol,
                                tick_sz,
                                lot_sz,
                                args.capital * args.leverage,
                                state,
                                current_price,
                                best_bid,
                                best_ask,
                            )

                            # 挂单等待 or 已成交 -> 管理 TP
                            if state.get("pending_open"):
                                log_message("[挂单等待] Maker单已挂出，下轮检查成交")
                            elif state.get("position", 0) != 0:
                                state = manage_tp_order(
                                    exchange,
                                    symbol,
                                    tick_sz,
                                    lot_sz,
                                    strategy,
                                    state,
                                )

                    # 6. 打印状态
                    print_status(exchange, symbol, state, leverage=args.leverage)
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

    finally:
        save_state(state)
        try:
            os.close(lock_fd)
            os.remove(LOCK_FILE)
        except Exception:
            pass
        log_message("程序退出，状态已保存")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_message("收到键盘中断，退出")
