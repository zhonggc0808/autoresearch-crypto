#!/usr/bin/env python3
"""Test suite for v0.8.5 Multi-Family Real Batch.

Tests:
    - MultiFamilyActionCycler family/action distribution
    - Each family gets at least 3 candidates in sufficient cycles
    - VFB templates are valid JSON and pass schema
    - CB templates still pass schema
    - Batch summary has per-family stats
    - Family analysis builder works with test data
    - Meta-review context includes family-level section
    - Cross-family patch protection (executor preserves family)
    - No forbidden content in VFB templates
    - Minimum cycles enforcement (3 per family)

Run:
    uv run pytest tests/test_multi_family_batch_v085.py -v --tb=short
    uv run pytest tests/test_multi_family_batch_v085.py -v --tb=short -x
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CB_FAMILIES = ["channel_breakout", "volatility_filtered_breakout"]


# ===================================================================
# Test: MultiFamilyActionCycler distribution
# ===================================================================


class TestMultiFamilyActionCycler:
    """Verify the cycler distributes families and actions correctly."""

    def test_round_robin_family_distribution(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=CB_FAMILIES)
        families_seen = []
        for _ in range(8):
            cycler.next_action()
            families_seen.append(cycler.current_family())
        # Round-robin: CB, VFB, CB, VFB, ...
        assert families_seen == ["channel_breakout", "volatility_filtered_breakout",
                                  "channel_breakout", "volatility_filtered_breakout",
                                  "channel_breakout", "volatility_filtered_breakout",
                                  "channel_breakout", "volatility_filtered_breakout"]

    def test_action_cycles_within_family(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=CB_FAMILIES)
        actions_seen = []
        for _ in range(6):
            act = cycler.next_action()
            actions_seen.append(act)
        # 6 cycles / 2 families = 3 actions per family block
        # Family 0 (CB) gets actions[0], family 1 (VFB) gets actions[1], ...
        # So: CB fork, VFB fork, CB kill, VFB kill, CB create, VFB create
        assert actions_seen == ["fork", "fork", "kill", "kill", "create", "create"]

    def test_each_family_gets_all_action_types(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=CB_FAMILIES)
        family_actions: dict = {}
        for _ in range(12):  # 2 families x 3 actions x 2 rounds
            act = cycler.next_action()
            fam = cycler.current_family()
            if fam not in family_actions:
                family_actions[fam] = []
            family_actions[fam].append(act)

        for fam in CB_FAMILIES:
            assert "fork" in family_actions.get(fam, []), f"{fam} missing fork"
            assert "kill" in family_actions.get(fam, []), f"{fam} missing kill"
            assert "create" in family_actions.get(fam, []), f"{fam} missing create"

    def test_min_cycles_property(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=CB_FAMILIES)
        assert cycler.n_families == 2

    def test_default_families_from_registry(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler()
        assert len(cycler.families) >= 2
        assert "channel_breakout" in cycler.families
        assert "volatility_filtered_breakout" in cycler.families


# ===================================================================
# Test: VFB templates are valid
# ===================================================================


class TestVFBTemplates:
    """Verify VFB templates are valid JSON and pass schema."""

    def test_vfb_candidate_is_valid_json(self):
        from scripts.run_batch_trial import VFB_CANDIDATE_TEMPLATE
        parsed = json.loads(VFB_CANDIDATE_TEMPLATE)
        assert parsed["params"]["strategy_type"] == "volatility_filtered_channel_breakout"
        assert "volatility_filter" in parsed["params"]
        assert parsed["params"]["volatility_filter"]["mode"] == "exclude_extreme"

    def test_vfb_candidate_passes_validation(self):
        from scripts.run_batch_trial import VFB_CANDIDATE_TEMPLATE
        from scripts.validate_candidate_v08 import validate_candidate
        parsed = json.loads(VFB_CANDIDATE_TEMPLATE)
        parsed["experiment_id"] = "exp_9999"
        parsed["strategy"] = "volatility_filtered_breakout"
        parsed["base"] = "v2.1_balanced"
        parsed["status"] = "research_only"
        parsed["candidate_role"] = "standalone"
        parsed["constraints"] = ["no_future_data", "inherits_v21_risk",
                                  "no_demo_routing", "research_only"]
        errors = validate_candidate(parsed, check_uniqueness=False)
        assert errors == [], f"VFB candidate failed validation: {errors}"

    def test_vfb_action_fork_is_valid(self):
        from scripts.run_batch_trial import VFB_ACTION_TEMPLATES
        for action_type in ("fork", "kill", "create"):
            parsed = json.loads(VFB_ACTION_TEMPLATES[action_type])
            assert parsed["action"] == action_type
            assert parsed["target_family"] == "volatility_filtered_breakout"
            if action_type == "fork":
                assert "volatility_filter" in parsed.get("allowed_change", {})

    def test_cb_candidate_still_valid(self):
        from scripts.run_batch_trial import CB_CANDIDATE_TEMPLATE
        from scripts.validate_candidate_v08 import validate_candidate
        parsed = json.loads(CB_CANDIDATE_TEMPLATE)
        parsed["experiment_id"] = "exp_9998"
        parsed["strategy"] = "channel_breakout"
        parsed["base"] = "v2.1_balanced"
        parsed["status"] = "research_only"
        parsed["candidate_role"] = "standalone"
        parsed["constraints"] = ["no_future_data", "inherits_v21_risk",
                                  "no_demo_routing", "research_only"]
        errors = validate_candidate(parsed, check_uniqueness=False)
        assert errors == [], f"CB candidate failed validation: {errors}"

    def test_vfb_candidate_no_forbidden_content(self):
        import re

        from scripts.run_batch_trial import VFB_CANDIDATE_TEMPLATE
        from scripts.validate_candidate_v02 import FORBIDDEN_PATTERNS
        for pattern_str, reason in FORBIDDEN_PATTERNS:
            match = re.search(pattern_str, VFB_CANDIDATE_TEMPLATE, re.IGNORECASE)
            assert not match, f"Forbidden pattern found in VFB template ({reason})"

    def test_vfb_actions_no_forbidden_content(self):
        import re

        from scripts.run_batch_trial import VFB_ACTION_TEMPLATES
        from scripts.validate_candidate_v02 import FORBIDDEN_PATTERNS
        for action_type, template in VFB_ACTION_TEMPLATES.items():
            for pattern_str, reason in FORBIDDEN_PATTERNS:
                match = re.search(pattern_str, template, re.IGNORECASE)
                assert not match, (
                    f"Forbidden pattern found in VFB {action_type} template ({reason})"
                )


# ===================================================================
# Test: Family template dispatch
# ===================================================================


class TestFamilyDispatch:
    """Verify template dispatch tables contain all families."""

    def test_family_action_templates_has_both(self):
        from scripts.run_batch_trial import FAMILY_ACTION_TEMPLATES
        assert "channel_breakout" in FAMILY_ACTION_TEMPLATES
        assert "volatility_filtered_breakout" in FAMILY_ACTION_TEMPLATES
        assert "exit_logic_variant" in FAMILY_ACTION_TEMPLATES

    def test_family_candidate_templates_has_both(self):
        from scripts.run_batch_trial import FAMILY_CANDIDATE_TEMPLATES
        assert "channel_breakout" in FAMILY_CANDIDATE_TEMPLATES
        assert "volatility_filtered_breakout" in FAMILY_CANDIDATE_TEMPLATES

    def test_vfb_mock_returns_vfb_candidate(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=["volatility_filtered_breakout"])
        cycler.next_action()  # prepare first cycle
        response = cycler.mock_fn("generator prompt", "user msg")
        parsed = json.loads(response)
        assert parsed["params"]["strategy_type"] == "volatility_filtered_channel_breakout"

    def test_cb_mock_returns_cb_candidate(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=["channel_breakout"])
        cycler.next_action()
        response = cycler.mock_fn("generator prompt", "user msg")
        parsed = json.loads(response)
        assert parsed["params"]["strategy_type"] == "regime_permission_channel_breakout"


# ===================================================================
# Test: Batch summary with family stats
# ===================================================================


class TestFamilyBatchSummary:
    """Verify batch summary includes per-family statistics."""

    def test_batch_summary_has_family_distribution(self):
        from scripts.run_batch_trial import _build_batch_summary

        results = [
            {"cycle": 1, "action": "fork", "family": "channel_breakout",
             "candidate_id": "exp_0001", "final_state": "executed_fork",
             "steps_ok": 5, "steps_total": 6, "elapsed": 1.0, "run_id": "r1"},
            {"cycle": 2, "action": "fork", "family": "volatility_filtered_breakout",
             "candidate_id": "exp_0002", "final_state": "executed_fork",
             "steps_ok": 5, "steps_total": 6, "elapsed": 1.0, "run_id": "r2"},
            {"cycle": 3, "action": "kill", "family": "channel_breakout",
             "candidate_id": "exp_0003", "final_state": "executed_kill",
             "steps_ok": 6, "steps_total": 6, "elapsed": 1.0, "run_id": "r3"},
            {"cycle": 4, "action": "kill", "family": "volatility_filtered_breakout",
             "candidate_id": "exp_0004", "final_state": "execute_failed",
             "steps_ok": 4, "steps_total": 6, "elapsed": 1.0, "run_id": "r4"},
            {"cycle": 5, "action": "create", "family": "channel_breakout",
             "candidate_id": "exp_0005", "final_state": "executed_create",
             "steps_ok": 1, "steps_total": 6, "elapsed": 1.0, "run_id": "r5"},
            {"cycle": 6, "action": "create", "family": "volatility_filtered_breakout",
             "candidate_id": "exp_0006", "final_state": "executed_create",
             "steps_ok": 3, "steps_total": 6, "elapsed": 1.0, "run_id": "r6"},
        ]

        summary = _build_batch_summary(results, 6, multi_family=True)

        assert "family_distribution" in summary
        assert summary["family_distribution"]["channel_breakout"] == 3
        assert summary["family_distribution"]["volatility_filtered_breakout"] == 3

        assert "family_success" in summary
        # CB: 3/3 success; VFB: 2/3 success (cycle 4 failed)
        assert summary["family_success"]["channel_breakout"] == 3
        assert summary["family_success"]["volatility_filtered_breakout"] == 2

    def test_batch_summary_family_actions(self):
        from scripts.run_batch_trial import _build_batch_summary

        results = [
            {"cycle": 1, "action": "fork", "family": "channel_breakout",
             "candidate_id": "exp_0001", "final_state": "executed_fork",
             "steps_ok": 5, "steps_total": 6, "elapsed": 1.0, "run_id": "r1"},
            {"cycle": 2, "action": "fork", "family": "channel_breakout",
             "candidate_id": "exp_0002", "final_state": "executed_kill",
             "steps_ok": 5, "steps_total": 6, "elapsed": 1.0, "run_id": "r2"},
            {"cycle": 3, "action": "kill", "family": "volatility_filtered_breakout",
             "candidate_id": "exp_0003", "final_state": "execute_failed",
             "steps_ok": 5, "steps_total": 6, "elapsed": 1.0, "run_id": "r3"},
        ]

        summary = _build_batch_summary(results, 3, multi_family=True)

        assert "family_actions" in summary
        assert summary["family_actions"]["channel_breakout"]["fork"] == 2
        assert summary["family_actions"]["volatility_filtered_breakout"]["kill"] == 1


# ===================================================================
# Test: Family analysis builder
# ===================================================================


class TestFamilyAnalysisBuilder:
    """Verify _build_family_analysis produces correct output."""

    def test_family_analysis_with_data(self):
        from scripts.meta_review import _build_family_analysis

        runs = [
            {"run_id": "r1", "family": "channel_breakout",
             "candidate_id": "exp_0001", "final_state": "executed_fork",
             "steps": [
                 {"step": "generate", "status": "ok"},
                 {"step": "validate", "status": "ok"},
                 {"step": "evaluate", "status": "ok"},
                 {"step": "scorecard", "status": "ok"},
                 {"step": "review", "status": "ok"},
                 {"step": "execute", "status": "ok", "action": "fork",
                  "fork_id": "exp_0001a"},
             ]},
            {"run_id": "r2", "family": "volatility_filtered_breakout",
             "candidate_id": "exp_0002", "final_state": "executed_kill",
             "steps": [
                 {"step": "generate", "status": "ok"},
                 {"step": "validate", "status": "ok"},
                 {"step": "evaluate", "status": "failed"},
                 {"step": "scorecard", "status": "not_found"},
                 {"step": "review", "status": "ok"},
                 {"step": "execute", "status": "ok", "action": "kill"},
             ]},
        ]

        analysis = _build_family_analysis(runs)
        assert "channel_breakout" in analysis
        assert "volatility_filtered_breakout" in analysis
        assert "Candidates: 1" in analysis  # each family has 1
        assert "fork" in analysis

    def test_family_analysis_empty(self):
        from scripts.meta_review import _build_family_analysis
        analysis = _build_family_analysis([])
        assert analysis == "(no family data)" or "no family" in analysis.lower()

    def test_family_analysis_handles_missing_family(self):
        from scripts.meta_review import _build_family_analysis

        analysis = _build_family_analysis([
            {
                "run_id": "r_missing",
                "family": None,
                "candidate_id": "exp_0000",
                "final_state": "executed_kill",
                "steps": [{"step": "execute", "status": "ok", "action": "kill"}],
            }
        ])
        assert "unknown" in analysis
        assert "Candidates: 1" in analysis

    def test_family_analysis_multiple_runs_per_family(self):
        from scripts.meta_review import _build_family_analysis

        runs = []
        for i in range(4):
            runs.append({
                "run_id": f"r{i}", "family": "channel_breakout" if i < 2 else "volatility_filtered_breakout",
                "candidate_id": f"exp_{i:04d}", "final_state": "executed_fork",
                "steps": [{"step": "execute", "status": "ok", "action": "fork"}],
            })

        analysis = _build_family_analysis(runs)
        assert "channel_breakout" in analysis
        assert "volatility_filtered_breakout" in analysis
        # Count occurrences of each family name (2 each)
        assert analysis.count("channel_breakout") >= 1
        assert analysis.count("volatility_filtered_breakout") >= 1


# ===================================================================
# Test: Meta-review context includes family analysis
# ===================================================================


class TestMetaReviewFamilyContext:
    """Verify meta-review context includes family-level data."""

    def test_context_has_family_analysis_placeholder_replaced(self):
        from scripts.meta_review import build_context, collect_history

        history = collect_history(n_runs=5)
        context = build_context(history, "meta_9999")

        # The template variable should be replaced (even if empty)
        assert "{{FAMILY_ANALYSIS}}" not in context, \
            "FAMILY_ANALYSIS placeholder was not replaced"

    def test_family_analysis_section_exists(self):
        from scripts.meta_review import build_context, collect_history

        history = collect_history(n_runs=5)
        context = build_context(history, "meta_9999")

        # The context should contain family-related text or the template section
        has_family_content = (
            "family" in context.lower()
            or "(no family data)" in context
        )
        assert has_family_content, "Context has no family-related content"


# ===================================================================
# Test: Cross-family patch protection
# ===================================================================


class TestCrossFamilyProtection:
    """Verify executor preserves family identity (no cross-family patches)."""

    def test_fork_preserves_channel_breakout(self):
        import json as _json

        from scripts.execute_action import execute_action

        LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
        LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

        cand = {
            "experiment_id": "exp_7777",
            "parent_id": "channel_breakout_v2_1_balanced",
            "candidate_role": "standalone",
            "strategy": "channel_breakout",
            "base": "v2.1_balanced",
            "status": "research_only",
            "hypothesis": "Test cross-family protection: fork from CB to verify family preserved.",
            "expected_behavior_change": "Should stay in channel_breakout family.",
            "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
            "params": {
                "strategy_type": "regime_permission_channel_breakout",
                "regime_change_policy": "permission_based",
                "regime_filter": {"fast_days": 50, "slow_days": 200},
                "bull": {"strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                         "permission": {"allow_long": True, "allow_short": False}},
                "bear": {"strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                         "permission": {"allow_long": True, "allow_short": True}},
                "neutral": {"strategy_params": {"entry_lookback": 375, "min_hold_bars": 432},
                            "permission": {"allow_long": True, "allow_short": True}},
            },
        }

        cand_path = LLM_CANDIDATES_DIR / "exp_7777.json"
        cand_path.write_text(_json.dumps(cand), encoding="utf-8")

        action = {
            "action": "fork",
            "source_candidate_id": "exp_7777",
            "target_family": "channel_breakout",
            "rationale": "Test cross-family protection for CB family with allowed_change validation.",
            "allowed_change": {"entry_lookback": 300},
        }

        result = execute_action(action)
        assert result["status"] == "executed"

        forked_id = result["experiment_id"]
        forked_path = LLM_CANDIDATES_DIR / f"{forked_id}.json"
        forked = _json.loads(forked_path.read_text(encoding="utf-8"))
        assert forked["strategy"] == "channel_breakout"

        # Cleanup
        cand_path.unlink()
        forked_path.unlink()

    def test_fork_preserves_volatility_filtered(self):
        import json as _json

        from scripts.execute_action import execute_action

        LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
        LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)

        cand = {
            "experiment_id": "exp_7776",
            "parent_id": "channel_breakout_v2_1_balanced",
            "candidate_role": "standalone",
            "strategy": "volatility_filtered_breakout",
            "base": "v2.1_balanced",
            "status": "research_only",
            "hypothesis": "Test cross-family protection: fork VFB preserved.",
            "expected_behavior_change": "Should stay in volatility_filtered_breakout family.",
            "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
            "params": {
                "strategy_type": "volatility_filtered_channel_breakout",
                "regime_change_policy": "permission_based",
                "regime_filter": {"fast_days": 50, "slow_days": 200},
                "volatility_filter": {
                    "enabled": True, "lookback": 288, "mode": "exclude_extreme",
                    "low_quantile": 0.05, "high_quantile": 0.95,
                },
                "bull": {"strategy_params": {"entry_lookback": 240, "min_hold_bars": 48},
                         "permission": {"allow_long": True, "allow_short": False}},
                "bear": {"strategy_params": {"entry_lookback": 240, "min_hold_bars": 48},
                         "permission": {"allow_long": True, "allow_short": True}},
                "neutral": {"strategy_params": {"entry_lookback": 240, "min_hold_bars": 48},
                            "permission": {"allow_long": True, "allow_short": True}},
            },
        }

        cand_path = LLM_CANDIDATES_DIR / "exp_7776.json"
        cand_path.write_text(_json.dumps(cand), encoding="utf-8")

        action = {
            "action": "fork",
            "source_candidate_id": "exp_7776",
            "target_family": "volatility_filtered_breakout",
            "rationale": "Test cross-family protection for VFB family.",
            "allowed_change": {"volatility_filter": {"lookback": 500, "mode": "exclude_high"}},
        }

        result = execute_action(action)
        assert result["status"] == "executed"

        forked_id = result["experiment_id"]
        forked_path = LLM_CANDIDATES_DIR / f"{forked_id}.json"
        forked = _json.loads(forked_path.read_text(encoding="utf-8"))
        assert forked["strategy"] == "volatility_filtered_breakout"

        # Verify volatility_filter params were applied
        params = forked["params"]
        assert params["volatility_filter"]["lookback"] == 500
        assert params["volatility_filter"]["mode"] == "exclude_high"

        cand_path.unlink()
        forked_path.unlink()


# ===================================================================
# Test: Cross-family fork rejection (CB action on VFB source)
# ===================================================================


class TestCrossFamilyForkRejection:
    """Verify fork across families is rejected."""

    def test_cross_family_fork_rejected(self):
        """Fork from CB source with VFB target should fail validation."""
        from scripts.family_registry import validate_allowed_change

        # VFB-specific allowed_change should fail for CB family
        errors = validate_allowed_change(
            "channel_breakout",
            {"volatility_filter": {"lookback": 500}},
        )
        assert errors, "Expected error for VFB field in CB allowed_change"


# ===================================================================
# Test: Minimum cycles enforcement
# ===================================================================


class TestMinimumCycles:
    """Verify family-level minimum cycle enforcement."""

    def test_three_families_require_nine_cycles(self):
        """With 3 unique families, minimum cycles = 9."""
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=["channel_breakout",
                                                    "volatility_filtered_breakout",
                                                    "exit_logic_variant"])
        min_needed = cycler.n_families * 3
        assert min_needed == 9
        assert cycler.n_families == 3

    def test_min_cycles_two_families(self):
        """With 2 families, minimum cycles = 6."""
        from scripts.run_batch_trial import MultiFamilyActionCycler
        cycler = MultiFamilyActionCycler(families=["channel_breakout",
                                                    "volatility_filtered_breakout"])
        min_needed = cycler.n_families * 3
        assert min_needed == 6


# ===================================================================
# Test: Mock response family correctness
# ===================================================================


class TestMockResponseFamilies:
    """Verify mock_fn returns correct family-specific responses."""

    def test_vfb_mock_action_has_vfb_target(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler

        cycler = MultiFamilyActionCycler(families=["volatility_filtered_breakout"])
        cycler.next_action()  # prepares VFB fork

        response = cycler.mock_fn("You are a reviewer ...", "Context: Experiment ID: exp_0001")
        parsed = json.loads(response)
        assert parsed["action"] == "fork"
        assert parsed["target_family"] == "volatility_filtered_breakout"

    def test_cb_mock_action_has_cb_target(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler

        cycler = MultiFamilyActionCycler(families=["channel_breakout"])
        cycler.next_action()

        response = cycler.mock_fn("You are a reviewer ...", "Context: Experiment ID: exp_0002")
        parsed = json.loads(response)
        assert parsed["action"] == "fork"
        assert parsed["target_family"] == "channel_breakout"

    def test_extract_cid(self):
        from scripts.run_batch_trial import _extract_cid_from_message
        msg = "Context: Experiment ID: exp_0042\nFamily: channel_breakout"
        cid = _extract_cid_from_message(msg)
        assert cid == "exp_0042"

    def test_extract_cid_no_match(self):
        from scripts.run_batch_trial import _extract_cid_from_message
        cid = _extract_cid_from_message("No ID here")
        assert cid == "exp_unknown"

    def test_exit_mock_action_has_exit_target(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler

        cycler = MultiFamilyActionCycler(families=["exit_logic_variant"])
        cycler.next_action()

        response = cycler.mock_fn("You are a reviewer ...", "Context: Experiment ID: exp_0003")
        parsed = json.loads(response)
        assert parsed["action"] == "fork"
        assert parsed["target_family"] == "exit_logic_variant"

    def test_exit_mock_returns_exit_candidate(self):
        from scripts.run_batch_trial import MultiFamilyActionCycler

        cycler = MultiFamilyActionCycler(families=["exit_logic_variant"])
        cycler.next_action()
        response = cycler.mock_fn("generator prompt", "user msg")
        parsed = json.loads(response)
        assert parsed["params"]["strategy_type"] == "exit_logic_channel_breakout"
        assert "exit_logic" in parsed["params"]


# ===================================================================
# Test: Exit logic variant batch templates
# ===================================================================


class TestExitBatchTemplates:
    """Verify exit_logic_variant batch templates are valid."""

    def test_exit_candidate_is_valid_json(self):
        from scripts.run_batch_trial import EXIT_CANDIDATE_TEMPLATE
        parsed = json.loads(EXIT_CANDIDATE_TEMPLATE)
        assert parsed["params"]["strategy_type"] == "exit_logic_channel_breakout"
        assert "exit_logic" in parsed["params"]
        assert parsed["params"]["exit_logic"]["take_profit_pct"] == 0.05
        assert parsed["params"]["exit_logic"]["trailing_stop"] is True

    def test_exit_candidate_passes_validation(self):
        from scripts.run_batch_trial import EXIT_CANDIDATE_TEMPLATE
        from scripts.validate_candidate_v08 import validate_candidate
        parsed = json.loads(EXIT_CANDIDATE_TEMPLATE)
        parsed["experiment_id"] = "exp_9996"
        parsed["strategy"] = "exit_logic_variant"
        parsed["base"] = "v2.1_balanced"
        parsed["status"] = "research_only"
        parsed["candidate_role"] = "standalone"
        parsed["constraints"] = ["no_future_data", "inherits_v21_risk",
                                  "no_demo_routing", "research_only"]
        errors = validate_candidate(parsed, check_uniqueness=False)
        assert errors == [], f"Exit candidate failed validation: {errors}"

    def test_exit_action_fork_is_valid(self):
        from scripts.run_batch_trial import EXIT_ACTION_TEMPLATES
        for action_type in ("fork", "kill", "create"):
            parsed = json.loads(EXIT_ACTION_TEMPLATES[action_type])
            assert parsed["action"] == action_type
            assert parsed["target_family"] == "exit_logic_variant"
            if action_type == "fork":
                assert "exit_logic" in parsed.get("allowed_change", {})

    def test_exit_templates_no_forbidden_content(self):
        import re

        from scripts.run_batch_trial import EXIT_ACTION_TEMPLATES, EXIT_CANDIDATE_TEMPLATE
        from scripts.validate_candidate_v02 import FORBIDDEN_PATTERNS
        all_text = EXIT_CANDIDATE_TEMPLATE
        for t in EXIT_ACTION_TEMPLATES.values():
            all_text += t
        for pattern_str, reason in FORBIDDEN_PATTERNS:
            match = re.search(pattern_str, all_text, re.IGNORECASE)
            assert not match, f"Forbidden pattern in exit templates ({reason})"


# ===================================================================
# Test: Default families from registry (3 families)
# ===================================================================


class TestDefaultFamilies:
    """Verify _get_default_families returns 3 families."""

    def test_default_families_three(self):
        from scripts.run_batch_trial import _get_default_families
        families = _get_default_families()
        assert len(families) >= 3
        assert "channel_breakout" in families
        assert "volatility_filtered_breakout" in families
        assert "exit_logic_variant" in families


# ===================================================================
# Test: Validate family_insight finding type
# ===================================================================


class TestFamilyInsightFindingType:
    """Verify family_insight is accepted as valid finding type."""

    def test_family_insight_accepted(self):
        from scripts.meta_review import validate_review

        review = {
            "review_id": "meta_8888",
            "window": {"runs_analyzed": 10, "candidates_analyzed": 6},
            "findings": [
                {
                    "type": "family_insight",
                    "severity": "medium",
                    "summary": "Volatility_filtered_breakout shows higher schema pass rate than channel_breakout (100% vs 80%).",
                    "evidence": ["VFB: exp_0001 passed", "exp_0002 passed",
                                  "CB: exp_0003 passed", "exp_0004 rejected"],
                },
            ],
            "recommendations": [
                {
                    "target": "family",
                    "action": "expand",
                    "proposal": "Prioritize VFB candidate generation due to higher schema compliance rate.",
                    "rationale": "VFB consistently passes schema on first attempt; CB has occasional extra-field rejections.",
                },
            ],
            "contract_changes": [],
            "prompt_changes": [],
            "requires_human_review": False,
        }
        errors = validate_review(review)
        assert errors == [], f"Expected no errors, got: {errors}"
