"""Kraken-style entry and size overlay experiments for channel_breakout_375_432."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from dex.config import COMMISSION, INITIAL_CAPITAL, SLIPPAGE
from dex.indicators import compute_atr, compute_rsi
from dex.regime_filter import build_daily_regime_labels
from dex.strategies.base import StrategyEvaluator
from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy

CHECKPOINT = "checkpoints/channel_breakout_375_432.pt"
DATA_FILE = Path("data/crypto/ETHUSDT_5m_2600d.parquet")
OOS_START = "2024-06-06 14:25:00"
OOS_END = "2026-06-12 02:55:00"
OUTPUT_DIR = Path("research_workspace/diagnostics")
OUTPUT_PREFIX = "channel_breakout_375_432_v2_oos_2600d"
BASE_SIZES = [0.45, 1.0]
VARIANTS = (
    "baseline_fixed",
    "kraken_mc_sizing",
    "kraken_long_filter_size",
    "kraken_symmetric_filter_size",
    "closed_bar_quality_sizing",
)
BLOCK_BARS = 288
SCRIPT_VERSION = "2026-06-19.kraken_overlay.v1"

RSI_PERIOD = 14
VOL_PERIOD = 20
RSI_MAX_LONG = 65.0
RSI_MIN_SHORT = 35.0
VOL_MIN_PCT = 0.10
VOL_MAX_PCT = 4.0
MC_MIN_CONF = 0.55
MC_PATHS = 800
MC_HORIZON_BARS = 12
MC_RETURN_LOOKBACK = 375
ATR_PERIOD = 14
ATR_PERCENTILE_LOOKBACK = 2016
VOLUME_Z_LOOKBACK = 288
BREAKOUT_ATR_FULL_SCORE = 0.5
QUALITY_SIZE_BUCKETS = ((0.70, 1.5), (0.55, 1.0), (0.40, 0.5))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mc-sims", type=int, default=2000)
    parser.add_argument("--mc-seed", type=int, default=2202)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df_oos = _load_oos_data()
    strategy = ChannelBreakoutTrendStrategy(entry_lookback=375, min_hold_bars=432)
    signals = strategy.generate_signals(df_oos)
    prices = df_oos["close"].to_numpy(dtype=float)

    confidence_cache: dict[tuple[int, ...], float] = {}
    baseline_equity = {
        base_size: _baseline_equity(signals, prices, df_oos, base_size)
        for base_size in BASE_SIZES
    }
    rows: list[dict[str, Any]] = []
    for base_size in BASE_SIZES:
        for variant in VARIANTS:
            rows.append(
                _run_variant(
                    variant,
                    base_size,
                    signals,
                    prices,
                    df_oos,
                    args.mc_sims,
                    args.mc_seed,
                    confidence_cache,
                    baseline_equity[base_size],
                )
            )

    _add_return_deltas(rows)

    csv_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_kraken_overlay_matrix.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    report_path = OUTPUT_DIR / f"{OUTPUT_PREFIX}_kraken_overlay_report.md"
    report_path.write_text(_report(rows, args.mc_sims), encoding="utf-8")

    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")


def build_kraken_position_sizes(
    df: pd.DataFrame,
    signals: np.ndarray,
    base_size: float,
    variant: str,
    *,
    seed: int = 2202,
    rsi_period: int = RSI_PERIOD,
    vol_period: int = VOL_PERIOD,
    rsi_max_long: float = RSI_MAX_LONG,
    rsi_min_short: float = RSI_MIN_SHORT,
    vol_min_pct: float = VOL_MIN_PCT,
    vol_max_pct: float = VOL_MAX_PCT,
    mc_min_conf: float = MC_MIN_CONF,
    mc_paths: int = MC_PATHS,
    mc_horizon_bars: int = MC_HORIZON_BARS,
    mc_return_lookback: int = MC_RETURN_LOOKBACK,
    confidence_cache: dict[tuple[int, ...], float] | None = None,
    shadow_equity: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, int]]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    if not 0 <= base_size <= 1:
        raise ValueError("base_size must be in [0, 1]")
    if len(df) != len(signals):
        raise ValueError("df and signals must have the same length")

    sizes = np.full(len(signals), base_size, dtype=float)
    stats = {
        "entry_count": 0,
        "long_entry_count": 0,
        "short_entry_count": 0,
        "blocked_entry_count": 0,
        "half_size_entry_count": 0,
        "boosted_entry_count": 0,
        "unchanged_entry_count": 0,
    }
    if variant == "baseline_fixed":
        return sizes, stats

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float) if "high" in df.columns else close
    low = df["low"].to_numpy(dtype=float) if "low" in df.columns else close
    volume = df["volume"].to_numpy(dtype=float)
    rsi = volatility_pct = volume_sma = None
    if variant in {"kraken_long_filter_size", "kraken_symmetric_filter_size"}:
        rsi = _safe_rsi(close, rsi_period)
        volatility_pct = _rolling_volatility_pct(close, vol_period)
        volume_sma = pd.Series(volume).rolling(vol_period, min_periods=vol_period).mean().to_numpy()
    channel_high = (
        pd.Series(close)
        .rolling(mc_return_lookback, min_periods=mc_return_lookback)
        .max()
        .shift(1)
        .to_numpy()
    )
    channel_low = (
        pd.Series(close)
        .rolling(mc_return_lookback, min_periods=mc_return_lookback)
        .min()
        .shift(1)
        .to_numpy()
    )
    quality_atr = quality_atr_percentile = quality_volume_z = None
    quality_regimes = quality_high = quality_low = None
    if variant == "closed_bar_quality_sizing":
        quality_atr = compute_atr(df, min(ATR_PERIOD, len(df)))
        quality_atr_pct = np.divide(
            quality_atr,
            close,
            out=np.zeros(len(close), dtype=float),
            where=close > 0,
        )
        quality_atr_percentile = _rolling_percentile(
            quality_atr_pct,
            min(ATR_PERCENTILE_LOOKBACK, len(df)),
        )
        quality_volume_z = _rolling_zscore(volume, min(VOLUME_Z_LOOKBACK, len(df)))
        quality_regimes = build_daily_regime_labels(df)
        quality_high = (
            pd.Series(high)
            .rolling(mc_return_lookback, min_periods=mc_return_lookback)
            .max()
            .shift(1)
            .to_numpy()
        )
        quality_low = (
            pd.Series(low)
            .rolling(mc_return_lookback, min_periods=mc_return_lookback)
            .min()
            .shift(1)
            .to_numpy()
        )

    cache = confidence_cache if confidence_cache is not None else {}
    position = 0
    for i, raw_signal in enumerate(np.asarray(signals, dtype=int)):
        target = _target_position(int(raw_signal), position)
        opens_new = target != 0 and target != position
        if not opens_new:
            position = target
            continue

        direction = target
        next_size = base_size
        if variant == "kraken_mc_sizing":
            conf = _cached_mc_confidence(
                cache,
                close,
                channel_high,
                channel_low,
                i,
                direction,
                seed,
                mc_paths,
                mc_horizon_bars,
                mc_return_lookback,
            )
            next_size = base_size * mc_size_multiplier(conf, mc_min_conf)
        elif variant == "kraken_long_filter_size" and direction == 1:
            assert rsi is not None
            assert volatility_pct is not None
            assert volume_sma is not None
            next_size = _filtered_size(
                base_size,
                _passes_filter(
                    direction,
                    i,
                    rsi,
                    volatility_pct,
                    volume,
                    volume_sma,
                    rsi_max_long,
                    rsi_min_short,
                    vol_min_pct,
                    vol_max_pct,
                ),
                _cached_mc_confidence(
                    cache,
                    close,
                    channel_high,
                    channel_low,
                    i,
                    direction,
                    seed,
                    mc_paths,
                    mc_horizon_bars,
                    mc_return_lookback,
                ),
                mc_min_conf,
            )
        elif variant == "kraken_symmetric_filter_size":
            assert rsi is not None
            assert volatility_pct is not None
            assert volume_sma is not None
            next_size = _filtered_size(
                base_size,
                _passes_filter(
                    direction,
                    i,
                    rsi,
                    volatility_pct,
                    volume,
                    volume_sma,
                    rsi_max_long,
                    rsi_min_short,
                    vol_min_pct,
                    vol_max_pct,
                ),
                _cached_mc_confidence(
                    cache,
                    close,
                    channel_high,
                    channel_low,
                    i,
                    direction,
                    seed,
                    mc_paths,
                    mc_horizon_bars,
                    mc_return_lookback,
                ),
                mc_min_conf,
            )
        elif variant == "closed_bar_quality_sizing":
            closed_i = i - 1
            if closed_i < 0:
                next_size = 0.0
            else:
                assert quality_atr is not None
                assert quality_atr_percentile is not None
                assert quality_volume_z is not None
                assert quality_regimes is not None
                assert quality_high is not None
                assert quality_low is not None
                quality = _closed_bar_quality_score(
                    direction,
                    closed_i,
                    close,
                    quality_high,
                    quality_low,
                    quality_atr,
                    quality_atr_percentile,
                    quality_volume_z,
                    quality_regimes,
                    shadow_equity,
                )
                next_size = base_size * quality_size_multiplier(quality)

        next_size = float(np.clip(next_size, 0.0, 1.0))
        sizes[i] = next_size
        _record_entry(stats, direction, next_size, base_size)
        position = target if next_size > 0 else 0

    return sizes, stats


def mc_size_multiplier(confidence: float, mc_min_conf: float = MC_MIN_CONF) -> float:
    if confidence < mc_min_conf:
        return 0.0
    if confidence >= 0.75:
        return 1.5
    if confidence >= 0.65:
        return 1.0
    return 0.5


def quality_size_multiplier(quality: float) -> float:
    for threshold, multiplier in QUALITY_SIZE_BUCKETS:
        if quality >= threshold:
            return multiplier
    return 0.0


def _closed_bar_quality_score(
    direction: int,
    i: int,
    close: np.ndarray,
    channel_high: np.ndarray,
    channel_low: np.ndarray,
    atr: np.ndarray,
    atr_percentile: np.ndarray,
    volume_z: np.ndarray,
    regimes: np.ndarray,
    shadow_equity: np.ndarray | None,
) -> float:
    breakout = _breakout_strength_score(direction, i, close, channel_high, channel_low, atr)
    volatility = _atr_percentile_score(float(atr_percentile[i]))
    volume = _volume_z_score(float(volume_z[i]))
    regime = _regime_score(direction, str(regimes[i]))
    drawdown = _drawdown_score(shadow_equity, i)
    scores = [breakout, volatility, volume, regime, drawdown]
    if not all(np.isfinite(score) for score in scores):
        return 0.0
    return float(0.30 * breakout + 0.20 * volatility + 0.20 * volume + 0.20 * regime + 0.10 * drawdown)


def _breakout_strength_score(
    direction: int,
    i: int,
    close: np.ndarray,
    channel_high: np.ndarray,
    channel_low: np.ndarray,
    atr: np.ndarray,
) -> float:
    if atr[i] <= 0 or not np.isfinite(atr[i]):
        return 0.0
    if direction == 1:
        distance = close[i] - channel_high[i]
    else:
        distance = channel_low[i] - close[i]
    return float(np.clip((distance / atr[i]) / BREAKOUT_ATR_FULL_SCORE, 0.0, 1.0))


def _atr_percentile_score(percentile: float) -> float:
    if not np.isfinite(percentile):
        return 0.0
    if percentile < 0.20:
        return float(percentile / 0.20)
    if percentile > 0.90:
        return float(max(0.0, (1.0 - percentile) / 0.10))
    return 1.0


def _volume_z_score(zscore: float) -> float:
    if not np.isfinite(zscore):
        return 0.0
    return float(np.clip((zscore + 0.5) / 2.0, 0.0, 1.0))


def _regime_score(direction: int, regime: str) -> float:
    if regime == "BULL":
        return 1.0 if direction == 1 else 0.0
    if regime == "BEAR":
        return 1.0 if direction == -1 else 0.75
    return 0.5


def _drawdown_score(equity: np.ndarray | None, i: int) -> float:
    if equity is None or i <= 0 or i >= len(equity):
        return 1.0
    peak = float(np.max(equity[: i + 1]))
    if peak <= 0:
        return 0.0
    dd = max(0.0, (peak - float(equity[i])) / peak)
    return float(np.clip(1.0 - dd / 0.20, 0.0, 1.0))


def _filtered_size(
    base_size: float,
    filters_pass: bool,
    confidence: float,
    mc_min_conf: float,
) -> float:
    if not filters_pass:
        return 0.0
    return base_size * mc_size_multiplier(confidence, mc_min_conf)


def _passes_filter(
    direction: int,
    i: int,
    rsi: np.ndarray,
    volatility_pct: np.ndarray,
    volume: np.ndarray,
    volume_sma: np.ndarray,
    rsi_max_long: float,
    rsi_min_short: float,
    vol_min_pct: float,
    vol_max_pct: float,
) -> bool:
    values = (rsi[i], volatility_pct[i], volume[i], volume_sma[i])
    if not all(np.isfinite(value) for value in values):
        return False
    rsi_ok = rsi[i] <= rsi_max_long if direction == 1 else rsi[i] >= rsi_min_short
    vol_ok = vol_min_pct <= volatility_pct[i] <= vol_max_pct
    volume_ok = volume[i] > volume_sma[i]
    return bool(rsi_ok and vol_ok and volume_ok)


def _cached_mc_confidence(
    cache: dict[tuple[int, ...], float],
    close: np.ndarray,
    channel_high: np.ndarray,
    channel_low: np.ndarray,
    i: int,
    direction: int,
    seed: int,
    paths: int,
    horizon_bars: int,
    return_lookback: int,
) -> float:
    key = (i, direction, seed, paths, horizon_bars, return_lookback)
    if key not in cache:
        cache[key] = _mc_breakout_confidence(
            close,
            channel_high,
            channel_low,
            i,
            direction,
            seed,
            paths,
            horizon_bars,
            return_lookback,
        )
    return cache[key]


def _mc_breakout_confidence(
    close: np.ndarray,
    channel_high: np.ndarray,
    channel_low: np.ndarray,
    i: int,
    direction: int,
    seed: int,
    paths: int,
    horizon_bars: int,
    return_lookback: int,
) -> float:
    threshold = channel_high[i] if direction == 1 else channel_low[i]
    if not np.isfinite(threshold) or close[i] <= 0 or paths <= 0 or horizon_bars <= 0:
        return 0.5

    start = max(1, i - return_lookback + 1)
    prev = close[start - 1 : i]
    current = close[start : i + 1]
    valid = prev > 0
    returns = current[valid] / prev[valid] - 1.0
    returns = returns[np.isfinite(returns)]
    if len(returns) < 10:
        return 0.5

    rng = np.random.default_rng(seed + i * 2 + (0 if direction == 1 else 1))
    sampled = rng.choice(returns, size=(paths, horizon_bars), replace=True)
    terminal = close[i] * np.prod(1.0 + sampled, axis=1)
    if direction == 1:
        return float(np.mean(terminal > threshold))
    return float(np.mean(terminal < threshold))


def _target_position(signal: int, position: int) -> int:
    if signal == 2:
        return 1
    if signal == 3:
        return -1
    if signal == 0:
        return 0
    return position


def _record_entry(stats: dict[str, int], direction: int, size: float, base_size: float) -> None:
    stats["entry_count"] += 1
    if direction == 1:
        stats["long_entry_count"] += 1
    else:
        stats["short_entry_count"] += 1

    if size == 0:
        stats["blocked_entry_count"] += 1
    elif size < base_size:
        stats["half_size_entry_count"] += 1
    elif size > base_size:
        stats["boosted_entry_count"] += 1
    else:
        stats["unchanged_entry_count"] += 1


def _rolling_volatility_pct(close: np.ndarray, period: int) -> np.ndarray:
    close_s = pd.Series(close)
    low = close_s.rolling(period, min_periods=period).min().to_numpy()
    high = close_s.rolling(period, min_periods=period).max().to_numpy()
    out = np.full(len(close), np.nan, dtype=float)
    valid = low > 0
    out[valid] = (high[valid] - low[valid]) / low[valid] * 100.0
    return out


def _safe_rsi(close: np.ndarray, period: int) -> np.ndarray:
    if len(close) <= period:
        return np.full(len(close), 50.0, dtype=float)
    return compute_rsi(close, period)


def _rolling_zscore(values: np.ndarray, period: int) -> np.ndarray:
    series = pd.Series(values)
    mean = series.rolling(period, min_periods=period).mean()
    std = series.rolling(period, min_periods=period).std(ddof=0)
    return ((series - mean) / std.replace(0.0, np.nan)).to_numpy(dtype=float)


def _rolling_percentile(values: np.ndarray, period: int) -> np.ndarray:
    series = pd.Series(values)
    return series.rolling(period, min_periods=period).apply(
        lambda window: float(np.mean(window <= window[-1])),
        raw=True,
    ).to_numpy(dtype=float)


def _run_variant(
    variant: str,
    base_size: float,
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
    mc_sims: int,
    mc_seed: int,
    confidence_cache: dict[tuple[int, ...], float],
    shadow_equity: np.ndarray,
) -> dict[str, Any]:
    evaluator = StrategyEvaluator()
    position_sizes, overlay_stats = build_kraken_position_sizes(
        df,
        signals,
        base_size,
        variant,
        seed=mc_seed,
        confidence_cache=confidence_cache,
        shadow_equity=shadow_equity,
    )
    equity, trades = evaluator.simulate(signals, prices, df, position_sizes=position_sizes)
    metrics = evaluator.compute_metrics(equity, trades)
    pnls = [float(t["pnl"]) for t in trades if t.get("pnl") is not None]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl < 0]

    row: dict[str, Any] = {
        "base_size": base_size,
        "variant": variant,
        "total_return": metrics["total_return"],
        "annualized_return": metrics["annualized_return"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "win_rate": metrics["win_rate"],
        "trade_count": len(pnls),
        "avg_win": float(np.mean(winners)) if winners else 0.0,
        "avg_loss": float(np.mean(losers)) if losers else 0.0,
        "profit_factor": _profit_factor(winners, losers),
        "max_single_loss": min(pnls) if pnls else 0.0,
        "position_size_min": float(np.min(position_sizes)),
        "position_size_max": float(np.max(position_sizes)),
        "position_size_mean": float(np.mean(position_sizes)),
        "git_commit": _git_commit(),
        "checkpoint": CHECKPOINT,
        "data_file": str(DATA_FILE),
        "oos_start": OOS_START,
        "oos_end": OOS_END,
        "commission": COMMISSION,
        "slippage": SLIPPAGE,
        "mc_block_bars": BLOCK_BARS,
        "script_version": SCRIPT_VERSION,
    }
    row.update(overlay_stats)
    row.update(_monte_carlo(equity, mc_sims, mc_seed))
    return row


def _add_return_deltas(rows: list[dict[str, Any]]) -> None:
    baseline = {
        row["base_size"]: row["total_return"]
        for row in rows
        if row["variant"] == "baseline_fixed"
    }
    for row in rows:
        base_return = float(baseline[row["base_size"]])
        delta = float(row["total_return"] - base_return)
        row["baseline_total_return"] = base_return
        row["total_return_delta"] = delta
        row["beats_baseline"] = row["variant"] != "baseline_fixed" and delta > 0


def _baseline_equity(
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame,
    base_size: float,
) -> np.ndarray:
    evaluator = StrategyEvaluator()
    sizes = np.full(len(signals), base_size, dtype=float)
    equity, _ = evaluator.simulate(signals, prices, df, position_sizes=sizes)
    return equity


def _report(rows: list[dict[str, Any]], mc_sims: int) -> str:
    lines = [
        "# Kraken Overlay - Return Priority Experiment",
        "",
        f"Generated: {datetime.now().isoformat()}",
        f"Checkpoint: {CHECKPOINT}",
        f"Data: {DATA_FILE}",
        f"OOS: {OOS_START} to {OOS_END}",
        f"Git commit: {_git_commit()}",
        f"Script version: {SCRIPT_VERSION}",
        f"Fee/slippage: commission={COMMISSION}, slippage={SLIPPAGE}",
        f"MC simulations: {mc_sims}",
        f"MC block bars: {BLOCK_BARS}",
        "",
        "Pass rule: `total_return > matching baseline total_return`. MC DD<-30% is diagnostic only.",
        "",
        "| Base | Variant | Return | Delta | Pass | MaxDD | Sharpe | Trades | MC DD<-30% | MC loss | Block/Half/Boost |",
        "|---:|---|---:|---:|:---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['base_size']:.3f} | `{row['variant']}` | "
            f"{row['total_return']:.1%} | {row['total_return_delta']:.1%} | "
            f"{'Y' if row['beats_baseline'] else ''} | {row['max_drawdown']:.1%} | "
            f"{row['sharpe_ratio']:.2f} | {row['trade_count']} | "
            f"{row['mc_dd30_probability']:.1%} | {row['mc_loss_probability']:.1%} | "
            f"{row['blocked_entry_count']}/{row['half_size_entry_count']}/{row['boosted_entry_count']} |"
        )

    winners = [row for row in rows if row["beats_baseline"]]
    lines.extend(["", "## Winners", ""])
    if winners:
        for row in winners:
            lines.append(
                f"- {row['base_size']:.3f} `{row['variant']}`: "
                f"return delta {row['total_return_delta']:.1%}"
            )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


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


def _monte_carlo(equity: np.ndarray, sims: int, seed: int) -> dict[str, float | int]:
    daily_returns = _daily_returns(equity)
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
        return np.array([equity[-1] / equity[0] - 1.0], dtype=float)
    sampled = equity[:usable].reshape(-1, BLOCK_BARS)
    return sampled[:, -1] / sampled[:, 0] - 1.0


def _max_drawdown(equity: np.ndarray) -> float:
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        max_dd = min(max_dd, (value - peak) / peak)
    return float(max_dd)


def _profit_factor(winners: list[float], losers: list[float]) -> float:
    loss_sum = sum(losers)
    return abs(sum(winners) / loss_sum) if loss_sum else float("inf")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
