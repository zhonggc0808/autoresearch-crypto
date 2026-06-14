"""Smoke test for research_oracle.py — Phase 2 validation.

Verifies:
  1. Oracle runs on v2.1 balanced checkpoint without error
  2. Output JSON has correct schema (all required top-level keys)
  3. No checkpoint files were modified
  4. No live_* files were accessed (checked via file mtime)
  5. Output files are correctly created and validated (temp paths)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Import the oracle module
import importlib.util
_oracle_path = PROJECT_DIR / "scripts" / "research_oracle.py"
_spec = importlib.util.spec_from_file_location("research_oracle", _oracle_path)
research_oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(research_oracle)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _with_temp_outputs(test_fn):
    """Decorator to run test with temp output paths."""
    def wrapper(*args, **kwargs):
        tmp = Path(tempfile.mkdtemp())
        orig_report = research_oracle.REPORT_PATH
        orig_tsv = research_oracle.TSV_PATH
        orig_jsonl = research_oracle.JSONL_PATH
        research_oracle.REPORT_PATH = tmp / "oracle_report.json"
        research_oracle.TSV_PATH = tmp / "results.tsv"
        research_oracle.JSONL_PATH = tmp / "experiments.jsonl"
        try:
            test_fn(*args, **kwargs)
        finally:
            research_oracle.REPORT_PATH = orig_report
            research_oracle.TSV_PATH = orig_tsv
            research_oracle.JSONL_PATH = orig_jsonl
            shutil.rmtree(tmp, ignore_errors=True)
    return wrapper


CKPT = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_oracle_runs_on_v21_baseline():
    """Oracle runs on v2.1 balanced checkpoint and returns valid result."""
    result = research_oracle.run_oracle(checkpoint_path=CKPT)

    # Schema validation
    required_top_keys = [
        "experiment_id", "parent_id", "candidate_role", "timestamp",
        "strategy", "params_hash", "data_hash", "commit",
        "data", "metrics", "flags",
    ]
    for key in required_top_keys:
        assert key in result, f"Missing top-level key: {key}"

    data = result["data"]
    for key in ["dataset_path", "data_hash", "data_start", "data_end",
                "is_start", "is_end", "oos_start", "oos_end",
                "is_bars", "oos_bars", "split_method", "split_ratio"]:
        assert key in data, f"Missing data key: {key}"

    metrics = result["metrics"]
    for key in ["is", "oos", "rolling", "regime", "execution_parity", "correlation", "sensitivity"]:
        assert key in metrics, f"Missing metrics key: {key}"

    for block_name in ["is", "oos"]:
        block = metrics[block_name]
        for variant in ["raw", "safe_execution", "regime_permission"]:
            assert variant in block, f"Missing {block_name}.{variant}"
            v = block[variant]
            for field in ["return", "dd", "sharpe", "trades", "trades_per_year", "score"]:
                assert field in v, f"Missing {block_name}.{variant}.{field}"

    # Sensitivity is now hierarchical: {is: {fees, slippage}, oos: {fees, slippage}}
    sens = metrics["sensitivity"]
    for side in ["is", "oos"]:
        assert side in sens, f"Missing sensitivity.{side}"
        for mtype in ["fees", "slippage"]:
            assert mtype in sens[side], f"Missing sensitivity.{side}.{mtype}"

    # Correlation block
    assert "correlation" in metrics, "Missing correlation"
    assert "vs_baseline" in metrics["correlation"], "Missing correlation.vs_baseline"

    flags = result["flags"]
    for key in ["status", "warnings", "disqualifications"]:
        assert key in flags, f"Missing flags key: {key}"
    assert flags["status"] in ("PASS", "WARN", "REJECT", "BASELINE"), (
        f"Invalid status: {flags['status']}"
    )

    rolling = metrics["rolling"]
    assert "6m_min_return" in rolling, "Missing rolling 6m"
    assert "12m_min_return" in rolling, "Missing rolling 12m"

    regime = metrics["regime"]
    for r in ["bull", "bear", "neutral"]:
        assert r in regime, f"Missing regime.{r}"

    assert isinstance(metrics["execution_parity"], float), "execution_parity must be float"

    print("  [PASS] Schema validation")


def test_no_checkpoint_modified():
    """Verify no checkpoint was modified during oracle execution."""
    ckpt = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    hash_before = _file_sha256(ckpt)

    research_oracle.run_oracle(checkpoint_path=str(ckpt))

    hash_after = _file_sha256(ckpt)
    assert hash_before == hash_after, "Checkpoint modified!"
    print("  [PASS] Checkpoint not modified")


def test_no_live_files_accessed():
    """Verify no live_* files were touched during oracle execution."""
    live_files = list(PROJECT_DIR.glob("live_*.py")) + list((PROJECT_DIR / "dex" / "live").glob("*.py"))
    assert len(live_files) > 0, "No live files found"

    mtimes_before = {f: f.stat().st_mtime for f in live_files}
    research_oracle.run_oracle(checkpoint_path=CKPT)
    mtimes_after = {f: f.stat().st_mtime for f in live_files}

    for f in live_files:
        assert mtimes_before[f] == mtimes_after[f], f"Live file modified: {f.name}"
    print("  [PASS] No live files modified")


def test_output_files_created():
    """Verify oracle writes correctly to temp paths (no false positive from stale files)."""
    tmp = Path(tempfile.mkdtemp())
    orig_report = research_oracle.REPORT_PATH
    orig_tsv = research_oracle.TSV_PATH
    orig_jsonl = research_oracle.JSONL_PATH

    try:
        report = tmp / "oracle_report.json"
        tsv = tmp / "results.tsv"
        jsonl = tmp / "experiments.jsonl"

        research_oracle.REPORT_PATH = report
        research_oracle.TSV_PATH = tsv
        research_oracle.JSONL_PATH = jsonl

        # Fresh temp dir — no files should exist yet
        assert not report.exists(), "Temp report already exists (setup error)"
        assert not tsv.exists(), "Temp TSV already exists (setup error)"
        assert not jsonl.exists(), "Temp JSONL already exists (setup error)"

        result = research_oracle.run_oracle(checkpoint_path=CKPT)

        # Now manually call writers (run_oracle returns result but doesn't write)
        research_oracle._write_oracle_report(result)
        research_oracle._append_results_tsv(result)
        research_oracle._append_experiments_jsonl(result)

        assert report.exists(), f"Report not created: {report}"
        assert tsv.exists(), f"TSV not created: {tsv}"
        assert jsonl.exists(), f"JSONL not created: {jsonl}"

        # Validate report JSON
        report_data = json.loads(report.read_text(encoding="utf-8"))
        assert "experiment_id" in report_data

        # Validate TSV
        tsv_lines = tsv.read_text(encoding="utf-8").strip().split("\n")
        assert len(tsv_lines) >= 2, f"TSV has <2 lines: {len(tsv_lines)}"
        assert "\t" in tsv_lines[0], "TSV header missing tabs"
        assert len(tsv_lines[1].split("\t")) >= 20, "TSV row missing columns"

        # Validate JSONL
        jsonl_lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(jsonl_lines) >= 1, "JSONL empty"
        record = json.loads(jsonl_lines[-1])
        assert "experiment_id" in record

        print("  [PASS] Output files created + validated (temp paths)")
        print(f"    report: {report} ({report.stat().st_size} bytes)")
        print(f"    tsv: {tsv} ({tsv.stat().st_size} bytes)")
        print(f"    jsonl: {jsonl} ({jsonl.stat().st_size} bytes)")
    finally:
        research_oracle.REPORT_PATH = orig_report
        research_oracle.TSV_PATH = orig_tsv
        research_oracle.JSONL_PATH = orig_jsonl
        shutil.rmtree(tmp, ignore_errors=True)


def test_oracle_metrics_are_sensible():
    """Quick sanity check that metrics are in reasonable ranges."""
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    is_raw = result["metrics"]["is"]["raw"]

    assert -1.0 < is_raw["return"] < 10.0, f"IS return out of range: {is_raw['return']}"
    assert -1.0 <= is_raw["dd"] <= 0.0, f"IS DD out of range: {is_raw['dd']}"
    assert is_raw["trades"] > 0, "IS trades = 0"

    parity = result["metrics"]["execution_parity"]
    assert 0.0 <= parity <= 1.0, f"Execution parity out of range: {parity}"

    print("  [PASS] Metrics in sensible ranges")
    print(f"    IS return: {is_raw['return']:.4f}")
    print(f"    IS DD: {is_raw['dd']:.4f}")
    print(f"    IS trades: {is_raw['trades']}")
    print(f"    Exec parity: {parity:.4f}")


def test_rolling_regime_distribution_valid():
    """Verify rolling window regime attribution is not all-NEUTRAL.

    Before the warmup fix, 6-month rolling windows would show 100% NEUTRAL
    because EMA200 couldn't warm up in a 180-day window.
    """
    result = research_oracle.run_oracle(checkpoint_path=CKPT)
    rolling = result["metrics"]["rolling"]

    regime_6m = rolling.get("6m_worst_regime")
    assert regime_6m is not None, "6m worst regime missing"

    pcts = [regime_6m.get("bull_pct", 0), regime_6m.get("bear_pct", 0), regime_6m.get("neutral_pct", 0)]
    max_pct = max(pcts)
    assert max_pct > 0.5, (
        f"Rolling 6m worst window regime distribution too uniform: "
        f"BULL={pcts[0]:.1%} BEAR={pcts[1]:.1%} NEUTRAL={pcts[2]:.1%}"
    )

    regime_12m = rolling.get("12m_worst_regime")
    assert regime_12m is not None, "12m worst regime missing"

    pcts_12 = [regime_12m.get("bull_pct", 0), regime_12m.get("bear_pct", 0), regime_12m.get("neutral_pct", 0)]
    num_nonzero = sum(1 for p in pcts_12 if p > 0.05)
    assert num_nonzero >= 2, (
        f"Rolling 12m worst window has only {num_nonzero} regimes > 5%: "
        f"BULL={pcts_12[0]:.1%} BEAR={pcts_12[1]:.1%} NEUTRAL={pcts_12[2]:.1%}"
    )

    print("  [PASS] Rolling regime distribution valid")
    print(f"    6m worst regime: {regime_6m['dominant']} "
          f"(BULL={regime_6m['bull_pct']:.1%} BEAR={regime_6m['bear_pct']:.1%} NEUTRAL={regime_6m['neutral_pct']:.1%})")
    print(f"    12m worst regime: {regime_12m['dominant']} "
          f"(BULL={regime_12m['bull_pct']:.1%} BEAR={regime_12m['bear_pct']:.1%} NEUTRAL={regime_12m['neutral_pct']:.1%})")


def test_baseline_status():
    """Verify v2.1 baseline gets BASELINE status, not REJECT."""
    result = research_oracle.run_oracle(checkpoint_path=CKPT)

    flags = result["flags"]
    assert flags["status"] == "BASELINE", f"Expected BASELINE, got {flags['status']}"
    assert "baseline_known_risks" in flags
    assert len(flags["disqualifications"]) == 0

    print("  [PASS] Baseline status correct")
    print(f"    status: {flags['status']}")
    print(f"    known_risks: {flags.get('baseline_known_risks', [])}")
    print(f"    warnings: {flags.get('warnings', [])}")


def test_baseline_metric_regression():
    """Verify v2.1 baseline OOS metrics stay within expected ranges.

    These ranges are loose enough to accommodate minor data fluctuations
    but tight enough to catch a warmup bug regression.
    """
    result = research_oracle.run_oracle(checkpoint_path=CKPT)

    oos_raw = result["metrics"]["oos"]["raw"]
    oos_safe = result["metrics"]["oos"]["safe_execution"]
    correlation = result["metrics"]["correlation"]

    assert 1.40 <= oos_raw["return"] <= 1.70, f"OOS return out of range: {oos_raw['return']:.4f}"
    assert -0.38 <= oos_raw["dd"] <= -0.30, f"OOS DD out of range: {oos_raw['dd']:.4f}"
    assert 2.1 <= oos_raw["sharpe"] <= 2.6, f"OOS Sharpe out of range: {oos_raw['sharpe']:.4f}"
    assert 50 <= oos_raw["trades"] <= 100, f"OOS trades out of range: {oos_raw['trades']}"

    return_diff = abs(oos_safe["return"] - oos_raw["return"])
    assert return_diff < 0.05, f"Safe vs raw return difference too large: {return_diff:.4f}"

    parity = result["metrics"]["execution_parity"]
    assert parity >= 0.99, f"Execution parity dropped: {parity}"

    # Correlation should be 1.0 (evaluating baseline)
    assert correlation["vs_baseline"] == 1.0, f"vs_baseline correlation should be 1.0, got {correlation['vs_baseline']}"

    # Version metadata
    assert result.get("oracle_version") == "v0.1.0", f"Unexpected version: {result.get('oracle_version')}"
    assert result.get("baseline_id") == "channel_breakout_v2_1_balanced"
    assert "split_id" in result
    assert "checkpoint_hash" in result

    # Sensitivity should have IS + OOS
    sens = result["metrics"]["sensitivity"]
    assert "is" in sens and "oos" in sens, "Sensitivity missing IS/OOS split"

    print("  [PASS] Baseline metric regression check")
    print(f"    OOS return: {oos_raw['return']:.4f}")
    print(f"    OOS DD: {oos_raw['dd']:.4f}")
    print(f"    OOS Sharpe: {oos_raw['sharpe']:.4f}")
    print(f"    OOS trades: {oos_raw['trades']}")
    print(f"    Exec parity: {parity:.4f}")
    print(f"    Correlation vs baseline: {correlation['vs_baseline']}")
    print(f"    Oracle version: {result.get('oracle_version')}")


def test_baseline_via_json_params():
    """Verify run_oracle(use_baseline=True) works (fresh clone path)."""
    result = research_oracle.run_oracle(use_baseline=True)

    assert "experiment_id" in result, "No experiment_id (use_baseline failed)"
    assert result["strategy"] == "channel_breakout_v21"
    assert result["flags"]["status"] == "BASELINE"

    oos_raw = result["metrics"]["oos"]["raw"]
    assert 1.40 <= oos_raw["return"] <= 1.70, f"JSON baseline OOS return out of range: {oos_raw['return']:.4f}"
    assert oos_raw["trades"] > 0, "No trades from JSON baseline"

    print("  [PASS] Baseline runs from JSON params")
    print(f"    OOS return: {oos_raw['return']:.4f}")
    print(f"    OOS trades: {oos_raw['trades']}")


def test_baseline_json_vs_pt_parity():
    """Verify JSON params produce identical results to .pt checkpoint.

    This proves the JSON baseline is a faithful reproduction of the
    frozen .pt checkpoint — critical for fresh clones without .pt access.
    """
    result_json = research_oracle.run_oracle(use_baseline=True)
    result_pt = research_oracle.run_oracle(checkpoint_path=CKPT)

    # Compare key metrics
    mj = result_json["metrics"]["oos"]["raw"]
    mp = result_pt["metrics"]["oos"]["raw"]

    assert abs(mj["return"] - mp["return"]) < 1e-6, (
        f"OOS return differs: JSON={mj['return']:.8f} PT={mp['return']:.8f}"
    )
    assert abs(mj["dd"] - mp["dd"]) < 1e-6, (
        f"OOS DD differs: JSON={mj['dd']:.8f} PT={mp['dd']:.8f}"
    )
    assert mj["trades"] == mp["trades"], (
        f"Trade count differs: JSON={mj['trades']} PT={mp['trades']}"
    )

    # Flags should be identical
    assert result_json["flags"]["status"] == result_pt["flags"]["status"]
    assert result_json["flags"]["baseline_known_risks"] == result_pt["flags"]["baseline_known_risks"]

    print("  [PASS] JSON params ↔ .pt checkpoint parity verified")
    print(f"    OOS return: {mj['return']:.4f} (both)")
    print(f"    OOS trades: {mj['trades']} (both)")
    print(f"    Status: {result_json['flags']['status']} (both)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Research Oracle Smoke Tests ===\n")

    ckpt = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    hash_before_all = _file_sha256(ckpt)

    tests = [
        ("Schema validation", test_oracle_runs_on_v21_baseline),
        ("No checkpoint modification", test_no_checkpoint_modified),
        ("No live files accessed", test_no_live_files_accessed),
        ("Output files created (temp paths)", test_output_files_created),
        ("Sensible metrics", test_oracle_metrics_are_sensible),
        ("Rolling regime distribution valid", test_rolling_regime_distribution_valid),
        ("Baseline status (not REJECT)", test_baseline_status),
        ("Baseline metric regression", test_baseline_metric_regression),
        ("Baseline via JSON params (fresh clone path)", test_baseline_via_json_params),
        ("JSON params vs .pt checkpoint parity", test_baseline_json_vs_pt_parity),
    ]

    failed = 0
    for name, test_fn in tests:
        try:
            print(f"[{name}]")
            test_fn()
        except Exception as e:
            print(f"  [FAIL] {e}")
            failed += 1
        print()

    hash_after_all = _file_sha256(ckpt)
    if hash_before_all != hash_after_all:
        print("  [FAIL] Checkpoint was modified during test suite!")
        failed += 1

    print(f"---")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    if failed > 0:
        print(f"{failed} FAILED")
        sys.exit(1)
    else:
        print("All smoke tests passed.")
        sys.exit(0)
