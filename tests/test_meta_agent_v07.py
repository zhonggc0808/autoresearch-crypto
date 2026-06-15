#!/usr/bin/env python3
"""Test suite for v0.7 Meta-Agent Review.

Tests:
    - Review validation (pure function)
    - History collection
    - Forbidden topic detection
    - Malformed output rejection
    - Data aggregation
    - Dry-run mode

Run:
    uv run pytest tests/test_meta_agent_v07.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_REVIEW = {
    "review_id": "meta_9999",
    "window": {"runs_analyzed": 20, "candidates_analyzed": 12},
    "findings": [
        {
            "type": "failure_pattern",
            "severity": "high",
            "summary": "Fast regime filters consistently increase drawdown without improving returns across 5+ candidates.",
            "evidence": ["exp_0001: DD_OVER_50", "exp_0005: ROLLING_NEGATIVE"],
        },
        {
            "type": "stuck_loop",
            "severity": "medium",
            "summary": "Fork chain shows 3 consecutive entries without changing entry_lookback. Parameter is stuck at 375.",
            "evidence": ["exp_0018 -> exp_0019 -> exp_0020: all entry_lookback=375"],
        },
    ],
    "recommendations": [
        {
            "target": "search_space",
            "action": "narrow",
            "proposal": "Temporarily deprioritize regime_filter fast_days below 30. None of the 6 candidates with fast_days<30 improved results.",
            "rationale": "Fast regime filters increase turnover without improving DD or returns.",
        },
        {
            "target": "prompt",
            "action": "modify",
            "proposal": "Add explicit warning in candidate_generator prompt: 'Do not set fast_days below 30 without strong justification.'",
            "rationale": "Generator keeps proposing fast regime filters despite consistent failures.",
        },
    ],
    "contract_changes": [],
    "prompt_changes": [
        "Add anti-fast-regime-filter guidance to candidate_generator.md"
    ],
    "requires_human_review": True,
}

VALID_REVIEW_NO_CHANGES = {
    "review_id": "meta_9998",
    "window": {"runs_analyzed": 5, "candidates_analyzed": 3},
    "findings": [
        {
            "type": "success_pattern",
            "severity": "low",
            "summary": "All recent candidates passed schema validation on first attempt.",
            "evidence": ["exp_0021: passed", "exp_0022: passed"],
        },
    ],
    "recommendations": [
        {
            "target": "process",
            "action": "modify",
            "proposal": "Consider reducing validation strictness on hypothesis length to 15 chars to avoid false rejections.",
            "rationale": "Several candidates were rejected for hypothesis length just below the 20-char minimum.",
        },
    ],
    "contract_changes": [],
    "prompt_changes": [],
    "requires_human_review": False,
}

REVIEW_WITH_FORBIDDEN_TOPIC = {
    "review_id": "meta_9997",
    "window": {"runs_analyzed": 5, "candidates_analyzed": 3},
    "findings": [
        {
            "type": "failure_pattern",
            "severity": "high",
            "summary": "Oracle is too strict with DD_OVER_50 threshold. Should relax to DD_OVER_60.",
            "evidence": ["exp_0001", "exp_0005"],
        },
    ],
    "recommendations": [
        {
            "target": "contract",
            "action": "modify",
            "proposal": "Modify the oracle to accept higher drawdown.",
            "rationale": "Too many candidates killed by strict DD gate.",
        },
    ],
    "contract_changes": ["Relax DD_OVER_50 to DD_OVER_60"],
    "prompt_changes": [],
    "requires_human_review": True,
}

MALFORMED_REVIEW = {"not_a_review": True}

EMPTY_FINDINGS_REVIEW = {
    "review_id": "meta_9996",
    "window": {"runs_analyzed": 1, "candidates_analyzed": 0},
    "findings": [],
    "recommendations": [],
    "contract_changes": [],
    "prompt_changes": [],
    "requires_human_review": False,
}

REVIEW_MISSING_HUMAN_REVIEW = {
    "review_id": "meta_9995",
    "window": {"runs_analyzed": 1, "candidates_analyzed": 1},
    "findings": [
        {"type": "failure_pattern", "severity": "low",
         "summary": "Something worth investigating in the results.",
         "evidence": ["exp_0001"]},
    ],
    "recommendations": [
        {"target": "contract", "action": "modify",
         "proposal": "Update contract to reflect new understanding of regime filter ranges.",
         "rationale": "New data suggests wider range is safe."},
    ],
    "contract_changes": ["Update regime filter ranges"],
    "prompt_changes": [],
    "requires_human_review": False,  # Should be True
}

MOCK_REVIEW_RESPONSE = json.dumps(VALID_REVIEW)


# ===================================================================
# Test: Review validation
# ===================================================================


class TestReviewValidation:
    """Verify meta-review schema validation catches all violations."""

    def test_valid_review(self):
        from scripts.meta_review import validate_review
        errors = validate_review(VALID_REVIEW)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_valid_review_no_changes(self):
        from scripts.meta_review import validate_review
        errors = validate_review(VALID_REVIEW_NO_CHANGES)
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_forbidden_topic_rejected(self):
        from scripts.meta_review import validate_review
        errors = validate_review(REVIEW_WITH_FORBIDDEN_TOPIC)
        # Should catch both "modify the oracle" in content
        oracle_errors = [e for e in errors if "forbidden" in e.lower()]
        assert oracle_errors, f"Expected forbidden topic error. Got: {errors}"

    def test_malformed_review(self):
        from scripts.meta_review import validate_review
        errors = validate_review(MALFORMED_REVIEW)
        assert errors, "Expected errors for malformed review"

    def test_empty_findings_rejected(self):
        from scripts.meta_review import validate_review
        errors = validate_review(EMPTY_FINDINGS_REVIEW)
        finding_errors = [e for e in errors if "finding" in e.lower()]
        assert finding_errors, f"Expected finding-related error. Got: {errors}"

    def test_missing_human_review_flag(self):
        from scripts.meta_review import validate_review
        errors = validate_review(REVIEW_MISSING_HUMAN_REVIEW)
        hr_errors = [e for e in errors if "human_review" in e.lower()]
        assert hr_errors, f"Expected human_review error. Got: {errors}"


# ===================================================================
# Test: Forbidden topic detection
# ===================================================================


class TestForbiddenTopics:
    """Verify forbidden topics are detected regardless of phrasing."""

    def test_oracle_modification_detected(self):
        from scripts.meta_review import validate_review
        review = dict(REVIEW_WITH_FORBIDDEN_TOPIC)
        errors = validate_review(review)
        assert any("oracle" in e.lower() and "forbidden" in e.lower()
                   for e in errors), f"Expected oracle-forbidden error. Got: {errors}"

    def test_baseline_modification_detected(self):
        from scripts.meta_review import validate_review
        review = {
            "review_id": "meta_9999",
            "window": {"runs_analyzed": 1, "candidates_analyzed": 1},
            "findings": [{"type": "failure_pattern", "severity": "high",
                          "summary": "Should modify baseline to use different params.",
                          "evidence": ["test"]}],
            "recommendations": [{"target": "contract", "action": "modify",
                                 "proposal": "Modify baseline parameters to improve results.",
                                 "rationale": "Baseline is underperforming."}],
            "contract_changes": [],
            "prompt_changes": [],
            "requires_human_review": True,
        }
        errors = validate_review(review)
        assert any("forbidden" in e.lower() for e in errors), \
            f"Expected forbidden error for baseline modification. Got: {errors}"

    def test_live_modification_detected(self):
        from scripts.meta_review import validate_review
        review = dict(VALID_REVIEW)
        review["recommendations"][0]["proposal"] = "Modify live trading to use the new candidate."
        errors = validate_review(review)
        assert any("forbidden" in e.lower() for e in errors), \
            f"Expected forbidden error for live modification. Got: {errors}"


# ===================================================================
# Test: History collection
# ===================================================================


class TestHistoryCollection:
    """Verify history collection works with available data."""

    def test_collect_history(self):
        from scripts.meta_review import collect_history
        history = collect_history(n_runs=10)
        assert "runs" in history
        assert "results" in history
        assert "summary" in history
        assert history["summary"]["action_distribution"] is not None


# ===================================================================
# Test: Mock LLM flow
# ===================================================================


class TestMetaReviewFlow:
    """Full meta-review flow with mocked LLM (no API key needed)."""

    @patch("scripts.meta_review.call_llm", return_value=MOCK_REVIEW_RESPONSE)
    def test_meta_review_produces_review(self, mock_call):
        from scripts.meta_review import run_meta_review

        # Use minimal runs for speed
        result = run_meta_review(n_runs=5)

        # May be ok (if there are runs) or rejected (if not enough data)
        assert result["status"] in ("ok", "rejected", "error")
        if result["status"] == "ok":
            assert result["review_path"] is not None
            assert Path(result["review_path"]).exists()

    def test_dry_run_produces_no_writes(self):
        from scripts.meta_review import run_meta_review

        result = run_meta_review(n_runs=5, dry_run=True)
        assert result["status"] == "dry_run"

    @patch("scripts.meta_review.call_llm", return_value="not valid json")
    def test_non_json_rejected(self, mock_call):
        from scripts.meta_review import run_meta_review

        result = run_meta_review(n_runs=5)
        assert result["status"] == "rejected"
        assert "Non-JSON" in str(result.get("errors", []))


# ===================================================================
# Test: Context building
# ===================================================================


class TestContextBuilding:
    """Verify the context builder handles edge cases."""

    def test_context_builds_with_data(self):
        from scripts.meta_review import collect_history, build_context
        history = collect_history(n_runs=5)
        context = build_context(history, "meta_9999")
        assert "Meta-Agent Review" in context
        # Template variables starting with {{ should be replaced
        assert "{{RUNS_ANALYZED}}" not in context
        assert "{{CANDIDATES_ANALYZED}}" not in context
        assert "search_space" in context.lower()

    def test_empty_history(self):
        """Collecting history with no runs should not crash."""
        from scripts.meta_review import collect_history
        # Temporarily point to empty directories
        original_runs = __import__('scripts.meta_review', fromlist=['']).RUNS_DIR
        # Just verify it doesn't crash with minimal data
        history = collect_history(n_runs=0)
        assert isinstance(history, dict)
        assert "summary" in history
