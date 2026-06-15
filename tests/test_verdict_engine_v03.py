#!/usr/bin/env python3
"""Test suite for v0.3 dual-window verdict engine.

Tests:
    - Verdict computation (pure function) at 1300d and 2600d
    - Scorecard structure with stage tracking
    - Promotion gate pass/fail
    - Evaluation state machine transitions
    - Candidate spec rejection

Run:
    uv run pytest tests/test_verdict_engine_v03.py -v --tb=short
    uv run pytest tests/test_verdict_engine_v03.py -v --tb=short -k "verdict"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.score_candidate import (
    VERDICT_KILL,
    VERDICT_REQUIRES_2600D,
    VERDICT_BLOCKED_MISSING_2600D,
    VERDICT_RESEARCH_ONLY_RECENT_REGIME,
    VERDICT_PROMOTE_REVIEW_PENDING,
    VERDICT_INVALID_ORACLE,
    VERDICT_INVALID_CANDIDATE,
    compute_verdict,
    build_scorecard,
    _check_promotion_gates,
)


# ---------------------------------------------------------------------------
# Fixtures — load oracle result JSONs
# ---------------------------------------------------------------------------


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    with open(path, "r") as fh:
        return json.load(fh)


@pytest.fixture
def oracle_1300d_fail() -> dict:
    return _load_fixture("oracle_1300d_fail.json")


@pytest.fixture
def oracle_1300d_pass() -> dict:
    return _load_fixture("oracle_1300d_pass.json")


@pytest.fixture
def oracle_2600d_fail() -> dict:
    return _load_fixture("oracle_2600d_fail.json")


@pytest.fixture
def oracle_2600d_pass() -> dict:
    return _load_fixture("oracle_2600d_pass.json")


# ===================================================================
# Verdict computation — 1300d stage
# ===================================================================


class TestVerdict1300d:
    """Verify all 1300d → verdict transitions."""

    def test_reject_is_kill(self, oracle_1300d_fail):
        v = compute_verdict(oracle_1300d_fail, stage="1300d")
        assert v["label"] == VERDICT_KILL
        assert "Disqualified" in v["reason"]
        assert v["source_status"] == "REJECT"

    def test_warn_is_requires_2600d(self, oracle_1300d_pass):
        v = compute_verdict(oracle_1300d_pass, stage="1300d")
        assert v["label"] == VERDICT_REQUIRES_2600D
        assert v["source_status"] == "WARN"

    def test_pass_is_requires_2600d(self):
        """Simulate a PASS oracle result at 1300d."""
        result = {
            "flags": {"status": "PASS", "disqualifications": [], "warnings": []}
        }
        v = compute_verdict(result, stage="1300d")
        assert v["label"] == VERDICT_REQUIRES_2600D

    def test_baseline_is_kill(self):
        result = {
            "flags": {"status": "BASELINE", "disqualifications": [],
                      "warnings": [], "baseline_known_risks": []}
        }
        v = compute_verdict(result, stage="1300d")
        assert v["label"] == VERDICT_KILL
        assert "Baseline" in v["reason"]

    def test_unknown_status_is_invalid(self):
        result = {"flags": {"status": "BOGUS", "disqualifications": [],
                            "warnings": []}}
        v = compute_verdict(result, stage="1300d")
        assert v["label"] == VERDICT_INVALID_ORACLE

    def test_disqualifications_override_pass_status(self):
        """Even with PASS status, if disqualifications list is non-empty → kill."""
        result = {
            "flags": {"status": "PASS",
                      "disqualifications": ["DD_OVER_50"],
                      "warnings": []}
        }
        v = compute_verdict(result, stage="1300d")
        assert v["label"] == VERDICT_KILL
        assert "DD_OVER_50" in v["reason"]


# ===================================================================
# Verdict computation — 2600d stage
# ===================================================================


class TestVerdict2600d:
    """Verify all 2600d → verdict transitions."""

    def test_reject_is_research_only(self, oracle_2600d_fail):
        v = compute_verdict(oracle_2600d_fail, stage="2600d")
        assert v["label"] == VERDICT_RESEARCH_ONLY_RECENT_REGIME
        assert "disqualified" in v["reason"].lower()

    def test_warn_is_research_only(self):
        result = {
            "flags": {"status": "WARN",
                      "disqualifications": [],
                      "warnings": ["OOS_DEGRADE", "DD_OVER_40"]}
        }
        v = compute_verdict(result, stage="2600d")
        assert v["label"] == VERDICT_RESEARCH_ONLY_RECENT_REGIME
        assert "warnings" in v["reason"].lower()

    def test_pass_is_promote_review_pending(self, oracle_2600d_pass):
        v = compute_verdict(oracle_2600d_pass, stage="2600d")
        assert v["label"] == VERDICT_PROMOTE_REVIEW_PENDING
        assert v["source_status"] == "PASS"
        assert "human promotion review" in v["reason"].lower()

    def test_baseline_is_research_only(self):
        result = {"flags": {"status": "BASELINE", "disqualifications": [],
                            "warnings": []}}
        v = compute_verdict(result, stage="2600d")
        assert v["label"] == VERDICT_RESEARCH_ONLY_RECENT_REGIME

    def test_unknown_status_is_invalid(self):
        result = {"flags": {"status": "BOGUS", "disqualifications": [],
                            "warnings": []}}
        v = compute_verdict(result, stage="2600d")
        assert v["label"] == VERDICT_INVALID_ORACLE

    def test_disqualifications_override_pass(self):
        """Even with PASS status, if disqualifications exist → research_only."""
        result = {
            "flags": {"status": "PASS",
                      "disqualifications": ["ROLLING_NEGATIVE"],
                      "warnings": []}
        }
        v = compute_verdict(result, stage="2600d")
        assert v["label"] == VERDICT_RESEARCH_ONLY_RECENT_REGIME


# ===================================================================
# Verdict computation — edge cases
# ===================================================================


class TestVerdictEdgeCases:
    """Unusual inputs that should not crash."""

    def test_empty_flags(self):
        result = {"flags": {}}
        v = compute_verdict(result, stage="1300d")
        assert v["label"] == VERDICT_INVALID_ORACLE

    def test_missing_flags(self):
        result = {}
        # The function is robust: returns invalid_oracle_output rather than crashing
        v = compute_verdict(result, stage="1300d")
        assert v["label"] == VERDICT_INVALID_ORACLE
        assert v["source_status"] == "UNKNOWN"

    def test_unknown_stage(self, oracle_1300d_pass):
        v = compute_verdict(oracle_1300d_pass, stage="invalid_stage")
        assert v["label"] == VERDICT_INVALID_ORACLE
        assert "stage" in v["reason"].lower()

    def test_none_stage(self, oracle_1300d_fail):
        v = compute_verdict(oracle_1300d_fail, stage=None)
        assert v["label"] == VERDICT_INVALID_ORACLE


# ===================================================================
# Scorecard structure
# ===================================================================


class TestScorecardStructure:
    """Verify scorecards include all v0.3 fields."""

    def test_scorecard_version(self, oracle_1300d_pass):
        sc = build_scorecard(oracle_1300d_pass, stage="1300d")
        assert sc["scorecard_version"] == "v0.3"

    def test_stage_and_window(self, oracle_1300d_pass):
        sc = build_scorecard(oracle_1300d_pass, stage="1300d")
        assert sc["stage"] == "1300d"
        assert sc["window"] == "1300d"

    def test_stage_2600d_on_scorecard(self, oracle_2600d_pass):
        sc = build_scorecard(oracle_2600d_pass, stage="2600d")
        assert sc["stage"] == "2600d"
        assert sc["window"] == "2600d"

    def test_next_steps_kill(self, oracle_1300d_fail):
        sc = build_scorecard(oracle_1300d_fail, stage="1300d")
        assert "no further evaluation" in sc["next_steps"].lower()

    def test_next_steps_requires_2600d(self, oracle_1300d_pass):
        sc = build_scorecard(oracle_1300d_pass, stage="1300d")
        assert "2600d" in sc["next_steps"].lower()

    def test_next_steps_promote_review(self, oracle_2600d_pass):
        sc = build_scorecard(oracle_2600d_pass, stage="2600d")
        assert "promotion packet" in sc["next_steps"].lower()

    def test_hypothesis_in_scorecard(self):
        spec = {"hypothesis": "Test hypothesis for scorecard.",
                "description": "test"}
        sc = build_scorecard(
            {"experiment_id": "test", "flags": {"status": "PASS",
              "disqualifications": [], "warnings": []},
             "metrics": {"is": {"raw": {}, "safe_execution": {},
                         "regime_permission": {}},
                         "oos": {"raw": {}, "safe_execution": {},
                                 "regime_permission": {}},
                         "rolling": {}, "regime": {},
                         "execution_parity": 0, "correlation": {},
                         "sensitivity": {"is": {"fees": {}, "slippage": {}},
                                         "oos": {"fees": {}, "slippage": {}}}}},
            candidate_spec=spec, stage="1300d",
        )
        assert sc["hypothesis"] == "Test hypothesis for scorecard."


# ===================================================================
# Promotion gates
# ===================================================================


class TestPromotionGates:
    """Verify promotion gate checks work correctly."""

    def test_fail_candidate_gates_blocked(self, oracle_1300d_fail):
        m = oracle_1300d_fail["metrics"]
        f = oracle_1300d_fail["flags"]
        pg = _check_promotion_gates(m, f)
        assert pg["all_pass"] is False
        assert pg["gates_passed"] < pg["gates_total"]

    def test_pass_candidate_gates_check(self, oracle_2600d_pass):
        m = oracle_2600d_pass["metrics"]
        f = oracle_2600d_pass["flags"]
        pg = _check_promotion_gates(m, f)
        # Even a passing candidate should have some blocked gates (human_review)
        assert pg["gates_total"] == 8
        # oracle_pass should pass when status=PASS and no disqualifications
        assert pg["gates"]["oracle_pass"]["pass"] is True

    def test_human_review_always_false(self, oracle_2600d_pass):
        m = oracle_2600d_pass["metrics"]
        f = oracle_2600d_pass["flags"]
        pg = _check_promotion_gates(m, f)
        assert pg["gates"]["human_review"]["pass"] is False

    def test_execution_parity_gate(self, oracle_1300d_pass):
        m = oracle_1300d_pass["metrics"]
        f = oracle_1300d_pass["flags"]
        pg = _check_promotion_gates(m, f)
        # oracle_1300d_pass has execution_parity=0.9989 >= 0.90
        assert pg["gates"]["execution_parity"]["pass"] is True

    def test_return_structure(self, oracle_1300d_fail):
        m = oracle_1300d_fail["metrics"]
        f = oracle_1300d_fail["flags"]
        pg = _check_promotion_gates(m, f)
        assert "gates_passed" in pg
        assert "gates_total" in pg
        assert "gates" in pg
        assert isinstance(pg["gates"], dict)


# ===================================================================
# Evaluation state machine (via evaluate_candidate)
# ===================================================================


class TestEvaluationDryRun:
    """State machine transitions via dry-run (no oracle, no I/O)."""

    def test_valid_candidate_happy_path(self):
        """Full happy path: valid candidate → 1300d pass → 2600d pass → promote_review."""
        from scripts.evaluate_candidate import evaluate_candidate
        from scripts.evaluate_candidate import (
            VERDICT_REQUIRES_2600D,
            VERDICT_PROMOTE_REVIEW_PENDING,
        )

        candidate_path = FIXTURES_DIR.parent.parent / "research_workspace" / \
            "llm_candidates" / "exp_0002.json"
        if not candidate_path.exists():
            pytest.skip("exp_0002.json not found")

        state = evaluate_candidate(candidate_path, dry_run=True)
        assert state["final_verdict"] == VERDICT_PROMOTE_REVIEW_PENDING
        assert len(state["history"]) == 2
        assert state["history"][0]["verdict"] == VERDICT_REQUIRES_2600D
        assert state["history"][1]["verdict"] == VERDICT_PROMOTE_REVIEW_PENDING


# ===================================================================
# Edge cases — invalid inputs
# ===================================================================


class TestInvalidInputs:
    """Verify graceful handling of invalid inputs."""

    def test_invalid_candidate_path(self):
        from scripts.evaluate_candidate import evaluate_candidate
        fake_path = Path("/nonexistent/candidate.json")
        state = evaluate_candidate(fake_path, dry_run=True)
        assert state.get("final_verdict") == VERDICT_INVALID_CANDIDATE

    def test_invalid_json_in_candidate(self, tmp_path):
        from scripts.evaluate_candidate import evaluate_candidate
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json")
        state = evaluate_candidate(bad_file, dry_run=True)
        assert state.get("final_verdict") == VERDICT_INVALID_CANDIDATE

    def test_schema_invalid_candidate(self, tmp_path):
        """Candidate missing required fields → schema validation fails."""
        from scripts.evaluate_candidate import evaluate_candidate
        bad_candidate = tmp_path / "bad_schema.json"
        bad_candidate.write_text(json.dumps({
            "experiment_id": "exp_bad",
            # Missing: hypothesis, constraints, base, status, params...
            "candidate_role": "standalone",
        }))
        state = evaluate_candidate(bad_candidate, dry_run=True)
        assert state.get("final_verdict") == VERDICT_INVALID_CANDIDATE
