# Oracle v0.2 — Configurable Regime Audit (Phase 5A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the research oracle from v0.1.0 to v0.2 with configurable EMA regime parameters (`--fast-days`/`--slow-days`), preserving exact behavior under defaults, with parity test and sensitivity sweep wrapper.

**Architecture:** Three sequential changes to `scripts/research_oracle.py`: (1) thread CLI parameters through 3 `build_daily_regime_labels()` call sites and bump metadata, (2) add a parity test against the frozen v0.1.0 baseline snapshot, (3) create a standalone sweep wrapper script that calls oracle via subprocess. No changes to `dex/regime_filter.py` or `dex/regime_permissions.py`.

**Tech Stack:** Python 3.10+, `argparse`, `hashlib`, `subprocess`, `pandas`, `numpy`

---

### Task 1: Parameter Threading + v0.2 Metadata (5A-1)

**Files:**
- Modify: `scripts/research_oracle.py` — CLI args, threading, version bump, metadata

- [ ] **Step 1: Bump oracle version and add regime_filter fields**

Change `ORACLE_VERSION` and add the `oracle` metadata block to `run_oracle()` output.

In `scripts/research_oracle.py`, line 89:
```python
# Old:
ORACLE_VERSION = "v0.1.0"
# New:
ORACLE_VERSION = "v0.2"
```

In the `main()` function (around line 929), update the banner:
```python
# Old:
print(f"=== Research Oracle (Phase 2 freeze {ORACLE_VERSION}) ===")
# New:
print(f"=== Research Oracle {ORACLE_VERSION} ===")
```

At the end of `run_oracle()` (around line 819, before `return result`), add the `oracle` metadata block:
```python
    # --- Add version fields ---
    result["oracle_version"] = ORACLE_VERSION
    result["oracle"] = {
        "version": ORACLE_VERSION,
        "regime_filter": {
            "fast_days": fast_days,
            "slow_days": slow_days,
        },
    }
    result["baseline_id"] = BASELINE_ID
    result["split_id"] = SPLIT_ID
    result["checkpoint_hash"] = checkpoint_hash
    
    return result
```

The existing lines that set `result["oracle_version"]` (line 816), `result["baseline_id"]` (line 817), and `result["split_id"]` (line 818) remain — the `result["oracle"]` block is new. Ensure the final ordering produces:
```json
{
    "oracle_version": "v0.2",
    "oracle": {
        "version": "v0.2",
        "regime_filter": {"fast_days": 50, "slow_days": 200}
    },
    ...
}
```

- [ ] **Step 2: Add CLI arguments + validation**

In `main()`, add two new arguments to the parser (after existing args, around line 949):
```python
    parser.add_argument(
        "--fast-days", type=int, default=50,
        help="EMA fast period for regime labels (default: 50)",
    )
    parser.add_argument(
        "--slow-days", type=int, default=200,
        help="EMA slow period for regime labels (default: 200)",
    )
```

After `args = parser.parse_args()` (around line 960), add validation:
```python
    if args.fast_days <= 0:
        parser.error("--fast-days must be > 0")
    if args.slow_days <= 0:
        parser.error("--slow-days must be > 0")
    if args.fast_days >= args.slow_days:
        parser.error("--fast-days must be less than --slow-days")
```

- [ ] **Step 3: Thread parameters through run_oracle() signature**

Change the `run_oracle()` function signature (line ~596):
```python
# Old:
def run_oracle(
    checkpoint_path: Optional[str] = None,
    candidate_path: Optional[str] = None,
    use_baseline: bool = False,
) -> Dict[str, Any]:
# New:
def run_oracle(
    checkpoint_path: Optional[str] = None,
    candidate_path: Optional[str] = None,
    use_baseline: bool = False,
    fast_days: int = 50,
    slow_days: int = 200,
) -> Dict[str, Any]:
```

Update all 3 `build_daily_regime_labels()` calls inside `run_oracle()` to use the parameters:

Call site [1] — inside `_generate_v21_signals()` (line ~253). The function must receive the parameters:
```python
# In run_oracle(), around line ~655:
if is_v21:
    signals_raw_full = _generate_v21_signals(checkpoint, df_full, fast_days, slow_days)
```

Call site [2] — Phase 3B filter branch (line ~674):
```python
# Inside `if _filter_config is not None:` block:
regimes_full = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
```

Call site [3] — Main pre-compute (line ~681):
```python
# After the filter block, unconditional:
regimes_full = build_daily_regime_labels(df_full, fast_days=fast_days, slow_days=slow_days)
```

- [ ] **Step 4: Thread parameters through _generate_v21_signals()**

Change the `_generate_v21_signals()` function signature (line ~233):
```python
# Old:
def _generate_v21_signals(checkpoint: Dict[str, Any], df: pd.DataFrame) -> np.ndarray:
# New:
def _generate_v21_signals(
    checkpoint: Dict[str, Any],
    df: pd.DataFrame,
    fast_days: int = 50,
    slow_days: int = 200,
) -> np.ndarray:
```

Inside `_generate_v21_signals()`, update the `build_daily_regime_labels()` call (line ~253):
```python
# Old:
regimes = build_daily_regime_labels(df, fast_days=50, slow_days=200)
# New:
regimes = build_daily_regime_labels(df, fast_days=fast_days, slow_days=slow_days)
```

- [ ] **Step 5: Thread parameters through main() → run_oracle()**

In `main()`, update the `run_oracle()` call (around line ~968):
```python
    result = run_oracle(
        checkpoint_path=args.checkpoint,
        candidate_path=args.candidate,
        use_baseline=args.baseline,
        fast_days=args.fast_days,
        slow_days=args.slow_days,
    )
```

- [ ] **Step 6: Update the CLI banner to show regime parameters**

In `main()`, after loading, add regime parameter info to the console output (around line ~963):
```python
    print(f"  Regime: EMA fast={args.fast_days}d / slow={args.slow_days}d")
```

- [ ] **Step 7: Run the existing test suite to verify no regression**

```bash
cd D:\aiproject2\autoresearch-crypto
uv run python tests/test_research_oracle.py
```

Expected: 15/15 passed (all existing tests still pass with v0.2).

- [ ] **Step 8: Quick smoke test — run oracle baseline with defaults**

```bash
uv run python scripts/research_oracle.py --baseline
```

Expected: Banner shows `=== Research Oracle v0.2 ===` and `Regime: EMA fast=50d / slow=200d`. Exit code 0. `research_workspace/oracle_report.json` contains the new `oracle` block.

- [ ] **Step 9: Commit**

```bash
git add scripts/research_oracle.py
git commit -m "feat: oracle v0.2 with configurable regime parameters (--fast-days/--slow-days)

- Bump ORACLE_VERSION to v0.2
- Add --fast-days (50) / --slow-days (200) CLI args with validation
- Thread parameters through 3 build_daily_regime_labels call sites
- Add oracle metadata block to output JSON (version + regime_filter)
- No changes to dex/regime_permissions.py, dex/regime_filter.py, or checkpoints

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Parity Test (5A-2)

**Files:**
- Modify: `tests/test_research_oracle.py` — add v0.2 default parity test

- [ ] **Step 1: Add the parity test**

Append to `tests/test_research_oracle.py` (before the `if __name__ == "__main__":` block):

```python
def test_v02_default_parity():
    """v0.2 default 50/200 must reproduce v0.1.0 metrics and signal path exactly."""
    import hashlib
    import json

    # --- Load frozen v0.1.0 baseline fixture ---
    v010_path = research_oracle.BASELINE_DIR / f"{research_oracle.BASELINE_ID}_oracle_v0.1.0.json"
    assert v010_path.exists(), f"v0.1.0 fixture not found: {v010_path}"
    v010 = json.loads(v010_path.read_text())

    # --- Run v0.2 oracle with defaults (50/200) ---
    result = research_oracle.run_oracle(checkpoint_path=CKPT, fast_days=50, slow_days=200)

    # --- 1. OOS metrics parity (1e-8) ---
    for metric in ["return", "dd", "sharpe"]:
        v2_val = result["metrics"]["oos"]["raw"][metric]
        v1_val = v010["metrics"]["oos"]["raw"][metric]
        assert abs(v2_val - v1_val) < 1e-8, \
            f"OOS {metric} mismatch: v0.2={v2_val} vs v0.1.0={v1_val}"

    # --- 2. IS metrics parity (1e-8) ---
    for metric in ["return", "dd", "sharpe"]:
        v2_val = result["metrics"]["is"]["raw"][metric]
        v1_val = v010["metrics"]["is"]["raw"][metric]
        assert abs(v2_val - v1_val) < 1e-8, \
            f"IS {metric} mismatch: v0.2={v2_val} vs v0.1.0={v1_val}"

    # --- 3. Regime label counts parity ---
    # Load data and generate regimes using the same path as run_oracle
    data_path = research_oracle._find_eth_data()
    df_is, df_oos, _ = research_oracle._load_and_split_data(data_path)
    df_full = pd.concat([df_is, df_oos], ignore_index=True)
    regimes = research_oracle.build_daily_regime_labels(
        df_full, fast_days=50, slow_days=200,
    )
    bull_count = int((regimes == "BULL").sum())
    bear_count = int((regimes == "BEAR").sum())
    neutral_count = int((regimes == "NEUTRAL").sum())
    total = len(regimes)
    assert bull_count + bear_count + neutral_count == total, \
        f"Regime counts {bull_count}+{bear_count}+{neutral_count} != {total}"
    # Every regime has at least some bars (verified on 1300d ETH data)
    assert bull_count > 0, "No BULL regime labels — data or function issue"
    assert bear_count > 0, "No BEAR regime labels — data or function issue"
    assert neutral_count > 0, "No NEUTRAL regime labels — data or function issue"
    # Compare regime breakdown returns against v0.1.0 fixture
    for regime in ["bull", "bear", "neutral"]:
        v2_r = result["metrics"]["regime"].get(regime, {}).get("return", 0)
        v1_r = v010["metrics"]["regime"].get(regime, {}).get("return", 0)
        assert abs(v2_r - v1_r) < 1e-6, \
            f"Regime {regime} return mismatch: v0.2={v2_r} vs v0.1.0={v1_r}"

    # --- 4. Signal pipeline determinism ---
    # Generate signals twice and verify they match (proves pipeline is deterministic)
    from dex.checkpoints import load_checkpoint
    ckpt = load_checkpoint(CKPT)
    signals_a = research_oracle._generate_v21_signals(ckpt, df_full, fast_days=50, slow_days=200)
    signals_b = research_oracle._generate_v21_signals(ckpt, df_full, fast_days=50, slow_days=200)
    hash_a = hashlib.sha256(signals_a.tobytes()).hexdigest()[:16]
    hash_b = hashlib.sha256(signals_b.tobytes()).hexdigest()[:16]
    assert hash_a == hash_b, "Signal pipeline not deterministic"
    # Verify the hash doesn't accidentally match the wrong settings
    signals_alt = research_oracle._generate_v21_signals(
        ckpt, df_full, fast_days=20, slow_days=100,
    )
    hash_alt = hashlib.sha256(signals_alt.tobytes()).hexdigest()[:16]
    assert hash_a != hash_alt, "Different regime params produced same signal hash"

    # --- 5. Version field is NOT compared (expected to differ) ---
    assert result["oracle_version"] == "v0.2"
    assert v010.get("oracle_version") == "v0.1.0"

    # --- 6. Verify v0.2 oracle metadata block ---
    assert result["oracle"]["regime_filter"]["fast_days"] == 50
    assert result["oracle"]["regime_filter"]["slow_days"] == 200

    print("  [PASS] v0.2 default 50/200 parity against v0.1.0 fixture")
```

- [ ] **Step 2: Register the test in the runner**

In the test runner list (around line 224), add after `test_baseline_json_vs_pt_parity`:
```python
        ("v0.2 parity", test_v02_default_parity),
```

The full list should now have 16 tests.

- [ ] **Step 3: Run all tests**

```bash
cd D:\aiproject2\autoresearch-crypto
uv run python tests/test_research_oracle.py
```

Expected: 16/16 passed. If regime breakdown is not in v0.1.0 snapshot, the regime-return comparison may need a `v010.get("metrics", {}).get("regime", {})` guard — adjust if the fixture structure differs.

- [ ] **Step 4: Commit**

```bash
git add tests/test_research_oracle.py
git commit -m "test: add v0.2 default parity test against v0.1.0 fixture

Verifies: OOS/IS metrics parity (1e-8), regime label counts and
breakdown returns, signal pipeline determinism, cross-parameter
hash divergence, and oracle metadata block.
Does NOT compare oracle_version field (expected to differ).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Sensitivity Sweep Wrapper (5A-3)

**Files:**
- Create: `scripts/regime_sensitivity_sweep.py` — standalone subprocess-only orchestration

- [ ] **Step 1: Create the sweep script**

Write `scripts/regime_sensitivity_sweep.py`:

```python
#!/usr/bin/env python3
"""Regime sensitivity sweep — run oracle across multiple fast_days/slow_days settings.

Usage:
    uv run python scripts/regime_sensitivity_sweep.py

Output:
    research_workspace/regime_sensitivity/
        sensitivity_report.json   — structured comparison
        results_comparison.tsv    — one-line per setting
        details_{fast}_{slow}.json  — full oracle output per setting
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

# Sweep sets
SETTINGS: List[Tuple[str, int, int]] = [
    ("default", 50, 200),
    ("fast", 20, 100),
    ("slow", 100, 300),
]

CHECKPOINT = str(PROJECT_DIR / "checkpoints" / "channel_breakout_v2_1_balanced.pt")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_pct(val: float) -> str:
    return f"{val * 100:+.2f}%"


def _run_one(label: str, fast_days: int, slow_days: int) -> Dict[str, Any]:
    """Run oracle as subprocess for one setting and return the parsed result."""
    print(f"  Running {label} ({fast_days}/{slow_days})...")
    cmd = [
        sys.executable, ORACLE_SCRIPT,
        "--checkpoint", CHECKPOINT,
        "--fast-days", str(fast_days),
        "--slow-days", str(slow_days),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  WARNING: oracle exited {result.returncode} for {label}")
        print(f"  stderr: {result.stderr[:500]}")
    # Read the oracle report that was written to disk
    if not ORACLE_DEFAULT_OUTPUT.exists():
        raise RuntimeError(
            f"Oracle did not produce {ORACLE_DEFAULT_OUTPUT} for {label}\n"
            f"stdout: {result.stdout[:500]}"
        )
    report = json.loads(ORACLE_DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    # Save a copy to the sensitivity directory
    detail_path = OUTPUT_DIR / f"details_{fast_days}_{slow_days}.json"
    detail_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"    → {detail_path}")
    return report


def _compute_comparison(
    results: List[Tuple[str, int, int, Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build structured comparison across all settings."""
    default_result = None
    for label, fd, sd, r in results:
        if fd == 50 and sd == 200:
            default_result = (label, fd, sd, r)
            break

    settings_data = []
    metric_deltas: Dict[str, Dict[str, str]] = {}
    regime_shifts: Dict[str, Dict[str, float]] = {}

    for label, fd, sd, r in results:
        entry = {
            "label": label,
            "fast_days": fd,
            "slow_days": sd,
            "is_return": r["metrics"]["is"]["raw"]["return"],
            "is_dd": r["metrics"]["is"]["raw"]["dd"],
            "is_sharpe": r["metrics"]["is"]["raw"]["sharpe"],
            "oos_return": r["metrics"]["oos"]["raw"]["return"],
            "oos_dd": r["metrics"]["oos"]["raw"]["dd"],
            "oos_sharpe": r["metrics"]["oos"]["raw"]["sharpe"],
            "oos_safe_return": r["metrics"]["oos"]["safe_execution"]["return"],
            "oos_safe_dd": r["metrics"]["oos"]["safe_execution"]["dd"],
            "execution_parity": r["metrics"]["execution_parity"],
            "rolling_6m_min": r["metrics"]["rolling"].get("6m_min_return"),
            "rolling_12m_min": r["metrics"]["rolling"].get("12m_min_return"),
            "dd_over_50": any(
                v["dd"] < -0.50
                for v in [r["metrics"]["is"]["raw"], r["metrics"]["oos"]["raw"]]
            ),
            "status": r["flags"]["status"],
            "oracle_version": r["oracle_version"],
            "regime_filter": r["oracle"]["regime_filter"],
        }
        settings_data.append(entry)

        # Metric deltas vs default
        if default_result and (fd != 50 or sd != 200):
            _, _, _, dr = default_result
            key = f"{fd}_{sd}"
            for m in ["oos_return", "oos_dd", "oos_sharpe"]:
                v = r["metrics"]["oos"]["raw"][m]
                dv = dr["metrics"]["oos"]["raw"][m]
                if isinstance(v, (int, float)) and isinstance(dv, (int, float)):
                    diff = v - dv
                    if m in ("return",):
                        metric_deltas.setdefault(m, {})[key] = _format_pct(diff)
                    elif m == "dd":
                        metric_deltas.setdefault(m, {})[key] = f"{diff * 100:+.2f}pp"
                    else:
                        metric_deltas.setdefault(m, {})[key] = f"{diff:+.4f}"

        # Regime label distribution change vs default
        if default_result and (fd != 50 or sd != 200):
            _, _, _, dr = default_result
            for regime in ["bull", "bear", "neutral"]:
                v = r["metrics"]["regime"].get(regime, {}).get("return", 0)
                dv = dr["metrics"]["regime"].get(regime, {}).get("return", 0)
                if isinstance(v, (int, float)) and isinstance(dv, (int, float)):
                    regime_shifts.setdefault(regime, {})[f"{fd}_{sd}"] = _format_pct(v - dv)

    comparison = {
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
    rows = []
    for label, fd, sd, r in results:
        m = r["metrics"]
        f = r["flags"]
        is_r = m["is"]["raw"]
        oos_r = m["oos"]["raw"]
        oos_s = m["oos"]["safe_execution"]
        row = {
            "setting": label,
            "fast_days": fd,
            "slow_days": sd,
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
            "dd_over_50": str(any(v["dd"] < -0.50 for v in [is_r, oos_r])),
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
    print(f"  → {tsv_path}")


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

    # Build comparison report
    comparison = _compute_comparison(results)

    # Write comparison report
    report_path = OUTPUT_DIR / "sensitivity_report.json"
    report_path.write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    print(f"  Sensitivity report: {report_path}")

    # Write TSV
    _write_tsv(results)

    elapsed = time.time() - t0
    print(f"\nDone. Elapsed: {elapsed:.1f}s")
    print(f"Results in: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Add .gitignore entry for sweep output**

Check if `research_workspace/regime_sensitivity/` is covered by gitignore. If not, add:

Append to `.gitignore` (or to an existing `research_workspace/` gitignore entry):
```
# Sweep outputs (regenerated on demand)
research_workspace/regime_sensitivity/
```

- [ ] **Step 3: Dry-run the sweep (optional, user may skip)**

```bash
cd D:\aiproject2\autoresearch-crypto
uv run python scripts/regime_sensitivity_sweep.py
```

Expected: Three sequential oracle runs, output to `research_workspace/regime_sensitivity/`:
- `details_50_200.json`, `details_20_100.json`, `details_100_300.json`
- `sensitivity_report.json` with comparison table
- `results_comparison.tsv`

Total runtime: ~3-5 minutes (one oracle run × 3).

- [ ] **Step 4: Commit**

```bash
git add scripts/regime_sensitivity_sweep.py .gitignore
git commit -m "feat: add regime sensitivity sweep wrapper (subprocess-only)

scripts/regime_sensitivity_sweep.py runs the oracle across three
regime parameter sets (50/200, 20/100, 100/300) via subprocess
and produces a structured comparison report.

Output: research_workspace/regime_sensitivity/
- sensitivity_report.json
- results_comparison.tsv
- details_{fast}_{slow}.json (raw oracle output per setting)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Self-Review

**Spec coverage check:**

| Spec Requirement | Task(s) | Status |
|---|---|---|
| `--fast-days`/`--slow-days` CLI args (default 50/200) | Task 1, Step 2 | ✅ |
| CLI validation (fast>0, slow>0, fast<slow) | Task 1, Step 2 | ✅ |
| Thread parameters through 3 call sites | Task 1, Steps 3-5 | ✅ |
| `ORACLE_VERSION` bump to v0.2 | Task 1, Step 1 | ✅ |
| Regime filter recorded in output JSON | Task 1, Step 1 | ✅ |
| Parity test (metrics, labels, signal hash) | Task 2, Step 1 | ✅ |
| Version field excluded from parity | Task 2, Step 1 (check #5) | ✅ |
| Fixed v0.1.0 fixture as reference | Task 2, Step 1 (v010_path) | ✅ |
| Sweep wrapper (subprocess-only) | Task 3, Steps 1-2 | ✅ |
| Sweep output to `research_workspace/regime_sensitivity/` | Task 3, Step 1 | ✅ |
| No changes to `dex/regime_permissions.py` | Entire plan | ✅ |
| No changes to `dex/regime_filter.py` | Entire plan | ✅ |
| No checkpoint/live/demo changes | Entire plan | ✅ |
| No `OracleConfig` dataclass | Entire plan | ✅ |

**Placeholder scan:** All steps contain concrete code, paths, and commands. No "TBD", "implement later", or vague instructions.

**Type consistency:** Function signatures, parameter names (`fast_days`, `slow_days`), and JSON field names are consistent across all 3 tasks.
