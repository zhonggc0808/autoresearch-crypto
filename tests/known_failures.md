# Known Test Failures

This file catalogs pre-existing test failures that are **unrelated to the LLM
Research Loop** and do not block v0.8.x or v1.0 development. They exist in
`test_reflection.py` and `test_llm_hypothesis.py` due to API drift in the GEPA
reflection engine and LLM hypothesis subsystems.

## Policy

- These failures are **allowed** for v0.8.x and v1.0.
- They are **not blockers** for any research-loop feature.
- They **must be resolved** (or converted to xfail) before a formal release.
- New failures in the research loop test suite (`test_*_v0*.py`,
  `test_research_oracle.py`) are **not** covered by this policy.

## Failure Inventory (13 total)

### Group A: `Hypothesis` dataclass missing `rationale` field (6 failures)

The `Hypothesis` dataclass in `dex/llm_hypothesis.py` was updated to require a
`rationale` positional argument, but tests in both `test_llm_hypothesis.py` and
`test_reflection.py` construct `Hypothesis()` without it.

**Files:** `tests/test_llm_hypothesis.py`, `tests/test_reflection.py`

| Test | Error | Root Cause |
|------|-------|------------|
| `TestLLMHypothesisGenerator::test_cache_reuse` | `missing 1 required positional argument: 'rationale'` | `Hypothesis(text="cached", agent="Alpha")` |
| `TestInjectLLMHypotheses::test_inject_into_engine` | same | `Hypothesis(text=f"Fake{i}", agent=f"Agent{i}")` |
| `TestReflectionEngine::test_inject_add_to_front` | same | `Hypothesis(text="LLM...", agent="Alpha")` |
| `TestReflectionEngine::test_run_experiment_hypothesis_with_expression` | same | `Hypothesis(text="...", agent="Alpha")` |
| `TestReflectionEngine::test_run_experiment_reflection_mentions_improvement` | same | `Hypothesis(text="...", agent="Alpha")` |
| `TestReflectionEngine::test_run_experiment_reflection_mentions_decline` | same | `Hypothesis(text="...", agent="Alpha")` |
| `TestReflectionEngine::test_save_load_state` | same | `Hypothesis(text="...", agent="Alpha")` |
| `TestReflectionEngine::test_meta_reflect_with_experiments` | same | `Hypothesis(text=f"...", agent="Alpha")` |

**Fix:** Add `rationale` argument to each `Hypothesis()` call in tests.

### Group B: `Hypothesis` dataclass missing all required args (1 failure)

| Test | Error | Root Cause |
|------|-------|------------|
| `TestHypothesis::test_defaults` | `missing 4 required positional arguments` | `Hypothesis()` with no args |

**Fix:** Provide required arguments or convert to `Hypothesis.__init__(...)`.

### Group C: Missing `inject_hypotheses` method (2 failures)

The `ReflectionEngine` API renamed or restructured injection, but tests still
call the old method name.

| Test | Error | Root Cause |
|------|-------|------------|
| `TestReflectionEngine::test_inject_empty` | `'ReflectionEngine' object has no attribute 'inject_hypotheses'` | API changed |
| `TestReflectionEngine::test_inject_none` | same | same |

**Fix:** Update test to use current API (`inject_hypothesis` or equivalent).

### Group D: Meta-reflect return type mismatch (1 failure)

| Test | Error | Root Cause |
|------|-------|------------|
| `TestReflectionEngine::test_meta_reflect_empty` | `isinstance(new_hypotheses, list)` asserts False; receives single `Hypothesis` object | `meta_reflect` returns a single Hypothesis, not a list |

**Fix:** Either wrap return in list or fix test assertion to accept single object.

### Group E: Missing `load_state` method (1 failure)

| Test | Error | Root Cause |
|------|-------|------------|
| `TestReflectionEngine::test_load_state_missing_file` | `'ReflectionEngine' object has no attribute 'load_state'` | API changed |

**Fix:** Update test to use current save/load API.

## Resolution Priority

1. **Group A** (8 failures, one root cause) — Mechanical, fix all at once by
   adding `rationale` argument. Estimated effort: 5 min.
2. **Group C + E** (3 failures, API drift) — Requires understanding current
   `ReflectionEngine` injection and persistence API. Estimated effort: 15 min.
3. **Group B** (1 failure) — Trivial, fix alongside Group A.
4. **Group D** (1 failure) — Need to confirm whether `meta_reflect` should
   return a list or single Hypothesis. Estimated effort: 5 min.

**Total estimated fix time:** 30 min for someone familiar with GEPA engine.

## Recommendation

Fix Groups A+B+D before v1.0 (trivial, ~10 min). Groups C+E require
understanding the current ReflectionEngine API and may need a maintainer.
Convert to `xfail` with a reference to this document if not fixed by v1.0 tag.
