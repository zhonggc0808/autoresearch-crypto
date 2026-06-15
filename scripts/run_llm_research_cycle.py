#!/usr/bin/env python3
"""LLM Research Cycle Runner v0.6.5 — one complete autonomous research cycle.

Orchestrates:

    generate/read candidate
    → validate (v0.2 schema)
    → evaluate 1300d (or mock)
    → scorecard
    → review action (v0.5)
    → execute action (v0.6)
    → write run summary

Usage:
    # Full cycle with mock LLM (no API key needed)
    uv run python scripts/run_llm_research_cycle.py --mock

    # Full cycle starting from existing candidate file
    uv run python scripts/run_llm_research_cycle.py \\
        --candidate research_workspace/llm_candidates/exp_NNNN.json

    # Full cycle with real LLM + oracle evaluation
    uv run python scripts/run_llm_research_cycle.py --evaluate

    # Full cycle with real LLM, no oracle (dry-run evaluation)
    uv run python scripts/run_llm_research_cycle.py

Exit codes:
    0 — cycle completed (any final state)
    1 — cycle failed (step error)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LLM_RUNS_DIR = PROJECT_DIR / "research_workspace" / "llm_runs"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
LLM_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Mock responses for --mock mode
# ---------------------------------------------------------------------------

MOCK_CANDIDATE_JSON = json.dumps(
    {
        "parent_id": "channel_breakout_v2_1_balanced",
        "description": "Mock cycle: test entry_lookback=300 across all regimes",
        "hypothesis": "Reducing entry_lookback from 375 to 300 increases trade frequency by ~15% without degrading DD.",
        "expected_behavior_change": "Trade count increases ~15%. Bear regime return stays above 200%.",
        "params": {
            "strategy_type": "regime_permission_channel_breakout",
            "regime_change_policy": "permission_based",
            "regime_filter": {"fast_days": 50, "slow_days": 200},
            "bull": {
                "candidate": "U4_cons3_L300",
                "strategy_params": {
                    "entry_lookback": 300,
                    "min_hold_bars": 432,
                    "enable_long": True,
                    "enable_short": False,
                },
                "permission": {
                    "allow_long": True,
                    "allow_short": False,
                    "close_below_ema_disables_long": True,
                    "ema_fast": 50,
                    "consecutive_below_ema_days": 3,
                },
            },
            "bear": {
                "candidate": "K0_base_L300",
                "strategy_params": {
                    "entry_lookback": 300,
                    "min_hold_bars": 432,
                    "enable_long": True,
                    "enable_short": True,
                },
                "permission": {"allow_long": True, "allow_short": True},
            },
            "neutral": {
                "candidate": "N3_dir_L300",
                "strategy_params": {
                    "entry_lookback": 300,
                    "min_hold_bars": 432,
                    "enable_long": True,
                    "enable_short": True,
                },
                "permission": {
                    "allow_long": True,
                    "allow_short": True,
                    "directional_only": True,
                    "ema_fast": 50,
                    "ema_slope_days": 5,
                },
            },
        },
    }
)

MOCK_ACTION_JSON = json.dumps(
    {
        "action": "fork",
        "source_candidate_id": "placeholder",
        "target_family": "channel_breakout",
        "rationale": "Candidate requires_2600d. Forking with adjusted entry_lookback to test sensitivity reduction while maintaining regime conviction.",
        "allowed_change": {"entry_lookback": 350, "min_hold_bars": 400},
        "risk_note": "Research fork only.",
    }
)

MOCK_ACTION_KILL = json.dumps(
    {
        "action": "kill",
        "source_candidate_id": "placeholder",
        "target_family": "channel_breakout",
        "rationale": "Candidate failed 1300d with disqualifications. No viable fork path from this direction.",
    }
)


def _mock_llm_response(system_prompt: str, user_message: str) -> str:
    """Return a canned response based on the role in the system prompt."""
    sp_lower = system_prompt.lower()
    if "reviewer" in sp_lower:
        return MOCK_ACTION_JSON
    else:
        return MOCK_CANDIDATE_JSON


# ---------------------------------------------------------------------------
# Cycle step results
# ---------------------------------------------------------------------------


def _step(name: str, status: str, **kwargs) -> Dict[str, Any]:
    """Build a step result dict."""
    return {"step": name, "status": status, **kwargs}


# ---------------------------------------------------------------------------
# Cycle orchestration
# ---------------------------------------------------------------------------


class ResearchCycle:
    """Orchestrates a single research cycle from candidate generation through
    action execution."""

    def __init__(
        self,
        *,
        mock: bool = False,
        evaluate: bool = False,
        candidate_path: Optional[str] = None,
    ):
        self.mock = mock
        self.evaluate = evaluate
        self.candidate_path = Path(candidate_path) if candidate_path else None
        self.run_id = _run_id()
        self.timestamp = _now_iso()
        self.steps: List[Dict[str, Any]] = []
        self.final_state: Optional[str] = None
        self.candidate_id: Optional[str] = None
        self.family: Optional[str] = None

    def run(self) -> Dict[str, Any]:
        """Execute one full research cycle."""
        print(f"\n{'=' * 60}")
        print("  LLM Research Cycle v0.6.5")
        print(f"  Run ID: {self.run_id}")
        print(f"  Mode: {'MOCK' if self.mock else 'LIVE'}")
        print(f"{'=' * 60}")

        # --- Step 1: Generate or read candidate ---
        print("\n[Step 1/6] Candidate generation ...")
        self._step_generate()

        if self.candidate_id is None:
            return self._summary("generate_failed")

        # --- Step 2: Validate ---
        print("\n[Step 2/6] Schema validation ...")
        self._step_validate()

        if self.final_state == "validation_failed":
            return self._summary("validation_failed")

        # --- Step 3: Evaluate 1300d ---
        print("\n[Step 3/6] 1300d evaluation ...")
        self._step_evaluate()

        # --- Step 4: Scorecard ---
        print("\n[Step 4/6] Scorecard ...")
        self._step_scorecard()

        # --- Step 5: Review action ---
        print("\n[Step 5/6] Review action ...")
        self._step_review()

        if self.final_state and self.final_state.endswith("_failed"):
            return self._summary(self.final_state)

        # --- Step 6: Execute action ---
        print("\n[Step 6/6] Execute action ...")
        self._step_execute()

        return self._summary(self.final_state or "completed")

    def _step_generate(self) -> None:
        """Generate a new candidate or use existing."""
        if self.candidate_path and self.candidate_path.exists():
            # Use existing candidate
            try:
                spec = json.loads(self.candidate_path.read_text(encoding="utf-8"))
                self.candidate_id = spec.get("experiment_id")
                self.family = spec.get("strategy", "channel_breakout")
                print(f"  Using existing candidate: {self.candidate_path.name}")
                print(f"  ID: {self.candidate_id}  Family: {self.family}")
                self.steps.append(
                    _step(
                        "generate",
                        "existing",
                        path=str(self.candidate_path),
                        experiment_id=self.candidate_id,
                        family=self.family,
                    )
                )
                return
            except (json.JSONDecodeError, IOError) as e:
                print(f"  [ERROR] Cannot read candidate: {e}")
                self.final_state = "generate_failed"
                self.steps.append(_step("generate", "error", error=str(e)))
                return

        # Generate via LLM
        from scripts.generate_candidate import generate_candidate
        from scripts.llm_client import call_llm as real_call_llm

        llm_fn = _mock_llm_response if self.mock else real_call_llm

        with patch("scripts.generate_candidate.call_llm", llm_fn):
            try:
                result = generate_candidate(dry_run=False)
                if result["status"] == "accepted":
                    self.candidate_id = result["experiment_id"]
                    self.candidate_path = result.get("candidate_path")
                    # Read family from generated candidate
                    if self.candidate_path and Path(self.candidate_path).exists():
                        try:
                            gen_spec = json.loads(
                                Path(self.candidate_path).read_text(encoding="utf-8")
                            )
                            self.family = gen_spec.get("strategy", "channel_breakout")
                        except (json.JSONDecodeError, IOError):
                            self.family = "channel_breakout"
                    else:
                        self.family = "channel_breakout"
                    print(f"  Generated candidate: {self.candidate_id}  Family: {self.family}")
                    self.steps.append(
                        _step(
                            "generate",
                            "ok",
                            experiment_id=self.candidate_id,
                            family=self.family,
                            path=str(self.candidate_path),
                        )
                    )
                else:
                    print(f"  [FAIL] Generation rejected: {result.get('errors', [])}")
                    self.final_state = "generate_failed"
                    self.steps.append(
                        _step("generate", "rejected", errors=result.get("errors", []))
                    )
            except Exception as e:
                print(f"  [ERROR] Generation failed: {e}")
                self.final_state = "generate_failed"
                self.steps.append(_step("generate", "error", error=str(e)))

    def _step_validate(self) -> None:
        """Validate the candidate against the v0.8 family-aware schema.

        Rejects candidates with unknown/unregistered families —
        prevents 'unknown family' candidates from entering the pipeline.
        """
        cid = self.candidate_id
        if not cid:
            self.final_state = "validation_failed"
            self.steps.append(_step("validate", "error", error="No candidate ID"))
            return

        # Try loading the candidate spec
        cand_path = self.candidate_path
        if not cand_path or not cand_path.exists():
            cand_path = LLM_CANDIDATES_DIR / f"{cid}.json"
        if not cand_path.exists():
            print(f"  [ERROR] Candidate not found: {cand_path}")
            self.final_state = "validation_failed"
            self.steps.append(_step("validate", "error", error=f"Not found: {cand_path}"))
            return

        spec = json.loads(cand_path.read_text(encoding="utf-8"))
        from scripts.validate_candidate_v08 import validate_candidate

        errors = validate_candidate(spec, current_path=cand_path, check_uniqueness=False)
        if errors:
            print(f"  [FAIL] Validation errors: {errors}")
            self.final_state = "validation_failed"
            self.steps.append(_step("validate", "failed", errors=errors))
        else:
            print("  [OK] Schema valid")
            self.steps.append(_step("validate", "ok"))

    def _step_evaluate(self) -> None:
        """Evaluate the candidate via evaluate_candidate.py."""
        cid = self.candidate_id
        if not cid:
            self.final_state = "evaluate_failed"
            self.steps.append(_step("evaluate", "error", error="No candidate ID"))
            return

        cand_path = self.candidate_path
        if not cand_path or not cand_path.exists():
            cand_path = LLM_CANDIDATES_DIR / f"{cid}.json"
        if not cand_path.exists():
            self.final_state = "evaluate_failed"
            self.steps.append(_step("evaluate", "error", error=f"Candidate not found: {cand_path}"))
            return

        # Use dry_run in mock mode, real oracle in live mode
        is_dry = self.mock or not self.evaluate
        print(f"  Mode: {'dry-run (mock)' if is_dry else 'LIVE oracle'}")

        from scripts.evaluate_candidate import evaluate_candidate

        try:
            t0 = time.time()
            state = evaluate_candidate(cand_path, dry_run=is_dry, force=True)
            elapsed = time.time() - t0

            verdict = state.get("final_verdict", "?")
            print(f"  Verdict: {verdict} ({elapsed:.1f}s)")

            # In mock mode, write a minimal evaluation state so review step can find it
            if is_dry and cid:
                LLM_SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
                ev_state = {
                    "evaluation_version": "v0.3",
                    "experiment_id": cid,
                    "state": "requires_2600d",
                    "final_verdict": "requires_2600d",
                    "history": [
                        {
                            "stage": "1300d",
                            "verdict": "requires_2600d",
                            "scorecard": "dry_run_simulated",
                        },
                    ],
                }
                ev_path = LLM_SCORECARDS_DIR / f"{cid}_evaluation.json"
                ev_path.write_text(json.dumps(ev_state, indent=2), encoding="utf-8")
                print(f"  Mock evaluation state: {ev_path.name}")

            self.steps.append(
                _step("evaluate", "ok", verdict=verdict, elapsed=round(elapsed, 1), dry_run=is_dry)
            )
        except Exception as e:
            print(f"  [ERROR] Evaluation failed: {e}")
            self.final_state = "evaluate_failed"
            self.steps.append(_step("evaluate", "error", error=str(e)))

    def _step_scorecard(self) -> None:
        """Verify that a scorecard was created."""
        cid = self.candidate_id
        if not cid:
            self.steps.append(_step("scorecard", "error", error="No candidate ID"))
            return

        # Check for scorecard
        found = None
        for stage in ("2600d", "1300d"):
            sc_path = LLM_SCORECARDS_DIR / f"{cid}_{stage}_scorecard.json"
            if sc_path.exists():
                found = str(sc_path)
                break
        if not found:
            # Check evaluation state
            ev_path = LLM_SCORECARDS_DIR / f"{cid}_evaluation.json"
            if ev_path.exists():
                found = str(ev_path)

        if found:
            print(f"  Scorecard: {found}")
            self.steps.append(_step("scorecard", "ok", path=found))
        else:
            print("  [WARN] No scorecard file found (expected in mock mode)")
            self.steps.append(
                _step("scorecard", "not_found", note="Scorecard not written in dry-run mock mode")
            )

    def _step_review(self) -> None:
        """Review the candidate via review_candidate.py.

        In mock mode, the module-level ``_mock_llm_response`` handles both
        generator and reviewer roles.  The caller (test or CLI) is responsible
        for providing the right mock — this method just calls LLM directly.
        """
        cid = self.candidate_id
        if not cid:
            self.final_state = "review_failed"
            self.steps.append(_step("review", "error", error="No candidate ID"))
            return

        # In mock mode, make _mock_llm_response return the default action.
        # Tests can override this via patching _mock_llm_response or call_llm.
        from scripts.llm_client import call_llm
        from scripts.review_candidate import review_candidate

        # When self.mock, replace call_llm with the module-level mock
        llm_fn = _mock_llm_response if self.mock else call_llm

        with patch("scripts.review_candidate.call_llm", llm_fn):
            try:
                result = review_candidate(cid, dry_run=False)
                if result["status"] == "accepted":
                    action = result.get("action", {}).get("action", "?")
                    print(f"  Action: {action}")
                    self.steps.append(
                        _step("review", "ok", action=action, path=result.get("action_path"))
                    )
                elif result["status"] == "rejected":
                    print(f"  [FAIL] Review rejected: {result.get('errors', [])}")
                    self.final_state = "review_failed"
                    self.steps.append(_step("review", "rejected", errors=result.get("errors", [])))
                else:
                    print(f"  [ERROR] Review failed: {result.get('errors', [])}")
                    self.final_state = "review_failed"
                    self.steps.append(_step("review", "error", errors=result.get("errors", [])))
            except Exception as e:
                print(f"  [ERROR] Review call failed: {e}")
                self.final_state = "review_failed"
                self.steps.append(_step("review", "error", error=str(e)))

    def _step_execute(self) -> None:
        """Execute the action from the review step."""

        # Find the action that was proposed in the review step
        review_step = None
        for s in self.steps:
            if s.get("step") == "review" and s.get("status") == "ok":
                review_step = s
                break

        if not review_step:
            self.final_state = "execute_failed"
            self.steps.append(_step("execute", "error", error="No successful review step"))
            return

        action_path = review_step.get("path")
        if not action_path or not Path(action_path).exists():
            self.final_state = "execute_failed"
            self.steps.append(
                _step("execute", "error", error=f"Action file not found: {action_path}")
            )
            return

        try:
            from scripts.execute_action import execute_action_from_path

            result = execute_action_from_path(Path(action_path))
            status = result.get("status", "?")
            act = result.get("action", "?")
            print(f"  Action: {act} → {status}")

            if status == "executed":
                if act == "fork":
                    fork_id = result.get("experiment_id")
                    print(f"  Fork generated: {fork_id}")
                    self.steps.append(
                        _step(
                            "execute",
                            "ok",
                            action=act,
                            fork_id=fork_id,
                            path=result.get("candidate_path"),
                        )
                    )
                elif act == "kill":
                    self.steps.append(
                        _step("execute", "ok", action=act, state_path=result.get("state_path"))
                    )
                elif act == "create":
                    self.steps.append(
                        _step(
                            "execute",
                            "ok",
                            action=act,
                            request_path=result.get("create_request_path"),
                        )
                    )
                elif act == "promote_review":
                    self.steps.append(
                        _step("execute", "ok", action=act, packet_path=result.get("packet_path"))
                    )
                elif act == "stable":
                    self.steps.append(
                        _step("execute", "ok", action=act, state_path=result.get("state_path"))
                    )
                self.final_state = f"executed_{act}"
            else:
                print(f"  [FAIL] Execute {status}: {result.get('error', '?')}")
                self.final_state = "execute_failed"
                self.steps.append(
                    _step("execute", status, error=result.get("error", "?"), action=act)
                )
        except Exception as e:
            print(f"  [ERROR] Execute failed: {e}")
            self.final_state = "execute_failed"
            self.steps.append(_step("execute", "error", error=str(e)))

    def _summary(self, final_state: str) -> Dict[str, Any]:
        """Build and write the run summary."""
        self.final_state = final_state

        summary = {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "mode": "mock" if self.mock else "live",
            "candidate_id": self.candidate_id,
            "family": self.family,
            "final_state": final_state,
            "steps": self.steps,
        }

        LLM_RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = LLM_RUNS_DIR / f"{self.run_id}.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

        print(f"\n{'=' * 60}")
        print("  Cycle complete")
        print(f"  Run ID:   {self.run_id}")
        print(f"  State:    {final_state}")
        print(f"  Steps:    {len(self.steps)}")
        for s in self.steps:
            status_icon = "[OK]" if s.get("status") == "ok" else "[--]"
            print(f"    {status_icon} {s['step']}: {s.get('status', '?')}")
        print(f"  Summary:  {path}")
        print(f"{'=' * 60}\n")

        return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="LLM Research Cycle Runner v0.6.5 — one complete autonomous cycle"
    )
    parser.add_argument(
        "--candidate",
        type=str,
        default=None,
        help="Start from an existing candidate file instead of generating",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock LLM responses + dry-run evaluation (no API key needed)",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run real oracle evaluation (requires data, slow)",
    )
    args = parser.parse_args()

    cycle = ResearchCycle(
        mock=args.mock,
        evaluate=args.evaluate,
        candidate_path=args.candidate,
    )
    summary = cycle.run()

    success_states = {
        "completed",
        "executed_kill",
        "executed_fork",
        "executed_create",
        "executed_stable",
        "executed_promote_review",
    }
    if summary.get("final_state") in success_states:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
