from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0139 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0139_v22_risk_based_position_sizing_diagnostic.py"


def load_exp0139():
    spec = importlib.util.spec_from_file_location("exp0139_risk_sizing", EXP0139)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0139_risk_sizing"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def ohlcv(
    close: list[float],
    *,
    open_: list[float] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=len(values), freq="5min"),
            "open": np.asarray(open_ if open_ is not None else close, dtype=float),
            "high": np.asarray(high if high is not None else values + 1.0, dtype=float),
            "low": np.asarray(low if low is not None else values - 1.0, dtype=float),
            "close": values,
            "volume": np.full(len(values), 100.0),
        }
    )


def test_shifted_atr_excludes_current_bar_range() -> None:
    exp0139 = load_exp0139()
    df = ohlcv(
        [100.0, 100.0, 100.0, 100.0, 100.0],
        high=[101.0, 101.0, 101.0, 200.0, 101.0],
        low=[99.0, 99.0, 99.0, 50.0, 99.0],
    )

    atr = exp0139.shifted_atr(df, window=2)

    assert atr.iloc[3] == pytest.approx(2.0)
    assert atr.iloc[4] == pytest.approx(76.0)


def test_shifted_realized_vol_excludes_current_return() -> None:
    exp0139 = load_exp0139()
    df = ohlcv([100.0, 101.0, 102.0, 200.0, 201.0])
    returns = pd.Series(df["close"], dtype=float).pct_change()
    expected = returns.rolling(2, min_periods=2).std().shift(1) * np.sqrt(exp0139.BARS_PER_YEAR)

    realized = exp0139.shifted_realized_vol(df, window=2)

    assert realized.iloc[3] == pytest.approx(expected.iloc[3])
    assert realized.iloc[3] < realized.iloc[4]


def test_entry_size_decision_uses_next_open_size_value() -> None:
    exp0139 = load_exp0139()
    df = ohlcv([100.0, 101.0, 102.0, 103.0])
    signals = np.asarray([2, 2, 0, 1], dtype=int)
    sizes = np.asarray([0.40, 0.60, 0.80, 1.00], dtype=float)
    spec = exp0139.RiskVariantSpec("test", "risk_based_candidate", "vol_target_20d")

    decisions = exp0139.make_entry_size_decisions(df, signals, sizes)
    _, _, events, exposure = exp0139.simulate_entry_fixed_sizing(
        spec,
        df,
        signals,
        sizes,
        commission=0.0,
        slippage=0.0,
    )
    opens = [event for event in events if event["event"] == "open"]

    assert decisions[0]["size"] == pytest.approx(0.60)
    assert opens[0]["step"] == 1
    assert opens[0]["size"] == pytest.approx(0.60)
    assert exposure[1] == pytest.approx(0.60)


def test_clipped_inverse_size_uses_one_for_missing_and_clip_bounds() -> None:
    exp0139 = load_exp0139()
    feature = pd.Series([np.nan, 0.05, 0.10, 0.20, 0.50], dtype=float)

    sizes = exp0139.clipped_inverse_size(feature, ref=0.10, clip_min=0.40, clip_max=1.00)

    assert sizes.tolist() == pytest.approx([1.0, 1.0, 1.0, 0.5, 0.4])


def test_constant_baseline_matches_exp0138_simple_path() -> None:
    exp0139 = load_exp0139()
    df = ohlcv([100.0, 101.0, 102.0, 103.0, 104.0])
    signals = np.asarray([2, 2, 0, 1, 1], dtype=int)
    spec = exp0139.RiskVariantSpec("baseline_100", "baseline_reference", "constant", constant_size=1.0)

    custom_equity, custom_trades, _, _ = exp0139.simulate_entry_fixed_sizing(
        spec,
        df,
        signals,
        np.ones(len(df)),
    )
    base_equity, base_trades, _, _ = exp0139.exp0138.simulate_sizing(exp0139.exp0138.VARIANTS[0], df, signals)

    assert custom_equity.tolist() == pytest.approx(base_equity.tolist())
    assert custom_trades[-1]["pnl"] == pytest.approx(base_trades[-1]["pnl"])


def test_stage0_blocks_atr_pass_when_high_atr_owns_top_winners() -> None:
    exp0139 = load_exp0139()

    def row(bucket: str, *, avg: float, med: float, worst: int, top_share: float) -> dict[str, float | int | str]:
        return {
            "bucket": bucket,
            "trade_count": 10,
            "avg_trade_return": avg,
            "median_trade_return": med,
            "worst20_loser_count": worst,
            "top20_winner_pnl_share": top_share,
        }

    atr_rows = [
        row("Q1", avg=0.10, med=0.08, worst=1, top_share=0.10),
        row("Q2", avg=0.09, med=0.07, worst=1, top_share=0.10),
        row("Q3", avg=0.08, med=0.06, worst=1, top_share=0.10),
        row("Q4", avg=0.04, med=0.01, worst=6, top_share=0.05),
        row("Q5", avg=-0.02, med=-0.01, worst=6, top_share=0.65),
    ]
    vol_rows = [row(f"Q{i}", avg=0.01, med=0.01, worst=1, top_share=0.10) for i in range(1, 6)]

    flags = exp0139.stage0_flags(atr_rows, vol_rows)

    assert flags["high_atr_quality_worse"] is True
    assert flags["high_atr_worst_concentrated"] is True
    assert flags["high_atr_topwinner_risk"] is True
    assert flags["atr_stage0_pass"] is False


def test_skipped_v2_gate_row_preserves_stage0_reason() -> None:
    exp0139 = load_exp0139()
    baseline = {
        "name": "baseline_100",
        "role": "baseline_reference",
        "max_dd": -0.50,
        "oos_return": 6.0,
        "dd_improve_vs_baseline": 0.0,
        "oos_keep_ratio_vs_baseline": 1.0,
        "rolling12_min": -0.20,
        "return_dd_ratio": 12.0,
        "top20_winner_pnl": 100.0,
        "top20_winner_damage_vs_baseline": 0.0,
        "worst20_loser_pnl": -50.0,
        "worst20_improve_vs_baseline": 0.0,
        "fee10_oos_return": 5.0,
        "fee10_dd_improve_vs_baseline": 0.0,
        "skipped": False,
    }
    v3 = {
        **baseline,
        "name": "V3_constant_75",
        "role": "linear_constant_control",
        "return_dd_ratio": 10.0,
    }
    spec = exp0139.RiskVariantSpec(
        "V2_atr_risk_parity_clip_0p4_1p0",
        "risk_based_candidate",
        "atr_risk_parity",
        skipped=True,
        skip_reason="skipped_by_stage0",
    )
    skipped = exp0139.skipped_variant_row(spec, baseline)

    gates = exp0139.stage_gate_rows(
        [baseline, v3, skipped],
        {"atr_stage0_pass": False, "vol_targeting_topwinner_risk": "normal"},
    )

    assert gates[-1]["skipped"] is True
    assert gates[-1]["skip_reason"] == "skipped_by_stage0"
    assert gates[-1]["observe"] is False


def test_verdict_mapping() -> None:
    exp0139 = load_exp0139()

    assert exp0139.verdict_from_gates([{"shadow_candidate": True, "observe": True}]) == "SHADOW_CANDIDATE"
    assert exp0139.verdict_from_gates([{"shadow_candidate": False, "observe": True}]) == "OBSERVE"
    assert exp0139.verdict_from_gates([{"shadow_candidate": False, "observe": False}]) == "REJECT"
