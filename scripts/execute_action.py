#!/usr/bin/env python3
"""Action Executor v0.6 — deterministic execution of validated action proposals.

Reads action proposals from ``proposals/actions/action_*.json`` and performs
the corresponding file-system operations:

    kill            → candidate_states/exp_NNNN_killed.json
    fork            → llm_candidates/exp_NNNN.json (derived from source)
    create          → proposals/create_requests/request_*.json
    stable          → candidate_states/exp_NNNN_stable.json
    promote_review  → proposals/promotion_reviews/review_*.json

Usage:
    # Execute the latest action in proposals/actions/
    uv run python scripts/execute_action.py --latest

    # Execute a specific action file
    uv run python scripts/execute_action.py \\
        --action research_workspace/proposals/actions/action_*.json

    # Evaluate after fork/create
    uv run python scripts/execute_action.py --latest --evaluate-after-execute

    # Dry-run (show what would happen, no writes)
    uv run python scripts/execute_action.py --latest --dry-run
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "actions"
CANDIDATE_STATES_DIR = PROJECT_DIR / "research_workspace" / "candidate_states"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
LEGACY_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "candidates"
PROMOTION_REVIEWS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "promotion_reviews"
CREATE_REQUESTS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "create_requests"
LLM_RESULTS_TSV = PROJECT_DIR / "research_workspace" / "llm_results.tsv"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONSECUTIVE_STABLE = 2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_experiment_id() -> str:
    """Find next available exp_NNNN ID across all candidate directories."""
    seen = set()
    for d in [LLM_CANDIDATES_DIR, LEGACY_CANDIDATES_DIR]:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix == ".json":
                m = re.match(r"exp_(\d+)", f.stem)
                if m:
                    seen.add(int(m.group(1)))
    if not seen:
        return "exp_0018"
    return f"exp_{max(seen) + 1:04d}"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None


def _find_source_candidate(source_id: str) -> Optional[Dict[str, Any]]:
    """Find a candidate spec by experiment_id."""
    if source_id == "baseline":
        return None
    # Check llm_candidates first, then legacy candidates
    for d in [LLM_CANDIDATES_DIR, LEGACY_CANDIDATES_DIR]:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.suffix != ".json":
                continue
            try:
                spec = json.loads(f.read_text(encoding="utf-8"))
                if spec.get("experiment_id") == source_id:
                    return spec
            except (json.JSONDecodeError, IOError):
                continue
    return None


# ---------------------------------------------------------------------------
# Re-validation (belt-and-suspenders before executing)
# ---------------------------------------------------------------------------


def _revalidate_action(action: Dict[str, Any]) -> List[str]:
    """Re-validate an action before executing (safety check).

    Returns errors (empty = valid).
    """
    errors: List[str] = []
    act = action.get("action")
    if act not in ("kill", "fork", "create", "stable", "promote_review"):
        errors.append(f"Unknown action type: {act}")

    sid = action.get("source_candidate_id")
    if not sid or not isinstance(sid, str):
        errors.append("Missing or invalid source_candidate_id")
    elif sid != "baseline" and not re.match(r"^(exp_\d{4,}|oracle_\w+)$", sid):
        errors.append(f"Invalid source_candidate_id: {sid}")

    rationale = action.get("rationale", "")
    if not rationale or len(rationale.strip()) < 20:
        errors.append(f"Rationale too short ({len(rationale.strip())} chars, min 20)")

    return errors


# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------


def execute_kill(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a kill action: write state file, do NOT delete candidate."""
    sid = action["source_candidate_id"]
    rationale = action.get("rationale", "")
    risk_note = action.get("risk_note", "")

    CANDIDATE_STATES_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "timestamp": _now_iso(),
        "experiment_id": sid,
        "action": "kill",
        "rationale": rationale,
        "risk_note": risk_note,
        "source_action": action,
    }
    path = CANDIDATE_STATES_DIR / f"{sid}_killed.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    return {
        "status": "executed",
        "action": "kill",
        "experiment_id": sid,
        "state_path": str(path),
        "candidate_deleted": False,
    }


def execute_fork(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a fork action: derive new candidate from source + allowed_change."""
    sid = action["source_candidate_id"]
    rationale = action.get("rationale", "")
    allowed_change = action.get("allowed_change", {})

    # Find source candidate
    source = _find_source_candidate(sid)
    if source is None:
        return {"status": "error", "action": "fork", "error": f"Source candidate {sid} not found"}

    # Deep copy the source candidate params
    import copy

    new_params = copy.deepcopy(source.get("params", {}))

    # Apply allowed_change
    for key, value in allowed_change.items():
        if key == "regime_filter":
            if isinstance(value, dict):
                for nk, nv in value.items():
                    if nk in ("fast_days", "slow_days"):
                        new_params.setdefault("regime_filter", {})[nk] = nv
                    elif nk in new_params.get("regime_filter", {}):
                        new_params["regime_filter"][nk] = nv
        elif key == "entry_lookback":
            for regime in ("bull", "bear", "neutral"):
                if regime in new_params:
                    new_params[regime].setdefault("strategy_params", {})["entry_lookback"] = value
        elif key == "min_hold_bars":
            for regime in ("bull", "bear", "neutral"):
                if regime in new_params:
                    new_params[regime].setdefault("strategy_params", {})["min_hold_bars"] = value
        else:
            # Could be a permission field — try applying to all regimes
            for regime in ("bull", "bear", "neutral"):
                if regime in new_params:
                    if key in ("enable_long", "enable_short"):
                        new_params[regime].setdefault("strategy_params", {})[key] = value
                    if key in new_params[regime].get("permission", {}):
                        new_params[regime]["permission"][key] = value

    # Assign new ID
    new_id = _next_experiment_id()
    new_desc = f"Fork from {sid}: {source.get('description', '')}"

    # Preserve family from source
    source_family = source.get("strategy", "channel_breakout")
    source_base = source.get("base", "v2.1_balanced")

    # Apply family-specific nested changes (volatility_filter, exit_logic, etc.)
    # Uses the family registry to find which fields are nested dicts
    from scripts.family_registry import get as _get_family_reg

    _fd = _get_family_reg(source_family)
    if _fd:
        for nested_key in _fd.allowed_change_nested:
            if nested_key == "regime_filter":
                continue  # handled above
            if nested_key in allowed_change and isinstance(allowed_change[nested_key], dict):
                new_params.setdefault(nested_key, {}).update(allowed_change[nested_key])

    # Build the new candidate spec
    new_candidate = {
        "experiment_id": new_id,
        "parent_id": sid,
        "candidate_role": "standalone",
        "strategy": source_family,
        "base": source_base,
        "status": "research_only",
        "description": new_desc.strip(),
        "hypothesis": f"Fork from {sid}: {rationale[:200]}",
        "expected_behavior_change": f"Fork adjusting: {', '.join(allowed_change.keys())}. See parent {sid} for baseline.",
        "constraints": ["no_future_data", "inherits_v21_risk", "no_demo_routing", "research_only"],
        "params": new_params,
    }

    # Validate via family-aware v0.8 schema
    from scripts.validate_candidate_v08 import validate_candidate as _validate

    errors = _validate(new_candidate, current_path=None, check_uniqueness=False)
    if errors:
        return {"status": "error", "action": "fork", "errors": errors, "candidate": new_candidate}

    # Write candidate
    LLM_CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = LLM_CANDIDATES_DIR / f"{new_id}.json"
    path.write_text(json.dumps(new_candidate, indent=2, default=str), encoding="utf-8")

    return {
        "status": "executed",
        "action": "fork",
        "experiment_id": new_id,
        "candidate_path": str(path),
        "parent_id": sid,
    }


def execute_create(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a create action: write a create_request for the generator.

    Does NOT call the LLM directly — the generator picks up create_requests.
    """
    rationale = action.get("rationale", "")
    risk_note = action.get("risk_note", "")

    CREATE_REQUESTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    request = {
        "timestamp": _now_iso(),
        "action_source": action.get("source_candidate_id", "baseline"),
        "rationale": rationale,
        "risk_note": risk_note,
    }
    path = CREATE_REQUESTS_DIR / f"create_request_{ts}.json"
    path.write_text(json.dumps(request, indent=2, default=str), encoding="utf-8")

    return {
        "status": "executed",
        "action": "create",
        "create_request_path": str(path),
        "note": "Create request written. Run generate_candidate.py to fulfill.",
    }


def _count_consecutive_stable(sid: str) -> int:
    """Count how many consecutive stable actions have been recorded for a candidate."""
    if not CANDIDATE_STATES_DIR.exists():
        return 0
    states = sorted(CANDIDATE_STATES_DIR.iterdir(), reverse=True)
    count = 0
    for f in states:
        if f.suffix != ".json":
            continue
        if sid not in f.name:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("action") == "stable":
                count += 1
            else:
                break  # non-stable action breaks the chain
        except (json.JSONDecodeError, IOError):
            continue
    return count


def execute_stable(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a stable action: write note with consecutive limit check."""
    sid = action["source_candidate_id"]
    rationale = action.get("rationale", "")

    # Check consecutive limit
    consecutive = _count_consecutive_stable(sid)
    if consecutive >= MAX_CONSECUTIVE_STABLE:
        return {
            "status": "rejected",
            "action": "stable",
            "error": f"Max consecutive stable ({MAX_CONSECUTIVE_STABLE}) exceeded for {sid}. "
            f"Consider fork or create instead.",
            "consecutive_stable": consecutive + 1,
        }

    CANDIDATE_STATES_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "timestamp": _now_iso(),
        "experiment_id": sid,
        "action": "stable",
        "rationale": rationale,
        "consecutive_stable": consecutive + 1,
    }
    path = CANDIDATE_STATES_DIR / f"{sid}_stable_{consecutive + 1}.json"
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    return {
        "status": "executed",
        "action": "stable",
        "experiment_id": sid,
        "state_path": str(path),
        "consecutive_stable": consecutive + 1,
    }


def execute_promote_review(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a promote_review action: generate promotion review packet draft."""
    sid = action["source_candidate_id"]
    rationale = action.get("rationale", "")
    risk_note = action.get("risk_note", "")

    # Gather data for review packet
    candidate = _find_source_candidate(sid)
    scorecard = _load_scorecard(sid)

    PROMOTION_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)

    packet = {
        "timestamp": _now_iso(),
        "candidate_id": sid,
        "rationale": rationale,
        "risk_note": risk_note,
        "candidate_spec": candidate,
        "scorecard": scorecard,
        "promotion_checklist": {
            "1_oracle_pass": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("oracle_pass", {})
            .get("pass")
            if scorecard
            else None,
            "2_execution_parity": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("execution_parity", {})
            .get("pass")
            if scorecard
            else None,
            "3_rolling_12m_positive": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("rolling_12m_return", {})
            .get("pass")
            if scorecard
            else None,
            "4_is_drawdown_above_40": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("is_drawdown", {})
            .get("pass")
            if scorecard
            else None,
            "5_correlation_below_095": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("correlation_vs_baseline", {})
            .get("pass")
            if scorecard
            else None,
            "6_fee_robustness": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("fee_robustness", {})
            .get("pass")
            if scorecard
            else None,
            "7_trade_count_20_yr": scorecard.get("promotion_gates", {})
            .get("gates", {})
            .get("trade_count", {})
            .get("pass")
            if scorecard
            else None,
        },
        "status": "draft",
        "requires_human_approval": True,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = PROMOTION_REVIEWS_DIR / f"promotion_review_{sid}_{ts}.json"
    path.write_text(json.dumps(packet, indent=2, default=str), encoding="utf-8")

    return {
        "status": "executed",
        "action": "promote_review",
        "experiment_id": sid,
        "packet_path": str(path),
        "note": "Promotion review packet created. Human review required before baseline change.",
    }


def _load_scorecard(experiment_id: str) -> Optional[Dict[str, Any]]:
    """Load scorecard for a candidate (try 2600d first, then 1300d, then old format)."""
    from scripts.score_candidate import LLM_SCORECARDS_DIR

    sc_dir = LLM_SCORECARDS_DIR
    for stage in ("2600d", "1300d"):
        p = sc_dir / f"{experiment_id}_{stage}_scorecard.json"
        if p.exists():
            return _load_json(p)
    p = sc_dir / f"{experiment_id}_scorecard.json"
    if p.exists():
        return _load_json(p)
    return None


# ---------------------------------------------------------------------------
# Action loader
# ---------------------------------------------------------------------------


def _find_latest_action() -> Optional[Tuple[Path, Dict[str, Any]]]:
    """Find the most recent action file in proposals/actions/."""
    if not ACTIONS_DIR.exists():
        return None
    actions = sorted(ACTIONS_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in actions:
        if f.suffix != ".json":
            continue
        data = _load_json(f)
        if data and "action" in data.get("action", {}):
            return f, data["action"]
    return None


def execute_action_from_path(action_path: Path) -> Dict[str, Any]:
    """Load and execute an action from a file path."""
    payload = _load_json(action_path)
    if payload is None:
        return {"status": "error", "error": f"Cannot read action file: {action_path}"}

    action = payload.get("action", {})
    if not action:
        return {"status": "error", "error": "Action file missing 'action' field"}

    return execute_action(action)


def execute_action(action: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a validated action proposal.

    Re-validates before executing (belt-and-suspenders).
    """
    # Re-validate
    errors = _revalidate_action(action)
    if errors:
        return {"status": "error", "action": action.get("action"), "errors": errors}

    act = action["action"]
    action_type = act

    if action_type == "kill":
        return execute_kill(action)
    elif action_type == "fork":
        return execute_fork(action)
    elif action_type == "create":
        return execute_create(action)
    elif action_type == "stable":
        return execute_stable(action)
    elif action_type == "promote_review":
        return execute_promote_review(action)
    else:
        return {
            "status": "error",
            "action": action_type,
            "error": f"Unknown action type: {action_type}",
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_result(result: Dict[str, Any]) -> None:
    """Print execution result in a readable format."""
    print(f"\n{'=' * 60}")
    status = result.get("status", "?")
    act = result.get("action", "?")
    print(f"  Action: {act}")
    print(f"  Status: {status}")

    if status == "executed":
        eid = result.get("experiment_id", "?")
        print(f"  Experiment: {eid}")
        if result.get("candidate_path"):
            print(f"  Candidate: {result['candidate_path']}")
        if result.get("state_path"):
            print(f"  State: {result['state_path']}")
        if result.get("packet_path"):
            print(f"  Review packet: {result['packet_path']}")
        if result.get("create_request_path"):
            print(f"  Create request: {result['create_request_path']}")
        if result.get("note"):
            print(f"  Note: {result['note']}")
    elif status == "error":
        for e in result.get("errors", [result.get("error", "?")]):
            print(f"  [ERROR] {e}")
    elif status == "rejected":
        print(f"  Reason: {result.get('error', '?')}")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Action Executor v0.6 — deterministic execution of validated action proposals"
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--latest",
        action="store_true",
        help="Execute the most recent action in proposals/actions/",
    )
    input_group.add_argument(
        "--action",
        type=str,
        default=None,
        help="Path to a specific action JSON file",
    )
    parser.add_argument(
        "--evaluate-after-execute",
        action="store_true",
        help="Run oracle evaluation after fork/create (default: off)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen, no writes",
    )
    args = parser.parse_args()

    # Load action
    if args.latest:
        result = _find_latest_action()
        if result is None:
            print("ERROR: No action files found in proposals/actions/")
            sys.exit(1)
        action_path, action = result
        print(f"Found action: {action_path.name}")
    elif args.action:
        action_path = Path(args.action)
        payload = _load_json(action_path)
        if payload is None:
            print(f"ERROR: Cannot load action file: {action_path}")
            sys.exit(1)
        action = payload.get("action", {})
        if not action:
            print("ERROR: Action file missing 'action' field")
            sys.exit(1)

    print(f"  Type: {action.get('action', '?')}")
    print(f"  Source: {action.get('source_candidate_id', '?')}")

    if args.dry_run:
        print("\n[Dry-run] Would execute:")
        print(f"  Action: {action.get('action')}")
        print(f"  Rationale: {action.get('rationale', '')[:120]}...")
        if action.get("allowed_change"):
            print(f"  Changes: {json.dumps(action['allowed_change'])}")
        print("  No files written.")
        sys.exit(0)

    result = execute_action(action)
    _print_result(result)

    if result["status"] == "executed":
        sys.exit(0)
    elif result["status"] == "rejected":
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
