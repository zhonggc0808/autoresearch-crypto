# v0.9b Codegen Evaluation Adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `evaluate_codegen_candidate.py` — an adapter that takes a v0.9a codegen candidate (strategy.py + manifest.json), re-verifies it, evaluates it through `_evaluate_signals`, and produces a scorecard with a forced `codegen_research_only` verdict. No modifications to oracle core, dex/, or existing evaluation pipeline.

**Architecture:** Three-phase flow (Re-Verify → Evaluate → Scorecard) that imports oracle utility functions as a read-only compatibility layer, uses `build_scorecard_from_result` (pure function) for scorecard construction, and self-manages output to `codegen_scorecards/`. All verdicts are overridden to prevent promotion.

**Tech Stack:** Python 3.10+, numpy, pandas. Imports from: research_oracle (private utilities), scan_candidate_code, sandbox_import_candidate, validate_code_candidate, score_candidate, dex.strategies.base.

---

## Files

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/evaluate_codegen_candidate.py` | **Create** | Three-phase evaluation adapter. CLI entry point. |
| `tests/test_codegen_evaluation_v09b.py` | **Create** | Full test suite covering valid eval, rejection, error handling, verdict override, exit codes, signature smoke test, dry-run. |

No existing files modified. No oracle core, dex/, or live code touched.

---

### Task 1: Create the adapter script skeleton + Phase 1

**Files:**
- Create: `scripts/evaluate_codegen_candidate.py`
- Test: `tests/test_codegen_evaluation_v09b.py` (written in Task 2)

- [ ] **Step 1: Write the script skeleton, paths, and Phase 1 verification functions**

`scripts/evaluate_codegen_candidate.py`:

```python
#!/usr/bin/env python3
"""Codegen Evaluation Adapter v0.9b — re-verify, evaluate, and scorecodegen candidates.

Three-phase flow:
    Phase 1 (Load + Re-Verify):
        - Read manifest.json + strategy.py from codegen_candidates/{cg_id}/
        - Static scan (scan_candidate_code.scan_code)
        - Sandbox import (sandbox_import_candidate.import_candidate_strategy)
        - Dynamic validation (validate_code_candidate.validate_candidate_code)
        → On failure: write rejection artifact, exit 2

    Phase 2 (Evaluate):
        - Load ETHUSDT 5m 1300d data (or synthetic for --mock-eval)
        - Split 70/30 IS/OOS
        - module.generate_signals(df) → signals
        - Compute: IS raw, IS safe-exec, OOS raw, OOS safe-exec
        - Rolling metrics (6m, 12m)
        - Fee sensitivity (0bp, 2bp, 4bp, 10bp)
        - Execution parity (IS, OOS)
        → On error: write evaluation_error artifact, exit 3

    Phase 3 (Scorecard):
        - Build oracle-like result dict
        - score_candidate.build_scorecard_from_result() → scorecard
        - Override verdict to codegen_research_only
        - Write scorecard to codegen_scorecards/
        - Update manifest.json (non-dry-run only)
        - Exit 0

Usage:
    uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001
    uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001 --dry-run
    uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001 --force
    uv run python scripts/evaluate_codegen_candidate.py --dir path/to/codegen_dir
    uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001 --mock-eval

Exit codes:
    0 — evaluation success: scorecard written
    1 — input error (unknown ID, missing files, invalid manifest)
    2 — candidate rejected before evaluation (Phase 1 failure)
    3 — evaluation error (Phase 2 failure)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from types import ModuleType

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Oracle utility imports (read-only compatibility layer)
# ---------------------------------------------------------------------------
# These are private functions imported for reuse. If a signature changes,
# the adapter fails hard — it does not silently change semantics.
from scripts.research_oracle import (
    _find_eth_data,
    _load_and_split_data,
    _evaluate_signals,
    _safe_execution_signals,
    _compute_rolling_metrics,
    _compute_fee_sensitivity,
    COMMISSION,
    SLIPPAGE,
    INITIAL_CAPITAL,
    BARS_PER_YEAR,
)

# ---------------------------------------------------------------------------
# Sandbox / validation imports
# ---------------------------------------------------------------------------
from scripts.scan_candidate_code import scan_code
from scripts.sandbox_import_candidate import (
    import_candidate_strategy,
    SandboxImportError,
)
from scripts.validate_code_candidate import validate_candidate_code

# ---------------------------------------------------------------------------
# Scorecard import (pure function only — no I/O)
# ---------------------------------------------------------------------------
from scripts.score_candidate import build_scorecard_from_result

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CODEGEN_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "codegen_candidates"
CODEGEN_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "codegen_scorecards"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
REJECTED_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "rejected_candidates"

# Oracle-equivalent constants
SPLIT_RATIO = 0.70
ROLLING_WINDOW_MONTHS = [6, 12]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_hash(path: Path) -> str:
    if not path.exists():
        return "sha256:FILE_NOT_FOUND"
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _ensure_dirs() -> None:
    CODEGEN_SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Phase 1: Load + Re-Verify
# ---------------------------------------------------------------------------


def _resolve_codegen_dir(
    codegen_id: Optional[str] = None,
    dir_path: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Resolve codegen directory from --codegen-id or --dir.

    Returns (cg_dir, cg_id).
    """
    if codegen_id is not None:
        cg_dir = CODEGEN_CANDIDATES_DIR / codegen_id
        if not cg_dir.is_dir():
            raise FileNotFoundError(
                f"Codegen directory not found: {cg_dir}"
            )
        return cg_dir, codegen_id

    if dir_path is not None:
        cg_dir = Path(dir_path).resolve()
        if not cg_dir.is_dir():
            raise FileNotFoundError(
                f"Codegen directory not found: {cg_dir}"
            )
        cg_id = cg_dir.name
        return cg_dir, cg_id

    raise ValueError("Either --codegen-id or --dir is required")


def _load_manifest(cg_dir: Path) -> Dict[str, Any]:
    """Load and validate manifest.json from codegen directory."""
    path = cg_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid manifest JSON: {e}")


def _load_strategy_code(cg_dir: Path) -> str:
    """Load strategy.py source code from codegen directory."""
    path = cg_dir / "strategy.py"
    if not path.exists():
        raise FileNotFoundError(f"Strategy file not found: {path}")
    return path.read_text(encoding="utf-8")


def _reverify_candidate(
    code: str,
    strategy_path: Path,
) -> ModuleType:
    """Re-run all v0.9a checks: static scan, sandbox import, dynamic validation.

    Returns imported module on success.
    Raises ValueError with accumulated errors on failure.
    """
    # Static scan
    scan_errors = scan_code(code)
    if scan_errors:
        raise ValueError(
            f"Static scan failed ({len(scan_errors)} violation(s))",
            scan_errors,
        )

    # Sandbox import
    try:
        module = import_candidate_strategy(str(strategy_path))
    except SandboxImportError as e:
        raise ValueError("Sandbox import rejected", [str(e)])
    except Exception as e:
        raise ValueError("Import failed", [str(e)])

    # Dynamic validation
    validation_errors = validate_candidate_code(module)
    if validation_errors:
        raise ValueError(
            f"Validation failed ({len(validation_errors)} error(s))",
            validation_errors,
        )

    return module
```

- [ ] **Step 2: Write mock evaluation data generator**

Add to the same file, after the Phase 2 section marker:

```python
# ---------------------------------------------------------------------------
# Phase 2: Evaluate
# ---------------------------------------------------------------------------


def _make_synthetic_data(n_bars: int = 2000) -> pd.DataFrame:
    """Generate synthetic OHLCV data for --mock-eval mode.

    Creates a trending random walk with realistic OHLC structure.
    """
    np.random.seed(42)
    base = 100.0
    close = base + np.cumsum(np.random.randn(n_bars) * 0.5)
    high = close + np.abs(np.random.randn(n_bars)) * 0.3
    low = close - np.abs(np.random.randn(n_bars)) * 0.3
    open_ = close - np.random.randn(n_bars) * 0.2

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.abs(np.random.randn(n_bars)) * 1000,
    })


def _load_data(mock_eval: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame, int]:
    """Load ETHUSDT 5m 1300d data (or synthetic for --mock-eval).

    Returns (df_is, df_oos, split_idx).
    """
    if mock_eval:
        df = _make_synthetic_data(2000)
        split_idx = int(len(df) * SPLIT_RATIO)
        return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy(), split_idx

    data_path = _find_eth_data()
    df_is, df_oos, split_idx = _load_and_split_data(data_path)
    return df_is, df_oos, split_idx
```

- [ ] **Step 3: Write the evaluation orchestration function**

Add after `_load_data`:

```python
def _compute_codegen_metrics(
    module: ModuleType,
    df_is: pd.DataFrame,
    df_oos: pd.DataFrame,
    split_idx: int,
) -> Dict[str, Any]:
    """Run generate_signals and compute all metrics.

    Returns a metrics dict matching oracle structure (without regime/correlation).
    """
    prices_is = df_is["close"].values.astype(float)
    prices_oos = df_oos["close"].values.astype(float)
    prices_full = np.concatenate([prices_is, prices_oos])
    df_full = pd.concat([df_is, df_oos], ignore_index=True)

    # Generate signals on full data
    try:
        signals_full = module.generate_signals(df_full.copy())
    except Exception as e:
        raise RuntimeError(f"generate_signals failed: {e}")

    if not isinstance(signals_full, np.ndarray):
        raise RuntimeError(
            f"generate_signals must return np.ndarray, got {type(signals_full)}"
        )
    if len(signals_full) != len(df_full):
        raise RuntimeError(
            f"Signal length {len(signals_full)} != data length {len(df_full)}"
        )

    signals_is = signals_full[:split_idx]
    signals_oos = signals_full[split_idx:]

    # Safe execution variants
    safe_full = _safe_execution_signals(signals_full)
    safe_is = safe_full[:split_idx]
    safe_oos = safe_full[split_idx:]

    # --- Evaluate IS ---
    is_raw = _evaluate_signals(signals_is, prices_is)
    is_safe = _evaluate_signals(safe_is, prices_is)

    # --- Evaluate OOS ---
    oos_raw = _evaluate_signals(signals_oos, prices_oos)
    oos_safe = _evaluate_signals(safe_oos, prices_oos)

    # --- Execution parity ---
    def _signal_parity(a: np.ndarray, b: np.ndarray) -> float:
        min_len = min(len(a), len(b))
        if min_len == 0:
            return 0.0
        return float(np.mean(a[:min_len] == b[:min_len]))

    exec_parity_is = _signal_parity(signals_is, safe_is)
    exec_parity_oos = _signal_parity(signals_oos, safe_oos)

    # --- Rolling metrics ---
    rolling = _compute_rolling_metrics(
        signals_full, prices_full, ROLLING_WINDOW_MONTHS,
        regimes=None, df=df_full,
    )

    # --- Fee sensitivity ---
    fee_is = _compute_fee_sensitivity(signals_is, prices_is)
    fee_oos = _compute_fee_sensitivity(signals_oos, prices_oos)

    return {
        "is": {"raw": is_raw, "safe_execution": is_safe},
        "oos": {"raw": oos_raw, "safe_execution": oos_safe},
        "rolling": rolling,
        "execution_parity": {"is": exec_parity_is, "oos": exec_parity_oos},
        "sensitivity": {
            "is": {"fees": fee_is},
            "oos": {"fees": fee_oos},
        },
    }
```

- [ ] **Step 4: Write Phase 3 (result building + scorecard + verdict override)**

```python
# ---------------------------------------------------------------------------
# Phase 3: Scorecard + Output
# ---------------------------------------------------------------------------


def _build_oracle_like_result(
    cg_id: str,
    manifest: Dict[str, Any],
    metrics: Dict[str, Any],
    commit: str,
    mock_eval: bool = False,
) -> Dict[str, Any]:
    """Build oracle-compatible result dict (lightweight, no baseline)."""
    timestamp = _now_iso()

    result = {
        "experiment_id": cg_id,
        "parent_id": manifest.get("parent_id", "codegen_v0.9a"),
        "candidate_role": "standalone",
        "timestamp": timestamp,
        "strategy": "codegen",
        "params_hash": f"codegen_v0.9b_{cg_id}",
        "data_hash": "synthetic" if mock_eval else "sha256:from_oracle",
        "commit": commit,
        "oracle_version": "codegen_adapter_v0.9b",
        "baseline_id": None,
        "split_id": f"ETHUSDT_5m_1300d_70_30_warmup",
        "metrics": metrics,
        "flags": _compute_flags(metrics),
        "checkpoint_path": None,
    }
    return result


def _compute_flags(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Compute PASS/WARN/REJECT status from metrics (minimal version)."""
    warnings: List[str] = []
    disqualifications: List[str] = []

    is_raw = metrics.get("is", {}).get("raw", {})
    oos_raw = metrics.get("oos", {}).get("raw", {})
    rolling = metrics.get("rolling", {})

    # Check for disqualifying conditions
    if is_raw.get("dd", 0) <= -0.40:
        disqualifications.append("IS drawdown exceeds -40%")
    if oos_raw.get("dd", 0) <= -0.50:
        disqualifications.append("OOS drawdown exceeds -50%")

    # Warnings
    if is_raw.get("trades_per_year", 0) < 10:
        warnings.append("Low trade frequency")
    if rolling.get("12m_min_return") is not None and rolling["12m_min_return"] < 0:
        warnings.append("Negative rolling 12-month minimum return")

    status = "PASS"
    if disqualifications:
        status = "REJECT"
    elif warnings:
        status = "WARN"

    return {
        "status": status,
        "warnings": warnings,
        "disqualifications": disqualifications,
        "baseline_known_risks": ["CODEGEN_RESEARCH_ONLY"],
    }


def _override_verdict(
    scorecard: Dict[str, Any],
    original_verdict: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Override scorecard verdict to enforce codegen research-only policy."""
    scorecard["_original_verdict"] = dict(original_verdict)

    scorecard["verdict"] = {
        "label": "codegen_research_only",
        "reason": (
            "Codegen candidates are research-only by policy. "
            "Human review and promotion are not available."
        ),
        "source_status": original_verdict.get(
            "source_status", result.get("flags", {}).get("status", "?")
        ),
        "description": "Codegen-generated strategy — research evaluation only.",
    }
    scorecard["promotion_eligible"] = False
    scorecard["promotion_block_reason"] = "CODEGEN_RESEARCH_ONLY"
    return scorecard


def _write_scorecard(scorecard: Dict[str, Any]) -> Path:
    """Write scorecard to codegen_scorecards/. Does NOT touch llm_results.tsv."""
    _ensure_dirs()
    eid = scorecard["experiment_id"]
    path = CODEGEN_SCORECARDS_DIR / f"{eid}_scorecard.json"
    path.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")
    return path


def _write_rejection_artifact(
    cg_id: str,
    reason: str,
    errors: List[str],
    manifest: Dict[str, Any],
) -> None:
    """Write rejection record for Phase 1 failure (timestamped filename)."""
    _ensure_dirs()
    ts = _now_iso().replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    record = {
        "codegen_id": cg_id,
        "timestamp": _now_iso(),
        "rejection_reason": reason,
        "errors": errors,
        "strategy_name": manifest.get("strategy_name", "?"),
    }
    path = REJECTED_DIR / f"rejected_codegen_{cg_id}_{ts}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(f"  Rejection record: {path}")


def _write_error_artifact(
    cg_id: str,
    reason: str,
    details: str,
    manifest: Dict[str, Any],
) -> None:
    """Write evaluation error record for Phase 2 failure (timestamped filename)."""
    _ensure_dirs()
    ts = _now_iso().replace(":", "").replace("-", "").replace("T", "_").replace("Z", "")
    record = {
        "codegen_id": cg_id,
        "timestamp": _now_iso(),
        "error_reason": reason,
        "details": details,
        "strategy_name": manifest.get("strategy_name", "?"),
    }
    path = REJECTED_DIR / f"error_codegen_{cg_id}_{ts}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(f"  Error record: {path}")


def _update_manifest(manifest_path: Path, status: str, scorecard_path: Optional[Path] = None) -> None:
    """Update manifest.json with evaluation status (non-dry-run only)."""
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evaluation_status"] = status
    manifest["evaluated_at"] = _now_iso()
    if scorecard_path:
        manifest["scorecard_path"] = str(scorecard_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
```

- [ ] **Step 5: Write the main orchestration function**

```python
# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def evaluate_codegen_candidate(
    codegen_id: Optional[str] = None,
    dir_path: Optional[Path] = None,
    *,
    dry_run: bool = False,
    force: bool = False,
    mock_eval: bool = False,
) -> Dict[str, Any]:
    """Run full three-phase evaluation for a codegen candidate.

    Returns a result dict with status, paths, and summary.
    """
    # --- Resolve input ---
    try:
        cg_dir, cg_id = _resolve_codegen_dir(codegen_id, dir_path)
    except (FileNotFoundError, ValueError) as e:
        return {"status": "input_error", "error": str(e), "exit_code": 1}

    manifest_path = cg_dir / "manifest.json"
    strategy_path = cg_dir / "strategy.py"

    result: Dict[str, Any] = {
        "codegen_id": cg_id,
        "codegen_dir": str(cg_dir),
        "status": "error",
        "exit_code": 1,
        "errors": [],
        "scorecard_path": None,
    }

    print(f"\n{'='*60}")
    print(f"  Codegen Evaluation Adapter v0.9b")
    print(f"  ID:   {cg_id}")
    print(f"  Dir:  {cg_dir}")
    print(f"  Mode: {'MOCK' if mock_eval else 'LIVE'}"
          f"{' (DRY RUN)' if dry_run else ''}")
    print(f"{'='*60}")

    # --- Phase 1: Load + Re-Verify ---
    print("\n[Phase 1] Loading candidate ...")
    try:
        manifest = _load_manifest(cg_dir)
        code = _load_strategy_code(cg_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"  [FAIL] {e}")
        result["status"] = "input_error"
        result["error"] = str(e)
        return result

    print(f"  Strategy: {manifest.get('strategy_name', '?')}")
    print(f"  Description: {manifest.get('description', '?')[:80]}")

    print("\n[Phase 1] Re-verifying candidate ...")
    try:
        module = _reverify_candidate(code, strategy_path)
        print("  [OK] All checks passed")
    except ValueError as e:
        reason, errors = e.args
        print(f"  [FAIL] {reason}")
        for err in errors:
            print(f"    - {err}")
        if not dry_run:
            _write_rejection_artifact(cg_id, reason, errors, manifest)
        result["status"] = "rejected"
        result["exit_code"] = 2
        result["errors"] = errors
        result["error"] = reason
        return result

    # --- Phase 2: Evaluate ---
    print("\n[Phase 2] Loading data ...")
    try:
        df_is, df_oos, split_idx = _load_data(mock_eval=mock_eval)
        print(f"  IS bars:  {len(df_is)}")
        print(f"  OOS bars: {len(df_oos)}")
    except Exception as e:
        print(f"  [FAIL] Data loading failed: {e}")
        if not dry_run:
            _write_error_artifact(cg_id, "data_load_failed", str(e), manifest)
        result["status"] = "evaluation_error"
        result["exit_code"] = 3
        result["error"] = str(e)
        return result

    print("\n[Phase 2] Computing metrics ...")
    try:
        metrics = _compute_codegen_metrics(module, df_is, df_oos, split_idx)
    except (RuntimeError, Exception) as e:
        print(f"  [FAIL] Evaluation error: {e}")
        if not dry_run:
            _write_error_artifact(cg_id, "evaluation_failed", str(e), manifest)
        result["status"] = "evaluation_error"
        result["exit_code"] = 3
        result["error"] = str(e)
        return result

    is_raw = metrics["is"]["raw"]
    oos_raw = metrics["oos"]["raw"]
    oos_safe = metrics["oos"]["safe_execution"]
    print(f"  IS raw:    return={is_raw.get('return', '?'):+.4f}  "
          f"DD={is_raw.get('dd', '?'):.4f}  Sharpe={is_raw.get('sharpe', '?'):.4f}")
    print(f"  OOS raw:   return={oos_raw.get('return', '?'):+.4f}  "
          f"DD={oos_raw.get('dd', '?'):.4f}  Sharpe={oos_raw.get('sharpe', '?'):.4f}")
    print(f"  OOS safe:  return={oos_safe.get('return', '?'):+.4f}  "
          f"DD={oos_safe.get('dd', '?'):.4f}")

    if dry_run:
        print("\n  (dry-run: stopping here, no files written)")
        result["status"] = "dry_run"
        result["exit_code"] = 0
        return result

    # --- Phase 3: Scorecard + Output ---
    print("\n[Phase 3] Building scorecard ...")

    # Get git commit for traceability
    commit = _get_git_commit()

    oracle_result = _build_oracle_like_result(
        cg_id, manifest, metrics, commit,
        mock_eval=mock_eval,
    )

    # Build candidate spec for scorecard description
    candidate_spec = {
        "experiment_id": cg_id,
        "strategy": "codegen",
        "description": manifest.get("description", ""),
        "hypothesis": manifest.get("hypothesis", ""),
    }

    scorecard = build_scorecard_from_result(oracle_result, candidate_spec)

    # Override verdict
    original_verdict = dict(scorecard.get("verdict", {}))
    scorecard = _override_verdict(scorecard, original_verdict, oracle_result)

    # Write scorecard
    sc_path = _write_scorecard(scorecard)
    print(f"  Scorecard: {sc_path}")

    # Update manifest
    _update_manifest(manifest_path, "evaluated", sc_path)
    print(f"  Manifest:  {manifest_path}")

    result["status"] = "success"
    result["exit_code"] = 0
    result["scorecard_path"] = str(sc_path)
    result["metrics"] = metrics

    print(f"\n{'='*60}")
    print(f"  Evaluation complete: {cg_id}")
    print(f"  Verdict: codegen_research_only (promotion_eligible=false)")
    print(f"  Scorecard: {sc_path}")
    print(f"{'='*60}\n")

    return result


def _get_git_commit() -> str:
    """Get short git commit hash, or '?' if not available."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=PROJECT_DIR,
        )
        return result.stdout.strip() if result.returncode == 0 else "?"
    except Exception:
        return "?"
```

- [ ] **Step 6: Write CLI entry point + main guard**

```python
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Codegen Evaluation Adapter v0.9b"
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--codegen-id", type=str, default=None,
        help="Codegen candidate ID (e.g., exp_0001)",
    )
    input_group.add_argument(
        "--dir", type=str, default=None,
        help="Direct path to codegen candidate directory",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Verify and print, no file writes",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-evaluate even if scorecard exists",
    )
    parser.add_argument(
        "--mock-eval", action="store_true",
        help="Use synthetic data instead of real market data (testing only)",
    )
    args = parser.parse_args()

    # Default to --codegen-id if neither specified
    if not args.codegen_id and not args.dir:
        parser.error("Either --codegen-id or --dir is required")

    result = evaluate_codegen_candidate(
        codegen_id=args.codegen_id,
        dir_path=Path(args.dir) if args.dir else None,
        dry_run=args.dry_run,
        force=args.force,
        mock_eval=args.mock_eval,
    )

    status = result.get("status", "error")
    exit_code = result.get("exit_code", 1)

    if status == "success":
        print(f"\n[OK] Evaluation complete: {result['codegen_id']}")
    elif status == "dry_run":
        print(f"\n[OK] Dry-run complete, no files written.")
    elif status == "rejected":
        print(f"\n[REJECTED] {result.get('error', '')}")
    elif status == "evaluation_error":
        print(f"\n[ERROR] {result.get('error', '')}")
    elif status == "input_error":
        print(f"\n[INPUT ERROR] {result.get('error', '')}")
    else:
        print(f"\n[FAIL] {status}: {result.get('error', '?')}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

---

### Task 2: Write the test suite

**Files:**
- Create: `tests/test_codegen_evaluation_v09b.py`

- [ ] **Step 1: Write test fixtures and imports**

```python
#!/usr/bin/env python3
"""Test suite for v0.9b Codegen Evaluation Adapter.

Tests:
    - Valid codegen candidate → evaluation → scorecard
    - Invalid code rejected before evaluation (exit 2)
    - generate_signals error → evaluation_error (exit 3)
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
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import numpy as np
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Sample strategy code (same as test_codegen_sandbox_v09a)
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
    raise RuntimeError("Intentional failure for testing")
"""

MOCK_MANIFEST = {
    "strategy_name": "ema_cross_test",
    "description": "Test strategy for v0.9b evaluation adapter",
    "hypothesis": "Test hypothesis — metrics are synthetic.",
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
    (tmpdir / "manifest.json").write_text(
        json.dumps(MOCK_MANIFEST, indent=2), encoding="utf-8"
    )
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def codegen_dir_broken():
    """Codegen directory with broken generate_signals."""
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "strategy.py").write_text(CODE_BROKEN_SIGNALS, encoding="utf-8")
    (tmpdir / "manifest.json").write_text(
        json.dumps(MOCK_MANIFEST, indent=2), encoding="utf-8"
    )
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def codegen_dir_no_manifest():
    """Codegen directory missing manifest.json."""
    tmpdir = Path(tempfile.mkdtemp())
    (tmpdir / "strategy.py").write_text(VALID_STRATEGY_CODE, encoding="utf-8")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)
```

- [ ] **Step 2: Write the evaluate_codegen_candidate import for tests**

```python
# ---------------------------------------------------------------------------
# Import the adapter
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_paths():
    """Ensure evaluate_codegen_candidate is importable from PROJECT_DIR."""
    import importlib
    # Force reimport in test context
    yield


def _run_eval(dir_path: Path, **kwargs) -> dict:
    """Helper to call evaluate_codegen_candidate with a dir path."""
    from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate
    return evaluate_codegen_candidate(
        dir_path=dir_path,
        **kwargs,
    )
```

- [ ] **Step 3: Write the happy-path evaluation test**

```python
# ===================================================================
# Tests: Happy path
# ===================================================================


class TestHappyPath:
    """Verify the full evaluation flow with a valid strategy."""

    def test_valid_candidate_evaluates(self, codegen_dir):
        """Full happy path: mock codegen → evaluation → scorecard."""
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
        assert ms.get("oos_safe_return") is not None

        # Cleanup
        sc_path.unlink()

    def test_fee_sensitivity_computed(self, codegen_dir):
        """Fee sensitivity in result has expected fee levels."""
        from scripts.evaluate_codegen_candidate import (
            evaluate_codegen_candidate,
        )
        # We need to inspect the oracle-like result, not just the scorecard.
        # Mock the full flow via --mock-eval and verify metrics.
        result = evaluate_codegen_candidate(
            dir_path=codegen_dir, mock_eval=True,
        )
        assert result["status"] == "success"
        metrics = result.get("metrics", {})
        fee_is = metrics.get("sensitivity", {}).get("is", {}).get("fees", {})
        fee_oos = metrics.get("sensitivity", {}).get("oos", {}).get("fees", {})

        assert len(fee_is) > 0, "IS fee sensitivity should not be empty"
        assert len(fee_oos) > 0, "OOS fee sensitivity should not be empty"

        # Check for known fee level keys
        for fee_dict in [fee_is, fee_oos]:
            for bps in [0, 2, 4, 10]:
                assert str(bps) in fee_dict or str(bps) + "bp" in fee_dict or bps in fee_dict, (
                    f"Missing fee level {bps}bp in {fee_dict}"
                )

        # Cleanup scorecard
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()

    def test_rolling_metrics_computed(self, codegen_dir):
        """Rolling min return fields are populated for both 6m and 12m."""
        from scripts.evaluate_codegen_candidate import (
            evaluate_codegen_candidate,
        )
        result = evaluate_codegen_candidate(
            dir_path=codegen_dir, mock_eval=True,
        )
        assert result["status"] == "success"
        metrics = result.get("metrics", {})
        rolling = metrics.get("rolling", {})

        assert "6m_min_return" in rolling, "Missing 6m rolling metric"
        assert "12m_min_return" in rolling, "Missing 12m rolling metric"

        # Cleanup scorecard
        if result.get("scorecard_path"):
            Path(result["scorecard_path"]).unlink()
```

- [ ] **Step 4: Write rejection and error handling tests**

```python
# ===================================================================
# Tests: Rejection & error handling
# ===================================================================


class TestRejectionAndErrors:
    """Verify invalid candidates are rejected before evaluation."""

    def test_invalid_code_rejected_before_evaluation(self, codegen_dir_forbidden):
        """Forbidden import → Phase 1 rejection, exit code 2, no evaluation."""
        result = _run_eval(codegen_dir_forbidden, mock_eval=True)
        assert result["status"] == "rejected", (
            f"Expected rejection, got {result['status']}"
        )
        assert result["exit_code"] == 2
        assert len(result.get("errors", [])) > 0

    def test_generate_signals_error_handled(self, codegen_dir_broken):
        """Broken generate_signals → evaluation_error, exit code 3."""
        result = _run_eval(codegen_dir_broken, mock_eval=True)
        assert result["status"] == "evaluation_error", (
            f"Expected evaluation_error, got {result['status']}"
        )
        assert result["exit_code"] == 3

    def test_input_error_missing_dir(self):
        """Non-existent directory → exit code 1."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate
        result = evaluate_codegen_candidate(
            dir_path=Path("/nonexistent/path/for/testing"),
        )
        assert result["status"] == "input_error"
        assert result["exit_code"] == 1

    def test_input_error_missing_manifest(self, codegen_dir_no_manifest):
        """Missing manifest.json → exit code 1."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate
        result = evaluate_codegen_candidate(
            dir_path=codegen_dir_no_manifest,
        )
        assert result["status"] == "input_error"
        assert result["exit_code"] == 1
```

- [ ] **Step 5: Write verdict override and dry-run tests**

```python
# ===================================================================
# Tests: Verdict override & dry-run
# ===================================================================


class TestVerdictOverride:
    """Verify verdict is always codegen_research_only regardless of metrics."""

    def test_verdict_always_codegen_research_only(self, codegen_dir):
        """Verdict label forced to codegen_research_only."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate
        result = evaluate_codegen_candidate(
            dir_path=codegen_dir, mock_eval=True,
        )
        assert result["status"] == "success"

        sc_path = Path(result["scorecard_path"])
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["verdict"]["label"] == "codegen_research_only"
        assert "promotion" not in scorecard["verdict"]["label"].lower()

        # Cleanup
        sc_path.unlink()

    def test_promotion_eligible_false(self, codegen_dir):
        """promotion_eligible=False and block_reason set."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate
        result = evaluate_codegen_candidate(
            dir_path=codegen_dir, mock_eval=True,
        )
        assert result["status"] == "success"

        sc_path = Path(result["scorecard_path"])
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["promotion_eligible"] is False
        assert scorecard["promotion_block_reason"] == "CODEGEN_RESEARCH_ONLY"

        # Cleanup
        sc_path.unlink()


class TestDryRun:
    """Verify --dry-run writes nothing."""

    def test_dry_run_no_writes(self, codegen_dir):
        """--dry-run writes nothing to disk."""
        from scripts.evaluate_codegen_candidate import evaluate_codegen_candidate

        # Get initial file listing
        scorecards_before = list(
            (PROJECT_DIR / "research_workspace" / "codegen_scorecards").glob("*")
        ) if (PROJECT_DIR / "research_workspace" / "codegen_scorecards").exists() else []

        result = evaluate_codegen_candidate(
            dir_path=codegen_dir, dry_run=True, mock_eval=True,
        )
        assert result["status"] == "dry_run"
        assert result["exit_code"] == 0

        # Verify no new scorecard files
        scorecards_after = list(
            (PROJECT_DIR / "research_workspace" / "codegen_scorecards").glob("*")
        ) if (PROJECT_DIR / "research_workspace" / "codegen_scorecards").exists() else []
        assert scorecards_after == scorecards_before, (
            f"Dry-run should not create files. Before: {scorecards_before}, After: {scorecards_after}"
        )

        # Verify manifest not updated
        manifest_path = codegen_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "evaluation_status" not in manifest, (
            "Dry-run should not update manifest"
        )
```

- [ ] **Step 6: Write oracle utility signature smoke test**

```python
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
        from scripts.research_oracle import _find_eth_data
        import inspect
        sig = inspect.signature(_find_eth_data)
        # No required args
        params = list(sig.parameters.keys())
        assert len(params) == 0, f"_find_eth_data should take no args, got: {params}"

    def test_evaluate_signals_signature(self):
        from scripts.research_oracle import _evaluate_signals
        import inspect
        sig = inspect.signature(_evaluate_signals)
        params = list(sig.parameters.keys())
        assert "signals" in params, f"_evaluate_signals needs 'signals' param, got: {params}"
        assert "prices" in params, f"_evaluate_signals needs 'prices' param, got: {params}"

    def test_safe_execution_signals_signature(self):
        from scripts.research_oracle import _safe_execution_signals
        import inspect
        sig = inspect.signature(_safe_execution_signals)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, f"_safe_execution_signals needs at least 1 param, got: {params}"

    def test_compute_rolling_metrics_signature(self):
        from scripts.research_oracle import _compute_rolling_metrics
        import inspect
        sig = inspect.signature(_compute_rolling_metrics)
        params = list(sig.parameters.keys())
        assert "signals" in params
        assert "prices" in params
        assert "window_months" in params

    def test_compute_fee_sensitivity_signature(self):
        from scripts.research_oracle import _compute_fee_sensitivity
        import inspect
        sig = inspect.signature(_compute_fee_sensitivity)
        params = list(sig.parameters.keys())
        assert "signals" in params
        assert "prices" in params
```

---

### Task 3: Run full regression + new tests

- [ ] **Step 1: Run existing tests to confirm no regressions**

Run: `uv run pytest tests/ -v --tb=short --ignore=tests/test_codegen_evaluation_v09b.py`

Expected: All existing tests pass, no new failures.

- [ ] **Step 2: Run the new v0.9b test suite**

Run: `uv run pytest tests/test_codegen_evaluation_v09b.py -v --tb=short`

Expected: All v0.9b tests pass.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --tb=short`

Expected: All tests pass (existing + new).

- [ ] **Step 4: Clean up any test artifacts**

Check `research_workspace/codegen_scorecards/` and `research_workspace/proposals/rejected_candidates/` for leftover test files. Remove if any.

---

## Self-Review Checklist

**1. Spec coverage:**
- §3 (import oracle utilities): Covered by Task 1 imports + signature smoke tests (Task 2 Step 6)
- §4 Phase 1 (re-verify): Covered by Task 1 Step 1 `_reverify_candidate`
- §4 Phase 2 (evaluate): Covered by Task 1 Step 2-3 `_load_data` + `_compute_codegen_metrics`
- §4 Phase 3 (scorecard): Covered by Task 1 Step 4
- §5 (oracle-like result format): Covered by Task 1 Step 4 `_build_oracle_like_result`
- §6 (verdict override): Covered by Task 1 Step 4 `_override_verdict` + Step 5 call site
- §7 (CLI): Covered by Task 1 Step 6
- §7 (exit codes): Covered by 0/1/2/3 in main(), tests verify each
- §8 (output files): Timestamped rejection files, dry-run skips manifest — all covered
- §9 (tests): All 12 test cases covered in Task 2

**2. Placeholder scan:** No TBDs, TODOs, or vague instructions in any step. Every step has complete code.

**3. Type consistency:**
- `_reverify_candidate` returns `ModuleType` — consumed by `_compute_codegen_metrics` which expects `ModuleType`
- `_compute_codegen_metrics` returns `Dict[str, Any]` with expected keys — consumed by `_build_oracle_like_result`
- `evaluate_codegen_candidate` returns `Dict[str, Any]` with `status`, `exit_code`, `errors`, `scorecard_path` — consumed by `main()`
- All field names consistent: `is/raw`, `is/safe_execution`, `oos/raw`, `oos/safe_execution`, `rolling`, `execution_parity`, `sensitivity`
- Scorecard override uses `scorecard["verdict"]["label"]` matched by test assertions

**4. Hard boundaries check:**
- No modifications to `research_oracle.py`, `dex/`, `score_candidate.py`, or any baseline/demo/live code
- No automatic promotion path
- Verdict always forced to `codegen_research_only`
- Scorecards written to `codegen_scorecards/`, not `llm_scorecards/`
