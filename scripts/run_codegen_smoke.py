#!/usr/bin/env python3
"""Codegen One-Shot Smoke — generate + evaluate in a single command.

Chains v0.9c (generate_code_candidate) and v0.9b (evaluate_codegen_candidate)
into one pipeline.  Produces a codegen_scorecard or a clean failure.

Flow:
    1. generate_code_candidate(mock=...) -> accepted candidate
    2. evaluate_codegen_candidate(codegen_id=..., mock_eval=True) -> scorecard
    3. Print summary

Usage:
    # Mock mode (no API key needed)
    uv run python scripts/run_codegen_smoke.py --mock

    # Real LLM (requires ANTHROPIC_API_KEY)
    uv run python scripts/run_codegen_smoke.py

    # Dry-run (print plan, no writes)
    uv run python scripts/run_codegen_smoke.py --dry-run

Exit codes:
    0 — smoke passed: candidate generated + evaluated + scorecard written
    1 — input error / configuration error
    2 — generation failed (rejected, bad LLM response, missing API key)
    3 — evaluation failed (evaluation error, data loading failure)

Boundaries:
    - Does NOT modify oracle, dex/, baseline, demo, or live code
    - Does NOT write to llm_results.tsv
    - Codegen scorecard always uses codegen_research_only verdict
    - Mock mode works without any external dependencies
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def run_smoke(*, mock: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """Run the full codegen one-shot smoke pipeline.

    Parameters
    ----------
    mock : bool
        If True, use mock strategy code (no API key needed).
        If False, attempt real LLM generation via Anthropic API.
    dry_run : bool
        If True, print plan and verify inputs, skip all writes.

    Returns
    -------
    dict with keys: status, exit_code, codegen_id, scorecard_path, error.
    """
    print(f"\n{'=' * 60}")
    print("  Codegen One-Shot Smoke")
    print(f"  Mode: {'MOCK' if mock else 'LIVE'}{' (DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 60}")

    # ------------------------------------------------------------------
    # Phase 1: Generate
    # ------------------------------------------------------------------
    print("\n--- Phase 1: Generate ---")
    from scripts.generate_code_candidate import generate_code_candidate

    gen_result = generate_code_candidate(mock=mock, dry_run=dry_run)

    gen_status = gen_result.get("status", "error")

    if dry_run and gen_status == "dry_run":
        print("\n[DRY-RUN] Generation step verified. Skipping evaluation (no writes).")
        return {
            "status": "dry_run",
            "exit_code": 0,
            "codegen_id": gen_result.get("codegen_id", "?"),
            "scorecard_path": None,
            "error": None,
        }

    if gen_status == "generate_failed":
        errors = gen_result.get("errors", ["Unknown generation error"])
        print(f"\n[FAIL] Generation failed: {'; '.join(str(e) for e in errors)}")
        return {
            "status": "generate_failed",
            "exit_code": 2,
            "codegen_id": gen_result.get("codegen_id", "?"),
            "scorecard_path": None,
            "error": errors[0] if errors else "Generation failed",
        }

    if gen_status == "rejected":
        errors = gen_result.get("errors", ["Candidate rejected"])
        print(f"\n[FAIL] Candidate rejected: {'; '.join(str(e) for e in errors)}")
        return {
            "status": "rejected",
            "exit_code": 2,
            "codegen_id": gen_result.get("codegen_id", "?"),
            "scorecard_path": None,
            "error": errors[0] if errors else "Candidate rejected",
        }

    if gen_status != "accepted":
        print(f"\n[FAIL] Unexpected generation status: {gen_status}")
        return {
            "status": "generate_failed",
            "exit_code": 2,
            "codegen_id": gen_result.get("codegen_id", "?"),
            "scorecard_path": None,
            "error": f"Unexpected generation status: {gen_status}",
        }

    codegen_id = gen_result["codegen_id"]
    print(f"\n[OK] Candidate generated: {codegen_id}")

    # ------------------------------------------------------------------
    # Phase 2: Evaluate
    # ------------------------------------------------------------------
    print("\n--- Phase 2: Evaluate ---")
    from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

    # Always use mock_eval=True for smoke (synthetic data).
    # Real evaluation with market data is a separate step.
    eval_result = evaluate_codegen_candidate(
        codegen_id=codegen_id,
        mock_eval=True,
        dry_run=False,
    )

    eval_status = eval_result.get("status", "error")

    if eval_status == "success":
        sc_path = eval_result.get("scorecard_path", "?")
        print(f"\n{'=' * 60}")
        print("  SMOKE PASSED")
        print(f"  Candidate: {codegen_id}")
        print("  Verdict:   codegen_research_only (promotion_eligible=false)")
        print(f"  Scorecard: {sc_path}")
        print(f"{'=' * 60}\n")
        return {
            "status": "success",
            "exit_code": 0,
            "codegen_id": codegen_id,
            "scorecard_path": sc_path,
            "error": None,
        }

    # Evaluation failed
    error = eval_result.get("error", "Unknown evaluation error")
    print(f"\n[FAIL] Evaluation failed: {error}")
    return {
        "status": "evaluation_failed",
        "exit_code": 3,
        "codegen_id": codegen_id,
        "scorecard_path": None,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Codegen One-Shot Smoke — generate + evaluate in one command"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock strategy code (no API key needed)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan, verify inputs, skip all writes",
    )
    args = parser.parse_args()

    result = run_smoke(mock=args.mock, dry_run=args.dry_run)
    sys.exit(result.get("exit_code", 1))


if __name__ == "__main__":
    main()
