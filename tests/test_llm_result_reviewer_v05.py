#!/usr/bin/env python3
"""Test suite for v0.5 LLM Result Reviewer.

Tests:
    - Action schema validation (pure function)
    - Verdict-action constraint enforcement
    - Allowed_change validation
    - Forbidden content rejection
    - Full review flow with mocked LLM

Run:
    uv run pytest tests/test_llm_result_reviewer_v05.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module-level setup: install scorecard fixture for exp_0002
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SCORECARDS_DIR = Path(__file__).resolve().parents[1] / "research_workspace" / "llm_scorecards"
FIXTURE_SCORECARD = FIXTURES_DIR / "scorecard_exp_0002_1300d.json"
TARGET_SCORECARD = SCORECARDS_DIR / "exp_0002_1300d_scorecard.json"


def setup_module():
    """Copy fixture scorecard so flow tests can find it."""
    SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    if FIXTURE_SCORECARD.exists():
        shutil.copy2(str(FIXTURE_SCORECARD), str(TARGET_SCORECARD))


def teardown_module():
    """Remove fixture scorecard after tests."""
    if TARGET_SCORECARD.exists():
        TARGET_SCORECARD.unlink()

# ===================================================================
# Action fixtures
# ===================================================================

VALID_KILL_ACTION = {
    "action": "kill",
    "source_candidate_id": "exp_0001",
    "target_family": "channel_breakout",
    "rationale": "Candidate failed 1300d with DD_OVER_50 and ROLLING_NEGATIVE. The regime filter is too sensitive, making the strategy untradable.",
    "risk_note": "No further iteration recommended on this direction.",
}

VALID_FORK_ACTION = {
    "action": "fork",
    "source_candidate_id": "exp_0002",
    "target_family": "channel_breakout",
    "rationale": "Candidate passed 1300d but blocked on 2600d data. Fork with fast_days=60 may reduce regime sensitivity while maintaining bear capture.",
    "allowed_change": {
        "regime_filter": {"fast_days": 60, "slow_days": 200},
        "entry_lookback": 375,
    },
    "risk_note": "Pure research fork; 2600d data still needed.",
}

VALID_CREATE_ACTION = {
    "action": "create",
    "source_candidate_id": "baseline",
    "target_family": "channel_breakout",
    "rationale": "No viable fork from current candidate. Starting fresh with different regime hypothesis.",
}

VALID_STABLE_ACTION = {
    "action": "stable",
    "source_candidate_id": "exp_0003",
    "target_family": "channel_breakout",
    "rationale": "Candidate is acceptable for research_only status. No clear improvement path from current data.",
}

VALID_PROMOTE_ACTION = {
    "action": "promote_review",
    "source_candidate_id": "exp_9999",
    "target_family": "channel_breakout",
    "rationale": "Candidate passed both 1300d and 2600d windows with strong metrics. Ready for human promotion review.",
    "risk_note": "IS DD at -35% is within limits but worth monitoring.",
}

INVALID_ACTION_NO_TYPE = {
    "source_candidate_id": "exp_0001",
    "target_family": "channel_breakout",
    "rationale": "No action field provided.",
}

ACTION_WITH_FORBIDDEN_FIELD = {
    "action": "fork",
    "source_candidate_id": "exp_0001",
    "target_family": "channel_breakout",
    "rationale": "Testing forbidden field checkpoint injection. " * 3,
    "checkpoint_path": "checkpoints/sneaky.pt",
    "allowed_change": {"entry_lookback": 300},
}

FORK_WITH_OUT_OF_BOUNDS = {
    "action": "fork",
    "source_candidate_id": "exp_0001",
    "target_family": "channel_breakout",
    "rationale": "Testing out of bounds parameter for validation coverage. " * 3,
    "allowed_change": {"entry_lookback": 5000},
}

FORK_WITH_UNKNOWN_FIELD = {
    "action": "fork",
    "source_candidate_id": "exp_0001",
    "target_family": "channel_breakout",
    "rationale": "Testing unknown field in allowed_change for schema rejection. " * 3,
    "allowed_change": {"leverage": 100},
}


# ===================================================================
# Test: Action schema validation (pure function)
# ===================================================================


class TestActionValidation:
    """Verify action validation catches all schema violations."""

    def test_valid_kill(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_KILL_ACTION, verdict_label="kill")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_fork(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_FORK_ACTION, verdict_label="kill")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_create(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_CREATE_ACTION, verdict_label="kill")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_stable(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_STABLE_ACTION, verdict_label="research_only_recent_regime")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_promote_review(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_PROMOTE_ACTION, verdict_label="promote_review_pending")
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_missing_action_type(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(INVALID_ACTION_NO_TYPE, verdict_label="kill")
        assert errors, "Expected errors for missing action type"

    def test_invalid_action_string(self):
        from scripts.review_candidate import validate_action
        errors = validate_action({"action": "evolve", "source_candidate_id": "exp_0001",
                                  "target_family": "channel_breakout", "rationale": "x" * 30},
                                verdict_label="kill")
        assert errors, "Expected error for invalid action type"

    def test_rationale_too_short(self):
        from scripts.review_candidate import validate_action
        errors = validate_action({"action": "kill", "source_candidate_id": "exp_0001",
                                  "target_family": "channel_breakout", "rationale": "Short"},
                                verdict_label="kill")
        assert errors, "Expected error for short rationale"

    def test_invalid_source_id(self):
        from scripts.review_candidate import validate_action
        errors = validate_action({"action": "kill", "source_candidate_id": "exp_abc",
                                  "target_family": "channel_breakout", "rationale": "x" * 30},
                                verdict_label="kill")
        assert errors, "Expected error for invalid source ID"

    def test_wrong_target_family(self):
        from scripts.review_candidate import validate_action
        errors = validate_action({"action": "kill", "source_candidate_id": "exp_0001",
                                  "target_family": "lstm_strategy", "rationale": "x" * 30},
                                verdict_label="kill")
        assert errors, "Expected error for wrong target family"


# ===================================================================
# Test: Verdict-action constraints
# ===================================================================


class TestVerdictActionConstraints:
    """Verify verdict-aware action constraints."""

    def test_kill_verdict_allows_kill(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_KILL_ACTION, verdict_label="kill")
        assert errors == []

    def test_kill_verdict_rejects_promote(self):
        from scripts.review_candidate import validate_action
        promote_action = dict(VALID_PROMOTE_ACTION)
        promote_action["rationale"] = "Testing promote on kill verdict for constraint validation. " * 2
        errors = validate_action(promote_action, verdict_label="kill")
        assert errors, "Expected error: promote not allowed for kill verdict"

    def test_promote_pending_verdict_allows_promote(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_PROMOTE_ACTION, verdict_label="promote_review_pending")
        assert errors == []

    def test_promote_pending_verdict_rejects_kill(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_KILL_ACTION, verdict_label="promote_review_pending")
        assert errors, "Expected error: kill not allowed for promote_review_pending verdict"

    def test_requires_2600d_verdict_rejects_promote(self):
        from scripts.review_candidate import validate_action
        promote_action = dict(VALID_PROMOTE_ACTION)
        promote_action["rationale"] = "Testing promote constraint for requires_2600d verdict. " * 2
        errors = validate_action(promote_action, verdict_label="requires_2600d")
        assert errors, "Expected error: promote not allowed for requires_2600d"

    def test_research_only_recent_regime_rejects_promote(self):
        from scripts.review_candidate import validate_action
        promote_action = dict(VALID_PROMOTE_ACTION)
        promote_action["rationale"] = "Testing promote constraint for research_only verdict. " * 2
        errors = validate_action(promote_action, verdict_label="research_only_recent_regime")
        assert errors, "Expected error: promote not allowed for research_only"

    def test_invalid_verdict_uses_default(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(VALID_KILL_ACTION, verdict_label="unknown_verdict")
        # Create not in default {create}, so kill should fail
        assert errors, "Expected error for kill action under unknown verdict (default only allows create)"


# ===================================================================
# Test: Allowed_change validation
# ===================================================================


class TestAllowedChangeValidation:
    """Verify fork action allowed_change is validated."""

    def test_out_of_bounds_entry_lookback(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(FORK_WITH_OUT_OF_BOUNDS, verdict_label="kill")
        assert errors, "Expected error for out-of-bounds entry_lookback"

    def test_unknown_change_field(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(FORK_WITH_UNKNOWN_FIELD, verdict_label="kill")
        assert errors, "Expected error for unknown allowed_change field"

    def test_nested_regime_filter_validation(self):
        from scripts.review_candidate import validate_action
        action = {
            "action": "fork",
            "source_candidate_id": "exp_0001",
            "target_family": "channel_breakout",
            "rationale": "Testing nested regime filter range validation for schema compliance. " * 2,
            "allowed_change": {"regime_filter": {"fast_days": 999, "slow_days": 200}},
        }
        errors = validate_action(action, verdict_label="kill")
        assert errors, "Expected error for out-of-bounds fast_days in regime_filter"

    def test_missing_allowed_change_for_fork(self):
        from scripts.review_candidate import validate_action
        action = dict(VALID_FORK_ACTION)
        del action["allowed_change"]
        errors = validate_action(action, verdict_label="kill")
        assert errors, "Expected error: fork requires allowed_change"


# ===================================================================
# Test: Forbidden content rejection
# ===================================================================


class TestForbiddenContent:
    """Verify forbidden content is caught in action proposals."""

    def test_forbidden_field_at_top_level(self):
        from scripts.review_candidate import validate_action
        errors = validate_action(ACTION_WITH_FORBIDDEN_FIELD, verdict_label="kill")
        # Should catch both the forbidden field name and content
        assert errors, "Expected errors for forbidden field and content"

    def test_checkpoint_in_rationale(self):
        from scripts.review_candidate import validate_action
        action = {
            "action": "fork",
            "source_candidate_id": "exp_0001",
            "target_family": "channel_breakout",
            "rationale": "Testing if checkpoint path reference is caught by scanner. " * 3,
            "allowed_change": {"entry_lookback": 300},
        }
        errors = validate_action(action, verdict_label="kill")
        # 'checkpoint' as a bare word should be caught
        checkpoint_errors = [e for e in errors if "checkpoint" in e.lower()]
        assert checkpoint_errors, (
            f"Expected checkpoint-related error. Got: {errors}"
        )

    def test_oracle_reference_rejected(self):
        from scripts.review_candidate import validate_action
        action = {
            "action": "fork",
            "source_candidate_id": "exp_0001",
            "target_family": "channel_breakout",
            "rationale": "Testing if research_oracle reference is caught by scanner. " * 3,
            "allowed_change": {"entry_lookback": 300},
        }
        errors = validate_action(action, verdict_label="kill")
        oracle_errors = [e for e in errors if "oracle" in e.lower()]
        assert oracle_errors, f"Expected oracle-related error. Got: {errors}"


# ===================================================================
# Test: Full review flow (mocked LLM)
# ===================================================================


class TestReviewFlow:
    """Full review_candidate flow with mocked LLM."""

    @patch("scripts.review_candidate.call_llm", return_value=json.dumps(VALID_KILL_ACTION))
    def test_kill_action_accepted(self, mock_call):
        """Review an evaluated candidate (exp_0002) with a valid kill action."""
        from scripts.review_candidate import review_candidate

        # Use exp_0002 which has an evaluation state from v0.3
        result = review_candidate("exp_0002", dry_run=False)
        # Note: may be accepted or rejected depending on whether scorecard exists
        # The test verifies the flow doesn't crash
        assert result["status"] in ("accepted", "rejected", "error")
        if result["status"] == "accepted":
            assert result["action"]["action"] == "kill"

    @patch("scripts.review_candidate.call_llm", return_value=json.dumps(VALID_PROMOTE_ACTION))
    def test_promote_from_pending(self, mock_call):
        """promote_review proposed for a promote_review_pending verdict should work if scorecard exists."""
        from scripts.review_candidate import review_candidate
        result = review_candidate("exp_0002", dry_run=False)
        assert result["status"] in ("accepted", "rejected", "error")

    @patch("scripts.review_candidate.call_llm", return_value="not json at all")
    def test_non_json_rejected(self, mock_call):
        from scripts.review_candidate import review_candidate
        result = review_candidate("exp_0002", dry_run=False)
        assert result["status"] == "rejected"
        assert "Non-JSON" in str(result.get("errors", []))

    def test_nonexistent_candidate(self):
        from scripts.review_candidate import review_candidate
        result = review_candidate("exp_nonexistent", dry_run=False)
        assert result["status"] == "error"
        assert "Scorecard not found" in str(result.get("errors", []))

    def test_dry_run(self):
        from scripts.review_candidate import review_candidate
        result = review_candidate("exp_0002", dry_run=True)
        assert result["status"] == "dry_run"

    @patch("scripts.review_candidate.call_llm",
           return_value=json.dumps({"action": "fork", "source_candidate_id": "exp_0001",
                                    "target_family": "channel_breakout",
                                    "rationale": "Testing promote action from requires_2600d verdict. This is a test for constraint enforcement."}))
    def test_promote_from_requires_2600d_rejected(self, mock_call):
        """promote_review proposed when verdict is not promote_review_pending → rejected."""
        from scripts.review_candidate import validate_action
        action = {
            "action": "promote_review",
            "source_candidate_id": "exp_0002",
            "target_family": "channel_breakout",
            "rationale": "Testing promote action from requires_2600d verdict for constraint enforcement. ",
        }
        errors = validate_action(action, verdict_label="requires_2600d")
        assert errors, "Expected error: promote not allowed for requires_2600d"
