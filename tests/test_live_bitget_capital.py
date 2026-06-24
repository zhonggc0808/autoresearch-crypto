from __future__ import annotations

from live_bitget_quant import resolve_entry_margin_and_notional


def test_entry_notional_uses_configured_margin_when_balance_is_enough() -> None:
    margin, notional = resolve_entry_margin_and_notional(100.0, 200.0, 2.0)

    assert margin == 100.0
    assert notional == 200.0


def test_entry_notional_clamps_to_available_margin_with_buffer() -> None:
    margin, notional = resolve_entry_margin_and_notional(100.0, 50.0, 2.0)

    assert margin == 49.0
    assert notional == 98.0


def test_entry_notional_applies_risk_size_multiplier_after_balance_clamp() -> None:
    margin, notional = resolve_entry_margin_and_notional(100.0, 50.0, 2.0, 0.5)

    assert margin == 24.5
    assert notional == 49.0
