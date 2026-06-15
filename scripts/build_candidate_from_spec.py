#!/usr/bin/env python3
"""Build and evaluate a research candidate from a spec JSON.

v0.1 — Manual Candidate Spec Loop

Usage:
    # Build a candidate from spec, run oracle, produce results
    uv run python scripts/build_candidate_from_spec.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json

    # Validate only (skip oracle evaluation)
    uv run python scripts/build_candidate_from_spec.py \\
        --candidate path/to/candidate.json --validate-only

    # Dry-run (validate + oracle, but no file writes)
    uv run python scripts/build_candidate_from_spec.py \\
        --candidate path/to/candidate.json --no-write

Exit codes:
    0 — success (candidate built, oracle completed)
    1 — validation error (invalid schema)
    2 — oracle error (evaluation failed)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on sys.path
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Schema validation v0.2 (delegates to validate_candidate_v02)
# ---------------------------------------------------------------------------

from scripts.validate_candidate_v08 import validate_candidate as _validate_v08
from scripts.family_registry import list_families

ALLOWED_STRATEGIES = set(list_families())
ALLOWED_ROLES = {"standalone", "filter", "overlay", "ensemble_component"}


def _find_next_experiment_id(candidates_dirs: List[Path]) -> str:
    """Scan candidate directories for the next sequential exp_NNNN ID."""
    seen = set()
    for d in candidates_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix == ".json":
                m = re.match(r"exp_(\d+)", f.stem)
                if m:
                    seen.add(int(m.group(1)))
    if not seen:
        return "exp_0001"
    next_num = max(seen) + 1
    return f"exp_{next_num:04d}"


def validate_candidate_spec(
    spec: Dict[str, Any],
    spec_path: Optional[Path] = None,
    auto_assign_id: bool = True,
    candidates_dirs: Optional[List[Path]] = None,
) -> List[str]:
    """Validate a candidate spec against v0.2 schema.

    Delegates to ``validate_candidate_v02.validate_candidate()``.
    Returns a list of error messages (empty = valid).
    """
    # Auto-assign experiment_id if missing (before v0.2 schema validation)
    if auto_assign_id and candidates_dirs and "experiment_id" not in spec:
        spec["experiment_id"] = _find_next_experiment_id(candidates_dirs)

    # Delegate to v0.8 validator (family-aware schema + common rules)
    return _validate_v08(
        spec,
        current_path=spec_path,
        check_uniqueness=(not auto_assign_id),  # skip uniqueness if auto-assigning
    )


# ---------------------------------------------------------------------------
# Oracle integration
# ---------------------------------------------------------------------------

def _run_oracle(candidate_path: Path, no_write: bool = False,
                fast_days: int = 50, slow_days: int = 200) -> Dict[str, Any]:
    """Run the oracle on a candidate spec, return result dict, persist outputs."""
    # Avoid circular import issues by importing inline
    from scripts.research_oracle import (
        run_oracle as _oracle_run,
        _write_oracle_report,
        _append_results_tsv,
        _append_experiments_jsonl,
    )

    result = _oracle_run(
        candidate_path=str(candidate_path),
        fast_days=fast_days,
        slow_days=slow_days,
    )

    if not no_write:
        _write_oracle_report(result)
        _append_results_tsv(result)
        _append_experiments_jsonl(result)

    return result


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_summary(result: Dict[str, Any]) -> None:
    """Print a human-readable summary of oracle results."""
    m = result["metrics"]
    f = result["flags"]
    print()
    print("=== Oracle Result ===")
    print(f"  Experiment: {result['experiment_id']}")
    print(f"  Status: {f['status']}")
    print(f"  Strategy: {result['strategy']}")
    print(f"  Timestamp: {result['timestamp']}")
    print()
    print("  --- IS (raw) ---")
    ir = m["is"]["raw"]
    print(f"    Return: {ir['return']:.4f}   DD: {ir['dd']:.4f}   "
          f"Sharpe: {ir['sharpe']:.4f}")
    print(f"    Trades/yr: {ir['trades_per_year']}   Win rate: {ir['win_rate']:.4f}")
    print()
    print("  --- OOS (safe) ---")
    osf = m["oos"]["safe_execution"]
    print(f"    Return: {osf['return']:.4f}   DD: {osf['dd']:.4f}   "
          f"Sharpe: {osf['sharpe']:.4f}")
    print()
    if f.get("warnings"):
        print(f"  Warnings: {f['warnings']}")
    if f.get("disqualifications"):
        print(f"  DISQUALIFICATIONS: {f['disqualifications']}")
    print(f"  Execution parity: {m['execution_parity']}")
    corr = m.get("correlation", {}).get("vs_baseline", "N/A")
    print(f"  Correlation vs baseline: {corr}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build and evaluate a research candidate from spec JSON"
    )
    parser.add_argument(
        "--candidate", type=str, required=True,
        help="Path to candidate spec JSON file",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Only validate schema, skip oracle evaluation",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Run oracle in dry-run mode (no file writes)",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Move candidate to this directory after processing (default: llm_candidates/)",
    )
    parser.add_argument(
        "--fast-days", type=int, default=50,
        help="EMA fast period for regime labels (default: 50)",
    )
    parser.add_argument(
        "--slow-days", type=int, default=200,
        help="EMA slow period for regime labels (default: 200)",
    )
    args = parser.parse_args()

    candidate_path = Path(args.candidate)
    if not candidate_path.exists():
        print(f"ERROR: Candidate file not found: {candidate_path}")
        sys.exit(1)

    # Load spec
    try:
        with open(candidate_path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {candidate_path}: {e}")
        sys.exit(1)

    # --- Validate ---
    candidates_dirs = [
        PROJECT_DIR / "research_workspace" / "llm_candidates",
        PROJECT_DIR / "research_workspace" / "candidates",
    ]
    errors = validate_candidate_spec(
        spec,
        spec_path=candidate_path,
        candidates_dirs=candidates_dirs,
    )
    if errors:
        print("=== CANDIDATE VALIDATION FAILED ===")
        for e in errors:
            print(f"  [ERR] {e}")
        print()
        print("Fix the spec and re-run.")
        sys.exit(1)

    # Save the validated spec (auto-assigned ID may have been set)
    if "experiment_id" in spec and candidate_path.stem != spec["experiment_id"]:
        # Rename file to match experiment_id
        new_name = candidate_path.with_stem(spec["experiment_id"])
        candidate_path.rename(new_name)
        candidate_path = new_name
        print(f"  Renamed candidate to {candidate_path.name}")

    print(f"[OK] Candidate schema valid: {spec.get('experiment_id', '?')}")
    print(f"  Description: {spec.get('description', '?')}")
    print(f"  Strategy: {spec.get('strategy')} / Role: {spec.get('candidate_role')}")

    if args.validate_only:
        print("\n  --validate-only: stopping here (no oracle evaluation).")
        sys.exit(0)

    # --- Extract regime filter from candidate spec ---
    rf = spec.get("params", {}).get("regime_filter", {})
    fast_days = rf.get("fast_days", args.fast_days)
    slow_days = rf.get("slow_days", args.slow_days)
    if rf:
        print(f"  Regime filter from spec: fast={fast_days}d / slow={slow_days}d")
    elif args.fast_days != 50 or args.slow_days != 200:
        print(f"  Regime filter from CLI: fast={fast_days}d / slow={slow_days}d")

    # --- Run oracle ---
    print("\n--- Running oracle evaluation ---")
    t0 = time.time()
    try:
        result = _run_oracle(
            candidate_path,
            no_write=args.no_write,
            fast_days=fast_days,
            slow_days=slow_days,
        )
    except Exception as e:
        print(f"ERROR: Oracle evaluation failed: {e}")
        sys.exit(2)

    elapsed = time.time() - t0
    _print_summary(result)
    print(f"\n  Elapsed: {elapsed:.1f}s")

    # --- Move candidate to llm_candidates/ if requested ---
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / candidate_path.name
        candidate_path.rename(dest)
        print(f"  Moved candidate to {dest}")

    # Exit code based on oracle status
    flags = result["flags"]
    if flags.get("status") == "REJECT":
        print("\n[WARN] Candidate REJECTED by oracle (disqualifications active).")
        sys.exit(0)  # Not a script error, just a candidate status
    else:
        print(f"\n[OK] Candidate status: {flags['status']}")
        sys.exit(0)


if __name__ == "__main__":
    main()
