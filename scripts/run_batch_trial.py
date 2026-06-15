#!/usr/bin/env python3
"""Batch Trial Runner v0.8.5 — run N complete research cycles + meta-review.

Each cycle: generate → validate → evaluate → scorecard → review → execute.
Mock action cycles through fork → kill → create per family.
Multi-family mode distributes cycles across registered families.

Usage:
    # Default: 6 cycles (2 fork + 2 kill + 2 create), channel_breakout only
    uv run python scripts/run_batch_trial.py

    # Multi-family batch: distributes across all registered families
    uv run python scripts/run_batch_trial.py --multi-family --cycles 10

    # Custom families
    uv run python scripts/run_batch_trial.py --multi-family \
        --families channel_breakout,volatility_filtered_breakout --cycles 8

    # Quiet mode (only show summary)
    uv run python scripts/run_batch_trial.py --quiet

    # Multi-family quiet
    uv run python scripts/run_batch_trial.py --multi-family --cycles 10 --quiet
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
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
META_REVIEWS_DIR = PROJECT_DIR / "research_workspace" / "meta_reviews"
LLM_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "llm_scorecards"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "actions"
REJECTED_ACTIONS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "rejected_actions"
CANDIDATE_STATES_DIR = PROJECT_DIR / "research_workspace" / "candidate_states"
CREATE_REQUESTS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "create_requests"
PROMOTION_REVIEWS_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "promotion_reviews"

# ---------------------------------------------------------------------------
# Action type cycle
# ---------------------------------------------------------------------------

MOCK_ACTIONS: List[str] = ["fork", "kill", "create"]

# ---------------------------------------------------------------------------
# Channel Breakout templates (original, unchanged)
# ---------------------------------------------------------------------------

CB_ACTION_TEMPLATES = {
    "fork": json.dumps({
        "action": "fork",
        "source_candidate_id": "PLACEHOLDER",
        "target_family": "channel_breakout",
        "rationale": "Batch trial fork: adjusting entry_lookback to test sensitivity reduction while maintaining regime conviction across multiple regimes.",
        "allowed_change": {"entry_lookback": 350, "min_hold_bars": 400},
        "risk_note": "Batch trial fork.",
    }),
    "kill": json.dumps({
        "action": "kill",
        "source_candidate_id": "PLACEHOLDER",
        "target_family": "channel_breakout",
        "rationale": "Batch trial kill: candidate disqualified, no viable fork path from this direction based on evaluation results.",
        "risk_note": "Batch trial kill.",
    }),
    "create": json.dumps({
        "action": "create",
        "source_candidate_id": "baseline",
        "target_family": "channel_breakout",
        "rationale": "Batch trial create: starting fresh direction after batch evaluation of current candidate.",
        "risk_note": "Batch trial create.",
    }),
}

CB_CANDIDATE_TEMPLATE = json.dumps({
    "parent_id": "channel_breakout_v2_1_balanced",
    "description": "Batch trial candidate",
    "hypothesis": "Batch trial: testing adjusted entry_lookback to evaluate systematic behavior across multiple research cycles.",
    "expected_behavior_change": "Trade count adjusts proportionally. DD remains within schema bounds across batch.",
    "params": {
        "strategy_type": "regime_permission_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "bull": {
            "candidate": "batch_bull",
            "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": False},
            "permission": {"allow_long": True, "allow_short": False,
                           "close_below_ema_disables_long": True,
                           "ema_fast": 50, "consecutive_below_ema_days": 3},
        },
        "bear": {
            "candidate": "batch_bear",
            "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True},
        },
        "neutral": {
            "candidate": "batch_neutral",
            "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True,
                           "directional_only": True, "ema_fast": 50,
                           "ema_slope_days": 5},
        },
    },
})

# ---------------------------------------------------------------------------
# Volatility Filtered Breakout templates (v0.8.5)
# ---------------------------------------------------------------------------

VFB_ACTION_TEMPLATES = {
    "fork": json.dumps({
        "action": "fork",
        "source_candidate_id": "PLACEHOLDER",
        "target_family": "volatility_filtered_breakout",
        "rationale": "Batch trial fork: adjusting volatility filter parameters to test effectiveness of exclusion modes across volatility regimes.",
        "allowed_change": {"volatility_filter": {"lookback": 500, "mode": "exclude_low"}},
        "risk_note": "Batch trial fork.",
    }),
    "kill": json.dumps({
        "action": "kill",
        "source_candidate_id": "PLACEHOLDER",
        "target_family": "volatility_filtered_breakout",
        "rationale": "Batch trial kill: volatility filtered candidate disqualified based on evaluation results, no viable fork path from this direction.",
        "risk_note": "Batch trial kill.",
    }),
    "create": json.dumps({
        "action": "create",
        "source_candidate_id": "baseline",
        "target_family": "volatility_filtered_breakout",
        "rationale": "Batch trial create: starting fresh volatility_filtered direction after evaluation cycle.",
        "risk_note": "Batch trial create.",
    }),
}

VFB_CANDIDATE_TEMPLATE = json.dumps({
    "parent_id": "channel_breakout_v2_1_balanced",
    "description": "Batch trial volatility filtered candidate",
    "hypothesis": "Adding volatility filter (exclude_extreme mode, lookback=288) reduces false breakout entries during high-volatility regimes while preserving trend capture in normal volatility conditions.",
    "expected_behavior_change": "Trade count drops 15-25% due to filtered extreme volatility entries. DD improves. Win rate rises above 45%.",
    "params": {
        "strategy_type": "volatility_filtered_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "volatility_filter": {
            "enabled": True,
            "lookback": 288,
            "mode": "exclude_extreme",
            "low_quantile": 0.05,
            "high_quantile": 0.95,
        },
        "bull": {
            "candidate": "batch_bull_vf",
            "strategy_params": {"entry_lookback": 240, "min_hold_bars": 48,
                                "enable_long": True, "enable_short": False},
            "permission": {"allow_long": True, "allow_short": False,
                           "close_below_ema_disables_long": True,
                           "ema_fast": 50, "consecutive_below_ema_days": 3},
        },
        "bear": {
            "candidate": "batch_bear_vf",
            "strategy_params": {"entry_lookback": 240, "min_hold_bars": 48,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True},
        },
        "neutral": {
            "candidate": "batch_neutral_vf",
            "strategy_params": {"entry_lookback": 240, "min_hold_bars": 48,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True,
                           "directional_only": True, "ema_fast": 50,
                           "ema_slope_days": 5},
        },
    },
})

# ---------------------------------------------------------------------------
# Family template dispatch tables
# ---------------------------------------------------------------------------

EXIT_ACTION_TEMPLATES = {
    "fork": json.dumps({
        "action": "fork",
        "source_candidate_id": "PLACEHOLDER",
        "target_family": "exit_logic_variant",
        "rationale": "Batch trial fork: adjusting exit logic parameters to test take-profit and stop-loss sensitivity across market regimes.",
        "allowed_change": {"exit_logic": {"take_profit_pct": 0.08, "stop_loss_pct": 0.12, "max_hold_bars": 500}},
        "risk_note": "Batch trial fork.",
    }),
    "kill": json.dumps({
        "action": "kill",
        "source_candidate_id": "PLACEHOLDER",
        "target_family": "exit_logic_variant",
        "rationale": "Batch trial kill: exit logic candidate disqualified based on evaluation results, no viable fork path.",
        "risk_note": "Batch trial kill.",
    }),
    "create": json.dumps({
        "action": "create",
        "source_candidate_id": "baseline",
        "target_family": "exit_logic_variant",
        "rationale": "Batch trial create: starting fresh exit_logic_variant direction after evaluation cycle.",
        "risk_note": "Batch trial create.",
    }),
}

EXIT_CANDIDATE_TEMPLATE = json.dumps({
    "parent_id": "channel_breakout_v2_1_balanced",
    "description": "Batch trial exit logic candidate",
    "hypothesis": "Adding configurable exit logic (take_profit=5%%, stop_loss=10%%, max_hold=720 bars, trailing) improves risk-adjusted returns by capping downside and locking in gains during trend reversals.",
    "expected_behavior_change": "Trade count increases due to TP/SL exits. Max DD drops below 25%%. Win rate rises above 50%%.",
    "params": {
        "strategy_type": "exit_logic_channel_breakout",
        "regime_change_policy": "permission_based",
        "regime_filter": {"fast_days": 50, "slow_days": 200},
        "exit_logic": {
            "exit_lookback": 288,
            "take_profit_pct": 0.05,
            "stop_loss_pct": 0.10,
            "max_hold_bars": 720,
            "trailing_stop": True,
        },
        "bull": {
            "candidate": "batch_bull_exit",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": False},
            "permission": {"allow_long": True, "allow_short": False,
                           "close_below_ema_disables_long": True,
                           "ema_fast": 50, "consecutive_below_ema_days": 3},
        },
        "bear": {
            "candidate": "batch_bear_exit",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True},
        },
        "neutral": {
            "candidate": "batch_neutral_exit",
            "strategy_params": {"entry_lookback": 375, "min_hold_bars": 432,
                                "enable_long": True, "enable_short": True},
            "permission": {"allow_long": True, "allow_short": True,
                           "directional_only": True, "ema_fast": 50,
                           "ema_slope_days": 5},
        },
    },
})

FAMILY_ACTION_TEMPLATES: Dict[str, Dict[str, str]] = {
    "channel_breakout": CB_ACTION_TEMPLATES,
    "volatility_filtered_breakout": VFB_ACTION_TEMPLATES,
    "exit_logic_variant": EXIT_ACTION_TEMPLATES,
}

FAMILY_CANDIDATE_TEMPLATES: Dict[str, str] = {
    "channel_breakout": CB_CANDIDATE_TEMPLATE,
    "volatility_filtered_breakout": VFB_CANDIDATE_TEMPLATE,
    "exit_logic_variant": EXIT_CANDIDATE_TEMPLATE,
}


def _get_default_families() -> List[str]:
    """Get list of registered families from the family registry."""
    try:
        from scripts.family_registry import list_families
        return list_families()
    except Exception:
        return ["channel_breakout"]


# ---------------------------------------------------------------------------
# Mock action cycler (single family, original)
# ---------------------------------------------------------------------------


class MockActionCycler:
    """Cycles through mock actions for single-family batch."""

    def __init__(self):
        self.cycle_count = 0

    def reset(self):
        self.cycle_count = 0

    def next_action(self) -> str:
        """Return the next action type in the cycle."""
        action_type = MOCK_ACTIONS[self.cycle_count % len(MOCK_ACTIONS)]
        self.cycle_count += 1
        return action_type

    def mock_fn(self, system_prompt: str, user_message: str) -> str:
        """Return a canned response based on system_prompt and current cycle."""
        sp = system_prompt.lower()
        if "reviewer" in sp:
            action_type = MOCK_ACTIONS[(self.cycle_count - 1) % len(MOCK_ACTIONS)]
            template = CB_ACTION_TEMPLATES[action_type]
            cid = _extract_cid_from_message(user_message)
            return template.replace("PLACEHOLDER", cid)
        else:
            return CB_CANDIDATE_TEMPLATE


# ---------------------------------------------------------------------------
# Multi-family action cycler (v0.8.5)
# ---------------------------------------------------------------------------


class MultiFamilyActionCycler:
    """Cycles through families and actions for multi-family batch.

    Distribution: round-robin across families, action cycle within each.
    Example with families=[CB, VFB]:
        Cycle 1: CB fork
        Cycle 2: VFB fork
        Cycle 3: CB kill
        Cycle 4: VFB kill
        Cycle 5: CB create
        Cycle 6: VFB create
        Cycle 7: CB fork
        ...
    """

    def __init__(self, families: Optional[List[str]] = None):
        self.families = families or _get_default_families()
        self.action_types = MOCK_ACTIONS
        self.cycle_count = 0
        self.last_family: Optional[str] = None
        self.last_action: Optional[str] = None

    @property
    def n_families(self) -> int:
        return len(self.families)

    def next_action(self) -> str:
        """Prepare for the next cycle and return its action type."""
        family_idx = self.cycle_count % self.n_families
        action_idx = (self.cycle_count // self.n_families) % len(self.action_types)
        self.last_family = self.families[family_idx]
        self.last_action = self.action_types[action_idx]
        self.cycle_count += 1
        return self.last_action

    def current_family(self) -> str:
        """Return the family for the current (most recently prepared) cycle."""
        return self.last_family or self.families[0]

    def mock_fn(self, system_prompt: str, user_message: str) -> str:
        """Return a canned response based on current family/action."""
        sp = system_prompt.lower()
        if "reviewer" in sp:
            templates = FAMILY_ACTION_TEMPLATES.get(self.current_family(), CB_ACTION_TEMPLATES)
            template = templates.get(self.last_action or "fork", CB_ACTION_TEMPLATES["fork"])
            cid = _extract_cid_from_message(user_message)
            return template.replace("PLACEHOLDER", cid)
        else:
            return FAMILY_CANDIDATE_TEMPLATES.get(self.current_family(), CB_CANDIDATE_TEMPLATE)


def _extract_cid_from_message(user_message: str) -> str:
    """Extract experiment ID from an LLM context message."""
    for line in user_message.split("\n"):
        if "ID:" in line:
            for word in line.split():
                word = word.strip(".,:;!?")
                if word.startswith("exp_") and len(word) > 4 and word[4:].isdigit():
                    return word
    return "exp_unknown"


# ---------------------------------------------------------------------------
# Batch trial runner
# ---------------------------------------------------------------------------


def run_batch(n_cycles: int = 6, quiet: bool = False) -> Dict[str, Any]:
    """Run N research cycles (single family, backward compatible).

    Returns batch summary dict.
    """
    cycler = MockActionCycler()
    results: List[Dict[str, Any]] = []

    print(f"\n{'='*60}")
    print(f"  Batch Trial v0.7.5 (single-family)")
    print(f"  Cycles: {n_cycles}")
    print(f"  Actions: {MOCK_ACTIONS} (cycling)")
    print(f"{'='*60}\n")

    from scripts.run_llm_research_cycle import ResearchCycle

    for i in range(n_cycles):
        action_type = cycler.next_action()
        print(f"\n{'─'*60}")
        print(f"  Cycle {i+1}/{n_cycles} — Action: {action_type}")
        print(f"{'─'*60}")

        t0 = time.time()
        cycle = ResearchCycle(mock=True, evaluate=False)

        with patch("scripts.run_llm_research_cycle._mock_llm_response", cycler.mock_fn):
            summary = cycle.run()

        elapsed = time.time() - t0
        steps_ok = sum(1 for s in summary.get("steps", []) if s.get("status") == "ok")
        total_steps = len(summary.get("steps", []))

        result = {
            "cycle": i + 1,
            "action": action_type,
            "family": "channel_breakout",
            "candidate_id": summary.get("candidate_id"),
            "final_state": summary.get("final_state"),
            "steps_ok": steps_ok,
            "steps_total": total_steps,
            "elapsed": round(elapsed, 1),
            "run_id": summary.get("run_id"),
        }
        results.append(result)

        status_icon = "[OK]" if summary.get("final_state", "").startswith("executed_") else "[FAIL]"
        if not quiet:
            print(f"  {status_icon} Cycle {i+1}: {summary.get('final_state', '?')} "
                  f"({steps_ok}/{total_steps} steps, {elapsed:.1f}s)")

    return _finalize_batch(results, n_cycles, quiet=quiet)


def run_multi_family_batch(
    n_cycles: int = 10,
    families: Optional[List[str]] = None,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Run N research cycles distributed across multiple families.

    Each family gets at least 3 cycles minimum.
    Returns batch summary dict with per-family stats.
    """
    families = families or _get_default_families()
    cycler = MultiFamilyActionCycler(families)
    results: List[Dict[str, Any]] = []

    # Validate minimum cycles: at least 3 per family
    min_cycles = max(n_cycles, len(families) * 3)

    print(f"\n{'='*60}")
    print(f"  Batch Trial v0.8.5 (multi-family)")
    print(f"  Cycles: {min_cycles}")
    print(f"  Families: {families}")
    print(f"  Actions: {MOCK_ACTIONS} (cycling per family)")
    print(f"{'='*60}\n")

    from scripts.run_llm_research_cycle import ResearchCycle

    for i in range(min_cycles):
        action_type = cycler.next_action()
        current_family = cycler.current_family()

        print(f"\n{'─'*60}")
        print(f"  Cycle {i+1}/{min_cycles} — Family: {current_family}, Action: {action_type}")
        print(f"{'─'*60}")

        t0 = time.time()
        cycle = ResearchCycle(mock=True, evaluate=False)

        with patch("scripts.run_llm_research_cycle._mock_llm_response", cycler.mock_fn):
            summary = cycle.run()

        elapsed = time.time() - t0
        steps_ok = sum(1 for s in summary.get("steps", []) if s.get("status") == "ok")
        total_steps = len(summary.get("steps", []))

        result = {
            "cycle": i + 1,
            "action": action_type,
            "family": current_family,
            "candidate_id": summary.get("candidate_id"),
            "final_state": summary.get("final_state"),
            "steps_ok": steps_ok,
            "steps_total": total_steps,
            "elapsed": round(elapsed, 1),
            "run_id": summary.get("run_id"),
        }
        results.append(result)

        status_icon = "[OK]" if summary.get("final_state", "").startswith("executed_") else "[FAIL]"
        if not quiet:
            print(f"  {status_icon} Cycle {i+1}: [{current_family}] {summary.get('final_state', '?')} "
                  f"({steps_ok}/{total_steps} steps, {elapsed:.1f}s)")

    return _finalize_batch(results, min_cycles, multi_family=True, families=families, quiet=quiet)


# ---------------------------------------------------------------------------
# Batch finalization (shared between single and multi-family)
# ---------------------------------------------------------------------------


def _finalize_batch(
    results: List[Dict[str, Any]],
    n_cycles: int,
    *,
    multi_family: bool = False,
    families: Optional[List[str]] = None,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Build summary, print batch results, and run meta-review."""
    # --- Build summary ---
    batch_summary = _build_batch_summary(results, n_cycles, multi_family=multi_family)

    # --- Print batch summary ---
    print(f"\n{'='*60}")
    print(f"  Batch Summary")
    print(f"{'='*60}")
    print(f"  Cycles: {batch_summary['cycles_completed']}/{n_cycles}")
    print(f"  Success: {batch_summary['cycles_succeeded']}")
    print(f"  Failed:  {batch_summary['cycles_failed']}")
    print(f"  Actions: {json.dumps(batch_summary['action_distribution'])}")
    if multi_family and "family_distribution" in batch_summary:
        print(f"  Families:")
        for fam, count in batch_summary["family_distribution"].items():
            print(f"    {fam}: {count} cycles")
    print(f"  Total time: {batch_summary['total_elapsed']:.1f}s")
    print()

    for r in results:
        icon = "[OK]" if r["final_state"].startswith("executed_") else "[--]"
        fam_tag = f"[{r['family']}] " if multi_family else ""
        print(f"  {icon} {fam_tag}Cycle {r['cycle']}: {r['candidate_id']} "
              f"→ {r['action']} → {r['final_state']} ({r['elapsed']:.1f}s)")

    # --- Meta-review ---
    print(f"\n{'─'*60}")
    print(f"  Running meta-review over batch history ...")
    print(f"{'─'*60}")
    _run_meta_review(quiet=quiet)

    # --- Write batch summary ---
    batch_path = _write_batch_summary(batch_summary)
    print(f"\n  Batch summary: {batch_path}")

    return batch_summary


def _build_batch_summary(
    results: List[Dict[str, Any]],
    n_cycles: int,
    *,
    multi_family: bool = False,
) -> Dict[str, Any]:
    """Build aggregate batch summary from per-cycle results."""
    succeeded = [r for r in results if r["final_state"].startswith("executed_")]
    failed = [r for r in results if not r["final_state"].startswith("executed_")]

    action_dist: Dict[str, int] = {}
    for r in results:
        action_dist[r["action"]] = action_dist.get(r["action"], 0) + 1

    summary: Dict[str, Any] = {
        "batch_version": "v0.8.5" if multi_family else "v0.7.5",
        "multi_family": multi_family,
        "timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycles_requested": n_cycles,
        "cycles_completed": len(results),
        "cycles_succeeded": len(succeeded),
        "cycles_failed": len(failed),
        "action_distribution": action_dist,
        "total_elapsed": sum(r.get("elapsed", 0) for r in results),
        "per_cycle": results,
    }

    if multi_family:
        # Family distribution
        family_dist: Dict[str, int] = {}
        family_success: Dict[str, int] = {}
        for r in results:
            fam = r.get("family", "unknown")
            family_dist[fam] = family_dist.get(fam, 0) + 1
            if r["final_state"].startswith("executed_"):
                family_success[fam] = family_success.get(fam, 0) + 1
        summary["family_distribution"] = family_dist
        summary["family_success"] = family_success

        # Per-family action breakdown
        family_actions: Dict[str, Dict[str, int]] = {}
        for r in results:
            fam = r.get("family", "unknown")
            if fam not in family_actions:
                family_actions[fam] = {}
            act = r["action"]
            family_actions[fam][act] = family_actions[fam].get(act, 0) + 1
        summary["family_actions"] = family_actions

        # Verify each family has at least 3 cycles
        for fam, count in family_dist.items():
            if count < 3:
                summary.setdefault("warnings", []).append(
                    f"{fam}: only {count} cycles (min 3 recommended)"
                )

    return summary


def _write_batch_summary(batch_summary: Dict[str, Any]) -> Path:
    """Write batch summary to llm_runs/."""
    LLM_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).strftime("%Y%m%d_%H%M%S")
    path = LLM_RUNS_DIR / f"batch_{ts}.json"
    path.write_text(json.dumps(batch_summary, indent=2, default=str), encoding="utf-8")
    return path


def _run_meta_review(quiet: bool = False) -> None:
    """Run meta-review over accumulated batch history."""
    from scripts.meta_review import run_meta_review

    from scripts.meta_review import _next_review_id as _meta_next_id
    mock_rid = _meta_next_id()
    mock_review = json.dumps({
        "review_id": mock_rid,
        "window": {"runs_analyzed": 10, "candidates_analyzed": 6},
        "findings": [
            {
                "type": "failure_pattern",
                "severity": "medium",
                "summary": "Batch trial reveals consistent fork chain pattern across families: most forks only adjust primary parameters without modifying regime_filter.",
                "evidence": ["Cycle fork chain: multiple candidates with same regime_filter 50/200 across both families"],
            },
            {
                "type": "success_pattern",
                "severity": "low",
                "summary": "All candidates passed schema validation and evaluation completed without errors.",
                "evidence": ["All cycles completed evaluate step successfully"],
            },
        ],
        "recommendations": [
            {
                "target": "search_space",
                "action": "expand",
                "proposal": "Consider allowing regime_filter variations in addition to primary parameter changes for fork actions across both families.",
                "rationale": "Current forks only touch entry_lookback or volatility_filter; regime_filter remains at baseline 50/200 in all candidates.",
            },
        ],
        "contract_changes": [],
        "prompt_changes": [],
        "requires_human_review": True,
    })

    with patch("scripts.meta_review.call_llm", lambda **kw: mock_review):
        result = run_meta_review(n_runs=50, dry_run=False)
        if result["status"] == "ok":
            if not quiet:
                print(f"  [OK] Meta-review: {result['review_id']}")
        else:
            print(f"  [WARN] Meta-review: {result.get('status', '?')} "
                  f" — {result.get('errors', '?')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Batch Trial Runner v0.8.5 — N research cycles + meta-review"
    )
    parser.add_argument(
        "--cycles", type=int, default=6,
        help="Number of research cycles (default: 6 single-family, min 3 per family in multi-family mode)",
    )
    parser.add_argument(
        "--multi-family", action="store_true",
        help="Distribute cycles across multiple registered families",
    )
    parser.add_argument(
        "--families", type=str, default=None,
        help="Comma-separated list of families (default: all registered)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only show summary, suppress per-cycle detail",
    )
    args = parser.parse_args()

    families: Optional[List[str]] = None
    if args.families:
        families = [f.strip() for f in args.families.split(",")]
        for f in families:
            from scripts.family_registry import is_valid
            if not is_valid(f):
                print(f"ERROR: Unknown family '{f}'")
                sys.exit(1)

    if args.multi_family:
        families = families or _get_default_families()
        if len(families) < 2:
            print(f"WARNING: Only 1 family available ({families[0]}). "
                  f"Use single-family mode or register more families.")
        min_cycles = max(args.cycles, len(families) * 3)
        print(f"\n  Multi-family mode: {families}")
        print(f"  Cycles: {min_cycles} ({args.cycles} requested, min {len(families)}x3 per family)")
        batch_summary = run_multi_family_batch(
            n_cycles=args.cycles,
            families=families,
            quiet=args.quiet,
        )
    else:
        if args.cycles < 3:
            print(f"ERROR: Minimum 3 cycles required (got {args.cycles})")
            sys.exit(1)
        batch_summary = run_batch(n_cycles=args.cycles, quiet=args.quiet)

    if batch_summary["cycles_failed"] > 0:
        print(f"\n  Batch completed with {batch_summary['cycles_failed']} failure(s).")
    else:
        print(f"\n  Batch completed: all {batch_summary['cycles_succeeded']} cycles successful.")

    # Print warnings
    for w in batch_summary.get("warnings", []):
        print(f"  [WARN] {w}")

    sys.exit(0 if batch_summary["cycles_failed"] == 0 else 1)


if __name__ == "__main__":
    main()
