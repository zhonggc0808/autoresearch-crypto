#!/usr/bin/env python3
"""Scorecard engine — transform oracle output into structured scorecard + results log.

v0.3 — Dual-window Verdict Engine

Pure scorer: given (oracle_result, stage) → scorecard with verdict + promotion gates.
No orchestration logic — that is the responsibility of ``evaluate_candidate.py``.

Produces:
    research_workspace/llm_scorecards/{experiment_id}_scorecard.json
    research_workspace/llm_results.tsv              (appended row)

Usage:
    # Score the latest oracle run (reads experiments.jsonl)
    uv run python scripts/score_candidate.py --latest

    # Score with explicit stage
    uv run python scripts/score_candidate.py --latest --stage 2600d

    # Score a specific experiment_id
    uv run python scripts/score_candidate.py --experiment-id exp_0001

    # Score from oracle_report.json directly
    uv run python scripts/score_candidate.py --oracle-report

    # Score from a candidate spec (re-reads experiments.jsonl for matching hash)
    uv run python scripts/score_candidate.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json

    # Dry-run (print scorecard, no file writes)
    uv run python scripts/score_candidate.py --latest --no-write

Exit codes:
    0 — scorecard produced
    1 — input not found / read error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Verdict enum (v0.3)
# ---------------------------------------------------------------------------

VERDICT_KILL = "kill"
VERDICT_REQUIRES_2600D = "requires_2600d"
VERDICT_BLOCKED_MISSING_2600D = "blocked_missing_2600d_data"
VERDICT_RESEARCH_ONLY_RECENT_REGIME = "research_only_recent_regime"
VERDICT_PROMOTE_REVIEW_PENDING = "promote_review_pending"
VERDICT_INVALID_ORACLE = "invalid_oracle_output"
VERDICT_INVALID_CANDIDATE = "invalid_candidate"

ALL_VERDICTS = frozenset(
    {
        VERDICT_KILL,
        VERDICT_REQUIRES_2600D,
        VERDICT_BLOCKED_MISSING_2600D,
        VERDICT_RESEARCH_ONLY_RECENT_REGIME,
        VERDICT_PROMOTE_REVIEW_PENDING,
        VERDICT_INVALID_ORACLE,
        VERDICT_INVALID_CANDIDATE,
    }
)

VERDICT_DESCRIPTIONS = {
    VERDICT_KILL: "Candidate disqualified at 1300d — no further iteration.",
    VERDICT_REQUIRES_2600D: "1300d passed — 2600d verification required.",
    VERDICT_BLOCKED_MISSING_2600D: "2600d dataset not available — cannot complete dual-window validation.",
    VERDICT_RESEARCH_ONLY_RECENT_REGIME: "Passed 1300d but failed 2600d — recent-regime only, not generalizable.",
    VERDICT_PROMOTE_REVIEW_PENDING: "Both 1300d and 2600d passed — ready for human promotion review.",
    VERDICT_INVALID_ORACLE: "Oracle output could not be interpreted — evaluation failed.",
    VERDICT_INVALID_CANDIDATE: "Candidate spec is invalid or violates schema.",
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

OUTPUT_DIR = PROJECT_DIR / "research_workspace"
LLM_CANDIDATES_DIR = OUTPUT_DIR / "llm_candidates"
LLM_SCORECARDS_DIR = OUTPUT_DIR / "llm_scorecards"
LLM_RESULTS_TSV = OUTPUT_DIR / "llm_results.tsv"
EXPERIMENTS_JSONL = OUTPUT_DIR / "experiments.jsonl"
ORACLE_REPORT = OUTPUT_DIR / "oracle_report.json"
CANDIDATES_DIR = OUTPUT_DIR / "candidates"


def _ensure_dirs() -> None:
    LLM_SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------


def _read_experiments_jsonl() -> List[Dict[str, Any]]:
    if not EXPERIMENTS_JSONL.exists():
        return []
    records = []
    with open(EXPERIMENTS_JSONL, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _find_latest_experiment() -> Optional[Dict[str, Any]]:
    records = _read_experiments_jsonl()
    if not records:
        return None
    return records[-1]


def _find_experiment_by_id(experiment_id: str) -> Optional[Dict[str, Any]]:
    records = _read_experiments_jsonl()
    for r in reversed(records):
        if r.get("experiment_id") == experiment_id:
            return r
    return None


def _read_oracle_report() -> Optional[Dict[str, Any]]:
    if not ORACLE_REPORT.exists():
        return None
    with open(ORACLE_REPORT, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_candidate_spec(candidate_path: Path) -> Optional[Dict[str, Any]]:
    if not candidate_path.exists():
        return None
    with open(candidate_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Promotion gate checks
# ---------------------------------------------------------------------------


def _check_promotion_gates(metrics: Dict[str, Any], flags: Dict[str, Any]) -> Dict[str, Any]:
    """Check all 8 promotion gates for standalone candidates.

    Returns dict with gate results and overall pass/fail.
    """
    m = metrics
    f = flags
    is_raw = m.get("is", {}).get("raw", {})
    rolling = m.get("rolling", {})
    sensitivity_is = m.get("sensitivity", {}).get("is", {})
    correlation = m.get("correlation", {})
    execution_parity = m.get("execution_parity", 0)

    gates = {}

    # Gate 1: Oracle status == PASS, no disqualifications
    g1_pass = f.get("status") == "PASS" and len(f.get("disqualifications", [])) == 0
    gates["oracle_pass"] = {
        "pass": g1_pass,
        "value": f.get("status"),
        "required": "PASS with no disqualifications",
    }

    # Gate 2: Safe execution parity >= 0.90
    g2_pass = execution_parity is not None and execution_parity >= 0.90
    gates["execution_parity"] = {
        "pass": g2_pass,
        "value": execution_parity,
        "required": ">= 0.90",
    }

    # Gate 3: Rolling 12-month minimum return > 0
    r12m = rolling.get("12m_min_return")
    g3_pass = r12m is not None and r12m > 0
    gates["rolling_12m_return"] = {
        "pass": g3_pass,
        "value": r12m,
        "required": "> 0",
    }

    # Gate 4: IS drawdown >= -40%
    is_dd = is_raw.get("dd", 0)
    g4_pass = is_dd >= -0.40
    gates["is_drawdown"] = {
        "pass": g4_pass,
        "value": is_dd,
        "required": ">= -0.40",
    }

    # Gate 5: Correlation vs baseline < 0.95
    corr = correlation.get("vs_baseline")
    g5_pass = corr is not None and corr < 0.95
    gates["correlation_vs_baseline"] = {
        "pass": g5_pass,
        "value": corr,
        "required": "< 0.95",
    }

    # Gate 6: Return stays positive at 10bp fees (IS)
    fee_10bp = sensitivity_is.get("fees", {}).get("10bp", 0)
    g6_pass = fee_10bp > 0
    gates["fee_robustness"] = {
        "pass": g6_pass,
        "value": fee_10bp,
        "required": "> 0 at 10bp",
    }

    # Gate 7: Trade count >= 20 per year
    tpy = is_raw.get("trades_per_year", 0)
    g7_pass = tpy >= 20
    gates["trade_count"] = {
        "pass": g7_pass,
        "value": tpy,
        "required": ">= 20/yr",
    }

    # Derived Gate: Turnover explosion (trades/yr > 500 or > baseline * 10)
    # This is a derived flag, not from oracle — detects strategies that
    # churn excessively, a common failure mode for LLM-generated strategies.
    TURNOVER_THRESHOLD_ABSOLUTE = 500
    TURNOVER_THRESHOLD_MULTIPLIER = 10
    baseline_tpy = is_raw.get("trades_per_year", 0)  # fallback if no baseline
    turnover_threshold = max(
        TURNOVER_THRESHOLD_ABSOLUTE,
        baseline_tpy * TURNOVER_THRESHOLD_MULTIPLIER,
    )
    g_turnover_pass = tpy <= turnover_threshold
    gates["turnover_explosion"] = {
        "pass": g_turnover_pass,
        "value": tpy,
        "required": f"<= {turnover_threshold}/yr",
        "derived": True,
        "note": "TURNOVER_EXPLOSION — excessive trade frequency",
    }

    # Gate 8: Human review (always false for automated scorecards)
    gates["human_review"] = {
        "pass": False,
        "value": False,
        "required": "Human approval (manual)",
    }

    all_pass = all(g["pass"] for g in gates.values())
    return {
        "all_pass": all_pass,
        "gates_passed": sum(1 for g in gates.values() if g["pass"]),
        "gates_total": len(gates),
        "gates": gates,
    }


# ---------------------------------------------------------------------------
# Verdict engine (v0.3 dual-window state machine)
# ---------------------------------------------------------------------------


def compute_verdict(oracle_result: Dict[str, Any], stage: str = "1300d") -> Dict[str, Any]:
    """Compute verdict from oracle result for a given validation stage.

    Parameters
    ----------
    oracle_result : dict
        Full oracle evaluation result (as produced by ``research_oracle.run_oracle()``).
    stage : str
        One of ``"1300d"`` or ``"2600d"``.

    Returns
    -------
    dict with keys: ``label``, ``reason``, ``source_status``, ``description``.

    Verdict rules
    -------------
    1300d:
        REJECT   → kill
        WARN     → requires_2600d  (passed hard gates)
        PASS     → requires_2600d
        BASELINE → kill  (baseline runs are not candidates)
        else     → invalid_oracle_output

    2600d:
        REJECT   → research_only_recent_regime
        WARN     → research_only_recent_regime  (warnings = not promotable)
        PASS     → promote_review_pending
        BASELINE → research_only_recent_regime
        else     → invalid_oracle_output
    """
    flags = oracle_result.get("flags", {})
    status = flags.get("status", "UNKNOWN")
    disqualifications = flags.get("disqualifications", [])
    warnings = flags.get("warnings", [])

    if stage == "1300d":
        if status == "REJECT" or disqualifications:
            return _verdict(
                VERDICT_KILL,
                f"Disqualified at 1300d: {', '.join(disqualifications)}",
                "REJECT",
                flags,
            )
        elif status in ("PASS", "WARN"):
            return _verdict(
                VERDICT_REQUIRES_2600D,
                f"1300d status={status}. 2600d verification required.",
                status,
                flags,
            )
        elif status == "BASELINE":
            return _verdict(VERDICT_KILL, "Baseline run is not a candidate.", "BASELINE", flags)
        else:
            return _verdict(
                VERDICT_INVALID_ORACLE,
                f"Unrecognized oracle status '{status}' at 1300d.",
                status,
                flags,
            )

    elif stage == "2600d":
        if status == "REJECT" or disqualifications:
            return _verdict(
                VERDICT_RESEARCH_ONLY_RECENT_REGIME,
                f"2600d disqualified: {', '.join(disqualifications)}. "
                "Recent-regime only, not generalizable.",
                "REJECT",
                flags,
            )
        elif status == "WARN":
            return _verdict(
                VERDICT_RESEARCH_ONLY_RECENT_REGIME,
                f"2600d warnings: {', '.join(warnings)}. Not clean enough for promotion.",
                "WARN",
                flags,
            )
        elif status == "PASS":
            return _verdict(
                VERDICT_PROMOTE_REVIEW_PENDING,
                "2600d PASS. Both windows clear — ready for human promotion review.",
                "PASS",
                flags,
            )
        elif status == "BASELINE":
            return _verdict(
                VERDICT_RESEARCH_ONLY_RECENT_REGIME,
                "Baseline run at 2600d — not a candidate.",
                "BASELINE",
                flags,
            )
        else:
            return _verdict(
                VERDICT_INVALID_ORACLE,
                f"Unrecognized oracle status '{status}' at 2600d.",
                status,
                flags,
            )

    else:
        return _verdict(VERDICT_INVALID_ORACLE, f"Unknown stage '{stage}'.", stage, flags)


def _verdict(label: str, reason: str, source_status: str, flags: Dict[str, Any]) -> Dict[str, Any]:
    """Build a verdict dict."""
    return {
        "label": label,
        "reason": reason,
        "source_status": source_status,
        "description": VERDICT_DESCRIPTIONS.get(label, ""),
    }


# ---------------------------------------------------------------------------
# Scorecard builder
# ---------------------------------------------------------------------------


def build_scorecard(
    result: Dict[str, Any],
    candidate_spec: Optional[Dict[str, Any]] = None,
    stage: str = "1300d",
) -> Dict[str, Any]:
    """Build a structured scorecard from an oracle result dict.

    Parameters
    ----------
    result : dict
        Oracle evaluation result.
    candidate_spec : dict or None
        Original candidate spec (for description, hypothesis, etc.).
    stage : str
        Validation window: ``"1300d"`` or ``"2600d"``.

    Returns
    -------
    dict — the scorecard.
    """
    m = result.get("metrics", {})
    f = result.get("flags", {})

    is_raw = m.get("is", {}).get("raw", {})
    oos_raw = m.get("oos", {}).get("raw", {})
    oos_safe = m.get("oos", {}).get("safe_execution", {})
    rolling = m.get("rolling", {})

    verdict = compute_verdict(result, stage=stage)
    promotion = _check_promotion_gates(m, f)

    # Fee extraction
    fee_10bp = 0.0
    try:
        fee_10bp = result["metrics"]["sensitivity"]["is"]["fees"]["10bp"]
    except (KeyError, TypeError):
        pass

    # Next steps hint (purely informational — the orchestrator decides)
    next_steps = _next_steps_hint(verdict["label"], stage)

    scorecard = {
        "scorecard_version": "v0.3",
        "stage": stage,
        "window": stage,  # alias for clarity
        "timestamp": _now_iso(),
        "oracle_timestamp": result.get("timestamp"),
        "experiment_id": result.get("experiment_id"),
        "parent_id": result.get("parent_id"),
        "candidate_role": result.get("candidate_role"),
        "strategy": result.get("strategy"),
        "params_hash": result.get("params_hash"),
        "data_hash": result.get("data_hash"),
        "commit": result.get("commit"),
        "oracle_version": result.get("oracle_version"),
        "baseline_id": result.get("baseline_id"),
        "split_id": result.get("split_id"),
        "description": candidate_spec.get("description") if candidate_spec else None,
        "hypothesis": candidate_spec.get("hypothesis") if candidate_spec else None,
        "verdict": verdict,
        "next_steps": next_steps,
        "promotion_gates": promotion,
        "metrics_summary": {
            "is_return": is_raw.get("return"),
            "is_dd": is_raw.get("dd"),
            "is_sharpe": is_raw.get("sharpe"),
            "oos_return": oos_raw.get("return"),
            "oos_dd": oos_raw.get("dd"),
            "oos_sharpe": oos_raw.get("sharpe"),
            "oos_safe_return": oos_safe.get("return"),
            "oos_safe_dd": oos_safe.get("dd"),
            "rolling_6m_min_return": rolling.get("6m_min_return"),
            "rolling_12m_min_return": rolling.get("12m_min_return"),
            "trades_per_year": is_raw.get("trades_per_year"),
            "execution_parity": m.get("execution_parity"),
            "corr_vs_baseline": m.get("correlation", {}).get("vs_baseline"),
            "fee_10bp_return": fee_10bp,
        },
        "flags": {
            "status": f.get("status"),
            "warnings": f.get("warnings", []),
            "disqualifications": f.get("disqualifications", []),
            "baseline_known_risks": f.get("baseline_known_risks", []),
        },
        "oracle_result_ref": result.get("experiment_id"),
    }
    return scorecard


def _next_steps_hint(verdict_label: str, stage: str) -> str:
    """Informational next-steps hint (the orchestrator makes the actual decision)."""
    hints = {
        VERDICT_KILL: "Candidate terminated. No further evaluation.",
        VERDICT_REQUIRES_2600D: "Run 2600d evaluation if data is available.",
        VERDICT_BLOCKED_MISSING_2600D: "Acquire 2600d dataset or mark as research_only.",
        VERDICT_RESEARCH_ONLY_RECENT_REGIME: "Research-only. Document regime limitation.",
        VERDICT_PROMOTE_REVIEW_PENDING: "Prepare promotion packet for human review.",
        VERDICT_INVALID_ORACLE: "Investigate oracle failure.",
        VERDICT_INVALID_CANDIDATE: "Fix candidate spec and re-submit.",
    }
    return hints.get(verdict_label, "Unknown verdict.")


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _scorecard_filename(experiment_id: str, stage: str) -> str:
    """E.g. exp_0002_1300d_scorecard.json or exp_0002_2600d_scorecard.json."""
    return f"{experiment_id}_{stage}_scorecard.json"


def _write_scorecard(scorecard: Dict[str, Any]) -> Path:
    _ensure_dirs()
    eid = scorecard["experiment_id"]
    stage = scorecard.get("stage", "1300d")
    fname = _scorecard_filename(eid, stage)
    path = LLM_SCORECARDS_DIR / fname
    path.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")
    return path


def _append_llm_results_tsv(scorecard: Dict[str, Any], scorecard_path: Path) -> None:
    ms = scorecard.get("metrics_summary", {})
    fl = scorecard.get("flags", {})
    v = scorecard.get("verdict", {})

    warnings_str = "|".join(fl.get("warnings", [])) if fl.get("warnings") else ""
    disqual_str = "|".join(fl.get("disqualifications", [])) if fl.get("disqualifications") else ""

    row = (
        f"{scorecard['timestamp']}\t"
        f"{scorecard['experiment_id']}\t"
        f"{scorecard.get('parent_id') or 'null'}\t"
        f"{scorecard.get('candidate_role')}\t"
        f"{scorecard.get('strategy')}\t"
        f"{scorecard.get('description') or ''}\t"
        f"{scorecard.get('stage', '?')}\t"
        f"{v.get('label', '?')}\t"
        f"{v.get('reason', '')}\t"
        f"{ms.get('is_return', 'N/A')}\t"
        f"{ms.get('is_dd', 'N/A')}\t"
        f"{ms.get('is_sharpe', 'N/A')}\t"
        f"{ms.get('oos_return', 'N/A')}\t"
        f"{ms.get('oos_dd', 'N/A')}\t"
        f"{ms.get('oos_safe_return', 'N/A')}\t"
        f"{ms.get('rolling_12m_min_return', 'N/A')}\t"
        f"{ms.get('trades_per_year', 'N/A')}\t"
        f"{ms.get('corr_vs_baseline', 'N/A')}\t"
        f"{ms.get('execution_parity', 'N/A')}\t"
        f"{ms.get('fee_10bp_return', 'N/A')}\t"
        f"{warnings_str}\t"
        f"{disqual_str}\t"
        f"{scorecard.get('next_steps', '')}\t"
        f"{scorecard_path.name}\n"
    )

    write_header = not LLM_RESULTS_TSV.exists()
    with open(LLM_RESULTS_TSV, "a", encoding="utf-8", newline="") as fh:
        if write_header:
            fh.write(
                "timestamp\texperiment_id\tparent_id\tcandidate_role\t"
                "strategy\tdescription\tstage\tverdict\tverdict_reason\t"
                "is_return\tis_dd\tis_sharpe\toos_return\toos_dd\t"
                "oos_safe_return\trolling_12m_min\ttrades_per_year\t"
                "corr_vs_baseline\texecution_parity\tfee_10bp_return\t"
                "warnings\tdisqualifications\tnext_steps\tscorecard_path\n"
            )
        fh.write(row)


# ---------------------------------------------------------------------------
# Public API (for use by evaluate_candidate.py)
# ---------------------------------------------------------------------------


def build_scorecard_from_result(
    oracle_result: Dict[str, Any],
    candidate_spec: Optional[Dict[str, Any]] = None,
    stage: str = "1300d",
) -> Dict[str, Any]:
    """Build scorecard from an oracle result.  Pure function — no I/O."""
    return build_scorecard(oracle_result, candidate_spec, stage=stage)


def write_scorecard_and_log(scorecard: Dict[str, Any]) -> Path:
    """Write scorecard JSON and append to TSV.  Returns the file path."""
    sc_path = _write_scorecard(scorecard)
    _append_llm_results_tsv(scorecard, sc_path)
    return sc_path


# ---------------------------------------------------------------------------
# Orchestration helpers (for evaluate_candidate.py)
# ---------------------------------------------------------------------------


def load_scorecard(experiment_id: str, stage: str) -> Optional[Dict[str, Any]]:
    """Load a previously written scorecard by experiment_id and stage."""
    fname = _scorecard_filename(experiment_id, stage)
    path = LLM_SCORECARDS_DIR / fname
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def process_latest(no_write: bool = False, stage: str = "1300d") -> Optional[Dict[str, Any]]:
    result = _find_latest_experiment()
    if result is None:
        print("ERROR: No experiments found in experiments.jsonl")
        return None
    print(f"Scoring latest experiment: {result['experiment_id']} (stage={stage})")
    return _process_result(result, stage=stage, no_write=no_write)


def process_experiment_id(
    experiment_id: str,
    no_write: bool = False,
    stage: str = "1300d",
) -> Optional[Dict[str, Any]]:
    result = _find_experiment_by_id(experiment_id)
    if result is None:
        print(f"ERROR: No experiment found with ID '{experiment_id}'")
        return None
    print(f"Scoring experiment: {experiment_id} (stage={stage})")
    return _process_result(result, stage=stage, no_write=no_write)


def process_oracle_report(
    no_write: bool = False,
    stage: str = "1300d",
) -> Optional[Dict[str, Any]]:
    result = _read_oracle_report()
    if result is None:
        print("ERROR: oracle_report.json not found")
        return None
    eid = result.get("experiment_id", "?")
    print(f"Scoring from oracle_report.json: {eid} (stage={stage})")
    return _process_result(result, stage=stage, no_write=no_write)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return "sha256:FILE_NOT_FOUND"
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def process_candidate_file(
    candidate_path_str: str,
    no_write: bool = False,
    stage: str = "1300d",
) -> Optional[Dict[str, Any]]:
    cpath = Path(candidate_path_str)
    spec = _read_candidate_spec(cpath)
    if spec is None:
        print(f"ERROR: Candidate file not found: {cpath}")
        return None

    file_hash = _file_hash(cpath)
    eid = spec.get("experiment_id", "?")
    records = _read_experiments_jsonl()

    result = None
    for r in reversed(records):
        if r.get("params_hash") == file_hash:
            result = r
            break

    if result is None:
        print("ERROR: No oracle result found matching candidate file hash")
        print(f"  Candidate: {cpath.name} (experiment_id={eid})")
        print(f"  Expected hash: {file_hash}")
        print("  Run build_candidate_from_spec.py first.")
        return None

    print(f"Scoring candidate {eid} (stage={stage})")
    return _process_result(result, candidate_spec=spec, stage=stage, no_write=no_write)


def _process_result(
    oracle_result: Dict[str, Any],
    candidate_spec: Optional[Dict[str, Any]] = None,
    stage: str = "1300d",
    no_write: bool = False,
) -> Dict[str, Any]:
    """Build scorecard, optionally write files, return scorecard."""
    scorecard = build_scorecard(oracle_result, candidate_spec, stage=stage)

    v = scorecard["verdict"]
    ms = scorecard["metrics_summary"]
    pg = scorecard["promotion_gates"]

    print()
    print("=== Scorecard (v0.3) ===")
    print(f"  Experiment: {scorecard['experiment_id']}")
    print(f"  Stage:      {stage}")
    print(f"  Verdict:    {v['label']} -- {v['reason']}")
    print(f"  Next steps: {scorecard['next_steps']}")
    print(
        f"  Promotion gates: {pg['gates_passed']}/{pg['gates_total']} "
        f"({'PASS' if pg['all_pass'] else 'BLOCKED'})"
    )
    print()
    print(
        f"  IS return:  {ms['is_return']:.4f}   DD: {ms['is_dd']:.4f}   "
        f"Sharpe: {ms['is_sharpe']:.4f}"
    )
    print(f"  OOS return: {ms['oos_return']:.4f}   DD: {ms['oos_dd']:.4f}")
    print(f"  OOS safe:   {ms['oos_safe_return']:.4f}")
    print(f"  Rolling 12m min: {ms['rolling_12m_min_return']}")
    print(f"  Trades/yr:  {ms['trades_per_year']}")
    print(f"  Fee@10bp:   {ms['fee_10bp_return']:.4f}")
    print(f"  Corr baseline: {ms['corr_vs_baseline']}")
    print(f"  Exec parity: {ms['execution_parity']}")
    print()
    print(f"  Status: {scorecard['flags']['status']}")
    print(f"  Warnings: {scorecard['flags']['warnings']}")
    print(f"  Disqualifications: {scorecard['flags']['disqualifications']}")

    if not no_write:
        sc_path = write_scorecard_and_log(scorecard)
        print(f"\n  Scorecard: {sc_path}")
        print(f"  Results:   {LLM_RESULTS_TSV}")
    else:
        print("\n  (--no-write: skipping output files)")

    return scorecard


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Scorecard engine v0.3 — oracle output → structured scorecard + results log"
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--latest",
        action="store_true",
        help="Score the most recent experiment in experiments.jsonl",
    )
    input_group.add_argument(
        "--experiment-id",
        type=str,
        default=None,
        help="Score a specific experiment by ID",
    )
    input_group.add_argument(
        "--oracle-report",
        action="store_true",
        help="Score from oracle_report.json",
    )
    input_group.add_argument(
        "--candidate",
        type=str,
        default=None,
        help="Score by matching candidate spec JSON to experiments.jsonl",
    )
    parser.add_argument(
        "--stage",
        type=str,
        default="1300d",
        choices=["1300d", "2600d"],
        help="Validation window (default: 1300d)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Dry-run: print scorecard, no file writes",
    )

    args = parser.parse_args()

    if args.latest:
        scorecard = process_latest(no_write=args.no_write, stage=args.stage)
    elif args.experiment_id:
        scorecard = process_experiment_id(
            args.experiment_id, no_write=args.no_write, stage=args.stage
        )
    elif args.oracle_report:
        scorecard = process_oracle_report(no_write=args.no_write, stage=args.stage)
    elif args.candidate:
        scorecard = process_candidate_file(args.candidate, no_write=args.no_write, stage=args.stage)
    else:
        parser.error(
            "One of --latest, --experiment-id, --oracle-report, or --candidate is required"
        )

    if scorecard is None:
        sys.exit(1)

    pg = scorecard.get("promotion_gates", {})
    if pg.get("all_pass"):
        verdict = scorecard.get("verdict", {}).get("label", "")
        if verdict == VERDICT_PROMOTE_REVIEW_PENDING:
            print(
                "\n[STAR] All promotion gates passed! "
                "This candidate is ready for human promotion review."
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
