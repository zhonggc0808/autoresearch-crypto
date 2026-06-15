"""Checkpoint loading and strategy construction helpers."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Mapping

import torch

from dex.strategies import (
    AdaptiveChannelBreakoutTrendStrategy,
    AdaptiveHybridStrategy,
    ChannelBreakoutTrendStrategy,
    GridStrategy,
    HybridMeanRevMomentumStrategy,
    HybridStrategy,
    DirectionalTrendStrategy,
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


def load_checkpoint(path: str | Path) -> dict[str, Any]:
    """Load a Torch checkpoint from disk."""
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    if not isinstance(checkpoint, dict):
        raise ValueError(f"Checkpoint must contain a dict, got {type(checkpoint).__name__}")
    return checkpoint


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
    ]
    values = [f"{field}={getattr(strategy, field)}" for field in fields if hasattr(strategy, field)]
    return [f"策略模式: {strategy_name}", "参数: " + ", ".join(values)]


def _filter_constructor_params(strategy_cls: type, params: Mapping[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(strategy_cls.__init__)
    accepted = set(signature.parameters) - {"self"}
    return {key: value for key, value in params.items() if key in accepted}
