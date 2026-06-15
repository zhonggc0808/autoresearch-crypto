# LLM Research Organization — Release Checklist

**Version:** 1.0.0  
**Status:** Stable  

---

## Pre-Release Checks

### 1. Version Marker

- [ ] `research_workspace/LLM_RESEARCH_ORG_VERSION.json` exists
- [ ] Version number matches intended release
- [ ] All three families listed: channel_breakout, volatility_filtered_breakout, exit_logic_variant
- [ ] `promotion` is `human_review_required`
- [ ] `codegen` is `disabled`

### 2. Family Registry

- [ ] `scripts/family_registry.py` has all expected families
- [ ] Each family has a valid `FamilyDefinition`
- [ ] Each family has a JSON Schema in `research_workspace/family_schemas/`
- [ ] `candidate_schema_v0.2.json` includes all strategy_type enums
- [ ] `validate_candidate_v08.py` correctly dispatches all families
- [ ] No hardcoded family names in `validate_action()`, `execute_fork()`,
      or `_build_full_candidate()`

### 3. Schema Validation

- [ ] All family schemas load without JSON errors
- [ ] Valid CB candidate passes v0.8 validation
- [ ] Valid VFB candidate passes v0.8 validation
- [ ] Valid ELV candidate passes v0.8 validation
- [ ] Unregistered family rejected
- [ ] Out-of-bounds params rejected per family

### 4. Core Tests

Run: `uv run pytest tests/test_candidate_schema_v02.py tests/test_verdict_engine_v03.py tests/test_llm_candidate_generator_v04.py tests/test_llm_result_reviewer_v05.py tests/test_action_executor_v06.py tests/test_llm_research_cycle_e2e.py tests/test_meta_agent_v07.py tests/test_multi_family_v08.py tests/test_multi_family_batch_v085.py tests/test_research_oracle.py -v --tb=short`

- [ ] 251+ tests pass, 0 failures in research loop suite
- [ ] Known failures (13 in test_reflection.py + test_llm_hypothesis.py) are
      documented in `tests/known_failures.md`
- [ ] No new failures introduced

### 5. Smoke Cycle

- [ ] `uv run python scripts/run_llm_research_cycle.py --mock` succeeds
- [ ] 6/6 steps pass
- [ ] Final state is `executed_fork` (or other valid terminal state)
- [ ] Candidate file written to `research_workspace/llm_candidates/`
- [ ] Run record written to `research_workspace/llm_runs/`

### 6. Batch Trial

- [ ] `uv run python scripts/run_batch_trial.py --multi-family --cycles 9` succeeds
- [ ] Each family gets ≥3 cycles
- [ ] 0 failures
- [ ] Batch summary written to `research_workspace/llm_runs/`

### 7. Meta-Review

- [ ] `uv run python scripts/meta_review.py --latest --dry-run` succeeds
- [ ] Context includes family-level analysis (`FAMILY_ANALYSIS` placeholder replaced)
- [ ] Context includes search space from registry

### 8. Forbidden Paths

- [ ] `checkpoints/` not modified
- [ ] `dex/` not modified
- [ ] `live_*.py` not modified
- [ ] `scripts/research_oracle.py` not modified
- [ ] `data/` not modified
- [ ] No candidate spec references `checkpoint`, `live_`, `research_oracle`
- [ ] No action proposes oracle/baseline/live modification

### 9. Documentation

- [ ] `docs/llm_research_org.md` exists and is accurate
- [ ] `docs/llm_research_runbook.md` exists and commands are tested
- [ ] `docs/llm_research_release_checklist.md` exists and is complete
- [ ] `docs/llm_research_contract.md` is up to date
- [ ] `tests/known_failures.md` exists and is accurate

### 10. Verification Script

- [ ] `scripts/verify_llm_research_org.py` exists
- [ ] All checks pass: health check returns status "ok"
- [ ] No errors or warnings

---

## Release Sign-Off

| Check | Done | Signature |
|-------|------|-----------|
| All core tests pass | | |
| Smoke cycle passes | | |
| Multi-family batch passes | | |
| No forbidden paths modified | | |
| Documentation complete | | |
| Known failures documented | | |
| Codegen explicitly disabled | | |

**Release approved:** Yes / No  
**Date:**  
**Maintainer:**  
