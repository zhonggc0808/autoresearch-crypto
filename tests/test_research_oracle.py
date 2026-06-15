"""Smoke test for research_oracle.py — Phase 2/3 validation.

Verifies:
  1-10: Core oracle functionality (baseline, schema, output, parity)
  11-13: Phase 3 candidate mode + ADX filter
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import shutil
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import importlib.util
_oracle_path = PROJECT_DIR / "scripts" / "research_oracle.py"
_spec = importlib.util.spec_from_file_location("research_oracle", _oracle_path)
research_oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(research_oracle)

# Import filters for direct unit tests
from dex.filters import apply_adx_filter


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


CKPT = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")
CAND = str(PROJECT_DIR / "research_workspace" / "candidates" / "exp_0001.json")


# --- Schema Validation ---

def test_oracle_runs_on_v21_baseline():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    for key in ["experiment_id", "strategy", "params_hash", "data", "metrics", "flags"]:
        assert key in result, f"Missing: {key}"
    for key in ["is", "oos", "rolling", "regime", "execution_parity", "correlation", "sensitivity"]:
        assert key in result["metrics"], f"Missing metrics: {key}"
    assert result["flags"]["status"] in ("PASS", "WARN", "REJECT", "BASELINE")
    print("  [PASS] Schema validation")


# --- Read-only invariants ---

def test_no_checkpoint_modified():
    ckpt = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    h = _file_sha256(ckpt)
    research_oracle.run_oracle(checkpoint_path=CKPT)
    assert _file_sha256(ckpt) == h, "Checkpoint modified!"
    print("  [PASS] Checkpoint not modified")


def test_no_live_files_accessed():
    live = list(PROJECT_DIR.glob("live_*.py")) + list((PROJECT_DIR / "dex" / "live").glob("*.py"))
    assert len(live) > 0
    mt = {f: f.stat().st_mtime for f in live}
    research_oracle.run_oracle(checkpoint_path=CKPT)
    for f in live:
        assert mt[f] == f.stat().st_mtime, f"Live file modified: {f.name}"
    print("  [PASS] No live files modified")


# --- Output ---

def test_output_files_created():
    tmp = Path(tempfile.mkdtemp())
    orig_r, orig_t, orig_j = research_oracle.REPORT_PATH, research_oracle.TSV_PATH, research_oracle.JSONL_PATH
    try:
        rp, tp, jp = tmp / "oracle_report.json", tmp / "results.tsv", tmp / "experiments.jsonl"
        research_oracle.REPORT_PATH, research_oracle.TSV_PATH, research_oracle.JSONL_PATH = rp, tp, jp
        assert not rp.exists()
        result = research_oracle.run_oracle(checkpoint_path=CKPT)
        research_oracle._write_oracle_report(result)
        research_oracle._append_results_tsv(result)
        research_oracle._append_experiments_jsonl(result)
        assert rp.exists() and json.loads(rp.read_text())["experiment_id"]
        assert tp.exists() and len(tp.read_text().strip().split("\n")) >= 2
        assert jp.exists()
        print("  [PASS] Output files created (temp paths)")
    finally:
        research_oracle.REPORT_PATH, research_oracle.TSV_PATH, research_oracle.JSONL_PATH = orig_r, orig_t, orig_j
        shutil.rmtree(tmp, ignore_errors=True)


# --- Metrics ---

def test_oracle_metrics_are_sensible():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    r = result["metrics"]["is"]["raw"]
    assert -1 < r["return"] < 10
    assert r["trades"] > 0
    assert 0 <= result["metrics"]["execution_parity"] <= 1
    print("  [PASS] Metrics sensible")


def test_rolling_regime_distribution_valid():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    r6 = result["metrics"]["rolling"].get("6m_worst_regime")
    assert r6 and max(r6.get("bull_pct", 0), r6.get("bear_pct", 0), r6.get("neutral_pct", 0)) > 0.5
    print("  [PASS] Rolling regime distribution valid")


def test_baseline_status():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    assert result["flags"]["status"] == "BASELINE"
    assert "baseline_known_risks" in result["flags"]
    assert len(result["flags"]["disqualifications"]) == 0
    print("  [PASS] Baseline status")


def test_baseline_metric_regression():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    o = result["metrics"]["oos"]["raw"]
    assert 1.40 <= o["return"] <= 1.70
    assert -0.38 <= o["dd"] <= -0.30
    assert 2.1 <= o["sharpe"] <= 2.6
    assert 50 <= o["trades"] <= 100
    assert result.get("oracle_version") == "v0.2"
    print("  [PASS] Baseline metric regression")


def test_baseline_via_json_params():
    result = research_oracle.run_oracle(use_baseline=True)
    assert result["flags"]["status"] == "BASELINE"
    print("  [PASS] Baseline via JSON params")


def test_baseline_json_vs_pt_parity():
    rj = research_oracle.run_oracle(use_baseline=True)
    rp = research_oracle.run_oracle(checkpoint_path=CKPT)
    mj, mp = rj["metrics"]["oos"]["raw"], rp["metrics"]["oos"]["raw"]
    assert abs(mj["return"] - mp["return"]) < 1e-6
    assert mj["trades"] == mp["trades"]
    print("  [PASS] JSON vs PT parity")


# --- Phase 3 candidate mode ---

def test_candidate_mode():
    assert Path(CAND).exists()
    result = research_oracle.run_oracle(candidate_path=CAND)
    assert "experiment_id" in result
    assert result["metrics"].get("correlation", {}).get("vs_baseline") is not None
    print("  [PASS] Candidate mode")


def test_candidate_invalid_fails():
    try:
        research_oracle.run_oracle(candidate_path="/nonexistent/path.json")
        assert False
    except (FileNotFoundError, NotImplementedError):
        pass
    print("  [PASS] Invalid candidate fails")


# --- Phase 3B ADX filter unit tests ---

def test_adx_filter_case_insensitive():
    """Verify regime name matching is case-insensitive.

    Each entry starts from flat (signal after CLOSE) to test NEW entry blocking.
    """
    sig = np.array([2, 0, 2, 0, 2, 0, 2], dtype=int)  # entry → close → entry → ...
    adx = np.array([30, 30, 5, 30, 5, 30, 30], dtype=float)
    regimes = np.array(["BULL", "BULL", "NEUTRAL", "BULL", "neutral", "BULL", "BULL"], dtype=object)

    result = apply_adx_filter(sig, adx, threshold=20, apply_to=["neutral"], regimes=regimes)
    assert result[0] == 2, "BULL entry should pass (regime not filtered)"
    assert result[1] == 0, "close should pass through"
    assert result[2] == 1, "NEUTRAL entry should be blocked (ADX 5 < 20)"
    assert result[3] == 0, "close should pass through"
    assert result[4] == 1, "neutral(lowercase) entry should be blocked (ADX 5 < 20)"
    assert result[5] == 0, "close should pass through"
    assert result[6] == 2, "BULL entry should pass (regime not filtered)"

    print("  [PASS] ADX filter case-insensitive regime matching")


def test_adx_filter_apply_to_subset():
    """Verify filter only blocks regimes specified in apply_to."""
    sig = np.array([2, 3, 2, 3], dtype=int)
    adx = np.array([5, 5, 5, 5], dtype=float)
    regimes = np.array(["BULL", "BEAR", "BULL", "BEAR"], dtype=object)

    # Only filter BEAR
    result = apply_adx_filter(sig, adx, threshold=20, apply_to=["bear"], regimes=regimes)
    assert result[0] == 2, "BULL entry should pass"
    assert result[1] == 1, "BEAR entry should be blocked"
    assert result[2] == 2, "BULL entry should pass"
    assert result[3] == 1, "BEAR entry should be blocked"

    print("  [PASS] ADX filter applies to correct regimes")


def test_adx_filter_unknown_type_raises():
    """Verify unknown filter type raises ValueError."""
    from dex.filters import build_filter_from_config
    import numpy as np
    try:
        build_filter_from_config({"type": "nonexistent"}, np.array([]), np.array([]))
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  [PASS] Unknown filter type raises ValueError")


# --- Runner ---

if __name__ == "__main__":
    print("=== Research Oracle Smoke Tests ===\n")
    ckpt = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    hash_before = _file_sha256(ckpt)

    tests = [
        ("Schema", test_oracle_runs_on_v21_baseline),
        ("Checkpoint integrity", test_no_checkpoint_modified),
        ("Live file isolation", test_no_live_files_accessed),
        ("Output files (temp)", test_output_files_created),
        ("Metrics sanity", test_oracle_metrics_are_sensible),
        ("Rolling regime", test_rolling_regime_distribution_valid),
        ("Baseline status", test_baseline_status),
        ("Metric regression", test_baseline_metric_regression),
        ("Baseline via JSON", test_baseline_via_json_params),
        ("JSON vs PT parity", test_baseline_json_vs_pt_parity),
        ("Candidate mode", test_candidate_mode),
        ("Invalid candidate", test_candidate_invalid_fails),
        ("ADX case-insensitive", test_adx_filter_case_insensitive),
        ("ADX apply_to subset", test_adx_filter_apply_to_subset),
        ("ADX unknown type error", test_adx_filter_unknown_type_raises),
    ]

    failed = 0
    for name, fn in tests:
        try:
            print(f"[{name}]")
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {e}")
            failed += 1
        print()

    if _file_sha256(ckpt) != hash_before:
        print("  [FAIL] Checkpoint modified during test suite!")
        failed += 1

    print(f"---\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
