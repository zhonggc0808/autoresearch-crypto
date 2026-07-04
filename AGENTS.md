# AGENTS.md

This file is the hard-rule entry point for Codex, Reasonix, and other coding agents in this repository.

Project reference, current state, decisions, experiment records, live safeguards, and handoff notes live under `docs/codex/`.

## Local External Paths

- Obsidian vault: `D:\obsidianrepo\zgc的知识库`

## Project Hard Rules

- Treat this repository as a crypto strategy research and live-trading codebase. Live-safety rules take priority over convenience.
- Default to read-only analysis for live trading, checkpoint, exchange connector, order execution, API key, and production strategy files.
- Do not modify these paths without explicit user approval in the current session:
  - `live_*_quant.py`
  - `dex/live/`
  - `configs/live/`
  - `checkpoints/`
  - `.env`, `.env.*`, credentials, API keys, or exchange routing/configuration
  - order placement, position sizing, leverage, stop-loss, retry, lock, or execution-safety code
- Do not modify `dex/strategies/channel_breakout.py` without explicit user approval.
- Do not modify `scripts/research_oracle.py` unless the user explicitly asks for oracle implementation work.
- New strategy or filter experiments must start in `research_workspace/diagnostics/`, `research_workspace/experiments/`, or a reviewed `research_workspace/llm_candidates/` spec. Do not start by editing production strategy files.
- Do not infer live/demo activation, checkpoint promotion, default-oracle routing, scoring changes, or family-registry changes from research results. Those require a separate explicit approval.
- Do not revert dirty files or generated artifacts that you did not create. Work with the existing dirty tree.

## Required Startup Read

Before changing code or running experiments, read these files:

- `docs/codex/PROJECT_BRIEF.md`
- `docs/codex/CURRENT_STATE.md`
- `docs/codex/DECISIONS.md`
- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/LIVE_GUARD.md`
- `docs/codex/HANDOFF.md`

For research/oracle work, also read:

- `docs/reference/llm_research_contract.md`
- `docs/reference/llm_research_runbook.md`
- `research_workspace/README.md`

## Required End-of-Task Updates

Before finishing a task that changes project state, update:

- `docs/codex/CURRENT_STATE.md`
- `docs/codex/HANDOFF.md`

If the task produced an experiment, backtest, diagnostic matrix, or candidate verdict, also update:

- `docs/codex/EXPERIMENT_LEDGER.md`
- `docs/codex/DECISIONS.md` when a direction is promoted, rejected, paused, or closed

Only record durable information: conclusions, evidence paths, modified files, rejected branches, and next handoff steps. Do not record chat filler.

## Experiment Reporting Contract

Every strategy, filter, gate, or execution-safety experiment must report:

- variant name
- parameter set
- sample interval and data window
- raw result
- regime-permission result, if applicable
- safe-execution result, if applicable
- optional drawdown-guard result, if applicable
- OOS return
- maximum drawdown
- rolling 12m result
- trade count
- whether top winners were accidentally blocked or cut
- whether worst losers were blocked or reduced
- conclusion: `REJECT`, `OBSERVE`, or `SHADOW_CANDIDATE`
- exact evidence files

Do not report only the guarded or best-looking result.

## Project Conventions

- Python 3.10+.
- Use `uv`; do not introduce `pip` workflows.
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Tests: `uv run pytest tests/`
- Scoring must use `dex/scoring.py::risk_adjusted_score()` or the established oracle score path, not raw Sharpe alone.
- New strategy classes go under `dex/strategies/`, inherit `BaseStrategy`, register in `dex/strategies/__init__.py`, and update `docs/reference/STRATEGIES.md`. This requires explicit approval if it touches production strategy behavior.

## Current Default Stance

- ETH is the default research/deployment universe.
- BTC and SOL results must be treated as separate evidence, not assumed transferable.
- Current operating baseline and challenger state are defined in `docs/codex/CURRENT_STATE.md`, not in old handoff files.
