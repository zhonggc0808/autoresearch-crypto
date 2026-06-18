from __future__ import annotations

from dex.live.profiles import (
    get_live_strategy_profile,
    profile_checkpoint_map,
    resolve_checkpoint_path,
    risk_config_for_profile,
)


def test_live_profiles_keep_old_versions_risk_noop() -> None:
    for name in [
        "channel_breakout_v2",
        "channel_breakout_v2_1_balanced",
        "channel_breakout_v2_2_mtg_bcd",
    ]:
        profile = get_live_strategy_profile(name)
        assert profile.risk_profile == "none"
        assert risk_config_for_profile(name) == {"risk_profile": "none"}


def test_live_profile_v23_names_combo_balanced() -> None:
    profile = get_live_strategy_profile("channel_breakout_v2_3_combo_balanced")
    risk = risk_config_for_profile(profile.name)

    assert profile.strategy_version == "2.3"
    assert profile.checkpoint == "checkpoints/channel_breakout_375_432.pt"
    assert risk["risk_profile"] == "combo_balanced"
    assert risk["base_size"] == 0.425
    assert risk["adverse_stop"]["threshold_pct"] == -0.05
    assert risk["adverse_stop"]["action"] == "reduce_half"


def test_resolve_checkpoint_uses_override_or_profile() -> None:
    assert (
        resolve_checkpoint_path(None, "channel_breakout_v2_3_combo_balanced")
        == "checkpoints/channel_breakout_375_432.pt"
    )
    assert resolve_checkpoint_path("custom.pt", "channel_breakout_v2") == "custom.pt"


def test_profile_checkpoint_map_supports_all_live_entrypoints() -> None:
    profiles = profile_checkpoint_map()

    assert "channel_breakout_v2" in profiles
    assert "channel_breakout_v2_1_balanced" in profiles
    assert "channel_breakout_v2_2_mtg_bcd" in profiles
    assert "channel_breakout_v2_3_combo_balanced" in profiles
    assert "hybrid_mm" in profiles
