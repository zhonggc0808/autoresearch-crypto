#!/usr/bin/env python3
"""Candidate evaluation orchestrator — dual-window state machine (v0.3).

Orchestrates the candidate lifecycle:

    candidate_created → 1300d oracle → scorecard
        → kill (stop)
        → requires_2600d → check 2600d data
            → blocked_missing_2600d_data (stop)
            → 2600d oracle → scorecard
                → research_only_recent_regime (stop)
                → promote_review_pending (stop)

This script is the ORCHESTRATOR.  It delegates to:
    - ``validate_candidate_v08`` for schema checking (family-aware, supports filter/overlay)
    - ``research_oracle`` for evaluation
    - ``score_candidate`` for scorecard production

It does NOT modify oracle core, demo/live files, or baseline checkpoints.

Usage:
    uv run python scripts/evaluate_candidate.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json

    # Skip 1300d oracle (use existing scorecard)
    uv run python scripts/evaluate_candidate.py \\
        --candidate path/to/candidate.json --resume

    # Force re-evaluation from scratch
    uv run python scripts/evaluate_candidate.py \\
        --candidate path/to/candidate.json --force

    # Dry-run (no oracle, no writes)
    uv run python scripts/evaluate_candidate.py \\
        --candidate path/to/candidate.json --dry-run

Exit codes:
    0 — evaluation completed (any final state)
    1 — candidate invalid
    2 — oracle error
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from scripts.score_candidate import (
    VERDICT_BLOCKED_MISSING_2600D,
    VERDICT_INVALID_CANDIDATE,
    VERDICT_INVALID_ORACLE,
    VERDICT_KILL,
    VERDICT_PROMOTE_REVIEW_PENDING,
    VERDICT_REQUIRES_2600D,
    VERDICT_RESEARCH_ONLY_RECENT_REGIME,
    build_scorecard_from_result,
    load_scorecard,
    write_scorecard_and_log,
)
from scripts.validate_candidate_v08 import validate_candidate as _validate_schema

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
LLM_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"
DATA_DIR = PROJECT_DIR / "data" / "crypto"

# ---------------------------------------------------------------------------
# Evaluation state
# ---------------------------------------------------------------------------

# Terminal states (evaluation stops here)
TERMINAL_STATES = frozenset(
    {
        VERDICT_KILL,
        VERDICT_BLOCKED_MISSING_2600D,
        VERDICT_RESEARCH_ONLY_RECENT_REGIME,
        VERDICT_PROMOTE_REVIEW_PENDING,
        VERDICT_INVALID_CANDIDATE,
    }
)

# Non-terminal states (evaluation should continue)
NON_TERMINAL_STATES = frozenset(
    {
        VERDICT_REQUIRES_2600D,
    }
)


# ---------------------------------------------------------------------------
# Data availability checks
# ---------------------------------------------------------------------------


def _has_2600d_data() -> bool:
    """Check if a 2600d dataset exists.

    Currently the oracle only supports 1300d data.  Once a 2600d parquet
    file is available, this function will detect it.

    Returns:
        True if ``ETHUSDT_5m_2600d.parquet`` exists in the data directory.
    """
    if not DATA_DIR.exists():
        return False
    for f in DATA_DIR.iterdir():
        if f.name.startswith("ETHUSDT") and "5m" in f.name and "2600d" in f.name:
            return True
    return False


# ---------------------------------------------------------------------------
# Oracle runner
# ---------------------------------------------------------------------------


def _run_oracle_1300d(
    candidate_path: Path, fast_days: int = 50, slow_days: int = 200
) -> Dict[str, Any]:
    """Run 1300d oracle evaluation for a candidate spec.

    Calls ``research_oracle.run_oracle()`` with the candidate file and
    persists the result to ``experiments.jsonl`` / ``results.tsv``.
    """
    from scripts.research_oracle import (
        _append_experiments_jsonl,
        _append_results_tsv,
        _write_oracle_report,
    )
    from scripts.research_oracle import (
        run_oracle as _oracle_run,
    )

    result = _oracle_run(
        candidate_path=str(candidate_path),
        fast_days=fast_days,
        slow_days=slow_days,
    )

    _write_oracle_report(result)
    _append_results_tsv(result)
    _append_experiments_jsonl(result)

    return result


# ---------------------------------------------------------------------------
# Evaluation state helpers
# ---------------------------------------------------------------------------


def _load_evaluation_state(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Load existing evaluation state from llm_scorecards/."""
    path = LLM_SCORECARDS_DIR / f"{experiment_id}_evaluation.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_evaluation_state(state: Dict[str, Any]) -> Path:
    """Save evaluation state to llm_scorecards/."""
    LLM_SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    eid = state["experiment_id"]
    path = LLM_SCORECARDS_DIR / f"{eid}_evaluation.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    return path


def _init_evaluation_state(
    experiment_id: str,
    candidate_path: Optional[Path] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Create a new evaluation state record."""
    return {
        "evaluation_version": "v0.3",
        "experiment_id": experiment_id,
        "candidate_path": str(candidate_path) if candidate_path else None,
        "description": description,
        "state": "candidate_created",
        "final_verdict": None,
        "history": [],
    }


def _append_history(state: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """Append a stage-transition entry to the evaluation history."""
    state["history"].append(entry)


# ---------------------------------------------------------------------------
# Stage evaluators
# ---------------------------------------------------------------------------


def evaluate_stage_1300d(
    candidate_path: Path,
    candidate_spec: Dict[str, Any],
    evaluation_state: Dict[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Evaluate a candidate at the 1300d stage.

    Returns (verdict_label, scorecard_or_None).
    """
    eid = candidate_spec.get("experiment_id", "?")

    # Check existing scorecard
    existing = None if force else load_scorecard(eid, "1300d")
    if existing is not None:
        print(f"[evaluate] Found existing 1300d scorecard for {eid}")
        verdict = existing["verdict"]["label"]
        return verdict, existing

    # Run oracle
    print(f"[evaluate] Running 1300d oracle for {eid} ...")
    if dry_run:
        print("  (dry-run: simulating 1300d pass)")
        # Simulate a pass to test the full state machine
        evaluation_state["state"] = "requires_2600d"
        eval_entry = {
            "stage": "1300d",
            "verdict": VERDICT_REQUIRES_2600D,
            "scorecard": "dry_run_simulated",
        }
        _append_history(evaluation_state, eval_entry)
        return VERDICT_REQUIRES_2600D, None

    t0 = time.time()
    try:
        oracle_result = _run_oracle_1300d(candidate_path)
    except Exception as e:
        print(f"[ERROR] 1300d oracle failed: {e}")
        return VERDICT_INVALID_ORACLE, None
    elapsed = time.time() - t0
    print(f"  1300d oracle completed in {elapsed:.1f}s")

    # Build scorecard
    scorecard = build_scorecard_from_result(oracle_result, candidate_spec, stage="1300d")

    # Write
    write_scorecard_and_log(scorecard)
    print(f"  1300d scorecard written: {scorecard['verdict']['label']}")

    verdict_label = scorecard["verdict"]["label"]
    eval_entry = {
        "stage": "1300d",
        "verdict": verdict_label,
        "oracle_id": oracle_result.get("experiment_id"),
        "scorecard": f"{eid}_1300d_scorecard.json",
    }
    _append_history(evaluation_state, eval_entry)

    if verdict_label in (VERDICT_KILL, VERDICT_INVALID_ORACLE):
        evaluation_state["state"] = f"1300d_{verdict_label}"
        evaluation_state["final_verdict"] = verdict_label
    elif verdict_label == VERDICT_REQUIRES_2600D:
        evaluation_state["state"] = "requires_2600d"

    return verdict_label, scorecard


def evaluate_stage_2600d(
    candidate_path: Path,
    candidate_spec: Dict[str, Any],
    evaluation_state: Dict[str, Any],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Evaluate a candidate at the 2600d stage.

    Returns (verdict_label, scorecard_or_None).
    """
    eid = candidate_spec.get("experiment_id", "?")

    # Check 2600d data availability
    if not _has_2600d_data():
        print(f"[evaluate] 2600d data not available for {eid}")
        evaluation_state["state"] = "2600d_blocked_missing_data"
        evaluation_state["final_verdict"] = VERDICT_BLOCKED_MISSING_2600D
        eval_entry = {
            "stage": "2600d_check",
            "verdict": VERDICT_BLOCKED_MISSING_2600D,
            "reason": "2600d dataset not found in data/crypto/",
            "scorecard": None,
        }
        _append_history(evaluation_state, eval_entry)
        return VERDICT_BLOCKED_MISSING_2600D, None

    # Check existing scorecard
    existing = None if force else load_scorecard(eid, "2600d")
    if existing is not None:
        print(f"[evaluate] Found existing 2600d scorecard for {eid}")
        verdict = existing["verdict"]["label"]
        return verdict, existing

    # Run oracle
    print(f"[evaluate] Running 2600d oracle for {eid} ...")
    if dry_run:
        print("  (dry-run: simulating 2600d pass)")
        evaluation_state["state"] = "2600d_passed"
        evaluation_state["final_verdict"] = VERDICT_PROMOTE_REVIEW_PENDING
        eval_entry = {
            "stage": "2600d",
            "verdict": VERDICT_PROMOTE_REVIEW_PENDING,
            "scorecard": "dry_run_simulated",
        }
        _append_history(evaluation_state, eval_entry)
        return VERDICT_PROMOTE_REVIEW_PENDING, None

    t0 = time.time()
    try:
        # 2600d oracle not yet supported — this will raise or return invalid
        # once a 2600d dataset exists, call a 2600d-aware oracle variant here
        raise NotImplementedError(
            "2600d oracle evaluation is not yet implemented. "
            "Requires ETHUSDT_5m_2600d.parquet and oracle support."
        )
    except NotImplementedError as e:
        print(f"[evaluate] {e}")
        evaluation_state["state"] = "2600d_blocked_missing_data"
        evaluation_state["final_verdict"] = VERDICT_BLOCKED_MISSING_2600D
        eval_entry = {
            "stage": "2600d_check",
            "verdict": VERDICT_BLOCKED_MISSING_2600D,
            "reason": str(e),
            "scorecard": None,
        }
        _append_history(evaluation_state, eval_entry)
        return VERDICT_BLOCKED_MISSING_2600D, None

    # --- The code below runs once 2600d oracle exists ---
    # oracle_result = _run_oracle_2600d(candidate_path)
    # elapsed = time.time() - t0
    # scorecard = build_scorecard_from_result(oracle_result, candidate_spec, stage="2600d")
    # write_scorecard_and_log(scorecard)
    # verdict_label = scorecard["verdict"]["label"]
    # eval_entry = {
    #     "stage": "2600d",
    #     "verdict": verdict_label,
    #     "oracle_id": oracle_result.get("experiment_id"),
    #     "scorecard": f"{eid}_2600d_scorecard.json",
    # }
    # _append_history(evaluation_state, eval_entry)
    # if verdict_label == VERDICT_RESEARCH_ONLY_RECENT_REGIME:
    #     evaluation_state["state"] = "2600d_failed"
    #     evaluation_state["final_verdict"] = verdict_label
    # elif verdict_label == VERDICT_PROMOTE_REVIEW_PENDING:
    #     evaluation_state["state"] = "2600d_passed"
    #     evaluation_state["final_verdict"] = verdict_label
    # return verdict_label, scorecard


# ---------------------------------------------------------------------------
# Main evaluation orchestration
# ---------------------------------------------------------------------------


def evaluate_candidate(
    candidate_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
    fast_days: int = 50,
    slow_days: int = 200,
) -> Dict[str, Any]:
    """Run the full evaluation lifecycle for a candidate.

    Returns the final evaluation state dict.
    """
    # --- Load candidate ---
    if not candidate_path.exists():
        print(f"ERROR: Candidate file not found: {candidate_path}")
        return _error_state("?", VERDICT_INVALID_CANDIDATE, f"File not found: {candidate_path}")

    try:
        with open(candidate_path, "r", encoding="utf-8") as fh:
            candidate_spec = json.load(fh)
    except json.JSONDecodeError as e:
        eid = candidate_path.stem
        return _error_state(eid, VERDICT_INVALID_CANDIDATE, f"Invalid JSON: {e}")

    eid = candidate_spec.get("experiment_id", candidate_path.stem)
    print(f"\n{'=' * 60}")
    print(f"  Evaluating candidate: {eid}")
    print(f"{'=' * 60}")

    # --- Validate schema ---
    print("\n[Phase 1] Schema validation ...")
    schema_errors = _validate_schema(
        candidate_spec, current_path=candidate_path, check_uniqueness=False
    )
    if schema_errors:
        print("[FAIL] Candidate schema invalid:")
        for e in schema_errors:
            print(f"  - {e}")
        state = _init_evaluation_state(eid, candidate_path)
        state["state"] = "invalid_candidate"
        state["final_verdict"] = VERDICT_INVALID_CANDIDATE
        state["errors"] = schema_errors
        eval_entry = {
            "stage": "schema_validation",
            "verdict": VERDICT_INVALID_CANDIDATE,
            "errors": schema_errors,
            "scorecard": None,
        }
        _append_history(state, eval_entry)
        if not dry_run:
            _save_evaluation_state(state)
        return state
    print("  [OK] Schema valid")

    # --- Initialize or load evaluation state ---
    state = _load_evaluation_state(eid)
    if state is None or force:
        desc = candidate_spec.get("description", "")
        state = _init_evaluation_state(eid, candidate_path, desc)

    # Check if already in a terminal state
    if state.get("final_verdict") and state["final_verdict"] in TERMINAL_STATES:
        print(f"\n[SKIP] Candidate already evaluated: final_verdict={state['final_verdict']}")
        print("  Use --force to re-evaluate.")
        return state

    # --- Phase 2: 1300d evaluation ---
    print("\n[Phase 2] 1300d evaluation ...")
    verdict_1300d, scorecard_1300d = evaluate_stage_1300d(
        candidate_path,
        candidate_spec,
        state,
        force=force,
        dry_run=dry_run,
    )

    # Terminal check
    if state.get("final_verdict") in TERMINAL_STATES:
        print(f"\n[STOP] 1300d terminal verdict: {state['final_verdict']}")
        if not dry_run:
            _save_evaluation_state(state)
        return state

    # --- Phase 3: 2600d evaluation ---
    print("\n[Phase 3] 2600d evaluation ...")
    verdict_2600d, scorecard_2600d = evaluate_stage_2600d(
        candidate_path,
        candidate_spec,
        state,
        force=force,
        dry_run=dry_run,
    )

    # --- Finalize ---
    if state.get("final_verdict") in TERMINAL_STATES:
        print(f"\n[DONE] Final verdict: {state['final_verdict']}")
    else:
        # Should not reach here — if it does, something is wrong
        state["state"] = "unexpected_state"
        print(f"\n[WARN] Evaluation reached unexpected state: {state['state']}")

    if not dry_run:
        _save_evaluation_state(state)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"  Evaluation complete: {eid}")
    print(f"  Final verdict: {state.get('final_verdict', '?')}")
    print(f"  History: {len(state['history'])} stage(s)")
    for h in state["history"]:
        print(f"    {h['stage']}: {h['verdict']}")
    print(f"{'=' * 60}\n")

    return state


def _error_state(experiment_id: str, verdict: str, reason: str) -> Dict[str, Any]:
    """Build an error evaluation state (no I/O)."""
    return {
        "evaluation_version": "v0.3",
        "experiment_id": experiment_id,
        "state": f"error_{verdict}",
        "final_verdict": verdict,
        "error": reason,
        "history": [],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Candidate evaluation orchestrator v0.3 — dual-window state machine"
    )
    parser.add_argument(
        "--candidate",
        type=str,
        required=True,
        help="Path to candidate spec JSON file",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing evaluation state (skip completed stages)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-evaluation from scratch (ignore existing scorecards)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run: validate + show planned stages, no oracle or writes",
    )
    parser.add_argument(
        "--fast-days",
        type=int,
        default=50,
        help="EMA fast period for regime labels (default: 50)",
    )
    parser.add_argument(
        "--slow-days",
        type=int,
        default=200,
        help="EMA slow period for regime labels (default: 200)",
    )
    args = parser.parse_args()

    candidate_path = Path(args.candidate)
    if not candidate_path.exists():
        print(f"ERROR: Candidate file not found: {candidate_path}")
        sys.exit(1)

    state = evaluate_candidate(
        candidate_path,
        force=args.force,
        dry_run=args.dry_run,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )

    final = state.get("final_verdict", "?")
    if final in (VERDICT_KILL, VERDICT_INVALID_CANDIDATE, VERDICT_INVALID_ORACLE):
        print(f"\nExit: {final}")
        sys.exit(0 if final == VERDICT_KILL else 1)
    elif final == VERDICT_BLOCKED_MISSING_2600D:
        print("\nExit: blocked_missing_2600d_data (expected — 2600d not yet available)")
        sys.exit(0)
    elif final == VERDICT_RESEARCH_ONLY_RECENT_REGIME:
        print("\nExit: research_only_recent_regime")
        sys.exit(0)
    elif final == VERDICT_PROMOTE_REVIEW_PENDING:
        print("\nExit: promote_review_pending -- candidate ready for human review")
        sys.exit(0)
    else:
        print(f"\nExit: {final}")
        sys.exit(2)


if __name__ == "__main__":
    main()
