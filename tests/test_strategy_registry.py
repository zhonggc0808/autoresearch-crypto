from __future__ import annotations


def test_strategy_registry_exports_documented_strategies() -> None:
    from dex.strategies import MultiTFEnsembleStrategy, PureActionV2Strategy

    assert PureActionV2Strategy.__name__ == "PureActionV2Strategy"
    assert MultiTFEnsembleStrategy.__name__ == "MultiTFEnsembleStrategy"
