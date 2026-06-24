#!/usr/bin/env python3
"""Codegen Evaluation Adapter v0.9b — re-verify, evaluate, and score codegen candidates.

Three-phase flow:
    Phase 1 (Load + Re-Verify):
        - Read manifest.json + strategy.py from codegen_candidates/{cg_id}/
        - Static scan (scan_candidate_code.scan_code)
        - Sandbox import (sandbox_import_candidate.import_candidate_strategy)
        - Dynamic validation (validate_code_candidate.validate_candidate_code)
        -> On failure: write rejection artifact, exit 2

    Phase 2 (Evaluate):
        - Load ETHUSDT 5m 1300d data (or synthetic for --mock-eval)
        - Split 70/30 IS/OOS
        - module.generate_signals(df) -> signals
        - Compute: IS raw, IS safe-exec, OOS raw, OOS safe-exec
        - Rolling metrics (6m, 12m)
        - Fee sensitivity (0bp, 2bp, 4bp, 10bp)
        - Execution parity (IS, OOS)
        -> On error: write evaluation_error artifact, exit 3

    Phase 3 (Scorecard):
        - Build oracle-like result dict
        - score_candidate.build_scorecard_from_result() -> scorecard
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
    0 - evaluation success: scorecard written
    1 - input error (unknown ID, missing files, invalid manifest)
    2 - candidate rejected before evaluation (Phase 1 failure)
    3 - evaluation error (Phase 2 failure)

Hard boundaries:
    - Does NOT modify research_oracle.py (read-only imports)
    - Does NOT modify dex/ (only imports StrategyEvaluator)
    - Does NOT modify score_candidate.py (only imports build_scorecard_from_result)
    - Does NOT modify baseline/demo/live code
    - Verdict always forced to codegen_research_only
    - Scorecards written to codegen_scorecards/ (not llm_scorecards/)
    - No writes to llm_results.tsv
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ---------------------------------------------------------------------------
# Oracle utility imports (read-only compatibility layer)
# ---------------------------------------------------------------------------
# These are private functions imported for reuse. If a signature changes,
# the adapter fails hard — it does not silently change evaluation semantics.
# When this path is stable, v1.1 may publicise these in an oracle utils module.
from scripts.research_oracle import (  # noqa: E402
    _compute_fee_sensitivity,
    _compute_rolling_metrics,
    _evaluate_signals,
    _find_eth_data,
    _load_and_split_data,
    _safe_execution_signals,
)
from scripts.sandbox_import_candidate import (  # noqa: E402
    SandboxImportError,
    import_candidate_strategy,
)

# ---------------------------------------------------------------------------
# Sandbox / validation imports
# ---------------------------------------------------------------------------
from scripts.scan_candidate_code import scan_code  # noqa: E402

# ---------------------------------------------------------------------------
# Scorecard import (pure function only — no I/O)
# ---------------------------------------------------------------------------
from scripts.score_candidate import build_scorecard_from_result  # noqa: E402
from scripts.validate_code_candidate import validate_candidate_code  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CODEGEN_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "codegen_candidates"
CODEGEN_SCORECARDS_DIR = PROJECT_DIR / "research_workspace" / "codegen_scorecards"
REJECTED_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "rejected_candidates"

# Oracle-equivalent constants
SPLIT_RATIO = 0.70
ROLLING_WINDOW_MONTHS = [6, 12]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            raise FileNotFoundError(f"Codegen directory not found: {cg_dir}")
        return cg_dir, codegen_id

    if dir_path is not None:
        cg_dir = Path(dir_path).resolve()
        if not cg_dir.is_dir():
            raise FileNotFoundError(f"Codegen directory not found: {cg_dir}")
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
            "Static scan failed",
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
            "Validation failed",
            validation_errors,
        )

    return module


# ---------------------------------------------------------------------------
# Phase 2: Evaluate
# ---------------------------------------------------------------------------


def _make_synthetic_data(n_bars: int = 2000) -> pd.DataFrame:
    """Generate synthetic OHLCV data for --mock-eval mode."""
    np.random.seed(42)
    base = 100.0
    close = base + np.cumsum(np.random.randn(n_bars) * 0.5)
    high = close + np.abs(np.random.randn(n_bars)) * 0.3
    low = close - np.abs(np.random.randn(n_bars)) * 0.3
    open_ = close - np.random.randn(n_bars) * 0.2

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.abs(np.random.randn(n_bars)) * 1000,
        }
    )


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


def _compute_codegen_metrics(
    module: ModuleType,
    df_is: pd.DataFrame,
    df_oos: pd.DataFrame,
    split_idx: int,
) -> Dict[str, Any]:
    """Run generate_signals and compute metrics across IS/OOS.

    Returns a metrics dict matching the oracle's structure
    (without regime breakdown or baseline correlation).
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
        raise RuntimeError(f"generate_signals must return np.ndarray, got {type(signals_full)}")
    if len(signals_full) != len(df_full):
        raise RuntimeError(f"Signal length {len(signals_full)} != data length {len(df_full)}")

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
        signals_full,
        prices_full,
        ROLLING_WINDOW_MONTHS,
        regimes=None,
        df=df_full,
    )

    # --- Fee sensitivity ---
    fee_is = _compute_fee_sensitivity(signals_is, prices_is)
    fee_oos = _compute_fee_sensitivity(signals_oos, prices_oos)

    return {
        "is": {"raw": is_raw, "safe_execution": is_safe},
        "oos": {"raw": oos_raw, "safe_execution": oos_safe},
        "rolling": rolling,
        # Single float matching oracle format (score_candidate reads this).
        # Use OOS parity as primary; full breakdown in execution_parity_detail.
        "execution_parity": exec_parity_oos,
        "execution_parity_detail": {"is": exec_parity_is, "oos": exec_parity_oos},
        "sensitivity": {
            "is": {"fees": fee_is},
            "oos": {"fees": fee_oos},
        },
    }


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

    result: Dict[str, Any] = {
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
        "split_id": "ETHUSDT_5m_1300d_70_30_warmup",
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
    if is_raw.get("trades_per_year", 0) > 300:
        disqualifications.append("Trade frequency exceeds 300/yr codegen cap")
    if is_raw.get("trades_per_year", 0) < 20:
        disqualifications.append("Trade frequency below 20/yr codegen floor")

    # Warnings
    if 20 <= is_raw.get("trades_per_year", 0) < 30:
        warnings.append("Low trade frequency")
    r12m = rolling.get("12m_min_return")
    if r12m is not None and r12m < 0:
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
    """Override scorecard verdict to enforce codegen research-only policy.

    The original verdict is preserved in _original_verdict for debugging.
    The top-level verdict.label is the authority for all consumers.
    """
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
        "description": "Codegen-generated strategy -- research evaluation only.",
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


def _update_manifest(
    manifest_path: Path,
    status: str,
    scorecard_path: Optional[Path] = None,
) -> None:
    """Update manifest.json with evaluation status (non-dry-run only)."""
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evaluation_status"] = status
    manifest["evaluated_at"] = _now_iso()
    if scorecard_path:
        manifest["scorecard_path"] = str(scorecard_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _get_git_commit() -> str:
    """Get short git commit hash, or '?' if not available."""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=PROJECT_DIR,
        )
        return result.stdout.strip() if result.returncode == 0 else "?"
    except Exception:
        return "?"


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

    Parameters
    ----------
    codegen_id : str, optional
        Codegen candidate ID (e.g. exp_0001). Resolves to
        research_workspace/codegen_candidates/{codegen_id}/.
    dir_path : Path, optional
        Direct path to codegen candidate directory.
    dry_run : bool
        If True, verify and print only, skip all file writes.
    force : bool
        If True, re-evaluate even if scorecard exists.
    mock_eval : bool
        If True, use synthetic data instead of real market data
        (testing only — never used in production).
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

    print(f"\n{'=' * 60}")
    print("  Codegen Evaluation Adapter v0.9b")
    print(f"  ID:   {cg_id}")
    print(f"  Dir:  {cg_dir}")
    print(f"  Mode: {'MOCK' if mock_eval else 'LIVE'}{' (DRY RUN)' if dry_run else ''}")
    print(f"{'=' * 60}")

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
    desc = manifest.get("description", "?")
    desc_preview = f"{desc[:80]}{'...' if len(desc) > 80 else ''}"
    print(f"  Description: {desc_preview.encode('ascii', 'replace').decode('ascii')}")

    print("\n[Phase 1] Re-verifying candidate ...")
    try:
        module = _reverify_candidate(code, strategy_path)
        print("  [OK] All checks passed")
    except ValueError as e:
        reason, errors_list = e.args
        print(f"  [FAIL] {reason}")
        for err in errors_list:
            print(f"    - {err}")
        if not dry_run:
            _write_rejection_artifact(cg_id, reason, errors_list, manifest)
        result["status"] = "rejected"
        result["exit_code"] = 2
        result["errors"] = errors_list
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
        t0 = time.time()
        metrics = _compute_codegen_metrics(module, df_is, df_oos, split_idx)
        elapsed = time.time() - t0
        print(f"  Evaluation completed in {elapsed:.1f}s")
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
    print(
        f"  IS raw:    return={is_raw.get('return', '?'):+.4f}  "
        f"DD={is_raw.get('dd', '?'):.4f}  "
        f"Sharpe={is_raw.get('sharpe', '?'):.4f}"
    )
    print(
        f"  OOS raw:   return={oos_raw.get('return', '?'):+.4f}  "
        f"DD={oos_raw.get('dd', '?'):.4f}  "
        f"Sharpe={oos_raw.get('sharpe', '?'):.4f}"
    )
    print(
        f"  OOS safe:  return={oos_safe.get('return', '?'):+.4f}  DD={oos_safe.get('dd', '?'):.4f}"
    )

    if dry_run:
        print("\n  (dry-run: stopping here, no files written)")
        result["status"] = "dry_run"
        result["exit_code"] = 0
        return result

    # --- Phase 3: Scorecard + Output ---
    print("\n[Phase 3] Building scorecard ...")

    commit = _get_git_commit()

    oracle_result = _build_oracle_like_result(
        cg_id,
        manifest,
        metrics,
        commit,
        mock_eval=mock_eval,
    )

    candidate_spec: Dict[str, Any] = {
        "experiment_id": cg_id,
        "strategy": "codegen",
        "description": manifest.get("description", ""),
        "hypothesis": manifest.get("hypothesis", ""),
    }

    scorecard = build_scorecard_from_result(oracle_result, candidate_spec)

    # Override verdict (hard rule: codegen is never promotable)
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

    print(f"\n{'=' * 60}")
    print(f"  Evaluation complete: {cg_id}")
    print("  Verdict: codegen_research_only (promotion_eligible=false)")
    print(f"  Scorecard: {sc_path}")
    print(f"{'=' * 60}\n")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Codegen Evaluation Adapter v0.9b")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--codegen-id",
        type=str,
        default=None,
        help="Codegen candidate ID (e.g., exp_0001)",
    )
    input_group.add_argument(
        "--dir",
        type=str,
        default=None,
        help="Direct path to codegen candidate directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify and print, no file writes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-evaluate even if scorecard exists (currently unused)",
    )
    parser.add_argument(
        "--mock-eval",
        action="store_true",
        help="Use synthetic data instead of real market data (testing only)",
    )
    args = parser.parse_args()

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

    status_labels = {
        "success": "[OK]",
        "dry_run": "[OK]",
        "rejected": "[REJECTED]",
        "evaluation_error": "[ERROR]",
        "input_error": "[INPUT ERROR]",
    }
    label = status_labels.get(status, "[FAIL]")
    msg = result.get("error", "")
    print(f"\n{label} {status}{': ' + msg if msg else ''}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
