# LLM Research Organization — Architecture

**Version:** 1.0.0  
**Status:** Stable  
**Codegen:** Disabled  
**Promotion:** Human review required  

---

## 1. Overview

The LLM Research Organization is a structured, auditable, constraint-based
system for autonomous cryptocurrency trading strategy research. LLMs propose
hypotheses and actions but never directly modify code, checkpoints, oracle
evaluation logic, or live trading infrastructure. All decisions go through
deterministic gates (schema validation, verdict engine, action validation,
executor).

### Design Principles

| Principle | Description |
|-----------|-------------|
| **Evaluate contract immutable** | LLM cannot modify oracle, scoring, or executor code |
| **Sandbox isolation** | LLM writes only to `research_workspace/` |
| **Dual-channel review** | Exploration lane → Promotion lane (gates + human) |
| **Frozen baseline** | v2.1 balanced is never modified |
| **Registry-driven** | All strategy families registered in one central place |
| **Runner-assigned IDs** | LLM never provides or chooses experiment IDs |

---

## 2. Architecture

```
                    ┌─────────────────────┐
                    │   Generator (v0.4)  │ ←── LLM proposes candidate
                    └─────────┬───────────┘
                              │ candidate.json
                              ▼
                    ┌─────────────────────┐
                    │  Validator (v0.2)   │ ←── JSON Schema + family rules
                    └─────────┬───────────┘
                              │ if invalid → rejected
                              ▼
                    ┌─────────────────────┐
                    │  Evaluator (v0.3)   │ ←── Oracle: 1300d → 2600d
                    └─────────┬───────────┘
                              │ scorecard + verdict
                              ▼
                    ┌─────────────────────┐
                    │   Scorer (v0.3)     │ ←── Verdict engine + scorecard
                    └─────────┬───────────┘
                              │ scorecard.json
                              ▼
                    ┌─────────────────────┐
                    │   Reviewer (v0.5)   │ ←── LLM proposes next action
                    └─────────┬───────────┘
                              │ action.json
                              ▼
                    ┌─────────────────────┐
                    │  Executor (v0.6)    │ ←── Deterministic file ops
                    └─────────┬───────────┘
                              │ fork/kill/create/stable/promote
                              ▼
                    ┌─────────────────────┐
                    │  Meta-Agent (v0.7)  │ ←── Periodic retrospective
                    └─────────────────────┘
```

### 2.1 Component Roles

| Component | File | Role |
|-----------|------|------|
| Generator | `scripts/generate_candidate.py` | Proposes candidate specs via LLM |
| Validator | `scripts/validate_candidate_v02.py` | Schema + rule validation |
| Validator (family) | `scripts/validate_candidate_v08.py` | Family-aware validation dispatch |
| Evaluator | `scripts/evaluate_candidate.py` | State-machine oracle runner |
| Scorer | `scripts/score_candidate.py` | Pure verdict computation |
| Reviewer | `scripts/review_candidate.py` | Proposes next action via LLM |
| Executor | `scripts/execute_action.py` | Deterministic action execution |
| Meta-Agent | `scripts/meta_review.py` | Retrospective analysis |
| Cycle Runner | `scripts/run_llm_research_cycle.py` | Orchestrates one full cycle |
| Batch Runner | `scripts/run_batch_trial.py` | Multi-cycle + meta-review |
| Family Registry | `scripts/family_registry.py` | Central family authority |
| LLM Client | `scripts/llm_client.py` | Thin LLM wrapper (mockable) |

---

## 3. Directory Structure

```
autoresearch-crypto/
├── docs/
│   ├── llm_research_contract.md       # Formal contract §2 (forbidden paths)
│   ├── llm_research_org.md            # This document
│   ├── llm_research_runbook.md        # Operations guide
│   └── llm_research_release_checklist.md
│
├── research_agents/
│   └── prompts/
│       ├── candidate_generator.md     # Generator LLM prompt
│       ├── result_reviewer.md         # Reviewer LLM prompt
│       └── meta_review.md             # Meta-agent LLM prompt
│
├── research_workspace/
│   ├── LLM_RESEARCH_ORG_VERSION.json  # Version lock
│   ├── candidate_schema_v0.2.json     # Common candidate schema
│   ├── family_schemas/                # Per-family JSON schemas
│   ├── llm_candidates/               # Generated candidate specs
│   ├── llm_scorecards/               # Evaluation scorecards
│   ├── llm_runs/                     # Run records + batch summaries
│   ├── meta_reviews/                 # Meta-agent reviews
│   └── proposals/                    # Actions, create requests, promotion packets
│       ├── actions/
│       ├── rejected_actions/
│       ├── create_requests/
│       └── promotion_reviews/
│
├── scripts/
│   ├── family_registry.py
│   ├── generate_candidate.py
│   ├── validate_candidate_v02.py
│   ├── validate_candidate_v08.py
│   ├── evaluate_candidate.py
│   ├── score_candidate.py
│   ├── review_candidate.py
│   ├── execute_action.py
│   ├── run_llm_research_cycle.py
│   ├── run_batch_trial.py
│   ├── meta_review.py
│   └── verify_llm_research_org.py
│
├── tests/
│   ├── known_failures.md             # Pre-existing failures policy
│   ├── test_candidate_schema_v02.py
│   ├── test_verdict_engine_v03.py
│   ├── test_llm_candidate_generator_v04.py
│   ├── test_llm_result_reviewer_v05.py
│   ├── test_action_executor_v06.py
│   ├── test_llm_research_cycle_e2e.py
│   ├── test_meta_agent_v07.py
│   ├── test_multi_family_v08.py
│   └── test_multi_family_batch_v085.py
```

---

## 4. Allowed & Forbidden Paths

| Scope | Allowed | Forbidden |
|-------|---------|-----------|
| **Read** | Any file (read-only) | — |
| **Write (LLM)** | `research_workspace/` only | Everything else |
| **Write (system)** | `research_workspace/`, `scripts/`, `docs/`, `tests/` | `dex/`, `checkpoints/`, `data/`, `live_*.py` |
| **Modify** | Prompt files, runbooks, test files | Oracle, baseline, live code, config |

### 33 Immutable Files (per §2 of contract)

These files must NEVER be modified by any agent:

- `checkpoints/channel_breakout_v2_1_balanced.pt`
- `dex/strategies/base.py`, `hybrid_mm.py`, `trend.py`, etc.
- `dex/indicators.py`, `dex/scoring.py`, `dex/config.py`
- `scripts/research_oracle.py`, `scripts/score_candidate.py`
- `live_nado_quant.py`, `live_okx_quant.py`, `live_binance_quant.py`
- `dex/live/common.py`
- `research_workspace/candidate_schema_v0.2.json`
- + family schemas (read-only by convention, modifiable by maintainer)

---

## 5. Family Registry

### 5.1 Rules

1. Every family must be registered in `scripts/family_registry.py`.
2. Each family has a `FamilyDefinition` with parameter bounds, allowed_change
   specs, and a dedicated JSON Schema in `research_workspace/family_schemas/`.
3. Allowed_change is validated via `family_registry.validate_allowed_change()`.
4. The executor applies nested changes via `allowed_change_nested` — no
   hardcoded field names.
5. The generator resolves `strategy` from `params.strategy_type` via the
   registry — no hardcoded family name.

### 5.2 Registered Families (v1.0)

| Family | Strategy Type | Schema File | Key Parameters |
|--------|---------------|-------------|----------------|
| `channel_breakout` | `regime_permission_channel_breakout` | `channel_breakout_v0.2.json` | entry_lookback, min_hold_bars |
| `volatility_filtered_breakout` | `volatility_filtered_channel_breakout` | `volatility_filtered_breakout_v0.8.json` | + volatility_filter (lookback, mode, quantiles) |
| `exit_logic_variant` | `exit_logic_channel_breakout` | `exit_logic_variant_v0.8.json` | + exit_logic (TP, SL, max_hold, trailing) |

### 5.3 Adding a New Family

1. Add `FamilyDefinition` to `family_registry.py`
2. Create schema file in `research_workspace/family_schemas/`
3. Add batch templates in `run_batch_trial.py` (if batch mode needed)
4. Update `candidate_schema_v0.2.json` strategy_type enum
5. No codegen needed — registry handles validation, fork, and dispatch

---

## 6. Candidate Lifecycle

```
┌──────────┐     ┌───────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│ GENERATE │ ──► │ VALIDATE  │ ──► │ EVALUATE │ ──► │ SCORECARD│ ──► │  REVIEW  │ ──► │ EXECUTE  │
│  (LLM)   │     │ (schema)  │     │ (oracle) │     │ (verdict)│     │  (LLM)   │     │ (system) │
└──────────┘     └───────────┘     └──────────┘     └──────────┘     └──────────┘     └──────────┘
     │                │                │                │                │                │
     ▼                ▼                ▼                ▼                ▼                ▼
 exp_NNNN           rejected       scorecard       verdict          action          fork/kill/
 .json              + reason       .json           + flags          .json           create/stable/
                                                                                    promote_review
```

### Gate Exits

- `generate_failed` — LLM error, non-JSON, forbidden content
- `validation_failed` — Schema violation, missing fields, out of bounds
- `evaluate_failed` — Oracle error
- `review_failed` — Invalid action, forbidden content
- `execute_failed` — Executor error

### Terminal States

- `executed_kill` — Candidate archived, no further iteration
- `executed_fork` — New candidate derived from source
- `executed_create` — New baseline proposal submitted
- `executed_stable` — Candidate accepted as research-only
- `executed_promote_review` — Promotion packet generated (human review)

---

## 7. Action Lifecycle

| Action | Source | Effect | Allowed Verdicts |
|--------|--------|--------|-------------------|
| **kill** | Any candidate | Writes killed state, no further cycles | kill, requires_2600d, blocked_missing_2600d_data, research_only_recent_regime |
| **fork** | Any candidate | Generates child with `allowed_change` applied | kill, requires_2600d, blocked_missing_2600d_data, research_only_recent_regime |
| **create** | `baseline` | Writes create request for maintainer | Any |
| **stable** | Any candidate | Marks as stable, research_only | requires_2600d, blocked_missing_2600d_data, research_only_recent_regime |
| **promote_review** | Any candidate | Generates promotion packet for human review | promote_review_pending only |

### Constraints

- `MAX_CONSECUTIVE_STABLE = 2`
- Fork preserves source family identity (no cross-family fork)
- Allowed_change validated against family registry per family
- Promotion still requires human review (not automated)

---

## 8. Meta-Review

The meta-agent (`scripts/meta_review.py`) analyzes accumulated run history
and produces structured findings. It:

- Aggregates runs, scorecards, actions, and rejection patterns
- Builds **family-level analysis** per registered family
- Identifies failure patterns, stuck loops, and success signals
- Produces recommendations for search space, prompts, or process changes
- Sets `requires_human_review` for contract/prompt changes

---

## 9. Known Failures Policy

See `tests/known_failures.md` for the current inventory of 13 pre-existing
failures in `test_reflection.py` and `test_llm_hypothesis.py`.

- These are **unrelated to the research loop** (GEPA engine API drift).
- They are **allowed** for v1.0.
- They **must be resolved** (or xfailed) before a formal production release.
- New failures in the research loop test suite are NOT covered by this policy.
