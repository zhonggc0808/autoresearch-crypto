"""Audit live order ledger against a replayed live signal stream.

Usage:
    uv run python scripts/audit_live_consistency.py --state logs/live_bitget_state.json
    uv run python scripts/audit_live_consistency.py --state logs/live_okx_state.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dex.checkpoints import (
    build_channel_breakout_strategy_from_checkpoint,
    build_strategy_from_checkpoint,
    is_regime_channel_breakout_checkpoint,
    load_checkpoint,
)
from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.data import load_crypto_data
from dex.exit_overlays import apply_exit_overlays
from dex.indicators import compute_adx
from dex.live.profiles import resolve_checkpoint_path
from dex.regime_filter import build_daily_regime_labels
from dex.regime_permissions import (
    RiskOffConfig,
    apply_permission_arrays,
    build_permission_arrays,
    compute_daily_indicators,
    route_regime_signals,
)
from dex.strategies.base import StrategyEvaluator
from dex.strategy_signals import generate_strategy_signals

REPORT_DIR = Path("reports")
OPEN_TYPES = {"buy": "long", "sell_short": "short"}
CLOSE_TYPES = {"sell": "long", "sell_final": "long", "buy_cover": "short", "buy_cover_final": "short"}


@dataclass
class LiveEvent:
    time: str
    bar_time: str | None
    offset_minutes: float | None
    kind: str
    side: str
    type: str
    price: float
    size: float
    pnl: float | None
    fee: float
    filled_hint: bool
    order_id: str


@dataclass
class MatchRow:
    live_time: str
    live_type: str
    live_side: str
    live_price: float
    expected_time: str | None
    expected_type: str | None
    expected_side: str | None
    expected_price: float | None
    time_diff_bars: float | None
    price_diff_pct: float | None
    status: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay live signals and audit state trades")
    parser.add_argument("--state", default="logs/live_bitget_state.json")
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--data", default=None, help="Live kline cache/parquet path")
    parser.add_argument("--tolerance-bars", type=int, default=2)
    parser.add_argument(
        "--live-time-offset-hours",
        type=float,
        default=-8.0,
        help="State trade time -> kline datetime offset. Asia/Shanghai local logs need -8.",
    )
    parser.add_argument("--disable-exit-overlays", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        _self_check()
        print("self-test ok")
        return

    state_path = Path(args.state)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    checkpoint_path = args.checkpoint or resolve_checkpoint_path(
        None,
        state.get("strategy_profile", "channel_breakout_v2_2_m375_bbm375_1p5"),
    )
    checkpoint = load_checkpoint(checkpoint_path)
    df = _load_bars(args.data, state)
    df = _trim_to_state(df, state)

    signals, diag = _build_replay_signals(
        df,
        checkpoint,
        exit_overlays_enabled=not args.disable_exit_overlays,
    )
    latest_idx = len(df) - 1
    latest_signal = int(signals[latest_idx]) if latest_idx >= 0 else 1
    expected_position = _position_after(signals)

    evaluator = StrategyEvaluator(
        initial_capital=float(state.get("initial_capital", INITIAL_CAPITAL)),
        commission=COMMISSION,
        slippage=SLIPPAGE,
    )
    _, replay_events = evaluator.simulate(signals, df["close"].to_numpy(dtype=float), df)
    expected_events = _normalize_replay_events(replay_events, df)
    live_events = _normalize_live_events(
        state.get("trades", []),
        df,
        live_time_offset_hours=args.live_time_offset_hours,
    )
    matches = _match_events(live_events, expected_events, args.tolerance_bars)
    live_trades = _build_live_trade_rows(live_events)

    REPORT_DIR.mkdir(exist_ok=True)
    stem = f"live_audit_{state_path.stem}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    live_csv = REPORT_DIR / f"{stem}_live_events.csv"
    expected_csv = REPORT_DIR / f"{stem}_expected_events.csv"
    matches_csv = REPORT_DIR / f"{stem}_matches.csv"
    trades_csv = REPORT_DIR / f"{stem}_closed_trades.csv"
    summary_json = REPORT_DIR / f"{stem}_summary.json"

    pd.DataFrame([asdict(e) for e in live_events]).to_csv(live_csv, index=False)
    pd.DataFrame(expected_events).to_csv(expected_csv, index=False)
    pd.DataFrame([asdict(m) for m in matches]).to_csv(matches_csv, index=False)
    pd.DataFrame(live_trades).to_csv(trades_csv, index=False)

    funding = _sum_funding(state)
    closed_pnl = sum(float(row.get("pnl") or 0.0) for row in live_trades)
    matched = sum(1 for row in matches if row.status == "matched")
    summary = {
        "state": str(state_path),
        "checkpoint": str(checkpoint_path),
        "data_start": str(df["datetime"].iloc[0]) if len(df) else None,
        "data_end": str(df["datetime"].iloc[-1]) if len(df) else None,
        "last_processed_bar": state.get("last_processed_bar"),
        "state_position": state.get("position"),
        "expected_position_without_timesfm": expected_position,
        "state_last_signal": state.get("last_signal"),
        "replay_latest_signal_without_timesfm": latest_signal,
        "state_v21_raw_signal": state.get("v21_raw_signal"),
        "state_v21_permission_signal": state.get("v21_permission_signal"),
        "state_v21_routed_signal": state.get("v21_routed_signal"),
        "replay_latest_diag": diag,
        "timesfm_gate_active": state.get("timesfm_gate_active", False),
        "timesfm_gate_checked": state.get("timesfm_gate_checked", False),
        "timesfm_gate_signal_before": state.get("timesfm_gate_signal_before"),
        "timesfm_gate_signal_after": state.get("timesfm_gate_signal_after"),
        "live_events": len(live_events),
        "matched_events": matched,
        "unmatched_live_events": len([m for m in matches if m.status == "unmatched_live"]),
        "closed_live_trades": len(live_trades),
        "closed_live_pnl": closed_pnl,
        "funding_pnl": funding,
        "closed_live_pnl_plus_funding": closed_pnl + funding,
        "reports": {
            "live_events": str(live_csv),
            "expected_events": str(expected_csv),
            "matches": str(matches_csv),
            "closed_trades": str(trades_csv),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"state: {state_path}")
    print(f"bars: {summary['data_start']} -> {summary['data_end']}")
    print(
        "signal: "
        f"replay={latest_signal} state_last={state.get('last_signal')} "
        f"state_routed={state.get('v21_routed_signal')}"
    )
    print(f"position: replay={expected_position} state={state.get('position')}")
    print(f"events: live={len(live_events)} matched={matched}/{len(matches)}")
    print(f"closed pnl: trades={closed_pnl:+.6f} funding={funding:+.6f} total={closed_pnl + funding:+.6f}")
    print(f"wrote: {summary_json}")


def _load_bars(path: str | None, state: dict[str, Any]) -> pd.DataFrame:
    if path:
        df = pd.read_parquet(path) if path.endswith(".parquet") else load_crypto_data(path)
        return _clean_bars(df)

    candidates = _live_cache_candidates(state) + _crypto_candidates(state)
    for candidate in candidates:
        if candidate.exists():
            return _clean_bars(pd.read_parquet(candidate))
    raise FileNotFoundError("No kline parquet found; pass --data data/live_cache/....parquet")


def _live_cache_candidates(state: dict[str, Any]) -> list[Path]:
    interval = state.get("interval", "5m")
    symbol = str(state.get("ccxt_symbol") or state.get("symbol") or "")
    raw = str(state.get("symbol") or "")
    files = []
    norm = symbol.replace("/", "_").replace(":", "_").replace("-", "_")
    raw_norm = raw.replace("/", "_").replace(":", "_").replace("-", "_")
    if "SWAP" in raw.upper():
        files.append(Path("data/live_cache") / f"okx_{raw_norm}_{interval}.parquet")
    files.append(Path("data/live_cache") / f"bitget_{norm}_{interval}.parquet")
    files.append(Path("data/live_cache") / f"binance_{raw_norm}_{interval}.parquet")
    return files


def _crypto_candidates(state: dict[str, Any]) -> list[Path]:
    interval = state.get("interval", "5m")
    symbol = _plain_symbol(str(state.get("symbol") or "ETHUSDT"))
    root = Path("data/crypto")
    return sorted(
        root.glob(f"{symbol}_{interval}*.parquet"),
        key=lambda p: (p.stat().st_size, p.name),
        reverse=True,
    )


def _plain_symbol(symbol: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", symbol.upper())
    if "ETH" in compact and "USDT" in compact:
        return "ETHUSDT"
    if "BTC" in compact and "USDT" in compact:
        return "BTCUSDT"
    if "SOL" in compact and "USDT" in compact:
        return "SOLUSDT"
    return compact.replace("SWAP", "")


def _clean_bars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns:
        out["datetime"] = pd.to_datetime(out["timestamp"], unit="ms", errors="coerce")
    else:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    if "timestamp" not in out.columns:
        out["timestamp"] = out["datetime"].astype("int64") // 10**6
    return out.dropna(subset=["datetime"]).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def _trim_to_state(df: pd.DataFrame, state: dict[str, Any]) -> pd.DataFrame:
    last = pd.to_datetime(state.get("last_processed_bar"), errors="coerce")
    if pd.isna(last):
        return df
    trimmed = df[df["datetime"] <= last].copy()
    return trimmed.reset_index(drop=True) if not trimmed.empty else df


def _build_replay_signals(
    df: pd.DataFrame,
    checkpoint: dict[str, Any],
    *,
    exit_overlays_enabled: bool,
) -> tuple[np.ndarray, dict[str, Any]]:
    if not is_regime_channel_breakout_checkpoint(checkpoint):
        strategy = build_strategy_from_checkpoint(checkpoint)
        signals = generate_strategy_signals(strategy, df, enable_short=getattr(strategy, "enable_short", True))
        return signals, {}

    bull_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bull")
    bear_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "bear")
    neutral_s = build_channel_breakout_strategy_from_checkpoint(checkpoint, "neutral")
    bull_cfg = RiskOffConfig(**checkpoint["bull"]["permission"])
    bear_cfg = RiskOffConfig(**checkpoint["bear"]["permission"])
    neutral_cfg = RiskOffConfig(**checkpoint["neutral"]["permission"])

    bull_raw = generate_strategy_signals(bull_s, df, enable_short=bull_s.enable_short)
    bear_raw = generate_strategy_signals(bear_s, df, enable_short=bear_s.enable_short)
    neutral_raw = generate_strategy_signals(neutral_s, df, enable_short=neutral_s.enable_short)
    regimes = build_daily_regime_labels(
        df,
        fast_days=int(checkpoint.get("regime_filter", {}).get("fast_days", 50)),
        slow_days=int(checkpoint.get("regime_filter", {}).get("slow_days", 200)),
    )
    routed = route_regime_signals(
        bull_raw,
        bear_raw,
        neutral_raw,
        regimes,
        regime_change_policy=checkpoint.get("regime_change_policy", "permission_based"),
    )
    adx_full, _, _ = compute_adx(df, 14)
    allow_long, allow_short, force_flat, exit_only = build_permission_arrays(
        df,
        regimes,
        bull_cfg,
        bear_cfg,
        neutral_cfg,
        compute_daily_indicators(df),
        adx_full,
    )
    permission_signals = apply_permission_arrays(routed, allow_long, allow_short, force_flat, exit_only)
    final = permission_signals
    if exit_overlays_enabled and checkpoint.get("exit_logic"):
        final = apply_exit_overlays(permission_signals, df, regimes, checkpoint["exit_logic"])
    i = len(df) - 1
    return final, {
        "regime": str(regimes[i]),
        "raw_signal": int(routed[i]),
        "permission_signal": int(permission_signals[i]),
        "routed_signal": int(final[i]),
        "exit_overlays_enabled": bool(exit_overlays_enabled and checkpoint.get("exit_logic")),
        "exit_overlay_changed_last": bool(final[i] != permission_signals[i]),
    }


def _position_after(signals: np.ndarray) -> int:
    pos = 0
    for raw in signals:
        if raw == 2:
            pos = 1
        elif raw == 3:
            pos = -1
        elif raw == 0:
            pos = 0
    return pos


def _normalize_replay_events(events: list[dict[str, Any]], df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type in OPEN_TYPES:
            kind, side = "open", OPEN_TYPES[event_type]
        elif event_type in CLOSE_TYPES:
            kind, side = "close", CLOSE_TYPES[event_type]
        else:
            continue
        step = int(event.get("step", -1))
        if not 0 <= step < len(df):
            continue
        rows.append(
            {
                "time": str(df["datetime"].iloc[step]),
                "step": step,
                "kind": kind,
                "side": side,
                "type": event_type,
                "price": float(event.get("entry_price") if kind == "open" else event.get("exit_price")),
                "pnl": event.get("pnl"),
            }
        )
    return rows


def _normalize_live_events(
    events: list[dict[str, Any]],
    df: pd.DataFrame,
    *,
    live_time_offset_hours: float,
) -> list[LiveEvent]:
    bar_times = pd.to_datetime(df["datetime"]).to_numpy()
    rows = []
    for event in events:
        kind_side = _event_kind(str(event.get("type", "")))
        if kind_side is None:
            continue
        kind, side = kind_side
        event_time = pd.to_datetime(event.get("time"), errors="coerce")
        shifted = event_time + timedelta(hours=live_time_offset_hours) if not pd.isna(event_time) else pd.NaT
        bar_idx = int(np.searchsorted(bar_times, np.datetime64(shifted), side="right") - 1) if not pd.isna(shifted) else -1
        bar_time = pd.Timestamp(bar_times[bar_idx]) if 0 <= bar_idx < len(bar_times) else None
        offset = ((shifted - bar_time).total_seconds() / 60.0) if bar_time is not None else None
        rows.append(
            LiveEvent(
                time=str(event.get("time", "")),
                bar_time=str(bar_time) if bar_time is not None else None,
                offset_minutes=float(offset) if offset is not None else None,
                kind=kind,
                side=side,
                type=str(event.get("type", "")),
                price=_float(event.get("price")),
                size=_float(event.get("size")),
                pnl=_maybe_float(event.get("pnl")),
                fee=_event_fee(event),
                filled_hint=_filled_hint(event),
                order_id=str(event.get("orderId") or event.get("digest") or ""),
            )
        )
    return rows


def _event_kind(event_type: str) -> tuple[str, str] | None:
    up = event_type.upper()
    if up.startswith("BUY_OPEN"):
        return "open", "long"
    if up.startswith("SELL_SHORT"):
        return "open", "short"
    if "CLOSE_LONG" in up:
        return "close", "long"
    if "CLOSE_SHORT" in up:
        return "close", "short"
    if up.startswith("FORCE_CLOSE"):
        return "close", "unknown"
    return None


def _match_events(
    live_events: list[LiveEvent],
    expected_events: list[dict[str, Any]],
    tolerance_bars: int,
) -> list[MatchRow]:
    unused = set(range(len(expected_events)))
    rows = []
    for live in live_events:
        live_bar = pd.to_datetime(live.bar_time, errors="coerce")
        best_i = None
        best_score = math.inf
        for i in unused:
            expected = expected_events[i]
            if expected["kind"] != live.kind:
                continue
            if live.side != "unknown" and expected["side"] != live.side:
                continue
            expected_time = pd.to_datetime(expected["time"], errors="coerce")
            diff_bars = abs((live_bar - expected_time).total_seconds()) / 300.0
            if diff_bars <= tolerance_bars and diff_bars < best_score:
                best_i = i
                best_score = diff_bars
        if best_i is None:
            rows.append(_match_row(live, None, None, "unmatched_live"))
            continue
        unused.remove(best_i)
        rows.append(_match_row(live, expected_events[best_i], best_score, "matched"))
    return rows


def _match_row(
    live: LiveEvent,
    expected: dict[str, Any] | None,
    diff_bars: float | None,
    status: str,
) -> MatchRow:
    expected_price = _maybe_float(expected.get("price")) if expected else None
    price_diff = None
    if expected_price and live.price > 0:
        price_diff = live.price / expected_price - 1.0
    return MatchRow(
        live_time=live.time,
        live_type=live.type,
        live_side=live.side,
        live_price=live.price,
        expected_time=expected.get("time") if expected else None,
        expected_type=expected.get("type") if expected else None,
        expected_side=expected.get("side") if expected else None,
        expected_price=expected_price,
        time_diff_bars=diff_bars,
        price_diff_pct=price_diff,
        status=status,
    )


def _build_live_trade_rows(events: list[LiveEvent]) -> list[dict[str, Any]]:
    rows = []
    open_event: LiveEvent | None = None
    for event in events:
        if event.kind == "open":
            open_event = event
            continue
        if event.kind != "close" or open_event is None:
            continue
        side = open_event.side
        if side == "long":
            ret = event.price / open_event.price - 1.0 if open_event.price else 0.0
        elif side == "short":
            ret = 1.0 - event.price / open_event.price if open_event.price else 0.0
        else:
            ret = 0.0
        rows.append(
            {
                "side": side,
                "entry_time": open_event.time,
                "exit_time": event.time,
                "entry_price": open_event.price,
                "exit_price": event.price,
                "size": min(open_event.size, event.size),
                "return_pct": ret,
                "pnl": event.pnl,
                "fees": open_event.fee + event.fee,
                "entry_order_id": open_event.order_id,
                "exit_order_id": event.order_id,
            }
        )
        open_event = None
    return rows


def _sum_funding(state: dict[str, Any]) -> float:
    return sum(_float(record.get("amount")) for record in state.get("funding_fee_records", []))


def _event_fee(event: dict[str, Any]) -> float:
    if event.get("fee") is not None:
        return _float(event.get("fee"))
    return sum(_float(item.get("cost")) for item in event.get("fees", []) if isinstance(item, dict))


def _filled_hint(event: dict[str, Any]) -> bool:
    return event.get("fee") is not None or event.get("pnl") is not None or bool(event.get("fees"))


def _float(value: Any) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _maybe_float(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _self_check() -> None:
    assert _event_kind("BUY_OPEN_MAKER") == ("open", "long")
    assert _event_kind("SELL_SHORT_IOC_FALLBACK") == ("open", "short")
    assert _event_kind("CLOSE_LONG_Taker(SL)") == ("close", "long")
    assert _event_kind("CLOSE_SHORT_Taker(SL)") == ("close", "short")
    tiny = pd.DataFrame(
        {
            "timestamp": [0, 300_000],
            "datetime": pd.to_datetime(["2026-01-01 00:00:00", "2026-01-01 00:05:00"]),
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1.0, 1.0],
        }
    )
    [event] = _normalize_live_events(
        [{"time": "2026-01-01T08:06:00", "type": "BUY_OPEN_MAKER", "price": 101, "size": 1}],
        tiny,
        live_time_offset_hours=-8,
    )
    assert event.bar_time == "2026-01-01 00:05:00"


if __name__ == "__main__":
    main()
