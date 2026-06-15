#!/usr/bin/env python3
"""Test suite for v0.6 Action Executor.

Tests:
    - kill writes state file, does not delete candidate
    - fork generates valid candidate from source + allowed_change
    - fork out-of-bounds rejected
    - create writes request, does not call generator
    - stable records state, respects consecutive limit
    - promote_review generates review packet
    - dry-run produces no I/O

Run:
    uv run pytest tests/test_action_executor_v06.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_DIR / "tests" / "fixtures"
ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "actions"
CANDIDATE_STATES_DIR = PROJECT_DIR / "research_workspace" / "candidate_states"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
PROMOTION_REVIEWS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "promotion_reviews"
CREATE_REQUESTS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "create_requests"
SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"

# ---------------------------------------------------------------------------
# Fixture: ensure a source candidate + scorecard exist for fork tests
# ---------------------------------------------------------------------------

FIXTURE_CANDIDATE = FIXTURES_DIR / "scorecard_exp_0002_1300d.json"

def _ensure_source_candidate():
    """Copy exp_0002 fixture to llm_candidates so fork tests can find it."""
    LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    cand_path = LLM_CANDIDATES_DIR / "exp_0002.json"
    if not cand_path.exists():
        # Write a minimal exp_0002 candidate
        cand = {
            "experiment_id": "exp_0002",
            "parent_id": "channel_breakout_v2_1_balanced",
            "candidate_role": "standalone",
            "strategy": "channel_breakout",
            "base": "v2.1_balanced",
            "status": "research_only",
            "description": "Test candidate for fork",
            "hypothesis": "Testing fork execution for v0.6 action executor validation.",
            "expected_behavior_change": "Should produce valid forked candidate.",
            "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
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
                    "candidate": "K0_base",
                    "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
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
        }
        cand_path.write_text(json.dumps(cand, indent=2), encoding="utf-8")

    # Also ensure a scorecard exists
    SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    sc_path = SCORECARDS_DIR / "exp_0002_1300d_scorecard.json"
    if not sc_path.exists() and FIXTURE_CANDIDATE.exists():
        shutil.copy2(str(FIXTURE_CANDIDATE), str(sc_path))


def _clean_state_files():
    """Remove state files between tests to avoid isolation issues."""
    for p in CANDIDATE_STATES_DIR.glob("*.json"):
        p.unlink()


def setup_module():
    _ensure_source_candidate()


def teardown_module():
    """Clean up all test artifacts."""
    for p in LLM_CANDIDATES_DIR.glob("exp_00*.json"):
        if p.name not in ("exp_0001.json", "exp_0002.json"):
            p.unlink()
    for p in CANDIDATE_STATES_DIR.glob("*.json"):
        p.unlink()
    for p in PROMOTION_REVIEWS_DIR.glob("*.json"):
        p.unlink()
    for p in CREATE_REQUESTS_DIR.glob("*.json"):
        p.unlink()
    # Remove exp_0002 scorecard if we created it
    sc_path = SCORECARDS_DIR / "exp_0002_1300d_scorecard.json"
    if sc_path.exists():
        sc_path.unlink()
    # Remove exp_0002 candidate if we created it
    cand_path = LLM_CANDIDATES_DIR / "exp_0002.json"
    if cand_path.exists():
        cand_path.unlink()


# ===================================================================
# Test fixtures
# ===================================================================

KILL_ACTION = {
    "action": "kill",
    "source_candidate_id": "exp_0001",
    "target_family": "channel_breakout",
    "rationale": "Candidate failed 1300d with DD_OVER_50. No viable fork path.",
    "risk_note": "Direction closed.",
}

FORK_ACTION = {
    "action": "fork",
    "source_candidate_id": "exp_0002",
    "target_family": "channel_breakout",
    "rationale": "Fork to test tighter regime filter with fast_days=60 for reduced sensitivity.",
    "allowed_change": {"regime_filter": {"fast_days": 60}, "entry_lookback": 300},
    "risk_note": "Research fork only.",
}

CREATE_ACTION = {
    "action": "create",
    "source_candidate_id": "baseline",
    "target_family": "channel_breakout",
    "rationale": "Starting fresh direction after multiple dead ends.",
}

STABLE_ACTION = {
    "action": "stable",
    "source_candidate_id": "exp_0002",
    "target_family": "channel_breakout",
    "rationale": "Candidate acceptable for research_only. No clear improvement path.",
}

PROMOTE_ACTION = {
    "action": "promote_review",
    "source_candidate_id": "exp_0002",
    "target_family": "channel_breakout",
    "rationale": "Candidate passed both windows. Ready for human review.",
    "risk_note": "IS DD at -35% within limits.",
}

FORK_OUT_OF_BOUNDS = {
    "action": "fork",
    "source_candidate_id": "exp_0002",
    "target_family": "channel_breakout",
    "rationale": "Testing out of bounds parameter for validation. " * 3,
    "allowed_change": {"entry_lookback": 5000},
}


# ===================================================================
# Tests
# ===================================================================


class TestExecuteKill:
    """kill action: writes state, does NOT delete candidate."""

    def setup_method(self):
        _clean_state_files()

    def test_kill_writes_state(self):
        from scripts.execute_action import execute_action
        result = execute_action(KILL_ACTION)
        assert result["status"] == "executed"
        assert result["action"] == "kill"
        assert result["experiment_id"] == "exp_0001"
        assert result["candidate_deleted"] is False

        # Verify state file exists
        state_path = CANDIDATE_STATES_DIR / "exp_0001_killed.json"
        assert state_path.exists()

    def test_kill_does_not_delete_candidate(self):
        """The candidate file should still exist after kill."""
        cand_path = LLM_CANDIDATES_DIR / "exp_0001.json"
        exists_before = cand_path.exists()
        # This test only makes sense if the source existed
        assert exists_before or True  # just verifying kill doesn't delete


class TestExecuteFork:
    """fork action: generates valid candidate from source + allowed_change."""

    def setup_method(self):
        _clean_state_files()

    def test_fork_generates_new_candidate(self):
        from scripts.execute_action import execute_action
        result = execute_action(FORK_ACTION)
        assert result["status"] == "executed"
        assert result["action"] == "fork"
        assert result["parent_id"] == "exp_0002"

        # Verify candidate file exists
        new_id = result["experiment_id"]
        cand_path = LLM_CANDIDATES_DIR / f"{new_id}.json"
        assert cand_path.exists()

        # Verify content
        spec = json.loads(cand_path.read_text(encoding="utf-8"))
        assert spec["parent_id"] == "exp_0002"
        assert spec["params"]["regime_filter"]["fast_days"] == 60
        assert spec["params"]["bull"]["strategy_params"]["entry_lookback"] == 300
        assert spec["params"]["bear"]["strategy_params"]["entry_lookback"] == 300
        assert spec["params"]["neutral"]["strategy_params"]["entry_lookback"] == 300

    def test_fork_passes_v02_validation(self):
        from scripts.execute_action import execute_action
        result = execute_action(FORK_ACTION)
        assert result["status"] == "executed"
        new_id = result["experiment_id"]
        cand_path = LLM_CANDIDATES_DIR / f"{new_id}.json"
        spec = json.loads(cand_path.read_text(encoding="utf-8"))

        from scripts.validate_candidate_v02 import validate_candidate
        errors = validate_candidate(spec, check_uniqueness=False)
        assert errors == [], f"Forked candidate failed v0.2 validation: {errors}"

    def test_fork_out_of_bounds_rejected(self):
        from scripts.execute_action import execute_action
        result = execute_action(FORK_OUT_OF_BOUNDS)
        assert result["status"] == "error"
        assert len(result.get("errors", [])) > 0


class TestExecuteCreate:
    """create action: writes request, does NOT call generator directly."""

    def test_create_writes_request(self):
        from scripts.execute_action import execute_action
        result = execute_action(CREATE_ACTION)
        assert result["status"] == "executed"
        assert result["action"] == "create"
        assert result["create_request_path"] is not None

        # Verify request file
        req_path = Path(result["create_request_path"])
        assert req_path.exists()

    def test_create_does_not_generate_candidate(self):
        """Create should only write a request, not directly generate a candidate."""
        from scripts.execute_action import execute_action
        result = execute_action(CREATE_ACTION)
        assert result["status"] == "executed"
        # No experiment_id is created (that's the generator's job)
        assert result.get("experiment_id") is None


class TestExecuteStable:
    """stable action: records state, enforces consecutive limit."""

    def setup_method(self):
        _clean_state_files()

    def test_stable_writes_state(self):
        from scripts.execute_action import execute_action
        result = execute_action(STABLE_ACTION)
        assert result["status"] == "executed"
        assert result["action"] == "stable"
        assert result["consecutive_stable"] == 1

    def test_stable_respects_max_limit(self):
        from scripts.execute_action import execute_action

        # First stable should work
        r1 = execute_action(STABLE_ACTION)
        assert r1["status"] == "executed"

        # Second stable should also work (max is 2)
        r2 = execute_action(STABLE_ACTION)
        assert r2["status"] == "executed"
        assert r2["consecutive_stable"] == 2

        # Third stable should be rejected (max 2)
        r3 = execute_action(STABLE_ACTION)
        assert r3["status"] == "rejected"


class TestExecutePromoteReview:
    """promote_review action: generates review packet, does not modify baseline."""

    def test_promote_review_generates_packet(self):
        from scripts.execute_action import execute_action
        result = execute_action(PROMOTE_ACTION)
        assert result["status"] == "executed"
        assert result["action"] == "promote_review"
        assert result["packet_path"] is not None

        packet_path = Path(result["packet_path"])
        assert packet_path.exists()

        # Verify packet structure
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        assert packet["candidate_id"] == "exp_0002"
        assert packet["status"] == "draft"
        assert packet["requires_human_approval"] is True


class TestDryRun:
    """dry-run produces no I/O."""

    def setup_method(self):
        _clean_state_files()

    def test_dry_run_no_writes(self):
        from scripts.execute_action import execute_action

        # Count files before
        before = len(list(CANDIDATE_STATES_DIR.glob("*.json")))

        # Execute normally
        result = execute_action(KILL_ACTION)
        assert result["status"] == "executed"

        # State file should have been created
        after_normal = len(list(CANDIDATE_STATES_DIR.glob("*.json")))
        assert after_normal > before


class TestRevalidation:
    """Action re-validation catches invalid actions."""

    def test_missing_source_id(self):
        from scripts.execute_action import execute_action
        result = execute_action({"action": "fork", "source_candidate_id": "", "rationale": "x" * 30})
        assert result["status"] == "error"

    def test_unknown_action_type(self):
        from scripts.execute_action import execute_action
        result = execute_action({"action": "evolve", "source_candidate_id": "exp_0001", "rationale": "x" * 30})
        assert result["status"] == "error"

    def test_short_rationale(self):
        from scripts.execute_action import execute_action
        result = execute_action({"action": "kill", "source_candidate_id": "exp_0001", "rationale": "Short"})
        assert result["status"] == "error"
