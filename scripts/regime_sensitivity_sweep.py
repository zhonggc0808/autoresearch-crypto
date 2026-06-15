#!/usr/bin/env python3
"""Regime sensitivity sweep — run oracle across multiple fast_days/slow_days settings.

Pure orchestration: calls the oracle as subprocess, never imports oracle internals.
Each setting runs a full oracle evaluation; results are collected into a structured
comparison report with a PASS/REVIEW/FAIL verdict.

Usage:
    uv run python scripts/regime_sensitivity_sweep.py
    uv run python scripts/regime_sensitivity_sweep.py --days 2600

Output:
    research_workspace/regime_sensitivity/
        sensitivity_report.json    — structured comparison with verdict
        results_comparison.tsv     — one-line-per-setting summary
        details_50_200.json        — raw oracle output per setting
        details_20_100.json
        details_100_300.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_DIR = Path(__file__).resolve().parents[1]
ORACLE_SCRIPT = str(PROJECT_DIR / "scripts" / "research_oracle.py")
OUTPUT_DIR = PROJECT_DIR / "research_workspace" / "regime_sensitivity"
ORACLE_DEFAULT_OUTPUT = PROJECT_DIR / "research_workspace" / "oracle_report.json"
DEFAULT_DATA_DIR = PROJECT_DIR / "data" / "crypto"

# Sweep sets: (label, fast_days, slow_days)
SETTINGS: List[Tuple[str, int, int]] = [
    ("default", 50, 200),
    ("fast", 20, 100),
    ("slow", 100, 300),
]

CHECKPOINT = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")

# Map display keys to raw oracle metric keys
METRIC_MAP: Dict[str, str] = {
    "oos_return": "return",
    "oos_dd": "dd",
    "oos_sharpe": "sharpe",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_pct(val: float) -> str:
    return f"{val * 100:+.2f}%"


def _resolve_data_path(days: int) -> str:
    """Find expected parquet file path for given days, return path or empty."""
    expected = DEFAULT_DATA_DIR / f"ETHUSDT_5m_{days}d.parquet"
    if expected.exists():
        return str(expected)
    # Fallback: search for any ETHUSDT_5m file with matching days
    for f in DEFAULT_DATA_DIR.glob(f"ETHUSDT_5m_{days}d.parquet"):
        return str(f)
    return ""


def _file_hash(path: str) -> str:
    """SHA256 short hash of a file."""
    p = Path(path)
    if not p.exists():
        return ""
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return f"sha256:{h}"


def _run_one(label: str, fast_days: int, slow_days: int) -> Dict[str, Any]:
    """Run oracle as subprocess for one setting, return parsed result."""
    print(f"  Running {label} ({fast_days}/{slow_days})...")
    cmd = [
        sys.executable, ORACLE_SCRIPT,
        "--checkpoint", CHECKPOINT,
        "--fast-days", str(fast_days),
        "--slow-days", str(slow_days),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print(f"  WARNING: oracle exited {proc.returncode} for {label}")
        if proc.stderr:
            print(f"  stderr: {proc.stderr[:500]}")
    if not ORACLE_DEFAULT_OUTPUT.exists():
        raise RuntimeError(
            f"Oracle did not produce {ORACLE_DEFAULT_OUTPUT} for {label}\n"
            f"stdout: {proc.stdout[:500]}"
        )
    report = json.loads(ORACLE_DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    # Archive a copy to the sensitivity directory
    detail_path = OUTPUT_DIR / f"details_{fast_days}_{slow_days}.json"
    detail_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"    → {detail_path}")
    return report


def _compute_verdict(settings_data: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Compute PASS/REVIEW/FAIL verdict from per-setting metrics.

    PASS: default (50/200) has highest Sharpe AND its DD is not >5pp worse
          than the best (highest/closest-to-zero) DD among all settings.
    REVIEW: default Sharpe is not highest, or default DD is >5pp worse.
    FAIL: default setting not found, or data inconsistency detected.
    """
    default = None
    for s in settings_data:
        if s["fast_days"] == 50 and s["slow_days"] == 200:
            default = s
            break
    if default is None:
        return "FAIL", "default 50/200 not found in sweep results"

    # Sharpe: default must be highest
    all_sharpes = [s["oos_sharpe"] for s in settings_data]
    best_sharpe = max(all_sharpes)
    default_sharpe_wins = abs(default["oos_sharpe"] - best_sharpe) < 1e-8

    # DD: default must not be >5pp worse than best DD
    # DD values are negative; "best" = highest (closest to zero)
    all_dds = [s["oos_dd"] for s in settings_data]
    best_dd = max(all_dds)
    dd_gap = best_dd - default["oos_dd"]  # positive means default is worse
    default_dd_ok = dd_gap <= 0.05

    if default_sharpe_wins and default_dd_ok:
        return (
            "PASS",
            f"default wins Sharpe ({default['oos_sharpe']:.2f}); "
            f"DD within 5pp of best ({default['oos_dd']:.4f} vs {best_dd:.4f})",
        )
    elif not default_sharpe_wins:
        return (
            "REVIEW",
            f"default Sharpe ({default['oos_sharpe']:.2f}) is not highest "
            f"(best: {best_sharpe:.2f})",
        )
    else:
        return (
            "REVIEW",
            f"default DD ({default['oos_dd']:.4f}) is >5pp worse "
            f"than best ({best_dd:.4f})",
        )


def _compute_comparison(
    results: List[Tuple[str, int, int, Dict[str, Any]]],
    data_days: int,
    data_path: str,
    data_hash: str,
) -> Dict[str, Any]:
    """Build structured comparison across all settings, keyed by setting label."""
    # Find default (50/200) as baseline for deltas
    default_result: Tuple[str, int, int, Dict[str, Any]] | None = None
    for label, fd, sd, r in results:
        if fd == 50 and sd == 200:
            default_result = (label, fd, sd, r)
            break

    settings_data: List[Dict[str, Any]] = []
    metric_deltas: Dict[str, Dict[str, str]] = {}
    regime_shifts: Dict[str, Dict[str, str]] = {}

    for label, fd, sd, r in results:
        m = r["metrics"]
        entry: Dict[str, Any] = {
            "label": label,
            "fast_days": fd,
            "slow_days": sd,
            "is_return": m["is"]["raw"]["return"],
            "is_dd": m["is"]["raw"]["dd"],
            "is_sharpe": m["is"]["raw"]["sharpe"],
            "oos_return": m["oos"]["raw"]["return"],
            "oos_dd": m["oos"]["raw"]["dd"],
            "oos_sharpe": m["oos"]["raw"]["sharpe"],
            "oos_safe_return": m["oos"]["safe_execution"]["return"],
            "oos_safe_dd": m["oos"]["safe_execution"]["dd"],
            "execution_parity": m["execution_parity"],
            "rolling_6m_min": m["rolling"].get("6m_min_return"),
            "rolling_12m_min": m["rolling"].get("12m_min_return"),
            "dd_over_50": any(
                v["dd"] < -0.50
                for v in [m["is"]["raw"], m["oos"]["raw"]]
            ),
            "status": r["flags"]["status"],
            "oracle_version": r["oracle_version"],
            "regime_filter": r["oracle"]["regime_filter"],
        }
        settings_data.append(entry)

        # Metric deltas vs default (only for non-default settings)
        if default_result is not None and (fd != 50 or sd != 200):
            _, _, _, dr = default_result
            key = f"{fd}_{sd}"
            for display_key, raw_key in METRIC_MAP.items():
                v = m["oos"]["raw"][raw_key]
                dv = dr["metrics"]["oos"]["raw"][raw_key]
                if isinstance(v, (int, float)) and isinstance(dv, (int, float)):
                    diff = v - dv
                    if raw_key == "return":
                        metric_deltas.setdefault(display_key, {})[key] = _format_pct(diff)
                    elif raw_key == "dd":
                        metric_deltas.setdefault(display_key, {})[key] = f"{diff * 100:+.2f}pp"
                    else:
                        metric_deltas.setdefault(display_key, {})[key] = f"{diff:+.4f}"

        # Regime return deltas vs default
        if default_result is not None and (fd != 50 or sd != 200):
            _, _, _, dr = default_result
            for regime in ["bull", "bear", "neutral"]:
                v = m["regime"].get(regime, {}).get("return", 0)
                dv = dr["metrics"]["regime"].get(regime, {}).get("return", 0)
                if isinstance(v, (int, float)) and isinstance(dv, (int, float)):
                    regime_shifts.setdefault(regime, {})[f"{fd}_{sd}"] = _format_pct(v - dv)

    # Compute verdict
    verdict, verdict_reason = _compute_verdict(settings_data)

    comparison: Dict[str, Any] = {
        "audit": {
            "date": _now_iso(),
            "oracle_version": settings_data[0]["oracle_version"],
            "data_days": data_days,
            "data_path": data_path,
            "data_hash": data_hash,
            "presets": [f"{fd}/{sd}" for _, fd, sd, _ in results],
        },
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "settings": settings_data,
        "comparison": {
            "metric_deltas_vs_default": metric_deltas,
            "regime_return_deltas_vs_default": regime_shifts,
        },
    }
    return comparison


def _write_tsv(results: List[Tuple[str, int, int, Dict[str, Any]]]) -> None:
    """Write one-line-per-setting TSV to sensitivity output dir."""
    rows: List[Dict[str, str]] = []
    for label, fd, sd, r in results:
        m = r["metrics"]
        f = r["flags"]
        is_r = m["is"]["raw"]
        oos_r = m["oos"]["raw"]
        oos_s = m["oos"]["safe_execution"]
        row = {
            "setting": label,
            "fast_days": str(fd),
            "slow_days": str(sd),
            "is_return": f"{is_r['return']:.6f}",
            "is_dd": f"{is_r['dd']:.6f}",
            "is_sharpe": f"{is_r['sharpe']:.6f}",
            "oos_return": f"{oos_r['return']:.6f}",
            "oos_dd": f"{oos_r['dd']:.6f}",
            "oos_sharpe": f"{oos_r['sharpe']:.6f}",
            "oos_safe_return": f"{oos_s['return']:.6f}",
            "oos_safe_dd": f"{oos_s['dd']:.6f}",
            "exec_parity": f"{m['execution_parity']:.4f}",
            "6m_min_return": str(m["rolling"].get("6m_min_return", "")),
            "12m_min_return": str(m["rolling"].get("12m_min_return", "")),
            "dd_over_50": str(
                any(v["dd"] < -0.50 for v in [is_r, oos_r])
            ),
            "status": f["status"],
        }
        rows.append(row)

    tsv_path = OUTPUT_DIR / "results_comparison.tsv"
    if rows:
        fieldnames = list(rows[0].keys())
        with open(tsv_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
    print(f"    → {tsv_path}")


def _print_summary(settings_data: List[Dict[str, Any]], verdict: str, verdict_reason: str) -> None:
    """Print formatted summary table to console."""
    print("\n" + "=" * 60)
    print("  REGIME SENSITIVITY AUDIT SUMMARY")
    print("=" * 60)
    header = f"{'Setting':<12} {'OOS Return':>10} {'OOS DD':>10} {'Sharpe':>8} {'6m Min':>8} {'12m Min':>8}"
    print(header)
    print("-" * len(header))
    for s in settings_data:
        r6 = f"{s['rolling_6m_min']:.4f}" if s['rolling_6m_min'] is not None else "N/A"
        r12 = f"{s['rolling_12m_min']:.4f}" if s['rolling_12m_min'] is not None else "N/A"
        print(
            f"{s['label']:<12}"
            f" {s['oos_return']:>10.4f}"
            f" {s['oos_dd']:>10.4f}"
            f" {s['oos_sharpe']:>8.2f}"
            f" {r6:>8}"
            f" {r12:>8}"
        )
    print("-" * len(header))
    dd_note = "(DD: higher/closer-to-zero = better)"
    print(f"  {dd_note}")
    print(f"\n  Verdict: {verdict} — {verdict_reason}")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regime sensitivity sweep — PASS/REVIEW/FAIL audit across EMA presets",
    )
    parser.add_argument(
        "--days", type=int, default=1300,
        help="Expected data horizon in days (default: 1300). Used for validation "
             "and traceability. The oracle uses whatever data it finds; the sweep "
             "records the expected days, actual path, and hash for audit.",
    )
    args = parser.parse_args()

    print("=== Regime Sensitivity Sweep ===")
    print(f"  Checkpoint: {CHECKPOINT}")
    print(f"  Settings: {[f'{l} ({f}/{s})' for l, f, s in SETTINGS]}")
    print(f"  Requested data: {args.days}d")
    print(f"  Output: {OUTPUT_DIR}")

    # Resolve data path for traceability
    data_path = _resolve_data_path(args.days)
    data_hash = _file_hash(data_path) if data_path else ""
    if data_path:
        print(f"  Data: {data_path}")
        print(f"  Hash: {data_hash}")
    else:
        print(f"  Data: ETHUSDT_5m_{args.days}d.parquet not found "
              f"(oracle will use its default)")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: List[Tuple[str, int, int, Dict[str, Any]]] = []

    t0 = time.time()
    for label, fast, slow in SETTINGS:
        print(f"[{label}] fast_days={fast}, slow_days={slow}")
        report = _run_one(label, fast, slow)
        results.append((label, fast, slow, report))
        print()

    # Build comparison with verdict
    comparison = _compute_comparison(results, args.days, data_path, data_hash)

    # Print inline summary
    _print_summary(
        comparison["settings"],
        comparison["verdict"],
        comparison["verdict_reason"],
    )

    # Write reports
    report_path = OUTPUT_DIR / "sensitivity_report.json"
    report_path.write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    print(f"\n  Report: {report_path}")

    _write_tsv(results)

    elapsed = time.time() - t0
    print(f"\nDone. Elapsed: {elapsed:.1f}s")
    print(f"Results in: {OUTPUT_DIR}")
    return 0 if comparison["verdict"] in ("PASS", "REVIEW") else 1


if __name__ == "__main__":
    sys.exit(main())
