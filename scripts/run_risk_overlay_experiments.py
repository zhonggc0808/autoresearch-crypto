"""Phase 2 risk overlay experiments for channel_breakout_375_432."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategies.trade_ledger import build_logical_trade_ledger

CHECKPOINT = "checkpoints/channel_breakout_375_432.pt"
DATA_FILE = Path("data/crypto/ETHUSDT_5m_2600d.parquet")
OOS_START = "2024-06-06 14:25:00"
OOS_END = "2026-06-12 02:55:00"
OUTPUT_DIR = Path("research_workspace/diagnostics")
OUTPUT_PREFIX = "channel_breakout_375_432_v2_oos_2600d"
SCRIPT_VERSION = "2026-06-17.risk_overlay.v1"
BASELINES = {"safe": 0.40, "balanced": 0.425}
BLOCK_BARS = 288


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mc-sims", type=int, default=2000)
    parser.add_argument("--mc-seed", type=int, default=2202)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_oos = _load_oos_data()
    _add_lagged_donchian(df_oos)
    strategy = ChannelBreakoutTrendStrategy(entry_lookback=375, min_hold_bars=432)
    signals = strategy.generate_signals(df_oos)
    prices = df_oos["close"].to_numpy(dtype=float)

    config_by_id: dict[str, dict[str, Any]] = {}
    stage_a = _run_stage_a(signals, prices, df_oos, config_by_id)
    stage_a_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_risk_overlay_stage_a.csv"
    pd.DataFrame(_public_rows(stage_a)).to_csv(stage_a_path, index=False)

    stage_b = _run_stage_b(stage_a, config_by_id, signals, prices, df_oos, args.mc_sims, args.mc_seed)
    stage_b_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_risk_overlay_stage_b.csv"
    pd.DataFrame(_public_rows(stage_b)).to_csv(stage_b_path, index=False)

    stage_c = _run_stage_c(stage_b, config_by_id, signals, prices, df_oos, args.mc_sims, args.mc_seed)
    stage_c_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_risk_overlay_stage_c.csv"
    pd.DataFrame(_public_rows(stage_c)).to_csv(stage_c_path, index=False)

    final_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_risk_overlay_final.md"
    final_path.write_text(_final_report(stage_a, stage_b, stage_c, args.mc_sims), encoding="utf-8")

    for path in [stage_a_path, stage_b_path, stage_c_path, final_path]:
        print(f"Wrote {path}")


def _run_stage_a(
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
    config_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    baselines: dict[float, dict[str, Any]] = {}
    for tier, base_size in BASELINES.items():
        config_id = f"baseline_{tier}_{base_size:g}"
        config = {"base_size": base_size}
        config_by_id[config_id] = config
        row = _evaluate_config(config_id, "baseline", tier, config, signals, prices, df)
        row["stage_a_pass"] = True
        row["stage_a_reason"] = "baseline"
        rows.append(row)
        baselines[base_size] = row

    for tier, base_size in BASELINES.items():
        for config_id, family, config in _single_overlay_configs(tier, base_size):
            config_by_id[config_id] = config
            row = _evaluate_config(config_id, family, tier, config, signals, prices, df)
            row.update(_stage_a_gate(row, baselines[base_size]))
            rows.append(row)
    return rows


def _single_overlay_configs(tier: str, base_size: float):
    dd_sets = {
        "dd_v1": [(0.05, 1.0), (0.10, 0.90), (0.15, 0.75), (0.20, 0.50)],
        "dd_v2": [(0.05, 0.95), (0.10, 0.85), (0.15, 0.65), (0.20, 0.40)],
        "dd_v3": [(0.05, 1.0), (0.08, 0.90), (0.12, 0.75), (0.18, 0.50)],
    }
    for name, tiers in dd_sets.items():
        yield (
            f"{tier}_{name}",
            "equity_dd_sizing",
            {
                "base_size": base_size,
                "equity_dd_sizing": {
                    "enabled": True,
                    "tiers": tiers,
                    "no_new_entry_dd": 0.20,
                    "kill_switch_dd": 0.25,
                },
            },
        )

    for threshold in [-0.04, -0.05, -0.06]:
        for action in ["reduce_half", "close"]:
            yield (
                f"{tier}_adverse_{threshold:g}_{action}",
                "adverse_stop",
                {
                    "base_size": base_size,
                    "adverse_stop": {
                        "enabled": True,
                        "threshold_pct": threshold,
                        "action": action,
                    },
                },
            )

    time_sets = {
        "time_v1": [(576, -0.02, "reduce_half"), (864, -0.03, "close"), (1152, -0.01, "close")],
        "time_v2": [(288, -0.015, "reduce_half"), (576, -0.02, "close"), (864, -0.025, "close")],
    }
    for name, tiers_config in time_sets.items():
        yield (
            f"{tier}_{name}",
            "time_in_loss_stop",
            {
                "base_size": base_size,
                "time_in_loss_stop": {"enabled": True, "tiers": tiers_config},
            },
        )

    for atr_multiple in [2.0, 2.5, 3.0]:
        for require_break in [True, False]:
            yield (
                f"{tier}_squeeze_{atr_multiple:g}_{int(require_break)}",
                "squeeze_stop",
                {
                    "base_size": base_size,
                    "squeeze_stop": {
                        "enabled": True,
                        "atr_multiple": atr_multiple,
                        "require_channel_break": require_break,
                        "channel_upper_lagged": True,
                        "action": "reduce_half",
                    },
                },
            )

    be_grid = [
        (0.025, 0.0, "reduce_half"),
        (0.03, 0.0, "reduce_half"),
        (0.04, 0.005, "reduce_half"),
        (0.03, 0.0, "close"),
    ]
    for trigger_mfe, stop_level, action in be_grid:
        yield (
            f"{tier}_be_{trigger_mfe:g}_{stop_level:g}_{action}",
            "break_even_stop",
            {
                "base_size": base_size,
                "break_even_stop": {
                    "enabled": True,
                    "trigger_mfe_pct": trigger_mfe,
                    "stop_level_pct": stop_level,
                    "action": action,
                },
            },
        )


def _stage_a_gate(row: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    ret_drop = _ratio_drop(row["total_return"], baseline["total_return"])
    dd_improve = _drawdown_improvement(row["max_drawdown"], baseline["max_drawdown"])
    tail_improve = row["top20_loss_contribution"] < baseline["top20_loss_contribution"]
    reasons = []
    if ret_drop > 0.35 and dd_improve <= 0.25:
        reasons.append("return_drop_without_dd_payoff")
    if dd_improve < 0.05 and not tail_improve:
        reasons.append("weak_dd_and_tail_improvement")
    if row["pnl_mismatch_abs"] > INITIAL_CAPITAL * 0.001:
        reasons.append("event_logical_pnl_mismatch")
    return {
        "return_drop_vs_baseline": ret_drop,
        "dd_improvement_vs_baseline": dd_improve,
        "stage_a_pass": not reasons,
        "stage_a_reason": "pass" if not reasons else ";".join(reasons),
    }


def _run_stage_b(
    stage_a: list[dict[str, Any]],
    config_by_id: dict[str, dict[str, Any]],
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
    mc_sims: int,
    seed: int,
) -> list[dict[str, Any]]:
    rows = []
    for family in ["equity_dd_sizing", "adverse_stop", "time_in_loss_stop", "squeeze_stop", "break_even_stop"]:
        candidates = [
            row for row in stage_a if row["overlay_family"] == family and row.get("stage_a_pass")
        ]
        candidates.sort(key=lambda row: (row["dd_improvement_vs_baseline"], row["total_return"]), reverse=True)
        for row in candidates[:2]:
            mc = _monte_carlo(row["equity_curve"], mc_sims, seed)
            rows.append({k: v for k, v in row.items() if k != "equity_curve"} | mc)
    return rows


def _run_stage_c(
    stage_b: list[dict[str, Any]],
    config_by_id: dict[str, dict[str, Any]],
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
    mc_sims: int,
    seed: int,
) -> list[dict[str, Any]]:
    best = _best_stage_b_by_family(stage_b)
    combos = []
    if "equity_dd_sizing" in best and "adverse_stop" in best:
        combos.append(("combo_conservative", "safe", ["equity_dd_sizing", "adverse_stop"]))
        combos.append(("combo_balanced", "balanced", ["equity_dd_sizing", "adverse_stop"]))
    if all(name in best for name in ["equity_dd_sizing", "adverse_stop", "time_in_loss_stop"]):
        combos.append(("combo_full", "balanced", ["equity_dd_sizing", "adverse_stop", "time_in_loss_stop"]))

    rows = []
    for config_id, tier, families in combos:
        config = {"base_size": BASELINES[tier]}
        for family in families:
            _merge_overlay(config, config_by_id[best[family]["config_id"]])
        row = _evaluate_config(config_id, "combo", tier, config, signals, prices, df)
        baseline = next(
            item for item in stage_b if item["config_id"].startswith(f"baseline_{tier}_")
        ) if any(item["config_id"].startswith(f"baseline_{tier}_") for item in stage_b) else None
        if baseline is None:
            baseline_config = {"base_size": BASELINES[tier]}
            baseline = _evaluate_config(
                f"baseline_{tier}_{BASELINES[tier]:g}",
                "baseline",
                tier,
                baseline_config,
                signals,
                prices,
                df,
            )
        row.update(_damaged_winner_stats(baseline["logical_trades"], row["logical_trades"]))
        rows.append(row | _monte_carlo(row["equity_curve"], mc_sims, seed))
    return rows


def _evaluate_config(
    config_id: str,
    family: str,
    tier: str,
    config: dict[str, Any],
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
) -> dict[str, Any]:
    evaluator = StrategyEvaluator()
    equity, events = evaluator.simulate(signals, prices, df=df, stop_config=config)
    metrics = evaluator.compute_metrics(equity, events)
    logical = build_logical_trade_ledger(events)
    event_pnl = sum(float(event.get("pnl", 0.0)) for event in events if event.get("pnl") is not None)
    logical_pnl = sum(float(trade["total_pnl"]) for trade in logical)
    losses = sorted(float(trade["total_pnl"]) for trade in logical if trade["total_pnl"] < 0)
    stop_counts = Counter(
        event.get("exit_reason")
        for event in events
        if event.get("exit_reason") not in (None, "signal")
    )
    return {
        "config_id": config_id,
        "overlay_family": family,
        "baseline_tier": tier,
        "base_size": config["base_size"],
        "total_return": metrics["total_return"],
        "annualized_return": metrics["annualized_return"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "event_trade_count": sum(1 for event in events if event.get("pnl") is not None),
        "logical_trade_count": len(logical),
        "partial_close_count": sum(1 for event in events if event.get("is_partial")),
        "stop_trigger_count": sum(stop_counts.values()),
        "stop_counts_json": json.dumps(dict(stop_counts), sort_keys=True),
        "event_pnl": event_pnl,
        "logical_pnl": logical_pnl,
        "pnl_mismatch_abs": abs(event_pnl - logical_pnl),
        "top20_loss_contribution": _top_loss_contribution(losses, 20),
        "top50_loss_contribution": _top_loss_contribution(losses, 50),
        "max_single_loss": min(losses) if losses else 0.0,
        "equity_curve": equity,
        "logical_trades": logical,
        "git_commit": _git_commit(),
        "checkpoint": CHECKPOINT,
        "data_file": str(DATA_FILE),
        "oos_start": OOS_START,
        "oos_end": OOS_END,
        "mc_seed": "",
        "mc_sims": "",
        "mc_block_bars": BLOCK_BARS,
        "commission": COMMISSION,
        "slippage": SLIPPAGE,
        "script_version": SCRIPT_VERSION,
    }


def _monte_carlo(equity: np.ndarray, sims: int, seed: int) -> dict[str, Any]:
    daily_returns = _daily_returns(equity)
    if len(daily_returns) == 0:
        return {}
    rng = np.random.default_rng(seed)
    returns = np.empty(sims)
    drawdowns = np.empty(sims)
    days = len(daily_returns)
    for sim in range(sims):
        sampled = daily_returns[rng.integers(0, days, size=days)]
        path = INITIAL_CAPITAL * np.cumprod(1.0 + sampled)
        returns[sim] = path[-1] / INITIAL_CAPITAL - 1.0
        drawdowns[sim] = _max_drawdown(path)
    return {
        "mc_sims": sims,
        "mc_seed": seed,
        "mc_return_p5": float(np.percentile(returns, 5)),
        "mc_return_p50": float(np.percentile(returns, 50)),
        "mc_return_p95": float(np.percentile(returns, 95)),
        "mc_dd_p5": float(np.percentile(drawdowns, 5)),
        "mc_dd_p50": float(np.percentile(drawdowns, 50)),
        "mc_dd_p95": float(np.percentile(drawdowns, 95)),
        "mc_loss_probability": float(np.mean(returns < 0)),
        "mc_dd30_probability": float(np.mean(drawdowns < -0.30)),
    }


def _daily_returns(equity: np.ndarray) -> np.ndarray:
    usable = (len(equity) // BLOCK_BARS) * BLOCK_BARS
    if usable <= BLOCK_BARS:
        return np.array([], dtype=float)
    sampled = equity[:usable].reshape(-1, BLOCK_BARS)
    start = sampled[:, 0]
    end = sampled[:, -1]
    return end / start - 1.0


def _load_oos_data() -> pd.DataFrame:
    if not DATA_FILE.exists():
        raise FileNotFoundError(DATA_FILE)
    df = pd.read_parquet(DATA_FILE)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "datetime" in df.columns:
            df.index = pd.to_datetime(df["datetime"])
        elif "timestamp" in df.columns:
            df.index = pd.to_datetime(df["timestamp"], unit="ms")
        else:
            df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df_oos = df[(df.index >= OOS_START) & (df.index <= OOS_END)].copy()
    if df_oos.empty:
        raise RuntimeError(f"OOS slice is empty: {OOS_START} to {OOS_END}")
    return df_oos


def _add_lagged_donchian(df: pd.DataFrame) -> None:
    df["donchian_upper_lagged"] = df["high"].rolling(375, min_periods=1).max().shift(1)
    df["donchian_upper_lagged"] = df["donchian_upper_lagged"].ffill()


def _final_report(stage_a: list[dict], stage_b: list[dict], stage_c: list[dict], mc_sims: int) -> str:
    lines = [
        "# Risk Overlay Final Report",
        "",
        f"Generated: {datetime.now().isoformat()}",
        f"Checkpoint: {CHECKPOINT}",
        f"Data: {DATA_FILE}",
        f"OOS: {OOS_START} to {OOS_END}",
        f"Git commit: {_git_commit()}",
        f"MC simulations: {mc_sims}",
        "",
        f"Stage A rows: {len(stage_a)}",
        f"Stage B rows: {len(stage_b)}",
        f"Stage C rows: {len(stage_c)}",
        "",
        "## Stage C",
        "",
        "| Config | Return | MaxDD | MC DD<-30% | MC loss |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in stage_c:
        lines.append(
            f"| {row['config_id']} | {row['total_return']:.1%} | {row['max_drawdown']:.1%} | "
            f"{row.get('mc_dd30_probability', 0):.1%} | {row.get('mc_loss_probability', 0):.1%} |"
        )
    lines.extend(
        [
            "",
            "## Tail Loss",
            "",
            "| Config | Top 20 loss share | Top 50 loss share |",
            "|---|---:|---:|",
        ]
    )
    for row in stage_c:
        lines.append(
            f"| {row['config_id']} | {row['top20_loss_contribution']:.1%} | "
            f"{row['top50_loss_contribution']:.1%} |"
        )
    lines.extend(
        [
            "",
            "## Damaged Winners",
            "",
            "| Config | Damaged / Big Winners | Share |",
            "|---|---:|---:|",
        ]
    )
    for row in stage_c:
        lines.append(
            f"| {row['config_id']} | {row.get('damaged_winner_count', 0)} / "
            f"{row.get('baseline_big_winner_count', 0)} | "
            f"{row.get('damaged_winner_share', 0):.1%} |"
        )
    eligible = [
        row
        for row in stage_c
        if row.get("mc_dd30_probability", 1.0) < 0.15
        and row.get("mc_loss_probability", 1.0) < 0.05
        and row.get("damaged_winner_share", 1.0) < 0.15
    ]
    recommendation = max(eligible, key=lambda row: row["total_return"])["config_id"] if eligible else "none"
    lines.extend(
        [
            "",
            f"Recommendation: {recommendation}.",
            "Promotion still requires human review of Gate 0/1 assumptions before production use.",
            "",
        ]
    )
    return "\n".join(lines)


def _damaged_winner_stats(
    baseline_logical: list[dict[str, Any]],
    overlay_logical: list[dict[str, Any]],
) -> dict[str, Any]:
    winners = [trade for trade in baseline_logical if float(trade["total_pnl"]) > 0]
    if not winners:
        return {
            "baseline_big_winner_count": 0,
            "damaged_winner_count": 0,
            "damaged_winner_share": 0.0,
        }
    cutoff = float(np.percentile([float(trade["total_pnl"]) for trade in winners], 80))
    big_winners = [trade for trade in winners if float(trade["total_pnl"]) >= cutoff]
    overlay_by_key = {_trade_key(trade): trade for trade in overlay_logical}
    damaged = 0
    for baseline_trade in big_winners:
        overlay_trade = overlay_by_key.get(_trade_key(baseline_trade))
        overlay_pnl = float(overlay_trade["total_pnl"]) if overlay_trade else 0.0
        baseline_pnl = float(baseline_trade["total_pnl"])
        if overlay_pnl <= 0 or overlay_pnl < 0.5 * baseline_pnl:
            damaged += 1
    return {
        "baseline_big_winner_count": len(big_winners),
        "damaged_winner_count": damaged,
        "damaged_winner_share": damaged / len(big_winners) if big_winners else 0.0,
    }


def _trade_key(trade: dict[str, Any]) -> tuple[int, str | None]:
    return int(trade["entry_step"]), trade.get("side")


def _public_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    private = {"equity_curve", "logical_trades"}
    return [{k: v for k, v in row.items() if k not in private} for row in rows]


def _best_stage_b_by_family(stage_b: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for row in stage_b:
        family = row["overlay_family"]
        old = best.get(family)
        if old is None or (
            row.get("mc_dd30_probability", 1.0),
            -row["total_return"],
        ) < (
            old.get("mc_dd30_probability", 1.0),
            -old["total_return"],
        ):
            best[family] = row
    return best


def _merge_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    for key, value in overlay.items():
        if key == "base_size":
            continue
        base[key] = copy.deepcopy(value)


def _ratio_drop(value: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return max(0.0, (baseline - value) / baseline)


def _drawdown_improvement(value: float, baseline: float) -> float:
    baseline_abs = abs(baseline)
    if baseline_abs <= 0:
        return 0.0
    return (baseline_abs - abs(value)) / baseline_abs


def _top_loss_contribution(losses: list[float], n: int) -> float:
    total = abs(sum(losses))
    return abs(sum(losses[:n])) / total if total else 0.0


def _max_drawdown(equity: np.ndarray) -> float:
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        max_dd = min(max_dd, (value - peak) / peak)
    return float(max_dd)


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
