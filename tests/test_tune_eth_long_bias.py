from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.tune_eth_long_bias import CandidateResult, build_checkpoint, passes_benchmark_gate


ROOT = Path(__file__).resolve().parents[1]


def test_passes_benchmark_gate_requires_edge_and_minimum_trades() -> None:
    candidate = CandidateResult(
        name="candidate",
        strategy="trend_follow",
        params={"enable_short": True},
        total_return=0.055,
        sharpe=1.0,
        max_drawdown=-0.03,
        win_rate=0.55,
        trades=8,
        score=0.1,
    )

    assert passes_benchmark_gate(candidate, benchmark_return=0.04, min_edge=0.005, min_trades=5)
    assert not passes_benchmark_gate(
        candidate, benchmark_return=0.052, min_edge=0.005, min_trades=5
    )
    assert not passes_benchmark_gate(
        candidate, benchmark_return=0.04, min_edge=0.005, min_trades=10
    )


def test_build_checkpoint_preserves_position_mode() -> None:
    candidate = CandidateResult(
        name="candidate",
        strategy="trend_follow",
        params={"enable_short": True, "long_ma_period": 100},
        total_return=0.05,
        sharpe=1.2,
        max_drawdown=-0.03,
        win_rate=0.6,
        trades=9,
        score=0.2,
    )

    checkpoint = build_checkpoint(candidate, benchmark_return=0.04, source="unit-test")

    assert checkpoint["strategy"] == "trend_follow"
    assert checkpoint["params"]["enable_short"] is True
    assert checkpoint["params"]["long_ma_period"] == 100
    assert checkpoint["benchmark"]["buy_hold_return"] == 0.04


def test_script_can_run_directly() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/tune_eth_long_bias.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
