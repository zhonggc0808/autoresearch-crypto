"""Local execution-safety signal patches.

These helpers are intentionally opt-in. They do not register strategy families
or change baseline signal generation by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import numpy as np

from dex.config import BARS_PER_DAY_5M


@dataclass(frozen=True)
class ExecutionSafetyResult:
    """Patched signals plus attribution counters."""

    signals: np.ndarray
    diagnostics: Dict[str, Any]


def _position_after_signal(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def apply_n2b_1d_block_reversals_only(
    signals: np.ndarray,
    regimes: np.ndarray,
    *,
    warmup_bars: int = BARS_PER_DAY_5M,
) -> ExecutionSafetyResult:
    """Block direct reversals for 1 day after NEUTRAL -> BEAR transitions.

    Locked contract semantics:
    - only NEUTRAL -> BEAR transition warmups are active;
    - only direct long -> short / short -> long flips are intercepted;
    - intercepted reversals become flat (signal 0);
    - flat entries, carried positions, holds, and closes pass through.
    """
    out = np.asarray(signals, dtype=int).copy()
    labels = np.asarray(regimes, dtype=object)
    if len(out) != len(labels):
        raise ValueError("signals and regimes must have the same length")
    if warmup_bars <= 0:
        raise ValueError("warmup_bars must be positive")

    position = 0
    warmup_until = -1
    diag = {
        "contract": "N2B_1d_block_reversals_only",
        "transition_count": 0,
        "warmup_bars_marked": 0,
        "blocked_actions_total": 0,
        "blocked_entries_total": 0,
        "blocked_reversals_total": 0,
        "blocked_long_to_short_reversals": 0,
        "blocked_short_to_long_reversals": 0,
        "signals_changed_total": 0,
    }

    for i in range(len(out)):
        if i > 0 and str(labels[i - 1]) == "NEUTRAL" and str(labels[i]) == "BEAR":
            diag["transition_count"] += 1
            warmup_until = max(warmup_until, i + warmup_bars)

        raw = int(out[i])
        sig = raw
        if i < warmup_until:
            diag["warmup_bars_marked"] += 1
            if position == 1 and raw == 3:
                sig = 0
                diag["blocked_actions_total"] += 1
                diag["blocked_reversals_total"] += 1
                diag["blocked_long_to_short_reversals"] += 1
            elif position == -1 and raw == 2:
                sig = 0
                diag["blocked_actions_total"] += 1
                diag["blocked_reversals_total"] += 1
                diag["blocked_short_to_long_reversals"] += 1

        if sig != raw:
            diag["signals_changed_total"] += 1
        out[i] = sig
        position = _position_after_signal(sig, position)

    return ExecutionSafetyResult(signals=out, diagnostics=diag)
