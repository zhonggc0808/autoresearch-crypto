# LLM Research Organization — Runbook

**Version:** 1.0.0  
**Status:** Stable  

---

## 1. Quick Start

```bash
# Smoke test (1 mock cycle, no API key needed)
uv run python scripts/run_llm_research_cycle.py --mock

# Multi-family batch (12 cycles across all families)
uv run python scripts/run_batch_trial.py --multi-family --cycles 12

# Meta-review over latest run history
uv run python scripts/meta_review.py --latest

# Full health check
uv run python scripts/verify_llm_research_org.py
```

---

## 2. Three Standard Modes

### 2.1 Smoke Mode

**Purpose:** Verify the research loop is functional.  
**Scope:** 1 cycle, mock LLM, no oracle evaluation.  
**Command:** `uv run python scripts/run_llm_research_cycle.py --mock`  
**Expected output:** 6/6 steps, final state `executed_*`

This is the fastest way to confirm the system is wired correctly. It
generates a candidate, validates it, runs a dry-run evaluation, produces a
scorecard, proposes an action (fork), and executes it — all with mocked LLM
responses.

**Extended smoke with candidate file:**
```bash
uv run python scripts/run_llm_research_cycle.py \
    --candidate research_workspace/llm_candidates/exp_NNNN.json
```

### 2.2 Batch Mode

**Purpose:** Multi-cycle exploration across registered families.  
**Scope:** N cycles, all families, all action types.  
**Command:** `uv run python scripts/run_batch_trial.py --multi-family --cycles 12`  
**Expected output:** ≥N cycles, ≥3 per family, 0 failures.

Batch mode distributes cycles round-robin across families and cycles through
fork → kill → create actions within each family. At the end, a meta-review
analyzes the batch.

**Single-family batch (backward compatible):**
```bash
uv run python scripts/run_batch_trial.py --cycles 6
```

**Quiet mode (summary only):**
```bash
uv run python scripts/run_batch_trial.py --multi-family --cycles 12 --quiet
```

### 2.3 Review Mode

**Purpose:** Retrospective analysis of accumulated run history.  
**Scope:** Latest N runs (default 20).  
**Command:** `uv run python scripts/meta_review.py --latest`  
**Expected output:** Structured review with findings and recommendations.

**Dry-run (inspect context without LLM call):**
```bash
uv run python scripts/meta_review.py --latest --dry-run
```

**Custom window:**
```bash
uv run python scripts/meta_review.py --runs 50
```

---

## 3. Command Reference

### 3.1 Research Cycle

```bash
# Full cycle with mock LLM (no API key)
uv run python scripts/run_llm_research_cycle.py --mock

# Full cycle from existing candidate
uv run python scripts/run_llm_research_cycle.py \
    --candidate research_workspace/llm_candidates/exp_NNNN.json

# Full cycle with real oracle (requires data)
uv run python scripts/run_llm_research_cycle.py --evaluate
```

Exit codes: 0 = cycle completed (any state), 1 = cycle failed.

### 3.2 Batch Trial

```bash
# Multi-family batch (recommended)
uv run python scripts/run_batch_trial.py --multi-family --cycles 12

# Single-family batch
uv run python scripts/run_batch_trial.py --cycles 6

# Custom family selection
uv run python scripts/run_batch_trial.py --multi-family \
    --families channel_breakout,exit_logic_variant

# Quiet mode
uv run python scripts/run_batch_trial.py --multi-family --cycles 12 --quiet
```

### 3.3 Meta-Review

```bash
# Latest 20 runs
uv run python scripts/meta_review.py --latest

# Custom window
uv run python scripts/meta_review.py --runs 50

# Dry-run (inspect context, skip LLM)
uv run python scripts/meta_review.py --latest --dry-run
```

### 3.4 Standalone Steps

```bash
# Generate a candidate (requires LLM API key)
uv run python scripts/generate_candidate.py

# Validate an existing candidate
uv run python scripts/build_candidate_from_spec.py \
    research_workspace/llm_candidates/exp_NNNN.json

# Evaluate (requires oracle + data)
uv run python scripts/evaluate_candidate.py \
    research_workspace/llm_candidates/exp_NNNN.json

# Execute an action
uv run python scripts/execute_action.py \
    research_workspace/proposals/actions/action_*.json

# Family registry status
uv run python -c "from scripts.family_registry import list_families; print(list_families())"
```

### 3.5 Verification

```bash
# Full health check
uv run python scripts/verify_llm_research_org.py

# Test suite
uv run pytest tests/test_candidate_schema_v02.py \
    tests/test_verdict_engine_v03.py \
    tests/test_llm_candidate_generator_v04.py \
    tests/test_llm_result_reviewer_v05.py \
    tests/test_action_executor_v06.py \
    tests/test_llm_research_cycle_e2e.py \
    tests/test_meta_agent_v07.py \
    tests/test_multi_family_v08.py \
    tests/test_multi_family_batch_v085.py \
    tests/test_research_oracle.py \
    -v --tb=short
```

---

## 4. Interpreting Results

### 4.1 Cycle States

| State | Meaning | Action Required |
|-------|---------|-----------------|
| `executed_fork` | New candidate derived | Review forked candidate, continue cycle |
| `executed_kill` | Candidate terminated | Can start fresh or fork from sibling |
| `executed_create` | New baseline request | Maintainer reviews create request |
| `executed_stable` | Acceptable as-is | No further action |
| `executed_promote_review` | Ready for human review | Check `proposals/promotion_reviews/` |
| `generate_failed` | LLM error or rejection | Check LLM key, prompt, or try --mock |
| `validation_failed` | Schema violation | Check candidate against family schema |
| `evaluate_failed` | Oracle error | Check oracle data availability |
| `review_failed` | Invalid action | Check action validation errors |
| `execute_failed` | Executor error | Check executor logs |

### 4.2 Verdicts

| Verdict | Meaning |
|---------|---------|
| `kill` | Candidate rejected (DD too high, overfit) |
| `requires_2600d` | Passed 1300d, needs longer window |
| `blocked_missing_2600d_data` | 2600d data not available |
| `research_only_recent_regime` | Only recent regime data available |
| `promote_review_pending` | Passed all gates, human review needed |

### 4.3 Batch Summary

Batch summaries are written to `research_workspace/llm_runs/batch_*.json`.
They contain:

- Cycles completed / succeeded / failed
- Family distribution (cycles per family)
- Family success rate (executed actions per family)
- Action distribution (fork/kill/create counts)
- Per-cycle detail with family, action, and final state

---

## 5. Workspace Layout

```
research_workspace/
├── LLM_RESEARCH_ORG_VERSION.json   # Version marker
├── candidate_schema_v0.2.json      # Shared schema
├── family_schemas/                  # Per-family schemas
├── llm_candidates/                  # Candidate JSON files (exp_NNNN.json)
├── llm_scorecards/                  # Scorecard + evaluation state
├── llm_runs/                        # Run records + batch summaries
├── meta_reviews/                    # Meta-agent reviews
├── proposals/
│   ├── actions/                     # Executed action proposals
│   ├── rejected_actions/            # Rejected action proposals
│   ├── create_requests/             # Create action requests
│   └── promotion_reviews/           # Promotion review packets
├── notes/                           # Free-form notes (optional)
└── llm_results.tsv                  # Experiment tracking log
```

---

## 6. Troubleshooting

### Issue: `uv` command not found
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or via pip
pip install uv
```

### Issue: Module import errors
```bash
# Ensure dependencies are installed
uv sync
uv sync --extra dev
```

### Issue: LLM API key not available
Use `--mock` flag for all commands. The system operates entirely in mock
mode without an API key.

### Issue: No oracle data
Oracle evaluation requires OHLCV parquet files in `data/crypto/`.
Download with:
```bash
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 1300
```

### Issue: Batch cycle fails for VFB/exit families
The most common cause is a hardcoded family name in the reviewer or
generator. If you see `review_failed` only for non-CB families, check:
- `scripts/review_candidate.py` — `validate_action()` must use
  `family_registry.is_valid()` not `=="channel_breakout"`
- `scripts/generate_candidate.py` — `INJECTED_FIELDS` must not hardcode
  `strategy`
- `scripts/execute_action.py` — nested allowed_change must use registry,
  not hardcoded field names

---

## 7. Adding a New Family

See `docs/llm_research_org.md` §5.3 and `scripts/family_registry.py` for
existing examples. The process:

1. Define `FamilyDefinition` in `family_registry.py`
2. Create JSON Schema in `research_workspace/family_schemas/`
3. Update `candidate_schema_v0.2.json` strategy_type enum
4. Add batch templates in `run_batch_trial.py` (optional)
5. No codegen needed — registry handles validation, fork, and dispatch

**Do NOT** hardcode the new family name anywhere. Everything should go
through the registry.
