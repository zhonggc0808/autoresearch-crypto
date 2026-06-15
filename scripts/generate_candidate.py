#!/usr/bin/env python3
"""LLM Candidate Generator — v0.4.

Runner script: loads context → calls LLM → validates → persists/rejects.

The LLM only proposes parameter changes. The runner:
    - Assigns experiment_id
    - Injects fixed fields (base, status, constraints, candidate_role, strategy)
    - Validates via validate_candidate_v08 (family-aware, supports filter/overlay)
    - Writes valid candidates to llm_candidates/
    - Writes rejected candidates to proposals/rejected_*.json

Usage:
    # Generate with auto-context (reads recent results, schema, prompt)
    uv run python scripts/generate_candidate.py

    # Dry-run: show prompt + expected output, don't call LLM
    uv run python scripts/generate_candidate.py --dry-run

    # Generate then evaluate
    uv run python scripts/generate_candidate.py --evaluate-after-generate

    # Generate with custom context
    uv run python scripts/generate_candidate.py --context-file my_context.txt

Exit codes:
    0 — candidate generated and accepted
    1 — candidate rejected (validation failed)
    2 — LLM call failed
    3 — internal error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from scripts.llm_client import call_llm
from scripts.validate_candidate_v08 import validate_candidate

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROMPT_PATH = PROJECT_DIR / "research_agents" / "prompts" / "candidate_generator.md"
PROGRAM_PATH = PROJECT_DIR / "research_agents" / "program.md"
SCHEMA_PATH = PROJECT_DIR / "research_workspace" / "candidate_schema_v0.2.json"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
LLM_RESULTS_TSV = PROJECT_DIR / "research_workspace" / "llm_results.tsv"
PROPOSALS_DIR = PROJECT_DIR / "research_workspace" / "proposals"

# Fields injected by runner (LLM must NOT include these)
INJECTED_FIELDS = {
    "candidate_role": "standalone",
    "base": "v2.1_balanced",
    "status": "research_only",
    "constraints": [
        "no_future_data",
        "inherits_v21_risk",
        "no_demo_routing",
        "research_only",
    ],
}


def _resolve_strategy_from_params(params: Dict[str, Any]) -> str:
    """Look up strategy family from params.strategy_type via the registry.

    Falls back to channel_breakout if resolution fails.
    """
    try:
        from scripts.family_registry import get as _reg_get

        st = params.get("strategy_type", "")
        if st:
            for name in _list_families_from_registry():
                fd = _reg_get(name)
                if fd and fd.strategy_type == st:
                    return name
        return "channel_breakout"
    except Exception:
        return "channel_breakout"


def _list_families_from_registry() -> list:
    try:
        from scripts.family_registry import list_families

        return list_families()
    except Exception:
        return ["channel_breakout"]


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _load_prompt_template() -> str:
    """Load the candidate_generator.md prompt template."""
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt template not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_recent_results(n: int = 10) -> str:
    """Load last N rows from llm_results.tsv as a summary string."""
    if not LLM_RESULTS_TSV.exists():
        return "(no results yet)"

    lines = LLM_RESULTS_TSV.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 1:
        return "(no results yet)"

    # Parse TSV header to find columns
    header = lines[0].split("\t")
    data_lines = lines[-n:]  # last N data rows

    summaries = []
    for line in data_lines:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        eid = row.get("experiment_id", "?")
        verdict = row.get("verdict", "?")
        stage = row.get("stage", "?")
        warnings = row.get("warnings", "")
        disqual = row.get("disqualifications", "")
        desc = row.get("description", "")[:60]

        parts_clean = [eid, stage, verdict]
        if desc:
            parts_clean.append(desc)
        if disqual:
            parts_clean.append(f"DISQUAL:{disqual}")
        elif warnings:
            parts_clean.append(f"WARN:{warnings}")
        summaries.append(" | ".join(parts_clean))

    return "\n".join(summaries)


def _get_next_experiment_id() -> str:
    """Find the next available exp_NNNN ID."""
    seen = set()
    for d in [LLM_CANDIDATES_DIR, PROJECT_DIR / "research_workspace" / "candidates"]:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix == ".json":
                m = re.match(r"exp_(\d+)", f.stem)
                if m:
                    seen.add(int(m.group(1)))
    if not seen:
        return "exp_0018"  # start after existing candidates
    next_num = max(seen) + 1
    return f"exp_{next_num:04d}"


def build_context(
    experiment_id: str,
    parent_id: str = "channel_breakout_v2_1_balanced",
    family: str = "channel_breakout",
) -> str:
    """Build the full context string for the LLM prompt."""
    template = _load_prompt_template()
    recent = _load_recent_results()

    # Get search space from family registry
    from scripts.family_registry import search_space_summary

    search_space = search_space_summary(family)

    # Inject template variables
    context = (
        template.replace("{{EXPERIMENT_ID}}", experiment_id)
        .replace("{{PARENT_ID}}", parent_id)
        .replace("{{RECENT_RESULTS}}", recent)
        .replace("{{SEARCH_SPACE}}", search_space)
    )
    return context


# ---------------------------------------------------------------------------
# LLM response parsing
# ---------------------------------------------------------------------------


def _parse_llm_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response into a candidate dict.

    Handles: pure JSON, JSON inside markdown code fences, error objects.
    Returns None if parsing fails.
    """
    text = response_text.strip()

    # Try to extract JSON from markdown code fences
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()

    # Try direct JSON parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object with heuristics
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                parsed = json.loads(text[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Check for error response
    if isinstance(parsed, dict) and "error" in parsed:
        return parsed  # LLM explicitly said "no valid hypothesis"

    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Candidate building
# ---------------------------------------------------------------------------


def _build_full_candidate(
    experiment_id: str,
    llm_proposal: Dict[str, Any],
) -> Dict[str, Any]:
    """Inject runner-assigned fields into the LLM's proposal."""
    candidate = dict(INJECTED_FIELDS)
    candidate["experiment_id"] = experiment_id

    # Resolve strategy from params.strategy_type via family registry
    params = llm_proposal.get("params", {})
    candidate["strategy"] = _resolve_strategy_from_params(params)

    # Copy fields from LLM proposal (with fallbacks)
    for key in ("parent_id", "description", "hypothesis", "expected_behavior_change"):
        if key in llm_proposal:
            candidate[key] = llm_proposal[key]
    # Fallbacks for required fields the LLM sometimes omits
    if "description" not in candidate and "hypothesis" in candidate:
        candidate["description"] = candidate["hypothesis"][:160]
    if "expected_behavior_change" not in candidate and "hypothesis" in candidate:
        candidate["expected_behavior_change"] = candidate["hypothesis"][:160]

    # Copy params
    if "params" in llm_proposal:
        candidate["params"] = llm_proposal["params"]

    # Copy execution if provided
    if "execution" in llm_proposal:
        candidate["execution"] = llm_proposal["execution"]

    # Copy filter if provided — auto-set candidate_role to filter
    if "filter" in llm_proposal:
        candidate["filter"] = llm_proposal["filter"]
        candidate["candidate_role"] = "filter"

    return candidate


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _write_candidate(candidate: Dict[str, Any]) -> Path:
    """Write a valid candidate to llm_candidates/."""
    LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    eid = candidate["experiment_id"]
    path = LLM_CANDIDATES_DIR / f"{eid}.json"
    path.write_text(json.dumps(candidate, indent=2, default=str), encoding="utf-8")
    return path


def _write_rejected(
    proposal: Dict[str, Any], reason: str, llm_response: Optional[str] = None
) -> Path:
    """Write a rejected proposal to proposals/rejected_*.json."""
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    eid = proposal.get("experiment_id", "unknown")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = PROPOSALS_DIR / f"rejected_{eid}_{ts}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "experiment_id": eid,
        "reason": reason,
        "proposal": proposal,
        "llm_response_raw": llm_response,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main generation flow
# ---------------------------------------------------------------------------


def generate_candidate(
    *,
    experiment_id: Optional[str] = None,
    parent_id: str = "channel_breakout_v2_1_balanced",
    evaluate_after: bool = False,
    dry_run: bool = False,
    context_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full candidate generation flow.

    Returns a result dict with keys:
        - status: "accepted" | "rejected" | "error" | "dry_run"
        - experiment_id: str
        - candidate_path: Path or None
        - errors: list[str]
        - llm_response: str or None
    """
    # --- Assign ID ---
    if experiment_id is None:
        experiment_id = _get_next_experiment_id()

    result: Dict[str, Any] = {
        "status": "error",
        "experiment_id": experiment_id,
        "candidate_path": None,
        "errors": [],
        "llm_response": None,
    }

    print(f"\n{'=' * 60}")
    print("  LLM Candidate Generator v0.4")
    print(f"  Experiment ID: {experiment_id}")
    print(f"{'=' * 60}")

    # --- Build context ---
    print("\n[Step 1] Building context ...")
    if context_override:
        context = context_override
        print("  Using provided context")
    else:
        context = build_context(experiment_id, parent_id)

    if dry_run:
        print("  (dry-run: printing context, skipping LLM call)")
        print(f"\n{'=' * 60}")
        print("  CONTEXT (sent to LLM):")
        print(f"{'=' * 60}")
        print(context[:2000] + ("\n  ... (truncated)" if len(context) > 2000 else ""))
        print(f"\n{'=' * 60}")
        print("  Expected output: valid candidate JSON with experiment_id injection")
        print(f"{'=' * 60}")
        result["status"] = "dry_run"
        return result

    # --- Call LLM ---
    print("\n[Step 2] Calling LLM ...")
    system_prompt = (
        "You are a quantitative strategy research assistant. "
        "Output ONLY valid JSON. No markdown, no explanations."
    )
    try:
        response = call_llm(system_prompt=system_prompt, user_message=context)
        result["llm_response"] = response
    except Exception as e:
        print(f"  [ERROR] LLM call failed: {e}")
        result["status"] = "error"
        result["errors"] = [str(e)]
        return result

    print(f"  Response length: {len(response)} chars")

    # --- Parse LLM response ---
    print("\n[Step 3] Parsing LLM response ...")
    proposal = _parse_llm_response(response)
    if proposal is None:
        print("  [FAIL] Could not parse JSON from LLM response")
        print(f"  First 500 chars: {response[:500]}")
        result["status"] = "rejected"
        result["errors"].append("Non-JSON response from LLM")

        # Write raw response as rejected proposal
        rejected_path = _write_rejected(
            {"experiment_id": experiment_id},
            "Non-JSON response from LLM",
            llm_response=response,
        )
        print(f"  Rejected proposal: {rejected_path}")
        return result

    if "error" in proposal:
        print(f"  LLM returned error: {proposal['error']}")
        result["status"] = "rejected"
        result["errors"].append(proposal["error"])
        rejected_path = _write_rejected(proposal, f"LLM error: {proposal['error']}")
        print(f"  Rejected proposal: {rejected_path}")
        return result

    print("  [OK] JSON parsed")

    # --- Build full candidate ---
    print("\n[Step 4] Building candidate (injecting runner fields) ...")
    candidate = _build_full_candidate(experiment_id, proposal)
    print(f"  Description: {candidate.get('description', '?')}")
    print(f"  Hypothesis: {candidate.get('hypothesis', '?')[:80]}...")

    # --- Validate ---
    print("\n[Step 5] Validating candidate ...")
    validation_errors = validate_candidate(
        candidate,
        current_path=None,
        check_uniqueness=True,
    )
    if validation_errors:
        print(f"  [FAIL] {len(validation_errors)} validation error(s):")
        for e in validation_errors:
            print(f"    - {e}")
        result["status"] = "rejected"
        result["errors"] = validation_errors
        rejected_path = _write_rejected(
            candidate,
            "; ".join(validation_errors),
            llm_response=response,
        )
        print(f"  Rejected proposal: {rejected_path}")
        return result

    print("  [OK] Schema valid")

    # --- Write candidate ---
    print("\n[Step 6] Writing candidate ...")
    candidate_path = _write_candidate(candidate)
    print(f"  [OK] Candidate written: {candidate_path}")
    result["status"] = "accepted"
    result["candidate_path"] = candidate_path

    # --- Optional: evaluate after generate ---
    if evaluate_after:
        print("\n[Step 7] Evaluating candidate (--evaluate-after-generate) ...")
        try:
            from scripts.build_candidate_from_spec import _run_oracle

            or_result = _run_oracle(candidate_path)
            status = or_result.get("flags", {}).get("status", "?")
            print(f"  Oracle status: {status}")
        except Exception as e:
            print(f"  [WARN] Oracle evaluation failed: {e}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="LLM Candidate Generator v0.4")
    parser.add_argument(
        "--experiment-id",
        type=str,
        default=None,
        help="Force a specific experiment ID (default: auto-assign)",
    )
    parser.add_argument(
        "--parent-id",
        type=str,
        default="channel_breakout_v2_1_balanced",
        help="Parent experiment ID (default: baseline)",
    )
    parser.add_argument(
        "--evaluate-after-generate",
        action="store_true",
        help="Run oracle evaluation after candidate is accepted (default: off)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print context and planned actions, skip LLM call and writes",
    )
    parser.add_argument(
        "--context-file",
        type=str,
        default=None,
        help="Use custom context from file instead of auto-building",
    )
    args = parser.parse_args()

    # Load custom context if provided
    context_override = None
    if args.context_file:
        cf_path = Path(args.context_file)
        if not cf_path.exists():
            print(f"ERROR: Context file not found: {cf_path}")
            sys.exit(3)
        context_override = cf_path.read_text(encoding="utf-8")

    result = generate_candidate(
        experiment_id=args.experiment_id,
        parent_id=args.parent_id,
        evaluate_after=args.evaluate_after_generate,
        dry_run=args.dry_run,
        context_override=context_override,
    )

    # Print summary
    print(f"\n{'=' * 60}")
    if result["status"] == "accepted":
        print("  [OK] Candidate accepted:")
        print(f"    ID:      {result['experiment_id']}")
        print(f"    Path:    {result['candidate_path']}")
        sys.exit(0)
    elif result["status"] == "rejected":
        print("  [FAIL] Candidate rejected:")
        for e in result["errors"]:
            print(f"    - {e}")
        sys.exit(1)
    elif result["status"] == "dry_run":
        print("  [OK] Dry-run complete.")
        sys.exit(0)
    else:
        print(f"  [ERROR] {result['errors']}")
        sys.exit(2)


if __name__ == "__main__":
    main()
