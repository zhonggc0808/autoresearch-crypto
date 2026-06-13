"""
Strategy variant template for research_workspace.

Usage:
  1. Copy this file, rename to your variant name (e.g. channel_breakout_variant_001.py)
  2. Import the base strategy class you want to extend
  3. Override or extend methods
  4. Register in strategy_variants/__init__.py (create if needed)
  5. Evaluate via oracle:
     uv run python research_oracle.py \
         --variant research_workspace/strategy_variants/channel_breakout_variant_001.py \
         --symbol ETHUSDT --interval 5m --days 1300

IMPORTANT:
  - Variant .py files are Phase 2+. In Phase 1, use YAML candidate specs only.
  - All variants must inherit from a BaseStrategy subclass.
  - Do NOT copy-paste the entire base strategy — only override what changes.
  - Variants are SANDBOXED: they cannot import live_* modules.
"""

# Example (DO NOT uncomment in Phase 1):
#
# from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy
#
# class ChannelBreakoutVariant001(ChannelBreakoutTrendStrategy):
#     """Example variant with trailing stop exit."""
#
#     def __init__(self, trailing_stop_pct: float = 0.05, **kwargs):
#         super().__init__(**kwargs)
#         self.trailing_stop_pct = trailing_stop_pct
#
#     def generate_signals(self, df, enable_short=True):
#         # Override or extend the base signal generation
#         signals = super().generate_signals(df, enable_short=enable_short)
#         # ... add trailing stop logic ...
#         return signals
