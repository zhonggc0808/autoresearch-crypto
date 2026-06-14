#!/usr/bin/env python3
"""Research Oracle — fixed evaluation harness for strategy experiments.

Phase 2 minimal implementation. Read-only: never modifies checkpoints, live
code, or data. Outputs structured metrics for human and LLM consumption.

Usage:
    # Evaluate v2.1 frozen baseline
    uv run python scripts/research_oracle.py \\
        --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt

    # Evaluate a standard checkpoint
    uv run python scripts/research_oracle.py \\
        --checkpoint checkpoints/eth_optimal.pt

    # Evaluate a candidate YAML spec (Phase 3+)
    uv run python scripts/research_oracle.py \\
        --candidate research_workspace/candidates/exp_0001.yaml

Outputs:
    oracle_report.json                — full structured output
    research_workspace/results.tsv    — appended one-line summary
    research_workspace/experiments.jsonl — appended machine-readable record
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch

# Ensure project root is on sys.path
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.checkpoints import STRATEGY_ALIASES, load_checkpoint
from dex.config import (
    BARS_PER_DAY_5M,
    BARS_PER_YEAR,
    COMMISSION,
    INITIAL_CAPITAL,
    SLIPPAGE,
)
from dex.data import list_crypto_files, load_crypto_data
from dex.drawdown_guard import apply_drawdown_guard
from dex.indicators import compute_adx
from dex.regime_filter import (
    apply_regime_short_filter,
    build_daily_regime_labels,
)
from dex.regime_permissions import (
    RiskOffConfig,
    apply_permission_arrays,
    build_permission_arrays,
    compute_daily_indicators,
    route_regime_signals,
)
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategy_signals import generate_strategy_signals

# ---------------------------------------------------------------------------
# Fixed oracle configuration (Phase 2)
# ---------------------------------------------------------------------------
SYMBOL = "ETHUSDT"
INTERVAL = "5m"
SPLIT_RATIO = 0.70
ROLLING_WINDOW_MONTHS = [6, 12]
BARS_PER_MONTH = BARS_PER_DAY_5M * 30
FEE_LEVELS_BPS = [0, 2, 4, 10]      # basis points
SLIPPAGE_LEVELS_BPS = [0, 2, 5]
OUTPUT_DIR = PROJECT_DIR / "research_workspace"
REPORT_PATH = OUTPUT_DIR / "oracle_report.json"
TSV_PATH = OUTPUT_DIR / "results.tsv"
JSONL_PATH = OUTPUT_DIR / "experiments.jsonl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_short(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:7]


def _file_hash(path: Path) -> str:
    """SHA256 of file contents."""
    if not path.exists():
        return "sha256:FILE_NOT_FOUND"
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _checkpoint_hash(checkpoint: Dict[str, Any]) -> str:
    """Deterministic hash of checkpoint params (JSON-encoded, sorted keys)."""
    params_str = json.dumps(checkpoint, sort_keys=True, default=str)
    return "sha256:" + _sha256_short(params_str.encode())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _safe_first_datetime(df: pd.DataFrame) -> str:
    """Extract first datetime from a DataFrame, handling various column names."""
    for col in ["datetime", "timestamp", "date"]:
        if col in df.columns:
            val = df[col].iloc[0]
            return str(pd.Timestamp(val))
    # Try index
    if isinstance(df.index, pd.DatetimeIndex):
        return str(df.index[0])
    return "unknown"


def _safe_last_datetime(df: pd.DataFrame) -> str:
    """Extract last datetime from a DataFrame, handling various column names."""
    for col in ["datetime", "timestamp", "date"]:
        if col in df.columns:
            val = df[col].iloc[-1]
            return str(pd.Timestamp(val))
    if isinstance(df.index, pd.DatetimeIndex):
        return str(df.index[-1])
    return "unknown"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _find_eth_data() -> Path:
    """Find the ETHUSDT 5m data file, preferring 1300d (v2.1 eval period).

    Scans the data directory directly (not via list_crypto_files which
    deduplicates to only the longest file per symbol/interval).
    """
    from dex.config import DATA_DIR
    data_dir = Path(DATA_DIR)
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    all_parquet = list(data_dir.glob("*.parquet"))
    eth_5m = [
        p for p in all_parquet
        if p.name.startswith("ETHUSDT") and "5m" in p.name
    ]
    if not eth_5m:
        raise FileNotFoundError(
            "No ETHUSDT 5m data files found in %s. "
            "Run: uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 1300"
            % data_dir
        )
    # Priority: 1300d > 730d > 365d > any non-2600d > largest
    for tag in ["1300d", "730d", "365d"]:
        for pf in eth_5m:
            if tag in pf.name:
                return pf
    non_2600 = [p for p in eth_5m if "2600d" not in p.name]
    if non_2600:
        return max(non_2600, key=lambda p: p.stat().st_size)
    return max(eth_5m, key=lambda p: p.stat().st_size)


def _load_and_split_data(data_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, int]:
    """Load data and split into IS/OOS at fix ed ratio."""
    df = (
        load_crypto_data(str(data_path))
        .sort_values("timestamp")
        .drop_duplicates(subset="timestamp")
        .reset_index(drop=True)
    )
    split_idx = int(len(df) * SPLIT_RATIO)
    df_is = df.iloc[:split_idx].reset_index(drop=True)
    df_oos = df.iloc[split_idx:].reset_index(drop=True)
    return df_is, df_oos, split_idx


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def _generate_raw_signals(strategy, df: pd.DataFrame) -> np.ndarray:
    """Generate raw signals from a single strategy instance."""
    enable_short = getattr(strategy, "enable_short", True)
    return generate_strategy_signals(strategy, df, enable_short=enable_short)


def _generate_v21_signals(checkpoint: Dict[str, Any], df: pd.DataFrame) -> np.ndarray:
    """Replay the v2.1 regime-permission signal pipeline.

    This reconstructs the multi-regime channel breakout with permission
    overlays exactly as used by v2.1 balanced.
    """
    bull_s = ChannelBreakoutTrendStrategy(**checkpoint["bull"]["strategy_params"])
    bear_s = ChannelBreakoutTrendStrategy(**checkpoint["bear"]["strategy_params"])
    neutral_s = ChannelBreakoutTrendStrategy(**checkpoint["neutral"]["strategy_params"])

    bull_cfg = RiskOffConfig(**checkpoint["bull"]["permission"])
    bear_cfg = RiskOffConfig(**checkpoint["bear"]["permission"])
    neutral_cfg = RiskOffConfig(**checkpoint["neutral"]["permission"])

    policy = checkpoint.get("regime_change_policy", "permission_based")

    bull_raw = _generate_raw_signals(bull_s, df)
    bear_raw = _generate_raw_signals(bear_s, df)
    neutral_raw = _generate_raw_signals(neutral_s, df)

    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    adx_full, _, _ = compute_adx(df, 14)
    daily_ctx = compute_daily_indicators(df)

    routed = route_regime_signals(
        bull_raw, bear_raw, neutral_raw, regimes, regime_change_policy=policy
    )
    al, as_arr, ff, eo = build_permission_arrays(
        df, regimes, bull_cfg, bear_cfg, neutral_cfg, daily_ctx, adx_full
    )
    final_signals = apply_permission_arrays(routed, al, as_arr, ff, eo)
    return final_signals


def _safe_execution_signals(signals: np.ndarray) -> np.ndarray:
    """Convert same-bar reversals to close→confirm→open semantics.

    If position is long and signal says SHORT(3) → output CLOSE(0) this bar.
    The actual reversal happens only if the signal repeats next bar.
    """
    out = signals.copy()
    position = 0
    for i in range(len(out)):
        raw = out[i]
        if raw == 2:  # long
            if position == -1:
                out[i] = 0
                position = 0
            else:
                position = 1
        elif raw == 3:  # short
            if position == 1:
                out[i] = 0
                position = 0
            else:
                position = -1
        elif raw == 0:
            position = 0
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _evaluate_signals(
    signals: np.ndarray,
    prices: np.ndarray,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
) -> Dict[str, Any]:
    """Evaluate a signal array and return metrics dict."""
    ev = StrategyEvaluator(
        initial_capital=INITIAL_CAPITAL,
        commission=commission,
        slippage=slippage,
    )
    score, metrics, trade_log = ev.evaluate(signals, prices)
    equity, trades = ev.simulate(signals, prices)

    n_bars = len(signals)
    years = n_bars / BARS_PER_YEAR if n_bars > 0 else 0.01

    trade_pnls = [t for t in trades if t.get("pnl") is not None]
    n_trades = len(trade_pnls)
    tpy = n_trades / years if years > 0 else 0

    return {
        "return": float(metrics.get("total_return", 0)),
        "dd": float(metrics.get("max_drawdown", 0)),
        "sharpe": float(metrics.get("sharpe_ratio", 0)),
        "win_rate": float(metrics.get("win_rate", 0)),
        "annual_return": float(metrics.get("annualized_return", 0)),
        "annual_vol": float(metrics.get("annualized_vol", 0)),
        "trades": n_trades,
        "trades_per_year": round(tpy, 1),
        "score": float(score),
        "n_bars": n_bars,
    }


def _compute_rolling_metrics(
    signals: np.ndarray,
    prices: np.ndarray,
    window_months: List[int],
) -> Dict[str, Any]:
    """Compute minimum metrics across rolling windows."""
    result = {}
    for months in window_months:
        window_bars = months * BARS_PER_MONTH
        if window_bars >= len(signals) // 2:
            # Window too large relative to data
            result[f"{months}m_min_return"] = None
            result[f"{months}m_min_sharpe"] = None
            continue

        step = window_bars // 2  # 50% overlap
        returns = []
        sharpes = []

        for start in range(0, len(signals) - window_bars, step):
            end = start + window_bars
            win_signals = signals[start:end]
            win_prices = prices[start:end]
            ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
            _, metrics, _ = ev.evaluate(win_signals, win_prices)
            returns.append(metrics.get("total_return", 0))
            sharpes.append(metrics.get("sharpe_ratio", 0))

        if returns:
            result[f"{months}m_min_return"] = round(float(np.min(returns)), 4)
            result[f"{months}m_min_sharpe"] = round(float(np.min(sharpes)), 4)
        else:
            result[f"{months}m_min_return"] = None
            result[f"{months}m_min_sharpe"] = None

    return result


def _compute_regime_breakdown(
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
) -> Dict[str, Any]:
    """Compute per-regime metrics."""
    regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
    breakdown = {}
    for label in ["BULL", "BEAR", "NEUTRAL"]:
        mask = regimes == label
        if not mask.any():
            breakdown[label.lower()] = {"return": 0.0, "trades": 0, "bars": 0}
            continue

        regime_signals = np.where(mask, signals, 1)  # Hold outside regime
        ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
        _, metrics, trade_log = ev.evaluate(regime_signals, prices)
        trade_pnls = [t for t in trade_log if t.get("pnl") is not None]

        breakdown[label.lower()] = {
            "return": round(float(metrics.get("total_return", 0)), 4),
            "trades": len(trade_pnls),
            "bars": int(mask.sum()),
        }
    return breakdown


def _compute_fee_sensitivity(
    signals: np.ndarray,
    prices: np.ndarray,
) -> Dict[str, Any]:
    """Return at different fee levels."""
    result = {}
    for bps in FEE_LEVELS_BPS:
        fee = bps / 10000.0
        ev = StrategyEvaluator(commission=fee, slippage=SLIPPAGE)
        _, metrics, _ = ev.evaluate(signals, prices)
        result[f"{bps}bp"] = round(float(metrics.get("total_return", 0)), 4)
    return result


def _compute_slippage_sensitivity(
    signals: np.ndarray,
    prices: np.ndarray,
) -> Dict[str, Any]:
    """Return at different slippage levels."""
    result = {}
    for bps in SLIPPAGE_LEVELS_BPS:
        slip = bps / 10000.0
        ev = StrategyEvaluator(commission=COMMISSION, slippage=slip)
        _, metrics, _ = ev.evaluate(signals, prices)
        result[f"{bps}bp"] = round(float(metrics.get("total_return", 0)), 4)
    return result


def _compute_execution_parity(
    raw_signals: np.ndarray,
    safe_signals: np.ndarray,
    prices: np.ndarray,
) -> float:
    """Correlation of equity curves: raw vs safe-execution."""
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    raw_equity, _ = ev.simulate(raw_signals, prices)
    safe_equity, _ = ev.simulate(safe_signals, prices)
    min_len = min(len(raw_equity), len(safe_equity))
    if min_len < 2:
        return 0.0
    raw_returns = np.diff(raw_equity[:min_len]) / (raw_equity[:min_len - 1] + 1e-12)
    safe_returns = np.diff(safe_equity[:min_len]) / (safe_equity[:min_len - 1] + 1e-12)
    corr = np.corrcoef(raw_returns, safe_returns)[0, 1]
    return round(float(0.0 if np.isnan(corr) else corr), 4)


# ---------------------------------------------------------------------------
# Disqualification flags
# ---------------------------------------------------------------------------

def _compute_flags(
    is_metrics: Dict[str, Any],
    oos_metrics: Dict[str, Any],
    rolling: Dict[str, Any],
    fee_sens: Dict[str, Any],
    execution_parity: float,
    corr_vs_v21: Optional[float],
) -> Dict[str, Any]:
    """Apply disqualification rules and return flags dict."""
    disqualifications = []
    warnings = []

    # Auto-reject gates
    if is_metrics["dd"] < -0.50:
        disqualifications.append("DD_OVER_50")
    if rolling.get("6m_min_return") is not None and rolling["6m_min_return"] < 0:
        disqualifications.append("ROLLING_NEGATIVE")

    # Warning gates
    if is_metrics["dd"] < -0.40:
        warnings.append("DD_OVER_40")
    if is_metrics["trades"] < 30:
        warnings.append("TRADES_UNDER_30")
    if oos_metrics["return"] < is_metrics["return"] * 0.5:
        warnings.append("OOS_DEGRADE")
    if execution_parity < 0.85:
        warnings.append("EXEC_PARITY_LOW")
    if corr_vs_v21 is not None and corr_vs_v21 > 0.99:
        warnings.append("CORR_BASELINE_099")
    if fee_sens.get("10bp", 0) < 0:
        warnings.append("FEE_FRAGILE")

    status = "REJECT" if disqualifications else ("WARN" if warnings else "PASS")
    return {
        "status": status,
        "warnings": warnings,
        "disqualifications": disqualifications,
    }


# ---------------------------------------------------------------------------
# Main oracle logic
# ---------------------------------------------------------------------------

def _is_v21_checkpoint(checkpoint: Dict[str, Any]) -> bool:
    """Detect v2.1 regime-permission checkpoint format."""
    return (
        checkpoint.get("strategy_type") == "regime_permission_channel_breakout"
        and "bull" in checkpoint
        and "bear" in checkpoint
        and "neutral" in checkpoint
    )


def run_oracle(
    checkpoint_path: Optional[str] = None,
    candidate_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full oracle evaluation and return structured results.

    This is the main entry point — it is pure logic with no side effects
    except for writing output files.
    """
    if checkpoint_path:
        checkpoint = load_checkpoint(checkpoint_path)
        is_v21 = _is_v21_checkpoint(checkpoint)
        checkpoint_hash = _checkpoint_hash(checkpoint)
    elif candidate_path:
        raise NotImplementedError("Candidate YAML evaluation is Phase 3+")
    else:
        raise ValueError("Either --checkpoint or --candidate is required")

    # --- Load data ---
    data_path = _find_eth_data()
    data_hash = _file_hash(data_path)
    df_is, df_oos, split_idx = _load_and_split_data(data_path)

    # --- Generate signals ---
    if is_v21:
        signals_raw_is = _generate_v21_signals(checkpoint, df_is)
        signals_raw_oos = _generate_v21_signals(checkpoint, df_oos)
        strategy_family = "channel_breakout_v21"
    else:
        strategy = checkpoint.get("_strategy_instance")
        if strategy is None:
            from dex.checkpoints import build_strategy_from_checkpoint
            strategy = build_strategy_from_checkpoint(checkpoint)
        strategy_name = str(checkpoint.get("strategy", "unknown")).lower()
        strategy_family = strategy_name
        signals_raw_is = _generate_raw_signals(strategy, df_is)
        signals_raw_oos = _generate_raw_signals(strategy, df_oos)

    # --- Signal variants ---
    # Safe-execution (close-confirm-open)
    signals_safe_is = _safe_execution_signals(signals_raw_is)
    signals_safe_oos = _safe_execution_signals(signals_raw_oos)

    # Regime-filtered (bear-only shorts)
    regimes_is = build_daily_regime_labels(df_is, fast_days=50, slow_days=200)
    regimes_oos = build_daily_regime_labels(df_oos, fast_days=50, slow_days=200)
    signals_regime_is, _ = apply_regime_short_filter(signals_raw_is, regimes_is, df_is)
    signals_regime_oos, _ = apply_regime_short_filter(signals_raw_oos, regimes_oos, df_oos)

    # --- Evaluate IS ---
    prices_is = df_is["close"].values.astype(float)
    prices_oos = df_oos["close"].values.astype(float)

    is_raw = _evaluate_signals(signals_raw_is, prices_is)
    is_safe = _evaluate_signals(signals_safe_is, prices_is)
    is_regime = _evaluate_signals(signals_regime_is, prices_is)

    # --- Evaluate OOS ---
    oos_raw = _evaluate_signals(signals_raw_oos, prices_oos)
    oos_safe = _evaluate_signals(signals_safe_oos, prices_oos)
    oos_regime = _evaluate_signals(signals_regime_oos, prices_oos)

    # --- Rolling metrics (full data, raw signals) ---
    prices_full = np.concatenate([prices_is, prices_oos])
    signals_full = np.concatenate([signals_raw_is, signals_raw_oos])
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    rolling = _compute_rolling_metrics(signals_full, prices_full, ROLLING_WINDOW_MONTHS)

    # --- Regime breakdown ---
    regime_breakdown = _compute_regime_breakdown(signals_full, prices_full, df_full)

    # --- Fee / slippage sensitivity ---
    fee_sens = _compute_fee_sensitivity(signals_raw_is, prices_is)
    slippage_sens = _compute_slippage_sensitivity(signals_raw_is, prices_is)

    # --- vs Baseline correlation ---
    # If evaluating baseline itself, correlation is 1.0
    corr_vs_v21 = 1.0 if is_v21 else None

    # --- Execution parity ---
    execution_parity = _compute_execution_parity(signals_raw_oos, signals_safe_oos, prices_oos)

    # --- Flags ---
    flags = _compute_flags(is_raw, oos_raw, rolling, fee_sens, execution_parity, corr_vs_v21)

    # --- Build result ---
    timestamp = _now_iso()
    commit = _get_git_commit()

    result = {
        "experiment_id": f"oracle_{timestamp.replace(':', '').replace('-', '').replace('T', '_').replace('Z', '')}",
        "parent_id": None,
        "candidate_role": "standalone",
        "timestamp": timestamp,
        "strategy": strategy_family,
        "params_hash": checkpoint_hash,
        "data_hash": data_hash,
        "commit": commit,
        "data": {
            "dataset_path": str(data_path),
            "data_hash": data_hash,
            "data_start": str(_safe_first_datetime(df_is)),
            "data_end": str(_safe_last_datetime(df_oos)),
            "is_start": str(_safe_first_datetime(df_is)),
            "is_end": str(_safe_last_datetime(df_is)),
            "oos_start": str(_safe_first_datetime(df_oos)),
            "oos_end": str(_safe_last_datetime(df_oos)),
            "is_bars": len(df_is),
            "oos_bars": len(df_oos),
            "split_method": "fixed-split",
            "split_ratio": SPLIT_RATIO,
        },
        "metrics": {
            "is": {
                "raw": is_raw,
                "safe_execution": is_safe,
                "regime_permission": is_regime,
            },
            "oos": {
                "raw": oos_raw,
                "safe_execution": oos_safe,
                "regime_permission": oos_regime,
            },
            "rolling": rolling,
            "regime": regime_breakdown,
            "execution_parity": execution_parity,
            "sensitivity": {
                "fees": fee_sens,
                "slippage": slippage_sens,
            },
        },
        "flags": flags,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
    }
    return result


def _get_git_commit() -> Optional[str]:
    """Get current git commit hash (7-char short)."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=str(PROJECT_DIR),
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_oracle_report(result: Dict[str, Any]) -> None:
    """Write full JSON report."""
    _ensure_output_dir()
    REPORT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"  oracle_report: {REPORT_PATH}")


def _append_results_tsv(result: Dict[str, Any]) -> None:
    """Append one-line summary to results.tsv."""
    _ensure_output_dir()
    m = result["metrics"]
    f = result["flags"]
    is_raw = m["is"]["raw"]
    oos_raw = m["oos"]["raw"]
    oos_safe = m["oos"]["safe_execution"]

    header = (
        "timestamp\texperiment_id\tparent_id\tcandidate_role\tevent\t"
        "strategy_family\tparams_hash\tis_return\tis_dd\tis_sharpe\t"
        "safe_return\tsafe_dd\toos_return_mean\trolling_12m_min\t"
        "combined_return\tcombined_dd\ttrades_per_year\t"
        "corr_vs_v21\toracle_score\tstatus\tdisqualifications\t"
        "decision\tnote\n"
    )

    rolling_12m = m["rolling"].get("12m_min_return", "N/A")
    if rolling_12m is not None:
        rolling_12m = round(rolling_12m, 4)

    disqual = "|".join(f["disqualifications"]) if f["disqualifications"] else ""

    row = (
        f"{result['timestamp']}\t{result['experiment_id']}\t{result['parent_id'] or 'null'}\t"
        f"{result['candidate_role']}\tevaluate\t{result['strategy']}\t"
        f"{result['params_hash']}\t"
        f"{is_raw['return']:.4f}\t{is_raw['dd']:.4f}\t{is_raw['sharpe']:.4f}\t"
        f"{oos_safe['return']:.4f}\t{oos_safe['dd']:.4f}\t"
        f"{oos_raw['return']:.4f}\t{rolling_12m}\t"
        f"N/A\tN/A\t"
        f"{is_raw['trades_per_year']}\t"
        f"{result.get('corr_vs_v21') or 'N/A'}\t"
        f"{is_raw['score']:.4f}\t{f['status']}\t{disqual}\t"
        f"baseline_check\t"
        f"oracle run for {result['strategy']}\n"
    )

    write_header = not TSV_PATH.exists()
    with open(TSV_PATH, "a", encoding="utf-8", newline="") as fh:
        if write_header:
            fh.write(header)
        fh.write(row)

    print(f"  results.tsv: appended row ({TSV_PATH})")


def _append_experiments_jsonl(result: Dict[str, Any]) -> None:
    """Append full machine-readable record to experiments.jsonl."""
    _ensure_output_dir()
    record = {k: v for k, v in result.items()}
    # Convert any non-serializable types
    with open(JSONL_PATH, "a", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    print(f"  experiments.jsonl: appended record ({JSONL_PATH})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Research Oracle — fixed evaluation harness (Phase 2)"
    )
    parser.add_argument(
        "--checkpoint", type=str, default=None,
        help="Path to a .pt checkpoint file.",
    )
    parser.add_argument(
        "--candidate", type=str, default=None,
        help="Path to a YAML candidate spec (Phase 3+).",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Do not write output files (dry-run).",
    )
    args = parser.parse_args()

    if not args.checkpoint and not args.candidate:
        parser.error("Either --checkpoint or --candidate is required.")

    print(f"=== Research Oracle (Phase 2) ===")
    print(f"  Mode: {'checkpoint' if args.checkpoint else 'candidate'}")
    if args.checkpoint:
        print(f"  Checkpoint: {args.checkpoint}")
    print(f"  Symbol: {SYMBOL} / {INTERVAL}")
    print(f"  Split: {int(SPLIT_RATIO*100)}/{int((1-SPLIT_RATIO)*100)} IS/OOS")
    print()

    t0 = time.time()
    result = run_oracle(
        checkpoint_path=args.checkpoint,
        candidate_path=args.candidate,
    )
    elapsed = time.time() - t0

    # --- Print summary ---
    m = result["metrics"]
    f = result["flags"]
    print("--- IS (raw) ---")
    for k, v in m["is"]["raw"].items():
        print(f"  {k}: {v}")
    print()
    print("--- OOS (raw) ---")
    for k, v in m["oos"]["raw"].items():
        print(f"  {k}: {v}")
    print()
    print("--- OOS (safe-execution) ---")
    for k, v in m["oos"]["safe_execution"].items():
        print(f"  {k}: {v}")
    print()
    print(f"--- Rolling ---")
    for k, v in m["rolling"].items():
        print(f"  {k}: {v}")
    print()
    print(f"--- Regime ---")
    for regime, data in m["regime"].items():
        print(f"  {regime}: return={data['return']}, trades={data['trades']}, bars={data['bars']}")
    print()
    print(f"--- Sensitivity ---")
    print(f"  fees: {m['sensitivity']['fees']}")
    print(f"  slippage: {m['sensitivity']['slippage']}")
    print()
    print(f"--- Flags ---")
    print(f"  status: {f['status']}")
    print(f"  warnings: {f['warnings']}")
    print(f"  disqualifications: {f['disqualifications']}")
    print()
    print(f"  Execution parity: {m['execution_parity']}")
    print(f"  Elapsed: {elapsed:.1f}s")

    # --- Write output ---
    if not args.no_write:
        print()
        _write_oracle_report(result)
        _append_results_tsv(result)
        _append_experiments_jsonl(result)
    else:
        print("\n  (--no-write: skipping output files)")

    print("\nDone.")
    return 0 if f["status"] != "REJECT" else 1


if __name__ == "__main__":
    sys.exit(main())
