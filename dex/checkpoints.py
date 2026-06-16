"""Checkpoint loading and strategy construction helpers."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Mapping

import torch

from dex.strategies import (
    AdaptiveChannelBreakoutTrendStrategy,
    AdaptiveHybridStrategy,
    ChannelBreakoutTrendStrategy,
    DirectionalTrendStrategy,
    GridStrategy,
    HybridMeanRevMomentumStrategy,
    HybridStrategy,
    LongBiasTrendStrategy,
    PureActionStrategy,
    RegimeStrategy,
    ScalpStrategy,
    TrendFollowStrategy,
    TrendStrategy,
)
from dex.strategy_signals import generate_strategy_signals as generate_strategy_signals

STRATEGY_ALIASES: dict[str, type] = {
    "adaptive": AdaptiveHybridStrategy,
    "adaptivehybrid": AdaptiveHybridStrategy,
    "adaptive_channel_breakout": AdaptiveChannelBreakoutTrendStrategy,
    "adaptive_channel": AdaptiveChannelBreakoutTrendStrategy,
    "bollinger_trend_filter": TrendStrategy,
    "channel_breakout": ChannelBreakoutTrendStrategy,
    "channel_breakout_trend": ChannelBreakoutTrendStrategy,
    "channelbreakout": ChannelBreakoutTrendStrategy,
    "donchian": ChannelBreakoutTrendStrategy,
    "donchian_breakout": ChannelBreakoutTrendStrategy,
    "grid": GridStrategy,
    "hybrid": HybridStrategy,
    "directional_trend": DirectionalTrendStrategy,
    "directionaltrend": DirectionalTrendStrategy,
    "macro_trend": DirectionalTrendStrategy,
    "macrotrend": DirectionalTrendStrategy,
    "hybrid_mm": HybridMeanRevMomentumStrategy,
    "hybridmeanrevmomentum": HybridMeanRevMomentumStrategy,
    "long_bias": LongBiasTrendStrategy,
    "long_bias_trend": LongBiasTrendStrategy,
    "longbias": LongBiasTrendStrategy,
    "longbiastrend": LongBiasTrendStrategy,
    "pure": PureActionStrategy,
    "pureaction": PureActionStrategy,
    "regime": RegimeStrategy,
    "scalp": ScalpStrategy,
    "trend": TrendStrategy,
    "trendstrategy": TrendStrategy,
    "trend_follow": TrendFollowStrategy,
    "trendfollow": TrendFollowStrategy,
}

REGIME_CHANNEL_BREAKOUT_STRATEGY_TYPES = {
    "regime_permission_channel_breakout",
    "exit_logic_channel_breakout",
}

EXIT_LOGIC_STRATEGY_PARAM_FIELDS = (
    "exit_lookback",
    "take_profit_pct",
    "stop_loss_pct",
    "max_hold_bars",
)

PROFIT_LOCK_FIELD_MAP = {
    "enabled": "profit_lock_enabled",
    "activate_profit_pct": "profit_lock_activate_pct",
    "giveback_ratio": "profit_lock_giveback_ratio",
    "trailing_atr_multiplier": "profit_lock_atr_multiplier",
    "atr_period": "profit_lock_atr_period",
    "min_hold_bars_before_lock": "profit_lock_min_hold_bars",
}


def load_checkpoint(path: str | Path) -> dict[str, Any]:
    """Load a Torch or JSON checkpoint/profile from disk."""
    checkpoint_path = Path(path)
    if checkpoint_path.suffix.lower() == ".json":
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(checkpoint, dict):
            raise ValueError(
                f"JSON checkpoint must contain a dict, got {type(checkpoint).__name__}"
            )
        if isinstance(checkpoint.get("params"), Mapping):
            raw = checkpoint
            checkpoint = dict(raw["params"])
            for key in (
                "experiment_id",
                "parent_id",
                "candidate_role",
                "strategy",
                "description",
                "hypothesis",
                "expected_behavior_change",
                "base",
                "status",
                "version",
                "variant",
            ):
                if key in raw and key not in checkpoint:
                    checkpoint[key] = raw[key]
        return checkpoint

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if not isinstance(checkpoint, dict):
        raise ValueError(f"Checkpoint must contain a dict, got {type(checkpoint).__name__}")
    return checkpoint


def is_regime_channel_breakout_checkpoint(checkpoint: Mapping[str, Any]) -> bool:
    """Detect v2.1-compatible regime-routed ChannelBreakout checkpoint formats."""
    return (
        checkpoint.get("strategy_type") in REGIME_CHANNEL_BREAKOUT_STRATEGY_TYPES
        and "bull" in checkpoint
        and "bear" in checkpoint
        and "neutral" in checkpoint
    )


def apply_exit_logic_to_strategy_params(
    params: Mapping[str, Any],
    exit_logic: Mapping[str, Any] | None,
    regime: str | None = None,
) -> dict[str, Any]:
    """Return strategy params with optional top-level exit logic applied.

    Family candidates keep exit research at checkpoint top level so the three
    regime parameter blocks remain comparable. This helper translates that
    overlay into the constructor fields consumed by ChannelBreakoutTrendStrategy.
    """
    merged = dict(params)
    if not exit_logic:
        return merged

    for field in EXIT_LOGIC_STRATEGY_PARAM_FIELDS:
        if field in exit_logic:
            merged[field] = exit_logic[field]

    _merge_profit_lock_params(merged, exit_logic.get("profit_lock"))

    by_regime = exit_logic.get("profit_lock_by_regime")
    if regime is not None and isinstance(by_regime, Mapping):
        _merge_profit_lock_params(merged, by_regime.get(regime))

    return merged


def _merge_profit_lock_params(params: dict[str, Any], profit_lock: Any) -> None:
    if not isinstance(profit_lock, Mapping):
        return
    for source, target in PROFIT_LOCK_FIELD_MAP.items():
        if source in profit_lock:
            params[target] = profit_lock[source]


def build_channel_breakout_strategy_from_checkpoint(
    checkpoint: Mapping[str, Any],
    regime: str,
) -> ChannelBreakoutTrendStrategy:
    """Build a regime ChannelBreakout strategy with top-level exit overlays."""
    regime_block = checkpoint[regime]
    params = apply_exit_logic_to_strategy_params(
        regime_block["strategy_params"],
        checkpoint.get("exit_logic"),
        regime=regime,
    )
    return ChannelBreakoutTrendStrategy(**params)


def build_strategy_from_checkpoint(checkpoint: Mapping[str, Any]) -> Any:
    """Instantiate the strategy described by a checkpoint dict."""
    params = dict(checkpoint.get("params") or {})
    strategy_name = str(checkpoint.get("strategy", "bollinger_trend_filter")).lower()
    strategy_cls = STRATEGY_ALIASES.get(strategy_name)
    if strategy_cls is None:
        known = ", ".join(sorted(STRATEGY_ALIASES))
        raise ValueError(f"Unknown strategy {strategy_name!r}; expected one of: {known}")

    init_params = _filter_constructor_params(strategy_cls, params)
    return strategy_cls(**init_params)


def describe_strategy(strategy: Any, strategy_name: str) -> list[str]:
    """Return concise log lines for a constructed strategy."""
    fields = [
        "window",
        "std_dev",
        "rsi_period",
        "rsi_low",
        "rsi_high",
        "ma_period",
        "fast_ma_period",
        "slow_ma_period",
        "macro_ma_period",
        "pullback_ma_period",
        "breakout_lookback",
        "entry_lookback",
        "exit_lookback",
        "breakout_buffer_pct",
        "breakout_atr_buffer",
        "emergency_stop_pct",
        "enable_long",
        "atr_period",
        "atr_multiplier",
        "trend_ma_period",
        "trend_slope_lookback",
        "min_trend_slope",
        "trend_buffer_pct",
        "adx_period",
        "require_di_alignment",
        "max_hold_bars",
        "take_profit_pct",
        "stop_loss_pct",
        "enable_short",
        "adx_threshold",
        "profit_lock_enabled",
        "profit_lock_activate_pct",
        "profit_lock_giveback_ratio",
        "profit_lock_atr_multiplier",
        "profit_lock_atr_period",
        "profit_lock_min_hold_bars",
    ]
    values = [f"{field}={getattr(strategy, field)}" for field in fields if hasattr(strategy, field)]
    return [f"策略模式: {strategy_name}", "参数: " + ", ".join(values)]


def _filter_constructor_params(strategy_cls: type, params: Mapping[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(strategy_cls.__init__)
    accepted = set(signature.parameters) - {"self"}
    return {key: value for key, value in params.items() if key in accepted}
