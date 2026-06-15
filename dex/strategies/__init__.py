"""dex.strategies — Trading strategy classes.

Re-exports all strategy types for convenient import::

    from dex.strategies import TrendStrategy, StrategyEvaluator
"""

from dex.strategies.base import BaseStrategy, StrategyEvaluator

# Strategy classes (imported as their modules are created)
__all__ = [
    "BaseStrategy",
    "StrategyEvaluator",
    "TrendStrategy",
    "ScalpStrategy",
    "PureActionStrategy",
    "HybridStrategy",
    "TrendFollowStrategy",
    "ChannelBreakoutTrendStrategy",
    "DirectionalTrendStrategy",
    "LongBiasTrendStrategy",
    "HybridMeanRevMomentumStrategy",
    "AdaptiveHybridStrategy",
    "AdaptiveChannelBreakoutTrendStrategy",
    "GridStrategy",
    "RegimeStrategy",
    "PureActionV2Strategy",
    "MultiTFEnsembleStrategy",
]


# Lazy imports to avoid circular dependencies — modules import from base/sub-modules
def __getattr__(name: str):
    _imports = {
        "TrendStrategy": "dex.strategies.trend",
        "ScalpStrategy": "dex.strategies.scalp",
        "PureActionStrategy": "dex.strategies.pure_action",
        "HybridStrategy": "dex.strategies.hybrid",
        "TrendFollowStrategy": "dex.strategies.trend_follow",
        "ChannelBreakoutTrendStrategy": "dex.strategies.channel_breakout",
        "AdaptiveChannelBreakoutTrendStrategy": "dex.strategies.adaptive_channel_breakout",
        "DirectionalTrendStrategy": "dex.strategies.long_bias",
        "LongBiasTrendStrategy": "dex.strategies.long_bias",
        "HybridMeanRevMomentumStrategy": "dex.strategies.hybrid_mm",
        "AdaptiveHybridStrategy": "dex.strategies.adaptive",
        "GridStrategy": "dex.strategies.grid",
        "RegimeStrategy": "dex.strategies.regime",
        "PureActionV2Strategy": "dex.strategies.pure_action_v2",
        "MultiTFEnsembleStrategy": "dex.strategies.multi_tf_ensemble",
    }
    if name in _imports:
        import importlib

        mod = importlib.import_module(_imports[name])
        cls = getattr(mod, name)
        # Cache in module namespace
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
