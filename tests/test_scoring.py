from __future__ import annotations

from dex.scoring import EdgeFlag, risk_adjusted_score


def test_risk_adjusted_score_flags_overfit_results() -> None:
    result = risk_adjusted_score(
        sharpe=2.0,
        total_return=0.20,
        max_drawdown=-0.05,
        win_rate=0.55,
        n_trades=3,
        min_trades=10,
    )

    assert result.score == 0.0
    assert EdgeFlag.OVERFIT in result.flags


def test_risk_adjusted_score_flags_risky_drawdown() -> None:
    result = risk_adjusted_score(
        sharpe=2.0,
        total_return=0.20,
        max_drawdown=-0.35,
        win_rate=0.55,
        n_trades=30,
        max_dd=0.30,
    )

    assert result.score == 0.0
    assert EdgeFlag.RISKY in result.flags
