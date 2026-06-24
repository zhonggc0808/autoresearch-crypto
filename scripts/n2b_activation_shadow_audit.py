#!/usr/bin/env python3
"""Shadow activation-readiness audit for N2B_1d_block_reversals_only.

This script does not activate the patch. It replays the frozen v2.1 baseline
signals, computes an opt-in helper-adjusted shadow copy, and records would-block
events for activation review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import BARS_PER_DAY_5M
from dex.execution_safety import apply_n2b_1d_block_reversals_only
from dex.regime_filter import build_daily_regime_labels
from scripts.research_oracle import (
    _find_eth_data,
    _generate_v21_signals,
    _load_and_split_data,
    _load_baseline_params,
)
from scripts.transition_attribution_audit import _datetimes

OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "activation_readiness"
REPORT_JSON = OUTPUT_DIR / "n2b_activation_shadow_report.json"
EVENTS_TSV = OUTPUT_DIR / "n2b_activation_shadow_events.tsv"
NOTES_MD = OUTPUT_DIR / "n2b_activation_readiness_notes.md"

CONTRACT_ID = "N2B_1d_block_reversals_only"
SIGNAL_NAMES = {0: "flat", 1: "hold", 2: "long", 3: "short"}
POSITION_NAMES = {-1: "short", 0: "flat", 1: "long"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _array_hash(values: np.ndarray) -> str:
    return "sha256:" + hashlib.sha256(np.asarray(values).tobytes()).hexdigest()[:16]


def _position_after_signal(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def _transition_id(index: int) -> str:
    return f"n2b_{index:04d}"


def _collect_shadow_events(
    raw_signals: np.ndarray,
    adjusted_signals: np.ndarray,
    regimes: np.ndarray,
    times: pd.DatetimeIndex,
    *,
    warmup_bars: int,
) -> List[Dict[str, Any]]:
    labels = np.asarray(regimes, dtype=object)
    events: List[Dict[str, Any]] = []
    position = 0
    warmup_until = -1
    active_transition_id: Optional[str] = None
    active_transition_start: Optional[int] = None
    transition_count = 0

    for i in range(len(raw_signals)):
        if i > 0 and str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR":
            transition_count += 1
            active_transition_id = _transition_id(transition_count)
            active_transition_start = i
            warmup_until = max(warmup_until, i + warmup_bars)

        raw = int(raw_signals[i])
        adjusted = int(adjusted_signals[i])
        if adjusted != raw:
            transition_bar_offset = (
                i - active_transition_start if active_transition_start is not None else None
            )
            events.append(
                {
                    "transition_id": active_transition_id,
                    "bar_index": i,
                    "bar_time": str(times[i]),
                    "transition_start_bar": active_transition_start,
                    "transition_bar_offset": transition_bar_offset,
                    "warmup_bars": warmup_bars,
                    "prev_regime": str(labels[i - 1]) if i > 0 else None,
                    "current_regime": str(labels[i]),
                    "prev_effective_position": POSITION_NAMES[position],
                    "raw_proposed_signal": raw,
                    "raw_proposed_signal_name": SIGNAL_NAMES.get(raw, str(raw)),
                    "adjusted_signal": adjusted,
                    "adjusted_signal_name": SIGNAL_NAMES.get(adjusted, str(adjusted)),
                    "reason": "n2b_1d_direct_reversal_to_flat",
                    "in_warmup": i < warmup_until,
                }
            )

        position = _position_after_signal(adjusted, position)

    return events


def _activation_readiness(
    diagnostics: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    semantic_ok = all(
        event["current_regime"] == "BEAR"
        and event["in_warmup"]
        and event["adjusted_signal"] == 0
        and (
            (event["prev_effective_position"] == "long" and event["raw_proposed_signal"] == 3)
            or (event["prev_effective_position"] == "short" and event["raw_proposed_signal"] == 2)
        )
        for event in events
    )
    count_ok = (
        diagnostics["blocked_actions_total"] == 6
        and diagnostics["blocked_entries_total"] == 0
        and diagnostics["signals_changed_total"] == 6
        and len(events) == 6
    )
    conclusion = "activation_ready_candidate" if semantic_ok and count_ok else "hold"
    return {
        "conclusion": conclusion,
        "semantic_ok": semantic_ok,
        "count_ok": count_ok,
        "allowed_conclusions": ["activation_ready_candidate", "hold"],
        "note": (
            "Shadow audit only. Future live/demo/default-oracle activation requires "
            "a separate activation contract."
        ),
    }


def _write_events_tsv(path: Path, events: List[Dict[str, Any]]) -> None:
    fields = [
        "transition_id",
        "bar_index",
        "bar_time",
        "transition_start_bar",
        "transition_bar_offset",
        "warmup_bars",
        "prev_regime",
        "current_regime",
        "prev_effective_position",
        "raw_proposed_signal",
        "raw_proposed_signal_name",
        "adjusted_signal",
        "adjusted_signal_name",
        "reason",
        "in_warmup",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(events)


def _write_notes(path: Path, report: Dict[str, Any]) -> None:
    readiness = report["readiness"]
    diagnostics = report["shadow"]["diagnostics"]
    lines = [
        "# N2B Activation Readiness Shadow Audit",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Status",
        "",
        f"- Conclusion: `{readiness['conclusion']}`.",
        "- This is activation readiness only; it does not enable live/demo/default-oracle routing.",
        "- Future activation requires a separate activation contract.",
        "",
        "## Proposed Future Insertion Point",
        "",
        "- After raw baseline signal generation and regime-permission routing.",
        "- Before generic safe-execution/reversal handling.",
        "- The helper must remain default-inert unless explicitly enabled by a future activation contract.",
        "",
        "## Shadow Result",
        "",
        f"- Would-block actions: {diagnostics['blocked_actions_total']}.",
        f"- Would-block entries: {diagnostics['blocked_entries_total']}.",
        f"- Would-block reversals: {diagnostics['blocked_reversals_total']}.",
        f"- Signals changed in shadow copy: {diagnostics['signals_changed_total']}.",
        "",
        "## Audit Fields",
        "",
        "- transition_id",
        "- bar_index / bar_time",
        "- transition_start_bar / transition_bar_offset",
        "- prev_effective_position",
        "- raw_proposed_signal",
        "- adjusted_signal",
        "- reason",
        "",
        "## Disable And Rollback",
        "",
        "- Disable path: leave the helper uncalled; baseline behavior is unchanged.",
        "- Rollback path after future activation: remove the explicit activation call and keep raw signals.",
        "- No checkpoint, family registry, LLM search, live/demo, or default-oracle path is modified here.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_shadow_audit(
    *,
    data_path: Optional[Path] = None,
    fast_days: int = 50,
    slow_days: int = 200,
) -> Dict[str, Any]:
    if data_path is None:
        data_path = _find_eth_data()

    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    times = _datetimes(df_full)
    regimes = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    checkpoint = _load_baseline_params()
    raw_signals = _generate_v21_signals(
        checkpoint,
        df_full,
        fast_days=fast_days,
        slow_days=slow_days,
    )

    shadow = apply_n2b_1d_block_reversals_only(raw_signals, regimes)
    events = _collect_shadow_events(
        raw_signals,
        shadow.signals,
        regimes,
        times,
        warmup_bars=BARS_PER_DAY_5M,
    )
    readiness = _activation_readiness(shadow.diagnostics, events)

    return {
        "generated_at": _now_iso(),
        "contract_id": CONTRACT_ID,
        "mode": "activation_readiness_shadow_only",
        "data": {
            "path": str(data_path),
            "bars": len(df_full),
            "start": str(times[0]),
            "end": str(times[-1]),
            "split_idx": split_idx,
        },
        "regime_filter": {"fast_days": fast_days, "slow_days": slow_days},
        "future_insertion_point": {
            "after": "raw_signal_generation_and_regime_permission_routing",
            "before": "generic_safe_execution_reversal_handling",
            "default_inert": True,
        },
        "shadow": {
            "raw_signal_hash": _array_hash(raw_signals),
            "adjusted_signal_hash": _array_hash(shadow.signals),
            "diagnostics": shadow.diagnostics,
            "events": events,
        },
        "readiness": readiness,
        "non_activation_guards": {
            "live_demo_routing_changed": False,
            "baseline_checkpoint_changed": False,
            "default_oracle_changed": False,
            "family_registry_changed": False,
            "llm_search_changed": False,
            "future_activation_requires_separate_contract": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shadow audit for N2B_1d_block_reversals_only activation readiness."
    )
    parser.add_argument("--data-path", type=str, default=None, help="Optional OHLCV parquet path.")
    parser.add_argument("--fast-days", type=int, default=50, help="EMA fast days (default 50).")
    parser.add_argument("--slow-days", type=int, default=200, help="EMA slow days (default 200).")
    args = parser.parse_args()

    if args.fast_days <= 0 or args.slow_days <= 0 or args.fast_days >= args.slow_days:
        parser.error("--fast-days and --slow-days must be positive with fast < slow")

    report = run_shadow_audit(
        data_path=Path(args.data_path) if args.data_path else None,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_events_tsv(EVENTS_TSV, report["shadow"]["events"])
    _write_notes(NOTES_MD, report)

    diag = report["shadow"]["diagnostics"]
    print("N2B activation readiness shadow audit complete.")
    print(f"  Conclusion: {report['readiness']['conclusion']}")
    print(f"  Would-block actions: {diag['blocked_actions_total']}")
    print(f"  JSON:  {REPORT_JSON}")
    print(f"  TSV:   {EVENTS_TSV}")
    print(f"  Notes: {NOTES_MD}")
    return 0 if report["readiness"]["conclusion"] == "activation_ready_candidate" else 1


if __name__ == "__main__":
    raise SystemExit(main())
