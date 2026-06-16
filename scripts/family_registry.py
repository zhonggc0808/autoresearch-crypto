#!/usr/bin/env python3
"""Family Registry v0.8 — central authority for strategy family definitions.

Every family must be registered here before it can be used in the research loop.
The registry defines:
    - Parameter bounds and types
    - Allowed change fields (for fork actions)
    - Schema file paths
    - Search space descriptions (for generator prompts)
    - Validation rules
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = PROJECT_DIR / "research_workspace" / "family_schemas"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ParamDef:
    """Definition of a single parameter's type and bounds."""

    type: type
    min: Optional[Any] = None
    max: Optional[Any] = None
    enum: Optional[List[Any]] = None


@dataclass
class FamilyDefinition:
    """Complete definition of a strategy family."""

    name: str
    description: str
    schema_file: str  # filename in family_schemas/
    strategy_type: str  # value for params.strategy_type field
    strategy_params: Dict[str, ParamDef]
    allowed_change: Dict[str, Any]  # flat keys + nested specs for fork validation
    allowed_change_nested: Dict[str, Dict[str, Any]]  # nested objects in allowed_change
    search_space_text: str  # text for generator prompt
    extra_fields: List[str] = field(default_factory=list)  # allowed extra top-level fields


# ---------------------------------------------------------------------------
# Family definitions
# ---------------------------------------------------------------------------

CHANNEL_BREAKOUT = FamilyDefinition(
    name="channel_breakout",
    description="Regime-permission channel breakout (Donchian)",
    schema_file="channel_breakout_v0.2.json",
    strategy_type="regime_permission_channel_breakout",
    strategy_params={
        "entry_lookback": ParamDef(int, 20, 1000),
        "min_hold_bars": ParamDef(int, 12, 1440),
        "enable_long": ParamDef(bool),
        "enable_short": ParamDef(bool),
    },
    allowed_change={
        "entry_lookback": {"type": "int", "min": 20, "max": 1000},
        "min_hold_bars": {"type": "int", "min": 12, "max": 1440},
    },
    allowed_change_nested={
        "regime_filter": {
            "fast_days": {"type": "int", "min": 5, "max": 200},
            "slow_days": {"type": "int", "min": 10, "max": 500},
        },
    },
    search_space_text=(
        "entry_lookback: [20, 1000], default 375\n"
        "min_hold_bars: [12, 1440], default 432\n"
        "regime_filter.fast_days: [5, 200], default 50\n"
        "regime_filter.slow_days: [10, 500], default 200"
    ),
    extra_fields=["filter"],
)

VOLATILITY_FILTERED_BREAKOUT = FamilyDefinition(
    name="volatility_filtered_breakout",
    description="Channel breakout with volatility-based entry filtering",
    schema_file="volatility_filtered_breakout_v0.8.json",
    strategy_type="volatility_filtered_channel_breakout",
    strategy_params={
        "entry_lookback": ParamDef(int, 20, 1000),
        "min_hold_bars": ParamDef(int, 12, 1440),
        "enable_long": ParamDef(bool),
        "enable_short": ParamDef(bool),
    },
    allowed_change={
        "entry_lookback": {"type": "int", "min": 20, "max": 1000},
        "min_hold_bars": {"type": "int", "min": 12, "max": 1440},
    },
    allowed_change_nested={
        "regime_filter": {
            "fast_days": {"type": "int", "min": 5, "max": 200},
            "slow_days": {"type": "int", "min": 10, "max": 500},
        },
        "volatility_filter": {
            "enabled": {"type": "bool"},
            "lookback": {"type": "int", "min": 60, "max": 1440},
            "mode": {"type": "str", "enum": ["exclude_extreme", "exclude_low", "exclude_high"]},
            "low_quantile": {"type": "float", "min": 0.01, "max": 0.50},
            "high_quantile": {"type": "float", "min": 0.50, "max": 0.99},
        },
    },
    search_space_text=(
        "entry_lookback: [20, 1000], default 240\n"
        "min_hold_bars: [12, 1440], default 48\n"
        "volatility_filter.lookback: [60, 1440], default 288\n"
        "volatility_filter.mode: exclude_extreme | exclude_low | exclude_high\n"
        "volatility_filter.low_quantile: [0.01, 0.50], default 0.05\n"
        "volatility_filter.high_quantile: [0.50, 0.99], default 0.95\n"
        "regime_filter.fast_days: [5, 200], default 50\n"
        "regime_filter.slow_days: [10, 500], default 200"
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, FamilyDefinition] = {}


def register(family: FamilyDefinition) -> None:
    """Register a family definition."""
    _REGISTRY[family.name] = family


def get(name: str) -> Optional[FamilyDefinition]:
    """Get a family definition by name."""
    return _REGISTRY.get(name)


def is_valid(name: str) -> bool:
    """Check if a family name is registered."""
    return name in _REGISTRY


def list_families() -> List[str]:
    """List all registered family names."""
    return list(_REGISTRY.keys())


def search_space_summary(family: Optional[str] = None) -> str:
    """Get search space summary for a family, or all families."""
    if family:
        fd = get(family)
        return fd.search_space_text if fd else f"(unknown family: {family})"
    parts = []
    for name in sorted(_REGISTRY.keys()):
        fd = _REGISTRY[name]
        parts.append(f"=== {name} ===\n{fd.search_space_text}")
    return "\n\n".join(parts)


def validate_allowed_change(family: str, ac: Dict[str, Any]) -> List[str]:
    """Validate an allowed_change dict for a specific family.

    This is the same logic used by review_candidate.py and execute_action.py.
    Returns list of error messages (empty = valid).
    """
    errors: List[str] = []
    fd = get(family)
    if not fd:
        return [f"Unknown family: {family}"]

    if not isinstance(ac, dict):
        return ["allowed_change must be a dict"]

    for key, value in ac.items():
        # Check nested objects
        if key in fd.allowed_change_nested:
            if not isinstance(value, dict):
                errors.append(f"allowed_change.{key}: expected dict, got {type(value).__name__}")
                continue
            nested_spec = fd.allowed_change_nested[key]
            for nk, nv in value.items():
                if nk not in nested_spec:
                    errors.append(f"allowed_change.{key}.{nk}: unknown field")
                    continue
                spec = nested_spec[nk]
                st = spec.get("type", "")
                if st == "int":
                    if not isinstance(nv, int):
                        errors.append(f"allowed_change.{key}.{nk}: expected int")
                    elif (
                        nv not in spec.get("allowed_values", [])
                        and (nv < spec.get("min", 0) or nv > spec.get("max", 999999))
                    ):
                        errors.append(
                            f"allowed_change.{key}.{nk}: {nv} out of range "
                            f"[{spec['min']}, {spec['max']}]"
                        )
                elif st == "float":
                    if not isinstance(nv, (int, float)):
                        errors.append(f"allowed_change.{key}.{nk}: expected number")
                    elif nv < spec.get("min", 0) or nv > spec.get("max", 999999):
                        errors.append(
                            f"allowed_change.{key}.{nk}: {nv} out of range "
                            f"[{spec['min']}, {spec['max']}]"
                        )
                elif st == "bool":
                    if not isinstance(nv, bool):
                        errors.append(f"allowed_change.{key}.{nk}: expected boolean")
                elif st == "str":
                    enum_vals = spec.get("enum")
                    if enum_vals and nv not in enum_vals:
                        errors.append(f"allowed_change.{key}.{nk}: '{nv}' not in {enum_vals}")
                elif st == "dict":
                    if not isinstance(nv, dict):
                        errors.append(f"allowed_change.{key}.{nk}: expected dict")
                        continue
                    nested_fields = spec.get("fields", {})
                    for dk, dv in nv.items():
                        if dk not in nested_fields:
                            errors.append(f"allowed_change.{key}.{nk}.{dk}: unknown field")
                            continue
                        dspec = nested_fields[dk]
                        dst = dspec.get("type", "")
                        if dst == "int":
                            if not isinstance(dv, int):
                                errors.append(f"allowed_change.{key}.{nk}.{dk}: expected int")
                            elif dv < dspec.get("min", 0) or dv > dspec.get("max", 999999):
                                errors.append(
                                    f"allowed_change.{key}.{nk}.{dk}: {dv} out of range "
                                    f"[{dspec['min']}, {dspec['max']}]"
                                )
                        elif dst == "float":
                            if not isinstance(dv, (int, float)):
                                errors.append(f"allowed_change.{key}.{nk}.{dk}: expected number")
                            elif dv < dspec.get("min", 0) or dv > dspec.get("max", 999999):
                                errors.append(
                                    f"allowed_change.{key}.{nk}.{dk}: {dv} out of range "
                                    f"[{dspec['min']}, {dspec['max']}]"
                                )
                        elif dst == "bool":
                            if not isinstance(dv, bool):
                                errors.append(f"allowed_change.{key}.{nk}.{dk}: expected boolean")
                        elif dst == "dict":
                            if not isinstance(dv, dict):
                                errors.append(f"allowed_change.{key}.{nk}.{dk}: expected dict")
                                continue
                            deep_fields = dspec.get("fields", {})
                            for ek, ev in dv.items():
                                if ek not in deep_fields:
                                    errors.append(
                                        f"allowed_change.{key}.{nk}.{dk}.{ek}: unknown field"
                                    )
                                    continue
                                espec = deep_fields[ek]
                                est = espec.get("type", "")
                                if est == "int":
                                    if not isinstance(ev, int):
                                        errors.append(
                                            f"allowed_change.{key}.{nk}.{dk}.{ek}: expected int"
                                        )
                                    elif ev < espec.get("min", 0) or ev > espec.get(
                                        "max", 999999
                                    ):
                                        errors.append(
                                            f"allowed_change.{key}.{nk}.{dk}.{ek}: "
                                            f"{ev} out of range "
                                            f"[{espec['min']}, {espec['max']}]"
                                        )
                                elif est == "float":
                                    if not isinstance(ev, (int, float)):
                                        errors.append(
                                            f"allowed_change.{key}.{nk}.{dk}.{ek}: "
                                            "expected number"
                                        )
                                    elif ev < espec.get("min", 0) or ev > espec.get(
                                        "max", 999999
                                    ):
                                        errors.append(
                                            f"allowed_change.{key}.{nk}.{dk}.{ek}: "
                                            f"{ev} out of range "
                                            f"[{espec['min']}, {espec['max']}]"
                                        )
                                elif est == "bool":
                                    if not isinstance(ev, bool):
                                        errors.append(
                                            f"allowed_change.{key}.{nk}.{dk}.{ek}: "
                                            "expected boolean"
                                        )
        # Check flat fields
        elif key in fd.allowed_change:
            spec = fd.allowed_change[key]
            st = spec.get("type", "")
            if st == "int" and not isinstance(value, int):
                errors.append(f"allowed_change.{key}: expected int")
            elif st == "int" and (value < spec.get("min", 0) or value > spec.get("max", 999999)):
                errors.append(
                    f"allowed_change.{key}: {value} out of range [{spec['min']}, {spec['max']}]"
                )
            elif st == "float" and not isinstance(value, (int, float)):
                errors.append(f"allowed_change.{key}: expected number")
            elif st == "float" and (value < spec.get("min", 0) or value > spec.get("max", 999999)):
                errors.append(
                    f"allowed_change.{key}: {value} out of range [{spec['min']}, {spec['max']}]"
                )
        else:
            errors.append(f"allowed_change.{key}: unknown field for family '{family}'")

    return errors


EXIT_LOGIC_VARIANT = FamilyDefinition(
    name="exit_logic_variant",
    description="Channel breakout with configurable exit logic (TP, SL, max hold, trailing stop)",
    schema_file="exit_logic_variant_v0.8.json",
    strategy_type="exit_logic_channel_breakout",
    strategy_params={
        "entry_lookback": ParamDef(int, 20, 1000),
        "min_hold_bars": ParamDef(int, 12, 1440),
        "enable_long": ParamDef(bool),
        "enable_short": ParamDef(bool),
    },
    allowed_change={
        "entry_lookback": {"type": "int", "min": 20, "max": 1000},
        "min_hold_bars": {"type": "int", "min": 12, "max": 1440},
    },
    allowed_change_nested={
        "regime_filter": {
            "fast_days": {"type": "int", "min": 5, "max": 200},
            "slow_days": {"type": "int", "min": 10, "max": 500},
        },
        "exit_logic": {
            "exit_lookback": {"type": "int", "min": 12, "max": 1440, "allowed_values": [0]},
            "take_profit_pct": {"type": "float", "min": 0.01, "max": 0.50},
            "stop_loss_pct": {"type": "float", "min": 0.01, "max": 0.30},
            "max_hold_bars": {"type": "int", "min": 12, "max": 2880},
            "trailing_stop": {"type": "bool"},
            "profit_lock": {
                "type": "dict",
                "fields": {
                    "enabled": {"type": "bool"},
                    "activate_profit_pct": {"type": "float", "min": 0.0, "max": 0.50},
                    "giveback_ratio": {"type": "float", "min": 0.0, "max": 1.0},
                    "trailing_atr_multiplier": {"type": "float", "min": 0.0, "max": 10.0},
                    "atr_period": {"type": "int", "min": 2, "max": 100},
                    "min_hold_bars_before_lock": {"type": "int", "min": 0, "max": 2880},
                },
            },
            "profit_lock_by_regime": {
                "type": "dict",
                "fields": {
                    "bull": {
                        "type": "dict",
                        "fields": {
                            "enabled": {"type": "bool"},
                            "activate_profit_pct": {"type": "float", "min": 0.0, "max": 0.50},
                            "giveback_ratio": {"type": "float", "min": 0.0, "max": 1.0},
                            "trailing_atr_multiplier": {
                                "type": "float",
                                "min": 0.0,
                                "max": 10.0,
                            },
                            "atr_period": {"type": "int", "min": 2, "max": 100},
                            "min_hold_bars_before_lock": {
                                "type": "int",
                                "min": 0,
                                "max": 2880,
                            },
                        },
                    },
                    "bear": {
                        "type": "dict",
                        "fields": {
                            "enabled": {"type": "bool"},
                            "activate_profit_pct": {"type": "float", "min": 0.0, "max": 0.50},
                            "giveback_ratio": {"type": "float", "min": 0.0, "max": 1.0},
                            "trailing_atr_multiplier": {
                                "type": "float",
                                "min": 0.0,
                                "max": 10.0,
                            },
                            "atr_period": {"type": "int", "min": 2, "max": 100},
                            "min_hold_bars_before_lock": {
                                "type": "int",
                                "min": 0,
                                "max": 2880,
                            },
                        },
                    },
                    "neutral": {
                        "type": "dict",
                        "fields": {
                            "enabled": {"type": "bool"},
                            "activate_profit_pct": {"type": "float", "min": 0.0, "max": 0.50},
                            "giveback_ratio": {"type": "float", "min": 0.0, "max": 1.0},
                            "trailing_atr_multiplier": {
                                "type": "float",
                                "min": 0.0,
                                "max": 10.0,
                            },
                            "atr_period": {"type": "int", "min": 2, "max": 100},
                            "min_hold_bars_before_lock": {
                                "type": "int",
                                "min": 0,
                                "max": 2880,
                            },
                        },
                    },
                },
            },
            "mature_trend_exit": {
                "type": "dict",
                "fields": {
                    "enabled": {"type": "bool"},
                    "activate_mfe_pct": {"type": "float", "min": 0.0, "max": 5.0},
                    "daily_ema_period": {"type": "int", "min": 2, "max": 200},
                    "use_completed_daily_bar": {"type": "bool"},
                    "min_hold_bars_before_exit": {"type": "int", "min": 0, "max": 2880},
                },
            },
            "bear_cooldown": {
                "type": "dict",
                "fields": {
                    "enabled": {"type": "bool"},
                    "loss_streak": {"type": "int", "min": 1, "max": 10},
                    "cooldown_bars": {"type": "int", "min": 1, "max": 2880},
                    "cooldown_days": {"type": "int", "min": 1, "max": 30},
                },
            },
        },
    },
    search_space_text=(
        "entry_lookback: [20, 1000], default 375\n"
        "min_hold_bars: [12, 1440], default 432\n"
        "exit_logic.exit_lookback: 0(off) or [12, 1440], default 288\n"
        "exit_logic.take_profit_pct: [0.01, 0.50], default 0.05\n"
        "exit_logic.stop_loss_pct: [0.01, 0.30], default 0.10\n"
        "exit_logic.max_hold_bars: [12, 2880], default 720\n"
        "exit_logic.trailing_stop: true | false, default true\n"
        "exit_logic.profit_lock.enabled: true | false, default false\n"
        "exit_logic.profit_lock.activate_profit_pct: [0.0, 0.50], default 0.08\n"
        "exit_logic.profit_lock.giveback_ratio: [0.0, 1.0], default 0.35\n"
        "exit_logic.profit_lock.trailing_atr_multiplier: [0.0, 10.0], default 3.0\n"
        "exit_logic.profit_lock_by_regime.{bull,bear,neutral}: optional overrides\n"
        "exit_logic.mature_trend_exit.activate_mfe_pct: [0.0, 5.0], default 1.20\n"
        "exit_logic.mature_trend_exit.daily_ema_period: [2, 200], default 20\n"
        "exit_logic.bear_cooldown.loss_streak: [1, 10], default 2\n"
        "exit_logic.bear_cooldown.cooldown_bars: [1, 2880], default 864\n"
        "regime_filter.fast_days: [5, 200], default 50\n"
        "regime_filter.slow_days: [10, 500], default 200"
    ),
)


VOLATILITY_GATE = FamilyDefinition(
    name="volatility_gate",
    description="Volatility gate filter — blocks entries during high-volatility periods",
    schema_file="volatility_gate_v0.1.json",
    strategy_type="volatility_gate_filter",
    strategy_params={
        "metric": ParamDef(str, enum=["atr_close_ratio", "atr_pct", "bb_width", "keltner_width"]),
        "threshold": ParamDef(float, 0.01, 0.20),
        "action": ParamDef(
            str,
            enum=[
                "block_entries_when_high_vol",
                "block_entries_when_low_vol",
                "reduce_position_size",
                "block_short_entries_when_high_vol",
                "block_long_entries_when_high_vol",
            ],
        ),
        "lookback": ParamDef(int, 12, 288),
    },
    allowed_change={},
    allowed_change_nested={},
    search_space_text=(
        "volatility_gate filter: applied on top of base strategy\n"
        "metric: atr_close_ratio | atr_pct | bb_width | keltner_width\n"
        "threshold: [0.01, 0.20], default 0.06\n"
        "action: block_entries_when_high_vol | block_entries_when_low_vol | reduce_position_size\n"
        "lookback: [12, 288], default 48"
    ),
    extra_fields=[],
)


COOLDOWN_AFTER_DRAWDOWN = FamilyDefinition(
    name="cooldown_after_drawdown",
    description="Drawdown cooldown — blocks new entries when price drawdown exceeds threshold",
    schema_file="cooldown_after_drawdown_v0.1.json",
    strategy_type="cooldown_after_drawdown_filter",
    strategy_params={
        "metric": ParamDef(str, enum=["close_drawdown"]),
        "threshold": ParamDef(float, 0.05, 0.30),
        "lookback": ParamDef(int, 288, 4032),
        "cooldown_bars": ParamDef(int, 24, 576),
        "action": ParamDef(str, enum=["block_entries_during_cooldown"]),
    },
    allowed_change={},
    allowed_change_nested={},
    search_space_text=(
        "cooldown_after_drawdown filter: blocks entries when drawdown > threshold\n"
        "metric: close_drawdown\n"
        "threshold: [0.05, 0.30], default 0.15\n"
        "lookback: [288, 4032], default 2016\n"
        "cooldown_bars: [24, 576], default 288\n"
        "action: block_entries_during_cooldown"
    ),
    extra_fields=[],
)


NEUTRAL_REGIME_ENTRY_BLOCK = FamilyDefinition(
    name="neutral_regime_entry_block",
    description="Blocks new entries during NEUTRAL regime — reduces low-conviction trading",
    schema_file="neutral_regime_entry_block_v0.1.json",
    strategy_type="neutral_regime_entry_block_filter",
    strategy_params={
        "metric": ParamDef(str, enum=["regime_label"]),
        "action": ParamDef(
            str,
            enum=[
                "block_entries_when_neutral",
                "block_short_entries_when_neutral",
                "block_long_entries_when_neutral",
            ],
        ),
    },
    allowed_change={},
    allowed_change_nested={},
    search_space_text=(
        "neutral_regime_entry_block filter: blocks entries during NEUTRAL regime\n"
        "metric: regime_label\n"
        "action: block_entries_when_neutral | block_short_entries_when_neutral | block_long_entries_when_neutral"
    ),
    extra_fields=[],
)


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------

register(CHANNEL_BREAKOUT)
register(VOLATILITY_FILTERED_BREAKOUT)
register(EXIT_LOGIC_VARIANT)
register(VOLATILITY_GATE)
register(COOLDOWN_AFTER_DRAWDOWN)
register(NEUTRAL_REGIME_ENTRY_BLOCK)
