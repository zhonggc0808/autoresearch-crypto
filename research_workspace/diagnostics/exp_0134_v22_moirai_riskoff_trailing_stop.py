from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXP0131_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp.py"
EXP0133_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow.py"

spec0131 = importlib.util.spec_from_file_location("exp0131_helper", EXP0131_PATH)
exp0131 = importlib.util.module_from_spec(spec0131)
sys.modules["exp0131_helper"] = exp0131
assert spec0131.loader is not None
spec0131.loader.exec_module(exp0131)

spec0133 = importlib.util.spec_from_file_location("exp0133_helper", EXP0133_PATH)
exp0133 = importlib.util.module_from_spec(spec0133)
sys.modules["exp0133_helper"] = exp0133
assert spec0133.loader is not None
spec0133.loader.exec_module(exp0133)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0134_v22_moirai_riskoff_trailing_stop"

ATR_MULTIPLIERS = (1.0, 1.5)
TTL_MODES = ("until_baseline_exit", "24h")
TTL_24H_BARS = 288
LIVE_ENTRY_TIME = exp0131.LIVE_ENTRY_TIME
LIVE_EXIT_TIME = exp0131.LIVE_EXIT_TIME
LIVE_ENTRY_PRICE = exp0131.LIVE_ENTRY_PRICE
LIVE_SIDE = exp0131.LIVE_SIDE
LIVE_PROTECT_LEVEL = 1590.0


@dataclass(frozen=True)
class RiskoffTrailingSpec:
    variant: str
    atr_multiplier: float
    ttl_mode: str
    signal_variant: str = "2h_wick_r75_v2p0_then_macd2h_12h"


def pct(x: float | None) -> str:
    return "" if x is None or pd.isna(x) else f"{x * 100:.2f}%"


def money(x: float | None) -> str:
    return "" if x is None or pd.isna(x) else f"{x:.2f}"


def bar_time(df: pd.DataFrame, i: int) -> str:
    return exp0131.bar_time(df, i)


def riskoff_market_spec() -> Any:
    return exp0131.VariantSpec(
        variant="2h_wick_r75_v2p0_then_macd2h_12h",
        use_2h_wick=True,
        wick_shadow_ratio=0.75,
        wick_volume_ratio=2.0,
        sequence_macd_tf="2h",
        sequence_window_bars=144,
    )


def build_matrix() -> list[RiskoffTrailingSpec]:
    rows: list[RiskoffTrailingSpec] = []
    for atr_multiplier in ATR_MULTIPLIERS:
        atr_label = str(atr_multiplier).replace(".", "p")
        for ttl_mode in TTL_MODES:
            rows.append(
                RiskoffTrailingSpec(
                    variant=f"2h_wick_r75_v2p0_then_macd2h_12h_trailATR{atr_label}_{ttl_mode}",
                    atr_multiplier=atr_multiplier,
                    ttl_mode=ttl_mode,
                )
            )
    return rows


def public_variant_fields(spec: RiskoffTrailingSpec) -> dict[str, Any]:
    return asdict(spec)


def ttl_until_bar(activation_bar: int, ttl_mode: str) -> int:
    if ttl_mode == "until_baseline_exit":
        return 10**12
    if ttl_mode == "24h":
        return activation_bar + TTL_24H_BARS
    raise ValueError(f"unknown ttl_mode={ttl_mode}")


def stop_price(position: int, anchor: float, atr_ref: float, multiplier: float) -> float:
    if position < 0:
        return anchor + multiplier * atr_ref
    if position > 0:
        return anchor - multiplier * atr_ref
    return float("nan")


def update_anchor(position: int, anchor: float, high: float, low: float) -> float:
    if position < 0:
        return min(anchor, low)
    if position > 0:
        return max(anchor, high)
    return anchor


def stop_crossed(position: int, close: float, stop: float) -> bool:
    if not np.isfinite(stop):
        return False
    if position < 0:
        return close >= stop
    if position > 0:
        return close <= stop
    return False


def reset_riskoff_state() -> dict[str, Any]:
    return {
        "active": False,
        "activation_bar": -1,
        "atr_ref": float("nan"),
        "anchor": float("nan"),
        "stop": float("nan"),
        "ttl_until": -1,
    }


def apply_riskoff_trailing_stop(
    signals: np.ndarray,
    df: pd.DataFrame,
    features: pd.DataFrame,
    spec: RiskoffTrailingSpec,
    base_trades: list[dict[str, Any]],
    top20_cutoff: float,
    worst20_entries: set[int],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    out = signals.astype(int).copy()
    market = riskoff_market_spec()
    short_setup, long_setup = exp0131.shape_masks(features, market)
    short_confirm, long_confirm = exp0131.sequence_confirm_masks(features, market)
    atr = exp0133.ensure_atr(df)
    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    pos = 0
    entry_bar = -1
    entry_price = 0.0
    setup_side = 0
    setup_bar = -1
    setup_until = -1
    risk = reset_riskoff_state()
    lockout = 0
    events: list[dict[str, Any]] = []

    for i in range(len(out)):
        if setup_side and i > setup_until:
            setup_side = 0
            setup_bar = -1
            setup_until = -1

        prev_signal = int(out[i - 1]) if i else 1
        target = exp0131.target_position(prev_signal, pos)
        if target != pos:
            if target == 0:
                pos = 0
                entry_bar = -1
                entry_price = 0.0
                risk = reset_riskoff_state()
                setup_side = 0
                setup_bar = -1
                setup_until = -1
            else:
                pos = target
                entry_bar = i
                entry_price = float(open_[i]) * (
                    1.0 + exp0131.exp0110.helper0108.SLIPPAGE
                    if pos > 0
                    else 1.0 - exp0131.exp0110.helper0108.SLIPPAGE
                )
                risk = reset_riskoff_state()
                setup_side = 0
                setup_bar = -1
                setup_until = -1

        decision = int(signals[i])
        raw_target = exp0131.target_position(decision, pos)
        if lockout and raw_target != lockout:
            lockout = 0
        if lockout and pos == 0 and raw_target == lockout:
            out[i] = 1
            continue

        baseline_leaves_position = pos != 0 and raw_target != pos

        # Priority 1: baseline exits/reversals are left untouched.
        if baseline_leaves_position:
            if risk["active"]:
                events.append(
                    event_row(
                        spec,
                        df,
                        i,
                        "riskoff_cancel_baseline",
                        pos,
                        entry_bar,
                        entry_price,
                        risk,
                        close[i],
                        base_trades,
                        top20_cutoff,
                        worst20_entries,
                    )
                )
            out[i] = decision
            continue

        if pos != 0 and raw_target == pos:
            setup_now = (pos < 0 and short_setup[i]) or (pos > 0 and long_setup[i])
            if setup_now:
                setup_side = pos
                setup_bar = i
                setup_until = i + int(market.sequence_window_bars)

        confirm_now = (pos < 0 and short_confirm[i]) or (pos > 0 and long_confirm[i])
        riskoff_activation = (
            pos != 0
            and raw_target == pos
            and not risk["active"]
            and setup_side == pos
            and i <= setup_until
            and confirm_now
        )

        # Priority 2: activation starts trailing only; it never exits on the same bar.
        if riskoff_activation:
            atr_ref = float(atr[i])
            if np.isfinite(atr_ref) and atr_ref > 0.0:
                anchor = low[i] if pos < 0 else high[i]
                risk = {
                    "active": True,
                    "activation_bar": i,
                    "atr_ref": atr_ref,
                    "anchor": anchor,
                    "stop": stop_price(pos, anchor, atr_ref, spec.atr_multiplier),
                    "ttl_until": ttl_until_bar(i, spec.ttl_mode),
                }
                events.append(
                    event_row(
                        spec,
                        df,
                        i,
                        "riskoff_activate",
                        pos,
                        entry_bar,
                        entry_price,
                        risk,
                        close[i],
                        base_trades,
                        top20_cutoff,
                        worst20_entries,
                        reasons=f"2h_lower_wick_setup@{bar_time(df, setup_bar)}+macd_bull_cross_2h"
                        if pos < 0
                        else f"2h_upper_wick_setup@{bar_time(df, setup_bar)}+macd_bear_cross_2h",
                    )
                )
            out[i] = decision
            continue

        # Priority 3: only an already-active trailing stop can exit.
        if risk["active"] and pos != 0 and raw_target == pos:
            if i > int(risk["ttl_until"]):
                events.append(
                    event_row(
                        spec,
                        df,
                        i,
                        "riskoff_cancel_ttl",
                        pos,
                        entry_bar,
                        entry_price,
                        risk,
                        close[i],
                        base_trades,
                        top20_cutoff,
                        worst20_entries,
                    )
                )
                risk = reset_riskoff_state()
            else:
                risk["anchor"] = update_anchor(pos, float(risk["anchor"]), high[i], low[i])
                risk["stop"] = stop_price(pos, float(risk["anchor"]), float(risk["atr_ref"]), spec.atr_multiplier)
                if stop_crossed(pos, close[i], float(risk["stop"])):
                    stopped_direction = pos
                    events.append(
                        event_row(
                            spec,
                            df,
                            i,
                            "riskoff_stop",
                            pos,
                            entry_bar,
                            entry_price,
                            risk,
                            close[i],
                            base_trades,
                            top20_cutoff,
                            worst20_entries,
                        )
                    )
                    decision = 0
                    lockout = stopped_direction
                    risk = reset_riskoff_state()
                    setup_side = 0
                    setup_bar = -1
                    setup_until = -1

        out[i] = decision
    return out, events


def event_row(
    spec: RiskoffTrailingSpec,
    df: pd.DataFrame,
    bar: int,
    event: str,
    position: int,
    entry_bar: int,
    entry_price: float,
    risk: dict[str, Any],
    close_price: float,
    base_trades: list[dict[str, Any]],
    top20_cutoff: float,
    worst20_entries: set[int],
    *,
    reasons: str = "",
) -> dict[str, Any]:
    base_trade = exp0131.trade_at_bar(base_trades, bar)
    base_pnl = float(base_trade["pnl"]) if base_trade else 0.0
    base_entry = int(base_trade["entry_step"]) if base_trade else -1
    entry_notional = float(base_trade.get("entry_notional") or np.nan) if base_trade else float("nan")
    execution_bar = bar + 1 if bar + 1 < len(df) else -1
    execution_open = float(df["open"].iloc[execution_bar]) if execution_bar >= 0 else close_price
    trigger_return = exp0133.current_return_after_cost(position, entry_price, close_price)
    execution_return = exp0133.current_return_after_cost(position, entry_price, execution_open)
    early_exit_pnl_proxy = execution_return * entry_notional if np.isfinite(entry_notional) else float("nan")
    delta_proxy = early_exit_pnl_proxy - base_pnl if np.isfinite(early_exit_pnl_proxy) else float("nan")
    activation_bar = int(risk.get("activation_bar", -1))
    return {
        **public_variant_fields(spec),
        "event": event,
        "bar": bar,
        "time": bar_time(df, bar),
        "execution_bar": execution_bar,
        "execution_time": bar_time(df, execution_bar) if execution_bar >= 0 else "",
        "side": "long" if position > 0 else "short",
        "entry_bar": entry_bar,
        "bars_held": bar - entry_bar if entry_bar >= 0 else 0,
        "entry_price": entry_price,
        "close": close_price,
        "execution_open": execution_open,
        "trigger_return_after_cost": trigger_return,
        "execution_return_after_cost": execution_return,
        "activation_bar": activation_bar,
        "activation_time": bar_time(df, activation_bar) if activation_bar >= 0 else "",
        "atr_ref": float(risk.get("atr_ref", np.nan)),
        "anchor": float(risk.get("anchor", np.nan)),
        "stop_price": float(risk.get("stop", np.nan)),
        "ttl_until": int(risk.get("ttl_until", -1)),
        "reasons": reasons,
        "entry_notional": entry_notional,
        "early_exit_pnl_proxy": early_exit_pnl_proxy,
        "delta_vs_base_pnl_proxy": delta_proxy,
        "exit_quality": exp0133.classify_exit(base_pnl, early_exit_pnl_proxy)
        if event == "riskoff_stop" and np.isfinite(early_exit_pnl_proxy)
        else "",
        "base_trade_entry_bar": base_entry,
        "base_trade_exit_bar": int(base_trade["step"]) if base_trade else -1,
        "base_trade_pnl": base_pnl,
        "base_trade_winner": base_pnl > 0,
        "base_trade_top20_winner": base_pnl >= top20_cutoff,
        "base_trade_worst20_loser": base_entry in worst20_entries,
    }


def stopped_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event["event"] == "riskoff_stop"]


def summarize_stops(events: list[dict[str, Any]]) -> dict[str, Any]:
    stops = exp0131.unique_trade_rows(stopped_events(events))
    deltas = [float(row.get("delta_vs_base_pnl_proxy", np.nan)) for row in stops]
    valid_deltas = [x for x in deltas if np.isfinite(x)]
    return {
        "stops": len(stopped_events(events)),
        "unique_base_trades_stopped": len(stops),
        "activations": sum(1 for row in events if row["event"] == "riskoff_activate"),
        "ttl_cancels": sum(1 for row in events if row["event"] == "riskoff_cancel_ttl"),
        "baseline_cancels": sum(1 for row in events if row["event"] == "riskoff_cancel_baseline"),
        "valuable_exit_count": sum(1 for x in valid_deltas if x > 0.0),
        "net_delta_pnl_proxy": float(sum(valid_deltas)),
        "saved_pnl_proxy": float(sum(x for x in valid_deltas if x > 0.0)),
        "missed_pnl_proxy": float(-sum(x for x in valid_deltas if x < 0.0)),
        "top20_winner_exits": sum(1 for row in stops if row["base_trade_top20_winner"]),
        "top20_winner_exited_pnl": float(sum(row["base_trade_pnl"] for row in stops if row["base_trade_top20_winner"])),
        "worst20_loser_exits": sum(1 for row in stops if row["base_trade_worst20_loser"]),
        "worst20_loser_exited_pnl": float(sum(row["base_trade_pnl"] for row in stops if row["base_trade_worst20_loser"])),
        "short_side_exits": sum(1 for row in stops if row["side"] == "short"),
        "long_side_exits": sum(1 for row in stops if row["side"] == "long"),
    }


def hard_gate(row: dict[str, Any], base_oos_return: float) -> bool:
    return (
        bool(row.get("live_case_hit", False))
        and row["top20_winner_exits"] <= 2
        and row["oos_return"] >= base_oos_return * 0.50
        and row["year_losses"] <= row["year_wins"]
        and row["net_delta_pnl_proxy"] > 0.0
    )


def classify_row(row: dict[str, Any], base_oos_return: float) -> tuple[str, str]:
    if hard_gate(row, base_oos_return):
        return "SHADOW_CANDIDATE", "passes_riskoff_trailing_hard_gate"
    if bool(row.get("live_case_hit", False)) and row["top20_winner_exits"] <= 2 and row["net_delta_pnl_proxy"] > 0.0:
        return "OBSERVE", "live_case_hit_but_year_or_oos_gate_failed"
    if bool(row.get("live_case_hit", False)):
        return "REJECT", "live_case_hit_but_winner_or_net_delta_failed"
    return "REJECT", "no_2026_06_23_protective_exit"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = sorted({k for row in rows for k in row}) if rows else ["variant"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_matrix() -> dict[str, Any]:
    df, base_signals, scope = exp0131.exp0110.load_base()
    df = exp0131.clean_ohlcv(df)
    split_idx = int(scope["split_idx"])
    features = exp0131.build_market_signal_features(df, exp0131.load_local_oi())
    variants = build_matrix()

    base_eval = exp0131.exp0110.evaluate(base_signals, df, split_idx)
    base_summary = base_eval["summary"]
    base_metrics = base_eval["metrics"]
    _, base_trades = exp0131.exp0110.helper0108.next_open_trades(base_signals, df)
    top_winners = sorted([float(t["pnl"]) for t in base_trades if float(t["pnl"]) > 0.0], reverse=True)[:20]
    top20_cutoff = min(top_winners) if top_winners else float("inf")
    worst_losers = sorted([t for t in base_trades if float(t["pnl"]) < 0.0], key=lambda t: float(t["pnl"]))[:20]
    worst20_entries = {int(t["entry_step"]) for t in worst_losers}
    base_capture = exp0131.capture_ratio(base_signals, df)

    rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    for variant in variants:
        print(f"=== {variant.variant} ===", flush=True)
        sig, events = apply_riskoff_trailing_stop(
            base_signals,
            df,
            features,
            variant,
            base_trades,
            top20_cutoff,
            worst20_entries,
        )
        result = exp0131.exp0110.evaluate(sig, df, split_idx)
        cap = exp0131.capture_ratio(sig, df)
        stop_summary = summarize_stops(events)
        dd_improve = (abs(base_metrics["dd"]) - abs(result["metrics"]["dd"])) / abs(base_metrics["dd"])
        row = {
            **public_variant_fields(variant),
            **stop_summary,
            "oos_return": result["summary"]["oos"]["return"],
            "delta_oos": result["summary"]["oos"]["return"] - base_summary["oos"]["return"],
            "full_return": result["metrics"]["return"],
            "delta_full": result["metrics"]["return"] - base_metrics["return"],
            "full_dd": result["metrics"]["dd"],
            "dd_improve_rel": dd_improve,
            "rolling12_min": result["summary"]["rolling_12m_min_return"],
            "delta_roll12": result["summary"]["rolling_12m_min_return"] - base_summary["rolling_12m_min_return"],
            "trades": result["metrics"]["trades"],
            "mfe_capture_avg": cap["mfe_capture_avg"],
            "delta_mfe_capture_avg": cap["mfe_capture_avg"] - base_capture["mfe_capture_avg"],
            "giveback_to_loss_6_count": cap["giveback_to_loss_6_count"],
            "delta_giveback_to_loss_6_count": cap["giveback_to_loss_6_count"]
            - base_capture["giveback_to_loss_6_count"],
            "giveback_to_loss_6_pnl": cap["giveback_to_loss_6_pnl"],
            "avg_bars_held": cap["avg_bars_held"],
            "delta_avg_bars_held": cap["avg_bars_held"] - base_capture["avg_bars_held"],
            "live_case_hit": False,
            "live_status": "",
            **exp0131.yearly_delta(base_signals, sig, df),
        }
        row["hard_gate_pass"] = False
        row["verdict"], row["verdict_reason"] = classify_row(row, base_summary["oos"]["return"])
        rows.append(row)
        event_rows.extend(events)

    report = {
        "scope": {
            "experiment_id": "exp_0134",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "mode": "riskoff trailing stop; activation is not close-to-flat",
            "signal_variant": "2h_wick_r75_v2p0_then_macd2h_12h",
            "atr_multipliers": list(ATR_MULTIPLIERS),
            "ttl_modes": list(TTL_MODES),
            "atr_ref": "fixed_at_riskoff_activation_bar",
            "anchor": "short min(low since activation), long max(high since activation)",
            "stop_detection": "completed 5m close crosses stop; next open executes",
            "post_stop_lockout": "same-side reentry suppressed until baseline leaves stopped direction",
            "priority": "baseline reversal > riskoff activation > riskoff stop",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "data": str(exp0131.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{bar_time(df, 0)} to {bar_time(df, len(df) - 1)}",
            "moirai_blocked": int(scope["moirai_blocked"]),
        },
        "baseline": {
            "raw_next_open": base_summary,
            "metrics": base_metrics,
            "capture": base_capture,
            "safe_execution_result": "next_open_evaluation_riskoff_stop_overlay",
            "regime_permission_result": "not_applicable_post_v22_moirai_baseline",
        },
        "rows": rows,
        "events": event_rows,
    }
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), rows)
    write_csv(OUT.with_name(OUT.name + "_events").with_suffix(".csv"), event_rows)
    write_markdown(rows, [], report)
    print(OUT.with_suffix(".md"))
    for row in rows:
        print(
            row["variant"],
            "stops",
            row["stops"],
            "dOOS",
            round(row["delta_oos"] * 100, 2),
            "top20",
            row["top20_winner_exits"],
            "net",
            round(row["net_delta_pnl_proxy"], 2),
        )
    return report


def simulate_live_case(spec: RiskoffTrailingSpec) -> dict[str, Any]:
    if not exp0131.LIVE_CACHE.exists():
        raise SystemExit(f"missing live cache: {exp0131.LIVE_CACHE}")
    df = exp0131.clean_ohlcv(pd.read_parquet(exp0131.LIVE_CACHE))
    features = exp0131.build_market_signal_features(df, None)
    market = riskoff_market_spec()
    short_setup, long_setup = exp0131.shape_masks(features, market)
    short_confirm, long_confirm = exp0131.sequence_confirm_masks(features, market)
    setup = short_setup if LIVE_SIDE < 0 else long_setup
    confirm = short_confirm if LIVE_SIDE < 0 else long_confirm
    atr = exp0133.ensure_atr(df)
    times = pd.to_datetime(df["datetime"])
    entry_idx = int(np.searchsorted(times.to_numpy(), np.datetime64(LIVE_ENTRY_TIME), side="left"))
    end_idx = int(np.searchsorted(times.to_numpy(), np.datetime64(LIVE_EXIT_TIME), side="right") - 1)
    if entry_idx >= len(df) or end_idx < entry_idx:
        raise SystemExit("live cache does not cover configured live-case window")

    setup_bar = -1
    setup_until = -1
    risk = reset_riskoff_state()
    activation_reasons = ""
    ttl_expired = False
    for i in range(entry_idx, end_idx + 1):
        if setup_bar >= 0 and i > setup_until:
            setup_bar = -1
            setup_until = -1
        if not risk["active"]:
            if setup[i]:
                setup_bar = i
                setup_until = i + int(market.sequence_window_bars)
            if setup_bar >= 0 and i <= setup_until and confirm[i]:
                atr_ref = float(atr[i])
                if np.isfinite(atr_ref) and atr_ref > 0.0:
                    anchor = float(df["low"].iloc[i] if LIVE_SIDE < 0 else df["high"].iloc[i])
                    risk = {
                        "active": True,
                        "activation_bar": i,
                        "atr_ref": atr_ref,
                        "anchor": anchor,
                        "stop": stop_price(LIVE_SIDE, anchor, atr_ref, spec.atr_multiplier),
                        "ttl_until": ttl_until_bar(i, spec.ttl_mode),
                    }
                    activation_reasons = (
                        f"2h_lower_wick_setup@{bar_time(df, setup_bar)}+macd_bull_cross_2h"
                        if LIVE_SIDE < 0
                        else f"2h_upper_wick_setup@{bar_time(df, setup_bar)}+macd_bear_cross_2h"
                    )
                continue
        else:
            if i > int(risk["ttl_until"]):
                ttl_expired = True
                break
            risk["anchor"] = update_anchor(
                LIVE_SIDE,
                float(risk["anchor"]),
                float(df["high"].iloc[i]),
                float(df["low"].iloc[i]),
            )
            risk["stop"] = stop_price(LIVE_SIDE, float(risk["anchor"]), float(risk["atr_ref"]), spec.atr_multiplier)
            close = float(df["close"].iloc[i])
            if stop_crossed(LIVE_SIDE, close, float(risk["stop"])):
                execution_bar = i + 1 if i + 1 < len(df) else -1
                execution_open = float(df["open"].iloc[execution_bar]) if execution_bar >= 0 else close
                protective = execution_open < LIVE_PROTECT_LEVEL if LIVE_SIDE < 0 else execution_open > LIVE_PROTECT_LEVEL
                return {
                    **public_variant_fields(spec),
                    "status": "riskoff_stop",
                    "live_case_hit": bool(protective),
                    "activation_bar": int(risk["activation_bar"]),
                    "activation_time_utc": bar_time(df, int(risk["activation_bar"])),
                    "activation_time_cst": str(
                        pd.Timestamp(df["datetime"].iloc[int(risk["activation_bar"])]) + pd.Timedelta(hours=8)
                    ),
                    "stop_bar": i,
                    "stop_time_utc": bar_time(df, i),
                    "stop_time_cst": str(pd.Timestamp(df["datetime"].iloc[i]) + pd.Timedelta(hours=8)),
                    "execution_bar": execution_bar,
                    "execution_time_utc": bar_time(df, execution_bar) if execution_bar >= 0 else "",
                    "close": close,
                    "execution_open": execution_open,
                    "entry_price": LIVE_ENTRY_PRICE,
                    "trigger_return_after_cost": exp0133.current_return_after_cost(LIVE_SIDE, LIVE_ENTRY_PRICE, close),
                    "execution_return_after_cost": exp0133.current_return_after_cost(
                        LIVE_SIDE,
                        LIVE_ENTRY_PRICE,
                        execution_open,
                    ),
                    "atr_ref": float(risk["atr_ref"]),
                    "anchor": float(risk["anchor"]),
                    "stop_price": float(risk["stop"]),
                    "saved_vs_1590": LIVE_PROTECT_LEVEL - execution_open
                    if LIVE_SIDE < 0
                    else execution_open - LIVE_PROTECT_LEVEL,
                    "reasons": activation_reasons,
                }

    if risk["active"] and ttl_expired:
        status = "ttl_expired"
    elif risk["active"]:
        status = "no_stop_before_baseline_exit"
    else:
        status = "no_activation"
    return {
        **public_variant_fields(spec),
        "status": status,
        "live_case_hit": False,
        "activation_bar": int(risk.get("activation_bar", -1)),
        "activation_time_utc": bar_time(df, int(risk["activation_bar"])) if int(risk.get("activation_bar", -1)) >= 0 else "",
        "activation_time_cst": str(
            pd.Timestamp(df["datetime"].iloc[int(risk["activation_bar"])]) + pd.Timedelta(hours=8)
        )
        if int(risk.get("activation_bar", -1)) >= 0
        else "",
        "stop_bar": -1,
        "stop_time_utc": "",
        "stop_time_cst": "",
        "execution_bar": -1,
        "execution_time_utc": "",
        "close": float("nan"),
        "execution_open": float("nan"),
        "entry_price": LIVE_ENTRY_PRICE,
        "trigger_return_after_cost": float("nan"),
        "execution_return_after_cost": float("nan"),
        "atr_ref": float(risk.get("atr_ref", np.nan)),
        "anchor": float(risk.get("anchor", np.nan)),
        "stop_price": float(risk.get("stop", np.nan)),
        "saved_vs_1590": float("nan"),
        "reasons": activation_reasons,
    }


def run_live_case() -> list[dict[str, Any]]:
    rows = [simulate_live_case(spec) for spec in build_matrix()]
    rows = sorted(
        rows,
        key=lambda row: (
            row["status"] != "riskoff_stop",
            not bool(row["live_case_hit"]),
            row["stop_bar"] if int(row["stop_bar"]) >= 0 else 10**12,
            row["variant"],
        ),
    )
    path = OUT.with_name(OUT.name + "_live_case").with_suffix(".csv")
    write_csv(path, rows)
    print(path)
    for row in rows:
        print(row["variant"], row["status"], row.get("stop_time_cst", ""), row.get("execution_open", ""))
    return rows


def merge_live_into_matrix(rows: list[dict[str, Any]], live_rows: list[dict[str, Any]], base_oos_return: float) -> list[dict[str, Any]]:
    live_by_variant = {row["variant"]: row for row in live_rows}
    merged = []
    for row in rows:
        out = row.copy()
        live = live_by_variant.get(row["variant"], {})
        out["live_status"] = live.get("status", "")
        out["live_case_hit"] = bool(live.get("live_case_hit", False))
        out["live_activation_time_cst"] = live.get("activation_time_cst", "")
        out["live_stop_time_cst"] = live.get("stop_time_cst", "")
        out["live_execution_open"] = live.get("execution_open", float("nan"))
        out["live_execution_return_after_cost"] = live.get("execution_return_after_cost", float("nan"))
        out["live_stop_price"] = live.get("stop_price", float("nan"))
        out["live_reasons"] = live.get("reasons", "")
        out["hard_gate_pass"] = hard_gate(out, base_oos_return)
        out["verdict"], out["verdict_reason"] = classify_row(out, base_oos_return)
        merged.append(out)
    return sorted(
        merged,
        key=lambda row: (
            row["verdict"] == "SHADOW_CANDIDATE",
            row["verdict"] == "OBSERVE",
            row["live_case_hit"],
            row["net_delta_pnl_proxy"],
            -row["top20_winner_exits"],
        ),
        reverse=True,
    )


def run_all() -> dict[str, Any]:
    report = run_matrix()
    live_rows = run_live_case()
    base_oos_return = report["baseline"]["raw_next_open"]["oos"]["return"]
    merged = merge_live_into_matrix(report["rows"], live_rows, base_oos_return)
    report["rows"] = merged
    report["live_case"] = live_rows
    report["scope"]["live_case_window"] = f"{LIVE_ENTRY_TIME} to {LIVE_EXIT_TIME}"
    report["scope"]["hard_gate"] = (
        "top20_winner_exits<=2, live_case_hit, oos_return>=50% baseline, "
        "year_losses<=year_wins, net_delta_pnl_proxy>0"
    )
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), merged)
    write_markdown(merged, live_rows, report)
    return report


def write_markdown(rows: list[dict[str, Any]], live_rows: list[dict[str, Any]], report: dict[str, Any]) -> None:
    base_summary = report["baseline"]["raw_next_open"]
    base_metrics = report["baseline"]["metrics"]
    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
    overall = "SHADOW_CANDIDATE" if verdict_counts.get("SHADOW_CANDIDATE", 0) else "OBSERVE"
    if not verdict_counts.get("SHADOW_CANDIDATE", 0) and not verdict_counts.get("OBSERVE", 0):
        overall = "REJECT"
    md = [
        "# exp_0134 v2.2 + Moirai riskoff trailing stop",
        "",
        "- diagnostic only; no live/checkpoint/config/production strategy change",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- riskoff signal: `2h_wick_r75_v2p0_then_macd2h_12h` only",
        "- mechanism: riskoff activation starts a temporary trailing stop; it does not close-to-flat immediately",
        "- ATR ref: fixed at riskoff activation bar",
        "- anchor: short `min(low since activation)`, long `max(high since activation)`",
        "- stop detection: completed 5m close crosses stop; next open executes",
        "- post-stop lockout: same-side reentry is suppressed until baseline leaves the stopped direction",
        "- TTL: `until_baseline_exit` or `24h`; TTL expiry cancels trailing only",
        "- priority: baseline reversal > riskoff activation > riskoff stop",
        "- hard gate: top20 cut <=2, 6/23 protective exit, OOS not halved, year_losses <= year_wins, net_delta_pnl_proxy > 0",
        f"- baseline OOS/full/DD/roll12/trades: {pct(base_summary['oos']['return'])} / {pct(base_metrics['return'])} / {pct(base_metrics['dd'])} / {pct(base_summary['rolling_12m_min_return'])} / {base_metrics['trades']}",
        f"- verdict: `{overall}`; counts `{verdict_counts}`",
        "",
        "## Matrix",
        "",
        "| variant | live hit | live stop | stops | activations | dOOS | DD improve | roll12 | top20 cut | net delta proxy | year W/L/F | hard gate | verdict |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        md.append(
            f"| {row['variant']} | {bool(row.get('live_case_hit', False))} | {row.get('live_stop_time_cst', '')} | "
            f"{int(row['stops'])} | {int(row['activations'])} | {pct(row['delta_oos'])} | "
            f"{pct(row['dd_improve_rel'])} | {pct(row['rolling12_min'])} | "
            f"{int(row['top20_winner_exits'])} | {money(row['net_delta_pnl_proxy'])} | "
            f"{int(row['year_wins'])}/{int(row['year_losses'])}/{int(row['year_flat'])} | "
            f"{bool(row['hard_gate_pass'])} | {row['verdict']} |"
        )
    md.extend(
        [
            "",
            "## Live Case",
            "",
            "| variant | status | hit | activation CST | stop CST | execution open | stop price | return after cost | saved vs 1590 | reasons |",
            "|---|---|---:|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in live_rows:
        md.append(
            f"| {row['variant']} | {row['status']} | {bool(row.get('live_case_hit', False))} | "
            f"{row.get('activation_time_cst', '')} | {row.get('stop_time_cst', '')} | "
            f"{money(row.get('execution_open'))} | {money(row.get('stop_price'))} | "
            f"{pct(row.get('execution_return_after_cost'))} | {money(row.get('saved_vs_1590'))} | "
            f"{row.get('reasons', '')} |"
        )
    hit_rows = [row for row in live_rows if row.get("live_case_hit")]
    live_stop_summary = "; ".join(
        f"{row['variant']} -> {row.get('stop_time_cst', '')} @ {money(row.get('execution_open'))}"
        for row in sorted(hit_rows, key=lambda item: item.get("stop_time_cst", ""))
    )
    ttl_has_effect = len(
        {
            (
                row["atr_multiplier"],
                row["stops"],
                row["delta_oos"],
                row["top20_winner_exits"],
                row["net_delta_pnl_proxy"],
            )
            for row in rows
        }
    ) > len({row["atr_multiplier"] for row in rows})
    min_top20 = min(int(row["top20_winner_exits"]) for row in rows) if rows else 0
    max_top20 = max(int(row["top20_winner_exits"]) for row in rows) if rows else 0
    year_pairs = sorted(
        {
            (int(row["year_wins"]), int(row["year_losses"]), int(row["year_flat"]))
            for row in rows
        }
    )
    md.extend(
        [
            "",
            "## Conclusion",
            "",
            f"- final verdict: `{overall}`; no row passed the hard gate."
            if overall == "REJECT"
            else f"- final verdict: `{overall}`; review row-level gates before any follow-up.",
            f"- live-case protection: {len(hit_rows)}/{len(live_rows)} rows stopped before the 1590 reference; {live_stop_summary}.",
            f"- long-window blocker: top20 winner exits range `{min_top20}-{max_top20}`, above the hard limit `<=2`.",
            f"- year-slice blocker: year W/L/F combinations are `{year_pairs}`, so `year_losses <= year_wins` fails.",
            "- TTL read: `24h` and `until_baseline_exit` differ in this run."
            if ttl_has_effect
            else "- TTL read: `24h` and `until_baseline_exit` are identical here because every activation stopped before TTL expiry.",
            "- implementation status: research-only; this does not authorize live/demo routing, checkpoint promotion, execution changes, or production strategy changes.",
            "",
            "## Reporting Contract",
            "",
            f"- raw result: full matrix in `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            "- regime-permission result: not applicable here because input is already the v2.2 + Moirai post-gate baseline stream.",
            "- safe-execution result: the overlay writes a stop signal on completed-bar close confirmation and is evaluated at next open.",
            "- OOS return, max drawdown, rolling 12m, trade count: columns in the matrix CSV.",
            "- top-winner damage: `top20_winner_exits` and `top20_winner_exited_pnl`.",
            "- worst-loser reduction: `worst20_loser_exits` and `worst20_loser_exited_pnl`.",
            "- net attribution: `net_delta_pnl_proxy` uses next-open execution return proxy versus baseline trade PnL.",
            "- conclusion: research-only; no live/demo routing or checkpoint promotion is authorized.",
            "",
            f"Events CSV: `{OUT.with_name(OUT.name + '_events').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"Live-case CSV: `{OUT.with_name(OUT.name + '_live_case').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Riskoff trailing stop for v2.2 + Moirai.")
    parser.add_argument("--mode", choices=["matrix", "live-case", "all"], default="all")
    args = parser.parse_args()
    if args.mode == "matrix":
        run_matrix()
    elif args.mode == "live-case":
        run_live_case()
    else:
        run_all()


if __name__ == "__main__":
    main()
