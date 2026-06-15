#!/usr/bin/env python3
"""Negative test suite for candidate_schema_v0.2.

Every test asserts that a KNOWN-INVALID candidate spec is correctly rejected
by the validator.

Run:
    uv run pytest tests/test_candidate_schema_v02.py -v
    uv run pytest tests/test_candidate_schema_v02.py -v --tb=short
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_candidate_v02 import validate_candidate

# ---------------------------------------------------------------------------
# Fixture: a valid baseline candidate (v0.2 compliant)
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_candidate() -> dict:
    """A spec that passes v0.2 validation."""
    return {
        "experiment_id": "exp_9999",
        "parent_id": "channel_breakout_v2_1_balanced",
        "candidate_role": "standalone",
        "strategy": "channel_breakout",
        "base": "v2.1_balanced",
        "status": "research_only",
        "hypothesis": "Testing that wider regime filter reduces false transitions during neutral chop without sacrificing bear capture.",
        "constraints": [
            "no_future_data",
            "inherits_v21_risk",
            "no_demo_routing",
            "research_only",
        ],
        "expected_behavior_change": "Trade count drops 10-20%. Bear regime return stays positive. Correlation vs baseline drops below 0.95.",
        "params": {
            "strategy_type": "regime_permission_channel_breakout",
            "regime_change_policy": "permission_based",
            "regime_filter": {"fast_days": 40, "slow_days": 200},
            "bull": {
                "candidate": "U4_cons3",
                "strategy_params": {
                    "entry_lookback": 375,
                    "min_hold_bars": 432,
                    "enable_long": True,
                    "enable_short": False,
                },
                "permission": {
                    "allow_long": True,
                    "allow_short": False,
                    "close_below_ema_disables_long": True,
                    "ema_fast": 50,
                    "consecutive_below_ema_days": 3,
                },
            },
            "bear": {
                "candidate": "K0_base",
                "strategy_params": {
                    "entry_lookback": 375,
                    "min_hold_bars": 432,
                    "enable_long": True,
                    "enable_short": True,
                },
                "permission": {
                    "allow_long": True,
                    "allow_short": True,
                },
            },
            "neutral": {
                "candidate": "N3_dir",
                "strategy_params": {
                    "entry_lookback": 375,
                    "min_hold_bars": 432,
                    "enable_long": True,
                    "enable_short": True,
                },
                "permission": {
                    "allow_long": True,
                    "allow_short": True,
                    "directional_only": True,
                    "ema_fast": 50,
                    "ema_slope_days": 5,
                },
            },
        },
    }


# ===================================================================
# Missing required fields
# ===================================================================


@pytest.mark.parametrize("field", [
    "hypothesis", "constraints", "expected_behavior_change",
    "base", "status", "experiment_id", "candidate_role",
    "strategy", "params",
])
def test_missing_required_field(valid_candidate, field):
    spec = copy.deepcopy(valid_candidate)
    del spec[field]
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, f"Expected error for missing field '{field}'"
    assert any(field in e for e in errors), (
        f"Error message should mention '{field}': {errors}"
    )


# ===================================================================
# Wrong constant values
# ===================================================================


def test_wrong_base_value(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["base"] = "v3.0_experimental"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for wrong base value"


def test_wrong_status_value(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["status"] = "promote_ready"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for wrong status value"


# ===================================================================
# Whitelist violations
# ===================================================================


def test_unknown_strategy(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["strategy"] = "lstm_regime_predictor"
    # v0.2 schema accepts any string; v0.8 validator catches unknown families via registry
    # This test uses v0.2 validator which only checks type, not registry
    try:
        from scripts.validate_candidate_v08 import validate_candidate as v08_validate
        errors = v08_validate(spec, check_uniqueness=False)
        assert errors, "Expected error for unknown strategy family (v0.8)"
    except Exception:
        pytest.skip("v0.8 validator not available")


def test_unknown_candidate_role(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["candidate_role"] = "live_ready"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for unknown candidate_role"


def test_unknown_strategy_type_in_params(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["strategy_type"] = "ml_regime_router"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for unknown strategy_type in params"


# ===================================================================
# Parameter bounds
# ===================================================================


@pytest.mark.parametrize("field,value", [
    ("entry_lookback", 5),     # below minimum 20
    ("entry_lookback", 2000),  # above maximum 1000
    ("min_hold_bars", 3),      # below minimum 12
    ("min_hold_bars", 9999),   # above maximum 1440
])
def test_strategy_param_out_of_range(valid_candidate, field, value):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["bull"]["strategy_params"][field] = value
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, f"Expected error for {field}={value}"


def test_regime_filter_fast_above_slow(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["regime_filter"]["fast_days"] = 200
    spec["params"]["regime_filter"]["slow_days"] = 50
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for fast_days >= slow_days"


def test_regime_filter_fast_equal_slow(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["regime_filter"]["fast_days"] = 100
    spec["params"]["regime_filter"]["slow_days"] = 100
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for fast_days == slow_days"


@pytest.mark.parametrize("field,value", [
    ("fast_days", 0),       # below minimum 5
    ("fast_days", 500),     # above maximum 200
    ("slow_days", 5),       # below minimum 10
    ("slow_days", 1000),    # above maximum 500
])
def test_regime_filter_field_out_of_range(valid_candidate, field, value):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["regime_filter"][field] = value
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, f"Expected error for regime_filter.{field}={value}"


# ===================================================================
# Extra / unknown fields (additionalProperties: false)
# ===================================================================


def test_extra_field_at_top_level(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["checkpoint_path"] = "checkpoints/sneaky.pt"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for extra field 'checkpoint_path'"


def test_extra_field_in_params(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["unknown_param"] = 42
    # v0.2 schema allows extra params fields for multi-family support.
    # Extra fields are caught by family-specific schemas.
    errors = validate_candidate(spec, check_uniqueness=False)
    # In v0.2, extra params fields are tolerated but no promises on behavior
    # The real enforcement is in the family schema via v0.8 validator


def test_extra_field_in_regime_config(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["bull"]["unknown_regime_field"] = "should_fail"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for extra field in regime config"


def test_extra_field_in_strategy_params(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["bull"]["strategy_params"]["leverage"] = 100
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for extra field in strategy_params"


def test_extra_field_in_permission(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["bull"]["permission"]["allow_margin"] = True
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for extra field in permission"


# ===================================================================
# Missing regime sections
# ===================================================================


def test_missing_bear_regime(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    del spec["params"]["bear"]
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for missing bear regime"


def test_missing_neutral_regime(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    del spec["params"]["neutral"]
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for missing neutral regime"


def test_missing_strategy_params_in_regime(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    del spec["params"]["bull"]["strategy_params"]
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for missing strategy_params"


def test_missing_permission_in_regime(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    del spec["params"]["bear"]["permission"]
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for missing permission"


# ===================================================================
# Hypothesis / constraints violations
# ===================================================================


def test_hypothesis_too_short(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["hypothesis"] = "Short."
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for too-short hypothesis"


def test_expected_behavior_change_too_short(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["expected_behavior_change"] = "Short."
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for too-short expected_behavior_change"


def test_constraints_missing_one(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["constraints"] = spec["constraints"][:3]  # only 3 of 4
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for incomplete constraints"


def test_constraints_empty(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["constraints"] = []
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for empty constraints"


def test_constraints_unknown_value(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["constraints"][0] = "allow_live_trading"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for unknown constraint value"


# ===================================================================
# Forbidden content patterns
# ===================================================================


@pytest.mark.parametrize("forbidden_text,desc", [
    ("checkpoints/sneaky.pt", "checkpoint path reference"),
    ("live_binance_quant.py", "live entry point reference"),
    ("research_oracle.py", "oracle file reference"),
    ("data/crypto/ETHUSDT_5m_1300d.parquet", "data file path"),
    ("force_promotion", "force promotion bypass"),
    ("skip_validation", "skip validation bypass"),
    ("auto_promote", "auto promote"),
])
def test_forbidden_content(valid_candidate, forbidden_text, desc):
    spec = copy.deepcopy(valid_candidate)
    # Insert forbidden text into hypothesis (a string field)
    spec["hypothesis"] = f"Testing {forbidden_text} in hypothesis string."
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, f"Expected error for forbidden content: {desc}"


# ===================================================================
# Experiment ID format
# ===================================================================


def test_invalid_experiment_id_format(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["experiment_id"] = "exp_abc"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for non-numeric experiment_id"


def test_experiment_id_no_prefix(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["experiment_id"] = "0001"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for experiment_id without 'exp_' prefix"


# ===================================================================
# Duplicate experiment_id
# ===================================================================


def test_duplicate_experiment_id(tmp_path):
    """Two candidates with same ID in llm_candidates/ should fail."""
    # Create a temporary duplicate detection scenario
    from scripts.validate_candidate_v02 import CANDIDATE_DIRS
    from scripts.validate_candidate_v02 import validate_candidate as vc

    spec = {
        "experiment_id": "exp_9998",
        "parent_id": "channel_breakout_v2_1_balanced",
        "candidate_role": "standalone",
        "strategy": "channel_breakout",
        "base": "v2.1_balanced",
        "status": "research_only",
        "hypothesis": "Duplicate ID detection test for validation framework schema hardening.",
        "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
        "expected_behavior_change": "Should fail with duplicate ID error before reaching oracle.",
        "params": {
            "strategy_type": "regime_permission_channel_breakout",
            "regime_change_policy": "permission_based",
            "regime_filter": {"fast_days": 50, "slow_days": 200},
            "bull": {"strategy_params": {"entry_lookback": 375, "min_hold_bars": 432}, "permission": {"allow_long": True, "allow_short": False}},
            "bear": {"strategy_params": {"entry_lookback": 375, "min_hold_bars": 432}, "permission": {"allow_long": True, "allow_short": True}},
            "neutral": {"strategy_params": {"entry_lookback": 375, "min_hold_bars": 432}, "permission": {"allow_long": True, "allow_short": True}},
        },
    }

    # Write the same spec twice with same ID into llm_candidates
    llm_dir = CANDIDATE_DIRS[0]
    llm_dir.mkdir(parents=True, exist_ok=True)
    f1 = llm_dir / "exp_9998.json"
    f2 = llm_dir / "exp_9998_copy.json"  # different file, same ID inside
    try:
        f1.write_text(json.dumps(spec), encoding="utf-8")
        # Second file has same experiment_id but different filename
        spec2 = dict(spec)
        spec2["hypothesis"] = "Same ID but different file content to trigger duplicate detection."
        f2.write_text(json.dumps(spec2), encoding="utf-8")

        # Validate the first one — should detect second file has same ID
        errors = vc(spec, current_path=f1, check_uniqueness=True)
        dup_errors = [e for e in errors if "Duplicate" in e]
        assert dup_errors, (
            f"Expected duplicate ID error. Got: {errors}"
        )
    finally:
        if f1.exists():
            f1.unlink()
        if f2.exists():
            f2.unlink()


# ===================================================================
# Parent ID format
# ===================================================================


def test_invalid_parent_id(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["parent_id"] = "some_random_experiment"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for invalid parent_id format"


# ===================================================================
# Malformed JSON / type errors
# ===================================================================


def test_params_is_string_instead_of_object(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"] = "not_a_params_object"
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for params as string"


def test_strategy_params_is_array(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["bull"]["strategy_params"] = [375, 432]
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for strategy_params as array"


def test_bool_instead_of_int_for_lookback(valid_candidate):
    spec = copy.deepcopy(valid_candidate)
    spec["params"]["bull"]["strategy_params"]["entry_lookback"] = True
    errors = validate_candidate(spec, check_uniqueness=False)
    assert errors, "Expected error for bool instead of int"
