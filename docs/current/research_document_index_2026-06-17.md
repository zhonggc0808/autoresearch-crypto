# Research Document Index — 2026-06-17

Purpose: classify the existing research documents so future work starts from
the right state instead of reopening old branches.

This file is an index only. It does not promote candidates, change checkpoints,
change oracle logic, or authorize live/demo routing.

## 2026-06-24 Current Baseline Update

Current ETH baseline is now `channel_breakout_v2_2_m375_bbm375_1p5` plus the
TimesFM quantile gate:

- checkpoint: `checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json`
- live/demo gate config: `configs/live/timesfm_gate_exp_0068.json`
- research candidate: `research_workspace/llm_candidates/exp_0068.json`
- gate params: `context=1024`, `horizon=72`, `min_edge_pct=-0.01`,
  `risk_floor_pct=0.05`

The older 2026-06-17 direction below is historical unless it conflicts with
this update.

## Read Order

Read these first, in order:

1. `research_workspace/research_state_handoff_v0.md`
   - Short current-state entry page.
   - Current framing: v2.2 M375/BBM375/1.5 is the signal core, with TimesFM
     gate as the current entry/reversal filter.

2. `docs/current/codex_handoff_2026-06-16_filter_family_closure.md`
   - Main current handoff.
   - Contains closed filter-family verdicts and the 2026-06-17 Monte Carlo
     sizing update.

3. `research_workspace/research_decision_register_v0.md`
   - Formal decision register.
   - Use it to check what is explicitly closed or not authorized.

4. `docs/baseline/2026-06-11-okx-trend-handoff.md`
   - Historical live/demo and ChannelBreakout evolution context.
   - Read for checkpoint origins, not for current research direction.

## Current Direction

Use these as the current north star:

| File | Status | Use |
|------|--------|-----|
| `research_workspace/research_state_handoff_v0.md` | current | Quick entry page |
| `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` | current | Main handoff and MC conclusions |
| `research_workspace/research_decision_register_v0.md` | current | Closure and authorization boundary |

Current active conclusion:

- `channel_breakout_v2_2_m375_bbm375_1p5` is the current signal core.
- TimesFM gate `exp_0068` is the current filter layer.
- Live/demo should reference `configs/live/timesfm_gate_exp_0068.json`, not
  `research_workspace/llm_candidates/`.
- v2.1 balanced remains historical oracle context, not the current operating
  baseline.
- ETH only remains the recommended deployment universe.

## Baseline And Strategy Context

These explain where the strategy came from and what the frozen baseline means:

| File | Status | Use |
|------|--------|-----|
| `docs/baseline/2026-06-11-okx-trend-handoff.md` | historical context | OKX/demo setup, v2 naked checkpoint origin |
| `docs/baseline/2026-06-12-channel-breakout-v2-1-regime-permission.md` | historical baseline design | v2.1 regime-permission construction |
| `docs/baseline/oracle_baseline_diagnostics.md` | baseline diagnostic | Why v2.1 baseline has known risk |
| `research_workspace/baselines/channel_breakout_v2_1_balanced_params.json` | frozen artifact | Baseline params, do not edit |
| `research_workspace/baselines/channel_breakout_v2_1_balanced_oracle_v0.1.0.json` | frozen result | Old oracle snapshot |
| `research_workspace/baselines/channel_breakout_v2_1_balanced_oracle_v0.2.json` | frozen result | Current oracle snapshot |

Do not infer that v2.1 remains the best signal core just because it is the
frozen baseline. The 2026-06-17 Monte Carlo notes supersede that as research
direction.

As of 2026-06-24, also do not infer that naked v2 `375/432` remains the
operating baseline; the current operating baseline is v2.2 M375/BBM375/1.5 plus
TimesFM gate.

## Current Research Operations

These define the research workflow and guardrails:

| File | Status | Use |
|------|--------|-----|
| `docs/reference/llm_research_contract.md` | active contract | Evaluation and candidate rules |
| `docs/reference/llm_research_runbook.md` | active runbook | How to run/review research cycles |
| `docs/reference/llm_research_org.md` | active org doc | Workspace structure |
| `docs/reference/llm_research_release_checklist.md` | checklist | Release hygiene |
| `docs/reference/research_sandbox_design.md` | architecture reference | Sandbox/oracle design |
| `research_workspace/README.md` | partially stale | LLM workspace rules; respect hard boundaries, but current direction lives in handoff docs |
| `research_workspace/candidate_schema_v0.2.json` | schema | Candidate validation |
| `research_workspace/family_schemas/*.json` | schemas | Family schema reference |

## Closed Or Paused Branches

These are useful evidence, not active directions:

| Branch | Status | Primary files |
|--------|--------|---------------|
| `volatility_gate` | paused | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md`, `research_workspace/llm_candidates/exp_0045.json` |
| `cooldown_after_drawdown` | closed | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md`, `research_workspace/llm_candidates/exp_0046.json` |
| `neutral_regime_entry_block` | closed | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md`, `research_workspace/llm_candidates/exp_0053.json` |
| B2N/BTN transition scans | closed | `research_workspace/b2n_transition_scan/`, `research_workspace/btn_transition_scan/` |
| rolling12m long/risk branches | closed | `research_workspace/rolling12m_*` |
| N2B helper activation | gate closed, shadow only | `research_workspace/activation_readiness/`, `research_workspace/transition_warmup/` |

Rule: do not repackage a closed branch as a new upgrade without new upstream
evidence and a new contract.

## Historical Phase Docs

These explain earlier thinking. Do not use them as current direction without
checking the handoff first.

| File | Status | Notes |
|------|--------|-------|
| `docs/historical/phase3a_summary.md` | historical | Older baseline comparison |
| `docs/historical/phase3b_design.md` | historical | Older oracle/filter design |
| `docs/historical/phase3c_design.md` | stale | Adaptive lookback direction is not current |
| `docs/historical/phase4_design.md` | stale | Superseded by filter-family closure |
| `docs/historical/phase4a_overlay_validation_result.md` | historical | Overlay evidence, not current priority |
| `docs/historical/2026-06-15-oracle-v02-configurable-regime-audit-results.md` | closed audit | Keep 50/200; do not restart sensitivity sweep |
| `docs/superpowers/specs/*.md` | implementation history | Plans/specs, not current strategy direction |
| `docs/superpowers/plans/*.md` | implementation history | Plans, not current strategy direction |

## Historical v2.2 / Exit Overlay Research

Keep these for evidence. This section refers to older v2.2/exit-overlay
branches such as `mtg_bcd`, not the current
`channel_breakout_v2_2_m375_bbm375_1p5` + TimesFM baseline:

| File | Status | Use |
|------|--------|-----|
| `docs/historical/2026-06-16-channel-breakout-v2-1-profit-taking-research.md` | historical | Profit-taking exploration |
| `docs/historical/2026-06-16-channel-breakout-v2-1-profit-lock-design.md` | historical design | Profit-lock mechanism design |
| `docs/historical/2026-06-16-channel-breakout-v2-1-profit-lock-high-return-combos.md` | historical results | High-return combinations |
| `docs/historical/2026-06-16-channel-breakout-v2-1-exp0024-risk-attribution-archive.md` | historical archive | exp_0024/v2.2 risk attribution |
| `checkpoints/channel_breakout_v2_2_mtg_bcd.json` | demo candidate | Observation-only unless separately promoted |

Reason: v2.2 reduced trades and return but barely improved Monte Carlo
`DD < -30%` breach probability.

## Market Intelligence And Strategy Reference

Use these as references only:

| File | Status | Use |
|------|--------|-----|
| `docs/reference/market_intelligence_overlay.md` | research reference | Market-intel overlay notes |
| `docs/reference/STRATEGIES.md` | strategy reference | Strategy catalog |
| `docs/reference/validation_framework.md` | validation reference | Validation framing |
| `docs/historical/candidate_status.md` | old snapshot | Candidate state at older date |
| `docs/reference/策略.md` | old strategy notes | Chinese brainstorm/reference |
| `docs/reference/策略2.md` | old strategy notes | Chinese brainstorm/reference |

## Generated Research Artifacts

These folders are data exhaust. Search them only when a specific evidence trail
is needed:

| Path | Use |
|------|-----|
| `research_workspace/llm_candidates/` | Candidate JSONs |
| `research_workspace/llm_scorecards/` | Candidate/oracle scorecards |
| `research_workspace/llm_runs/` | Run logs |
| `research_workspace/proposals/` | Generated/accepted/rejected proposal actions |
| `research_workspace/candidate_states/` | Candidate lifecycle states |
| `research_workspace/meta_reviews/` | Meta-review outputs |
| `research_workspace/*/no_touch_audit.md` | Evidence that a branch did not touch protected paths |

Do not start by reading all generated artifacts. Start from the current handoff,
then open only the branch folder needed for the question.

## Applied Layout

The top-level `docs/` Markdown files have been moved into this structure:

```text
docs/
  current/
    codex_handoff_2026-06-16_filter_family_closure.md
    research_document_index_2026-06-17.md
  baseline/
    2026-06-11-okx-trend-handoff.md
    2026-06-12-channel-breakout-v2-1-regime-permission.md
    oracle_baseline_diagnostics.md
  historical/
    phase*.md
    2026-06-15-*.md
    2026-06-16-channel-breakout-*.md
  reference/
    STRATEGIES.md
    validation_framework.md
    llm_research_*.md
```

`research_workspace/` generated artifacts were intentionally left in place.
