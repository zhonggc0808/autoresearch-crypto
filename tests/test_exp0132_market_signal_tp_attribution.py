from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXP0132 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution.py"


def load_exp0132():
    spec = importlib.util.spec_from_file_location("exp0132_market_signal_tp_attribution", EXP0132)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exp0132_market_signal_tp_attribution"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classify_exit_saved_and_missed() -> None:
    exp0132 = load_exp0132()

    assert exp0132.classify_exit(base_pnl=-100.0, early_exit_pnl_proxy=50.0) == "saved_loser"
    assert exp0132.classify_exit(base_pnl=100.0, early_exit_pnl_proxy=150.0) == "improved_winner"
    assert exp0132.classify_exit(base_pnl=100.0, early_exit_pnl_proxy=40.0) == "missed_winner"
    assert exp0132.classify_exit(base_pnl=-100.0, early_exit_pnl_proxy=-150.0) == "worsened_loser"


def test_classify_variant_buckets() -> None:
    exp0132 = load_exp0132()

    assert exp0132.classify_variant(pd.Series({"pass_gate": True, "live_hit": False})) == (
        "A_long_pass_no_live",
        "OBSERVE_REVIEW_EXITS",
    )
    assert exp0132.classify_variant(pd.Series({"pass_gate": False, "live_hit": True})) == (
        "B_live_hit_rejected_for_live",
        "REJECTED_FOR_LIVE",
    )
    assert exp0132.classify_variant(
        pd.Series({"pass_gate": False, "live_hit": False, "exits": 63, "top20_winner_exits": 15})
    ) == ("C_overtrigger_or_winner_damage", "REJECT")
    assert exp0132.classify_variant(
        pd.Series({"pass_gate": False, "live_hit": False, "exits": 1, "top20_winner_exits": 0})
    ) == ("D_low_signal_or_neutral", "REJECT_OR_IGNORE")
