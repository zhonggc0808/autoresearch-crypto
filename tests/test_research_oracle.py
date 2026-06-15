"""Smoke test for research_oracle.py — Phase 2/3 validation.

Verifies:
  1. Oracle runs on v2.1 balanced checkpoint without error
  2. Output JSON has correct schema
  3. No checkpoint files were modified
  4. No live_* files were accessed
  5. Output files correctly created (temp paths)
  6. Rolling regime distribution valid
  7. Baseline status and metric regression
  8. JSON params vs .pt checkpoint parity
  9. Candidate mode (Phase 3 read-only)
  10. Invalid candidate handling
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import importlib.util
_oracle_path = PROJECT_DIR / "scripts" / "research_oracle.py"
_spec = importlib.util.spec_from_file_location("research_oracle", _oracle_path)
research_oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(research_oracle)


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
    for side in ["is", "oos"]:
        for v in ["raw", "safe_execution", "regime_permission"]:
            assert v in result["metrics"][side], f"Missing {side}.{v}"
    sens = result["metrics"]["sensitivity"]
    for s in ["is", "oos"]:
        assert s in sens and "fees" in sens[s] and "slippage" in sens[s]
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


# --- Metrics sanity ---

def test_oracle_metrics_are_sensible():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    r = result["metrics"]["is"]["raw"]
    assert -1 < r["return"] < 10
    assert -1 <= r["dd"] <= 0
    assert r["trades"] > 0
    assert 0 <= result["metrics"]["execution_parity"] <= 1
    print("  [PASS] Metrics sensible")


def test_rolling_regime_distribution_valid():
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    r6 = result["metrics"]["rolling"].get("6m_worst_regime")
    assert r6 and max(r6.get("bull_pct", 0), r6.get("bear_pct", 0), r6.get("neutral_pct", 0)) > 0.5
    r12 = result["metrics"]["rolling"].get("12m_worst_regime")
    assert r12 and sum(1 for k in ["bull_pct", "bear_pct", "neutral_pct"] if r12.get(k, 0) > 0.05) >= 2
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
    c = result["metrics"]["correlation"]
    assert 1.40 <= o["return"] <= 1.70
    assert -0.38 <= o["dd"] <= -0.30
    assert 2.1 <= o["sharpe"] <= 2.6
    assert 50 <= o["trades"] <= 100
    assert result["metrics"]["execution_parity"] >= 0.99
    assert c["vs_baseline"] == 1.0
    assert result.get("oracle_version") == "v0.1.0"
    print("  [PASS] Baseline metric regression")


def test_baseline_via_json_params():
    result = research_oracle.run_oracle(use_baseline=True)
    assert result["flags"]["status"] == "BASELINE"
    o = result["metrics"]["oos"]["raw"]
    assert 1.40 <= o["return"] <= 1.70
    print("  [PASS] Baseline via JSON params")


def test_baseline_json_vs_pt_parity():
    rj = research_oracle.run_oracle(use_baseline=True)
    rp = research_oracle.run_oracle(checkpoint_path=CKPT)
    mj, mp = rj["metrics"]["oos"]["raw"], rp["metrics"]["oos"]["raw"]
    assert abs(mj["return"] - mp["return"]) < 1e-6
    assert abs(mj["dd"] - mp["dd"]) < 1e-6
    assert mj["trades"] == mp["trades"]
    print("  [PASS] JSON vs PT parity")


# --- Phase 3 candidate mode ---

def test_candidate_mode():
    assert Path(CAND).exists(), f"Candidate not found: {CAND}"
    result = research_oracle.run_oracle(candidate_path=CAND)
    assert "experiment_id" in result
    assert result["strategy"] == "channel_breakout_v21"
    corr = result["metrics"].get("correlation", {}).get("vs_baseline")
    assert corr is not None
    for block in ["is", "oos"]:
        for v in ["raw", "safe_execution"]:
            assert v in result["metrics"][block]
    assert result["flags"]["status"] in ("PASS", "WARN", "REJECT")
    print("  [PASS] Candidate mode")
    print(f"    OOS return: {result['metrics']['oos']['raw']['return']:.4f}")
    print(f"    Correlation vs baseline: {corr}")


def test_candidate_invalid_fails():
    try:
        research_oracle.run_oracle(candidate_path="/nonexistent/path.json")
        assert False
    except (FileNotFoundError, NotImplementedError):
        pass
    print("  [PASS] Invalid candidate fails")


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
    ]

    failed = 0
    for name, fn in tests:
        try:
            print(f"[{name}]")
            fn()
        except Exception as e:
            print(f"  [FAIL] {e}")
            failed += 1
        print()

    if _file_sha256(ckpt) != hash_before:
        print("  [FAIL] Checkpoint modified during test suite!")
        failed += 1

    print(f"---\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
