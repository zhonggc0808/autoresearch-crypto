#!/usr/bin/env python3
"""LLM Research Organization Health Check v1.0.

Verifies that the research org is correctly installed and functional.
Runs a series of checks and reports results.

Usage:
    uv run python scripts/verify_llm_research_org.py

Exit codes:
    0 — all checks passed (or only informational warnings)
    1 — one or more required checks failed
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Check categories
# ---------------------------------------------------------------------------

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"
SKIP = "[SKIP]"

results: list = []
exit_code = 0


def check(name: str, status: str, detail: str = "") -> None:
    """Record a check result."""
    results.append({"name": name, "status": status, "detail": detail})
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


# ===================================================================
# 1. Version marker
# ===================================================================


def check_version_marker() -> None:
    print("\n[1] Version Marker")

    path = PROJECT_DIR / "research_workspace" / "LLM_RESEARCH_ORG_VERSION.json"
    if not path.exists():
        check("Version marker exists", FAIL, f"Not found: {path}")
        return

    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(v.get("version"), str), "version must be string"
        assert isinstance(v.get("families"), list), "families must be list"
        assert v.get("promotion") == "human_review_required", "promotion must be human_review_required"
        codegen = v.get("codegen", {})
        if isinstance(codegen, str):
            assert codegen in ("disabled", "limited"), f"codegen must be disabled/limited"
        elif isinstance(codegen, dict):
            assert codegen.get("status") in ("disabled", "limited"), f"codegen.status must be disabled/limited"
        check("Version marker valid", PASS, f"v{v['version']}, {len(v['families'])} families")
    except (json.JSONDecodeError, AssertionError, KeyError) as e:
        check("Version marker valid", FAIL, str(e))


# ===================================================================
# 2. Family registry
# ===================================================================


def check_family_registry() -> None:
    print("\n[2] Family Registry")

    try:
        from scripts.family_registry import list_families, get, is_valid

        families = list_families()
        if len(families) >= 3:
            check("Registry has 3+ families", PASS, f"{len(families)} registered: {families}")
        else:
            check("Registry has 3+ families", FAIL, f"Only {len(families)} families: {families}")

        for f in ["channel_breakout", "volatility_filtered_breakout", "exit_logic_variant"]:
            if is_valid(f):
                fd = get(f)
                if fd and fd.schema_file:
                    check(f"  {f}", PASS, f"schema={fd.schema_file}, type={fd.strategy_type}")
                else:
                    check(f"  {f}", FAIL, "missing definition or schema_file")
            else:
                check(f"  {f}", FAIL, "not registered")

        # Allowed_change validation
        from scripts.family_registry import validate_allowed_change

        errs = validate_allowed_change("channel_breakout", {"entry_lookback": 300})
        if errs:
            check("allowed_change CB", FAIL, str(errs))
        else:
            check("allowed_change CB valid", PASS)

        errs = validate_allowed_change(
            "volatility_filtered_breakout",
            {"volatility_filter": {"lookback": 500, "mode": "exclude_low"}},
        )
        if errs:
            check("allowed_change VFB", FAIL, str(errs))
        else:
            check("allowed_change VFB valid", PASS)

        errs = validate_allowed_change(
            "exit_logic_variant",
            {"exit_logic": {"take_profit_pct": 0.08, "trailing_stop": False}},
        )
        if errs:
            check("allowed_change ELV", FAIL, str(errs))
        else:
            check("allowed_change ELV valid", PASS)

        # Cross-family rejection
        errs = validate_allowed_change(
            "channel_breakout",
            {"volatility_filter": {"lookback": 500}},
        )
        if errs:
            check("Cross-family rejection", PASS, "volatility_filter rejected for CB")
        else:
            check("Cross-family rejection", FAIL, "volatility_filter not rejected for CB")

    except Exception as e:
        check("Family registry", FAIL, str(e))


# ===================================================================
# 3. Schema files
# ===================================================================


def check_schemas() -> None:
    print("\n[3] Schema Files")

    schemas_dir = PROJECT_DIR / "research_workspace" / "family_schemas"
    if not schemas_dir.exists():
        check("Schema directory", FAIL, "Not found")
        return

    schema_files = list(schemas_dir.glob("*.json"))
    for sf in schema_files:
        try:
            json.loads(sf.read_text(encoding="utf-8"))
            check(f"  {sf.name}", PASS)
        except json.JSONDecodeError as e:
            check(f"  {sf.name}", FAIL, str(e))

    # Common schema
    common = PROJECT_DIR / "research_workspace" / "candidate_schema_v0.2.json"
    if common.exists():
        try:
            s = json.loads(common.read_text(encoding="utf-8"))
            types = s.get("properties", {}).get("params", {}).get("properties", {}).get("strategy_type", {}).get("enum", [])
            check("Common schema valid", PASS, f"{len(types)} strategy_types: {types}")
        except json.JSONDecodeError as e:
            check("Common schema valid", FAIL, str(e))
    else:
        check("Common schema exists", FAIL, "Not found")


# ===================================================================
# 4. Prompt files
# ===================================================================


def check_prompts() -> None:
    print("\n[4] Prompt Files")

    prompts_dir = PROJECT_DIR / "research_agents" / "prompts"
    required_prompts = ["candidate_generator.md", "result_reviewer.md", "meta_review.md"]

    for name in required_prompts:
        path = prompts_dir / name
        if path.exists():
            content = path.read_text(encoding="utf-8")
            # Check for un-replaced template variables
            unfilled = [v for v in ["{{", "{%"] if v in content]
            if unfilled:
                check(f"  {name}", WARN, f"Possible unfilled template vars: {unfilled}")
            else:
                check(f"  {name}", PASS)
        else:
            check(f"  {name}", FAIL, "Not found")


# ===================================================================
# 5. Workspace directories
# ===================================================================


def check_workspace_dirs() -> None:
    print("\n[5] Workspace Directories")

    required = [
        "llm_candidates",
        "llm_scorecards",
        "llm_runs",
        "meta_reviews",
        "proposals",
        "proposals/actions",
        "proposals/rejected_actions",
        "proposals/create_requests",
        "proposals/promotion_reviews",
    ]

    base = PROJECT_DIR / "research_workspace"
    for rel in required:
        path = base / rel
        if path.exists() and path.is_dir():
            check(f"  {rel}", PASS)
        else:
            try:
                path.mkdir(parents=True, exist_ok=True)
                check(f"  {rel}", WARN, "Created (was missing)")
            except Exception as e:
                check(f"  {rel}", FAIL, str(e))


# ===================================================================
# 6. Known failures policy
# ===================================================================


def check_known_failures() -> None:
    print("\n[6] Known Failures Policy")

    path = PROJECT_DIR / "tests" / "known_failures.md"
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if "Known Test Failures" in content:
            check("known_failures.md exists and valid", PASS)
        else:
            check("known_failures.md exists", WARN, "Unexpected content")
    else:
        check("known_failures.md exists", FAIL, "Not found")


# ===================================================================
# 7. Key script imports
# ===================================================================


def check_script_imports() -> None:
    print("\n[7] Script Imports (syntax check)")

    scripts = [
        "scripts/family_registry.py",
        "scripts/generate_candidate.py",
        "scripts/validate_candidate_v02.py",
        "scripts/validate_candidate_v08.py",
        "scripts/evaluate_candidate.py",
        "scripts/score_candidate.py",
        "scripts/review_candidate.py",
        "scripts/execute_action.py",
        "scripts/run_llm_research_cycle.py",
        "scripts/run_batch_trial.py",
        "scripts/meta_review.py",
    ]

    for rel in scripts:
        path = PROJECT_DIR / rel
        if not path.exists():
            check(f"  {rel}", FAIL, "Not found")
            continue
        # Use utf-8 to avoid GBK encoding issues on Windows
        try:
            src = path.read_text(encoding="utf-8")
            compile(src, str(path), "exec")
            check(f"  {rel}", PASS)
        except SyntaxError as e:
            check(f"  {rel}", FAIL, f"SyntaxError: {e}")


# ===================================================================
# 8. Forbidden paths
# ===================================================================


def check_forbidden_paths() -> None:
    print("\n[8] Forbidden Path Integrity")

    guarded = [
        ("checkpoints", PROJECT_DIR / "checkpoints"),
        ("dex core", PROJECT_DIR / "dex" / "strategies" / "base.py"),
        ("oracle", PROJECT_DIR / "scripts" / "research_oracle.py"),
    ]

    for name, path in guarded:
        if path.exists():
            check(f"  {name} intact", PASS)
        else:
            check(f"  {name} intact", WARN, "Path not found (may be expected)")


# ===================================================================
# 9. Smoke cycle
# ===================================================================


def check_smoke_cycle() -> None:
    print("\n[9] Mock Smoke Cycle")

    try:
        from unittest.mock import patch
        from scripts.run_llm_research_cycle import ResearchCycle

        cycle = ResearchCycle(mock=True, evaluate=False)
        def _smoke_mock(system_prompt: str, user_message: str) -> str:
            # Extract actual experiment ID from reviewer context
            cid = "exp_0001"
            for line in user_message.split("\n"):
                for word in line.split():
                    w = word.strip(".,:;!?")
                    if w.startswith("exp_") and len(w) > 4 and w[4:].isdigit():
                        cid = w
                        break
                if cid != "exp_0001":
                    break
            if "reviewer" in system_prompt.lower():
                return json.dumps({
                    "action": "fork",
                    "source_candidate_id": cid,
                    "target_family": "channel_breakout",
                    "rationale": "Verification smoke cycle: fork to validate executor pipeline.",
                    "allowed_change": {"entry_lookback": 350},
                    "risk_note": "Verification cycle.",
                })
            return json.dumps({
                       "parent_id": "channel_breakout_v2_1_balanced",
                       "description": "Verify smoke cycle",
                       "hypothesis": "Verification smoke cycle: testing system health across all components.",
                       "expected_behavior_change": "Should complete 6/6 steps and produce valid output.",
                       "params": {
                           "strategy_type": "regime_permission_channel_breakout",
                           "regime_change_policy": "permission_based",
                           "regime_filter": {"fast_days": 50, "slow_days": 200},
                           "bull": {
                               "candidate": "verif_bull",
                               "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                                   "enable_long": True, "enable_short": False},
                               "permission": {"allow_long": True, "allow_short": False,
                                              "close_below_ema_disables_long": True,
                                              "ema_fast": 50, "consecutive_below_ema_days": 3},
                           },
                           "bear": {
                               "candidate": "verif_bear",
                               "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                                   "enable_long": True, "enable_short": True},
                               "permission": {"allow_long": True, "allow_short": True},
                           },
                           "neutral": {
                               "candidate": "verif_neutral",
                               "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                                   "enable_long": True, "enable_short": True},
                               "permission": {"allow_long": True, "allow_short": True,
                                              "directional_only": True, "ema_fast": 50,
                                              "ema_slope_days": 5},
                           },
                       },
                   })

        with patch("scripts.run_llm_research_cycle._mock_llm_response", _smoke_mock):
            summary = cycle.run()

        state = summary.get("final_state", "?")
        steps_ok = sum(1 for s in summary.get("steps", []) if s.get("status") == "ok")
        total_steps = len(summary.get("steps", []))

        if state.startswith("executed_"):
            check("Smoke cycle completes", PASS, f"{state} ({steps_ok}/{total_steps} steps)")
        elif state == "completed":
            check("Smoke cycle completes", PASS, f"{state} ({steps_ok}/{total_steps} steps)")
        else:
            check("Smoke cycle completes", FAIL, f"Final state: {state}")

    except Exception as e:
        check("Smoke cycle", FAIL, str(e))
        import traceback
        check("  traceback", FAIL, traceback.format_exc()[:200])


# ===================================================================
# Main
# ===================================================================


def main() -> int:
    global exit_code

    print(f"\n{'='*60}")
    print(f"  LLM Research Organization — Health Check v1.0")
    print(f"  Project: {PROJECT_DIR}")
    print(f"{'='*60}")

    check_version_marker()
    check_family_registry()
    check_schemas()
    check_prompts()
    check_workspace_dirs()
    check_known_failures()
    check_script_imports()
    check_forbidden_paths()
    check_smoke_cycle()

    # Summary
    print(f"\n{'='*60}")
    passed = sum(1 for r in results if r["status"] == PASS)
    failed = sum(1 for r in results if r["status"] == FAIL)
    warns = sum(1 for r in results if r["status"] == WARN)
    skipped = sum(1 for r in results if r["status"] == SKIP)

    print(f"  Results: {passed} passed, {failed} failed, {warns} warnings, {skipped} skipped")
    print(f"  Total:   {len(results)} checks")

    if failed > 0:
        print(f"\n  {FAIL} {failed} check(s) failed — review details above.")
        exit_code = 1
    else:
        print(f"\n  [OK] All required checks passed.")

    print(f"{'='*60}\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
