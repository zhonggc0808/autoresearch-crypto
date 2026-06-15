# v0.9b Codegen Evaluation Adapter — Design Spec

**Date:** 2026-06-15
**Status:** Approved for implementation
**Version:** v0.9b

## 1. Purpose

Bridge the gap between v0.9a's codegen sandbox and the existing oracle/scorecard
pipeline. A codegen strategy generates `generate_signals(df)` code, not parameter
JSONs — the adapter evaluates this code through the existing `StrategyEvaluator`
and produces oracle-compatible scorecards.

**Key constraint:** Codegen candidates remain `research_only`. No promotion path.

## 2. Architecture

```
codegen_candidates/exp_NNNN/
├── strategy.py          ← sandbox-validated strategy code
└── manifest.json        ← metadata (name, hypothesis, params)

evaluate_codegen_candidate.py
├── Phase 1: Load + Re-Verify
├── Phase 2: Evaluate
└── Phase 3: Scorecard → codegen_scorecards/

score_candidate.py       ← imported for build_scorecard_from_result() only
research_oracle.py       ← imported for utility functions only (NOT modified)
sandbox_import_*.py      ← reused (NOT modified)
validate_code_candidate.py  ← reused (NOT modified)
scan_candidate_code.py   ← reused (NOT modified)
```

## 3. Reused Components (Import, No Modification)

| Source | What's Imported | Purpose |
|--------|----------------|---------|
| `research_oracle` | `_find_eth_data`, `_load_and_split_data`, `_evaluate_signals`, `_safe_execution_signals`, `_compute_rolling_metrics`, `_compute_fee_sensitivity` | Data loading, metric computation, safe-exec transform, rolling windows, fee sensitivity |
| `scan_candidate_code` | `scan_code` | Static AST-based security scan |
| `sandbox_import_candidate` | `import_candidate_strategy`, `SandboxImportError` | Restricted import sandbox |
| `validate_code_candidate` | `validate_candidate_code` | Dynamic interface + future-data + side-effect validation |
| `score_candidate` | `build_scorecard_from_result` only | Scorecard builder (pure function, no I/O). |
| `dex.strategies.base` | `StrategyEvaluator` | Bar-by-bar trading simulation |

**Note on score_candidate:** v0.9b imports `build_scorecard_from_result` only
— the pure function that transforms an oracle-result dict into a scorecard.
The adapter handles its own file I/O (writing to `codegen_scorecards/`).
`write_scorecard_and_log` is NOT imported, because it writes to `llm_scorecards/`
and `llm_results.tsv`, which would violate the independent-directory rule.

**Signature smoke test:** The test suite includes a `test_oracle_utility_signatures`
test that verifies each imported private function has the expected parameter
signature. This catches silent interface breakage when oracle internals change.

**Boundary contract:** These are read-only imports. If any private (`_`)
function changes signature, the adapter fails hard — it does not silently
change evaluation semantics.

## 4. Evaluation Flow (3 Phases)

### Phase 1: Load + Re-Verify

1. Locate codegen directory from `--codegen-id` or `--dir`
2. Read `manifest.json` → candidate metadata
3. Read `strategy.py` → source code
4. `scan_code(code)` — static scan
5. `import_candidate_strategy(strategy_path)` — sandbox import
6. `validate_candidate_code(module)` — dynamic validation
7. **Any failure →** write `rejected/evaluation_error` artifact,
   return early. No evaluation attempted.

### Phase 2: Evaluate

8. `_find_eth_data()` → locate `ETHUSDT_5m_1300d.parquet`
9. `_load_and_split_data(data_path)` → `(df_is, df_oos, split_idx)`
10. `module.generate_signals(df_full)` → raw signal array
11. Compute metrics via `_evaluate_signals()` (the single metric entry point,
    which internally uses `StrategyEvaluator`):
    - IS raw: `_evaluate_signals(signals_is, prices_is)`
    - IS safe-exec: `_evaluate_signals(safe_signals_is, prices_is)`
    - OOS raw: `_evaluate_signals(signals_oos, prices_oos)`
    - OOS safe-exec: `_evaluate_signals(safe_signals_oos, prices_oos)`
    - Rolling (6m, 12m): `_compute_rolling_metrics(...)`
    - Fee sensitivity: `_compute_fee_sensitivity(...)` (IS + OOS)
    - Execution parity (IS): compare IS safe vs IS raw signal match rate
    - Execution parity (OOS): compare OOS safe vs OOS raw signal match rate
12. **Evaluation error →** write `evaluation_error` artifact, return

### Phase 3: Scorecard + Output

13. Build oracle-like result dict (see §5)
14. `build_scorecard_from_result(result, candidate_spec)` → scorecard
    (pure function — no I/O)
15. **Override verdict** (see §6)
16. Write scorecard to `codegen_scorecards/{cg_id}_scorecard.json`
    (self-managed — does NOT call `write_scorecard_and_log`)
17. On non-dry-run: update `manifest.json` with `evaluation_status` field
18. On dry-run: skip all writes (manifest included). Only print summary.
19. Print summary

## 5. Oracle-like Result Format (Lightweight)

Omitted fields: `regime_breakdown`, `correlation.vs_baseline`, `slippage_sensitivity`.

```python
{
    "experiment_id": "exp_NNNN",
    "parent_id": "codegen_v0.9a",
    "candidate_role": "standalone",
    "strategy": "codegen",
    "params_hash": "sha256:...",
    "data_hash": "sha256:...",
    "oracle_version": "codegen_adapter_v0.9b",
    "baseline_id": None,
    "commit": "...",
    "data": {
        "dataset_path": "...",
        "data_hash": "...",
        "data_start": "...", "data_end": "...",
        "is_bars": N, "oos_bars": N,
        "split_method": "fixed-split", "split_ratio": 0.70,
    },
    "metrics": {
        "is": {"raw": {...}, "safe_execution": {...}},
        "oos": {"raw": {...}, "safe_execution": {...}},
        "rolling": {"6m_min_return": ..., "12m_min_return": ...},
        "execution_parity": {"is": 0.0, "oos": 0.0},
        "correlation": {"vs_baseline": None},
        "sensitivity": {
            "is": {"fees": {"0bp": ..., "2bp": ..., "4bp": ..., "10bp": ...}},
            "oos": {"fees": {"0bp": ..., "2bp": ..., "4bp": ..., "10bp": ...}},
        },
    },
    "flags": {
        "status": "PASS" | "WARN" | "REJECT",
        "warnings": [],
        "disqualifications": [],
    },
}
```

## 6. Codegen Verdict Override (Hard Rule)

After `build_scorecard_from_result()` produces a scorecard, the adapter
**always** overrides:

```python
scorecard["verdict"] = {
    "label": "codegen_research_only",
    "reason": "Codegen candidates are research-only by policy. "
              "Human review and promotion are not available.",
    "source_status": original_verdict.get("source_status",
                                         result.get("flags", {}).get("status", "?")),
    "description": "Codegen-generated strategy — research evaluation only.",
}
scorecard["promotion_eligible"] = False
scorecard["promotion_block_reason"] = "CODEGEN_RESEARCH_ONLY"
```

The original oracle verdict is preserved in `scorecard["_original_verdict"]`
for debugging.

Reasoning:
- Prevents any downstream system from mistaking a codegen candidate
  as promotable
- The `promotion_gates` block remains as-is (informational/metrics)
- The top-level `verdict.label` is the authority for all consumers

## 7. CLI

```bash
# Evaluate by codegen ID
uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001

# Evaluate by path
uv run python scripts/evaluate_codegen_candidate.py --dir path/to/codegen_dir

# Dry-run (verify only, no writes)
uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001 --dry-run

# Force re-evaluation
uv run python scripts/evaluate_codegen_candidate.py --codegen-id exp_0001 --force
```

Exit codes:
- 0 — evaluation success: scorecard written to `codegen_scorecards/`
- 1 — input error (unknown ID, missing files, invalid manifest)
- 2 — candidate rejected before evaluation (Phase 1 failure: scan, sandbox, or validation)
- 3 — evaluation error (Phase 2 failure: oracle utility error, generate_signals crash)

Rationale for differentiated exit codes:
- CI/automation can distinguish "processable result" (0) from
  "candidate was bad" (2) from "infrastructure error" (3)
- Rejected candidates (2) are expected and not alarming
- Error artifacts (3) deserve investigation

A `--strict-exit-codes` flag is available for forward compatibility;
the defaults above ARE the strict behavior.

## 8. Output Files

| Path | Condition | Contents |
|------|-----------|----------|
| `codegen_scorecards/{cg_id}_scorecard.json` | Evaluation success, non-dry-run | Full scorecard with overridden verdict |
| `proposals/rejected_candidates/rejected_codegen_{cg_id}_{ts}.json` | Phase 1 failure, non-dry-run | Structured rejection record (timestamped to avoid overwrite) |
| `proposals/rejected_candidates/error_codegen_{cg_id}_{ts}.json` | Phase 2 failure, non-dry-run | Evaluation error record (timestamped to avoid overwrite) |
| `codegen_candidates/{cg_id}/manifest.json` | Non-dry-run only | Updated with `evaluation_status` field and `evaluated_at` timestamp |

**Dry-run rule:** `--dry-run` writes nothing to disk — no scorecard, no
manifest update, no artifact. Verification output goes to stdout only.

## 9. Tests (`test_codegen_evaluation_v09b.py`)

| Test | What It Verifies |
|------|------------------|
| `test_valid_candidate_evaluates` | Full happy path: mock codegen → evaluation → scorecard |
| `test_invalid_code_rejected_before_evaluation` | Forbidden import → Phase 1 rejection, no evaluation, exit code 2 |
| `test_generate_signals_error_handled` | Broken generate_signals → evaluation_error artifact, exit code 3 |
| `test_scorecard_verdict_always_codegen_research_only` | Verdict label forced to `codegen_research_only` |
| `test_promotion_eligible_false` | `promotion_eligible=False` + `block_reason` set |
| `test_oracle_utility_signatures` | Each imported `_private` oracle function has expected parameter signature |
| `test_no_modifications_to_oracle_core` | Verify oracle files unchanged post-test |
| `test_dry_run_no_writes` | `--dry-run` writes nothing (no scorecard, no manifest update, no artifact) |
| `test_fee_sensitivity_computed` | Fee sensitivity in result has expected fee levels |
| `test_rolling_metrics_computed` | Rolling min return fields are populated for both 6m and 12m |
| `test_exit_code_0_on_success` | Exit code 0 when evaluation succeeds |
| `test_exit_code_1_on_input_error` | Exit code 1 for missing ID / missing files |
| `test_exit_code_2_on_rejection` | Exit code 2 when candidate rejected at Phase 1 |

## 10. Hard Boundaries

- ❌ Not modify `scripts/research_oracle.py`
- ❌ Not modify `dex/` directory (only import `StrategyEvaluator`)
- ❌ Not modify `scripts/score_candidate.py`
- ❌ Not modify any baseline / demo / live code
- ❌ No automatic promotion (verdict always `codegen_research_only`)
- ❌ Codegen family not added to generator search space
- ✅ Imports from oracle are read-only; signature changes fail hard
