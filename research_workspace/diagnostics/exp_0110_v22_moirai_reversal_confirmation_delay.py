from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

HELPER_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0108_v22_moirai_loss_taxonomy_oracle.py"
spec = importlib.util.spec_from_file_location("exp0108_helper", HELPER_PATH)
helper0108 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helper0108)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0110_v22_moirai_reversal_confirmation_delay"


@dataclass
class Pending:
    target: int
    created: int
    confirm_bar: int
    original_level: float


def pct(x: float | None) -> str:
    return "" if x is None else f"{x * 100:.2f}%"


def load_base() -> tuple[pd.DataFrame, np.ndarray, dict[str, Any]]:
    checkpoint = helper0108.load_checkpoint(helper0108.CHECKPOINT)
    candidate = json.loads(helper0108.CANDIDATE.read_text(encoding="utf-8"))
    df_is, df_oos, split_idx = helper0108._load_and_split_data(helper0108.DATA)
    df = pd.concat([df_is, df_oos], ignore_index=True)
    raw_signals = helper0108._generate_v21_signals(checkpoint, df)
    forecasts = helper0108.helper.load_cache(helper0108.CACHE)
    decisions = helper0108.helper.collect_decision_indices(raw_signals)
    missing = [i for i in decisions if i not in forecasts]
    if missing:
        raise SystemExit(f"missing {len(missing)} Moirai forecasts")
    signals, diag = helper0108.helper.apply_gate(
        raw_signals,
        {i: forecasts[i] for i in decisions},
        min_edge_pct=float(candidate["params"]["min_edge_pct"]),
        risk_floor_pct=float(candidate["params"]["risk_floor_pct"]),
    )
    return df, signals, {"split_idx": split_idx, "moirai_blocked": int(diag["blocked_long"] + diag["blocked_short"])}


def donchian_level(df: pd.DataFrame, i: int, target: int, window: int = 375) -> float:
    end = max(0, i)
    start = max(0, end - window)
    w = df.iloc[start:end]
    if w.empty:
        return float(df["close"].iloc[i])
    return float(w["high"].max() if target > 0 else w["low"].min())


def breakout_strength_atr(df: pd.DataFrame, i: int, target: int) -> float:
    close = float(df["close"].iloc[i])
    atr = float(df["atr"].iloc[i]) if "atr" in df and pd.notna(df["atr"].iloc[i]) else np.nan
    if not np.isfinite(atr) or atr <= 0:
        return np.nan
    level = donchian_level(df, i, target)
    return (close - level) / atr if target > 0 else (level - close) / atr


def confirm(pending: Pending, mode: str, base_signal: int, df: pd.DataFrame, i: int) -> bool:
    close = float(df["close"].iloc[i])
    if mode == "signal":
        return helper0108.helper.signal_target(int(base_signal), 0) == pending.target
    if mode == "price":
        return close > pending.original_level if pending.target > 0 else close < pending.original_level
    if mode == "strength":
        strength = breakout_strength_atr(df, i, pending.target)
        return bool(np.isfinite(strength) and strength > 0)
    raise ValueError(mode)


def apply_delay(signals: np.ndarray, df: pd.DataFrame, *, delay: int, mode: str) -> tuple[np.ndarray, dict[str, Any]]:
    out = np.ones(len(signals), dtype=int)
    pos = 0
    pending: Pending | None = None
    suppressed_target = 0
    delayed: list[dict[str, Any]] = []
    confirmed: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []

    for i, raw in enumerate(signals.astype(int)):
        target = helper0108.helper.signal_target(int(raw), pos)
        if pending is not None:
            if target == -pending.target:
                expired.append(
                    {
                        "bar": i,
                        "created": pending.created,
                        "time": str(pd.Timestamp(df["datetime"].iloc[i])),
                        "reason": "opposite_signal",
                        "target": pending.target,
                    }
                )
                suppressed_target = pending.target
                pending = None
                out[i] = 1
                pos = 0
                continue
            elif i >= pending.confirm_bar:
                if confirm(pending, mode, int(raw), df, i):
                    out[i] = 2 if pending.target > 0 else 3
                    pos = pending.target
                    confirmed.append(
                        {
                            "bar": i,
                            "created": pending.created,
                            "time": str(pd.Timestamp(df["datetime"].iloc[i])),
                            "target": pending.target,
                        }
                    )
                    pending = None
                    suppressed_target = 0
                    continue
                expired.append(
                    {
                        "bar": i,
                        "created": pending.created,
                        "time": str(pd.Timestamp(df["datetime"].iloc[i])),
                        "reason": "confirm_failed",
                        "target": pending.target,
                    }
                )
                suppressed_target = pending.target
                pending = None
                out[i] = 1
                pos = 0
                continue

        target = helper0108.helper.signal_target(int(raw), pos)
        if suppressed_target and pos == 0:
            if target == suppressed_target:
                out[i] = 1
                continue
            suppressed_target = 0

        if target != 0 and pos != 0 and target != pos:
            level = donchian_level(df, i, target)
            out[i] = 0
            delayed.append(
                {
                    "bar": i,
                    "time": str(pd.Timestamp(df["datetime"].iloc[i])),
                    "from": pos,
                    "target": target,
                    "original_level": level,
                    "mode": mode,
                    "delay": delay,
                }
            )
            pending = Pending(target=target, created=i, confirm_bar=i + delay, original_level=level)
            pos = 0
            continue

        out[i] = int(raw)
        pos = helper0108.helper.signal_target(int(out[i]), pos)
    return out, {"delayed": delayed, "confirmed": confirmed, "expired": expired}


def evaluate(signals: np.ndarray, df: pd.DataFrame, split_idx: int) -> dict[str, Any]:
    summary = helper0108.helper.summarize(signals, df, split_idx, None)
    eq, trades = helper0108.next_open_trades(signals, df)
    m = helper0108.metrics(eq, trades)
    return {"summary": summary, "metrics": m, "trades": trades}


def reversal_bars(signals: np.ndarray) -> set[int]:
    pos = 0
    out = set()
    for i, raw in enumerate(signals.astype(int)):
        target = helper0108.helper.signal_target(int(raw), pos)
        if target != 0 and pos != 0 and target != pos:
            out.add(i)
        pos = target
    return out


def blocked_reversal_pnl(
    base_trades: list[dict[str, Any]],
    confirmed: list[dict[str, Any]],
    rev_bars: set[int],
) -> dict[str, Any]:
    confirmed_created = {int(e["created"]) for e in confirmed if int(e["created"]) in rev_bars}
    blocked = [
        t for t in base_trades
        if int(t["entry_step"]) - 1 in rev_bars and int(t["entry_step"]) - 1 not in confirmed_created
    ]
    winners = [float(t["pnl"]) for t in blocked if float(t["pnl"]) > 0]
    losers = [float(t["pnl"]) for t in blocked if float(t["pnl"]) <= 0]
    return {
        "confirmed_baseline_reversals": len(confirmed_created),
        "blocked_reversals": len(blocked),
        "blocked_reversal_pnl": sum(float(t["pnl"]) for t in blocked),
        "missed_winner_reversal_pnl": sum(winners),
        "blocked_loser_reversal_pnl": sum(losers),
        "missed_winner_count": len(winners),
        "blocked_loser_count": len(losers),
    }


def max_loss(trades: list[dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    return min(float(t["pnl"]) for t in trades)


def confirmed_reversal_trades(
    trades: list[dict[str, Any]],
    confirmed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    confirmed_bars = {int(e["bar"]) for e in confirmed}
    return [t for t in trades if int(t["entry_step"]) - 1 in confirmed_bars]


def yearly_delta(base_signals: np.ndarray, test_signals: np.ndarray, df: pd.DataFrame) -> dict[str, Any]:
    years = pd.to_datetime(df["datetime"]).dt.year.to_numpy()
    deltas = []
    for year in sorted(set(int(y) for y in years)):
        idx = np.flatnonzero(years == year)
        if len(idx) < 100:
            continue
        start, end = int(idx[0]), int(idx[-1]) + 1
        b = helper0108.helper.evaluate_next_open(base_signals[start:end], df.iloc[start:end].reset_index(drop=True), None)
        t = helper0108.helper.evaluate_next_open(test_signals[start:end], df.iloc[start:end].reset_index(drop=True), None)
        deltas.append(t["return"] - b["return"])
    return {
        "year_wins": sum(1 for x in deltas if x > 1e-12),
        "year_losses": sum(1 for x in deltas if x < -1e-12),
        "year_flat": sum(1 for x in deltas if abs(x) <= 1e-12),
        "year_min_delta": min(deltas) if deltas else 0.0,
    }


def main() -> None:
    df, base_signals, scope = load_base()
    split_idx = int(scope["split_idx"])
    base_eval = evaluate(base_signals, df, split_idx)
    base_summary = base_eval["summary"]
    base_metrics = base_eval["metrics"]
    revs = reversal_bars(base_signals)
    base_reversal_trades = [t for t in base_eval["trades"] if int(t["entry_step"]) - 1 in revs]
    base_reversal_max_loss = max_loss(base_reversal_trades)
    rows = []
    for mode in ["signal", "price", "strength"]:
        for delay in [1, 3, 6, 12]:
            name = f"{mode}_delay{delay}"
            print(f"=== {name} ===", flush=True)
            sig, diag = apply_delay(base_signals, df, delay=delay, mode=mode)
            result = evaluate(sig, df, split_idx)
            pnl = blocked_reversal_pnl(base_eval["trades"], diag["confirmed"], revs)
            test_reversal_max_loss = max_loss(confirmed_reversal_trades(result["trades"], diag["confirmed"]))
            year = yearly_delta(base_signals, sig, df)
            dd_improve = (abs(base_metrics["dd"]) - abs(result["metrics"]["dd"])) / abs(base_metrics["dd"])
            delayed_count = len(diag["delayed"])
            confirmed_count = len(diag["confirmed"])
            row = {
                "variant": name,
                "mode": mode,
                "delay": delay,
                "oos_return": result["summary"]["oos"]["return"],
                "delta_oos": result["summary"]["oos"]["return"] - base_summary["oos"]["return"],
                "full_return": result["metrics"]["return"],
                "delta_full": result["metrics"]["return"] - base_metrics["return"],
                "full_dd": result["metrics"]["dd"],
                "dd_improve_rel": dd_improve,
                "rolling12_min": result["summary"]["rolling_12m_min_return"],
                "delta_roll12": result["summary"]["rolling_12m_min_return"] - base_summary["rolling_12m_min_return"],
                "trades": result["metrics"]["trades"],
                "reversal_retained": pnl["confirmed_baseline_reversals"] / max(1, len(revs)),
                "delayed": delayed_count,
                "confirmed": confirmed_count,
                "expired": len(diag["expired"]),
                "base_reversal_max_loss": base_reversal_max_loss,
                "test_confirmed_reversal_max_loss": test_reversal_max_loss,
                "max_loss_improve_rel": (
                    (abs(base_reversal_max_loss) - abs(test_reversal_max_loss)) / abs(base_reversal_max_loss)
                    if base_reversal_max_loss < 0 and test_reversal_max_loss < 0
                    else 0.0
                ),
                **pnl,
                **year,
            }
            row["pass_gate"] = (
                row["oos_return"] >= base_summary["oos"]["return"] * 0.90
                and row["dd_improve_rel"] >= 0.05
                and row["delta_roll12"] >= -0.02
                and row["year_losses"] <= 2
                and row["reversal_retained"] >= 0.70
                and row["blocked_reversal_pnl"] < 0
                and row["missed_winner_reversal_pnl"] < abs(row["blocked_loser_reversal_pnl"])
            )
            rows.append(row)

    ranked = sorted(rows, key=lambda r: (r["pass_gate"], r["dd_improve_rel"], r["delta_oos"]), reverse=True)
    report = {
        "scope": {
            "experiment_id": "exp_0110",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "status": "research_rejected_no_variant_passed",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "moirai_blocked": scope["moirai_blocked"],
            "base_reversals": len(revs),
        },
        "baseline": {"summary": base_summary, "metrics": base_metrics},
        "summary": rows,
        "ranked": ranked,
    }
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    public_cols = [
        "variant", "mode", "delay", "oos_return", "delta_oos", "full_return", "full_dd",
        "dd_improve_rel", "rolling12_min", "delta_roll12", "reversal_retained",
        "delayed", "confirmed", "expired", "confirmed_baseline_reversals",
        "blocked_reversals", "blocked_reversal_pnl", "missed_winner_reversal_pnl",
        "blocked_loser_reversal_pnl", "base_reversal_max_loss", "test_confirmed_reversal_max_loss",
        "max_loss_improve_rel", "year_wins", "year_losses", "year_flat", "year_min_delta",
        "pass_gate",
    ]
    with OUT.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=public_cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in public_cols})
    md = [
        "# exp_0110 v2.2 + Moirai reversal confirmation delay",
        "",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- execution: `next_bar_open`",
        "- behavior: reversal becomes close-to-flat, then pending confirmation opens new direction if confirmed",
        "- live/checkpoint: no change",
        "",
        "## Summary",
        "",
        "| variant | ΔOOS | DD improve | Δroll12 | rev retained | delayed/confirmed/expired | blocked pnl | missed winner pnl | blocked loser pnl | year W/L/F | pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        md.append(
            f"| {row['variant']} | {pct(row['delta_oos'])} | {pct(row['dd_improve_rel'])} | "
            f"{pct(row['delta_roll12'])} | {pct(row['reversal_retained'])} | "
            f"{row['delayed']}/{row['confirmed']}/{row['expired']} | "
            f"{row['blocked_reversal_pnl']:.2f} | {row['missed_winner_reversal_pnl']:.2f} | {row['blocked_loser_reversal_pnl']:.2f} | "
            f"{row['year_wins']}/{row['year_losses']}/{row['year_flat']} | {row['pass_gate']} |"
        )
    md.extend(
        [
            "",
            "## Decision",
            "",
            "- no variant passes the gate.",
            "- `price_delay1` is the only interesting diagnostic: OOS and rolling12 improve, and the unretained baseline reversal PnL is net negative, but full DD is unchanged, baseline reversal retention is below 70%, and year-reset loses too many years.",
            "- `signal` confirmation mostly only adds a one-bar close-to-flat delay; it does not reduce DD and hurts OOS.",
            "- `strength` confirmation is too strict; it removes too many profitable reversal opportunities.",
            "- live/checkpoint action: no change.",
            "",
            f"CSV: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(OUT.with_suffix(".md"))
    for row in ranked[:6]:
        print(row["variant"], "pass", row["pass_gate"], "dOOS", round(row["delta_oos"] * 100, 2), "ddImp", round(row["dd_improve_rel"] * 100, 2), "blockedPnl", round(row["blocked_reversal_pnl"], 2))


if __name__ == "__main__":
    main()
