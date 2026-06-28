from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0137 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0137_cross_asset_core_donchian_sanity.py"


def load_exp0137():
    spec = importlib.util.spec_from_file_location("exp0137_cross_asset", EXP0137)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0137_cross_asset"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_select_longest_5m_file_prefers_largest_dated_window(tmp_path: Path) -> None:
    exp0137 = load_exp0137()
    (tmp_path / "BTCUSDT_5m_365d.parquet").touch()
    (tmp_path / "BTCUSDT_5m_1300d.parquet").touch()
    (tmp_path / "BTCUSDT_5m.parquet").touch()

    selected = exp0137.select_longest_5m_file("BTCUSDT", tmp_path)

    assert selected.name == "BTCUSDT_5m_1300d.parquet"


def test_gate_requires_dd_above_threshold_and_positive_oos() -> None:
    exp0137 = load_exp0137()

    assert exp0137.gate_flags(-0.849, 0.001)["passes_sanity"] is True
    assert exp0137.gate_flags(-0.85, 0.001)["passes_sanity"] is False
    assert exp0137.gate_flags(-0.80, 0.0)["passes_sanity"] is False
    assert exp0137.gate_flags(-0.80, -0.001)["passes_sanity"] is False


def test_split_time_uses_fixed_70_30_ratio() -> None:
    exp0137 = load_exp0137()
    periods = exp0137.ENTRY_LOOKBACK + exp0137.MIN_HOLD_BARS + 100
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=periods, freq="5min"),
            "open": range(periods),
            "high": range(periods),
            "low": range(periods),
            "close": range(periods),
            "volume": range(periods),
        }
    )

    split_idx, split_time = exp0137.split_time_for(df)

    assert split_idx == int(periods * 0.70)
    assert split_time == df.loc[split_idx, "datetime"]
