---
name: live-change-guard
description: Use before modifying any live trading, demo trading, checkpoint, exchange connector, order execution, runtime risk, live config, API credential, production ChannelBreakout, or research-oracle file in autoresearch-crypto. Requires file classification and explicit user approval for live-risk, config, checkpoint, oracle, or protected strategy edits.
---

# Live Change Guard

## Workflow

Use this guard before editing any file that may affect live or demo trading.

1. Read `AGENTS.md` and `docs/codex/LIVE_GUARD.md`.
2. List every file the task may touch before editing.
3. Classify each file as one of:
   - `research-only`
   - `config`
   - `oracle`
   - `strategy`
   - `live-risk`
4. If any file is `live-risk`, `config`, `checkpoint`, `oracle`, or protected strategy, stop and ask the user for explicit approval.
5. Continue only after approval, and keep the edit scoped to the approved files.

## Protected Areas

Always treat these as protected:

- `live_*_quant.py`
- `dex/live/`
- `configs/live/`
- `checkpoints/`
- `.env`, `.env.*`, credentials, API keys, account ids, exchange routing
- order placement, cancellation, leverage, margin, position sizing, stop loss, retry, lock, runtime risk, and execution-safety code
- `dex/strategies/channel_breakout.py`
- `scripts/research_oracle.py`

## Required Approval Message

When approval is needed, provide:

- files that would be touched
- classification for each file
- intended behavior change
- validation plan
- rollback path

Do not edit protected files before approval.

## Non-Negotiables

- Never change credentials or print secrets.
- Never silently change execution price semantics.
- Never convert shadow mode into enforcement.
- Never route adjusted signals into execution or scoring without explicit approval.
- Never promote a checkpoint or live config because a diagnostic result looks good.
