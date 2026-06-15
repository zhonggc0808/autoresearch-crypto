# Oracle v0.2 — Configurable Regime Audit (Phase 5A)

Date: 2026-06-15

## Status: Design Approved

Approved by user on 2026-06-15 with 4 additional constraints incorporated.

---

## Objective

Upgrade the research oracle from v0.1.0 to v0.2 with configurable regime EMA parameters
(`fast_days`, `slow_days`) while preserving exact v0.1.0 behavior under default values.

The first goal is **parity, not performance improvement**.

## 4 Additional Constraints (from user review)

The following were added after the initial design review, incorporated into the
sections above:

1. **Version field excluded from parity** — `oracle_version` comparison is
   skipped; parity checks metrics, regime labels, and signal hash only.
2. **CLI input validation** — `fast_days > 0`, `slow_days > 0`,
   `fast_days < slow_days` enforced at parse time.
3. **Signal hash computed in-test** — not exposed in oracle output JSON;
   computed via internal oracle calls for parity test only.
4. **Fixed baseline fixture** — parity test reads frozen v0.1.0 snapshot as
   reference, never self-generated data.

## Scope

### In Scope (Phase 5A)

- Bump oracle version to v0.2
- Add `--fast-days` / `--slow-days` CLI arguments (defaults: 50/200)
- Thread regime parameters through 4 call sites in the oracle signal pipeline
- Record regime parameters in oracle output JSON
- Parity test: v0.2 defaults match v0.1.0 exactly (metrics, regime labels, signal hash)
- Sensitivity sweep script: `scripts/regime_sensitivity_sweep.py`

### Out of Scope (explicitly excluded)

- ❌ No `OracleConfig` dataclass
- ❌ No changes to `dex/regime_permissions.py` (`compute_daily_indicators` EMA50/100 stays)
- ❌ No changes to `dex/regime_filter.py` (already parameterized)
- ❌ No changes to checkpoints, live files, or demo baseline
- ❌ Oracle does not know about "sweep"
- ❌ No adaptive lookback or ADX search

---

## Design

### 1. CLI Changes (`scripts/research_oracle.py`)

Add two arguments to `ArgumentParser`:

```python
parser.add_argument("--fast-days", type=int, default=50,
    help="EMA fast period for regime labels (default: 50)")
parser.add_argument("--slow-days", type=int, default=200,
    help="EMA slow period for regime labels (default: 200)")
```

**Validation** (enforced after parse):

| Rule | Rationale |
|------|-----------|
| `fast_days > 0` | EMA period must be positive |
| `slow_days > 0` | EMA period must be positive |
| `fast_days < slow_days` | Fast must be shorter than slow |

Default values are hard-coded at 50/200 so omitting either flag reproduces
v0.1.0 behavior exactly. No existing scripts break.

### 2. Parameter Threading

`fast_days` and `slow_days` flow through 3 call sites (plus 1 explicit no-change):

```
main()
  └─ run_oracle(..., fast_days=50, slow_days=200)
       ├─ _generate_v21_signals(checkpoint, df, fast_days, slow_days)
       │    └─ build_daily_regime_labels(df, fast_days, slow_days)    [1]
       │
       ├─ Phase 3B filter branch
       │    └─ build_daily_regime_labels(df, fast_days, slow_days)    [2]
       │
       └─ Pre-compute regimes (FULL data, line ~681)
            └─ build_daily_regime_labels(df_full, fast_days, slow_days)  [3]
```

All calls use the same `fast_days`/`slow_days` pair. No partial application.

**Note on [2] vs [3]**: The Phase 3B path [2] runs only when `--candidate`
supplies a filter config. Line 681 (site [3]) then unconditionally overwrites
`regimes_full`, so [2] and [3] are never both live. Both still receive the
same `fast_days`/`slow_days` for correctness under either path.

### 3. Oracle Version and Parameter Recording

```python
ORACLE_VERSION = "v0.2"
```

Output JSON now includes:

```json
{
  "oracle_version": "v0.2",
  "oracle": {
    "version": "v0.2",
    "regime_filter": {
      "fast_days": 50,
      "slow_days": 200
    }
  },
  ...
}
```

This ensures every oracle run's regime parameters are self-documenting and
reproducible.

**Baseline snapshot**: filename becomes
`channel_breakout_v2_1_balanced_oracle_v0.2.json`, coexisting with the v0.1.0
snapshot. Parity tests read a frozen v0.1.0 snapshot as their fixture, never
self-generated data.

### 4. Parity Test (`test_research_oracle.py`)

New test: `test_v02_default_parity()`

| Check | Method | Tolerance |
|-------|--------|-----------|
| OOS return | direct comparison | `1e-8` |
| OOS DD | direct comparison | `1e-8` |
| OOS Sharpe | direct comparison | `1e-8` |
| Regime label counts | BULL/BEAR/NEUTRAL counts from `build_daily_regime_labels()` | exact |
| Signal hash | SHA256 of `signals_raw_full.tobytes()` (computed within the test via internal calls) | exact |

**Design notes**:
- The test reads a **frozen v0.1.0 baseline snapshot** (committed JSON) as the
  reference. It does not compare v0.2 against v0.2.
- `oracle_version` field is excluded from the comparison — version bump is an
  expected difference.
- Signal hash is computed in-test from internal oracle functions, not from the
  oracle's output JSON. This avoids contaminating the output schema for testing
  purposes.

### 5. Sensitivity Sweep Script (`scripts/regime_sensitivity_sweep.py`)

Pure orchestration. The oracle is called as a subprocess
(`uv run python scripts/research_oracle.py ...`) for each parameter set.
The sweep script does **not** import or call oracle internals.

**Sweep sets**:

| Label | fast_days | slow_days |
|-------|-----------|-----------|
| default | 50 | 200 |
| fast | 20 | 100 |
| slow | 100 | 300 |

**Output** (`research_workspace/regime_sensitivity/`):

```
sensitivity_report.json
results_comparison.tsv
details_50_200.json
details_20_100.json
details_100_300.json
```

**`sensitivity_report.json` schema** — per-setting comparison:

```json
{
  "timestamp": "2026-06-15T...",
  "settings": [
    {
      "label": "default",
      "fast_days": 50,
      "slow_days": 200,
      "oracle_result": { "... full oracle output ..." }
    },
    ...
  ],
  "comparison": {
    "regime_shift": {
      "regime_label_delta_pct": { "BULL": 0, "BEAR": 0, "NEUTRAL": 0 },
      "signal_hash_delta": null
    },
    "metric_deltas": {
      "oos_raw_return": { "20_100": "+12.3%", "100_300": "-5.1%" },
      "oos_dd": { "20_100": "-3.2pp", "100_300": "+1.1pp" },
      ...
    }
  }
}
```

**`results_comparison.tsv`** — columns: `setting`, `fast_days`, `slow_days`,
`is_return`, `is_dd`, `oos_raw_return`, `oos_dd`, `oos_sharpe`,
`6m_min_return`, `12m_min_return`, `dd_over_50`, `exec_parity`,
`regime_label_counts`, `signal_hash`.

### 6. Files Changed

| File | Action | Est. Δ |
|------|--------|--------|
| `scripts/research_oracle.py` | Modify (+CLI args, +threading, +metadata) | +25 lines |
| `scripts/regime_sensitivity_sweep.py` | **Create** | +100 lines |
| `tests/test_research_oracle.py` | Modify (+parity test) | +30 lines |
| `research_workspace/regime_sensitivity/` | Runtime output (gitignored) | — |

### 7. No-Touch List

- `dex/regime_permissions.py` — `compute_daily_indicators()` EMA50/100 unchanged
- `dex/regime_filter.py` — already parameterized
- `dex/scoring.py`
- `dex/config.py`
- All `checkpoints/` files
- All `live_*.py` scripts
- `dex/live/common.py`
- `start_bitget_demo_v21*.bat`

---

## Files Not Created or Modified (Affirmation)

The following check verifies the no-touch list is mechanically enforced:
- `scripts/research_oracle.py` — only existing production/oracle file modified
- `tests/test_research_oracle.py` — modified for parity coverage
- `scripts/regime_sensitivity_sweep.py` — created as orchestration only
- Files outside this set are not touched, including all no-touch-list entries
- The sweep script calls oracle via subprocess; it does not import or call oracle internals

## Return Criteria (Pass/Fail)

Phase 5A is acceptable only if:

1. ✅ v0.2 default (50/200) passes full parity against v0.1.0
2. ✅ Regime parameter deltas from sensitivity sweep are reported as research
   data only — not as a new baseline
3. ✅ Track A (live/demo) untouched
4. ✅ No-touch list verified clean

---

## Follow-on (Phase 5B, not started)

If sensitivity testing becomes routine, Phase 5B could introduce an integrated
sweep mode. Not before.

---

## Appendix: Call-Site Map

| File | Line (approx.) | Current Code | After Change |
|------|----------------|--------------|--------------|
| `research_oracle.py` | 253 | `build_daily_regime_labels(df, fast_days=50, slow_days=200)` | `build_daily_regime_labels(df, fast_days, slow_days)` |
| `research_oracle.py` | 674 | `build_daily_regime_labels(df, fast_days=50, slow_days=200)` | `build_daily_regime_labels(df, fast_days, slow_days)` |
| `research_oracle.py` | 681 | `build_daily_regime_labels(df_full, fast_days=50, slow_days=200)` | `build_daily_regime_labels(df_full, fast_days, slow_days)` |
| `research_oracle.py` | 254 | `compute_daily_indicators(df)` — **NO CHANGE** | — |
