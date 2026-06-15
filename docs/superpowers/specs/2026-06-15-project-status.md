# Project Status — 2026-06-15

## Architecture Overview

```
Structured Research Org (v1.0)          Codegen Sandbox (v0.9d)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Multi-family LLM research loop          LLM → strategy.py → scan → sandbox
run_batch_trial.py                      → validate → codegen scorecard
3 families registered                   usable but quarantined
Oracle 1300d evaluation                 independent scorecard dir
Scorecard → reviewer → action           never promotes
llm_results.tsv                         no llm_results.tsv pollution

Shared infrastructure:
  - dex/filters.py (filter execution engine)
  - family_registry.py (family definitions)
  - validate_candidate_v08.py (family-aware validator)
  - research_oracle.py (1300d ETH evaluation)
  - score_candidate.py (scorecard engine)
  - review_candidate.py (LLM action reviewer)
  - execute_action.py (action executor)
  - meta_review.py (process retrospective)
```

## Completed Phases

### v0.9a — Limited Codegen Sandbox
- Static code scanner (AST-based, forbidden imports/calls)
- Restricted import sandbox (safe builtins only)
- Dynamic validation (interface, future-data, side-effect)
- Full generate → scan → sandbox → validate → wrap flow

### v0.9b — Codegen Evaluation Adapter
- Three-phase adapter: re-verify → evaluate → scorecard
- Imports oracle pure functions as read-only compatibility layer
- All verdicts overridden to `codegen_research_only`
- Independent `codegen_scorecards/` directory

### v0.9c — Real LLM Codegen
- Anthropic API via stdlib (no new dependencies)
- Multi-provider LLM client (Anthropic + OpenAI-compatible)
- Auto-load `.env` for API keys
- Structured JSON response parsing with markdown fence compat
- Prompt exposes zero project internals

### v0.9d — One-Shot Smoke
- `run_codegen_smoke.py`: generate + evaluate in single command
- 57 tests covering full codegen pipeline

### v1.0 — Structured Research Org
- Multi-family batch runner (`run_batch_trial.py`)
- LLM research cycle: generate → validate → evaluate → scorecard → review → execute
- Meta-review agent (scorecard-first, `--latest` mode)
- Family registry with 5 registered families
- Validator pipeline unified to v0.8 (family-aware, filter/overlay support)

## Registered Families

| Family | Type | Status |
|--------|------|--------|
| `channel_breakout` | Strategy (regime_permission) | Active |
| `volatility_filtered_breakout` | Strategy | Active |
| `exit_logic_variant` | Strategy | Active (watch trades/yr) |
| `volatility_gate` | Filter/overlay | **Paused** — see below |
| `cooldown_after_drawdown` | Filter/overlay | **Closed** — exp_0046 |

## Filter Family Test Results

### volatility_gate (paused — 2026-06-15)

ATR/close threshold sweep on ETH 5m (lookback 48):

Calibration: p90=0.0039, p95=0.0047, p97=0.0053, p99=0.0069, max=0.030

| Exp | Action | Threshold | blocked | Fee@10bp | Rolling12m | OOS safe | Verdict |
|-----|--------|-----------|---------|----------|-----------|----------|---------|
| exp_0045 | broad block | 0.06 | 0 (no-op) | -1.43% | -15.57% | +199.85% | baseline |
| exp_0047 | block_long | 0.05 | 0 (no-op) | -65.41% | -31.13% | — | ❌ kill |
| exp_0048 | block_short | 0.05 | 0 (no-op) | -1.43% | -15.57% | +199.85% | identical to 0045 |
| exp_0050 | block_short | 0.0047 (p95) | 488 | **+0.72%** | **-19.05%** | +182.28% | fee⇧, rolling⇩ |
| exp_0051 | block_short | 0.0069 (p99) | 7 | -1.19% | -15.57% | +199.85% | near no-op |
| exp_0052 | block_short | 0.0053 (p97) | 248 | +0.05% | -16.83% | +176.17% | tradeoff |

**Conclusion:** Volatility gate can improve IS fee robustness but always worsens rolling12m and OOS return. No threshold avoids this tradeoff. Family paused.

### cooldown_after_drawdown (closed — 2026-06-15)

| Exp | Description | IS DD | Rolling12m | Fee@10bp | Verdict |
|-----|-------------|-------|-----------|----------|---------|
| exp_0046 | close_drawdown, threshold 0.15 | -67.76% | -39.81% | -21.36% | ❌ kill (everything worse) |

**Conclusion:** Close-drawdown cooldown is too crude — blocks recovery entries, misses rebounds, keeps losing exposure. Closed.

### channel_breakout regime_filter variants

Three early experiments (exp_0042/43/44) confirmed that adjusting only `entry_lookback`, `min_hold_bars`, `fast_days`, `slow_days`, or EMA regime conditions cannot solve rolling12m negative returns or DD_OVER_40. These are killed by oracle and should not be repeated.

## Key Engineering Fixes (v1.0.x)

| Fix | Description |
|-----|-------------|
| v1.0.1 | Action `source_candidate_id` now accepts `oracle_*` ID format |
| v1.0.2 | Meta-review `--latest` mode: scorecard-first, demotes historical rejections |
| v1.0.3 | Derived `TURNOVER_EXPLOSION` flag in scorecard (trades/yr > 500 or > baseline×10) |
| filter family alias normalization | Maps LLM near-synonyms → canonical names (validator + generator) |
| filter field normalization | Maps metric/action near-synonyms in generator before validation |
| evaluator validator unified | All three entry points now use v0.8 family-aware validator |
| evaluator 1300d error stop | Oracle error now sets final_verdict immediately, doesn't continue to 2600d |
| reviewer source_candidate_id | Force-set to experiment_id before validation |
| reviewer fork discipline | Rule: fork only if metrics materially improved vs prior candidate |
| directional filter bug | `block_short/long_entries_when_high_vol` were not in blocked-determination chain |

## LLM Client Configuration

Multi-provider via `.env`:
```
LLM_PROVIDER=openai
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
```

Auto-loads `.env` from project root. Falls back to `ANTHROPIC_API_KEY` for Anthropic compatibility.

## Test Suites

| Suite | Tests | Focus |
|-------|-------|-------|
| `test_codegen_sandbox_v09a.py` | 25 | Static scan, import sandbox, validation, LLM mock |
| `test_codegen_evaluation_v09b.py` | 24 | Adapter, verdict override, exit codes, oracle signatures |
| `test_codegen_one_shot_v09d.py` | 8 | Smoke pipeline, dry-run, error handling |
| `test_action_executor_v06.py` | 14 | Kill, fork, create, stable, promote_review |
| `test_multi_family_v08.py` + batch | ~30 | Family registry, validation, templates |
| Others (various) | ~50 | Oracle, scorecard, review, verdict engine |

**Total: ~150+ tests across all suites.**

## Operational Rules

1. **Structured path** is the main research line — multi-family batch with oracle-gated evaluation.
2. **Codegen path** is a creative probe — single candidate, manual review, never promotes.
3. Filter/overlay candidates must pass schema → validator → oracle before forking.
4. No LLM cycles without calibration validation on the filter metric first.
5. No batch > 3-5 cycles before meta-review.
6. `execute_create`/`executed_fork` ≠ strategy success. Only oracle PASS counts.
7. Prompt exposes zero project internals (no oracle, baseline, scorecard, filesystem).
8. Oracle core (`scripts/research_oracle.py`, `dex/`) modified only for filter execution wiring — never for scoring logic.

## Current Bottleneck

**Not pipeline stability — but search space quality.** The system can reliably generate, validate, evaluate, and score candidates. But meaningful filter design requires:
- Metric calibration before testing (done for ATR/close)
- Directional hypothesis before blind forking
- Meta-review analysis between cycles to redirect search

## Next Actions (uncommitted)

1. Register `neutral_regime_entry_block` as third filter family
2. Manual threshold calibration on neutral-regime entry data
3. Test single calibrated candidate before LLM-in-the-loop
4. 3-cycle meta-review to assess filter learning quality
