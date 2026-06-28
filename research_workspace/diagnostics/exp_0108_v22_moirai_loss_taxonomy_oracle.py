from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.checkpoints import load_checkpoint
from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import _generate_v21_signals, _load_and_split_data

HELPER_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0095_btc_v22_moirai2_compare.py"
spec = importlib.util.spec_from_file_location("exp0095_helper", HELPER_PATH)
helper = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(helper)

CHECKPOINT = PROJECT_ROOT / "checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json"
CANDIDATE = PROJECT_ROOT / "configs/live/moirai2_gate_exp_0093.json"
DATA = PROJECT_ROOT / "data/crypto/ETHUSDT_5m_2600d.parquet"
CACHE = PROJECT_ROOT / "research_workspace/diagnostics/exp_0094_no_boll_moirai2_shadow_c1024_h72_2600d_cache.json"
OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0108_v22_moirai_loss_taxonomy_oracle"

GIVEBACK_MAIN = 0.06
IMMEDIATE_MAIN = {"horizon": 24, "mae": 0.012, "mfe": 0.005}


def pct(x: float | None) -> str:
    return "" if x is None else f"{x * 100:.2f}%"


def next_open_trades(signals: np.ndarray, df: pd.DataFrame) -> tuple[np.ndarray, list[dict[str, Any]]]:
    exec_signals = np.ones(len(signals), dtype=int)
    exec_signals[1:] = signals[:-1]
    ev = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=COMMISSION,
        slippage=SLIPPAGE,
        execution_price="signal_bar_open",
    )
    equity, trades = ev.simulate(exec_signals, df["close"].to_numpy(dtype=float), df=df)
    return equity, [t for t in trades if t.get("pnl") is not None]


def metrics(equity: np.ndarray, trades: list[dict[str, Any]]) -> dict[str, Any]:
    peaks = np.maximum.accumulate(equity)
    dd = equity / np.where(peaks == 0, np.nan, peaks) - 1.0
    return {
        "return": float(equity[-1] / equity[0] - 1.0),
        "dd": float(np.nanmin(dd)),
        "trades": len(trades),
    }


def decision_context(signals: np.ndarray) -> dict[int, dict[str, Any]]:
    pos = 0
    last_exit_bar = -10**9
    last_dir = ""
    out: dict[int, dict[str, Any]] = {}
    for i, raw in enumerate(signals.astype(int)):
        target = helper.signal_target(int(raw), pos)
        if target == 0 and pos != 0:
            last_exit_bar = i
            last_dir = "long" if pos > 0 else "short"
            pos = 0
            continue
        if target != 0 and target != pos:
            direction = "long" if target > 0 else "short"
            entry_type = "reversal" if pos != 0 else "fresh_entry"
            if entry_type == "fresh_entry" and direction == last_dir and i - last_exit_bar <= 432:
                entry_type = "reentry"
            out[i] = {"position_before": pos, "target": target, "direction": direction, "entry_type": entry_type}
            pos = target
    return out


def trade_features(trades: list[dict[str, Any]], signals: np.ndarray, df: pd.DataFrame) -> list[dict[str, Any]]:
    ctx = decision_context(signals)
    rows = []
    for t in trades:
        entry = int(t["entry_step"])
        exit_ = int(t["step"])
        decision_bar = max(0, entry - 1)
        direction = "long" if str(t["type"]).startswith("sell") else "short"
        entry_price = float(t["entry_price"])
        entry_notional = max(1e-12, float(t.get("entry_notional") or 0.0))
        w = df.iloc[entry : exit_ + 1]
        if direction == "long":
            favorable = w["high"].astype(float)
            adverse = w["low"].astype(float)
            mfe_series = favorable / entry_price - 1.0
            mae_series = 1.0 - adverse / entry_price
        else:
            favorable = w["low"].astype(float)
            adverse = w["high"].astype(float)
            mfe_series = 1.0 - favorable / entry_price
            mae_series = adverse / entry_price - 1.0
        life_mfe = float(max(0.0, mfe_series.max()))
        life_mae = float(max(0.0, mae_series.max()))
        mfe_step = int(mfe_series.idxmax()) if len(mfe_series) else entry
        realized_return = float(t["pnl"]) / entry_notional
        row = {
            "entry_bar": entry,
            "decision_bar": decision_bar,
            "exit_bar": exit_,
            "entry_time": str(pd.Timestamp(df["datetime"].iloc[entry])),
            "exit_time": str(pd.Timestamp(df["datetime"].iloc[exit_])),
            "direction": direction,
            "entry_type": ctx.get(decision_bar, {}).get("entry_type", "unknown"),
            "pnl": float(t["pnl"]),
            "realized_return": realized_return,
            "bars_held": exit_ - entry,
            "life_mfe": life_mfe,
            "life_mae": life_mae,
            "bars_after_mfe_to_exit": exit_ - mfe_step,
            "giveback_ratio": ((life_mfe - realized_return) / life_mfe) if life_mfe > 0 else None,
        }
        for n in (12, 24, 72):
            wn = df.iloc[entry : min(len(df), entry + n)]
            if direction == "long":
                row[f"mfe_{n}"] = float(max(0.0, (wn["high"].max() / entry_price) - 1.0))
                row[f"mae_{n}"] = float(max(0.0, 1.0 - (wn["low"].min() / entry_price)))
            else:
                row[f"mfe_{n}"] = float(max(0.0, 1.0 - (wn["low"].min() / entry_price)))
                row[f"mae_{n}"] = float(max(0.0, (wn["high"].max() / entry_price) - 1.0))
            atr = float(df["atr"].iloc[entry]) if "atr" in df.columns and pd.notna(df["atr"].iloc[entry]) else np.nan
            if atr and np.isfinite(atr) and atr > 0:
                row[f"mfe_atr_{n}"] = row[f"mfe_{n}"] * entry_price / atr
                row[f"mae_atr_{n}"] = row[f"mae_{n}"] * entry_price / atr
        row["profit_giveback_to_loss_6"] = row["life_mfe"] >= GIVEBACK_MAIN and row["pnl"] <= 0
        row["immediate_adverse_24"] = (
            row["mae_24"] >= IMMEDIATE_MAIN["mae"] and row["mfe_24"] <= IMMEDIATE_MAIN["mfe"] and row["pnl"] <= 0
        )
        if row["pnl"] > 0:
            row["main_class"] = "normal_winner"
        elif row["profit_giveback_to_loss_6"]:
            row["main_class"] = "profit_giveback_to_loss"
        elif row["immediate_adverse_24"]:
            row["main_class"] = "immediate_adverse"
        else:
            row["main_class"] = "clean_loss"
        rows.append(row)
    return rows


def dd_period(equity: np.ndarray) -> tuple[int, int]:
    peaks = np.maximum.accumulate(equity)
    dd = equity / np.where(peaks == 0, np.nan, peaks) - 1.0
    trough = int(np.nanargmin(dd))
    peak = int(np.argmax(equity[: trough + 1]))
    return peak, trough


def class_summary(rows: list[dict[str, Any]], peak: int, trough: int) -> list[dict[str, Any]]:
    total_loss = abs(sum(r["pnl"] for r in rows if r["pnl"] < 0)) or 1.0
    top10 = sorted([r for r in rows if r["pnl"] < 0], key=lambda r: r["pnl"])[:10]
    top10_loss = abs(sum(r["pnl"] for r in top10)) or 1.0
    dd_loss = abs(sum(r["pnl"] for r in rows if r["pnl"] < 0 and peak <= r["exit_bar"] <= trough)) or 1.0
    out = []
    for cls in ["profit_giveback_to_loss", "immediate_adverse", "clean_loss", "normal_winner"]:
        group = [r for r in rows if r["main_class"] == cls]
        losses = [r for r in group if r["pnl"] < 0]
        out.append(
            {
                "class": cls,
                "count": len(group),
                "sum_pnl": sum(r["pnl"] for r in group),
                "avg_pnl": np.mean([r["pnl"] for r in group]) if group else 0.0,
                "max_loss": min([r["pnl"] for r in group], default=0.0),
                "top10_loss_share": abs(sum(r["pnl"] for r in losses if r in top10)) / top10_loss,
                "share_of_total_loss": abs(sum(r["pnl"] for r in losses)) / total_loss,
                "share_of_max_dd_period": abs(sum(r["pnl"] for r in losses if peak <= r["exit_bar"] <= trough)) / dd_loss,
                "avg_bars_after_mfe_to_exit": np.mean([r["bars_after_mfe_to_exit"] for r in group]) if group else 0.0,
                "avg_giveback_ratio": np.nanmean([r["giveback_ratio"] for r in group if r["giveback_ratio"] is not None]) if group else 0.0,
            }
        )
    return out


def group_pivot(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out = []
    for cls in ["profit_giveback_to_loss", "immediate_adverse", "clean_loss", "normal_winner"]:
        item = {"class": cls}
        for val in sorted({str(r[key]) for r in rows}):
            group = [r for r in rows if r["main_class"] == cls and str(r[key]) == val]
            item[f"{val}_count"] = len(group)
            item[f"{val}_pnl"] = sum(r["pnl"] for r in group)
        out.append(item)
    return out


def threshold_sweeps(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    giveback = []
    for th in [0.02, 0.04, 0.06, 0.08, 0.10]:
        group = [r for r in rows if r["pnl"] <= 0 and r["life_mfe"] >= th]
        giveback.append({"life_mfe_threshold": th, "count": len(group), "sum_pnl": sum(r["pnl"] for r in group)})
    immediate = []
    for n, mae, mfe in [(12, 0.010, 0.003), (24, 0.012, 0.005), (72, 0.020, 0.008)]:
        group = [r for r in rows if r["pnl"] <= 0 and r[f"mae_{n}"] >= mae and r[f"mfe_{n}"] <= mfe]
        immediate.append({"horizon": n, "mae_threshold": mae, "mfe_threshold": mfe, "count": len(group), "sum_pnl": sum(r["pnl"] for r in group)})
    for n in (12, 24, 72):
        group = [r for r in rows if r["pnl"] <= 0 and r.get(f"mae_atr_{n}", 0) >= 1.5 and r.get(f"mfe_atr_{n}", 999) <= 0.5]
        immediate.append({"horizon": n, "mae_atr_threshold": 1.5, "mfe_atr_threshold": 0.5, "count": len(group), "sum_pnl": sum(r["pnl"] for r in group)})
    return {"giveback": giveback, "immediate": immediate}


def oracle_block(signals: np.ndarray, block_decision_bars: set[int]) -> np.ndarray:
    out = np.ones(len(signals), dtype=int)
    pos = 0
    for i, raw in enumerate(signals.astype(int)):
        target = helper.signal_target(int(raw), pos)
        if i in block_decision_bars and target != 0 and target != pos:
            out[i] = 0 if pos else 1
            if pos:
                pos = 0
            continue
        out[i] = int(raw)
        pos = helper.signal_target(int(out[i]), pos)
    return out


def oracle_rows(rows: list[dict[str, Any]], signals: np.ndarray, df: pd.DataFrame, baseline: dict[str, Any]) -> list[dict[str, Any]]:
    sets = {
        "avoid_giveback6": {r["decision_bar"] for r in rows if r["profit_giveback_to_loss_6"]},
        "avoid_immediate24": {r["decision_bar"] for r in rows if r["immediate_adverse_24"]},
    }
    sets["avoid_both"] = sets["avoid_giveback6"] | sets["avoid_immediate24"]
    out = []
    for name, bars in sets.items():
        sig = oracle_block(signals, bars)
        eq, trades = next_open_trades(sig, df)
        m = metrics(eq, trades)
        out.append(
            {
                "oracle": name,
                "blocked_decisions": len(bars),
                "return": m["return"],
                "delta_return": m["return"] - baseline["return"],
                "dd": m["dd"],
                "dd_improvement_rel": (abs(baseline["dd"]) - abs(m["dd"])) / abs(baseline["dd"]),
                "trades": m["trades"],
            }
        )
    return out


def main() -> None:
    checkpoint = load_checkpoint(CHECKPOINT)
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    df_is, df_oos, split_idx = _load_and_split_data(DATA)
    df = pd.concat([df_is, df_oos], ignore_index=True)
    raw_signals = _generate_v21_signals(checkpoint, df)
    forecasts = helper.load_cache(CACHE)
    decisions = helper.collect_decision_indices(raw_signals)
    missing = [i for i in decisions if i not in forecasts]
    if missing:
        raise SystemExit(f"missing {len(missing)} Moirai forecasts")
    signals, diag = helper.apply_gate(
        raw_signals,
        {i: forecasts[i] for i in decisions},
        min_edge_pct=float(candidate["params"]["min_edge_pct"]),
        risk_floor_pct=float(candidate["params"]["risk_floor_pct"]),
    )
    equity, trades = next_open_trades(signals, df)
    baseline = metrics(equity, trades)
    peak, trough = dd_period(equity)
    rows = trade_features(trades, signals, df)
    summaries = class_summary(rows, peak, trough)
    sweeps = threshold_sweeps(rows)
    oracles = oracle_rows(rows, signals, df, baseline)

    report = {
        "scope": {
            "experiment_id": "exp_0108",
            "base": "channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093",
            "status": "loss_taxonomy_oracle",
            "note": "Attribution uses future path; not a strategy rule.",
            "main_giveback_threshold": GIVEBACK_MAIN,
            "main_immediate_rule": IMMEDIATE_MAIN,
            "data": str(DATA.relative_to(PROJECT_ROOT)),
            "candidate": str(CANDIDATE.relative_to(PROJECT_ROOT)),
            "cache": str(CACHE.relative_to(PROJECT_ROOT)),
            "moirai_blocked": int(diag["blocked_long"] + diag["blocked_short"]),
            "max_dd_peak": str(pd.Timestamp(df["datetime"].iloc[peak])),
            "max_dd_trough": str(pd.Timestamp(df["datetime"].iloc[trough])),
        },
        "baseline": baseline,
        "class_summary": summaries,
        "direction_pivot": group_pivot(rows, "direction"),
        "entry_type_pivot": group_pivot(rows, "entry_type"),
        "threshold_sweeps": sweeps,
        "oracle": oracles,
        "trades": rows,
    }
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with OUT.with_name(OUT.name + "_trades.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)

    with OUT.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)

    md = [
        "# exp_0108 v2.2 + Moirai loss taxonomy and oracle",
        "",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        "- execution: `next_bar_open`",
        "- attribution may use future path; strategy rules must not.",
        f"- max DD period: `{report['scope']['max_dd_peak']}` -> `{report['scope']['max_dd_trough']}`",
        "",
        "## Table 1: loss source classification",
        "",
        "| class | count | sum pnl | avg pnl | max loss | top10 loss share | total loss share | max DD loss share | avg bars after MFE | avg giveback |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summaries:
        md.append(
            f"| {r['class']} | {r['count']} | {r['sum_pnl']:.2f} | {r['avg_pnl']:.2f} | {r['max_loss']:.2f} | "
            f"{pct(r['top10_loss_share'])} | {pct(r['share_of_total_loss'])} | {pct(r['share_of_max_dd_period'])} | "
            f"{r['avg_bars_after_mfe_to_exit']:.1f} | {r['avg_giveback_ratio']:.2f} |"
        )
    md.extend(["", "## Giveback threshold sweep", "", "| life MFE >= | count | sum pnl |", "|---:|---:|---:|"])
    for r in sweeps["giveback"]:
        md.append(f"| {pct(r['life_mfe_threshold'])} | {r['count']} | {r['sum_pnl']:.2f} |")
    md.extend(["", "## Immediate adverse sweep", "", "| rule | count | sum pnl |", "|---|---:|---:|"])
    for r in sweeps["immediate"]:
        if "mae_threshold" in r:
            rule = f"{r['horizon']} bars MAE>={pct(r['mae_threshold'])}, MFE<={pct(r['mfe_threshold'])}"
        else:
            rule = f"{r['horizon']} bars MAE>=1.5ATR, MFE<=0.5ATR"
        md.append(f"| {rule} | {r['count']} | {r['sum_pnl']:.2f} |")
    md.extend(["", "## Table 2: by direction", ""])
    md.append("| class | long count | long pnl | short count | short pnl |")
    md.append("|---|---:|---:|---:|---:|")
    for r in report["direction_pivot"]:
        md.append(f"| {r['class']} | {r.get('long_count', 0)} | {r.get('long_pnl', 0):.2f} | {r.get('short_count', 0)} | {r.get('short_pnl', 0):.2f} |")
    md.extend(["", "## Table 3: by entry type", ""])
    md.append("| class | fresh count | fresh pnl | reversal count | reversal pnl | reentry count | reentry pnl |")
    md.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in report["entry_type_pivot"]:
        md.append(
            f"| {r['class']} | {r.get('fresh_entry_count', 0)} | {r.get('fresh_entry_pnl', 0):.2f} | "
            f"{r.get('reversal_count', 0)} | {r.get('reversal_pnl', 0):.2f} | {r.get('reentry_count', 0)} | {r.get('reentry_pnl', 0):.2f} |"
        )
    md.extend(["", "## Table 4: oracle upper bounds", ""])
    md.append("| oracle | blocked decisions | return | Δreturn | DD | DD improvement | trades |")
    md.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in oracles:
        md.append(f"| {r['oracle']} | {r['blocked_decisions']} | {pct(r['return'])} | {pct(r['delta_return'])} | {pct(r['dd'])} | {pct(r['dd_improvement_rel'])} | {r['trades']} |")
    md.extend(["", f"Trades CSV: `{OUT.with_name(OUT.name + '_trades.csv').relative_to(PROJECT_ROOT)}`"])
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(OUT.with_suffix(".md"))
    for r in summaries:
        print(r["class"], r["count"], round(r["sum_pnl"], 2), round(r["share_of_total_loss"] * 100, 1))
    for r in oracles:
        print(r["oracle"], "ddImp", round(r["dd_improvement_rel"] * 100, 2), "dRet", round(r["delta_return"] * 100, 2))


if __name__ == "__main__":
    main()
