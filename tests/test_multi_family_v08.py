#!/usr/bin/env python3
"""Test suite for v0.8 Multi-Family Search.

Tests:
    - Family registry returns correct definitions
    - Old channel_breakout candidate continues validating
    - New volatility_filtered_breakout candidate passes schema
    - Unregistered family rejected
    - Family-specific param out-of-bounds rejected
    - Allowed_change validated per family
    - Executor fork preserves family
    - Batch trial with new family

Run:
    uv run pytest tests/test_multi_family_v08.py -v --tb=short
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
LLM_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"
LLM_RUNS_DIR = PROJECT_DIR / "research_workspace" / "llm_runs"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CHANNEL_BREAKOUT = {
    "experiment_id": "exp_9999",
    "parent_id": "channel_breakout_v2_1_balanced",
    "candidate_role": "standalone",
    "strategy": "channel_breakout",
    "base": "v2.1_balanced",
    "status": "research_only",
    "hypothesis": "Testing that channel breakout still validates after multi-family schema hardening.",
    "expected_behavior_change": "Should pass validation as before.",
    "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
    "params": {
        "strategy_type": "regime_permission_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "bull": {"candidate": "test", "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                 "permission": {"allow_long": True, "allow_short": False}},
        "bear": {"candidate": "test", "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                 "permission": {"allow_long": True, "allow_short": True}},
        "neutral": {"candidate": "test", "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                    "permission": {"allow_long": True, "allow_short": True}},
    },
}

VALID_VOLATILITY_FILTERED = {
    "experiment_id": "exp_9998",
    "parent_id": "channel_breakout_v2_1_balanced",
    "candidate_role": "standalone",
    "strategy": "volatility_filtered_breakout",
    "base": "v2.1_balanced",
    "status": "research_only",
    "hypothesis": "Testing volatility filtered breakout validation for multi-family schema support.",
    "expected_behavior_change": "Should pass family-specific schema validation.",
    "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
    "params": {
        "strategy_type": "volatility_filtered_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "volatility_filter": {
            "enabled": True,
            "lookback": 288,
            "mode": "exclude_extreme",
            "low_quantile": 0.05,
            "high_quantile": 0.95,
        },
        "bull": {"candidate": "test", "strategy_params": {"entry_lookback": 240, "min_hold_bars": 48},
                 "permission": {"allow_long": True, "allow_short": False}},
        "bear": {"candidate": "test", "strategy_params": {"entry_lookback": 240, "min_hold_bars": 48},
                 "permission": {"allow_long": True, "allow_short": True}},
        "neutral": {"candidate": "test", "strategy_params": {"entry_lookback": 240, "min_hold_bars": 48},
                    "permission": {"allow_long": True, "allow_short": True}},
    },
}

VALID_EXIT_LOGIC_VARIANT = {
    "experiment_id": "exp_9997",
    "parent_id": "channel_breakout_v2_1_balanced",
    "candidate_role": "standalone",
    "strategy": "exit_logic_variant",
    "base": "v2.1_balanced",
    "status": "research_only",
    "hypothesis": "Testing exit logic variant validation for multi-family schema support.",
    "expected_behavior_change": "Should pass family-specific schema validation with exit_logic section.",
    "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
    "params": {
        "strategy_type": "exit_logic_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "exit_logic": {
            "exit_lookback": 288,
            "take_profit_pct": 0.05,
            "stop_loss_pct": 0.10,
            "max_hold_bars": 720,
            "trailing_stop": True,
        },
        "bull": {"candidate": "test", "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                 "permission": {"allow_long": True, "allow_short": False}},
        "bear": {"candidate": "test", "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                 "permission": {"allow_long": True, "allow_short": True}},
        "neutral": {"candidate": "test", "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                    "permission": {"allow_long": True, "allow_short": True}},
    },
}

VALID_FIXED_POSITION_SCALER = copy.deepcopy(VALID_CHANNEL_BREAKOUT)
VALID_FIXED_POSITION_SCALER["experiment_id"] = "exp_9992"
VALID_FIXED_POSITION_SCALER["strategy"] = "fixed_position_scaler"
VALID_FIXED_POSITION_SCALER["params"]["strategy_type"] = "position_sized_channel_breakout"
VALID_FIXED_POSITION_SCALER["params"]["position_sizing"] = {
    "mode": "fixed_fraction",
    "fixed_fraction": 0.50,
}

VALID_DRAWDOWN_CONTROL_SCALER = copy.deepcopy(VALID_CHANNEL_BREAKOUT)
VALID_DRAWDOWN_CONTROL_SCALER["experiment_id"] = "exp_9991"
VALID_DRAWDOWN_CONTROL_SCALER["strategy"] = "drawdown_control_scaler"
VALID_DRAWDOWN_CONTROL_SCALER["params"]["strategy_type"] = "drawdown_control_channel_breakout"
VALID_DRAWDOWN_CONTROL_SCALER["params"]["position_sizing"] = {
    "mode": "close_drawdown_scale",
    "base_fraction": 0.30,
    "reduced_fraction": 0.05,
    "drawdown_threshold": 0.15,
    "lookback_bars": 288,
}

VALID_EXIT_LOGIC_WITH_PROFIT_LOCK = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
VALID_EXIT_LOGIC_WITH_PROFIT_LOCK["experiment_id"] = "exp_9996"
VALID_EXIT_LOGIC_WITH_PROFIT_LOCK["params"]["exit_logic"]["profit_lock"] = {
    "enabled": True,
    "activate_profit_pct": 0.08,
    "giveback_ratio": 0.35,
    "trailing_atr_multiplier": 3.0,
    "atr_period": 14,
    "min_hold_bars_before_lock": 72,
}

VALID_EXIT_LOGIC_WITH_DISABLED_EXIT_LOOKBACK = copy.deepcopy(VALID_EXIT_LOGIC_WITH_PROFIT_LOCK)
VALID_EXIT_LOGIC_WITH_DISABLED_EXIT_LOOKBACK["experiment_id"] = "exp_9995"
VALID_EXIT_LOGIC_WITH_DISABLED_EXIT_LOOKBACK["params"]["exit_logic"]["exit_lookback"] = 0

VALID_EXIT_LOGIC_WITH_REGIME_PROFIT_LOCK = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
VALID_EXIT_LOGIC_WITH_REGIME_PROFIT_LOCK["experiment_id"] = "exp_9994"
VALID_EXIT_LOGIC_WITH_REGIME_PROFIT_LOCK["params"]["exit_logic"]["profit_lock"] = {
    "enabled": False,
    "activate_profit_pct": 0.20,
    "giveback_ratio": 0.80,
    "trailing_atr_multiplier": 0.0,
    "atr_period": 14,
    "min_hold_bars_before_lock": 0,
}
VALID_EXIT_LOGIC_WITH_REGIME_PROFIT_LOCK["params"]["exit_logic"]["profit_lock_by_regime"] = {
    "bear": {
        "enabled": True,
        "activate_profit_pct": 0.08,
        "giveback_ratio": 0.35,
        "trailing_atr_multiplier": 3.0,
        "atr_period": 14,
        "min_hold_bars_before_lock": 72,
    }
}

VALID_EXIT_LOGIC_WITH_MATURE_GUARDS = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
VALID_EXIT_LOGIC_WITH_MATURE_GUARDS["experiment_id"] = "exp_9993"
VALID_EXIT_LOGIC_WITH_MATURE_GUARDS["params"]["exit_logic"]["mature_trend_exit"] = {
    "enabled": True,
    "activate_mfe_pct": 1.20,
    "daily_ema_period": 20,
    "use_completed_daily_bar": True,
    "min_hold_bars_before_exit": 0,
}
VALID_EXIT_LOGIC_WITH_MATURE_GUARDS["params"]["exit_logic"]["bear_cooldown"] = {
    "enabled": True,
    "loss_streak": 2,
    "cooldown_bars": 864,
    "scope": "bear_entry_trades",
}

INVALID_UNREGISTERED_FAMILY = copy.deepcopy(VALID_CHANNEL_BREAKOUT)
INVALID_UNREGISTERED_FAMILY["strategy"] = "lstm_strategy"

INVALID_VOLATILITY_PARAM = copy.deepcopy(VALID_VOLATILITY_FILTERED)
INVALID_VOLATILITY_PARAM["params"]["volatility_filter"]["lookback"] = 2000  # above max 1440

INVALID_WRONG_STRATEGY_TYPE = copy.deepcopy(VALID_VOLATILITY_FILTERED)
INVALID_WRONG_STRATEGY_TYPE["params"]["strategy_type"] = "regime_permission_channel_breakout"

INVALID_EXIT_LOGIC_MISSING = copy.deepcopy(VALID_CHANNEL_BREAKOUT)
INVALID_EXIT_LOGIC_MISSING["strategy"] = "exit_logic_variant"
INVALID_EXIT_LOGIC_MISSING["params"]["strategy_type"] = "exit_logic_channel_breakout"
# Missing exit_logic section

INVALID_EXIT_LOGIC_TP_OUT = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
INVALID_EXIT_LOGIC_TP_OUT["params"]["exit_logic"]["take_profit_pct"] = 0.60  # above max 0.50

INVALID_EXIT_LOGIC_SL_OUT = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
INVALID_EXIT_LOGIC_SL_OUT["params"]["exit_logic"]["stop_loss_pct"] = 0.35  # above max 0.30

INVALID_EXIT_LOGIC_LOOKBACK_OUT = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
INVALID_EXIT_LOGIC_LOOKBACK_OUT["params"]["exit_logic"]["exit_lookback"] = 5  # below min 12

INVALID_EXIT_LOGIC_PROFIT_LOCK_GIVEBACK_OUT = copy.deepcopy(VALID_EXIT_LOGIC_WITH_PROFIT_LOCK)
INVALID_EXIT_LOGIC_PROFIT_LOCK_GIVEBACK_OUT["params"]["exit_logic"]["profit_lock"][
    "giveback_ratio"
] = 1.5

INVALID_EXIT_LOGIC_MATURE_USES_LIVE_DAILY = copy.deepcopy(VALID_EXIT_LOGIC_WITH_MATURE_GUARDS)
INVALID_EXIT_LOGIC_MATURE_USES_LIVE_DAILY["params"]["exit_logic"]["mature_trend_exit"][
    "use_completed_daily_bar"
] = False


# ===================================================================
# Test: Family registry
# ===================================================================


class TestFamilyRegistry:
    """Verify the family registry returns correct definitions."""

    def test_registered_families(self):
        from scripts.family_registry import is_valid, list_families

        assert is_valid("channel_breakout")
        assert is_valid("volatility_filtered_breakout")
        assert not is_valid("unknown_family")
        assert len(list_families()) >= 2

    def test_channel_breakout_def(self):
        from scripts.family_registry import get
        fd = get("channel_breakout")
        assert fd is not None
        assert fd.strategy_type == "regime_permission_channel_breakout"
        assert "entry_lookback" in fd.strategy_params

    def test_volatility_filtered_def(self):
        from scripts.family_registry import get
        fd = get("volatility_filtered_breakout")
        assert fd is not None
        assert fd.strategy_type == "volatility_filtered_channel_breakout"
        assert fd.allowed_change_nested is not None
        assert "volatility_filter" in fd.allowed_change_nested

    def test_search_space_summary(self):
        from scripts.family_registry import search_space_summary
        ss = search_space_summary("volatility_filtered_breakout")
        assert "volatility_filter" in ss
        assert "entry_lookback" in ss


# ===================================================================
# Test: Family-specific validation
# ===================================================================


class TestFamilyValidation:
    """Verify candidate validation works per family."""

    def test_channel_breakout_still_valid(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(VALID_CHANNEL_BREAKOUT, check_uniqueness=False)
        assert errors == [], f"channel_breakout failed: {errors}"

    def test_volatility_filtered_valid(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(VALID_VOLATILITY_FILTERED, check_uniqueness=False)
        assert errors == [], f"volatility_filtered_breakout failed: {errors}"

    def test_unregistered_family_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_UNREGISTERED_FAMILY, check_uniqueness=False)
        assert errors, "Expected error for unregistered family"
        assert any("Unknown strategy" in e for e in errors)

    def test_volatility_param_out_of_bounds(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_VOLATILITY_PARAM, check_uniqueness=False)
        assert errors, "Expected error for out-of-bounds volatility param"

    def test_wrong_strategy_type_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_WRONG_STRATEGY_TYPE, check_uniqueness=False)
        assert errors, "Expected error for wrong strategy_type"


# ===================================================================
# Test: Allowed_change per family
# ===================================================================


class TestAllowedChangeFamily:
    """Verify allowed_change validation is family-aware."""

    def test_channel_breakout_allowed_change(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change("channel_breakout", {"entry_lookback": 300})
        assert errors == [], f"Expected no errors: {errors}"

    def test_volatility_filtered_allowed_change(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "volatility_filtered_breakout",
            {"volatility_filter": {"lookback": 500, "mode": "exclude_low"}},
        )
        assert errors == [], f"Expected no errors: {errors}"

    def test_volatility_filter_field_out_of_bounds(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "volatility_filtered_breakout",
            {"volatility_filter": {"lookback": 5000}},
        )
        assert errors, "Expected error for out-of-bounds lookback"

    def test_cross_family_allowed_change_rejected(self):
        """volatility_filter field should not be valid for channel_breakout family."""
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "channel_breakout",
            {"volatility_filter": {"lookback": 500}},
        )
        assert errors, "Expected error: volatility_filter unknown for channel_breakout"


# ===================================================================
# Test: Execute fork preserves family
# ===================================================================


class TestExecuteForkFamily:
    """Verify executor fork preserves the family identity."""

    def test_fork_preserves_channel_breakout(self):
        from scripts.execute_action import execute_action

        # Ensure source candidate exists
        LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        cand_path = LLM_CANDIDATES_DIR / "exp_8888.json"
        cb_spec = copy.deepcopy(VALID_CHANNEL_BREAKOUT)
        cb_spec["experiment_id"] = "exp_8888"
        cand_path.write_text(json.dumps(cb_spec), encoding="utf-8")

        action = {
            "action": "fork",
            "source_candidate_id": "exp_8888",
            "target_family": "channel_breakout",
            "rationale": "Test fork preserves family identity for channel breakout validation.",
            "allowed_change": {"entry_lookback": 300},
        }

        result = execute_action(action)
        if result["status"] != "executed":
            pytest.fail(f"Fork failed: {result.get('errors', result.get('error', '?'))}")

        # Read forked candidate
        forked_id = result["experiment_id"]
        forked_path = LLM_CANDIDATES_DIR / f"{forked_id}.json"
        forked = json.loads(forked_path.read_text(encoding="utf-8"))
        assert forked["strategy"] == "channel_breakout"

        # Cleanup
        cand_path.unlink()
        forked_path.unlink()

    def test_fork_preserves_volatility_filtered(self):
        from scripts.execute_action import execute_action

        LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        cand_path = LLM_CANDIDATES_DIR / "exp_8887.json"
        vf_spec = copy.deepcopy(VALID_VOLATILITY_FILTERED)
        vf_spec["experiment_id"] = "exp_8887"
        cand_path.write_text(json.dumps(vf_spec), encoding="utf-8")

        action = {
            "action": "fork",
            "source_candidate_id": "exp_8887",
            "target_family": "volatility_filtered_breakout",
            "rationale": "Test fork preserves volatility_filtered family with allowed changes.",
            "allowed_change": {"volatility_filter": {"lookback": 500, "mode": "exclude_low"}},
        }

        result = execute_action(action)
        assert result["status"] == "executed"

        forked_id = result["experiment_id"]
        forked_path = LLM_CANDIDATES_DIR / f"{forked_id}.json"
        forked = json.loads(forked_path.read_text(encoding="utf-8"))
        assert forked["strategy"] == "volatility_filtered_breakout"

        # Verify volatility_filter was applied
        params = forked["params"]
        assert params["volatility_filter"]["lookback"] == 500
        assert params["volatility_filter"]["mode"] == "exclude_low"

        cand_path.unlink()
        forked_path.unlink()


# ===================================================================
# Test: Batch trial with volatility family
# ===================================================================


class TestVolatilityBatchTrial:
    """Run a mini batch trial with the volatility_filtered_breakout family."""

    def test_single_volatility_cycle(self):
        """One complete research cycle using volatility_filtered_breakout."""
        from scripts.execute_action import execute_action

        # Write a volatility_filtered candidate to be picked up by the cycle
        LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        cand = copy.deepcopy(VALID_VOLATILITY_FILTERED)
        cand["experiment_id"] = "exp_8886"
        cand_path = LLM_CANDIDATES_DIR / "exp_8886.json"
        cand_path.write_text(json.dumps(cand), encoding="utf-8")

        # Execute fork
        action = {
            "action": "fork",
            "source_candidate_id": "exp_8886",
            "target_family": "volatility_filtered_breakout",
            "rationale": "Batch trial volatility family fork for multi-family validation testing.",
            "allowed_change": {"entry_lookback": 200, "volatility_filter": {"mode": "exclude_high"}},
        }
        result = execute_action(action)
        assert result["status"] == "executed"

        forked_id = result["experiment_id"]
        forked_path = LLM_CANDIDATES_DIR / f"{forked_id}.json"
        assert forked_path.exists()

        forked = json.loads(forked_path.read_text(encoding="utf-8"))
        assert forked["strategy"] == "volatility_filtered_breakout"

        # Validate forked candidate
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(forked, check_uniqueness=False)
        assert errors == [], f"Forked volatility candidate failed: {errors}"

        # Cleanup
        cand_path.unlink()
        forked_path.unlink()


# ===================================================================
# Test: Exit Logic Variant family
# ===================================================================


class TestExitLogicVariant:
    """Verify exit_logic_variant family is properly registered and validated."""

    def test_registered(self):
        from scripts.family_registry import get, is_valid

        assert is_valid("exit_logic_variant")
        fd = get("exit_logic_variant")
        assert fd is not None
        assert fd.strategy_type == "exit_logic_channel_breakout"
        assert "exit_logic" in fd.allowed_change_nested

    def test_fixed_position_scaler_registered(self):
        from scripts.family_registry import get, is_valid

        assert is_valid("fixed_position_scaler")
        fd = get("fixed_position_scaler")
        assert fd is not None
        assert fd.strategy_type == "position_sized_channel_breakout"
        assert "position_sizing" in fd.allowed_change_nested

    def test_drawdown_control_scaler_registered(self):
        from scripts.family_registry import get, is_valid

        assert is_valid("drawdown_control_scaler")
        fd = get("drawdown_control_scaler")
        assert fd is not None
        assert fd.strategy_type == "drawdown_control_channel_breakout"
        assert "position_sizing" in fd.allowed_change_nested

    def test_valid_candidate_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(VALID_EXIT_LOGIC_VARIANT, check_uniqueness=False)
        assert errors == [], f"Exit logic variant failed: {errors}"

    def test_fixed_position_scaler_candidate_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(VALID_FIXED_POSITION_SCALER, check_uniqueness=False)
        assert errors == [], f"Fixed position scaler failed: {errors}"

    def test_drawdown_control_scaler_candidate_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(VALID_DRAWDOWN_CONTROL_SCALER, check_uniqueness=False)
        assert errors == [], f"Drawdown control scaler failed: {errors}"

    def test_valid_profit_lock_candidate_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(VALID_EXIT_LOGIC_WITH_PROFIT_LOCK, check_uniqueness=False)
        assert errors == [], f"Exit logic profit-lock variant failed: {errors}"

    def test_exit_lookback_zero_disables_channel_exit_and_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(
            VALID_EXIT_LOGIC_WITH_DISABLED_EXIT_LOOKBACK,
            check_uniqueness=False,
        )
        assert errors == [], f"exit_lookback=0 should disable channel exit: {errors}"

    def test_regime_specific_profit_lock_candidate_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(
            VALID_EXIT_LOGIC_WITH_REGIME_PROFIT_LOCK,
            check_uniqueness=False,
        )
        assert errors == [], f"regime-specific profit-lock variant failed: {errors}"

    def test_mature_guard_candidate_passes(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(
            VALID_EXIT_LOGIC_WITH_MATURE_GUARDS,
            check_uniqueness=False,
        )
        assert errors == [], f"mature trend guard variant failed: {errors}"

    def test_missing_exit_logic_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_EXIT_LOGIC_MISSING, check_uniqueness=False)
        assert errors, "Expected error for missing exit_logic section"

    def test_tp_out_of_bounds_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_EXIT_LOGIC_TP_OUT, check_uniqueness=False)
        assert errors, "Expected error for take_profit_pct > 0.50"

    def test_sl_out_of_bounds_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_EXIT_LOGIC_SL_OUT, check_uniqueness=False)
        assert errors, "Expected error for stop_loss_pct > 0.30"

    def test_exit_lookback_out_of_bounds_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(INVALID_EXIT_LOGIC_LOOKBACK_OUT, check_uniqueness=False)
        assert errors, "Expected error for exit_lookback < 12"

    def test_profit_lock_giveback_out_of_bounds_rejected(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(
            INVALID_EXIT_LOGIC_PROFIT_LOCK_GIVEBACK_OUT,
            check_uniqueness=False,
        )
        assert errors, "Expected error for profit_lock.giveback_ratio > 1.0"

    def test_mature_guard_rejects_non_completed_daily_bar(self):
        from scripts.validate_candidate_v08 import validate_candidate
        errors = validate_candidate(
            INVALID_EXIT_LOGIC_MATURE_USES_LIVE_DAILY,
            check_uniqueness=False,
        )
        assert errors, "Expected error for use_completed_daily_bar=false"

    def test_allowed_change_exit_logic(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {"exit_logic": {"take_profit_pct": 0.08, "stop_loss_pct": 0.12, "trailing_stop": False}},
        )
        assert errors == [], f"Expected no errors: {errors}"

    def test_allowed_change_profit_lock(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {
                "exit_logic": {
                    "profit_lock": {
                        "enabled": True,
                        "activate_profit_pct": 0.08,
                        "giveback_ratio": 0.35,
                    }
                }
            },
        )
        assert errors == [], f"Expected no errors: {errors}"

    def test_allowed_change_regime_specific_profit_lock(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {
                "exit_logic": {
                    "profit_lock_by_regime": {
                        "bear": {
                            "enabled": True,
                            "activate_profit_pct": 0.18,
                            "giveback_ratio": 0.65,
                            "trailing_atr_multiplier": 5.0,
                        }
                    }
                }
            },
        )
        assert errors == [], f"Expected no errors: {errors}"

    def test_allowed_change_mature_guard_and_bear_cooldown(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {
                "exit_logic": {
                    "mature_trend_exit": {
                        "enabled": True,
                        "activate_mfe_pct": 1.20,
                        "daily_ema_period": 20,
                        "use_completed_daily_bar": True,
                    },
                    "bear_cooldown": {
                        "enabled": True,
                        "loss_streak": 2,
                        "cooldown_bars": 864,
                    },
                }
            },
        )
        assert errors == [], f"Expected no errors: {errors}"

    def test_allowed_change_regime_specific_profit_lock_rejects_unknown_regime(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {
                "exit_logic": {
                    "profit_lock_by_regime": {
                        "sideways": {
                            "enabled": True,
                            "activate_profit_pct": 0.18,
                        }
                    }
                }
            },
        )
        assert errors, "Expected error for unknown profit_lock_by_regime key"

    def test_exit_allowed_change_out_of_bounds(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {"exit_logic": {"take_profit_pct": 0.60}},
        )
        assert errors, "Expected error for take_profit_pct > 0.50"

    def test_cross_family_volatility_in_exit_rejected(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "exit_logic_variant",
            {"volatility_filter": {"lookback": 500}},
        )
        assert errors, "Expected error: volatility_filter unknown for exit_logic_variant"

    def test_cross_family_exit_in_cb_rejected(self):
        from scripts.family_registry import validate_allowed_change
        errors = validate_allowed_change(
            "channel_breakout",
            {"exit_logic": {"take_profit_pct": 0.08}},
        )
        assert errors, "Expected error: exit_logic unknown for channel_breakout"


# ===================================================================
# Test: Executor fork for exit_logic_variant
# ===================================================================


class TestExecuteForkExitLogic:
    """Verify executor fork preserves exit_logic_variant family."""

    def test_fork_preserves_exit_logic(self):
        from scripts.execute_action import execute_action

        LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        cand_path = LLM_CANDIDATES_DIR / "exp_8885.json"
        el_spec = copy.deepcopy(VALID_EXIT_LOGIC_VARIANT)
        el_spec["experiment_id"] = "exp_8885"
        cand_path.write_text(json.dumps(el_spec), encoding="utf-8")

        action = {
            "action": "fork",
            "source_candidate_id": "exp_8885",
            "target_family": "exit_logic_variant",
            "rationale": "Test fork preserves exit_logic_variant family with allowed changes.",
            "allowed_change": {"exit_logic": {"take_profit_pct": 0.08, "max_hold_bars": 500}},
        }

        result = execute_action(action)
        assert result["status"] == "executed"

        forked_id = result["experiment_id"]
        forked_path = LLM_CANDIDATES_DIR / f"{forked_id}.json"
        forked = json.loads(forked_path.read_text(encoding="utf-8"))
        assert forked["strategy"] == "exit_logic_variant"

        # Verify exit_logic params were applied
        params = forked["params"]
        assert params["exit_logic"]["take_profit_pct"] == 0.08
        assert params["exit_logic"]["max_hold_bars"] == 500

        cand_path.unlink()
        forked_path.unlink()
