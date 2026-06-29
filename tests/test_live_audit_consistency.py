from __future__ import annotations

import pandas as pd

from scripts.audit_live_consistency import _build_live_trade_rows, _normalize_live_events


def test_live_audit_ignores_canceled_maker_events() -> None:
    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-01 00:00:00", "2026-01-01 00:05:00", "2026-01-01 00:10:00"]
            )
        }
    )
    events = [
        {
            "time": "2026-01-01T08:01:00",
            "type": "SELL_SHORT_MAKER",
            "price": 100.0,
            "size": 0.02,
            "orderId": "open-filled",
            "order_status": "filled",
            "filled": True,
        },
        {
            "time": "2026-01-01T08:06:00",
            "type": "CLOSE_SHORT_Maker(TP)",
            "price": 95.0,
            "size": 0.02,
            "orderId": "close-canceled",
            "order_status": "canceled",
            "filled": False,
        },
        {
            "time": "2026-01-01T08:11:00",
            "type": "CLOSE_SHORT_Maker(TP)",
            "price": 94.0,
            "size": 0.02,
            "orderId": "close-filled",
            "order_status": "filled",
            "filled": True,
            "fee": "-0.01",
        },
    ]

    live_events = _normalize_live_events(events, df, live_time_offset_hours=-8)
    rows = _build_live_trade_rows(live_events)

    assert [event.order_id for event in live_events] == ["open-filled", "close-filled"]
    assert len(rows) == 1
    assert rows[0]["exit_order_id"] == "close-filled"
