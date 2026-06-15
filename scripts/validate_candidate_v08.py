#!/usr/bin/env python3
"""Candidate spec validator v0.8 — family-aware multi-family validation.

Extends v0.2 by dispatching to family-specific schema validation based on
the ``strategy`` field. Common rules (forbidden content, constraints, etc.)
are inherited from v0.2.

Usage:
    uv run python scripts/validate_candidate_v08.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema

from scripts.family_registry import get as _get_family
from scripts.family_registry import is_valid as _valid_family
from scripts.family_registry import list_families
from scripts.validate_candidate_v02 import (
    print_errors as _print_errors,
)
from scripts.validate_candidate_v02 import (
    validate_candidate as _validate_v02_common,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_DIR / "research_workspace" / "family_schemas"


def validate_candidate(
    spec: Dict[str, Any],
    *,
    current_path: Optional[Path] = None,
    check_uniqueness: bool = True,
) -> List[str]:
    """Validate a candidate spec against its family schema + v0.2 common rules.

    Returns list of error messages (empty = valid).
    """
    errors: List[str] = []

    # --- Determine family ---
    family = spec.get("strategy", "")
    if not _valid_family(family):
        errors.append(f"Unknown strategy '{family}'. Registered: {list_families()}")
        return errors  # cannot validate further without known family

    fd = _get_family(family)

    # --- Phase 1: Family-specific JSON Schema validation ---
    schema_path = SCHEMAS_DIR / fd.schema_file
    if not schema_path.exists():
        errors.append(f"Schema file not found: {schema_path}")
        return errors

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        errors.append(f"Cannot load schema {schema_path}: {e}")
        return errors

    # Validate params against family schema
    params = spec.get("params", {})
    if not isinstance(params, dict):
        errors.append("'params' must be a dict")
        return errors

    validator = jsonschema.Draft7Validator(schema)
    for e in validator.iter_errors(params):
        path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "params"
        msg = e.message.replace("\n", " ")
        errors.append(f"[schema] {path}: {msg}")

    # --- Phase 2: Strategy type check ---
    st = params.get("strategy_type")
    expected_st = fd.strategy_type
    if st and st != expected_st:
        errors.append(
            f"params.strategy_type mismatch for family '{family}': "
            f"expected '{expected_st}', got '{st}'"
        )

    # --- Phase 3: Filter/overlay validation ---
    role = spec.get("candidate_role", "standalone")
    flt = spec.get("filter")
    if flt is not None:
        if role not in ("filter", "overlay", "standalone"):
            errors.append(
                f"candidate_role '{role}' requires filter field: "
                f"expected 'filter' or 'overlay' or 'standalone'"
            )
        if not isinstance(flt, dict):
            errors.append("'filter' must be a dict")
        else:
            _validate_filter_block(flt, errors)

    # --- Phase 4: Common v0.2 rules ---
    common_errors = _validate_v02_common(
        spec,
        current_path=current_path,
        check_uniqueness=check_uniqueness,
    )
    errors.extend(common_errors)

    return errors


def _validate_filter_block(flt: Dict[str, Any], errors: List[str]) -> None:
    """Validate the filter block structure."""
    fam = flt.get("family", "")
    if not fam:
        errors.append("filter.family is required")
        return

    if not _valid_family(fam):
        errors.append(f"Unknown filter family '{fam}'. Registered: {list_families()}")
        return

    fd = _get_family(fam)
    schema_path = SCHEMAS_DIR / fd.schema_file
    if not schema_path.exists():
        errors.append(f"Filter schema not found: {schema_path}")
        return

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        errors.append(f"Cannot load filter schema {schema_path}: {e}")
        return

    import jsonschema

    validator = jsonschema.Draft7Validator(schema)
    for e in validator.iter_errors(flt):
        path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "filter"
        msg = e.message.replace("\n", " ")
        errors.append(f"[schema] {path}: {msg}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Candidate spec validator v0.8 (multi-family)")
    parser.add_argument(
        "--candidate",
        type=str,
        required=True,
        help="Path to candidate spec JSON file",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
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

    errors = validate_candidate(spec, current_path=cpath)
    _print_errors(errors)

    if args.verbose and not errors:
        print(f"  Family: {spec.get('strategy', '?')}")
        print(f"  ID: {spec.get('experiment_id', '?')}")

    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    main()
