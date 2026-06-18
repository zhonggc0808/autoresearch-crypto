from __future__ import annotations

import pytest

from dex.live.profiles import risk_config_for_profile
from dex.live.risk import compute_adverse_move, evaluate_live_risk


def _combo_config() -> dict:
    return risk_config_for_profile("channel_breakout_v2_3_combo_balanced")


def test_risk_profile_none_is_noop_for_old_versions() -> None:
    decision = evaluate_live_risk(
        {"risk_profile": "none"},
        {"risk_peak_equity": 10_000.0},
        position=1,
        entry_price=100.0,
        current_close=90.0,
        current_equity=8_000.0,
    )

    assert decision.action == "none"
    assert decision.allow_new_entry is True
    assert decision.entry_size_multiplier == 1.0
    assert decision.state_updates == {}


def test_compute_adverse_move_for_long_and_short() -> None:
    assert compute_adverse_move(1, 100.0, 95.0) == pytest.approx(-0.05)
    assert compute_adverse_move(-1, 100.0, 105.0) == pytest.approx(-0.05)


def test_combo_balanced_reduces_half_on_long_adverse_close() -> None:
    decision = evaluate_live_risk(
        _combo_config(),
        {"risk_peak_equity": 10_000.0, "bar_count": 2, "confirmed_entry_bar": 1},
        position=1,
        entry_price=100.0,
        current_close=94.9,
        current_equity=9_900.0,
    )

    assert decision.action == "reduce"
    assert decision.reason == "adverse_stop"
    assert decision.size_fraction == 0.5
    assert decision.allow_new_entry is False
    assert decision.state_updates["risk_adverse_stop_fired"] is True


def test_combo_balanced_does_not_repeat_adverse_reduce() -> None:
    decision = evaluate_live_risk(
        _combo_config(),
        {"risk_adverse_stop_fired": True, "bar_count": 3, "confirmed_entry_bar": 1},
        position=1,
        entry_price=100.0,
        current_close=90.0,
        current_equity=9_900.0,
    )

    assert decision.action == "none"


def test_combo_balanced_honors_entry_bar_immunity() -> None:
    decision = evaluate_live_risk(
        _combo_config(),
        {"bar_count": 5, "confirmed_entry_bar": 5},
        position=-1,
        entry_price=100.0,
        current_close=110.0,
        current_equity=9_900.0,
    )

    assert decision.action == "none"
    assert "risk_adverse_stop_fired" not in decision.state_updates


def test_combo_balanced_dd_sizing_and_no_new_entry() -> None:
    risk = _combo_config()
    sized = evaluate_live_risk(
        risk,
        {"risk_peak_equity": 10_000.0},
        position=0,
        entry_price=0.0,
        current_close=100.0,
        current_equity=8_600.0,
    )
    blocked = evaluate_live_risk(
        risk,
        {"risk_peak_equity": 10_000.0},
        position=0,
        entry_price=0.0,
        current_close=100.0,
        current_equity=8_000.0,
    )

    assert sized.entry_size_multiplier == pytest.approx(0.425 * 0.65)
    assert sized.allow_new_entry is True
    assert blocked.allow_new_entry is False


def test_combo_balanced_kill_switch_closes_existing_position() -> None:
    decision = evaluate_live_risk(
        _combo_config(),
        {"risk_peak_equity": 10_000.0},
        position=-1,
        entry_price=100.0,
        current_close=110.0,
        current_equity=7_500.0,
    )

    assert decision.action == "close"
    assert decision.reason == "kill_switch"
    assert decision.size_fraction == 1.0
    assert decision.allow_new_entry is False
