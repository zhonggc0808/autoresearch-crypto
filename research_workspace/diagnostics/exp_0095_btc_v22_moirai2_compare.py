"""BTC v2.2 + Moirai2 exp_0093 shadow comparison.

Research-only runner. Writes diagnostics under research_workspace/diagnostics and
does not modify strategy, checkpoint, live, or data files.
"""

from __future__ import annotations

import csv
import json
import os
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
from dex.live.timesfm_gate import forecast_allows
from dex.strategies.base import StrategyEvaluator
from scripts.research_oracle import (
    BARS_PER_MONTH,
    _generate_v21_signals,
    _load_and_split_data,
    _position_sizes_from_config,
)
from scripts.run_timesfm_breakout_filter_experiment import (
    collect_decision_indices,
    signal_target,
)

CHECKPOINT = PROJECT_ROOT / "checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json"
CANDIDATE = PROJECT_ROOT / "configs/live/moirai2_gate_exp_0093.json"
MODEL = PROJECT_ROOT / "research_workspace/diagnostics/moirai_2_small"
OUTPUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0095_btc_v22_moirai2_compare"
DATA_FILES = [
    PROJECT_ROOT / "data/crypto/BTCUSDT_5m_60d.parquet",
    PROJECT_ROOT / "data/crypto/BTCUSDT_5m_365d.parquet",
    PROJECT_ROOT / "data/crypto/BTCUSDT_5m_730d.parquet",
    PROJECT_ROOT / "data/crypto/BTCUSDT_5m_1300d.parquet",
]


def bar_time(df: pd.DataFrame, i: int) -> str:
    for col in ("datetime", "timestamp", "date"):
        if col in df.columns:
            return str(pd.Timestamp(df[col].iloc[i]))
    return str(i)


def shift_for_next_open(
    signals: np.ndarray,
    position_sizes: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray | None]:
    shifted = np.ones(len(signals), dtype=int)
    shifted[1:] = signals[:-1]
    if position_sizes is None:
        return shifted, None
    shifted_sizes = np.zeros(len(position_sizes), dtype=float)
    shifted_sizes[1:] = np.asarray(position_sizes, dtype=float)[:-1]
    return shifted, shifted_sizes


def evaluate_next_open(
    signals: np.ndarray,
    df: pd.DataFrame,
    position_sizes: np.ndarray | None,
    *,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> dict[str, Any]:
    exec_signals, exec_sizes = shift_for_next_open(signals, position_sizes)
    ev = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=commission,
        slippage=slippage,
        execution_price="signal_bar_open",
    )
    _, metrics, trades = ev.evaluate(
        exec_signals,
        df["close"].to_numpy(dtype=float),
        df=df,
        position_sizes=exec_sizes,
    )
    closed = [t for t in trades if t.get("pnl") is not None]
    return {
        "return": float(metrics.get("total_return", 0.0)),
        "dd": float(metrics.get("max_drawdown", 0.0)),
        "sharpe": float(metrics.get("sharpe_ratio", 0.0)),
        "win_rate": float(metrics.get("win_rate", 0.0)),
        "trades": len(closed),
    }


def summarize(
    signals: np.ndarray,
    df: pd.DataFrame,
    split_idx: int,
    position_sizes: np.ndarray | None,
) -> dict[str, Any]:
    sizes_is = position_sizes[:split_idx] if position_sizes is not None else None
    sizes_oos = position_sizes[split_idx:] if position_sizes is not None else None
    return {
        "is": evaluate_next_open(signals[:split_idx], df.iloc[:split_idx], sizes_is),
        "oos": evaluate_next_open(signals[split_idx:], df.iloc[split_idx:], sizes_oos),
        "full": evaluate_next_open(signals, df, position_sizes),
        "rolling_12m_min_return": rolling_12m_min_return(signals, df, position_sizes),
        "fee_10bp_return_full": evaluate_next_open(
            signals,
            df,
            position_sizes,
            commission=0.001,
        )["return"],
    }


def rolling_12m_min_return(
    signals: np.ndarray,
    df: pd.DataFrame,
    position_sizes: np.ndarray | None,
) -> float | None:
    window = 12 * BARS_PER_MONTH
    if window >= len(signals) // 2:
        return None
    step = max(1, window // 2)
    worst = float("inf")
    for start in range(0, len(signals) - window, step):
        end = start + window
        sizes = position_sizes[start:end] if position_sizes is not None else None
        ret = evaluate_next_open(signals[start:end], df.iloc[start:end], sizes)["return"]
        worst = min(worst, ret)
    return float(worst)


def load_cache(path: Path) -> dict[int, dict[str, float]]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def save_cache(path: Path, forecasts: dict[int, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {str(k): v for k, v in sorted(forecasts.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_moirai2_model(context: int, horizon: int):
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    import importlib

    import torch

    torch.cuda.is_available = lambda: False
    torch.set_float32_matmul_precision("high")
    moirai2 = importlib.import_module("uni2ts.model.moirai2")
    module = moirai2.Moirai2Module.from_pretrained(str(MODEL))
    return moirai2.Moirai2Forecast(
        prediction_length=horizon,
        target_dim=1,
        feat_dynamic_real_dim=0,
        past_feat_dynamic_real_dim=0,
        context_length=context,
        module=module,
    )


def extract_moirai2_forecast(
    quantiles: Any,
    *,
    row: int,
    horizon: int,
    spot: float,
) -> dict[str, float]:
    arr = np.asarray(quantiles, dtype=float)
    q = arr[row]
    vals = q[:, horizon - 1]
    if vals.ndim > 1:
        vals = vals[:, 0]
    return {
        "spot": float(spot),
        "median_return": float(vals[4] / spot - 1.0),
        "q10_return": float(vals[0] / spot - 1.0),
        "q90_return": float(vals[8] / spot - 1.0),
    }


def forecast_decisions(
    df: pd.DataFrame,
    indices: list[int],
    *,
    context: int,
    horizon: int,
    cache_path: Path,
    batch_size: int = 16,
) -> tuple[dict[int, dict[str, float]], int, int]:
    forecasts = load_cache(cache_path)
    cache_before = len(forecasts)
    missing = [i for i in indices if i not in forecasts and i >= 32]
    if not missing:
        return forecasts, cache_before, len(forecasts)

    model = build_moirai2_model(context, horizon)
    close = df["close"].to_numpy(dtype=float)
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        inputs = [close[max(0, i - context + 1) : i + 1].astype(np.float32) for i in batch]
        try:
            quantiles = model.predict(inputs)
            for row, i in enumerate(batch):
                forecasts[i] = extract_moirai2_forecast(
                    quantiles,
                    row=row,
                    horizon=horizon,
                    spot=float(close[i]),
                )
        except Exception:
            for i in batch:
                quantiles = model.predict(
                    [close[max(0, i - context + 1) : i + 1].astype(np.float32)]
                )
                forecasts[i] = extract_moirai2_forecast(
                    quantiles,
                    row=0,
                    horizon=horizon,
                    spot=float(close[i]),
                )
        save_cache(cache_path, forecasts)
        print(f"forecasted {min(start + len(batch), len(missing))}/{len(missing)}")
    return forecasts, cache_before, len(forecasts)


def apply_gate(
    signals: np.ndarray,
    forecasts: dict[int, dict[str, float]],
    *,
    min_edge_pct: float,
    risk_floor_pct: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    filtered = np.ones(len(signals), dtype=int)
    position = 0
    blocked_dir = 0
    diag: dict[str, Any] = {
        "decision_points": 0,
        "allowed_long": 0,
        "allowed_short": 0,
        "blocked_long": 0,
        "blocked_short": 0,
        "blocked_entries": [],
        "signals_changed": 0,
    }
    for i, raw in enumerate(signals.astype(int)):
        if raw == 1:
            filtered[i] = 1
            continue
        target = signal_target(int(raw), position)
        if target != blocked_dir:
            blocked_dir = 0
        if target == 0:
            filtered[i] = 0
            position = 0
            continue
        if target == position:
            filtered[i] = int(raw)
            continue
        if blocked_dir == target:
            filtered[i] = 0 if position else 1
            continue

        forecast = forecasts[i]
        diag["decision_points"] += 1
        if forecast_allows(target, forecast, min_edge_pct, risk_floor_pct):
            filtered[i] = int(raw)
            position = target
            diag["allowed_long" if target > 0 else "allowed_short"] += 1
            continue

        filtered[i] = 0 if position else 1
        if position:
            position = 0
        blocked_dir = target
        direction = "long" if target > 0 else "short"
        diag["blocked_long" if target > 0 else "blocked_short"] += 1
        diag["blocked_entries"].append(
            {
                "bar": i,
                "time": bar_time_placeholder(i),
                "direction": direction,
                **{k: float(forecast[k]) for k in ("median_return", "q10_return", "q90_return")},
            }
        )

    diag["signals_changed"] = int(np.sum(filtered != signals))
    return filtered, diag


def bar_time_placeholder(i: int) -> str:
    return str(i)


def blocked_attribution(
    signals: np.ndarray,
    df: pd.DataFrame,
    position_sizes: np.ndarray | None,
    blocked_entries: list[dict[str, Any]],
    split_idx: int,
) -> dict[str, Any]:
    exec_signals, exec_sizes = shift_for_next_open(signals, position_sizes)
    ev = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=COMMISSION,
        slippage=SLIPPAGE,
        execution_price="signal_bar_open",
    )
    _, _, trades = ev.evaluate(
        exec_signals,
        df["close"].to_numpy(dtype=float),
        df=df,
        position_sizes=exec_sizes,
    )
    closed = [t for t in trades if t.get("pnl") is not None]
    by_decision_bar = {int(t["entry_step"]) - 1: t for t in closed}
    rows = []
    for entry in blocked_entries:
        i = int(entry["bar"])
        trade = by_decision_bar.get(i)
        row = {**entry, "time": bar_time(df, i), "in_oos": i >= split_idx}
        if trade:
            row.update(
                {
                    "entry_bar": int(trade["entry_step"]),
                    "entry_time": bar_time(df, int(trade["entry_step"])),
                    "exit_bar": int(trade["step"]),
                    "exit_time": bar_time(df, int(trade["step"])),
                    "bars_held": int(trade["step"]) - int(trade["entry_step"]),
                    "pnl": float(trade["pnl"]),
                }
            )
        rows.append(row)

    matched = [r for r in rows if "pnl" in r]
    matched_oos = [r for r in matched if r["in_oos"]]
    return {
        "summary": {
            "blocked_entries": len(rows),
            "matched_trades": len(matched),
            "blocked_oos": sum(1 for r in rows if r["in_oos"]),
            "blocked_losers": sum(1 for r in matched if r["pnl"] < 0),
            "blocked_winners": sum(1 for r in matched if r["pnl"] > 0),
            "blocked_original_pnl": float(sum(r["pnl"] for r in matched)),
            "blocked_oos_original_pnl": float(sum(r["pnl"] for r in matched_oos)),
        },
        "rows": rows,
    }


def pct(x: float | None) -> str:
    if x is None:
        return ""
    return f"{x * 100:.2f}%"


def write_outputs(report: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    json_path = OUTPUT.with_suffix(".json")
    csv_path = OUTPUT.with_suffix(".csv")
    md_path = OUTPUT.with_suffix(".md")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fieldnames = sorted({k for row in rows for k in row})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    md = [
        "# exp_0095 BTC v2.2 + Moirai2 exp_0093 compare",
        "",
        "- base: `channel_breakout_v2_2_m375_bbm375_1p5`",
        "- gate: Moirai2 exp_0093 (`min_edge=-2%`, `risk_floor=4%`, `context=1024`, `horizon=72`)",
        "- execution: completed-bar signal, `next_bar_open` fill",
        "- symbol: BTCUSDT 5m",
        "",
        "| days | raw OOS | Moirai OOS | Δ OOS | raw full | Moirai full | raw DD | Moirai DD | blocked full/oos | blocked pnl full/oos |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["summary"]:
        md.append(
            "| {days} | {raw_oos} | {gate_oos} | {delta_oos} | {raw_full} | "
            "{gate_full} | {raw_dd} | {gate_dd} | {blocked} / {blocked_oos} | "
            "{blocked_pnl:.2f} / {blocked_oos_pnl:.2f} |".format(
                days=row["days"],
                raw_oos=pct(row["raw_oos_return"]),
                gate_oos=pct(row["gate_oos_return"]),
                delta_oos=pct(row["gate_delta_oos"]),
                raw_full=pct(row["raw_full_return"]),
                gate_full=pct(row["gate_full_return"]),
                raw_dd=pct(row["raw_full_dd"]),
                gate_dd=pct(row["gate_full_dd"]),
                blocked=row["blocked_full"],
                blocked_oos=row["blocked_oos"],
                blocked_pnl=row["blocked_original_pnl"],
                blocked_oos_pnl=row["blocked_oos_original_pnl"],
            )
        )
    wins = sum(1 for row in report["summary"] if row["gate_delta_oos"] > 0)
    md.extend(
        [
            "",
            f"结论短句：Moirai2 在 BTC OOS 赢了 {wins}/{len(report['summary'])} 个窗口。",
            "",
            "如果要继续，优先看 730/1300d 的 OOS 和 DD；60d/365d 只能当烟测，不适合下结论。",
        ]
    )
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    spec = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    params = spec["params"]
    checkpoint = load_checkpoint(CHECKPOINT)
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []

    for data_path in DATA_FILES:
        if not data_path.exists():
            print(f"skip missing {data_path}")
            continue
        days = int(data_path.stem.split("_")[-1].removesuffix("d"))
        print(f"=== BTC {days}d ===")
        df_is, df_oos, split_idx = _load_and_split_data(data_path)
        df = pd.concat([df_is, df_oos], ignore_index=True)
        signals = _generate_v21_signals(checkpoint, df)
        position_sizes = _position_sizes_from_config(checkpoint, len(signals), df)
        raw = summarize(signals, df, split_idx, position_sizes)

        decision_indices = collect_decision_indices(signals)
        cache_path = OUTPUT.parent / f"exp_0095_btc_moirai2_c1024_h72_{days}d_cache.json"
        forecasts, cache_before, cache_after = forecast_decisions(
            df,
            decision_indices,
            context=int(params["context"]),
            horizon=int(params["horizon"]),
            cache_path=cache_path,
        )
        filtered, diag = apply_gate(
            signals,
            {i: forecasts[i] for i in decision_indices},
            min_edge_pct=float(params["min_edge_pct"]),
            risk_floor_pct=float(params["risk_floor_pct"]),
        )
        gated = summarize(filtered, df, split_idx, position_sizes)
        attribution = blocked_attribution(
            signals,
            df,
            position_sizes,
            diag["blocked_entries"],
            split_idx,
        )
        attrib_summary = attribution["summary"]
        row = {
            "days": days,
            "data": str(data_path.relative_to(PROJECT_ROOT)),
            "bars": len(df),
            "start": bar_time(df, 0),
            "end": bar_time(df, len(df) - 1),
            "split_idx": split_idx,
            "decisions": len(decision_indices),
            "cache_before": cache_before,
            "cache_after": cache_after,
            "raw_is_return": raw["is"]["return"],
            "raw_oos_return": raw["oos"]["return"],
            "raw_full_return": raw["full"]["return"],
            "raw_full_dd": raw["full"]["dd"],
            "raw_trades_full": raw["full"]["trades"],
            "raw_rolling_12m_min": raw["rolling_12m_min_return"],
            "raw_fee_10bp_full": raw["fee_10bp_return_full"],
            "gate_is_return": gated["is"]["return"],
            "gate_oos_return": gated["oos"]["return"],
            "gate_full_return": gated["full"]["return"],
            "gate_full_dd": gated["full"]["dd"],
            "gate_trades_full": gated["full"]["trades"],
            "gate_rolling_12m_min": gated["rolling_12m_min_return"],
            "gate_fee_10bp_full": gated["fee_10bp_return_full"],
            "gate_delta_oos": gated["oos"]["return"] - raw["oos"]["return"],
            "gate_delta_full": gated["full"]["return"] - raw["full"]["return"],
            "blocked_full": int(diag["blocked_long"] + diag["blocked_short"]),
            "blocked_oos": int(attrib_summary["blocked_oos"]),
            "blocked_original_pnl": attrib_summary["blocked_original_pnl"],
            "blocked_oos_original_pnl": attrib_summary["blocked_oos_original_pnl"],
            "signals_changed": diag["signals_changed"],
        }
        rows.append(row)
        summary.append(row)
        details.append(
            {
                "days": days,
                "raw": raw,
                "gated": gated,
                "gate_diag": diag,
                "blocked_attribution": attribution,
            }
        )

    report = {
        "scope": {
            "experiment_id": "exp_0095",
            "question": "Can ETH v2.2 + Moirai2 exp_0093 generalize to BTC downloaded windows?",
            "checkpoint": str(CHECKPOINT.relative_to(PROJECT_ROOT)),
            "candidate": str(CANDIDATE.relative_to(PROJECT_ROOT)),
            "model": str(MODEL.relative_to(PROJECT_ROOT)),
            "execution": "next_bar_open",
            "symbol": "BTCUSDT",
            "interval": "5m",
        },
        "summary": summary,
        "details": details,
    }
    write_outputs(report, rows)
    print(f"wrote {OUTPUT.with_suffix('.md')}")


if __name__ == "__main__":
    main()
