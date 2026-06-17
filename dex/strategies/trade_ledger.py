"""Trade ledger enrichment for diagnostics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

OPEN_TYPES = {"buy": "long", "sell_short": "short"}
CLOSE_TYPES = {"sell", "buy_cover", "sell_final", "buy_cover_final"}


def enrich_trade_ledger(
    trades: list[dict[str, Any]],
    df: pd.DataFrame,
    timeframe_minutes: int = 5,
    regime_col: str | None = "regime",
) -> list[dict[str, Any]]:
    """Return one diagnostic row per closed trade."""
    open_by_step: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []

    for event in trades:
        event_type = event.get("type")
        step = int(event.get("step", -1))
        if event_type in OPEN_TYPES:
            open_by_step[step] = event
            continue
        if event_type not in CLOSE_TYPES:
            continue

        entry_step = int(event.get("entry_step", -1))
        open_event = open_by_step.get(entry_step, {})
        side = OPEN_TYPES.get(str(open_event.get("type")), _side_from_close(str(event_type)))
        if side is None or entry_step < 0 or step < 0:
            continue

        entry_price = float(event["entry_price"])
        exit_price = float(event["exit_price"])
        entry_notional = float(event.get("entry_notional", 0.0))
        return_pct = exit_price / entry_price - 1.0 if side == "long" else 1.0 - exit_price / entry_price
        mae_pct, mfe_pct, mae_step, mfe_step = _mae_mfe(
            side, entry_price, exit_price, entry_step, step, df
        )

        duration_bars = step - entry_step
        rows.append(
            {
                "side": side,
                "entry_step": entry_step,
                "exit_step": step,
                "entry_time": _index_at(df, entry_step),
                "exit_time": _index_at(df, step),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_notional": entry_notional,
                "entry_size": float(event.get("entry_size", open_event.get("entry_size", 1.0))),
                "pnl": float(event.get("pnl", 0.0)),
                "return_pct": float(return_pct),
                "duration_bars": duration_bars,
                "duration_days": float(duration_bars * timeframe_minutes / 1440.0),
                "mae_pct": float(mae_pct),
                "mfe_pct": float(mfe_pct),
                "mae_step": mae_step,
                "mfe_step": mfe_step,
                "time_to_mae_bars": mae_step - entry_step,
                "time_to_mae_hours": float((mae_step - entry_step) * timeframe_minutes / 60.0),
                "time_to_mfe_bars": mfe_step - entry_step,
                "time_to_mfe_hours": float((mfe_step - entry_step) * timeframe_minutes / 60.0),
                "entry_regime": _regime_at(df, regime_col, entry_step),
                "exit_regime": _regime_at(df, regime_col, step),
                "mae_pnl_est": float(mae_pct * entry_notional),
                "mfe_pnl_est": float(mfe_pct * entry_notional),
            }
        )
    return rows


def build_logical_trade_ledger(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate event ledger close events into one row per parent trade."""
    groups: dict[int, list[dict[str, Any]]] = {}
    opens: dict[int, dict[str, Any]] = {}

    for event in events:
        event_type = event.get("type")
        if event_type in OPEN_TYPES:
            trade_id = event.get("parent_trade_id", event.get("trade_id"))
            if trade_id is not None:
                opens[int(trade_id)] = event
            continue
        if event.get("pnl") is None:
            continue
        parent_trade_id = event.get("parent_trade_id")
        if parent_trade_id is None:
            continue
        groups.setdefault(int(parent_trade_id), []).append(event)

    rows: list[dict[str, Any]] = []
    for parent_trade_id, closes in sorted(groups.items()):
        total_pnl = sum(float(event.get("pnl", 0.0)) for event in closes)
        reasons = [str(event["exit_reason"]) for event in closes if event.get("exit_reason")]
        first = closes[0]
        last = closes[-1]
        open_event = opens.get(parent_trade_id, {})
        rows.append(
            {
                "parent_trade_id": parent_trade_id,
                "side": OPEN_TYPES.get(str(open_event.get("type")), _side_from_close(first["type"])),
                "entry_step": first.get("entry_step"),
                "exit_step": last.get("step"),
                "total_pnl": total_pnl,
                "is_winner": total_pnl > 0,
                "partial_close_count": sum(1 for event in closes if event.get("is_partial")),
                "exit_reasons": reasons,
                "entry_price": first.get("entry_price"),
                "exit_price": last.get("exit_price"),
                "entry_notional": first.get("entry_notional"),
            }
        )
    return rows


def _mae_mfe(
    side: str,
    entry_price: float,
    exit_price: float,
    entry_step: int,
    exit_step: int,
    df: pd.DataFrame,
) -> tuple[float, float, int, int]:
    if exit_step <= entry_step:
        ret = exit_price / entry_price - 1.0 if side == "long" else 1.0 - exit_price / entry_price
        return min(0.0, ret), max(0.0, ret), exit_step, exit_step

    start = entry_step + 1
    stop = min(exit_step + 1, len(df))
    if start >= stop:
        ret = exit_price / entry_price - 1.0 if side == "long" else 1.0 - exit_price / entry_price
        return min(0.0, ret), max(0.0, ret), exit_step, exit_step

    high = df["high"].iloc[start:stop].to_numpy(dtype=float)
    low = df["low"].iloc[start:stop].to_numpy(dtype=float)
    if side == "long":
        adverse = low / entry_price - 1.0
        favorable = high / entry_price - 1.0
    else:
        adverse = 1.0 - high / entry_price
        favorable = 1.0 - low / entry_price

    mae_idx = int(np.argmin(adverse))
    mfe_idx = int(np.argmax(favorable))
    return (
        float(adverse[mae_idx]),
        float(favorable[mfe_idx]),
        start + mae_idx,
        start + mfe_idx,
    )


def _side_from_close(event_type: str) -> str | None:
    if event_type in {"sell", "sell_final"}:
        return "long"
    if event_type in {"buy_cover", "buy_cover_final"}:
        return "short"
    return None


def _index_at(df: pd.DataFrame, step: int) -> Any:
    return df.index[step] if 0 <= step < len(df.index) else None


def _regime_at(df: pd.DataFrame, regime_col: str | None, step: int) -> str | None:
    if not regime_col or regime_col not in df.columns or not 0 <= step < len(df):
        return None
    value = df[regime_col].iloc[step]
    return None if pd.isna(value) else str(value)
