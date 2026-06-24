#!/usr/bin/env python3
"""Test suite for v0.4 LLM Candidate Generator.

All tests mock the LLM call to avoid requiring an API key.

Run:
    uv run pytest tests/test_llm_candidate_generator_v04.py -v --tb=short
"""

from __future__ import annotations

import json
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_LLM_RESPONSE = json.dumps({
    "parent_id": "channel_breakout_v2_1_balanced",
    "description": "Test: reduce entry_lookback to 300 for faster bear capture",
    "hypothesis": "Reducing entry_lookback from 375 to 300 in bear regime increases trade frequency by ~15% without degrading DD.",
    "expected_behavior_change": "Trade count increases ~15% in bear regime. IS DD stays above -50%. Bear regime return stays above 200%.",
    "params": {
        "strategy_type": "regime_permission_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "bull": {
            "candidate": "U4_cons3",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": False},
            "permission": {"allow_long": True, "allow_short": False,
                           "close_below_ema_disables_long": True,
                           "ema_fast": 50, "consecutive_below_ema_days": 3},
        },
        "bear": {
            "candidate": "K0_base_L300",
            "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True},
        },
        "neutral": {
            "candidate": "N3_dir",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True,
                           "directional_only": True, "ema_fast": 50,
                           "ema_slope_days": 5},
        },
    },
})

LLM_ERROR_RESPONSE = json.dumps({
    "error": "No valid hypothesis given current constraints.",
})

FORBIDDEN_CONTENT_RESPONSE = json.dumps({
    "parent_id": "channel_breakout_v2_1_balanced",
    "description": "Test with forbidden content",
    "hypothesis": "Testing checkpoint path injection to see if validation catches it.",
    "expected_behavior_change": "Should be rejected by forbidden content scanner.",
    "params": {
        "strategy_type": "regime_permission_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "bull": {
            "candidate": "test",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": False},
            "permission": {"allow_long": True, "allow_short": False},
        },
        "bear": {
            "candidate": "test",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True},
        },
        "neutral": {
            "candidate": "test",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True},
        },
    },
})


# ===================================================================
# Parse LLM response
# ===================================================================


class TestParseLLMResponse:
    """Verify LLM response parsing handles all edge cases."""

    def test_valid_json(self):
        from scripts.generate_candidate import _parse_llm_response
        result = _parse_llm_response(VALID_LLM_RESPONSE)
        assert result is not None
        assert result["description"] == (
            "Test: reduce entry_lookback to 300 for faster bear capture"
        )

    def test_json_in_markdown_fence(self):
        from scripts.generate_candidate import _parse_llm_response
        md_response = f"Here is the candidate:\n```json\n{VALID_LLM_RESPONSE}\n```\n"
        result = _parse_llm_response(md_response)
        assert result is not None
        assert "description" in result

    def test_json_in_markdown_without_lang(self):
        from scripts.generate_candidate import _parse_llm_response
        md_response = f"```\n{VALID_LLM_RESPONSE}\n```\n"
        result = _parse_llm_response(md_response)
        assert result is not None
        assert "description" in result

    def test_non_json_response(self):
        from scripts.generate_candidate import _parse_llm_response
        result = _parse_llm_response("This is not JSON at all.")
        assert result is None

    def test_error_response(self):
        from scripts.generate_candidate import _parse_llm_response
        result = _parse_llm_response(LLM_ERROR_RESPONSE)
        assert result is not None
        assert "error" in result

    def test_empty_response(self):
        from scripts.generate_candidate import _parse_llm_response
        result = _parse_llm_response("")
        assert result is None

    def test_response_with_extra_text_after_json(self):
        from scripts.generate_candidate import _parse_llm_response
        text = VALID_LLM_RESPONSE + "\nSome trailing text"
        result = _parse_llm_response(text)
        assert result is not None
        assert "description" in result


# ===================================================================
# Candidate building (runner field injection)
# ===================================================================


class TestBuildCandidate:
    """Verify runner-assigned fields are injected correctly."""

    def test_injected_fields_present(self):
        from scripts.generate_candidate import _build_full_candidate
        proposal = json.loads(VALID_LLM_RESPONSE)
        candidate = _build_full_candidate("exp_9999", proposal)

        assert candidate["experiment_id"] == "exp_9999"
        assert candidate["base"] == "v2.1_balanced"
        assert candidate["status"] == "research_only"
        assert candidate["candidate_role"] == "standalone"
        assert candidate["strategy"] == "channel_breakout"
        assert "no_future_data" in candidate["constraints"]

    def test_llm_fields_preserved(self):
        from scripts.generate_candidate import _build_full_candidate
        proposal = json.loads(VALID_LLM_RESPONSE)
        candidate = _build_full_candidate("exp_9999", proposal)

        assert candidate["description"] == proposal["description"]
        assert candidate["hypothesis"] == proposal["hypothesis"]
        assert candidate["params"] == proposal["params"]

    def test_llm_cannot_override_injected(self):
        """Even if LLM includes base/status, runner values win."""
        from scripts.generate_candidate import _build_full_candidate
        proposal = json.loads(VALID_LLM_RESPONSE)
        proposal["base"] = "v3.0_experimental"  # LLM tries to override
        candidate = _build_full_candidate("exp_9999", proposal)
        assert candidate["base"] == "v2.1_balanced"  # runner wins

    def test_mature_trend_exit_defaults_are_injected(self):
        from scripts.generate_candidate import _build_full_candidate
        from scripts.validate_candidate_v08 import validate_candidate

        proposal = json.loads(VALID_LLM_RESPONSE)
        proposal["params"]["strategy_type"] = "exit_logic_channel_breakout"
        proposal["params"]["exit_logic"] = {
            "exit_lookback": 0,
            "take_profit_pct": 0.20,
            "stop_loss_pct": 0.12,
            "max_hold_bars": 720,
            "trailing_stop": True,
            "mature_trend_exit": {
                "activate_mfe_pct": 1.2,
                "daily_ema_period": 20,
            },
        }

        candidate = _build_full_candidate("exp_9993", proposal)
        mature = candidate["params"]["exit_logic"]["mature_trend_exit"]
        assert mature["enabled"] is True
        assert mature["use_completed_daily_bar"] is True
        assert candidate["strategy"] == "exit_logic_variant"
        assert validate_candidate(candidate, check_uniqueness=False) == []


# ===================================================================
# Full generation flow (mocked LLM)
# ===================================================================


class TestGenerateFlow:
    """Full generate_candidate flow with mocked LLM."""

    @patch("scripts.generate_candidate.call_llm", return_value=VALID_LLM_RESPONSE)
    def test_valid_candidate_accepted(self, mock_call):
        from scripts.generate_candidate import generate_candidate
        result = generate_candidate(
            experiment_id="exp_9999",
            dry_run=False,
        )
        assert result["status"] == "accepted"
        assert result["candidate_path"] is not None
        assert result["experiment_id"] == "exp_9999"

        # Clean up
        if result["candidate_path"] and result["candidate_path"].exists():
            result["candidate_path"].unlink()

    @patch("scripts.generate_candidate.call_llm", return_value="not json at all")
    def test_non_json_rejected(self, mock_call):
        from scripts.generate_candidate import generate_candidate
        result = generate_candidate(
            experiment_id="exp_9998",
            dry_run=False,
        )
        assert result["status"] == "rejected"
        assert "Non-JSON" in result["errors"][0]

    @patch("scripts.generate_candidate.call_llm", return_value=LLM_ERROR_RESPONSE)
    def test_llm_error_rejected(self, mock_call):
        from scripts.generate_candidate import generate_candidate
        result = generate_candidate(
            experiment_id="exp_9997",
            dry_run=False,
        )
        assert result["status"] == "rejected"
        assert "No valid hypothesis" in result["errors"][0]

    @patch("scripts.generate_candidate.call_llm",
           return_value=str({"error": "Cannot form hypothesis"}))
    def test_llm_cannot_form_hypothesis(self, mock_call):
        from scripts.generate_candidate import generate_candidate
        result = generate_candidate(
            experiment_id="exp_9996",
            dry_run=False,
        )
        assert result["status"] == "rejected"

    def test_dry_run_no_writes(self):
        from scripts.generate_candidate import generate_candidate
        result = generate_candidate(
            experiment_id="exp_9995",
            dry_run=True,
        )
        assert result["status"] == "dry_run"
        assert result["candidate_path"] is None

    @patch("scripts.generate_candidate.call_llm",
           return_value=json.dumps({
               "parent_id": "channel_breakout_v2_1_balanced",
               "description": "Hypothesis with checkpoint reference",
               "hypothesis": "Checkpoint path injection test. " * 5,
               "expected_behavior_change": "Should be rejected. " * 5,
               "params": {
                   "strategy_type": "regime_permission_channel_breakout",
                   "regime_change_policy": "permission_based",
                   "regime_filter": {"fast_days": 50, "slow_days": 200},
                   "bull": {
                       "candidate": "test",
                       "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                           "enable_long": True, "enable_short": False},
                       "permission": {"allow_long": True, "allow_short": False},
                   },
                   "bear": {
                       "candidate": "test",
                       "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                           "enable_long": True, "enable_short": True},
                       "permission": {"allow_long": True, "allow_short": True},
                   },
                   "neutral": {
                       "candidate": "test",
                       "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                           "enable_long": True, "enable_short": True},
                       "permission": {"allow_long": True, "allow_short": True},
                   },
               },
           }))
    def test_forbidden_content_rejected(self, mock_call):
        """Hypothesis containing 'checkpoint' should be rejected."""
        from scripts.generate_candidate import generate_candidate
        result = generate_candidate(
            experiment_id="exp_9994",
            dry_run=False,
        )
        assert result["status"] == "rejected"

    @patch("scripts.generate_candidate.call_llm", return_value=VALID_LLM_RESPONSE)
    def test_id_assignment_sequential(self, mock_call):
        """IDs should be sequential. This test is for the _get_next_experiment_id function."""
        from scripts.generate_candidate import _get_next_experiment_id
        eid = _get_next_experiment_id()
        assert eid.startswith("exp_")
        assert len(eid) > 5


# ===================================================================
# Context building
# ===================================================================


class TestContextBuilding:
    """Verify the context builder works."""

    def test_build_context(self):
        from scripts.generate_candidate import build_context
        ctx = build_context("exp_9999")
        assert "exp_9999" in ctx
        assert "EXPIRATION_ID" not in ctx  # template variables should be replaced
        assert "{{" not in ctx  # all template vars should be filled

    def test_context_includes_schema_reference(self):
        from scripts.generate_candidate import build_context
        ctx = build_context("exp_9999")
        assert "entry_lookback" in ctx
        assert "min_hold_bars" in ctx

    def test_recent_results_loading(self):
        from scripts.generate_candidate import _load_recent_results
        results = _load_recent_results(n=5)
        assert isinstance(results, str)
        assert len(results) > 0
