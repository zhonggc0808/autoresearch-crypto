#!/usr/bin/env python3
"""Test suite for v0.9b Codegen Evaluation Adapter.

Tests:
    - Valid codegen candidate -> evaluation -> scorecard
    - Invalid code rejected before evaluation (exit 2)
    - generate_signals error -> evaluation_error (exit 3)
    - Verdict always codegen_research_only
    - promotion_eligible always false
    - Oracle utility signature smoke test
    - Dry-run writes nothing
    - Fee sensitivity and rolling metrics populated
    - Exit codes: 0=success, 1=input_error, 2=rejected, 3=eval_error

Run:
    uv run pytest tests/test_codegen_evaluation_v09b.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Sample strategy code
# ---------------------------------------------------------------------------

VALID_STRATEGY_CODE = """\
import numpy as np
import pandas as pd

def generate_signals(df):
    close = df['close'].values
    fast_period = 20
    slow_period = 50

    fast_ema = np.full(len(df), np.nan)
    slow_ema = np.full(len(df), np.nan)
    fast_ema[0] = close[0]
    slow_ema[0] = close[0]

    for i in range(1, len(df)):
        k_fast = 2.0 / (fast_period + 1)
        k_slow = 2.0 / (slow_period + 1)
        fast_ema[i] = close[i] * k_fast + fast_ema[i-1] * (1 - k_fast)
        slow_ema[i] = close[i] * k_slow + slow_ema[i-1] * (1 - k_slow)

    signals = np.zeros(len(df), dtype=np.int8)
    for i in range(1, len(df)):
        if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]):
            continue
        if fast_ema[i] > slow_ema[i] and fast_ema[i-1] <= slow_ema[i-1]:
            signals[i] = 2
        elif fast_ema[i] < slow_ema[i] and fast_ema[i-1] >= slow_ema[i-1]:
            signals[i] = 3
        else:
            signals[i] = 1
    return signals
"""

CODE_WITH_FORBIDDEN_IMPORT = """\
import os
import numpy as np

def generate_signals(df):
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_BROKEN_SIGNALS = """\
import numpy as np

def generate_signals(df):
    # Pass interface validation (synthetic 200 bars) but fail at
    # evaluation (mock 2000 bars). ZeroDivisionError is a builtin
    # that works in the restricted sandbox without imports.
    if len(df) > 500:
        1 / 0  # ZeroDivisionError
    return np.zeros(len(df), dtype=np.int8)
"""

MOCK_MANIFEST: Dict[str, Any] = {
    "strategy_name": "ema_cross_test",
    "description": "Test strategy for v0.9b evaluation adapter",
    "hypothesis": "Test hypothesis -- metrics are synthetic.",
    "params": {"fast_period": 20, "slow_period": 50},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def codegen_dir():
    """Create a temporary codegen candidate directory with valid strategy."""
    tmpdir = Path(tempfile.mkdtemp())
    strategy_path = tmpdir / "strategy.py"
    manifest_path = tmpdir / "manifest.json"
    strategy_path.write_text(VALID_STRATEGY_CODE, encoding="utf-8")
    manifest_path.write_text(json.dumps(MOCK_MANIFEST, indent=2), encoding="utf-8")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def codegen_dir_forbidden():
    """Codegen directory with forbidden import (os)."""
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "strategy.py").write_text(CODE_WITH_FORBIDDEN_IMPORT, encoding="utf-8")
    (tmpdir / "manifest.json").write_text(json.dumps(MOCK_MANIFEST, indent=2), encoding="utf-8")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def codegen_dir_broken():
    """Codegen directory with broken generate_signals."""
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "strategy.py").write_text(CODE_BROKEN_SIGNALS, encoding="utf-8")
    (tmpdir / "manifest.json").write_text(json.dumps(MOCK_MANIFEST, indent=2), encoding="utf-8")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def codegen_dir_no_manifest():
    """Codegen directory missing manifest.json."""
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "strategy.py").write_text(VALID_STRATEGY_CODE, encoding="utf-8")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run_eval(dir_path: Path, **kwargs) -> Dict[str, Any]:
    """Helper to call evaluate_codegen_candidate with a dir path."""
    from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

    return evaluate_codegen_candidate(
        dir_path=dir_path,
        **kwargs,
    )


# ===================================================================
# Tests: Flags
# ===================================================================


class TestCodegenFlags:
    """Verify codegen-specific scorecard flags."""

    def test_trade_frequency_cap_rejects_high_turnover(self):
        from scripts.evaluate_codegen_candidate import _compute_flags

        metrics = {
            "is": {"raw": {"dd": -0.10, "trades_per_year": 301}},
            "oos": {"raw": {"dd": -0.10}},
            "rolling": {"12m_min_return": 0.01},
        }

        flags = _compute_flags(metrics)

        assert flags["status"] == "REJECT"
        assert "Trade frequency exceeds 300/yr codegen cap" in flags["disqualifications"]

    def test_trade_frequency_floor_rejects_dead_strategy(self):
        from scripts.evaluate_codegen_candidate import _compute_flags

        metrics = {
            "is": {"raw": {"dd": -0.10, "trades_per_year": 0}},
            "oos": {"raw": {"dd": -0.10}},
            "rolling": {"12m_min_return": 0.0},
        }

        flags = _compute_flags(metrics)

        assert flags["status"] == "REJECT"
        assert "Trade frequency below 20/yr codegen floor" in flags["disqualifications"]


# ===================================================================
# Tests: Happy path
# ===================================================================


class TestHappyPath:
    """Verify the full evaluation flow with a valid strategy."""

    def test_valid_candidate_evaluates(self, codegen_dir):
        """Full happy path: mock codegen -> evaluation -> scorecard."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success", (
            f"Expected success, got {result['status']}: {result.get('error', '')}"
        )
        assert result["exit_code"] == 0
        assert result["scorecard_path"] is not None

        # Verify scorecard file exists
        sc_path = Path(result["scorecard_path"])
        assert sc_path.exists()

        # Verify scorecard content
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["experiment_id"] == codegen_dir.name
        assert scorecard["verdict"]["label"] == "codegen_research_only"
        assert scorecard["promotion_eligible"] is False
        assert scorecard["promotion_block_reason"] == "CODEGEN_RESEARCH_ONLY"

        # Verify metrics
        ms = scorecard.get("metrics_summary", {})
        assert ms.get("is_return") is not None
        assert ms.get("oos_return") is not None

        # Cleanup
        sc_path.unlink()

    def test_fee_sensitivity_computed(self, codegen_dir):
        """Fee sensitivity in result has expected fee levels."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"
        metrics = result.get("metrics", {})
        fee_is = metrics.get("sensitivity", {}).get("is", {}).get("fees", {})
        fee_oos = metrics.get("sensitivity", {}).get("oos", {}).get("fees", {})

        assert len(fee_is) > 0, "IS fee sensitivity should not be empty"
        assert len(fee_oos) > 0, "OOS fee sensitivity should not be empty"

        # Check for known fee level keys (format: "0bp", "2bp", "4bp", "10bp")
        for fee_dict in [fee_is, fee_oos]:
            for bps in [0, 2, 4, 10]:
                key = f"{bps}bp"
                assert key in fee_dict, f"Missing fee level {key} in {fee_dict}"

        # Cleanup scorecard
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()

    def test_rolling_metrics_computed(self, codegen_dir):
        """Rolling min return fields are populated for both 6m and 12m."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"
        metrics = result.get("metrics", {})
        rolling = metrics.get("rolling", {})

        assert "6m_min_return" in rolling, "Missing 6m rolling metric"
        assert "12m_min_return" in rolling, "Missing 12m rolling metric"

        # Cleanup scorecard
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()

    def test_execution_parity_both_is_and_oos(self, codegen_dir):
        """Execution parity is computed for both IS and OOS.

        execution_parity (float, OOS) matches oracle format for score_candidate.
        execution_parity_detail has per-window breakdown.
        """
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"
        metrics = result.get("metrics", {})

        # Main parity field must be a float (matching oracle format)
        ep_main = metrics.get("execution_parity")
        assert isinstance(ep_main, float), f"execution_parity should be float, got {type(ep_main)}"

        # Detail field has per-window breakdown
        ep_detail = metrics.get("execution_parity_detail", {})
        assert "is" in ep_detail, "Missing IS execution parity detail"
        assert "oos" in ep_detail, "Missing OOS execution parity detail"
        assert isinstance(ep_detail["is"], float)
        assert isinstance(ep_detail["oos"], float)

        # Cleanup scorecard
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()

    def test_manifest_updated_after_evaluation(self, codegen_dir):
        """Manifest.json is updated with evaluation_status after success."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"

        manifest_path = codegen_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest.get("evaluation_status") == "evaluated"
        assert "evaluated_at" in manifest
        assert "scorecard_path" in manifest

        # Cleanup scorecard
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()


# ===================================================================
# Tests: Rejection & error handling
# ===================================================================


class TestRejectionAndErrors:
    """Verify invalid candidates are rejected before evaluation."""

    def test_invalid_code_rejected_before_evaluation(self, codegen_dir_forbidden):
        """Forbidden import -> Phase 1 rejection, exit code 2, no evaluation."""
        result = _run_eval(codegen_dir_forbidden, mock_eval=True)
        assert result["status"] == "rejected", f"Expected rejection, got {result['status']}"
        assert result["exit_code"] == 2
        assert len(result.get("errors", [])) > 0

    def test_generate_signals_error_handled(self, codegen_dir_broken):
        """Broken generate_signals -> evaluation_error, exit code 3."""
        result = _run_eval(codegen_dir_broken, mock_eval=True)
        assert result["status"] == "evaluation_error", (
            f"Expected evaluation_error, got {result['status']}"
        )
        assert result["exit_code"] == 3

    def test_input_error_missing_dir(self):
        """Non-existent directory -> exit code 1."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

        result = evaluate_codegen_candidate(
            dir_path=Path("/nonexistent/path/for/testing"),
        )
        assert result["status"] == "input_error"
        assert result["exit_code"] == 1

    def test_input_error_missing_manifest(self, codegen_dir_no_manifest):
        """Missing manifest.json -> exit code 1."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

        result = evaluate_codegen_candidate(
            dir_path=codegen_dir_no_manifest,
        )
        assert result["status"] == "input_error"
        assert result["exit_code"] == 1


# ===================================================================
# Tests: Verdict override
# ===================================================================


class TestVerdictOverride:
    """Verify verdict is always codegen_research_only regardless of metrics."""

    def test_verdict_always_codegen_research_only(self, codegen_dir):
        """Verdict label forced to codegen_research_only."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"

        sc_path = Path(result["scorecard_path"])
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["verdict"]["label"] == "codegen_research_only"
        assert "promotion" not in scorecard["verdict"]["label"].lower()

        # Cleanup
        sc_path.unlink()

    def test_promotion_eligible_false(self, codegen_dir):
        """promotion_eligible=False and block_reason set."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"

        sc_path = Path(result["scorecard_path"])
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["promotion_eligible"] is False
        assert scorecard["promotion_block_reason"] == "CODEGEN_RESEARCH_ONLY"

        # Cleanup
        sc_path.unlink()

    def test_original_verdict_preserved(self, codegen_dir):
        """Original verdict preserved in _original_verdict for debugging."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"

        sc_path = Path(result["scorecard_path"])
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert "_original_verdict" in scorecard
        orig = scorecard["_original_verdict"]
        assert "label" in orig
        assert orig["label"] != "codegen_research_only", (
            f"Original verdict should differ from override, got {orig['label']}"
        )

        # Cleanup
        sc_path.unlink()


# ===================================================================
# Tests: Dry-run
# ===================================================================


class TestDryRun:
    """Verify --dry-run writes nothing."""

    def test_dry_run_no_writes(self, codegen_dir):
        """--dry-run writes nothing to disk (no files, no manifest update)."""
        # Snapshot codegen_scorecards before
        scorecards_dir = PROJECT_DIR / "research_workspace" / "codegen_scorecards"
        files_before = set()
        if scorecards_dir.exists():
            files_before = set(p.name for p in scorecards_dir.iterdir() if p.is_file())

        result = _run_eval(codegen_dir, dry_run=True, mock_eval=True)
        assert result["status"] == "dry_run"
        assert result["exit_code"] == 0
        assert result.get("scorecard_path") is None

        # Verify no new scorecard files
        files_after = set()
        if scorecards_dir.exists():
            files_after = set(p.name for p in scorecards_dir.iterdir() if p.is_file())
        assert files_after == files_before, (
            f"Dry-run should not create scorecard files. "
            f"Before: {files_before}, After: {files_after}"
        )

        # Verify manifest NOT updated
        manifest_path = codegen_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "evaluation_status" not in manifest, "Dry-run should not update manifest"


# ===================================================================
# Tests: Oracle utility signature smoke test
# ===================================================================


class TestOracleUtilitySignatures:
    """Verify imported private oracle functions have expected signatures.

    This catches silent interface breakage when oracle internals change.
    If this test fails, the oracle utility API has changed and the adapter
    needs to be updated — it should NOT silently accept different semantics.
    """

    def test_find_eth_data_signature(self):
        import inspect

        from scripts.research_oracle import _find_eth_data

        sig = inspect.signature(_find_eth_data)
        params = list(sig.parameters.keys())
        assert len(params) == 0, f"_find_eth_data should take no args, got: {params}"

    def test_evaluate_signals_signature(self):
        import inspect

        from scripts.research_oracle import _evaluate_signals

        sig = inspect.signature(_evaluate_signals)
        params = list(sig.parameters.keys())
        assert "signals" in params, f"_evaluate_signals needs 'signals' param, got: {params}"
        assert "prices" in params, f"_evaluate_signals needs 'prices' param, got: {params}"

    def test_safe_execution_signals_signature(self):
        import inspect

        from scripts.research_oracle import _safe_execution_signals

        sig = inspect.signature(_safe_execution_signals)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, f"_safe_execution_signals needs at least 1 param, got: {params}"

    def test_compute_rolling_metrics_signature(self):
        import inspect

        from scripts.research_oracle import _compute_rolling_metrics

        sig = inspect.signature(_compute_rolling_metrics)
        params = list(sig.parameters.keys())
        assert "signals" in params
        assert "prices" in params
        assert "window_months" in params

    def test_compute_fee_sensitivity_signature(self):
        import inspect

        from scripts.research_oracle import _compute_fee_sensitivity

        sig = inspect.signature(_compute_fee_sensitivity)
        params = list(sig.parameters.keys())
        assert "signals" in params
        assert "prices" in params


# ===================================================================
# Test: No oracle core modifications
# ===================================================================


class TestNoOracleModifications:
    """Verify that oracle core files remain unchanged post-evaluation."""

    def test_no_modifications_to_oracle_core(self, codegen_dir):
        """Run evaluation and verify oracle files haven't changed."""
        import hashlib

        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

        # Hash oracle files before
        oracle_path = PROJECT_DIR / "scripts" / "research_oracle.py"
        hash_before = hashlib.sha256(oracle_path.read_bytes()).hexdigest()

        # Run evaluation
        result = evaluate_codegen_candidate(
            dir_path=codegen_dir,
            mock_eval=True,
        )
        assert result["status"] == "success"

        # Hash oracle files after
        hash_after = hashlib.sha256(oracle_path.read_bytes()).hexdigest()

        assert hash_before == hash_after, "research_oracle.py was modified by codegen evaluation!"

        # Cleanup
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()


# ===================================================================
# Test: Exit code correctness
# ===================================================================


class TestExitCodes:
    """Verify each failure mode returns the correct exit code."""

    def test_exit_code_0_on_success(self, codegen_dir):
        """Exit code 0 when evaluation succeeds."""
        result = _run_eval(codegen_dir, mock_eval=True)
        assert result["status"] == "success"
        assert result["exit_code"] == 0

        # Cleanup
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()

    def test_exit_code_1_on_input_error(self):
        """Exit code 1 for missing directory."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

        result = evaluate_codegen_candidate(
            codegen_id="nonexistent_exp_9999",
        )
        assert result["status"] == "input_error"
        assert result["exit_code"] == 1

    def test_exit_code_2_on_rejection(self, codegen_dir_forbidden):
        """Exit code 2 when candidate rejected at Phase 1."""
        result = _run_eval(codegen_dir_forbidden, mock_eval=True)
        assert result["status"] == "rejected"
        assert result["exit_code"] == 2

    def test_exit_code_3_on_evaluation_error(self, codegen_dir_broken):
        """Exit code 3 when evaluation fails."""
        result = _run_eval(codegen_dir_broken, mock_eval=True)
        assert result["status"] == "evaluation_error"
        assert result["exit_code"] == 3

    def test_dry_run_exit_code_0(self, codegen_dir):
        """Dry-run returns exit code 0."""
        result = _run_eval(codegen_dir, dry_run=True, mock_eval=True)
        assert result["status"] == "dry_run"
        assert result["exit_code"] == 0
