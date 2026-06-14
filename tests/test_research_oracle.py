"""Smoke test for research_oracle.py — Phase 2 validation.

Verifies:
  1. Oracle runs on v2.1 balanced checkpoint without error
  2. Output JSON has correct schema (all required top-level keys)
  3. No checkpoint files were modified
  4. No live_* files were accessed (checked via file mtime)
  5. Output files are created at expected paths
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
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


def test_oracle_runs_on_v21_baseline():
    """Oracle runs on v2.1 balanced checkpoint and returns valid result."""
    ckpt_path = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")
    assert Path(ckpt_path).exists(), f"Checkpoint not found: {ckpt_path}"

    result = research_oracle.run_oracle(checkpoint_path=ckpt_path)

    # --- Schema validation ---
    required_top_keys = [
        "experiment_id", "parent_id", "candidate_role", "timestamp",
        "strategy", "params_hash", "data_hash", "commit",
        "data", "metrics", "flags",
    ]
    for key in required_top_keys:
        assert key in result, f"Missing top-level key: {key}"

    # data block
    data = result["data"]
    for key in ["dataset_path", "data_hash", "data_start", "data_end",
                "is_start", "is_end", "oos_start", "oos_end",
                "is_bars", "oos_bars", "split_method", "split_ratio"]:
        assert key in data, f"Missing data key: {key}"

    # metrics block
    metrics = result["metrics"]
    for key in ["is", "oos", "rolling", "regime", "execution_parity", "sensitivity"]:
        assert key in metrics, f"Missing metrics key: {key}"

    # IS/OOS sub-blocks
    for block_name in ["is", "oos"]:
        block = metrics[block_name]
        for variant in ["raw", "safe_execution", "regime_permission"]:
            assert variant in block, f"Missing {block_name}.{variant}"
            v = block[variant]
            for field in ["return", "dd", "sharpe", "trades", "trades_per_year", "score"]:
                assert field in v, f"Missing {block_name}.{variant}.{field}"

    # sensitivity
    sens = metrics["sensitivity"]
    assert "fees" in sens, "Missing sensitivity.fees"
    assert "slippage" in sens, "Missing sensitivity.slippage"

    # flags
    flags = result["flags"]
    for key in ["status", "warnings", "disqualifications"]:
        assert key in flags, f"Missing flags key: {key}"
    assert flags["status"] in ("PASS", "WARN", "REJECT", "BASELINE"), (
        f"Invalid status: {flags['status']}"
    )

    # rolling
    rolling = metrics["rolling"]
    assert "6m_min_return" in rolling, "Missing rolling 6m"
    assert "12m_min_return" in rolling, "Missing rolling 12m"

    # regime
    regime = metrics["regime"]
    for r in ["bull", "bear", "neutral"]:
        assert r in regime, f"Missing regime.{r}"

    # execution parity
    assert isinstance(metrics["execution_parity"], float), "execution_parity must be float"

    print("  [PASS] Schema validation")
    return result


def test_no_checkpoint_modified():
    """Verify no checkpoint was modified during oracle execution."""
    ckpt_path = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    hash_before = _file_sha256(ckpt_path)

    research_oracle.run_oracle(checkpoint_path=str(ckpt_path))

    hash_after = _file_sha256(ckpt_path)
    assert hash_before == hash_after, (
        f"Checkpoint modified! Hash before: {hash_before}, after: {hash_after}"
    )
    print("  [PASS] Checkpoint not modified")


def test_no_live_files_accessed():
    """Verify no live_* files were touched during oracle execution."""
    # Record mtimes of live files before
    live_files = list(PROJECT_DIR.glob("live_*.py")) + list((PROJECT_DIR / "dex" / "live").glob("*.py"))
    assert len(live_files) > 0, "No live files found — test may be misconfigured"

    mtimes_before = {f: f.stat().st_mtime for f in live_files}

    ckpt_path = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")
    research_oracle.run_oracle(checkpoint_path=ckpt_path)

    mtimes_after = {f: f.stat().st_mtime for f in live_files}
    for f in live_files:
        assert mtimes_before[f] == mtimes_after[f], (
            f"Live file was modified: {f.name}"
        )
    print("  [PASS] No live files modified")


def test_output_files_created():
    """Verify oracle writes output files."""
    ckpt_path = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")

    # Run oracle (it will write to default paths)
    research_oracle.run_oracle(checkpoint_path=ckpt_path)

    report_path = research_oracle.REPORT_PATH
    tsv_path = research_oracle.TSV_PATH
    jsonl_path = research_oracle.JSONL_PATH

    assert report_path.exists(), f"Report not created: {report_path}"
    assert tsv_path.exists(), f"TSV not created: {tsv_path}"
    assert jsonl_path.exists(), f"JSONL not created: {jsonl_path}"

    # Validate report is valid JSON
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "experiment_id" in report, "Report missing experiment_id"

    # Validate TSV has header + at least 1 row
    tsv_lines = tsv_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(tsv_lines) >= 2, f"TSV has fewer than 2 lines: {len(tsv_lines)}"
    assert "\t" in tsv_lines[0], "TSV header missing tabs"

    # Validate JSONL
    jsonl_lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(jsonl_lines) >= 1, "JSONL has no lines"
    record = json.loads(jsonl_lines[-1])
    assert "experiment_id" in record, "JSONL record missing experiment_id"

    print("  [PASS] Output files created + validated")
    print(f"    report: {report_path} ({report_path.stat().st_size} bytes)")
    print(f"    tsv: {tsv_path} ({tsv_path.stat().st_size} bytes)")
    print(f"    jsonl: {jsonl_path} ({jsonl_path.stat().st_size} bytes)")


def test_oracle_metrics_are_sensible():
    """Quick sanity check that metrics are in reasonable ranges."""
    ckpt_path = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")
    result = research_oracle.run_oracle(checkpoint_path=ckpt_path)

    is_raw = result["metrics"]["is"]["raw"]

    # Return should be between -1 and +10 (1000%)
    assert -1.0 < is_raw["return"] < 10.0, f"IS return out of range: {is_raw['return']}"

    # DD should be between -1.0 and 0
    assert -1.0 <= is_raw["dd"] <= 0.0, f"IS DD out of range: {is_raw['dd']}"

    # Trades should be > 0 for a real strategy
    assert is_raw["trades"] > 0, "IS trades = 0 — something is wrong"

    # Execution parity should be between 0 and 1
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
    ckpt_path = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")
    result = research_oracle.run_oracle(checkpoint_path=ckpt_path)

    rolling = result["metrics"]["rolling"]

    # 6m worst regime must have meaningful distribution
    regime_6m = rolling.get("6m_worst_regime")
    assert regime_6m is not None, "6m worst regime missing from rolling metrics"

    # At least one regime should be > 50% and not all three near 33%
    # (which would indicate uniform/random labeling)
    pcts = [regime_6m.get("bull_pct", 0), regime_6m.get("bear_pct", 0), regime_6m.get("neutral_pct", 0)]
    max_pct = max(pcts)

    assert max_pct > 0.5, (
        f"Rolling 6m worst window regime distribution too uniform: "
        f"BULL={pcts[0]:.1%} BEAR={pcts[1]:.1%} NEUTRAL={pcts[2]:.1%}. "
        f"EMA200 warmup may still be broken."
    )

    # 12m worst regime should also have valid distribution
    regime_12m = rolling.get("12m_worst_regime")
    assert regime_12m is not None, "12m worst regime missing"

    pcts_12 = [regime_12m.get("bull_pct", 0), regime_12m.get("bear_pct", 0), regime_12m.get("neutral_pct", 0)]
    # 12-month windows should have at least some variety
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
    ckpt_path = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")
    result = research_oracle.run_oracle(checkpoint_path=ckpt_path)

    flags = result["flags"]
    assert flags["status"] == "BASELINE", (
        f"v2.1 baseline should have BASELINE status, got {flags['status']}"
    )
    assert "baseline_known_risks" in flags, "baseline_known_risks field missing"
    assert len(flags["disqualifications"]) == 0, (
        f"Baseline should have no disqualifications, got {flags['disqualifications']}"
    )

    print("  [PASS] Baseline status correct")
    print(f"    status: {flags['status']}")
    print(f"    known_risks: {flags.get('baseline_known_risks', [])}")
    print(f"    warnings: {flags.get('warnings', [])}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Research Oracle Smoke Tests ===\n")

    # Record checkpoint hash before any test
    ckpt = PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt"
    hash_before_all = _file_sha256(ckpt)

    tests = [
        ("Schema validation", test_oracle_runs_on_v21_baseline),
        ("No checkpoint modification", test_no_checkpoint_modified),
        ("No live files accessed", test_no_live_files_accessed),
        ("Output files created", test_output_files_created),
        ("Sensible metrics", test_oracle_metrics_are_sensible),
        ("Rolling regime distribution valid", test_rolling_regime_distribution_valid),
        ("Baseline status (not REJECT)", test_baseline_status),
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

    # Final checkpoint integrity check
    hash_after_all = _file_sha256(ckpt)
    if hash_before_all != hash_after_all:
        print(f"  [FAIL] Checkpoint was modified during test suite!")
        failed += 1

    print(f"---")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    if failed > 0:
        print(f"{failed} FAILED")
        sys.exit(1)
    else:
        print("All smoke tests passed.")
        sys.exit(0)
