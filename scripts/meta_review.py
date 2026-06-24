#!/usr/bin/env python3
"""Meta-Agent Review v0.7 — research process retrospective.

Reads historical run data, scorecards, action outcomes, and rejection patterns.
Produces structured findings and recommendations for improving the research loop.
Does NOT modify anything — only writes reviews to ``meta_reviews/``.

Usage:
    # Latest 20 runs
    uv run python scripts/meta_review.py --latest

    # Custom window
    uv run python scripts/meta_review.py --runs 50

    # Dry-run (print context, skip LLM)
    uv run python scripts/meta_review.py --latest --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from scripts.llm_client import call_llm

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROMPT_PATH = PROJECT_DIR / "research_agents" / "prompts" / "meta_review.md"
RESULTS_TSV = PROJECT_DIR / "research_workspace" / "llm_results.tsv"
RUNS_DIR = PROJECT_DIR / "research_workspace" / "llm_runs"
SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"
ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "actions"
REJECTED_ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "rejected_actions"
REJECTED_DIR = PROJECT_DIR / "research_workspace" / "proposals"
META_REVIEWS_DIR = PROJECT_DIR / "research_workspace" / "meta_reviews"
CONTRACT_PATH = PROJECT_DIR / "docs" / "llm_research_contract.md"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RUNS = 20

FORBIDDEN_REVIEW_TOPICS = [
    "modify the oracle",
    "modify baseline",
    "modify demo",
    "modify live",
    "bypass validation",
    "force promotion",
    "auto-promote",
    "override verdict",
    "skip schema",
    "disable guard",
    "remove constraint",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_review_id() -> str:
    seen = set()
    if META_REVIEWS_DIR.exists():
        for f in META_REVIEWS_DIR.glob("meta_*.json"):
            m = re.match(r"meta_(\d+)", f.stem)
            if m:
                seen.add(int(m.group(1)))
    next_num = max(seen) + 1 if seen else 1
    return f"meta_{next_num:04d}"


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------


def _read_results_tsv(n: int = 10) -> List[Dict[str, str]]:
    """Read last N rows from llm_results.tsv."""
    if not RESULTS_TSV.exists():
        return []
    lines = RESULTS_TSV.read_text(encoding="utf-8").strip().split("\n")
    if len(lines) <= 1:
        return []
    header = lines[0].split("\t")
    rows = []
    for line in lines[-n:]:
        parts = line.split("\t")
        if len(parts) >= len(header):
            rows.append(dict(zip(header, parts)))
    return rows


def _read_run_files(n: int = DEFAULT_RUNS) -> List[Dict[str, Any]]:
    """Read last N run records from llm_runs/."""
    if not RUNS_DIR.exists():
        return []
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    runs = []
    for f in files[:n]:
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, IOError):
            continue
    return runs


def _read_scorecard_files(n: int = 10) -> List[Dict[str, Any]]:
    """Read latest N scorecards from llm_scorecards/.

    Scorecards contain oracle flags, disqualifications, trades/year,
    and verdict — the primary quality signal for meta-review.
    """
    if not SCORECARDS_DIR.exists():
        return []
    files = sorted(
        SCORECARDS_DIR.glob("*_scorecard.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    scorecards = []
    for f in files[:n]:
        try:
            scorecards.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, IOError):
            continue
    return scorecards


def _summarize_scorecards(scorecards: List[Dict[str, Any]]) -> str:
    """Build a compact summary of scorecard signals for meta-review context.

    Prioritizes: verdict, oracle flags, disqualifications, trades/year,
    rolling min return. Relegates generic rejections to secondary status.
    """
    if not scorecards:
        return "  (no scorecards found)"

    lines = []
    for sc in scorecards:
        eid = sc.get("experiment_id", "?")
        verdict = sc.get("verdict", {})
        ms = sc.get("metrics_summary", {})
        fl = sc.get("flags", {})

        v_label = verdict.get("label", "?")
        status = fl.get("status", "?")
        disqual = fl.get("disqualifications", [])
        warnings = fl.get("warnings", [])

        is_ret = ms.get("is_return")
        oos_ret = ms.get("oos_return")
        dd = ms.get("oos_dd")
        tpy = ms.get("trades_per_year")
        r12m = ms.get("rolling_12m_min_return")
        ep = ms.get("execution_parity")
        fee10 = ms.get("fee_10bp_return")

        parts = [f"  {eid} | verdict={v_label} | status={status}"]
        if is_ret is not None:
            parts.append(f"IS={is_ret:+.4f}")
        if oos_ret is not None:
            parts.append(f"OOS={oos_ret:+.4f}")
        if dd is not None:
            parts.append(f"DD={dd:.4f}")
        if tpy is not None:
            parts.append(f"Trades/yr={tpy:.1f}")
        if r12m is not None:
            parts.append(f"Roll12m={r12m:+.4f}")
        if ep is not None:
            parts.append(f"ExecParity={ep:.2f}")
        if fee10 is not None:
            parts.append(f"Fee10bp={fee10:+.4f}")
        if disqual:
            parts.append(f"DISQUAL={','.join(disqual)}")
        if warnings:
            parts.append(f"WARN={','.join(warnings)}")

        lines.append(" | ".join(parts))

    return "\n".join(lines)


def _read_rejected_proposals() -> List[Dict[str, Any]]:
    """Read rejected proposals from proposals/."""
    rejected = []
    for d in [REJECTED_DIR / "rejected_actions", REJECTED_DIR]:
        if not d.exists():
            continue
        for f in d.glob("rejected_*.json"):
            try:
                rejected.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, IOError):
                continue
    return rejected


def _read_action_files() -> List[Dict[str, Any]]:
    """Read executed action files."""
    if not ACTIONS_DIR.exists():
        return []
    actions = []
    for f in sorted(ACTIONS_DIR.glob("action_*.json"), key=lambda f: f.stat().st_mtime):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            actions.append(data.get("action", data))
        except (json.JSONDecodeError, IOError):
            continue
    return actions


def _read_contract_summary() -> str:
    """Read the first ~30 lines of the contract for context."""
    if not CONTRACT_PATH.exists():
        return "(contract not found)"
    lines = CONTRACT_PATH.read_text(encoding="utf-8").split("\n")[:40]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context builders
# ---------------------------------------------------------------------------


def _build_action_distribution(runs: List[Dict[str, Any]]) -> str:
    """Summarize what actions were taken across recent runs."""
    counts: Dict[str, int] = {}
    for run in runs:
        for step in run.get("steps", []):
            if step.get("step") == "execute":
                act = step.get("action", "?")
                counts[act] = counts.get(act, 0) + 1
    if not counts:
        return "(no actions executed)"
    parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
    return ", ".join(parts)


def _build_run_records_summary(runs: List[Dict[str, Any]]) -> str:
    """Build a compact summary of recent runs."""
    if not runs:
        return "(no runs)"
    summaries = []
    for r in runs[:10]:
        rid = r.get("run_id", "?")[-20:]
        state = r.get("final_state", "?")
        cid = r.get("candidate_id", "?")
        steps_ok = sum(1 for s in r.get("steps", []) if s.get("status") == "ok")
        steps_total = len(r.get("steps", []))
        summaries.append(f"{rid} cid={cid} state={state} steps={steps_ok}/{steps_total}")
    return "\n".join(summaries)


def _build_rejection_patterns(rejected: List[Dict[str, Any]]) -> str:
    """Summarize common rejection reasons."""
    if not rejected:
        return "(no rejections)"
    reason_counts: Dict[str, int] = {}
    for r in rejected:
        errors = r.get("errors", [r.get("reason", str(r))])
        for e in errors if isinstance(errors, list) else [errors]:
            key = e[:80]
            reason_counts[key] = reason_counts.get(key, 0) + 1
    parts = [f"{k} (x{v})" for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])[:10]]
    return "\n".join(parts)


def _build_fork_chains(runs: List[Dict[str, Any]]) -> str:
    """Identify fork chains from recent runs."""
    forks = []
    for r in runs:
        for s in r.get("steps", []):
            if s.get("step") == "execute" and s.get("action") == "fork":
                parent = r.get("candidate_id", "?")
                child = s.get("fork_id", "?")
                forks.append(f"{parent} -> {child}")
    if not forks:
        return "(no fork chains)"
    return "\n".join(forks[-10:])


def _search_space_summary() -> str:
    """Return the current search space definition from family registry."""
    try:
        from scripts.family_registry import search_space_summary as reg_summary

        return reg_summary() + "\n\nroles: standalone only"
    except Exception:
        return (
            "entry_lookback: [20, 1000], default 375\n"
            "min_hold_bars: [12, 1440], default 432\n"
            "regime_filter.fast_days: [5, 200], default 50\n"
            "regime_filter.slow_days: [10, 500], default 200\n"
            "roles: standalone only"
        )


def _build_family_analysis(runs: List[Dict[str, Any]]) -> str:
    """Build per-family analysis from run data.

    Analyzes each family's candidate count, success rate, action distribution,
    and step pass rate to identify family-level patterns.
    """
    from collections import Counter, defaultdict

    families: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "candidate_ids": [],
            "final_states": [],
            "actions": [],
            "steps_ok": 0,
            "steps_total": 0,
            "fork_chains": [],
        }
    )

    for r in runs:
        fam = r.get("family") or "unknown"
        families[fam]["candidate_ids"].append(r.get("candidate_id", "?"))
        families[fam]["final_states"].append(r.get("final_state", "?"))

        for s in r.get("steps", []):
            if s.get("status") == "ok":
                families[fam]["steps_ok"] += 1
            families[fam]["steps_total"] += 1
            if s.get("step") == "execute":
                act = s.get("action", "?")
                families[fam]["actions"].append(act)

        # Track fork chains
        for s in r.get("steps", []):
            if s.get("step") == "execute" and s.get("action") == "fork":
                parent = r.get("candidate_id", "?")
                child = s.get("fork_id", "?")
                families[fam]["fork_chains"].append(f"{parent} -> {child}")

    lines = []
    for fam, data in sorted(families.items(), key=lambda item: item[0]):
        n = len(data["candidate_ids"])
        if n == 0:
            continue

        success = sum(1 for s in data["final_states"] if s and s.startswith("executed_"))
        action_counts = Counter(data["actions"])
        action_str = ", ".join(f"{k}: {v}" for k, v in sorted(action_counts.items()))
        n_forks = len(data["fork_chains"])

        lines.append(f"{fam}:")
        lines.append(f"  Candidates: {n}")
        lines.append(f"  Success rate: {success}/{n} ({success * 100 // n}%)")
        if action_str:
            lines.append(f"  Actions: {action_str}")
        if data["steps_total"] > 0:
            lines.append(
                f"  Step pass rate: {data['steps_ok']}/{data['steps_total']} "
                f"({data['steps_ok'] * 100 // data['steps_total']}%)"
            )
        if n_forks > 0:
            lines.append(f"  Fork chains: {n_forks}")
            for fc in data["fork_chains"][:3]:
                lines.append(f"    {fc}")
            if n_forks > 3:
                lines.append(f"    ... ({n_forks - 3} more)")
        lines.append(f"  Final states: {Counter(data['final_states']).most_common(3)}")
        lines.append("")

    return "\n".join(lines) if lines else "(no family data)"


# ---------------------------------------------------------------------------
# Historical data aggregation
# ---------------------------------------------------------------------------


def collect_history(
    n_runs: int = DEFAULT_RUNS,
    *,
    latest: bool = False,
) -> Dict[str, Any]:
    """Collect all historical data needed for a meta-review.

    In ``latest`` mode, scorecard signals (verdict, flags, trades/yr)
    are prioritised over historical rejection counts.
    """
    runs = _read_run_files(n_runs)
    results = _read_results_tsv(n_runs)
    rejected = _read_rejected_proposals() if not latest else []
    actions = _read_action_files()
    scorecards = _read_scorecard_files(n_runs)

    summary: Dict[str, Any] = {
        "action_distribution": _build_action_distribution(runs),
        "run_records": _build_run_records_summary(runs),
        "rejection_patterns": _build_rejection_patterns(rejected),
        "fork_chains": _build_fork_chains(runs),
        "search_space": _search_space_summary(),
        "contract_summary": _read_contract_summary(),
        "scorecard_summary": _summarize_scorecards(scorecards),
        "scorecard_count": len(scorecards),
        "rejection_count": len(rejected),
    }

    return {
        "runs": runs,
        "results": results,
        "rejected": rejected,
        "actions": actions,
        "scorecards": scorecards,
        "summary": summary,
    }


def build_context(history: Dict[str, Any], review_id: str) -> str:
    """Build the meta-review prompt context.

    Scorecard signals (verdict, oracle flags, disqualifications,
    trades/year) are presented first. Historical rejections follow
    as secondary diagnostics.
    """
    template = _load_prompt_template()
    summary = history["summary"]
    runs = history["runs"]
    candidates = set()
    for r in runs:
        if r.get("candidate_id"):
            candidates.add(r["candidate_id"])

    # Build family-level analysis
    family_analysis = _build_family_analysis(runs)

    context = (
        template.replace("{{REVIEW_ID}}", review_id)
        .replace("{{RUNS_ANALYZED}}", str(len(runs)))
        .replace("{{CANDIDATES_ANALYZED}}", str(len(candidates)))
        .replace("{{TIME_PERIOD}}", runs[0].get("timestamp", "unknown")[:10] if runs else "N/A")
        .replace("{{ACTION_DISTRIBUTION}}", summary["action_distribution"])
        .replace("{{FAMILY_ANALYSIS}}", family_analysis)
        .replace("{{RUN_RECORDS}}", summary["run_records"])
        .replace("{{REJECTION_PATTERNS}}", summary["rejection_patterns"])
        .replace("{{FORK_CHAINS}}", summary["fork_chains"])
        .replace("{{SEARCH_SPACE}}", summary["search_space"])
        .replace("{{CONTRACT_SUMMARY}}", summary["contract_summary"])
        .replace("{{SCORECARD_SUMMARY}}", summary["scorecard_summary"])
        .replace("{{SCORECARD_COUNT}}", str(summary["scorecard_count"]))
        .replace("{{REJECTION_COUNT}}", str(summary["rejection_count"]))
    )
    return context


def _load_prompt_template() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Meta-review prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Review validation
# ---------------------------------------------------------------------------


def validate_review(review: Dict[str, Any]) -> List[str]:
    """Validate meta-review JSON structure and content.

    Returns list of error messages (empty = valid).
    """
    errors: List[str] = []

    if not isinstance(review, dict):
        return ["Review must be a JSON object"]

    # Check for forbidden topics
    raw = json.dumps(review, default=str).lower()
    for topic in FORBIDDEN_REVIEW_TOPICS:
        if topic.lower() in raw:
            errors.append(f"Forbidden topic detected: '{topic}'")

    # review_id
    rid = review.get("review_id", "")
    if not rid or not re.match(r"^meta_\d{4,}$", str(rid)):
        errors.append(f"Invalid review_id: '{rid}'")

    # window
    window = review.get("window", {})
    if not isinstance(window, dict):
        errors.append("'window' must be a dict")
    else:
        for k in ("runs_analyzed", "candidates_analyzed"):
            if not isinstance(window.get(k), int):
                errors.append(f"window.{k} must be an int")

    # findings
    findings = review.get("findings", [])
    if not isinstance(findings, list):
        errors.append("'findings' must be a list")
    elif len(findings) == 0:
        errors.append("At least one finding is required")
    else:
        for i, f in enumerate(findings):
            if not isinstance(f.get("type"), str):
                errors.append(f"findings[{i}].type must be a string")
            if f.get("severity") not in ("low", "medium", "high"):
                errors.append(f"findings[{i}].severity must be low/medium/high")
            if not f.get("summary") or len(str(f.get("summary", ""))) < 10:
                errors.append(f"findings[{i}].summary too short (min 10 chars)")

    # recommendations
    recs = review.get("recommendations", [])
    if not isinstance(recs, list):
        errors.append("'recommendations' must be a list")
    elif len(recs) == 0:
        errors.append("At least one recommendation is required")
    else:
        for i, r in enumerate(recs):
            if r.get("target") not in (
                "search_space",
                "prompt",
                "contract",
                "schema",
                "process",
                "family",
            ):
                errors.append(f"recommendations[{i}].target unknown: '{r.get('target')}'")
            if r.get("action") not in ("narrow", "expand", "modify", "retire"):
                errors.append(f"recommendations[{i}].action unknown: '{r.get('action')}'")
            if not r.get("proposal") or len(str(r.get("proposal", ""))) < 10:
                errors.append(f"recommendations[{i}].proposal too short (min 10 chars)")

    # requires_human_review
    if not isinstance(review.get("requires_human_review"), bool):
        errors.append("'requires_human_review' must be a boolean")

    # If contract_changes or prompt_changes are non-empty, must flag human review
    if review.get("contract_changes") and not review.get("requires_human_review"):
        errors.append("contract_changes requires requires_human_review=true")
    if review.get("prompt_changes") and not review.get("requires_human_review"):
        errors.append("prompt_changes requires requires_human_review=true")

    return errors


# ---------------------------------------------------------------------------
# Main review flow
# ---------------------------------------------------------------------------


def run_meta_review(
    n_runs: int = DEFAULT_RUNS,
    *,
    dry_run: bool = False,
    latest: bool = False,
) -> Dict[str, Any]:
    """Run one meta-review cycle.

    Parameters
    ----------
    n_runs : int
        Number of recent runs/scorecards to analyze.
    dry_run : bool
        If True, print context, skip LLM call and writes.
    latest : bool
        If True, focus on latest scorecards and runs.
        Historical rejections are summarised as a count only,
        not presented as dominant signals.
    """
    review_id = _next_review_id()
    result: Dict[str, Any] = {
        "status": "error",
        "review_id": review_id,
        "errors": [],
        "review_path": None,
    }

    print(f"\n{'=' * 60}")
    print("  Meta-Agent Review v0.7")
    print(f"  Review ID: {review_id}")
    print(f"  Runs: {n_runs}")
    if latest:
        print("  Mode: LATEST (scorecard-first)")
    print(f"{'=' * 60}")

    # --- Collect history ---
    print("\n[Step 1] Collecting history ...")
    history = collect_history(n_runs, latest=latest)
    print(f"  Runs: {len(history['runs'])}")
    print(f"  Results: {len(history['results'])}")
    print(f"  Rejected: {len(history['rejected'])}")
    print(f"  Scorecards: {history['summary']['scorecard_count']}")
    print(f"  Actions: {len(history['actions'])}")

    # --- Build context ---
    print("\n[Step 2] Building context ...")
    context = build_context(history, review_id)

    if dry_run:
        print("  (dry-run: printing context, skipping LLM)")
        print(f"\n{'=' * 60}")
        print("  CONTEXT:")
        print(f"{'=' * 60}")
        print(context[:2000] + ("\n  ... (truncated)" if len(context) > 2000 else ""))
        print(f"\n{'=' * 60}")
        result["status"] = "dry_run"
        return result

    # --- Call LLM ---
    print("\n[Step 3] Calling LLM ...")
    system_prompt = (
        "You are a research process reviewer. Output ONLY valid JSON. No markdown, no explanations."
    )
    try:
        response = call_llm(system_prompt=system_prompt, user_message=context)
        result["llm_response"] = response
    except Exception as e:
        print(f"  [ERROR] LLM call failed: {e}")
        result["errors"] = [str(e)]
        return result
    print(f"  Response length: {len(response)} chars")

    # --- Parse ---
    print("\n[Step 4] Parsing review ...")
    review = _parse_review_response(response)
    if review is None:
        print("  [FAIL] Could not parse JSON from LLM response")
        result["errors"] = ["Non-JSON response from LLM"]
        result["status"] = "rejected"
        return result
    print(f"  Review ID: {review.get('review_id', '?')}")
    print(f"  Findings: {len(review.get('findings', []))}")
    print(f"  Recommendations: {len(review.get('recommendations', []))}")

    # --- Validate ---
    print("\n[Step 5] Validating review ...")
    validation_errors = validate_review(review)
    if validation_errors:
        print(f"  [FAIL] {len(validation_errors)} error(s):")
        for e in validation_errors:
            print(f"    - {e}")
        result["status"] = "rejected"
        result["errors"] = validation_errors
        return result
    print("  [OK] Review valid")

    # --- Write ---
    print("\n[Step 6] Writing review ...")
    review_path = _write_review(review)
    print(f"  [OK] Review written: {review_path}")
    result["status"] = "ok"
    result["review_path"] = str(review_path)

    return result


def _parse_review_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM response into a review dict."""
    text = response_text.strip()
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                parsed = json.loads(text[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                return None
        else:
            return None
    if not isinstance(parsed, dict):
        return None
    _normalize_review_id(parsed)
    return parsed


def _normalize_review_id(review: Dict[str, Any]) -> None:
    """Normalize common LLM review_id shape slips in-place."""
    rid = review.get("review_id")
    if not isinstance(rid, str):
        return
    match = re.fullmatch(r"meta_(\d{1,3})", rid)
    if match:
        review["review_id"] = f"meta_{int(match.group(1)):04d}"


def _write_review(review: Dict[str, Any]) -> Path:
    """Write a valid meta-review to meta_reviews/."""
    META_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    rid = review.get("review_id", "meta_unknown")
    path = META_REVIEWS_DIR / f"{rid}.json"
    payload = {
        "timestamp": _now_iso(),
        "review": review,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Meta-Agent Review v0.7 — research process retrospective"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Number of recent runs to analyze (default: {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show context, skip LLM call and writes",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Focus on latest scorecards/runs. Historical rejections are "
        "summarised as a count, not dominant signals.",
    )
    args = parser.parse_args()

    result = run_meta_review(args.runs, dry_run=args.dry_run, latest=args.latest)

    print(f"\n{'=' * 60}")
    if result["status"] == "ok":
        print(f"  [OK] Meta-review complete: {result['review_id']}")
        print(f"  Path: {result['review_path']}")
        sys.exit(0)
    elif result["status"] == "rejected":
        print("  [FAIL] Review rejected:")
        for e in result["errors"]:
            print(f"    - {e}")
        sys.exit(1)
    elif result["status"] == "dry_run":
        print("  [OK] Dry-run complete.")
        sys.exit(0)
    else:
        print(f"  [ERROR] {result.get('errors', ['Unknown error'])}")
        sys.exit(2)


if __name__ == "__main__":
    main()
