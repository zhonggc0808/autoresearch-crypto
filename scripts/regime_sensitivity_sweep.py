#!/usr/bin/env python3
"""Regime sensitivity sweep — run oracle across multiple fast_days/slow_days settings.

Pure orchestration: calls the oracle as subprocess, never imports oracle internals.
Each setting runs a full oracle evaluation; results are collected into a structured
comparison report.

Usage:
    uv run python scripts/regime_sensitivity_sweep.py

Output:
    research_workspace/regime_sensitivity/
        sensitivity_report.json    — structured comparison across all settings
        results_comparison.tsv     — one-line-per-setting summary
        details_50_200.json        — raw oracle output per setting
        details_20_100.json
        details_100_300.json
"""

from __future__ import annotations

import csv
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


def _compute_comparison(
    results: List[Tuple[str, int, int, Dict[str, Any]]],
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

    comparison: Dict[str, Any] = {
        "timestamp": _now_iso(),
        "checkpoint": CHECKPOINT,
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


def main() -> int:
    print("=== Regime Sensitivity Sweep ===")
    print(f"  Checkpoint: {CHECKPOINT}")
    print(f"  Settings: {[f'{l} ({f}/{s})' for l, f, s in SETTINGS]}")
    print(f"  Output: {OUTPUT_DIR}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: List[Tuple[str, int, int, Dict[str, Any]]] = []

    t0 = time.time()
    for label, fast, slow in SETTINGS:
        print(f"[{label}] fast_days={fast}, slow_days={slow}")
        report = _run_one(label, fast, slow)
        results.append((label, fast, slow, report))
        print()

    # Build and write comparison report
    comparison = _compute_comparison(results)
    report_path = OUTPUT_DIR / "sensitivity_report.json"
    report_path.write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    print(f"  Report: {report_path}")

    # Write TSV
    _write_tsv(results)

    elapsed = time.time() - t0
    print(f"\nDone. Elapsed: {elapsed:.1f}s")
    print(f"Results in: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
