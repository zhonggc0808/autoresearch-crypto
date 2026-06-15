#!/usr/bin/env python3
"""Research Oracle — fixed evaluation harness for strategy experiments.

Phase 2 minimal implementation. Read-only: never modifies checkpoints, live
code, or data. Outputs structured metrics for human and LLM consumption.

Usage:
    # Evaluate v2.1 frozen baseline (from committed JSON params)
    uv run python scripts/research_oracle.py --baseline

    # Evaluate a checkpoint
    uv run python scripts/research_oracle.py \\
        --checkpoint checkpoints/eth_optimal.pt

    # Evaluate a candidate params JSON (Phase 3 read-only loop)
    uv run python scripts/research_oracle.py \\
        --candidate research_workspace/candidates/exp_0001.json

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
from dex.filters import build_filter_from_config
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
from dex.strategy_signals import generate_strategy_signals

# ---------------------------------------------------------------------------
# Fixed oracle configuration (Phase 2 freeze — v0.1.0)
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
BASELINE_DIR = OUTPUT_DIR / "baselines"

# Oracle version — increment when evaluation logic changes
ORACLE_VERSION = "v0.2"
BASELINE_ID = "channel_breakout_v2_1_balanced"
FROZEN_BASELINE_PARAMS = BASELINE_DIR / f"{BASELINE_ID}_params.json"
SPLIT_ID = f"{SYMBOL}_{INTERVAL}_1300d_{int(SPLIT_RATIO*100)}_{int((1-SPLIT_RATIO)*100)}_full_warmup"

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


def _ensure_baseline_dir() -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)


def _safe_first_datetime(df: pd.DataFrame) -> str:
    """Extract first datetime from a DataFrame, handling various column names."""
    for col in ["datetime", "timestamp", "date"]:
        if col in df.columns:
            val = df[col].iloc[0]
            return str(pd.Timestamp(val))
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
# Baseline loading (from frozen JSON params, not .pt)
# ---------------------------------------------------------------------------

def _load_baseline_params() -> Dict[str, Any]:
    """Load frozen baseline params from committed JSON.

    The JSON is checked into git (unlike .pt checkpoints which are gitignored).
    This ensures oracle is reproducible on a fresh clone.
    """
    if not FROZEN_BASELINE_PARAMS.exists():
        raise FileNotFoundError(
            f"Frozen baseline params not found: {FROZEN_BASELINE_PARAMS}\n"
            f"Run: uv run python scripts/research_oracle.py --checkpoint "
            f"checkpoints/channel_breakout_v2_1_balanced.pt"
        )
    with open(FROZEN_BASELINE_PARAMS, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _find_eth_data() -> Path:
    """Find the ETHUSDT 5m 1300d data file (v0.1.0 freeze: fail fast).

    Scans the data directory directly. In freeze mode (v0.1.0), only 1300d
    is accepted — no fallback to shorter data.
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
    # v0.1.0 freeze: MUST be 1300d. No fallback.
    for pf in eth_5m:
        if "1300d" in pf.name:
            return pf
    available = ", ".join(p.name for p in eth_5m)
    raise FileNotFoundError(
        "ETHUSDT_5m_1300d.parquet not found in %s. "
        "Required for oracle v0.1.0 freeze. "
        "Available files: %s. "
        "Run: uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 1300"
        % (data_dir, available)
    )


def _load_and_split_data(data_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, int]:
    """Load data and split into IS/OOS at fixed ratio."""
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


def _generate_v21_signals(
    checkpoint: Dict[str, Any],
    df: pd.DataFrame,
    fast_days: int = 50,
    slow_days: int = 200,
) -> np.ndarray:
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

    regimes = build_daily_regime_labels(df, fast_days=fast_days, slow_days=slow_days)
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

    n_bars = len(signals)
    years = n_bars / BARS_PER_YEAR if n_bars > 0 else 0.01

    trade_pnls = [t for t in trade_log if t.get("pnl") is not None]
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
    regimes: Optional[np.ndarray] = None,
    df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Compute minimum metrics across rolling windows.

    Uses pre-computed regimes (from full data) and slices them alongside
    signals/prices. This avoids EMA warmup issues when the rolling window
    is shorter than the EMA slow period (200 days).
    """
    result = {}
    for months in window_months:
        window_bars = months * BARS_PER_MONTH
        key = f"{months}m"
        if window_bars >= len(signals) // 2:
            result[f"{key}_min_return"] = None
            result[f"{key}_min_sharpe"] = None
            result[f"{key}_worst_regime"] = None
            result[f"{key}_worst_time"] = None
            continue

        step = window_bars // 2
        worst_return = float("inf")
        worst_sharpe = float("inf")
        worst_regime = None
        worst_time = None

        for start in range(0, len(signals) - window_bars, step):
            end = start + window_bars
            win_signals = signals[start:end]
            win_prices = prices[start:end]
            ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
            _, metrics, _ = ev.evaluate(win_signals, win_prices)
            ret = metrics.get("total_return", 0)
            sh = metrics.get("sharpe_ratio", 0)

            if ret < worst_return:
                worst_return = ret
                worst_sharpe = sh
                # Regime attribution from pre-computed labels (sliced, not recomputed)
                if regimes is not None and len(regimes) > end:
                    win_regimes = regimes[start:end]
                    bull_pct = float((win_regimes == "BULL").mean())
                    bear_pct = float((win_regimes == "BEAR").mean())
                    neutral_pct = float((win_regimes == "NEUTRAL").mean())
                    dom = max(
                        [("BULL", bull_pct), ("BEAR", bear_pct), ("NEUTRAL", neutral_pct)],
                        key=lambda x: x[1],
                    )
                    worst_regime = {
                        "dominant": dom[0],
                        "bull_pct": round(bull_pct, 3),
                        "bear_pct": round(bear_pct, 3),
                        "neutral_pct": round(neutral_pct, 3),
                    }
                worst_time = start  # bar index

        # Convert worst bar index to date string
        worst_time_date = None
        if worst_time is not None and df is not None and worst_time < len(df):
            dt_val = _safe_first_datetime(df.iloc[max(0, worst_time - 1):worst_time + 1])
            worst_time_date = dt_val if dt_val != "unknown" else str(worst_time)

        result[f"{key}_min_return"] = round(float(worst_return), 4)
        result[f"{key}_min_sharpe"] = round(float(worst_sharpe), 4)
        result[f"{key}_worst_regime"] = worst_regime
        result[f"{key}_worst_bar"] = worst_time
        result[f"{key}_worst_time"] = worst_time_date

    return result


def _compute_regime_breakdown(
    signals: np.ndarray,
    prices: np.ndarray,
    regimes: np.ndarray,
) -> Dict[str, Any]:
    """Compute per-regime metrics using pre-computed regime labels.

    NOTE: Regime PnL attribution is signal-isolated, not PnL-attributed. A
    position opened in BULL and held into NEUTRAL will have its NEUTRAL-period
    PnL reported under BULL. This is a documented approximation — use regime
    returns for directional comparison, not for precise decomposition.
    """
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


def _compute_equity_correlation(
    signals_a: np.ndarray,
    signals_b: np.ndarray,
    prices: np.ndarray,
) -> float:
    """Correlation of equity curves between two signal sets."""
    ev = StrategyEvaluator(commission=COMMISSION, slippage=SLIPPAGE)
    eq_a, _ = ev.simulate(signals_a, prices)
    eq_b, _ = ev.simulate(signals_b, prices)
    min_len = min(len(eq_a), len(eq_b))
    if min_len < 2:
        return 0.0
    ret_a = np.diff(eq_a[:min_len]) / (eq_a[:min_len - 1] + 1e-12)
    ret_b = np.diff(eq_b[:min_len]) / (eq_b[:min_len - 1] + 1e-12)
    corr = np.corrcoef(ret_a, ret_b)[0, 1]
    return round(float(0.0 if np.isnan(corr) else corr), 4)


# ---------------------------------------------------------------------------
# Disqualification flags
# ---------------------------------------------------------------------------

def _compute_flags(
    is_metrics: Dict[str, Any],
    oos_metrics: Dict[str, Any],
    rolling: Dict[str, Any],
    fee_sens: Dict[str, Any],
    fee_sens_oos: Dict[str, Any],
    execution_parity: float,
    correlation: Optional[Dict[str, Any]],
    is_baseline: bool = False,
) -> Dict[str, Any]:
    """Apply disqualification rules and return flags dict.

    When ``is_baseline=True``, known risks (DD_OVER_50, ROLLING_NEGATIVE) are
    recorded as ``baseline_known_risks`` instead of disqualifications — the
    baseline cannot be disqualified by the oracle it anchors.
    """
    disqualifications = []
    warnings = []
    baseline_known_risks = []

    # Auto-reject gates (check both IS and OOS)
    has_is_dd50 = is_metrics["dd"] < -0.50
    has_oos_dd50 = oos_metrics["dd"] < -0.50
    has_dd50 = has_is_dd50 or has_oos_dd50

    has_rolling_neg = (
        rolling.get("6m_min_return") is not None and rolling["6m_min_return"] < 0
    )

    if is_baseline:
        if has_is_dd50:
            baseline_known_risks.append("IS_DD_OVER_50")
        if has_oos_dd50:
            baseline_known_risks.append("OOS_DD_OVER_50")
        if has_rolling_neg:
            baseline_known_risks.append("ROLLING_NEGATIVE_IN_WINDOW")
    else:
        if has_dd50:
            disqualifications.append("DD_OVER_50")
        if has_rolling_neg:
            disqualifications.append("ROLLING_NEGATIVE")

    # Warning gates (apply to both baseline and candidates)
    if is_metrics["dd"] < -0.40:
        warnings.append("DD_OVER_40")
    if is_metrics["trades"] < 30:
        warnings.append("TRADES_UNDER_30")
    if oos_metrics["return"] < is_metrics["return"] * 0.5:
        warnings.append("OOS_DEGRADE")
    if execution_parity < 0.85:
        warnings.append("EXEC_PARITY_LOW")
    if correlation is not None and correlation.get("vs_baseline", 0) >= 0.99:
        warnings.append("CORR_BASELINE_099")
    # FEE_FRAGILE: check OOS first, then IS
    oos_10bp = fee_sens_oos.get("10bp", 0)
    is_10bp = fee_sens.get("10bp", 0)
    if oos_10bp < 0 or is_10bp < 0:
        warnings.append("FEE_FRAGILE")

    if is_baseline:
        status = "BASELINE"
    else:
        status = "REJECT" if disqualifications else ("WARN" if warnings else "PASS")

    result = {
        "status": status,
        "warnings": warnings,
        "disqualifications": disqualifications,
    }
    if baseline_known_risks:
        result["baseline_known_risks"] = baseline_known_risks
    return result


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
    use_baseline: bool = False,
    fast_days: int = 50,
    slow_days: int = 200,
) -> Dict[str, Any]:
    """Run the full oracle evaluation and return structured results.

    This is the main entry point — it is pure logic with no side effects
    except for writing output files.
    """
    _filter_config = None  # Phase 3B candidate-selectable filter (baseline=disabled)
    if use_baseline:
        # Load from frozen JSON params (committed to git)
        checkpoint = _load_baseline_params()
        is_v21 = True
        checkpoint_hash = _checkpoint_hash(checkpoint)
        _is_frozen_baseline = True
    elif checkpoint_path:
        checkpoint = load_checkpoint(checkpoint_path)
        is_v21 = _is_v21_checkpoint(checkpoint)
        # Hash of the raw .pt file for tracking
        checkpoint_hash = _file_hash(Path(checkpoint_path))
        # Compare frozen baseline using only param keys (strip eval artifacts)
        if is_v21 and FROZEN_BASELINE_PARAMS.exists():
            param_keys = {"strategy_type", "version", "variant",
                          "regime_change_policy", "regime_filter",
                          "bull", "bear", "neutral"}
            ckpt_params = {k: v for k, v in checkpoint.items() if k in param_keys}
            baseline_params = _load_baseline_params()
            # Both should be dicts with same keys after filtering
            _is_frozen_baseline = _checkpoint_hash(ckpt_params) == _checkpoint_hash(baseline_params)
        else:
            _is_frozen_baseline = False
    elif candidate_path:
        # Phase 3 read-only candidate evaluation.
        # Load candidate params JSON (same format as frozen baseline JSON).
        # Supports both standalone and filter/overlay roles.
        cpath = Path(candidate_path)
        if not cpath.exists():
            raise FileNotFoundError(f"Candidate file not found: {candidate_path}")
        raw = json.loads(cpath.read_text(encoding="utf-8"))
        # Support both nested (with "params" key) and flat formats
        checkpoint = raw.get("params") if isinstance(raw, dict) and "params" in raw else raw
        is_v21 = _is_v21_checkpoint(checkpoint)
        checkpoint_hash = _file_hash(cpath)
        _is_frozen_baseline = False
        # Extract optional filter config (Phase 3B)
        _filter_config = raw.get("filter") if isinstance(raw, dict) else None
    else:
        raise ValueError("Either --checkpoint, --baseline, or --candidate is required")

    # --- Load data ---
    data_path = _find_eth_data()
    data_hash = _file_hash(data_path)
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)

    # --- Generate signals on FULL data first (for regime pre-compute) ---
    if is_v21:
        signals_raw_full = _generate_v21_signals(checkpoint, df_full, fast_days, slow_days)
        strategy_family = "channel_breakout_v21"
    else:
        strategy = checkpoint.get("_strategy_instance")
        if strategy is None:
            from dex.checkpoints import build_strategy_from_checkpoint
            strategy = build_strategy_from_checkpoint(checkpoint)
        strategy_name = str(checkpoint.get("strategy", "unknown")).lower()
        strategy_family = strategy_name
        signals_raw_full = _generate_raw_signals(strategy, df_full)

    # Split signals for IS/OOS
    signals_raw_is = signals_raw_full[:split_idx]
    signals_raw_oos = signals_raw_full[split_idx:]

    # --- Phase 3B: candidate-selectable filter ---
    if _filter_config is not None:
        from dex.indicators import compute_adx
        adx_full, _, _ = compute_adx(df_full, 14)
        regimes_full = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
        filter_fn = build_filter_from_config(_filter_config, adx_full, regimes_full, df=df_full)
        signals_raw_full = filter_fn(signals_raw_full)
        signals_raw_is = signals_raw_full[:split_idx]
        signals_raw_oos = signals_raw_full[split_idx:]

    # --- Pre-compute regimes on FULL data (EMA50/200 warmup on df_full) ---
    regimes_full = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
    regimes_is = regimes_full[:split_idx]
    regimes_oos = regimes_full[split_idx:]

    # --- Signal variants ---
    signals_safe_full = _safe_execution_signals(signals_raw_full)
    signals_safe_is = signals_safe_full[:split_idx]
    signals_safe_oos = signals_safe_full[split_idx:]

    signals_regime_is, _ = apply_regime_short_filter(signals_raw_is, regimes_is, df_is)
    signals_regime_oos, _ = apply_regime_short_filter(signals_raw_oos, regimes_oos, df_oos)

    # --- Evaluate IS ---
    prices_is = df_is["close"].values.astype(float)
    prices_oos = df_oos["close"].values.astype(float)
    prices_full = np.concatenate([prices_is, prices_oos])

    is_raw = _evaluate_signals(signals_raw_is, prices_is)
    is_safe = _evaluate_signals(signals_safe_is, prices_is)
    is_regime = _evaluate_signals(signals_regime_is, prices_is)

    # --- Evaluate OOS ---
    oos_raw = _evaluate_signals(signals_raw_oos, prices_oos)
    oos_safe = _evaluate_signals(signals_safe_oos, prices_oos)
    oos_regime = _evaluate_signals(signals_regime_oos, prices_oos)

    # --- Rolling metrics (full data, pre-computed regimes) ---
    rolling = _compute_rolling_metrics(
        signals_raw_full, prices_full, ROLLING_WINDOW_MONTHS,
        regimes=regimes_full, df=df_full,
    )

    # --- Regime breakdown (pre-computed regimes) ---
    regime_breakdown = _compute_regime_breakdown(signals_raw_full, prices_full, regimes_full)

    # --- Fee / slippage sensitivity (IS + OOS) ---
    fee_sens = _compute_fee_sensitivity(signals_raw_is, prices_is)
    fee_sens_oos = _compute_fee_sensitivity(signals_raw_oos, prices_oos)
    slippage_sens = _compute_slippage_sensitivity(signals_raw_is, prices_is)
    slippage_sens_oos = _compute_slippage_sensitivity(signals_raw_oos, prices_oos)

    # --- vs Baseline correlation ---
    # If evaluating baseline itself, compute same-vs-same for documentation
    if _is_frozen_baseline:
        corr_vs_v21 = 1.0
    else:
        # Compare against baseline signals on same data
        if FROZEN_BASELINE_PARAMS.exists():
            baseline_ckpt = _load_baseline_params()
            baseline_signals = _generate_v21_signals(baseline_ckpt, df_full)
            corr_vs_v21 = _compute_equity_correlation(
                signals_raw_full, baseline_signals, prices_full
            )
        else:
            corr_vs_v21 = None

    correlation = {
        "vs_baseline": corr_vs_v21,
        "method": "equity_return_corr",
    }

    # --- Execution parity ---
    execution_parity = _compute_execution_parity(signals_raw_oos, signals_safe_oos, prices_oos)

    # --- Flags (baseline gets known_risks, not disqualifications) ---
    flags = _compute_flags(
        is_raw, oos_raw, rolling, fee_sens, fee_sens_oos, execution_parity, correlation,
        is_baseline=_is_frozen_baseline,
    )

    # --- Build result ---
    timestamp = _now_iso()
    commit = _get_git_commit()

    result = {
        "experiment_id": (
            f"oracle_{timestamp.replace(':', '').replace('-', '').replace('T', '_').replace('Z', '')}"
        ),
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
            "regime_attribution": {
                "method": "signal_isolated",
                "pnl_attributed": False,
                "caveat": "Positions may carry across regime boundaries. "
                          "Regime returns are directional comparisons, not precise PnL decomposition.",
            },
            "execution_parity": execution_parity,
            "correlation": correlation,
            "sensitivity": {
                "is": {
                    "fees": fee_sens,
                    "slippage": slippage_sens,
                },
                "oos": {
                    "fees": fee_sens_oos,
                    "slippage": slippage_sens_oos,
                },
            },
        },
        "flags": flags,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
    }
    # --- Add version fields ---
    result["oracle_version"] = ORACLE_VERSION
    result["oracle"] = {
        "version": ORACLE_VERSION,
        "regime_filter": {
            "fast_days": fast_days,
            "slow_days": slow_days,
        },
    }
    result["baseline_id"] = BASELINE_ID
    result["split_id"] = SPLIT_ID
    result["checkpoint_hash"] = checkpoint_hash

    return result


def _save_baseline_snapshot(result: Dict[str, Any]) -> None:
    """Save an immutable baseline snapshot to research_workspace/baselines/.

    The snapshot is named with oracle version so it is never overwritten
    by future oracle versions. This preserves the exact comparison anchor
    for all candidates.
    """
    _ensure_baseline_dir()
    snapshot_name = f"{BASELINE_ID}_oracle_{ORACLE_VERSION}.json"
    snapshot_path = BASELINE_DIR / snapshot_name
    if snapshot_path.exists():
        print(f"  baseline snapshot exists (not overwritten): {snapshot_name}")
        return
    snapshot_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"  baseline snapshot saved: {snapshot_path}")


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
    corr_val = m.get("correlation", {}).get("vs_baseline", "N/A")
    if corr_val is None:
        corr_val = "N/A"

    row = (
        f"{result['timestamp']}\t{result['experiment_id']}\t{result['parent_id'] or 'null'}\t"
        f"{result['candidate_role']}\tevaluate\t{result['strategy']}\t"
        f"{result['params_hash']}\t"
        f"{is_raw['return']:.4f}\t{is_raw['dd']:.4f}\t{is_raw['sharpe']:.4f}\t"
        f"{oos_safe['return']:.4f}\t{oos_safe['dd']:.4f}\t"
        f"{oos_raw['return']:.4f}\t{rolling_12m}\t"
        f"N/A\tN/A\t"
        f"{is_raw['trades_per_year']}\t"
        f"{corr_val}\t"
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
    with open(JSONL_PATH, "a", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(result, default=str) + "\n")
    print(f"  experiments.jsonl: appended record ({JSONL_PATH})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Research Oracle — fixed evaluation harness"
    )
    parser.add_argument(
        "--checkpoint", type=str, default=None,
        help="Path to a .pt checkpoint file.",
    )
    parser.add_argument(
        "--baseline", action="store_true",
        help="Evaluate frozen baseline from committed JSON params.",
    )
    parser.add_argument(
        "--candidate", type=str, default=None,
        help="Path to a candidate params JSON (Phase 3 read-only).",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Do not write output files (dry-run).",
    )
    parser.add_argument(
        "--fast-days", type=int, default=50,
        help="EMA fast period for regime labels (default: 50)",
    )
    parser.add_argument(
        "--slow-days", type=int, default=200,
        help="EMA slow period for regime labels (default: 200)",
    )
    args = parser.parse_args()

    # Validate regime parameters
    if args.fast_days <= 0:
        parser.error("--fast-days must be > 0")
    if args.slow_days <= 0:
        parser.error("--slow-days must be > 0")
    if args.fast_days >= args.slow_days:
        parser.error("--fast-days must be less than --slow-days")

    # Require exactly one input mode
    modes = sum([bool(args.checkpoint), args.baseline, bool(args.candidate)])
    if modes != 1:
        parser.error("Exactly one of --checkpoint, --baseline, or --candidate is required.")

    print(f"=== Research Oracle {ORACLE_VERSION} ===")
    if args.baseline:
        print(f"  Mode: baseline (frozen JSON params)")
    elif args.checkpoint:
        print(f"  Checkpoint: {args.checkpoint}")
    elif args.candidate:
        print(f"  Candidate: {args.candidate}")
    print(f"  Symbol: {SYMBOL} / {INTERVAL} (1300d, v0.2 data)")
    print(f"  Split: {int(SPLIT_RATIO*100)}/{int((1-SPLIT_RATIO)*100)} IS/OOS")
    print(f"  Regime: EMA fast={args.fast_days}d / slow={args.slow_days}d")
    print()

    t0 = time.time()
    result = run_oracle(
        checkpoint_path=args.checkpoint,
        candidate_path=args.candidate,
        use_baseline=args.baseline,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
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
        if "worst_bar" in k:
            continue
        elif "worst_time" in k or "worst_regime" in k:
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {v}")
    print()
    print(f"--- Regime ---")
    print(f"  (attribution: signal-isolated, not PnL-attributed)")
    for regime, data in m["regime"].items():
        print(f"  {regime}: return={data['return']}, trades={data['trades']}, bars={data['bars']}")
    print()
    print(f"--- Sensitivity ---")
    print(f"  IS  fees={m['sensitivity']['is']['fees']}")
    print(f"  OOS fees={m['sensitivity']['oos']['fees']}")
    print(f"  IS  slippage={m['sensitivity']['is']['slippage']}")
    print(f"  OOS slippage={m['sensitivity']['oos']['slippage']}")
    print()
    corr = m.get("correlation", {}).get("vs_baseline", "N/A")
    print(f"  Correlation vs baseline: {corr}")
    print()
    print(f"--- Flags ---")
    print(f"  status: {f['status']}")
    if "baseline_known_risks" in f:
        print(f"  baseline_known_risks: {f['baseline_known_risks']}")
    print(f"  warnings: {f['warnings']}")
    print(f"  disqualifications: {f['disqualifications']}")
    print()
    print(f"  Execution parity: {m['execution_parity']}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Oracle version: {result.get('oracle_version')}")
    print(f"  Baseline: {result.get('baseline_id', 'unknown')}")
    print(f"  Split: {result.get('split_id', 'unknown')}")

    # --- Write output ---
    if not args.no_write:
        print()
        _write_oracle_report(result)
        _append_results_tsv(result)
        _append_experiments_jsonl(result)
        # Save baseline snapshot when running baseline
        if args.baseline:
            _save_baseline_snapshot(result)
    else:
        print("\n  (--no-write: skipping output files)")

    print("\nDone.")
    return 0 if f["status"] not in ("REJECT",) else 1


if __name__ == "__main__":
    sys.exit(main())
