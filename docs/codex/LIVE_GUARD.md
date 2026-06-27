# Live Guard

Last updated: 2026-06-27

This file defines safeguards for any work that could affect live or demo trading.

## Default Rule

If a task may touch live trading behavior, stop before editing and ask for explicit approval.

This includes demo trading. Demo routes can become templates for real routes, so treat them as high-risk.

## Protected Paths

Do not modify without explicit approval in the current session:

- `live_bitget_quant.py`
- `live_nado_quant.py`
- `live_okx_quant.py`
- `live_binance_quant.py`
- `dex/live/`
- `configs/live/`
- `checkpoints/`
- `.env`, `.env.*`, API keys, credentials, account ids, exchange routing
- order placement, order cancellation, leverage, margin mode, position sizing, stop loss, retry, lock, and execution-safety code

Extra protected strategy file:

- `dex/strategies/channel_breakout.py`

Protected oracle file unless explicitly requested:

- `scripts/research_oracle.py`

## Required Pre-Edit Checklist

Before editing any file that might be live-sensitive:

1. List every file that would be touched.
2. Classify each file as `research-only`, `config`, `oracle`, `strategy`, or `live-risk`.
3. Explain the intended behavior change.
4. State the rollback path.
5. Ask for explicit approval if any file is `live-risk`, `config`, `checkpoint`, `oracle`, or protected strategy.

Do not continue until the user approves.

## Runtime Safety Requirements

Live or demo changes must preserve:

- completed-bar-only signal decisions
- no future data
- explicit fallback behavior when model/gate inference fails
- timeout handling
- retry limits
- state persistence safety
- lock handling
- safe flat behavior for blocked reversals
- no credential logging

Maker-first behavior is preferred where applicable. Any taker fallback must be explicit and justified.

## Promotion Requirements

Before a research candidate can affect live/demo routing, it needs:

- raw result
- regime-permission result, if applicable
- safe-execution result
- optional drawdown-guard result, if applicable
- OOS return
- max drawdown
- rolling12m
- trade count
- top-winner damage report
- worst-loser capture report
- evidence files
- fallback plan
- explicit user approval

## Forbidden Shortcuts

- Do not activate a gate because a diagnostic file looks good.
- Do not promote a checkpoint from a research artifact alone.
- Do not change API keys or exchange routing as part of strategy work.
- Do not silently change execution price semantics.
- Do not treat shadow mode as live enforcement.
- Do not route adjusted signals into execution or scoring unless explicitly approved.
