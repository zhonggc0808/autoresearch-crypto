"""Live guards for retest next-open strategies."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

RETEST_EVENT_REASONS = {
    "long_pending_created",
    "short_pending_created",
    "flip_long_pending_created",
    "flip_short_pending_created",
    "retest_confirmed_entry_scheduled",
    "scheduled_entry_executed",
    "pending_timeout",
}
RETEST_NEXT_OPEN_GRACE_BARS = 1
RETEST_HOLD_REENTRY_REASONS = {"hold_long", "hold_short"}


def bar_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value == "" or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    return ts.tz_localize(None) if ts.tzinfo else ts


def bar_timestamp_str(value: Any) -> str:
    ts = bar_timestamp(value)
    return "" if ts is None else ts.strftime("%Y-%m-%d %H:%M:%S")


def replay_log_with_ts(replay_log: pd.DataFrame | None) -> pd.DataFrame | None:
    if replay_log is None or replay_log.empty:
        return replay_log
    if "_bar_ts" in replay_log.columns:
        return replay_log
    log = replay_log.copy()
    bar_ts = pd.to_datetime(log["timestamp"])
    try:
        bar_ts = bar_ts.dt.tz_localize(None)
    except TypeError:
        pass
    log["_bar_ts"] = bar_ts
    return log


def summarize_retest_gap(
    state: dict[str, Any],
    replay_log: pd.DataFrame,
    current_time: Any,
    interval_seconds: int,
) -> dict[str, Any] | None:
    last = bar_timestamp(state.get("last_processed_bar"))
    current = bar_timestamp(current_time)
    if last is None or current is None or current <= last + pd.Timedelta(seconds=interval_seconds):
        return None

    log = replay_log_with_ts(replay_log)
    gap = log[(log["_bar_ts"] > last) & (log["_bar_ts"] < current)]
    if gap.empty:
        return None

    before = log[log["_bar_ts"] <= last].tail(1)
    events = gap[gap["reason"].isin(RETEST_EVENT_REASONS)]
    return {
        "gap_start": bar_timestamp_str(gap.iloc[0]["_bar_ts"]),
        "gap_end": bar_timestamp_str(gap.iloc[-1]["_bar_ts"]),
        "gap_bars": int(len(gap)),
        "gap_replayed": int(len(gap)),
        "state_before_gap": _retest_state_label(before.iloc[-1] if not before.empty else None),
        "state_after_gap": _retest_state_label(gap.iloc[-1]),
        "events": events["reason"].value_counts().to_dict(),
    }


def log_retest_gap_if_any(
    state: dict[str, Any],
    replay_log: pd.DataFrame,
    current_time: Any,
    interval_seconds: int,
    log_fn: Callable[[str], None],
) -> dict[str, Any] | None:
    summary = summarize_retest_gap(state, replay_log, current_time, interval_seconds)
    if not summary:
        return None
    log_fn(
        "[RetestGap] gap_detected=true "
        f"gap_start={summary['gap_start']} gap_end={summary['gap_end']} "
        f"gap_bars={summary['gap_bars']} gap_replayed={summary['gap_replayed']} "
        f"state_before_gap={summary['state_before_gap']} "
        f"state_after_gap={summary['state_after_gap']} events={summary['events']}"
    )
    return summary


def guard_retest_next_open(
    signal_id: int,
    state: dict[str, Any],
    replay_log: pd.DataFrame,
    current_time: Any,
    interval_seconds: int,
    log_fn: Callable[[str], None],
) -> int:
    if signal_id not in (2, 3) or state.get("position", 0) != 0:
        return signal_id

    log = replay_log_with_ts(replay_log)
    current = bar_timestamp(current_time)
    entries = log[
        (log["_bar_ts"] <= current)
        & (log["reason"] == "scheduled_entry_executed")
        & (log["executed_signal"] == signal_id)
    ]
    if entries.empty:
        log_fn(f"[RetestNextOpen] suppress entry: no scheduled next-open for signal={signal_id}")
        return 1

    entry = entries.iloc[-1]
    bars_late = int((current - entry["_bar_ts"]).total_seconds() // interval_seconds)
    state["scheduled_entry_direction"] = 1 if signal_id == 2 else -1
    state["scheduled_entry_due_bar"] = bar_timestamp_str(entry["_bar_ts"])
    state["scheduled_entry_reason"] = "scheduled_entry_executed"
    state["scheduled_entry_level"] = _scheduled_entry_level(log, entry)

    if bars_late <= RETEST_NEXT_OPEN_GRACE_BARS:
        if bars_late > 0:
            log_fn(
                "[RetestNextOpen] late next-open allowed "
                f"due_bar={state['scheduled_entry_due_bar']} late_by_bars={bars_late}"
            )
        return signal_id

    state["scheduled_entry_direction"] = 0
    state["scheduled_entry_missed_at"] = bar_timestamp_str(current)
    state["scheduled_entry_missed_reason"] = "missed_next_open"
    log_fn(
        "[RetestNextOpen] missed_next_open cancel "
        f"due_bar={bar_timestamp_str(entry['_bar_ts'])} current_bar={bar_timestamp_str(current)} "
        f"late_by_bars={bars_late}"
    )
    return 1


def guard_retest_hold_reentry(
    signal_id: int,
    state: dict[str, Any],
    replay_row: dict[str, Any] | None,
    log_fn: Callable[[str], None],
) -> int:
    if signal_id not in (2, 3) or state.get("position", 0) != 0:
        return signal_id
    reason = "" if replay_row is None else str(replay_row.get("reason", ""))
    if reason not in RETEST_HOLD_REENTRY_REASONS:
        return signal_id

    state["retest_hold_reentry_blocked_at"] = bar_timestamp_str(
        None if replay_row is None else replay_row.get("timestamp")
    )
    state["retest_hold_reentry_blocked_reason"] = reason
    log_fn(f"[RetestHoldReentry] suppress flat re-entry from {reason}; wait for scheduled entry")
    return 1


def record_retest_schedule_state(
    state: dict[str, Any],
    replay_log: pd.DataFrame,
    replay_row: dict[str, Any] | None,
) -> None:
    if replay_row is None or replay_row.get("reason") != "retest_confirmed_entry_scheduled":
        return
    log = replay_log_with_ts(replay_log)
    due_i = replay_row.get("entry_due_bar")
    due_row = log[log["bar_index"] == int(due_i)].tail(1) if pd.notna(due_i) else pd.DataFrame()
    state["scheduled_entry_direction"] = int(replay_row.get("pending_before", 0))
    state["scheduled_entry_due_bar"] = (
        bar_timestamp_str(due_row.iloc[0]["_bar_ts"]) if not due_row.empty else ""
    )
    state["scheduled_entry_reason"] = "retest_confirmed_entry_scheduled"
    state["scheduled_entry_level"] = _float_or_zero(replay_row.get("pending_level_before"))


def _retest_state_label(row: pd.Series | None) -> str:
    if row is None:
        return "unknown"
    return (
        f"pos={int(row['position_after'])},pending={int(row['pending_after'])},"
        f"reason={row['reason']}"
    )


def _scheduled_entry_level(log: pd.DataFrame, entry_row: pd.Series) -> float:
    matches = log[log["entry_due_bar"] == int(entry_row["bar_index"])].tail(1)
    if matches.empty:
        return 0.0
    return _float_or_zero(matches.iloc[0].get("pending_level_before"))


def _float_or_zero(value: Any) -> float:
    return 0.0 if pd.isna(value) else float(value)
