# Phase 5B — Oracle v0.2 Regime Sensitivity Operationalization

Date: 2026-06-15
Status: Design / Plan

## Background

Phase 5A validated that oracle v0.2 parameterization is correct (50/200 parity
passed) and that alternative regime settings (20/100, 100/300) are inferior.
5B does not search for better parameters — it hardens the ad-hoc sweep
workflow from 5A into a repeatable audit process.

## Scope

### In Scope

1. **Formalize the sweep into a repeatable audit command.**
2. **Decide whether to integrate into the oracle CLI** or keep as wrapper.
3. **Extend validation to 2600d** (Phase 5A was 1300d only).
4. **Define a fixed pass/fail schema for future regime audits.**

### Explicitly Out of Scope

- ❌ No changes to v2.1 balanced checkpoint
- ❌ No Track A / demo / live file changes
- ❌ No reopening 20/100 or 100/300 optimization
- ❌ No broader regime parameter grid search (only the 3 preset pairs)
- ❌ No promoting 50/200 conclusion to "strategy upgrade" — it's an oracle audit conclusion

---

## Design Decisions

### Decision 1: Wrapper vs Built-in Sweep

**Chosen: Option A — keep wrapper.** Oracle does not learn about "sweep".

Rationale: wrapper already works, only 3 preset pairs, oracle's core value is
single-set single-run evaluation. Adding sweep would spread complexity into
output path coordination, report aggregation, and failure recovery — none of
which the oracle needs.

Phase 5C (not started) could revisit if wrapper maintenance becomes painful.

### Decision 2: 2600d Data

Phase 5A used 1300d ETHUSDT data. 2600d is an optional annex — not a 5B
completion blocker.

- The sweep script defaults to 1300d (the oracle's current default).
- `--days 2600` must be explicitly passed to use longer data.
- Every output records `data_days`, `data_path`, and `data_hash` for traceability.
- If 2600d parquet is unavailable, the annex is documented as
  `blocked: data unavailable` and does not block 5B sign-off.

---

## Implementation Plan

### Task 1: Formalize sweep script (minor hardening)

**Files:** `scripts/regime_sensitivity_sweep.py`

- Add `--days` argument (default: 1300 — matches oracle default data).
- Record `data_days`, `data_path`, `data_hash` in sensitivity report output.
- Add inline summary table to console output after sweep completes.
- Add verdict field to `sensitivity_report.json` per the PASS/REVIEW/FAIL schema.
- No changes to oracle internals or output paths.

### Task 2: 2600d sensitivity annex (optional, data-gated)

**Files:** `docs/2026-06-15-oracle-v02-configurable-regime-audit-results.md`

- **If 2600d parquet is available**: run `--days 2600`, append results section.
- **If unavailable**: document as `2600d annex: blocked (data unavailable)`.
- This is not a 5B completion gating item.

### Task 3: Verify no-touch and commit

- Confirm no changes to dex/, checkpoints/, live_*.py.
- Confirm oracle still has no `--regime-sweep` flag.
- Commit sweep hardening and results update.

---

## Pass/Fail Schema (Standardized)

Every regime sensitivity audit produces a verdict:

| Verdict | Condition |
|---------|-----------|
| **PASS** | Default (50/200) has highest Sharpe AND its DD is not clearly worse than the best DD group (within 5pp) |
| **REVIEW** | Default Sharpe is not highest, or default DD is >5pp worse than the best group |
| **FAIL** | Parity check fails, or default 50/200 metrics are not reproducible |

**DD semantics**: DD values are negative (e.g., -0.34). "Best DD" = numerically
highest (closest to zero). "Worse DD" = numerically lower (more negative).

```json
{
  "audit": {
    "date": "2026-06-15",
    "oracle_version": "v0.2",
    "data_days": 1300,
    "data_path": "data/crypto/ETHUSDT_5m_1300d.parquet",
    "presets": ["50/200", "20/100", "100/300"]
  },
  "verdict": "PASS",
  "verdict_reason": "default wins Sharpe (2.35 vs 1.71, 0.11); DD within 5pp of best",
  "default_wins": {
    "sharpe": true,
    "return": true,
    "dd": true,             // default DD -0.338 is best (highest) among all settings
    "rolling_12m": true     // default 12m min return -0.130 is best
  },
  "recommendation": "keep 50/200"
}
```

This schema lives in `sensitivity_report.json` and is printed to console.
Schema version frozen at Phase 5B — do not change without a version bump.

---

## Summary

```
Phase 5B status:      design / not started
Goal:                 operationalize sweep (Option A, keep wrapper)
Oracle integration:   none — oracle unchanged
2600d annex:          optional, data-gated, NOT a 5B blocker
PASS/REVIEW schema:   approved with clarified DD semantics
Baseline impact:      none
Promotion impact:     none
Track A impact:       none
```
