from __future__ import annotations

from live_bitget_quant import check_stop_loss


def test_max_hold_bars_zero_disables_time_exit() -> None:
    should_close, reason = check_stop_loss(
        {"position": 1, "entry_price": 100.0, "entry_bar": 0, "bar_count": 690},
        current_price=120.0,
        stop_loss_pct=0.0,
        max_hold_bars=0,
    )

    assert should_close is False
    assert reason == ""
