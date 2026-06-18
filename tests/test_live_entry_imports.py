from __future__ import annotations

import importlib

import pytest

from dex.live.common import CloseOrderPlan, EntryOrderPlan

pytestmark = pytest.mark.filterwarnings("ignore:pkg_resources is deprecated as an API:UserWarning")


def test_live_nado_module_imports_without_initializing_trader() -> None:
    module = importlib.import_module("live_nado_quant")

    assert hasattr(module, "execute_trade")


def test_live_nado_small_entry_uses_ioc_instead_of_post_only(monkeypatch) -> None:
    module = importlib.import_module("live_nado_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)

    class FakeTrader:
        def __init__(self) -> None:
            self.orders = []

        def get_position(self, _product_id):
            return 0.0

        def cancel_all_orders(self, _product_id):
            return True

        def place_order(self, **kwargs):
            self.orders.append(kwargs)
            return "digest-1"

    trader = FakeTrader()
    state = {"position": 0, "strategy_size": 0.0, "trades": [], "bar_count": 12}

    new_state = module.execute_trade(
        signal_id=2,
        trader=trader,
        product_id=1,
        tick_size=0.1,
        size_increment=0.001,
        capital_per_trade=50.0,
        state=state,
        current_price=2000.0,
        best_bid=1999.9,
        best_ask=2000.0,
        force_ioc=False,
    )

    assert trader.orders[0]["order_type"] == module.OrderType.IOC
    assert trader.orders[0]["side"] == "buy"
    assert trader.orders[0]["price"] == 2000.2
    assert new_state["position"] == 1
    assert new_state["trades"][0]["type"] == "BUY_OPEN_IOC_FALLBACK"


def test_live_binance_entry_uses_shared_entry_plan(monkeypatch) -> None:
    module = importlib.import_module("live_binance_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _exchange, _symbol: True)
    monkeypatch.setattr(module, "get_position", lambda _exchange, _symbol: 0.0)
    monkeypatch.setattr(
        module,
        "plan_entry_order",
        lambda **_kwargs: EntryOrderPlan(
            action="maker",
            side="buy",
            position_side="long",
            size=0.02,
            price=1999.9,
            notional=40.0,
            target_position=1,
            trade_type="BUY_OPEN_MAKER",
        ),
    )
    orders = []

    def fake_limit_order(
        exchange, symbol, side, pos_side, sz, px, post_only=True, time_in_force=None
    ):
        orders.append((exchange, symbol, side, pos_side, sz, px, post_only, time_in_force))
        return "binance-order-1"

    monkeypatch.setattr(module, "place_limit_order", fake_limit_order)
    monkeypatch.setattr(
        module,
        "place_market_order",
        lambda *_args, **_kwargs: pytest.fail("market order should not be used"),
    )

    state = {"position": 0, "strategy_size": 0.0, "trades": [], "bar_count": 7}
    new_state = module.execute_trade(
        signal_id=2,
        exchange=object(),
        symbol="BTC/USDT:USDT",
        tick_sz=0.1,
        lot_sz=0.001,
        capital_per_trade=50.0,
        state=state,
        current_price=2000.0,
        best_bid=1999.9,
        best_ask=2000.0,
    )

    assert orders[0][4:7] == (0.02, 1999.9, True)
    assert new_state["pending_open"] is True
    assert new_state["trades"][0]["orderId"] == "binance-order-1"


def test_live_binance_insufficient_margin_halts_and_notifies(monkeypatch) -> None:
    module = importlib.import_module("live_binance_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _exchange, _symbol: True)
    monkeypatch.setattr(module, "get_position", lambda _exchange, _symbol: 0.0)
    monkeypatch.setattr(
        module,
        "plan_entry_order",
        lambda **_kwargs: EntryOrderPlan(
            action="maker",
            side="buy",
            position_side="long",
            size=0.02,
            price=1999.9,
            notional=40.0,
            target_position=1,
            trade_type="BUY_OPEN_MAKER",
        ),
    )
    notices = []
    monkeypatch.setattr(
        module,
        "notify_trade_action",
        lambda action, **kwargs: notices.append((action, kwargs)) or True,
    )

    class FakeExchange:
        def create_order(self, *_args, **_kwargs):
            raise module.ccxt.InsufficientFunds(
                'binanceusdm {"code":-2019,"msg":"Margin is insufficient."}'
            )

    state = {"position": 0, "strategy_size": 0.0, "trades": [], "bar_count": 7}
    with pytest.raises(module.LiveHalt):
        module.execute_trade(
            signal_id=2,
            exchange=FakeExchange(),
            symbol="BTC/USDT:USDT",
            tick_sz=0.1,
            lot_sz=0.001,
            capital_per_trade=50.0,
            state=state,
            current_price=2000.0,
            best_bid=1999.9,
            best_ask=2000.0,
            notify_email_to="ops@example.com",
            mode_name="Demo",
        )

    assert state["halted"] is True
    assert state["halt_reason"] == "insufficient_margin"
    assert notices[0][0] == "Binance保证金不足，程序已停止"


def test_live_okx_entry_uses_shared_entry_plan(monkeypatch) -> None:
    module = importlib.import_module("live_okx_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _trade_api, _inst_id: True)
    monkeypatch.setattr(module, "get_position", lambda _account_api, _inst_id: 0.0)
    monkeypatch.setattr(
        module,
        "plan_entry_order",
        lambda **_kwargs: EntryOrderPlan(
            action="maker",
            side="sell",
            position_side="short",
            size=0.03,
            price=2000.1,
            notional=60.0,
            target_position=-1,
            trade_type="SELL_SHORT_MAKER",
        ),
    )
    orders = []

    def fake_limit_order(
        trade_api, inst_id, side, pos_side, sz, px, td_mode="cross", reduce_only=False
    ):
        orders.append((trade_api, inst_id, side, pos_side, sz, px, td_mode))
        return "okx-order-1"

    monkeypatch.setattr(module, "place_limit_order", fake_limit_order)
    monkeypatch.setattr(
        module,
        "place_market_order",
        lambda *_args, **_kwargs: pytest.fail("market order should not be used"),
    )

    state = {"position": 0, "strategy_size": 0.0, "trades": [], "bar_count": 7}
    new_state = module.execute_trade(
        signal_id=3,
        trade_api=object(),
        account_api=object(),
        inst_id="BTC-USDT-SWAP",
        tick_sz=0.1,
        lot_sz=0.001,
        capital_per_trade=50.0,
        state=state,
        current_price=2000.0,
        best_bid=1999.9,
        best_ask=2000.0,
    )

    assert orders[0][4:6] == (0.03, 2000.1)
    assert new_state["pending_open"] is True
    assert new_state["trades"][0]["orderId"] == "okx-order-1"


def test_live_okx_insufficient_margin_halts_and_notifies(monkeypatch) -> None:
    module = importlib.import_module("live_okx_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _trade_api, _inst_id: True)
    monkeypatch.setattr(module, "get_position", lambda _account_api, _inst_id: 0.0)
    monkeypatch.setattr(
        module,
        "plan_entry_order",
        lambda **_kwargs: EntryOrderPlan(
            action="maker",
            side="sell",
            position_side="short",
            size=0.03,
            price=2000.1,
            notional=60.0,
            target_position=-1,
            trade_type="SELL_SHORT_MAKER",
        ),
    )
    notices = []
    monkeypatch.setattr(
        module,
        "notify_trade_action",
        lambda action, **kwargs: notices.append((action, kwargs)) or True,
    )

    def reject_limit_order(*_args, **_kwargs):
        raise module.OKXInsufficientMarginError(
            {
                "code": "1",
                "data": [
                    {
                        "sCode": "51008",
                        "sMsg": "Order failed. Insufficient USDT margin in account ",
                    }
                ],
            }
        )

    monkeypatch.setattr(module, "place_limit_order", reject_limit_order)

    state = {"position": 0, "strategy_size": 0.0, "trades": [], "bar_count": 7}
    with pytest.raises(module.LiveHalt):
        module.execute_trade(
            signal_id=3,
            trade_api=object(),
            account_api=object(),
            inst_id="BTC-USDT-SWAP",
            tick_sz=0.1,
            lot_sz=0.001,
            capital_per_trade=50.0,
            state=state,
            current_price=2000.0,
            best_bid=1999.9,
            best_ask=2000.0,
            notify_email_to="ops@example.com",
            mode_name="Demo",
        )

    assert state["halted"] is True
    assert state["halt_reason"] == "insufficient_margin"
    assert notices[0][0] == "OKX保证金不足，程序已停止"


def test_live_bitget_insufficient_margin_halts_and_notifies(monkeypatch) -> None:
    module = importlib.import_module("live_bitget_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _exchange, _symbol: True)
    monkeypatch.setattr(module, "get_position", lambda _exchange, _symbol: 0.0)
    monkeypatch.setattr(
        module,
        "plan_entry_order",
        lambda **_kwargs: EntryOrderPlan(
            action="maker",
            side="sell",
            position_side="short",
            size=0.03,
            price=2000.1,
            notional=60.0,
            target_position=-1,
            trade_type="SELL_SHORT_MAKER",
        ),
    )
    notices = []
    monkeypatch.setattr(
        module,
        "notify_trade_action",
        lambda action, **kwargs: notices.append((action, kwargs)) or True,
    )

    class FakeExchange:
        def create_order(self, *_args, **_kwargs):
            raise Exception("bitget: not enough margin to place order")

    state = {"position": 0, "strategy_size": 0.0, "trades": [], "bar_count": 7}
    with pytest.raises(module.LiveHalt):
        module.execute_trade(
            signal_id=3,
            exchange=FakeExchange(),
            symbol="BTC/USDT:USDT",
            tick_sz=0.1,
            lot_sz=0.001,
            capital_per_trade=50.0,
            state=state,
            current_price=2000.0,
            best_bid=1999.9,
            best_ask=2000.0,
            notify_email_to="ops@example.com",
            mode_name="Demo",
        )

    assert state["halted"] is True
    assert state["halt_reason"] == "insufficient_margin"
    assert notices[0][0] == "Bitget保证金不足，程序已停止"


def test_live_okx_empty_proxy_disables_proxy(monkeypatch) -> None:
    module = importlib.import_module("live_okx_quant")
    monkeypatch.setenv("OKX_API_KEY", "test-key")
    monkeypatch.setenv("OKX_API_SECRET", "test-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "test-passphrase")
    monkeypatch.setenv("OKX_HTTP_PROXY", "")
    proxies = []

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            proxies.append(kwargs.get("proxy"))

    monkeypatch.setattr(module, "AccountAPI", FakeAPI)
    monkeypatch.setattr(module, "TradeAPI", FakeAPI)
    monkeypatch.setattr(module, "MarketAPI", FakeAPI)
    monkeypatch.setattr(module, "PublicAPI", FakeAPI)
    monkeypatch.setattr(module, "log_message", lambda _msg: None)

    module.init_okx_api(flag="1")

    assert proxies == [None, None, None, None]


def test_live_binance_close_uses_shared_close_plan(monkeypatch) -> None:
    module = importlib.import_module("live_binance_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _exchange, _symbol: True)
    monkeypatch.setattr(module, "get_position", lambda _exchange, _symbol: 0.02)
    monkeypatch.setattr(
        module,
        "plan_close_order",
        lambda **_kwargs: CloseOrderPlan(
            action="maker",
            side="sell",
            position_side="long",
            size=0.02,
            price=2100.0,
            pnl_pct=0.05,
            trade_type="CLOSE_LONG_Maker(TP)",
        ),
    )
    orders = []

    def fake_limit_order(
        exchange, symbol, side, pos_side, sz, px, post_only=True, time_in_force=None
    ):
        orders.append((exchange, symbol, side, pos_side, sz, px, post_only, time_in_force))
        return "binance-close-1"

    monkeypatch.setattr(module, "place_limit_order", fake_limit_order)
    monkeypatch.setattr(
        module,
        "place_market_order",
        lambda *_args, **_kwargs: pytest.fail("market order should not be used"),
    )

    state = {"position": 1, "strategy_size": 0.02, "entry_price": 2000.0, "trades": []}
    new_state = module.execute_trade(
        signal_id=0,
        exchange=object(),
        symbol="BTC/USDT:USDT",
        tick_sz=0.1,
        lot_sz=0.001,
        capital_per_trade=50.0,
        state=state,
        current_price=2100.0,
        best_bid=2099.9,
        best_ask=2100.0,
    )

    assert orders[0][2:7] == ("sell", "long", 0.02, 2100.0, True)
    assert new_state["position"] == 0
    assert new_state["trades"][0]["type"] == "CLOSE_LONG_Maker(TP)"


def test_live_okx_close_uses_shared_close_plan(monkeypatch) -> None:
    module = importlib.import_module("live_okx_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda _trade_api, _inst_id: True)
    pos_call_count = [0]

    def fake_get_position(_account_api, _inst_id):
        pos_call_count[0] += 1
        return 0.0 if pos_call_count[0] >= 2 else -0.02

    monkeypatch.setattr(module, "get_position", fake_get_position)
    monkeypatch.setattr(
        module,
        "plan_close_order",
        lambda **_kwargs: CloseOrderPlan(
            action="taker",
            side="buy",
            position_side="short",
            size=0.02,
            price=2100.2,
            pnl_pct=-0.05,
            trade_type="CLOSE_SHORT_Taker(SL)",
        ),
    )
    orders = []

    def fake_market_order(
        trade_api, inst_id, side, pos_side, sz, td_mode="cross", reduce_only=False
    ):
        orders.append((trade_api, inst_id, side, pos_side, sz, td_mode))
        return "okx-close-1"

    monkeypatch.setattr(module, "place_market_order", fake_market_order)
    monkeypatch.setattr(
        module,
        "place_limit_order",
        lambda *_args, **_kwargs: pytest.fail("limit order should not be used"),
    )

    state = {"position": -1, "strategy_size": 0.02, "entry_price": 2000.0, "trades": []}
    new_state = module.execute_trade(
        signal_id=0,
        trade_api=object(),
        account_api=object(),
        inst_id="BTC-USDT-SWAP",
        tick_sz=0.1,
        lot_sz=0.001,
        capital_per_trade=50.0,
        state=state,
        current_price=2100.0,
        best_bid=2099.9,
        best_ask=2100.0,
    )

    assert orders[0][2:5] == ("buy", "short", 0.02)
    assert new_state["position"] == 0
    assert new_state["trades"][0]["type"] == "CLOSE_SHORT_Taker(SL)"


def test_live_okx_force_close_fraction_keeps_remaining_position(monkeypatch) -> None:
    module = importlib.import_module("live_okx_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_tp_order", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda *_args, **_kwargs: True)
    positions = iter([0.02, 0.01])
    monkeypatch.setattr(module, "get_position", lambda *_args: next(positions))
    orders = []

    def fake_market_order(
        trade_api, inst_id, side, pos_side, sz, td_mode="cross", reduce_only=False
    ):
        orders.append((side, pos_side, sz, td_mode, reduce_only))
        return "okx-reduce-1"

    monkeypatch.setattr(module, "place_market_order", fake_market_order)
    state = {
        "position": 1,
        "strategy_size": 0.02,
        "entry_price": 2000.0,
        "trades": [],
        "tp_order_id": "tp-1",
    }

    new_state = module.force_close(
        trade_api=object(),
        account_api=object(),
        inst_id="BTC-USDT-SWAP",
        state=state,
        current_price=1900.0,
        best_bid=1899.9,
        best_ask=1900.0,
        tick_sz=0.1,
        lot_sz=0.001,
        reason="live_risk:adverse_stop",
        fraction=0.5,
        success_state_updates={"risk_adverse_stop_fired": True},
    )

    assert orders == [("sell", "long", 0.01, "cross", True)]
    assert new_state["position"] == 1
    assert new_state["strategy_size"] == pytest.approx(0.01)
    assert new_state["risk_adverse_stop_fired"] is True
    assert new_state["tp_order_id"] is None


def test_live_bitget_force_close_fraction_keeps_remaining_position(monkeypatch) -> None:
    module = importlib.import_module("live_bitget_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_tp_order", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda *_args, **_kwargs: True)
    positions = iter([-0.04, -0.02])
    monkeypatch.setattr(module, "get_position", lambda *_args: next(positions))
    orders = []

    def fake_market_order(exchange, symbol, side, pos_side, sz, reduce_only=False):
        orders.append((side, pos_side, sz, reduce_only))
        return "bitget-reduce-1"

    monkeypatch.setattr(module, "place_market_order", fake_market_order)
    state = {
        "position": -1,
        "strategy_size": 0.04,
        "entry_price": 2000.0,
        "trades": [],
        "tp_order_id": "tp-1",
    }

    new_state = module.force_close(
        exchange=object(),
        symbol="BTC/USDT:USDT",
        state=state,
        current_price=2100.0,
        best_bid=2099.9,
        best_ask=2100.0,
        tick_sz=0.1,
        lot_sz=0.001,
        reason="live_risk:adverse_stop",
        fraction=0.5,
        success_state_updates={"risk_adverse_stop_fired": True},
    )

    assert orders == [("buy", "short", 0.02, True)]
    assert new_state["position"] == -1
    assert new_state["strategy_size"] == pytest.approx(0.02)
    assert new_state["risk_adverse_stop_fired"] is True
    assert new_state["tp_order_id"] is None


def test_live_binance_force_close_fraction_keeps_remaining_position(monkeypatch) -> None:
    module = importlib.import_module("live_binance_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(module, "cancel_tp_order", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "cancel_all_orders", lambda *_args, **_kwargs: True)
    positions = iter([0.06, 0.03])
    monkeypatch.setattr(module, "get_position", lambda *_args: next(positions))
    orders = []

    def fake_market_order(exchange, symbol, side, pos_side, sz):
        orders.append((side, pos_side, sz))
        return "binance-reduce-1"

    monkeypatch.setattr(module, "place_market_order", fake_market_order)
    state = {
        "position": 1,
        "strategy_size": 0.06,
        "entry_price": 2000.0,
        "trades": [],
        "tp_order_id": "tp-1",
    }

    new_state = module.force_close(
        exchange=object(),
        symbol="BTC/USDT:USDT",
        state=state,
        current_price=1900.0,
        best_bid=1899.9,
        best_ask=1900.0,
        tick_sz=0.1,
        lot_sz=0.001,
        reason="live_risk:adverse_stop",
        fraction=0.5,
        success_state_updates={"risk_adverse_stop_fired": True},
    )

    assert orders == [("sell", "long", 0.03)]
    assert new_state["position"] == 1
    assert new_state["strategy_size"] == pytest.approx(0.03)
    assert new_state["risk_adverse_stop_fired"] is True
    assert new_state["tp_order_id"] is None


def test_live_binance_fetch_candles_paginates_older_history(monkeypatch) -> None:
    module = importlib.import_module("live_binance_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    interval_ms = module.INTERVAL_SECONDS_MAP["5m"] * 1000
    calls = []

    class FakeExchange:
        def fetch_ohlcv(self, symbol, timeframe="5m", limit=500, since=None):
            calls.append(since)
            slots = range(10, 13) if since is None else range(5, 10)
            return [[i * interval_ms, 1, 2, 0.5, 1.5, 100] for i in slots]

    df = module.fetch_candles(FakeExchange(), "BTC/USDT:USDT", bar="5m", limit=6)

    assert calls[0] is None
    assert calls[1] is not None
    assert list(df["timestamp"]) == [i * interval_ms for i in [7, 8, 9, 10, 11, 12]]


def test_live_nado_close_uses_shared_close_plan(monkeypatch) -> None:
    module = importlib.import_module("live_nado_quant")
    monkeypatch.setattr(module, "log_message", lambda _msg: None)
    monkeypatch.setattr(
        module,
        "plan_close_order",
        lambda **_kwargs: CloseOrderPlan(
            action="taker",
            side="sell",
            position_side="long",
            size=0.02,
            price=2099.8,
            pnl_pct=-0.05,
            trade_type="CLOSE_LONG_Taker(SL)",
        ),
    )

    class FakeTrader:
        def __init__(self) -> None:
            self.orders = []

        def get_position(self, _product_id):
            return 0.02

        def cancel_all_orders(self, _product_id):
            return True

        def place_order(self, **kwargs):
            self.orders.append(kwargs)
            return "digest-close-1"

    trader = FakeTrader()
    state = {"position": 1, "strategy_size": 0.02, "entry_price": 2200.0, "trades": []}
    new_state = module.execute_trade(
        signal_id=0,
        trader=trader,
        product_id=1,
        tick_size=0.1,
        size_increment=0.001,
        capital_per_trade=50.0,
        state=state,
        current_price=2100.0,
        best_bid=2099.9,
        best_ask=2100.0,
    )

    assert trader.orders[0]["order_type"] == module.OrderType.IOC
    assert trader.orders[0]["side"] == "sell"
    assert new_state["position"] == 0
    assert new_state["trades"][0]["type"] == "CLOSE_LONG_Taker(SL)"
