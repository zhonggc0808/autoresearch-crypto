from __future__ import annotations

from dex.live.common import (
    append_funding_fee_record,
    attach_fee_fields_to_trade,
    extract_fee_fields,
)


def test_extract_fee_fields_keeps_exchange_fee_names() -> None:
    order = {
        "fee": {"cost": 0.0123, "currency": "USDT"},
        "fees": [{"cost": 0.0123, "currency": "USDT"}],
        "info": {
            "fee": "-0.0123",
            "feeCcy": "USDT",
            "fillFee": "-0.0123",
            "tradeFee": "0.0123",
            "rebate": "0",
        },
    }

    fee_fields = extract_fee_fields(order)

    assert fee_fields["fee"] == "-0.0123"
    assert fee_fields["feeCcy"] == "USDT"
    assert fee_fields["fillFee"] == "-0.0123"
    assert fee_fields["tradeFee"] == "0.0123"
    assert fee_fields["fees"] == [{"cost": 0.0123, "currency": "USDT"}]


def test_attach_fee_fields_to_matching_trade() -> None:
    trades = [{"orderId": "old"}, {"orderId": "abc123", "type": "BUY_OPEN_MAKER"}]

    attached = attach_fee_fields_to_trade(trades, "abc123", {"fee": "-0.01", "feeCcy": "USDT"})

    assert attached is True
    assert trades[-1]["fee"] == "-0.01"
    assert trades[-1]["feeCcy"] == "USDT"


def test_append_funding_fee_record_dedupes_and_caps() -> None:
    state = {}

    assert append_funding_fee_record(state, {"id": "funding-1", "amount": -0.02})
    assert not append_funding_fee_record(state, {"id": "funding-1", "amount": -0.02})
    assert append_funding_fee_record(state, {"id": "funding-2", "amount": 0.01}, max_records=1)

    assert state["funding_fee_records"] == [{"id": "funding-2", "amount": 0.01}]
