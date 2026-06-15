#!/usr/bin/env python3
"""LLM Result Reviewer v0.5 — proposes next action from scorecard + evaluation state.

The LLM proposes an action. The runner validates it against hard rules.
Actions are NOT executed — they land in ``proposals/actions/`` for human
or downstream agent review.

Usage:
    # Review the latest evaluated candidate
    uv run python scripts/review_candidate.py --latest

    # Review a specific candidate by experiment_id
    uv run python scripts/review_candidate.py --experiment-id exp_0002

    # Review from a candidate spec (matches by hash)
    uv run python scripts/review_candidate.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json

    # Dry-run (show context, skip LLM)
    uv run python scripts/review_candidate.py --latest --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from scripts.llm_client import call_llm
from scripts.validate_candidate_v02 import FORBIDDEN_PATTERNS

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROMPT_PATH = PROJECT_DIR / "research_agents" / "prompts" / "result_reviewer.md"
LLM_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"
LLM_RESULTS_TSV = PROJECT_DIR / "research_workspace" / "llm_results.tsv"
ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "actions"
REJECTED_ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "rejected_actions"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"

# ---------------------------------------------------------------------------
# Action schema constants
# ---------------------------------------------------------------------------

ALLOWED_ACTIONS = frozenset({"kill", "fork", "create", "stable", "promote_review"})

ALLOWED_CHANGE_FIELDS: Dict[str, Dict[str, Any]] = {
    "entry_lookback": {"type": int, "min": 20, "max": 1000},
    "min_hold_bars": {"type": int, "min": 12, "max": 1440},
}

ALLOWED_CHANGE_NESTED: Dict[str, Dict[str, Dict[str, Any]]] = {
    "regime_filter": {
        "fast_days": {"type": int, "min": 5, "max": 200},
        "slow_days": {"type": int, "min": 10, "max": 500},
    },
}

# Verdict → allowed actions
VERDICT_ACTION_MAP: Dict[str, set] = {
    "kill": {"kill", "fork", "create"},
    "requires_2600d": {"kill", "fork", "create", "stable"},
    "blocked_missing_2600d_data": {"kill", "fork", "create", "stable"},
    "research_only_recent_regime": {"kill", "fork", "create", "stable"},
    "promote_review_pending": {"promote_review"},
    "invalid_oracle_output": {"create"},
    "invalid_candidate": {"create"},
}
DEFAULT_ALLOWED_ACTIONS = {"create"}  # fallback for unknown verdicts

# Hard-blocked fields in action proposals
FORBIDDEN_ACTION_FIELDS = {"checkpoint_path", "oracle_override", "live_config",
                            "demo_config", "skip_validation", "force_promotion",
                            "auto_approve", "experiment_id"}


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _load_prompt_template() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt template not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_recent_results(n: int = 5) -> str:
    if not LLM_RESULTS_TSV.exists():
        return "(no results yet)"
    lines = LLM_RESULTS_TSV.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 1:
        return "(no results yet)"
    header = lines[0].split("\t")
    data_lines = lines[-n:]
    summaries = []
    for line in data_lines:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        row = dict(zip(header, parts))
        eid = row.get("experiment_id", "?")
        verdict = row.get("verdict", "?")
        stage = row.get("stage", "?")
        disqual = row.get("disqualifications", "")
        warnings = row.get("warnings", "")
        desc = row.get("description", "")[:50]
        parts_clean = [eid, stage, verdict, desc]
        if disqual:
            parts_clean.append(f"X:{disqual}")
        elif warnings:
            parts_clean.append(f"W:{warnings}")
        summaries.append(" | ".join(parts_clean))
    return "\n".join(summaries)


def _promotion_gates_summary(scorecard: Dict[str, Any]) -> str:
    """Build a one-line summary of promotion gate results."""
    pg = scorecard.get("promotion_gates", {})
    if not pg:
        return "(not computed)"
    passed = pg.get("gates_passed", 0)
    total = pg.get("gates_total", 8)
    details = []
    for name, gate in pg.get("gates", {}).items():
        status = "PASS" if gate.get("pass") else "BLOCK"
        details.append(f"{name}={status}({gate.get('value', '?')})")
    return f"{passed}/{total} passed\n" + "\n".join(details)


def _format_value(val: Any) -> str:
    """Format a value for the context string."""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.4f}"
    return str(val)


def build_context(
    experiment_id: str,
    scorecard: Dict[str, Any],
    evaluation_state: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the reviewer prompt context from scorecard + evaluation state."""
    template = _load_prompt_template()
    recent = _load_recent_results()

    ms = scorecard.get("metrics_summary", {})
    v = scorecard.get("verdict", {})
    fl = scorecard.get("flags", {})
    pg_summary = _promotion_gates_summary(scorecard)

    # Determine evaluation stage
    eid = scorecard.get("experiment_id", experiment_id)
    stage = scorecard.get("stage", "1300d")
    verdict_label = v.get("label", "?")
    verdict_reason = v.get("reason", "?")

    # Check for evaluation state
    eval_stage = evaluation_state.get("state", "unknown") if evaluation_state else stage
    final_verdict = evaluation_state.get("final_verdict") if evaluation_state else verdict_label
    if final_verdict:
        verdict_label = final_verdict

    context = (
        template
        .replace("{{EXPERIMENT_ID}}", eid)
        .replace("{{DESCRIPTION}}", scorecard.get("description") or "(not set)")
        .replace("{{HYPOTHESIS}}", scorecard.get("hypothesis") or "(not set)")
        .replace("{{EVALUATION_STAGE}}", eval_stage)
        .replace("{{VERDICT}}", verdict_label)
        .replace("{{VERDICT_REASON}}", verdict_reason)
        .replace("{{IS_RETURN}}", _format_value(ms.get("is_return")))
        .replace("{{IS_DD}}", _format_value(ms.get("is_dd")))
        .replace("{{IS_SHARPE}}", _format_value(ms.get("is_sharpe")))
        .replace("{{OOS_SAFE_RETURN}}", _format_value(ms.get("oos_safe_return")))
        .replace("{{OOS_DD}}", _format_value(ms.get("oos_dd")))
        .replace("{{ROLLING_12M}}", _format_value(ms.get("rolling_12m_min_return")))
        .replace("{{TRADES_PER_YEAR}}", _format_value(ms.get("trades_per_year")))
        .replace("{{CORR_BASELINE}}", _format_value(ms.get("corr_vs_baseline")))
        .replace("{{FEE_10BP}}", _format_value(ms.get("fee_10bp_return")))
        .replace("{{WARNINGS}}", ", ".join(fl.get("warnings", [])) or "(none)")
        .replace("{{DISQUALIFICATIONS}}", ", ".join(fl.get("disqualifications", [])) or "(none)")
        .replace("{{PROMOTION_GATES}}", pg_summary)
        .replace("{{RECENT_RESULTS}}", recent)
    )
    return context


# ---------------------------------------------------------------------------
# Scorecard / state loading
# ---------------------------------------------------------------------------


def _load_scorecard_and_state(
    experiment_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Load scorecard and optional evaluation state for an experiment.

    Returns (scorecard, evaluation_state_or_None).
    """
    # Try evaluation state first (v0.3+)
    state_path = LLM_SCORECARDS_DIR / f"{experiment_id}_evaluation.json"
    evaluation_state = None
    if state_path.exists():
        with open(state_path, "r", encoding="utf-8") as fh:
            evaluation_state = json.load(fh)

    # Try loading scorecard by stage
    scorecard = None
    for stage in ("2600d", "1300d"):
        sc_path = LLM_SCORECARDS_DIR / f"{experiment_id}_{stage}_scorecard.json"
        if sc_path.exists():
            with open(sc_path, "r", encoding="utf-8") as fh:
                scorecard = json.load(fh)
            break

    # Fallback: old v0.1 scorecard format
    if scorecard is None:
        old_path = LLM_SCORECARDS_DIR / f"{experiment_id}_scorecard.json"
        if old_path.exists():
            with open(old_path, "r", encoding="utf-8") as fh:
                scorecard = json.load(fh)

    # Build minimal scorecard from evaluation state when no scorecard found
    if scorecard is None and evaluation_state is not None:
        scorecard = _scorecard_from_evaluation_state(evaluation_state)

    return scorecard, evaluation_state


def _scorecard_from_evaluation_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build scorecard from evaluation state, reading oracle scorecard if available.

    The evaluation state's history references an oracle run. The actual
    scorecard is stored under the oracle ID, not the candidate's exp_NNNN ID.
    This function finds and loads it, falling back to a minimal record only
    when the oracle scorecard cannot be found.
    """
    # Try to find the oracle scorecard from history
    history = state.get("history", [])
    for entry in history:
        oracle_id = entry.get("oracle_id")
        stage = entry.get("stage", "1300d")
        if oracle_id:
            # Oracle scorecards are named oracle_*_scorecard.json, not exp_*_scorecard.json
            sc_path = LLM_SCORECARDS_DIR / f"{oracle_id}_{stage}_scorecard.json"
            if sc_path.exists():
                try:
                    return json.loads(sc_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, IOError):
                    pass

            # Fallback: try with just stage (some scorecards omit stage in filename)
            sc_path2 = LLM_SCORECARDS_DIR / f"{oracle_id}_scorecard.json"
            if sc_path2.exists():
                try:
                    return json.loads(sc_path2.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, IOError):
                    pass

    # No oracle scorecard found — build minimal record
    return {
        "experiment_id": state.get("experiment_id", "?"),
        "stage": state.get("state", "unknown"),
        "verdict": {
            "label": state.get("final_verdict", "?"),
            "reason": state.get("error", "From evaluation state only"),
        },
        "flags": {"warnings": [], "disqualifications": []},
        "metrics_summary": {},
        "promotion_gates": {},
    }


def _find_latest_experiment_id() -> Optional[str]:
    """Find the most recent experiment with a scorecard."""
    import os
    scores = []
    if LLM_SCORECARDS_DIR.exists():
        for f in LLM_SCORECARDS_DIR.iterdir():
            if f.suffix != ".json":
                continue
            if "_scorecard" not in f.name and "_evaluation" not in f.name:
                continue
            m = re.match(r"(exp_\d+)_", f.stem)
            if m:
                scores.append((f.stat().st_mtime, m.group(1)))
    if scores:
        scores.sort(reverse=True)
        return scores[0][1]
    return None


def _find_from_candidate_file(candidate_path_str: str) -> Optional[str]:
    """Find experiment_id from a candidate spec file path or its content."""
    cpath = Path(candidate_path_str)
    if not cpath.exists():
        return None
    try:
        spec = json.loads(cpath.read_text(encoding="utf-8"))
        return spec.get("experiment_id")
    except (json.JSONDecodeError, IOError):
        return cpath.stem


# ---------------------------------------------------------------------------
# Action validation
# ---------------------------------------------------------------------------


def validate_action(
    action: Dict[str, Any],
    verdict_label: str,
) -> List[str]:
    """Validate a proposed action against v0.5 schema + hard rules.

    Returns list of error messages (empty = valid).
    """
    errors: List[str] = []

    # --- Check for forbidden fields ---
    for key in action:
        if key in FORBIDDEN_ACTION_FIELDS:
            errors.append(f"Forbidden field: '{key}'")

    # --- action type ---
    action_type = action.get("action")
    if not action_type:
        errors.append("Missing 'action' field")
        return errors
    if action_type not in ALLOWED_ACTIONS:
        errors.append(f"Invalid action '{action_type}': must be one of {sorted(ALLOWED_ACTIONS)}")
        return errors

    # --- Verdict-aware constraint check ---
    allowed_for_verdict = VERDICT_ACTION_MAP.get(verdict_label, DEFAULT_ALLOWED_ACTIONS)
    if action_type not in allowed_for_verdict:
        errors.append(
            f"Action '{action_type}' not allowed for verdict '{verdict_label}'. "
            f"Allowed: {sorted(allowed_for_verdict)}"
        )

    # --- source_candidate_id ---
    source = action.get("source_candidate_id")
    if not source:
        errors.append("Missing 'source_candidate_id'")
    elif not re.match(r"^(exp_\d{4,}|baseline)$", str(source)):
        errors.append(f"Invalid source_candidate_id '{source}': must match exp_NNNN or 'baseline'")

    # --- target_family ---
    family = action.get("target_family")
    if not family:
        errors.append("Missing 'target_family'")
    else:
        from scripts.family_registry import is_valid as _family_valid
        if not _family_valid(family):
            errors.append(f"Invalid target_family '{family}': unknown family")

    # --- rationale ---
    rationale = action.get("rationale", "")
    if not rationale or len(rationale.strip()) < 30:
        errors.append(f"Rationale too short ({len(rationale.strip())} chars, min 30)")

    # --- allowed_change (for fork actions) ---
    if action_type == "fork":
        ac = action.get("allowed_change")
        if not ac:
            errors.append("fork action requires 'allowed_change'")
        elif not isinstance(ac, dict):
            errors.append("'allowed_change' must be a dict")
        else:
            errors.extend(_validate_allowed_change(ac, family=family))

    # --- risk_note (optional, no validation needed) ---

    # --- Forbidden content scanning ---
    raw = json.dumps(action, default=str)
    for pattern, reason in FORBIDDEN_PATTERNS:
        if re.search(pattern, raw, re.IGNORECASE):
            errors.append(f"Forbidden content detected ({reason})")

    return errors


def _validate_allowed_change(
    ac: Dict[str, Any],
    family: str = "channel_breakout",
) -> List[str]:
    """Validate the allowed_change section of a fork action.

    Uses the family registry for family-specific allowed_change specs.
    Falls back to hardcoded values for backward compatibility.
    """
    # Try family registry first
    from scripts.family_registry import validate_allowed_change as _registry_validate
    reg_errors = _registry_validate(family, ac)
    if reg_errors:
        return reg_errors

    # Also run hardcoded checks as belt-and-suspenders
    errors: List[str] = []
    for key, value in ac.items():
        if key in ALLOWED_CHANGE_NESTED:
            if not isinstance(value, dict):
                errors.append(f"allowed_change.{key}: expected dict, got {type(value).__name__}")
                continue
            nested_spec = ALLOWED_CHANGE_NESTED[key]
            for nk, nv in value.items():
                if nk not in nested_spec:
                    errors.append(f"allowed_change.{key}.{nk}: unknown field")
                    continue
                spec = nested_spec[nk]
                if not isinstance(nv, spec["type"]):
                    errors.append(f"allowed_change.{key}.{nk}: expected {spec['type'].__name__}")
                elif nv < spec["min"] or nv > spec["max"]:
                    errors.append(
                        f"allowed_change.{key}.{nk}: {nv} out of range [{spec['min']}, {spec['max']}]"
                    )
        elif key in ALLOWED_CHANGE_FIELDS:
            spec = ALLOWED_CHANGE_FIELDS[key]
            if not isinstance(value, spec["type"]):
                errors.append(f"allowed_change.{key}: expected {spec['type'].__name__}")
            elif value < spec["min"] or value > spec["max"]:
                errors.append(
                    f"allowed_change.{key}: {value} out of range [{spec['min']}, {spec['max']}]"
                )
        elif key not in [k for k in ac if k in ALLOWED_CHANGE_FIELDS or k in ALLOWED_CHANGE_NESTED]:
            pass  # already handled by registry
    return errors


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _write_action(action: Dict[str, Any], experiment_id: str) -> Path:
    """Write a valid action proposal."""
    ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    action_type = action.get("action", "unknown")
    path = ACTIONS_DIR / f"action_{experiment_id}_{action_type}_{ts}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "experiment_id": experiment_id,
        "action": action,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _write_rejected_action(
    action: Dict[str, Any],
    experiment_id: str,
    errors: List[str],
    llm_response: Optional[str] = None,
) -> Path:
    """Write a rejected action proposal."""
    REJECTED_ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    action_type = action.get("action", "unknown")
    path = REJECTED_ACTIONS_DIR / f"rejected_action_{experiment_id}_{action_type}_{ts}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "experiment_id": experiment_id,
        "action": action,
        "errors": errors,
        "llm_response_raw": llm_response,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main review flow
# ---------------------------------------------------------------------------


def review_candidate(
    experiment_id: str,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run the full review flow for a candidate.

    Returns a result dict with status, action info, etc.
    """
    result: Dict[str, Any] = {
        "status": "error",
        "experiment_id": experiment_id,
        "action": None,
        "errors": [],
        "llm_response": None,
        "action_path": None,
    }

    print(f"\n{'='*60}")
    print(f"  LLM Result Reviewer v0.5")
    print(f"  Experiment: {experiment_id}")
    print(f"{'='*60}")

    # --- Load scorecard + evaluation state ---
    print("\n[Step 1] Loading scorecard ...")
    scorecard, evaluation_state = _load_scorecard_and_state(experiment_id)
    if scorecard is None:
        print(f"  [ERROR] No scorecard found for {experiment_id}")
        result["errors"].append("Scorecard not found")
        return result

    # Determine verdict
    verdict_label = scorecard.get("verdict", {}).get("label", "?")
    if evaluation_state and evaluation_state.get("final_verdict"):
        verdict_label = evaluation_state["final_verdict"]
    # Also check from state
    eval_stage = evaluation_state.get("state", scorecard.get("stage", "1300d")) \
        if evaluation_state else scorecard.get("stage", "1300d")
    print(f"  Stage: {eval_stage}   Verdict: {verdict_label}")

    # --- Build context ---
    print("\n[Step 2] Building context ...")
    context = build_context(experiment_id, scorecard, evaluation_state)

    if dry_run:
        print("  (dry-run: printing context, skipping LLM call)")
        print(f"\n{'='*60}")
        print("  CONTEXT:")
        print(f"{'='*60}")
        print(context[:2000] + ("\n  ... (truncated)" if len(context) > 2000 else ""))
        print(f"\n{'='*60}")
        result["status"] = "dry_run"
        return result

    # --- Call LLM ---
    print("\n[Step 3] Calling LLM ...")
    system_prompt = (
        "You are a quantitative strategy research reviewer. "
        "Output ONLY valid JSON. No markdown, no explanations."
    )
    try:
        response = call_llm(system_prompt=system_prompt, user_message=context)
        result["llm_response"] = response
    except Exception as e:
        print(f"  [ERROR] LLM call failed: {e}")
        result["errors"].append(str(e))
        return result
    print(f"  Response length: {len(response)} chars")

    # --- Parse LLM response ---
    print("\n[Step 4] Parsing action proposal ...")
    action = _parse_action_response(response)
    if action is None:
        print("  [FAIL] Could not parse JSON from LLM response")
        result["status"] = "rejected"
        result["errors"].append("Non-JSON response from LLM")
        rejected_path = _write_rejected_action(
            {"action": "parse_error"}, experiment_id,
            ["Non-JSON response"], llm_response=response,
        )
        print(f"  Rejected action: {rejected_path}")
        return result

    # --- Force source_candidate_id to the original experiment_id ---
    # The LLM may see the oracle scorecard ID and use it, but actions
    # must reference the candidate ID (exp_NNNN), not the oracle run.
    action["source_candidate_id"] = experiment_id

    proposed_action = action.get("action", "?")
    print(f"  Proposed action: {proposed_action}")
    result["action"] = action

    # --- Validate action ---
    print("\n[Step 5] Validating action ...")
    validation_errors = validate_action(action, verdict_label)
    if validation_errors:
        print(f"  [FAIL] {len(validation_errors)} error(s):")
        for e in validation_errors:
            print(f"    - {e}")
        result["status"] = "rejected"
        result["errors"] = validation_errors
        rejected_path = _write_rejected_action(
            action, experiment_id, validation_errors, llm_response=response,
        )
        print(f"  Rejected action: {rejected_path}")
        return result

    print("  [OK] Action valid")

    # --- Write action ---
    print("\n[Step 6] Writing action proposal ...")
    action_path = _write_action(action, experiment_id)
    print(f"  [OK] Action written: {action_path}")
    result["status"] = "accepted"
    result["action_path"] = action_path

    return result


def _parse_action_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response into an action dict.

    Handles pure JSON, JSON in markdown fences, error objects.
    """
    text = response_text.strip()

    # Markdown code fences
    json_match = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL
    )
    if json_match:
        text = json_match.group(1).strip()

    # Direct parse
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                parsed = json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None

    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="LLM Result Reviewer v0.5 — action proposal from scorecard + evaluation state"
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--latest", action="store_true",
        help="Review the most recently evaluated candidate",
    )
    input_group.add_argument(
        "--experiment-id", type=str, default=None,
        help="Review a specific candidate by experiment_id",
    )
    input_group.add_argument(
        "--candidate", type=str, default=None,
        help="Review by matching candidate spec file",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show context, skip LLM call and writes",
    )

    args = parser.parse_args()

    # Determine experiment_id
    if args.latest:
        eid = _find_latest_experiment_id()
        if eid is None:
            print("ERROR: No evaluated candidates found")
            sys.exit(1)
    elif args.experiment_id:
        eid = args.experiment_id
    elif args.candidate:
        eid = _find_from_candidate_file(args.candidate)
        if eid is None:
            print(f"ERROR: Could not determine experiment_id from candidate file")
            sys.exit(1)

    result = review_candidate(eid, dry_run=args.dry_run)

    print(f"\n{'='*60}")
    if result["status"] == "accepted":
        print(f"  [OK] Action accepted: {result['action'].get('action', '?')}")
        print(f"  Path: {result['action_path']}")
        sys.exit(0)
    elif result["status"] == "rejected":
        print(f"  [FAIL] Action rejected:")
        for e in result["errors"]:
            print(f"    - {e}")
        sys.exit(1)
    elif result["status"] == "dry_run":
        print(f"  [OK] Dry-run complete.")
        sys.exit(0)
    else:
        print(f"  [ERROR] {result['errors']}")
        sys.exit(2)


if __name__ == "__main__":
    main()
