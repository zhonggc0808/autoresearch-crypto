#!/usr/bin/env python3
"""Candidate spec validator v0.2 — JSON Schema + Python-level checks.

Usage:
    uv run python scripts/validate_candidate_v02.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json

    uv run python scripts/validate_candidate_v02.py \\
        --candidate path/to/candidate.json --verbose

Exit codes:
    0 — valid
    1 — validation errors
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema

PROJECT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCHEMA_PATH = PROJECT_DIR / "research_workspace" / "candidate_schema_v0.2.json"
CANDIDATE_DIRS = [
    PROJECT_DIR / "research_workspace" / "llm_candidates",
]

# ---------------------------------------------------------------------------
# Forbidden content patterns (detected in string values)
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: List[Tuple[str, str]] = [
    # File paths that shouldn't appear in candidates
    (r"checkpoints?[/\\]", "References a checkpoint path"),
    (r"\bcheckpoint\b", "References a checkpoint (bare word)"),
    (r"live_[\w]+\.py", "References a live entry point"),
    (r"research_oracle(?:\.py)?", "References the oracle script"),
    (r"data[/\\]crypto", "References data directory"),
    (r"dex/", "References dex module"),
    # Operations the candidate shouldn't request
    (r"skip_validation", "Attempts to bypass schema validation"),
    (r"force_promot", "Attempts to force promotion bypass"),
    (r"auto_promot", "Attempts to auto-promote"),
    (r"oracle_version.*override", "Attempts to override oracle version"),
    (r"override.*baseline", "Attempts to override baseline"),
    # Dangerous configuration
    (r"enable_live", "Attempts to enable live routing from candidate"),
    (r"routing.*live", "References live routing"),
    (r"bypass.*guard", "Attempts to bypass safety guards"),
    (r"unbounded", "References unbounded operation"),
]

# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


def _load_schema() -> Dict[str, Any]:
    """Load the JSON Schema from disk."""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file not found: {SCHEMA_PATH}\n"
            f"This file must exist for v0.2 validation. "
            f"Create it from candidate_schema_v0.2.json template."
        )
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Python-level checks
# ---------------------------------------------------------------------------


def _check_regime_filter_fast_slow(params: Any, errors: List[str]) -> None:
    """Check fast_days < slow_days (not expressible in JSON Schema draft-07)."""
    if not isinstance(params, dict):
        return
    rf = params.get("regime_filter", {})
    if isinstance(rf, dict):
        fast = rf.get("fast_days")
        slow = rf.get("slow_days")
        if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
            errors.append(
                f"params.regime_filter: fast_days ({fast}) must be < slow_days ({slow})"
            )


def _check_execution_regime_filter_fast_slow(execution: Any, errors: List[str]) -> None:
    """Check execution.regime_filter fast_days < slow_days."""
    if not isinstance(execution, dict):
        return
    rf = execution.get("regime_filter")
    if not isinstance(rf, dict):
        return
    fast = rf.get("fast_days")
    slow = rf.get("slow_days")
    if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
        errors.append(
            f"execution.regime_filter: fast_days ({fast}) must be < slow_days ({slow})"
        )


def _check_dd_guard_values(execution: Any, errors: List[str]) -> None:
    """Check dd_guard.max_dd > dd_guard.recovery_dd if both present."""
    if not isinstance(execution, dict):
        return
    dg = execution.get("dd_guard")
    if not isinstance(dg, dict):
        return
    max_dd = dg.get("max_dd")
    recovery_dd = dg.get("recovery_dd")
    if max_dd is not None and recovery_dd is not None and max_dd <= recovery_dd:
        errors.append(
            f"execution.dd_guard: max_dd ({max_dd}) must be > recovery_dd ({recovery_dd})"
        )


def _check_id_uniqueness(
    experiment_id: str, errors: List[str],
    *,
    current_path: Optional[Path] = None,
    allow_existing: bool = False,
) -> None:
    """Check that experiment_id is not already used by another candidate.

    Scans both filenames AND internal experiment_id fields in JSON files.
    ``current_path`` is the candidate file being validated — it is not flagged
    as a duplicate of itself.

    Unless ``allow_existing=True`` (for test/re-validation of known candidates).
    """
    if not experiment_id or allow_existing:
        return
    for d in CANDIDATE_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix != ".json" or f.name == ".gitkeep":
                continue
            # Skip the file being validated itself
            if current_path is not None and f.resolve() == current_path.resolve():
                continue
            # Check 1: filename matches experiment_id
            if f.stem == experiment_id:
                errors.append(
                    f"Duplicate experiment_id '{experiment_id}': "
                    f"filename conflict in {d.name}/{f.name}"
                )
                continue
            # Check 2: internal experiment_id field matches
            try:
                content = json.loads(f.read_text(encoding="utf-8"))
                if content.get("experiment_id") == experiment_id:
                    errors.append(
                        f"Duplicate experiment_id '{experiment_id}': "
                        f"found inside {d.name}/{f.name}"
                    )
            except (json.JSONDecodeError, IOError, UnicodeDecodeError):
                pass  # skip unreadable files


def _check_forbidden_content(spec: Dict[str, Any], errors: List[str]) -> None:
    """Scan all string values for forbidden patterns."""
    raw = json.dumps(spec, default=str)
    for pattern, reason in FORBIDDEN_PATTERNS:
        if re.search(pattern, raw, re.IGNORECASE):
            errors.append(f"Forbidden content detected ({reason})")


def _check_forbidden_top_level_fields(spec: Dict[str, Any], errors: List[str]) -> None:
    """Extra check: known forbidden field names at top level (belt and suspenders)."""
    forbidden = {"checkpoint_path", "oracle_override", "live_config", "demo_config",
                 "skip_validation", "force_promotion", "auto_approve"}
    for key in spec:
        if key in forbidden:
            errors.append(f"Forbidden field at top level: '{key}'")


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------


def validate_candidate(
    spec: Dict[str, Any],
    *,
    current_path: Optional[Path] = None,
    check_uniqueness: bool = True,
) -> List[str]:
    """Validate a candidate spec against v0.2 schema and rules.

    ``current_path`` is the path to the candidate file (used for duplicate
    detection — the file itself is not flagged as a duplicate).

    Returns a list of error messages (empty = valid).
    """
    errors: List[str] = []

    # --- Load schema ---
    try:
        schema = _load_schema()
    except FileNotFoundError as e:
        errors.append(str(e))
        return errors

    # --- Phase 1: JSON Schema validation (collect ALL errors) ---
    validator = jsonschema.Draft7Validator(schema)
    for e in validator.iter_errors(spec):
        path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
        msg = e.message.replace("\n", " ")
        errors.append(f"[schema] {path}: {msg}")

    # --- Phase 2: Python-level checks (run even if schema errors) ---
    params = spec.get("params", {})

    _check_regime_filter_fast_slow(params, errors)

    execution = spec.get("execution")
    if execution is not None:
        _check_execution_regime_filter_fast_slow(execution, errors)
        _check_dd_guard_values(execution, errors)

    if check_uniqueness:
        _check_id_uniqueness(spec.get("experiment_id", ""), errors,
                             current_path=current_path)

    _check_forbidden_content(spec, errors)
    _check_forbidden_top_level_fields(spec, errors)

    return errors


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def print_errors(errors: List[str]) -> None:
    """Print validation errors in a structured format."""
    if not errors:
        print("[OK] Candidate spec is valid.")
        return
    print(f"[FAIL] {len(errors)} validation error(s):")
    for i, e in enumerate(errors, 1):
        print(f"  {i}. {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Candidate spec validator v0.2"
    )
    parser.add_argument(
        "--candidate", type=str, required=True,
        help="Path to candidate spec JSON file",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print candidate summary even on success",
    )
    parser.add_argument(
        "--allow-existing", action="store_true",
        help="Skip ID uniqueness check (for re-validation of existing candidates)",
    )
    args = parser.parse_args()

    cpath = Path(args.candidate)
    if not cpath.exists():
        print(f"ERROR: Candidate file not found: {cpath}")
        sys.exit(1)

    try:
        with open(cpath, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        sys.exit(1)

    errors = validate_candidate(
        spec,
        current_path=cpath,
        check_uniqueness=not args.allow_existing,
    )

    print_errors(errors)
    for _ in errors:
        pass  # consume iterator

    if args.verbose and not errors:
        eid = spec.get("experiment_id", "?")
        strat = spec.get("strategy", "?")
        hyp = spec.get("hypothesis", "")[:60]
        print(f"  ID:         {eid}")
        print(f"  Strategy:   {strat}")
        print(f"  Hypothesis: {hyp}...")

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
