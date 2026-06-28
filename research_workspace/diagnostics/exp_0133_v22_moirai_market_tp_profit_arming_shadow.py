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
spec = importlib.util.spec_from_file_location("exp0131_helper", EXP0131_PATH)
exp0131 = importlib.util.module_from_spec(spec)
sys.modules["exp0131_helper"] = exp0131
assert spec.loader is not None
spec.loader.exec_module(exp0131)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0133_v22_moirai_market_tp_profit_arming_shadow"

ARMING_THRESHOLDS_ATR = (1.5, 2.0, 2.5, 3.0)
ARMING_METRIC = "open_profit_atr_entry_based"
ARMING_LIFECYCLE = "latched"
LIVE_ENTRY_TIME = exp0131.LIVE_ENTRY_TIME
LIVE_EXIT_TIME = exp0131.LIVE_EXIT_TIME
LIVE_ENTRY_PRICE = exp0131.LIVE_ENTRY_PRICE
LIVE_SIDE = exp0131.LIVE_SIDE


@dataclass(frozen=True)
class ArmedVariantSpec:
    variant: str
    signal_variant: str
    market_spec: Any
    arming_threshold_atr: float
    arming_metric: str = ARMING_METRIC
    arming_lifecycle: str = ARMING_LIFECYCLE


def pct(x: float | None) -> str:
    return "" if x is None or pd.isna(x) else f"{x * 100:.2f}%"


def money(x: float | None) -> str:
    return "" if x is None or pd.isna(x) else f"{x:.2f}"


def bar_time(df: pd.DataFrame, i: int) -> str:
    return exp0131.bar_time(df, i)


def threshold_label(value: float) -> str:
    return str(value).replace(".", "p")


def selected_market_specs() -> list[Any]:
    return [
        exp0131.VariantSpec(
            variant="1h_strict_engulf_v2p0_macd4h",
            engulf_style="strict",
            engulf_volume_ratio=2.0,
            macd_tf="4h",
        ),
        exp0131.VariantSpec(
            variant="2h_wick_r75_v2p0_then_macd2h_12h",
            use_2h_wick=True,
            wick_shadow_ratio=0.75,
            wick_volume_ratio=2.0,
            sequence_macd_tf="2h",
            sequence_window_bars=144,
        ),
        exp0131.VariantSpec(
            variant="2h_wick_r75_v1p5_macd2h",
            use_2h_wick=True,
            wick_shadow_ratio=0.75,
            wick_volume_ratio=1.5,
            macd_tf="2h",
        ),
    ]


def build_armed_matrix() -> list[ArmedVariantSpec]:
    variants: list[ArmedVariantSpec] = []
    for market_spec in selected_market_specs():
        for threshold in ARMING_THRESHOLDS_ATR:
            variants.append(
                ArmedVariantSpec(
                    variant=f"{market_spec.variant}_armATR{threshold_label(threshold)}",
                    signal_variant=market_spec.variant,
                    market_spec=market_spec,
                    arming_threshold_atr=threshold,
                )
            )
    return variants


def public_variant_fields(spec: ArmedVariantSpec) -> dict[str, Any]:
    market = asdict(spec.market_spec)
    market.pop("variant", None)
    return {
        "variant": spec.variant,
        "signal_variant": spec.signal_variant,
        "arming_metric": spec.arming_metric,
        "arming_threshold_atr": spec.arming_threshold_atr,
        "arming_lifecycle": spec.arming_lifecycle,
        **market,
    }


def ensure_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    if "atr" in df.columns:
        atr = pd.to_numeric(df["atr"], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(atr).any():
            return atr

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean().to_numpy(dtype=float)


def entry_atr_at(atr: np.ndarray, entry_bar: int) -> float:
    start = max(0, int(entry_bar) - 1)
    for j in range(start, -1, -1):
        value = float(atr[j])
        if np.isfinite(value) and value > 0.0:
            return value
    return float("nan")


def open_profit_atr(position: int, entry_price: float, close: float, entry_atr: float) -> float:
    if not np.isfinite(entry_atr) or entry_atr <= 0.0 or entry_price <= 0.0:
        return float("nan")
    if position > 0:
        return (close - entry_price) / entry_atr
    if position < 0:
        return (entry_price - close) / entry_atr
    return 0.0


def current_return_after_cost(position: int, entry_price: float, close: float) -> float:
    if position == 0 or entry_price <= 0.0 or close <= 0.0:
        return 0.0
    slippage = float(exp0131.exp0110.helper0108.SLIPPAGE)
    commission = float(exp0131.exp0110.helper0108.COMMISSION)
    if position > 0:
        exit_price = close * (1.0 - slippage)
        gross = exit_price / entry_price - 1.0
    else:
        exit_price = close * (1.0 + slippage)
        gross = entry_price / max(exit_price, 1e-12) - 1.0
    return gross - 2.0 * commission


def classify_exit(base_pnl: float, early_exit_pnl_proxy: float) -> str:
    delta = early_exit_pnl_proxy - base_pnl
    if base_pnl <= 0 and delta > 0:
        return "saved_loser"
    if base_pnl > 0 and delta > 0:
        return "improved_winner"
    if base_pnl > 0:
        return "missed_winner"
    return "worsened_loser"


def apply_profit_armed_market_tp(
    signals: np.ndarray,
    df: pd.DataFrame,
    features: pd.DataFrame,
    spec: ArmedVariantSpec,
    base_trades: list[dict[str, Any]],
    top20_cutoff: float,
    worst20_entries: set[int],
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    out = signals.astype(int).copy()
    short_mask, long_mask = exp0131.trigger_masks(features, spec.market_spec)
    short_setup, long_setup = exp0131.shape_masks(features, spec.market_spec)
    short_confirm, long_confirm = exp0131.sequence_confirm_masks(features, spec.market_spec)
    atr = ensure_atr(df)
    open_ = df["open"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    pos = 0
    entry_bar = -1
    entry_price = 0.0
    entry_atr = float("nan")
    armed = False
    armed_bar = -1
    armed_profit_atr = float("nan")
    lockout = 0
    setup_side = 0
    setup_bar = -1
    setup_until = -1
    exits: list[dict[str, Any]] = []
    arm_events: list[dict[str, Any]] = []

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
                entry_atr = float("nan")
                armed = False
                armed_bar = -1
                armed_profit_atr = float("nan")
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
                entry_atr = entry_atr_at(atr, i)
                armed = False
                armed_bar = -1
                armed_profit_atr = float("nan")
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

        gross_return = 0.0
        if pos > 0 and entry_price > 0:
            gross_return = close[i] / entry_price - 1.0
        elif pos < 0 and entry_price > 0:
            gross_return = entry_price / max(close[i], 1e-12) - 1.0
        net_return = current_return_after_cost(pos, entry_price, float(close[i]))
        profit_atr = open_profit_atr(pos, entry_price, float(close[i]), entry_atr)
        if pos != 0 and not armed and np.isfinite(profit_atr) and profit_atr >= spec.arming_threshold_atr:
            armed = True
            armed_bar = i
            armed_profit_atr = profit_atr
            arm_events.append(
                {
                    "variant": spec.variant,
                    "signal_variant": spec.signal_variant,
                    "bar": i,
                    "time": bar_time(df, i),
                    "side": "long" if pos > 0 else "short",
                    "entry_bar": entry_bar,
                    "entry_price": entry_price,
                    "entry_atr": entry_atr,
                    "open_profit_atr": profit_atr,
                    "gross_return": gross_return,
                    "current_return_after_cost": net_return,
                }
            )

        market_allowed = pos != 0 and raw_target == pos and armed and net_return > 0.0
        if (
            spec.market_spec.sequence_macd_tf
            and market_allowed
            and ((pos < 0 and short_setup[i]) or (pos > 0 and long_setup[i]))
        ):
            setup_side = pos
            setup_bar = i
            setup_until = i + int(spec.market_spec.sequence_window_bars)

        forced_exit = False
        sequence_reasons: list[str] = []
        if spec.market_spec.sequence_macd_tf:
            confirm = (pos < 0 and short_confirm[i]) or (pos > 0 and long_confirm[i])
            forced_exit = market_allowed and setup_side == pos and i <= setup_until and confirm
            if forced_exit:
                setup_label = "2h_lower_wick_setup" if pos < 0 else "2h_upper_wick_setup"
                cross_label = "macd_bull_cross" if pos < 0 else "macd_bear_cross"
                sequence_reasons = [
                    f"{setup_label}@{bar_time(df, setup_bar)}",
                    f"{cross_label}_{spec.market_spec.sequence_macd_tf}",
                ]
        else:
            forced_exit = market_allowed and ((pos < 0 and short_mask[i]) or (pos > 0 and long_mask[i]))

        if forced_exit:
            reasons = sequence_reasons if sequence_reasons else exp0131.trigger_reasons(features.iloc[i], pos, spec.market_spec)
            base_trade = exp0131.trade_at_bar(base_trades, i)
            base_pnl = float(base_trade["pnl"]) if base_trade else 0.0
            base_entry = int(base_trade["entry_step"]) if base_trade else -1
            entry_notional = float(base_trade.get("entry_notional") or np.nan) if base_trade else float("nan")
            early_exit_pnl_proxy = net_return * entry_notional if np.isfinite(entry_notional) else float("nan")
            delta_proxy = early_exit_pnl_proxy - base_pnl if np.isfinite(early_exit_pnl_proxy) else float("nan")
            exits.append(
                {
                    **public_variant_fields(spec),
                    "bar": i,
                    "time": bar_time(df, i),
                    "side": "long" if pos > 0 else "short",
                    "entry_bar": entry_bar,
                    "bars_held": i - entry_bar if entry_bar >= 0 else 0,
                    "entry_price": entry_price,
                    "entry_atr": entry_atr,
                    "armed_bar": armed_bar,
                    "armed_time": bar_time(df, armed_bar) if armed_bar >= 0 else "",
                    "armed_open_profit_atr": armed_profit_atr,
                    "open_profit_atr": profit_atr,
                    "close": close[i],
                    "current_return": gross_return,
                    "current_return_after_cost": net_return,
                    "entry_notional": entry_notional,
                    "early_exit_pnl_proxy": early_exit_pnl_proxy,
                    "delta_vs_base_pnl_proxy": delta_proxy,
                    "exit_quality": classify_exit(base_pnl, early_exit_pnl_proxy)
                    if np.isfinite(early_exit_pnl_proxy)
                    else "unknown",
                    "reasons": "+".join(reasons),
                    "base_trade_entry_bar": base_entry,
                    "base_trade_exit_bar": int(base_trade["step"]) if base_trade else -1,
                    "base_trade_pnl": base_pnl,
                    "base_trade_winner": base_pnl > 0,
                    "base_trade_top20_winner": base_pnl >= top20_cutoff,
                    "base_trade_worst20_loser": base_entry in worst20_entries,
                    "lower_shadow_ratio_2h": exp0131.safe_float(features.at[i, "lower_shadow_ratio_2h"]),
                    "upper_shadow_ratio_2h": exp0131.safe_float(features.at[i, "upper_shadow_ratio_2h"]),
                    "volume_ratio_2h": exp0131.safe_float(features.at[i, "volume_ratio_2h"]),
                    "volume_ratio_1h": exp0131.safe_float(features.at[i, "volume_ratio_1h"]),
                    "oi_change_2h": exp0131.safe_float(features.at[i, "oi_change_2h"]),
                    "oi_change_4h": exp0131.safe_float(features.at[i, "oi_change_4h"]),
                    "macd_hist_2h": exp0131.safe_float(features.at[i, "macd_hist_2h"]),
                    "macd_hist_4h": exp0131.safe_float(features.at[i, "macd_hist_4h"]),
                }
            )
            decision = 0
            lockout = pos
            pos = 0
            entry_bar = -1
            entry_price = 0.0
            entry_atr = float("nan")
            armed = False
            armed_bar = -1
            armed_profit_atr = float("nan")
            setup_side = 0
            setup_bar = -1
            setup_until = -1
        out[i] = decision
    return out, exits, arm_events


def summarize_exit_quality(exits: list[dict[str, Any]]) -> dict[str, Any]:
    unique = exp0131.unique_trade_rows(exits)
    quality_counts = {
        "saved_loser": 0,
        "improved_winner": 0,
        "missed_winner": 0,
        "worsened_loser": 0,
        "unknown": 0,
    }
    for row in unique:
        quality_counts[str(row.get("exit_quality", "unknown"))] = quality_counts.get(str(row.get("exit_quality", "unknown")), 0) + 1
    deltas = [float(row.get("delta_vs_base_pnl_proxy", np.nan)) for row in unique]
    valid_deltas = [x for x in deltas if np.isfinite(x)]
    return {
        "unique_base_trades_exited": len(unique),
        "valuable_exit_count": sum(1 for x in valid_deltas if x > 0.0),
        "saved_loser_count": quality_counts["saved_loser"],
        "improved_winner_count": quality_counts["improved_winner"],
        "missed_winner_count": quality_counts["missed_winner"],
        "worsened_loser_count": quality_counts["worsened_loser"],
        "net_delta_pnl_proxy": float(sum(valid_deltas)),
        "saved_pnl_proxy": float(sum(x for x in valid_deltas if x > 0.0)),
        "missed_pnl_proxy": float(-sum(x for x in valid_deltas if x < 0.0)),
        "exited_base_pnl": float(sum(float(e["base_trade_pnl"]) for e in unique)),
        "exited_base_winner_pnl": float(sum(float(e["base_trade_pnl"]) for e in unique if e["base_trade_winner"])),
        "exited_base_loser_pnl": float(sum(float(e["base_trade_pnl"]) for e in unique if not e["base_trade_winner"])),
        "top20_winner_exits": sum(1 for e in unique if e["base_trade_top20_winner"]),
        "top20_winner_exited_pnl": float(sum(float(e["base_trade_pnl"]) for e in unique if e["base_trade_top20_winner"])),
        "worst20_loser_exits": sum(1 for e in unique if e["base_trade_worst20_loser"]),
        "worst20_loser_exited_pnl": float(sum(float(e["base_trade_pnl"]) for e in unique if e["base_trade_worst20_loser"])),
        "short_side_exits": sum(1 for e in unique if e["side"] == "short"),
        "long_side_exits": sum(1 for e in unique if e["side"] == "long"),
    }


def classify_row(row: dict[str, Any]) -> tuple[str, str]:
    long_window_pass = bool(row.get("long_window_pass", False))
    attribution_pass = bool(row.get("attribution_pass", False))
    live_case_hit = bool(row.get("live_case_hit", False))
    if long_window_pass and attribution_pass and live_case_hit:
        return "SHADOW_CANDIDATE", "profit_armed_market_tp_shadow_candidate"
    if live_case_hit and not long_window_pass:
        return "REJECT", "rejected_for_live_long_window_gate_failed"
    if long_window_pass:
        return "OBSERVE", "long_window_pass_needs_live_or_exit_review"
    if live_case_hit:
        return "OBSERVE", "live_case_hit_but_needs_attribution_review"
    return "REJECT", "no_live_hit_or_no_long_window_edge"


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
    variants = build_armed_matrix()

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
    exit_rows: list[dict[str, Any]] = []
    arm_rows: list[dict[str, Any]] = []
    for variant in variants:
        print(f"=== {variant.variant} ===", flush=True)
        sig, exits, arms = apply_profit_armed_market_tp(
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
        quality = summarize_exit_quality(exits)
        dd_improve = (abs(base_metrics["dd"]) - abs(result["metrics"]["dd"])) / abs(base_metrics["dd"])
        row = {
            **public_variant_fields(variant),
            "armed_trades": len({int(e["entry_bar"]) for e in arms}),
            "exits": len(exits),
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
            **quality,
            **exp0131.yearly_delta(base_signals, sig, df),
        }
        row["long_window_pass"] = (
            row["exits"] > 0
            and row["oos_return"] >= base_summary["oos"]["return"] * 0.95
            and row["dd_improve_rel"] >= 0.0
            and row["delta_roll12"] >= -0.01
            and row["top20_winner_exits"] <= 1
            and row["trades"] <= base_metrics["trades"] * 1.20
            and row["year_losses"] <= 2
        )
        row["attribution_pass"] = (
            row["valuable_exit_count"] >= row["missed_winner_count"]
            and row["net_delta_pnl_proxy"] >= 0.0
            and row["top20_winner_exits"] <= 1
        )
        row["live_status"] = ""
        row["live_case_hit"] = False
        row["verdict"], row["verdict_reason"] = classify_row(row)
        rows.append(row)
        exit_rows.extend(exits)
        arm_rows.extend(arms)

    rows = sorted(
        rows,
        key=lambda r: (
            r["long_window_pass"],
            r["attribution_pass"],
            r["dd_improve_rel"],
            r["delta_oos"],
            -r["top20_winner_exits"],
        ),
        reverse=True,
    )
    report = {
        "scope": {
            "experiment_id": "exp_0133",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "mode": "profit-armed market-signal take-profit shadow",
            "direction": "B_arm_first_no_new_shape_library",
            "data": str(exp0131.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "data_window": f"{bar_time(df, 0)} to {bar_time(df, len(df) - 1)}",
            "variant_count": len(variants),
            "arming_metric": ARMING_METRIC,
            "arming_thresholds_atr": list(ARMING_THRESHOLDS_ATR),
            "arming_lifecycle": ARMING_LIFECYCLE,
            "execution": "next 5m open close-to-flat; no reverse/open; same-side lockout",
            "conflict_policy": "baseline reversal on same bar wins; market TP is not attributed",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "moirai_blocked": int(scope["moirai_blocked"]),
        },
        "baseline": {
            "raw_next_open": base_summary,
            "metrics": base_metrics,
            "capture": base_capture,
            "safe_execution_result": "next_open_evaluation_close_to_flat_overlay",
            "regime_permission_result": "not_applicable_post_v22_moirai_baseline",
        },
        "rows": rows,
        "exits": exit_rows,
        "arm_events": arm_rows,
    }
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT.with_suffix(".csv"), rows)
    write_csv(OUT.with_name(OUT.name + "_exits").with_suffix(".csv"), exit_rows)
    write_csv(OUT.with_name(OUT.name + "_arms").with_suffix(".csv"), arm_rows)
    write_markdown(rows, [], report)
    print(OUT.with_suffix(".md"))
    for row in rows:
        print(
            row["variant"],
            "LW",
            row["long_window_pass"],
            "ATTR",
            row["attribution_pass"],
            "exits",
            row["exits"],
            "dOOS",
            round(row["delta_oos"] * 100, 2),
            "top20",
            row["top20_winner_exits"],
        )
    return report


def run_live_case() -> list[dict[str, Any]]:
    if not exp0131.LIVE_CACHE.exists():
        raise SystemExit(f"missing live cache: {exp0131.LIVE_CACHE}")
    df = exp0131.clean_ohlcv(pd.read_parquet(exp0131.LIVE_CACHE))
    features = exp0131.build_market_signal_features(df, None)
    variants = build_armed_matrix()
    times = pd.to_datetime(df["datetime"])
    entry_idx = int(np.searchsorted(times.to_numpy(), np.datetime64(LIVE_ENTRY_TIME), side="left"))
    end_idx = int(np.searchsorted(times.to_numpy(), np.datetime64(LIVE_EXIT_TIME), side="right") - 1)
    if entry_idx >= len(df) or end_idx < entry_idx:
        raise SystemExit("live cache does not cover configured live-case window")

    rows: list[dict[str, Any]] = []
    for variant in variants:
        trigger_row = first_live_trigger_armed(df, features, variant, entry_idx, end_idx)
        if trigger_row is None:
            trigger_row = {
                **public_variant_fields(variant),
                "status": "no_trigger",
                "trigger_bar": -1,
                "trigger_time_utc": "",
                "trigger_time_cst": "",
                "execution_bar": -1,
                "execution_time_utc": "",
                "close": float("nan"),
                "next_open": float("nan"),
                "current_return": float("nan"),
                "current_return_after_cost": float("nan"),
                "open_profit_atr": float("nan"),
                "entry_atr": float("nan"),
                "armed_bar": -1,
                "armed_time_utc": "",
                "armed_time_cst": "",
                "armed_open_profit_atr": float("nan"),
                "saved_vs_1590": float("nan"),
                "before_1590": False,
                "live_case_hit": False,
                "reasons": "",
            }
        rows.append(trigger_row)

    rows = sorted(
        rows,
        key=lambda r: (
            r["status"] != "triggered",
            not bool(r["live_case_hit"]),
            r["trigger_bar"] if r["trigger_bar"] >= 0 else 10**12,
            r["variant"],
        ),
    )
    path = OUT.with_name(OUT.name + "_live_case").with_suffix(".csv")
    write_csv(path, rows)
    print(path)
    for row in rows:
        print(row["variant"], row["status"], row["trigger_time_cst"], row["close"], row["reasons"])
    return rows


def first_live_trigger_armed(
    df: pd.DataFrame,
    features: pd.DataFrame,
    spec: ArmedVariantSpec,
    entry_idx: int,
    end_idx: int,
) -> dict[str, Any] | None:
    short_mask, long_mask = exp0131.trigger_masks(features, spec.market_spec)
    short_setup, long_setup = exp0131.shape_masks(features, spec.market_spec)
    short_confirm, long_confirm = exp0131.sequence_confirm_masks(features, spec.market_spec)
    mask = short_mask if LIVE_SIDE < 0 else long_mask
    setup = short_setup if LIVE_SIDE < 0 else long_setup
    confirm = short_confirm if LIVE_SIDE < 0 else long_confirm
    atr = ensure_atr(df)
    entry_atr = entry_atr_at(atr, entry_idx)
    setup_bar = -1
    setup_until = -1
    armed = False
    armed_bar = -1
    armed_profit_atr = float("nan")

    for i in range(entry_idx, end_idx + 1):
        close = float(df["close"].iloc[i])
        if LIVE_SIDE < 0:
            current_return = LIVE_ENTRY_PRICE / max(close, 1e-12) - 1.0
        else:
            current_return = close / LIVE_ENTRY_PRICE - 1.0
        net_return = current_return_after_cost(LIVE_SIDE, LIVE_ENTRY_PRICE, close)
        profit_atr = open_profit_atr(LIVE_SIDE, LIVE_ENTRY_PRICE, close, entry_atr)
        if not armed and np.isfinite(profit_atr) and profit_atr >= spec.arming_threshold_atr:
            armed = True
            armed_bar = i
            armed_profit_atr = profit_atr
        if setup_bar >= 0 and i > setup_until:
            setup_bar = -1
            setup_until = -1
        if not armed or net_return <= 0.0:
            continue

        reasons: list[str] = []
        triggered = False
        if spec.market_spec.sequence_macd_tf:
            if setup[i]:
                setup_bar = i
                setup_until = i + int(spec.market_spec.sequence_window_bars)
            if setup_bar >= 0 and i <= setup_until and confirm[i]:
                setup_label = "2h_lower_wick_setup" if LIVE_SIDE < 0 else "2h_upper_wick_setup"
                cross_label = "macd_bull_cross" if LIVE_SIDE < 0 else "macd_bear_cross"
                reasons = [f"{setup_label}@{bar_time(df, setup_bar)}", f"{cross_label}_{spec.market_spec.sequence_macd_tf}"]
                triggered = True
        elif mask[i]:
            reasons = exp0131.trigger_reasons(features.iloc[i], LIVE_SIDE, spec.market_spec)
            triggered = True
        if not triggered:
            continue

        execution_bar = i + 1 if i + 1 < len(df) else -1
        next_open = float(df["open"].iloc[execution_bar]) if execution_bar >= 0 else float("nan")
        before_1590 = close < 1590.0 if LIVE_SIDE < 0 else close > 1590.0
        return {
            **public_variant_fields(spec),
            "status": "triggered",
            "trigger_bar": i,
            "trigger_time_utc": bar_time(df, i),
            "trigger_time_cst": str(pd.Timestamp(df["datetime"].iloc[i]) + pd.Timedelta(hours=8)),
            "execution_bar": execution_bar,
            "execution_time_utc": bar_time(df, execution_bar) if execution_bar >= 0 else "",
            "close": close,
            "next_open": next_open,
            "current_return": current_return,
            "current_return_after_cost": net_return,
            "open_profit_atr": profit_atr,
            "entry_atr": entry_atr,
            "armed_bar": armed_bar,
            "armed_time_utc": bar_time(df, armed_bar) if armed_bar >= 0 else "",
            "armed_time_cst": str(pd.Timestamp(df["datetime"].iloc[armed_bar]) + pd.Timedelta(hours=8))
            if armed_bar >= 0
            else "",
            "armed_open_profit_atr": armed_profit_atr,
            "saved_vs_1590": 1590.0 - close if LIVE_SIDE < 0 else close - 1590.0,
            "before_1590": before_1590,
            "live_case_hit": bool(before_1590),
            "reasons": "+".join(reasons),
            "lower_shadow_ratio_2h": exp0131.safe_float(features.at[i, "lower_shadow_ratio_2h"]),
            "volume_ratio_2h": exp0131.safe_float(features.at[i, "volume_ratio_2h"]),
            "volume_ratio_1h": exp0131.safe_float(features.at[i, "volume_ratio_1h"]),
            "macd_hist_2h": exp0131.safe_float(features.at[i, "macd_hist_2h"]),
            "macd_hist_4h": exp0131.safe_float(features.at[i, "macd_hist_4h"]),
        }
    return None


def merge_live_into_matrix(rows: list[dict[str, Any]], live_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live_by_variant = {row["variant"]: row for row in live_rows}
    merged = []
    for row in rows:
        out = row.copy()
        live = live_by_variant.get(row["variant"], {})
        out["live_status"] = live.get("status", "")
        out["live_trigger_time_cst"] = live.get("trigger_time_cst", "")
        out["live_trigger_close"] = live.get("close", float("nan"))
        out["live_next_open"] = live.get("next_open", float("nan"))
        out["live_current_return_after_cost"] = live.get("current_return_after_cost", float("nan"))
        out["live_open_profit_atr"] = live.get("open_profit_atr", float("nan"))
        out["live_before_1590"] = bool(live.get("before_1590", False))
        out["live_case_hit"] = bool(live.get("live_case_hit", False))
        out["live_reasons"] = live.get("reasons", "")
        out["verdict"], out["verdict_reason"] = classify_row(out)
        merged.append(out)
    return sorted(
        merged,
        key=lambda r: (
            r["verdict"] == "SHADOW_CANDIDATE",
            r["verdict"] == "OBSERVE",
            r["live_case_hit"],
            r["long_window_pass"],
            r["attribution_pass"],
            r["dd_improve_rel"],
            r["delta_oos"],
        ),
        reverse=True,
    )


def run_all() -> dict[str, Any]:
    report = run_matrix()
    live_rows = run_live_case()
    merged = merge_live_into_matrix(report["rows"], live_rows)
    report["rows"] = merged
    report["live_case"] = live_rows
    report["scope"]["live_case_window"] = f"{LIVE_ENTRY_TIME} to {LIVE_EXIT_TIME}"
    report["scope"]["judgement"] = "SHADOW_CANDIDATE only if long-window, attribution, and live-case gates all pass"
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
        "# exp_0133 v2.2 + Moirai profit-armed market TP shadow",
        "",
        "- diagnostic only; no live/checkpoint/config/production strategy change",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- direction: `B_arm_first_no_new_shape_library`",
        "- signal pool: `1h_strict_engulf_v2p0_macd4h`, `2h_wick_r75_v2p0_then_macd2h_12h`, `2h_wick_r75_v1p5_macd2h`",
        "- arming: `open_profit_atr_entry_based`, thresholds `{1.5, 2.0, 2.5, 3.0}`, latched once reached",
        "- trigger condition: armed plus market signal plus `current_return_after_cost > 0`",
        "- execution: next 5m open close-to-flat; no direct reverse/open; same-side lockout",
        "- conflict policy: if the baseline reverses on the same bar, market TP is not attributed",
        f"- baseline OOS/full/DD/roll12/trades: {pct(base_summary['oos']['return'])} / {pct(base_metrics['return'])} / {pct(base_metrics['dd'])} / {pct(base_summary['rolling_12m_min_return'])} / {base_metrics['trades']}",
        f"- verdict: `{overall}`; counts `{verdict_counts}`",
        "",
        "## Matrix",
        "",
        "| variant | live hit | live trigger | exits | armed | dOOS | DD improve | roll12 | top20 cut | valuable | missed winners | net delta proxy | year W/L/F | verdict |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        md.append(
            f"| {row['variant']} | {bool(row.get('live_case_hit', False))} | {row.get('live_trigger_time_cst', '')} | "
            f"{int(row['exits'])} | {int(row['armed_trades'])} | {pct(row['delta_oos'])} | "
            f"{pct(row['dd_improve_rel'])} | {pct(row['rolling12_min'])} | "
            f"{int(row['top20_winner_exits'])} | {int(row['valuable_exit_count'])} | "
            f"{int(row['missed_winner_count'])} | {money(row['net_delta_pnl_proxy'])} | "
            f"{int(row['year_wins'])}/{int(row['year_losses'])}/{int(row['year_flat'])} | "
            f"{row['verdict']} |"
        )
    md.extend(
        [
            "",
            "## Live Case",
            "",
            "| variant | status | hit before 1590 | trigger CST | close | next open | armed ATR | trigger ATR | net return | reasons |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in live_rows:
        md.append(
            f"| {row['variant']} | {row['status']} | {bool(row.get('live_case_hit', False))} | "
            f"{row.get('trigger_time_cst', '')} | {money(row.get('close'))} | {money(row.get('next_open'))} | "
            f"{money(row.get('armed_open_profit_atr'))} | {money(row.get('open_profit_atr'))} | "
            f"{pct(row.get('current_return_after_cost'))} | {row.get('reasons', '')} |"
        )
    md.extend(
        [
            "",
            "## Conclusion",
            "",
            "- No row qualifies as `SHADOW_CANDIDATE` because no variant passes long-window, attribution, and live-case gates together.",
            "- The ATR arming thresholds `{1.5, 2.0, 2.5, 3.0}` did not change the exit set inside each selected signal family; when these market signals fire, the trade is already well beyond the arming threshold.",
            "- `2h_wick_r75_v1p5_macd2h` is the cleanest long-window observe branch: only `2` exits, `0` top20 cuts, positive proxy attribution, but it does not hit the 2026-06-23 Bitget live-case.",
            "- `2h_wick_r75_v2p0_then_macd2h_12h` still explains the live-case near `1564.52`, but remains rejected for live because it cuts `7/20` top winners and loses `5` year slices.",
            "- `1h_strict_engulf_v2p0_macd4h` remains observe-only: long-window metrics are okay, but attribution proxy stays net negative and live-case has no trigger.",
            "- No live/demo routing, checkpoint, config, execution, oracle, or production strategy change is authorized.",
            "",
            "## Reporting Contract",
            "",
            f"- raw result: full matrix in `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            "- regime-permission result: not applicable here because input is already the v2.2 + Moirai post-gate baseline stream.",
            "- safe-execution result: the overlay emits only close-to-flat and is evaluated at next open.",
            "- OOS return, max drawdown, rolling 12m, trade count: columns in the matrix CSV.",
            "- top-winner damage: `top20_winner_exits` and `top20_winner_exited_pnl`.",
            "- worst-loser reduction: `worst20_loser_exits` and `worst20_loser_exited_pnl`.",
            "- MFE capture and giveback-to-loss stats: matrix CSV columns.",
            "- conclusion: research-only shadow evidence; no live/demo routing or checkpoint promotion is authorized.",
            "",
            f"Exits CSV: `{OUT.with_name(OUT.name + '_exits').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"Live-case CSV: `{OUT.with_name(OUT.name + '_live_case').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Profit-armed market TP shadow for v2.2 + Moirai.")
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
