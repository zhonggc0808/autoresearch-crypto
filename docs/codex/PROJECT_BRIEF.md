# Project Brief

Last updated: 2026-06-27

## Purpose

`autoresearch-crypto` is an autonomous crypto quantitative strategy research framework. It covers data download, strategy discovery, backtesting, evolution training, research sandboxing, and demo/live trading.

The project is research-heavy but has real live-trading entry points. Treat live execution safety as a first-class constraint.

## Main Pipelines

| Stage | Entry points | Purpose |
|---|---|---|
| Strategy discovery | `search_eth_optimal.py`, `search_deep.py` | Evolutionary search over strategy parameters |
| Backtesting | `backtest_quant.py` | Walk-forward backtests with fees and slippage |
| Training/evolution | `train_quant.py`, `scripts/evolve.py`, `scripts/evolve_gepa.py` | ATLAS and GEPA style evolution |
| Research oracle | `scripts/research_oracle.py` | Read-only evaluation gate for sandbox candidates |
| Live/demo trading | `live_bitget_quant.py`, `live_nado_quant.py`, `live_okx_quant.py`, `live_binance_quant.py` | Exchange-specific runtime entry points |

## Core Library

Important modules:

- `dex/strategies/base.py`: `BaseStrategy` and `StrategyEvaluator`.
- `dex/strategies/channel_breakout.py`: ChannelBreakout signal core. Protected by AGENTS rules.
- `dex/scoring.py`: risk-adjusted scoring. Do not replace with pure Sharpe.
- `dex/regime_filter.py`: market regime filter used by ChannelBreakout.
- `dex/drawdown_guard.py`: drawdown guard.
- `dex/execution_safety.py`: local execution-safety signal patches.
- `dex/live/`: shared live runtime helpers. Protected path.
- `dex/live/signals.py`: live signal handling.
- `dex/live/risk.py`: runtime risk controls.

## Strategy Signal Contract

All strategies inherit `BaseStrategy` and implement:

```python
generate_signals(df) -> np.ndarray
```

Signal encoding:

| Value | Meaning |
|---:|---|
| 0 | flat / close |
| 1 | hold |
| 2 | long |
| 3 | short |

## Data

Data is OHLCV Parquet under `data/crypto/`, named like:

- `ETHUSDT_5m_60d.parquet`
- `ETHUSDT_5m_2600d.parquet`

Do not assume data files are portable or committed.

## Current Operating Family

Current active strategy family is ChannelBreakout on ETH.

The current operating baseline and challenger state are maintained in:

- `docs/codex/CURRENT_STATE.md`
- `docs/codex/EXPERIMENT_LEDGER.md`

Historical baseline docs remain useful for evidence, but do not override `docs/codex/`.

## Essential Commands

Install and development:

```powershell
uv sync
uv sync --extra dev
uv run ruff check .
uv run ruff format .
uv run pytest tests/
```

Data:

```powershell
uv run python prepare_crypto.py --symbol ETHUSDT --interval 5m --days 60
```

Backtest:

```powershell
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 60
```

Research oracle:

```powershell
uv run python scripts/research_oracle.py --baseline
uv run python scripts/research_oracle.py --candidate research_workspace/llm_candidates/exp_0068.json
```

Live/demo examples require explicit user intent before editing related files:

```powershell
uv run python live_bitget_quant.py --symbol ETHUSDT --interval 5m --demo --capital 100
uv run python live_binance_quant.py --symbol BTCUSDT --interval 5m --demo --capital 100
```

## Research Sandbox

The research sandbox separates exploratory work from production code:

- `research_workspace/`: LLM/research writable area.
- `docs/reference/research_sandbox_design.md`: sandbox architecture.
- `docs/reference/llm_research_contract.md`: active candidate/evaluation contract.
- `scripts/research_oracle.py`: oracle entry point. Treat as protected unless the user asks for oracle changes.

Initial candidate work should start as diagnostics or specs, not production strategy edits.
