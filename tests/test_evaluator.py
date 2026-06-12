from __future__ import annotations

import numpy as np

from dex.config import BARS_PER_YEAR
from dex.strategies.base import StrategyEvaluator


def test_annualized_return_uses_5m_bars_per_year() -> None:
    evaluator = StrategyEvaluator()
    equity = np.linspace(100.0, 110.0, BARS_PER_YEAR)

    metrics = evaluator.compute_metrics(equity, [])

    assert np.isclose(metrics["total_return"], 0.10)
    assert 0.099 < metrics["annualized_return"] < 0.101
