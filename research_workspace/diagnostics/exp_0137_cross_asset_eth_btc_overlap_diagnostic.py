from __future__ import annotations

import argparse
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

EXP0136_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0136_v22_multitimeframe_diversification_diagnostic.py"
spec0136 = importlib.util.spec_from_file_location("exp0136_multitimeframe", EXP0136_PATH)
exp0136 = importlib.util.module_from_spec(spec0136)
sys.modules[spec0136.name] = exp0136
assert spec0136.loader is not None
spec0136.loader.exec_module(exp0136)

OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0137_cross_asset_eth_btc_overlap_diagnostic"
BTC_DATA = PROJECT_ROOT / "data/crypto/BTCUSDT_5m_1300d.parquet"

INITIAL = exp0136.INITIAL
FEE10_COMMISSION = exp0136.FEE10_COMMISSION
ENTRY_LOOKBACK = 375
MIN_HOLD_BARS = 432
SPLIT_RATIO = 0.70
BTC_DD_GATE = -0.75
SYNC_DEEP_LOSS_GATE = -0.10

ETH = "ETH_v22_moirai_overlap"
BTC = "BTC_core_donchian_overlap"
ASSETS = ("ETHUSDT", "BTCUSDT")
COMBO_SPECS = [
    ("combo_80_20_ETH_BTC", {ETH: 0.80, BTC: 0.20}),
    ("combo_70_30_ETH_BTC", {ETH: 0.70, BTC: 0.30}),
    ("combo_60_40_ETH_BTC", {ETH: 0.60, BTC: 0.40}),
]

core_donchian_signals = exp0136.core_donchian_signals
shifted_next_open_signals = exp0136.shifted_next_open_signals


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{value * 100:.2f}%"


def safe_float(value: Any) -> float:
    return exp0136.safe_float(value)


def json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return str(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def load_eth_baseline() -> tuple[pd.DataFrame, np.ndarray, pd.Timestamp, dict[str, Any]]:
    df, signals, scope = exp0136.exp0110.load_base()
    df = exp0136.clean_ohlcv(df)
    split_idx = int(scope["split_idx"])
    split_time = pd.Timestamp(df["datetime"].iloc[split_idx])
    return df, signals.astype(int), split_time, scope


def load_btc_core() -> tuple[pd.DataFrame, np.ndarray, pd.Timestamp]:
    df = exp0136.clean_ohlcv(pd.read_parquet(BTC_DATA))
    signals = core_donchian_signals(df, ENTRY_LOOKBACK, MIN_HOLD_BARS)
    split_idx = int(len(df) * SPLIT_RATIO)
    split_idx = min(max(split_idx, 1), len(df) - 1)
    return df, signals, pd.Timestamp(df["datetime"].iloc[split_idx])


def overlap_bounds(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = max(pd.Timestamp(left["datetime"].iloc[0]), pd.Timestamp(right["datetime"].iloc[0]))
    end = min(pd.Timestamp(left["datetime"].iloc[-1]), pd.Timestamp(right["datetime"].iloc[-1]))
    if start >= end:
        raise ValueError("ETH/BTC data windows do not overlap")
    return start, end


def slice_by_time_range(
    df: pd.DataFrame,
    signals: np.ndarray,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, np.ndarray]:
    times = pd.to_datetime(df["datetime"])
    mask = (times >= start) & (times <= end)
    idx = np.flatnonzero(mask.to_numpy())
    if len(idx) == 0:
        raise ValueError("No rows inside requested time range")
    return df.iloc[idx].reset_index(drop=True), signals[idx]


def split_time_for_overlap(reference_df: pd.DataFrame, split_ratio: float = SPLIT_RATIO) -> tuple[int, pd.Timestamp]:
    split_idx = int(len(reference_df) * split_ratio)
    split_idx = min(max(split_idx, 1), len(reference_df) - 1)
    return split_idx, pd.Timestamp(reference_df["datetime"].iloc[split_idx])


def daily_equity_no_fill(df: pd.DataFrame, equity: np.ndarray) -> pd.Series:
    series = pd.Series(equity, index=pd.to_datetime(df["datetime"]))
    out = series.resample("1D").last().dropna()
    out.name = "equity"
    return out


def aligned_daily_frame_no_fill(series_by_name: dict[str, pd.Series]) -> pd.DataFrame:
    return pd.concat(series_by_name, axis=1).dropna()


def evaluate_signals(
    name: str,
    symbol: str,
    panel: str,
    role: str,
    df: pd.DataFrame,
    signals: np.ndarray,
    split_time: pd.Timestamp,
) -> dict[str, Any]:
    equity, trades = exp0136.evaluate_next_open(signals, df)
    oos_df, oos_signals = exp0136.slice_by_time(df, signals, split_time)
    oos_equity, oos_trades = exp0136.evaluate_next_open(oos_signals, oos_df)
    fee10_equity, _ = exp0136.evaluate_next_open(signals, df, commission=FEE10_COMMISSION)
    fee10_oos_equity, _ = exp0136.evaluate_next_open(oos_signals, oos_df, commission=FEE10_COMMISSION)

    daily = daily_equity_no_fill(df, equity)
    fee10_daily = daily_equity_no_fill(df, fee10_equity)
    full_m = exp0136.equity_metrics(equity)
    oos_m = exp0136.equity_metrics(oos_equity)
    fee10_m = exp0136.equity_metrics(fee10_equity)
    fee10_oos_m = exp0136.equity_metrics(fee10_oos_equity)
    returns = exp0136.trade_returns(trades)
    hold_bars = [int(trade["step"]) - int(trade["entry_step"]) for trade in trades]
    row = {
        "name": name,
        "symbol": symbol,
        "panel": panel,
        "role": role,
        "start_time": str(pd.Timestamp(df["datetime"].iloc[0])),
        "end_time": str(pd.Timestamp(df["datetime"].iloc[-1])),
        "split_time": str(split_time),
        "entry_lookback": ENTRY_LOOKBACK if symbol == "BTCUSDT" else "",
        "min_hold_bars": MIN_HOLD_BARS if symbol == "BTCUSDT" else "",
        "full_return": full_m["return"],
        "oos_return": oos_m["return"],
        "max_dd": full_m["dd"],
        "rolling12_min": exp0136.rolling_min_return(daily, 365),
        "trade_count": len(trades),
        "trades_per_year": len(trades) / exp0136.duration_years(df),
        "oos_trade_count": len(oos_trades),
        "oos_trades_per_year": len(oos_trades) / exp0136.duration_years(oos_df),
        "avg_trade_return": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return": float(np.median(returns)) if returns else 0.0,
        "avg_trade_pnl": float(np.mean([safe_float(trade.get("pnl")) for trade in trades])) if trades else 0.0,
        "median_trade_pnl": float(np.median([safe_float(trade.get("pnl")) for trade in trades])) if trades else 0.0,
        "avg_hold_bars": float(np.mean(hold_bars)) if hold_bars else 0.0,
        "median_hold_bars": float(np.median(hold_bars)) if hold_bars else 0.0,
        "max_idle_days": exp0136.max_idle_days(df, trades),
        "fee10_full_return": fee10_m["return"],
        "fee10_oos_return": fee10_oos_m["return"],
        "fee10_max_dd": fee10_m["dd"],
        **exp0136.top_worst_contribution(trades),
        **exp0136.year_wlf(daily),
    }
    return {
        "row": row,
        "df": df,
        "signals": signals,
        "equity": equity,
        "fee10_equity": fee10_equity,
        "trades": trades,
        "daily": daily,
        "fee10_daily": fee10_daily,
    }


def return_corr(a: pd.Series, b: pd.Series) -> float:
    return exp0136.return_corr(a, b)


def correlation_rows(daily_frame: pd.DataFrame) -> list[dict[str, Any]]:
    returns = daily_frame[[ETH, BTC]].pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    monthly = daily_frame[[ETH, BTC]].resample("ME").last().pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    eth_ret = returns[ETH]
    btc_ret = returns[BTC]
    roll90 = eth_ret.rolling(90).corr(btc_ret).dropna()
    roll180 = eth_ret.rolling(180).corr(btc_ret).dropna()
    return [
        {
            "left": ETH,
            "right": BTC,
            "daily_return_corr": return_corr(eth_ret, btc_ret),
            "rolling90_corr_mean": float(roll90.mean()) if len(roll90) else 0.0,
            "rolling90_corr_max": float(roll90.max()) if len(roll90) else 0.0,
            "rolling180_corr_mean": float(roll180.mean()) if len(roll180) else 0.0,
            "rolling180_corr_max": float(roll180.max()) if len(roll180) else 0.0,
            "monthly_return_corr": return_corr(monthly[ETH], monthly[BTC]) if ETH in monthly and BTC in monthly else 0.0,
        }
    ]


def period_return(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    return exp0136.period_return(series, start, end)


def dd_overlap_rows(daily_frame: pd.DataFrame) -> list[dict[str, Any]]:
    dd = {name: exp0136.max_drawdown_period(daily_frame[name]) for name in (ETH, BTC)}
    worst30 = {name: exp0136.rolling_worst_period(daily_frame[name], 30) for name in (ETH, BTC)}
    worst90 = {name: exp0136.rolling_worst_period(daily_frame[name], 90) for name in (ETH, BTC)}
    rows: list[dict[str, Any]] = []
    for anchor, other in ((ETH, BTC), (BTC, ETH)):
        anchor_dd = dd[anchor]
        rows.append(
            {
                "anchor": anchor,
                "other": other,
                "start": str(anchor_dd["start"]),
                "end": str(anchor_dd["end"]),
                "anchor_return": anchor_dd["return"],
                "anchor_dd": anchor_dd["dd"],
                "other_return": period_return(daily_frame[other], anchor_dd["start"], anchor_dd["end"]),
                "dd_overlap_ratio": exp0136.overlap_ratio(anchor_dd, dd[other]),
                "worst30_overlap_ratio": exp0136.overlap_ratio(worst30[anchor], worst30[other]),
                "worst90_overlap_ratio": exp0136.overlap_ratio(worst90[anchor], worst90[other]),
            }
        )
    return rows


def top_winner_rows(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, result in results.items():
        top = sorted(
            [trade for trade in result["trades"] if safe_float(trade.get("pnl")) > 0.0],
            key=lambda trade: safe_float(trade.get("pnl")),
            reverse=True,
        )[:20]
        df = result["df"]
        for rank, trade in enumerate(top, 1):
            entry = int(trade["entry_step"])
            exit_ = int(trade["step"])
            entry_time = pd.Timestamp(df["datetime"].iloc[min(entry, len(df) - 1)])
            exit_time = pd.Timestamp(df["datetime"].iloc[min(exit_, len(df) - 1)])
            notional = safe_float(trade.get("entry_notional"))
            pnl = safe_float(trade.get("pnl"))
            rows.append(
                {
                    "sleeve": name,
                    "rank": rank,
                    "entry_time": str(entry_time),
                    "exit_time": str(exit_time),
                    "month": str(entry_time.to_period("M")),
                    "quarter": str(entry_time.to_period("Q")),
                    "pnl": pnl,
                    "return": pnl / notional if notional > 0 else float("nan"),
                    "entry_notional": notional,
                }
            )
    return rows


def windows_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return exp0136.windows_overlap(left, right)


def top_overlap_rows(top_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in top_rows:
        by_name.setdefault(str(row["sleeve"]), []).append(row)
    eth_rows = by_name.get(ETH, [])
    btc_rows = by_name.get(BTC, [])
    eth_overlap = [row for row in eth_rows if any(windows_overlap(row, other) for other in btc_rows)]
    btc_overlap = [row for row in btc_rows if any(windows_overlap(row, other) for other in eth_rows)]
    eth_total = sum(safe_float(row["pnl"]) for row in eth_rows) or 1.0
    btc_total = sum(safe_float(row["pnl"]) for row in btc_rows) or 1.0
    return [
        {
            "left": ETH,
            "right": BTC,
            "top20_overlap_count": len(eth_overlap),
            "top20_overlap_count_eth": len(eth_overlap),
            "top20_overlap_count_btc": len(btc_overlap),
            "top20_overlap_by_month": len({row["month"] for row in eth_rows} & {row["month"] for row in btc_rows}),
            "top20_overlap_by_quarter": len({row["quarter"] for row in eth_rows} & {row["quarter"] for row in btc_rows}),
            "eth_contribution_overlap": sum(safe_float(row["pnl"]) for row in eth_overlap) / eth_total,
            "btc_contribution_overlap": sum(safe_float(row["pnl"]) for row in btc_overlap) / btc_total,
        }
    ]


def validate_combo_weights(weights: dict[str, float]) -> None:
    total = sum(float(value) for value in weights.values())
    if not np.isclose(total, 1.0):
        raise ValueError(f"combo weights must sum to 1.0, got {total}")


def combo_daily_equity(daily_frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    validate_combo_weights(weights)
    frame = daily_frame[list(weights)].dropna()
    returns = frame.pct_change().fillna(0.0)
    combo_returns = sum(float(weight) * returns[name] for name, weight in weights.items())
    equity = INITIAL * (1.0 + combo_returns).cumprod()
    equity.name = "combo"
    return equity


def dd_improve_ratio(base_dd: float, combo_dd: float) -> float:
    return 1.0 - abs(combo_dd) / abs(base_dd) if base_dd else 0.0


def combo_rows(
    daily_frame: pd.DataFrame,
    fee10_daily_frame: pd.DataFrame,
    eth_row: dict[str, Any],
    split_time: pd.Timestamp,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    eth_oos = float(eth_row["oos_return"])
    eth_dd = float(eth_row["max_dd"])
    eth_fee10_dd = float(eth_row["fee10_max_dd"])
    for name, weights in COMBO_SPECS:
        daily = combo_daily_equity(daily_frame, weights)
        fee10_daily = combo_daily_equity(fee10_daily_frame, weights)
        full_m = exp0136.equity_metrics(daily.to_numpy(dtype=float))
        fee10_m = exp0136.equity_metrics(fee10_daily.to_numpy(dtype=float))
        oos = daily.loc[daily.index >= split_time.floor("D")]
        fee10_oos = fee10_daily.loc[fee10_daily.index >= split_time.floor("D")]
        oos_return = float(oos.iloc[-1] / oos.iloc[0] - 1.0) if len(oos) > 1 else 0.0
        fee10_oos_return = float(fee10_oos.iloc[-1] / fee10_oos.iloc[0] - 1.0) if len(fee10_oos) > 1 else 0.0
        improve = dd_improve_ratio(eth_dd, full_m["dd"])
        fee10_improve = dd_improve_ratio(eth_fee10_dd, fee10_m["dd"])
        row = {
            "combo": name,
            "weights": json.dumps(weights, sort_keys=True),
            "combo_full_return": full_m["return"],
            "combo_oos_return": oos_return,
            "combo_max_dd": full_m["dd"],
            "combo_rolling12_min": exp0136.rolling_min_return(daily, 365),
            "combo_worst90d": exp0136.rolling_worst_period(daily, 90)["return"],
            "combo_return_dd_ratio": full_m["return"] / abs(full_m["dd"]) if full_m["dd"] else 0.0,
            "combo_fee10_oos_return": fee10_oos_return,
            "combo_fee10_max_dd": fee10_m["dd"],
            "dd_improve_ratio_vs_eth_same_window": improve,
            "fee10_dd_improve_ratio_vs_eth_same_window": fee10_improve,
            "oos_not_worse_than_eth_by_20pct": oos_return >= eth_oos * 0.80,
        }
        rows.append(row)
    return rows


def stage_gate_rows(
    btc_row: dict[str, Any],
    eth_row: dict[str, Any],
    combos: list[dict[str, Any]],
    dd_rows: list[dict[str, Any]],
    top_overlap: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eth_dd_row = next(row for row in dd_rows if row["anchor"] == ETH and row["other"] == BTC)
    top_row = top_overlap[0] if top_overlap else {}
    btc_oos_positive = float(btc_row["oos_return"]) > 0.0
    btc_dd_pass = float(btc_row["max_dd"]) > BTC_DD_GATE
    btc_fee10_oos_positive = float(btc_row["fee10_oos_return"]) > 0.0
    btc_year_balance_ok = int(btc_row["year_losses"]) <= int(btc_row["year_wins"])
    btc_sleeve_pass = btc_oos_positive and btc_dd_pass and btc_fee10_oos_positive and btc_year_balance_ok
    eth_maxdd_btc_not_deep_loss = safe_float(eth_dd_row["other_return"]) > SYNC_DEEP_LOSS_GATE
    top20_misaligned = (
        safe_float(top_row.get("top20_overlap_count_eth")) <= 5
        and safe_float(top_row.get("eth_contribution_overlap")) < 0.35
        and safe_float(top_row.get("btc_contribution_overlap")) < 0.35
    )
    rows: list[dict[str, Any]] = []
    for combo in combos:
        combo_dd_improve_15 = safe_float(combo["dd_improve_ratio_vs_eth_same_window"]) >= 0.15
        combo_oos_preserved = bool(combo["oos_not_worse_than_eth_by_20pct"])
        combo_rolling12_improved = safe_float(combo["combo_rolling12_min"]) > safe_float(eth_row["rolling12_min"])
        combo_fee10_stable = safe_float(combo["combo_fee10_oos_return"]) > 0.0 and safe_float(
            combo["combo_fee10_max_dd"]
        ) > -0.85
        passes_combo_gate = (
            btc_sleeve_pass
            and combo_dd_improve_15
            and combo_oos_preserved
            and combo_rolling12_improved
            and eth_maxdd_btc_not_deep_loss
            and top20_misaligned
            and combo_fee10_stable
        )
        passes_observe_gate = (
            btc_sleeve_pass
            and combo_dd_improve_15
            and combo_oos_preserved
            and combo_fee10_stable
        )
        rows.append(
            {
                "combo": combo["combo"],
                "btc_oos_positive": btc_oos_positive,
                "btc_dd_pass_gt_minus75": btc_dd_pass,
                "btc_fee10_oos_positive": btc_fee10_oos_positive,
                "btc_year_balance_ok": btc_year_balance_ok,
                "btc_sleeve_pass": btc_sleeve_pass,
                "combo_dd_improve_15": combo_dd_improve_15,
                "combo_oos_preserved": combo_oos_preserved,
                "combo_rolling12_improved": combo_rolling12_improved,
                "eth_maxdd_btc_not_deep_loss": eth_maxdd_btc_not_deep_loss,
                "top20_misaligned": top20_misaligned,
                "combo_fee10_stable": combo_fee10_stable,
                "passes_observe_gate": passes_observe_gate,
                "passes_combo_gate": passes_combo_gate,
            }
        )
    return rows


def verdict_from_stage_gates(stage_rows: list[dict[str, Any]]) -> str:
    if any(bool(row["passes_combo_gate"]) for row in stage_rows):
        return "SHADOW_CANDIDATE"
    if any(bool(row["passes_observe_gate"]) for row in stage_rows):
        return "OBSERVE"
    return "REJECT"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in rows for key in row}) if rows else ["empty"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_all() -> dict[str, Any]:
    eth_df, eth_signals, eth_full_split_time, eth_scope = load_eth_baseline()
    btc_df, btc_signals, btc_full_split_time = load_btc_core()
    overlap_start, overlap_end = overlap_bounds(eth_df, btc_df)
    eth_overlap_df, eth_overlap_signals = slice_by_time_range(eth_df, eth_signals, overlap_start, overlap_end)
    btc_overlap_df, btc_overlap_signals = slice_by_time_range(btc_df, btc_signals, overlap_start, overlap_end)
    _, overlap_split_time = split_time_for_overlap(btc_overlap_df)

    panel_a_eth = evaluate_signals(
        "ETH_v22_moirai_full2600d",
        "ETHUSDT",
        "panel_a_standalone_full_available",
        "v2.2_moirai_baseline",
        eth_df,
        eth_signals,
        eth_full_split_time,
    )
    panel_a_btc = evaluate_signals(
        "BTC_core_donchian_full1300d",
        "BTCUSDT",
        "panel_a_standalone_full_available",
        "core_donchian",
        btc_df,
        btc_signals,
        btc_full_split_time,
    )
    panel_b_eth = evaluate_signals(
        ETH,
        "ETHUSDT",
        "panel_b_eth_btc_overlap",
        "v2.2_moirai_baseline",
        eth_overlap_df,
        eth_overlap_signals,
        overlap_split_time,
    )
    panel_b_btc = evaluate_signals(
        BTC,
        "BTCUSDT",
        "panel_b_eth_btc_overlap",
        "core_donchian",
        btc_overlap_df,
        btc_overlap_signals,
        overlap_split_time,
    )

    overlap_results = {ETH: panel_b_eth, BTC: panel_b_btc}
    daily_frame = aligned_daily_frame_no_fill({ETH: panel_b_eth["daily"], BTC: panel_b_btc["daily"]})
    fee10_daily_frame = aligned_daily_frame_no_fill({ETH: panel_b_eth["fee10_daily"], BTC: panel_b_btc["fee10_daily"]})
    correlations = correlation_rows(daily_frame)
    dd_rows = dd_overlap_rows(daily_frame)
    winners = top_winner_rows(overlap_results)
    top_overlap = top_overlap_rows(winners)
    combos = combo_rows(daily_frame, fee10_daily_frame, panel_b_eth["row"], overlap_split_time)
    gates = stage_gate_rows(panel_b_btc["row"], panel_b_eth["row"], combos, dd_rows, top_overlap)
    verdict = verdict_from_stage_gates(gates)

    sleeve_rows = [panel_a_eth["row"], panel_a_btc["row"], panel_b_eth["row"], panel_b_btc["row"]]
    payload = {
        "experiment_id": "exp_0137_cross_asset_eth_btc_overlap_diagnostic",
        "verdict": verdict,
        "scope": {
            "research_only": True,
            "live_action": "no_change",
            "checkpoint_action": "no_change",
            "config_action": "no_change",
            "oracle_action": "no_change",
            "production_strategy_action": "no_change",
            "included": ["ETHUSDT v2.2+Moirai baseline", "BTCUSDT core Donchian", "1300d overlap"],
            "excluded": ["SOLUSDT", "BTC Moirai", "BTC BB", "BTC regime split", "MTG/BCD", "parameter optimization"],
            "eth_data": str(exp0136.exp0110.helper0108.DATA.relative_to(PROJECT_ROOT)),
            "btc_data": str(BTC_DATA.relative_to(PROJECT_ROOT)),
            "overlap_start": str(overlap_start),
            "overlap_end": str(overlap_end),
            "overlap_split_time": str(overlap_split_time),
            "moirai_blocked": int(eth_scope["moirai_blocked"]),
        },
        "sleeves": sleeve_rows,
        "correlations": correlations,
        "dd_overlap": dd_rows,
        "top_winners": winners,
        "top_overlap": top_overlap,
        "combos": combos,
        "stage_gates": gates,
    }
    write_outputs(payload)
    print("verdict", verdict)
    for row in sleeve_rows:
        print(
            row["name"],
            "oos",
            round(float(row["oos_return"]) * 100, 2),
            "dd",
            round(float(row["max_dd"]) * 100, 2),
            "trades/y",
            round(float(row["trades_per_year"]), 2),
        )
    for combo in combos:
        print(
            combo["combo"],
            "oos",
            round(float(combo["combo_oos_return"]) * 100, 2),
            "dd",
            round(float(combo["combo_max_dd"]) * 100, 2),
            "ddImp",
            round(float(combo["dd_improve_ratio_vs_eth_same_window"]) * 100, 2),
        )
    return payload


def write_outputs(payload: dict[str, Any]) -> None:
    OUT.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")
    write_csv(OUT.with_name(OUT.name + "_sleeves.csv"), payload["sleeves"])
    write_csv(OUT.with_name(OUT.name + "_correlations.csv"), payload["correlations"])
    write_csv(OUT.with_name(OUT.name + "_dd_overlap.csv"), payload["dd_overlap"])
    write_csv(OUT.with_name(OUT.name + "_top_winners.csv"), payload["top_winners"])
    write_csv(OUT.with_name(OUT.name + "_top_overlap.csv"), payload["top_overlap"])
    write_csv(OUT.with_name(OUT.name + "_combos.csv"), payload["combos"])
    write_csv(OUT.with_name(OUT.name + "_stage_gates.csv"), payload["stage_gates"])
    write_report(payload)


def write_report(payload: dict[str, Any]) -> None:
    sleeves = {row["name"]: row for row in payload["sleeves"]}
    lines = [
        "# exp_0137 ETH/BTC cross-asset overlap diagnostic",
        "",
        "- research-only",
        "- no live/checkpoint/config/oracle/production strategy change",
        "- no BTC live route",
        "- no SOL in this round",
        "- no portfolio allocator",
        f"- verdict: `{payload['verdict']}`",
        "",
        "## Scope",
        "",
        "- ETH sleeve: `channel_breakout_v2_2_m375_bbm375_1p5 + moirai2_gate_exp_0093`",
        f"- BTC sleeve: core Donchian `m={ENTRY_LOOKBACK}`, `min_hold_bars={MIN_HOLD_BARS}`",
        f"- overlap: `{payload['scope']['overlap_start']}` to `{payload['scope']['overlap_end']}`",
        f"- overlap split: `{payload['scope']['overlap_split_time']}`",
        "",
        "## Sleeves",
        "",
        "| name | panel | full | OOS | DD | roll12 | trades | trades/year | fee10 OOS | fee10 DD | years W/L/F |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["sleeves"]:
        lines.append(
            f"| {row['name']} | {row['panel']} | {pct(row['full_return'])} | {pct(row['oos_return'])} | "
            f"{pct(row['max_dd'])} | {pct(row['rolling12_min'])} | {row['trade_count']} | "
            f"{float(row['trades_per_year']):.2f} | {pct(row['fee10_oos_return'])} | "
            f"{pct(row['fee10_max_dd'])} | {row['year_wins']}/{row['year_losses']}/{row['year_flat']} |"
        )
    lines.extend(
        [
            "",
            "## Correlation",
            "",
            "| daily | roll90 mean | roll90 max | roll180 mean | roll180 max | monthly |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["correlations"]:
        lines.append(
            f"| {row['daily_return_corr']:.2f} | {row['rolling90_corr_mean']:.2f} | "
            f"{row['rolling90_corr_max']:.2f} | {row['rolling180_corr_mean']:.2f} | "
            f"{row['rolling180_corr_max']:.2f} | {row['monthly_return_corr']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## DD Overlap",
            "",
            "| anchor | other | start | end | anchor DD | other return | DD overlap | worst30 overlap | worst90 overlap |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["dd_overlap"]:
        lines.append(
            f"| {row['anchor']} | {row['other']} | {row['start']} | {row['end']} | "
            f"{pct(row['anchor_dd'])} | {pct(row['other_return'])} | {row['dd_overlap_ratio']:.2f} | "
            f"{row['worst30_overlap_ratio']:.2f} | {row['worst90_overlap_ratio']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Top20 Overlap",
            "",
            "| ETH overlap count | BTC overlap count | month overlap | quarter overlap | ETH pnl overlap | BTC pnl overlap |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["top_overlap"]:
        lines.append(
            f"| {row['top20_overlap_count_eth']} | {row['top20_overlap_count_btc']} | "
            f"{row['top20_overlap_by_month']} | {row['top20_overlap_by_quarter']} | "
            f"{pct(row['eth_contribution_overlap'])} | {pct(row['btc_contribution_overlap'])} |"
        )
    eth_row = sleeves[ETH]
    lines.extend(
        [
            "",
            "## Fixed Combos",
            "",
            "| combo | full | OOS | DD | DD improve | roll12 | worst90 | fee10 OOS | fee10 DD | OOS gate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["combos"]:
        lines.append(
            f"| {row['combo']} | {pct(row['combo_full_return'])} | {pct(row['combo_oos_return'])} | "
            f"{pct(row['combo_max_dd'])} | {pct(row['dd_improve_ratio_vs_eth_same_window'])} | "
            f"{pct(row['combo_rolling12_min'])} | {pct(row['combo_worst90d'])} | "
            f"{pct(row['combo_fee10_oos_return'])} | {pct(row['combo_fee10_max_dd'])} | "
            f"{row['oos_not_worse_than_eth_by_20pct']} |"
        )
    lines.extend(
        [
            "",
            "## Stage Gates",
            "",
            "| combo | BTC sleeve | DD >=15% | OOS kept | roll12 better | ETH DD BTC ok | top20 misaligned | fee10 stable | observe | pass |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in payload["stage_gates"]:
        lines.append(
            f"| {row['combo']} | {row['btc_sleeve_pass']} | {row['combo_dd_improve_15']} | "
            f"{row['combo_oos_preserved']} | {row['combo_rolling12_improved']} | "
            f"{row['eth_maxdd_btc_not_deep_loss']} | {row['top20_misaligned']} | "
            f"{row['combo_fee10_stable']} | {row['passes_observe_gate']} | {row['passes_combo_gate']} |"
        )
    lines.extend(
        [
            "",
            "## Read",
            "",
            f"- ETH same-window OOS baseline is `{pct(eth_row['oos_return'])}` with DD `{pct(eth_row['max_dd'])}`.",
            "- Final verdict is based only on Panel B overlap metrics, not ETH 2600d full-window numbers.",
            "- If REJECT: do not proceed to BTC shadow sleeve.",
            "- If SHADOW_CANDIDATE: next step is independent BTC shadow sleeve only, not live capital allocation and not signal ensemble.",
            "",
            "## Evidence",
            "",
            f"- sleeves: `{OUT.with_name(OUT.name + '_sleeves.csv').relative_to(PROJECT_ROOT)}`",
            f"- correlations: `{OUT.with_name(OUT.name + '_correlations.csv').relative_to(PROJECT_ROOT)}`",
            f"- DD overlap: `{OUT.with_name(OUT.name + '_dd_overlap.csv').relative_to(PROJECT_ROOT)}`",
            f"- top winners: `{OUT.with_name(OUT.name + '_top_winners.csv').relative_to(PROJECT_ROOT)}`",
            f"- top overlap: `{OUT.with_name(OUT.name + '_top_overlap.csv').relative_to(PROJECT_ROOT)}`",
            f"- combos: `{OUT.with_name(OUT.name + '_combos.csv').relative_to(PROJECT_ROOT)}`",
            f"- stage gates: `{OUT.with_name(OUT.name + '_stage_gates.csv').relative_to(PROJECT_ROOT)}`",
            f"- json: `{OUT.with_suffix('.json').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all"], default="all")
    parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
