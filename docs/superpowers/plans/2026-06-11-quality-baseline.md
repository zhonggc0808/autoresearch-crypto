# Quality Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize checkpoint replay, add a first useful test suite, and reduce live/backtest duplication risk.

**Architecture:** Add a small shared checkpoint factory under `dex/` that owns checkpoint-to-strategy construction and signal invocation differences. Keep backtest and live scripts as thin callers. Add focused pytest tests before changing production behavior.

**Tech Stack:** Python 3.10, pytest, Ruff, pandas/numpy, existing `dex` package.

---

### Task 1: Shared Strategy Construction

**Files:**
- Create: `dex/checkpoints.py`
- Modify: `backtest_quant.py`
- Modify: `live_binance_quant.py`
- Modify: `live_okx_quant.py`
- Modify: `live_nado_quant.py`
- Test: `tests/test_checkpoints.py`

- [ ] Write failing tests for constructing `pureaction`, `hybrid_mm`, and `regime` strategies from checkpoint dicts.
- [ ] Write failing tests for generating signals through a shared helper when strategy signatures differ.
- [ ] Implement `build_strategy_from_checkpoint`, `generate_strategy_signals`, and `load_checkpoint`.
- [ ] Wire backtest and live scripts to use the shared helpers.
- [ ] Verify the `eth_optimal.pt` pureaction checkpoint backtests without a signature error.

### Task 2: Strategy Registry Completeness

**Files:**
- Modify: `dex/strategies/__init__.py`
- Test: `tests/test_strategy_registry.py`

- [ ] Write failing tests for `PureActionV2Strategy` and `MultiTFEnsembleStrategy` exports.
- [ ] Add lazy imports and `__all__` entries.
- [ ] Verify imports work through `from dex.strategies import ...`.

### Task 3: Baseline Behavior Tests

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_evaluator.py`
- Create: `tests/test_scoring.py`
- Create: `tests/test_live_common.py`
- Create: `tests/test_strategy_contracts.py`

- [ ] Add reusable OHLCV fixture.
- [ ] Test evaluator metrics on hold/long/short signal paths.
- [ ] Test scoring flags for overfit and risky cases.
- [ ] Test live order-price and stop-loss helpers.
- [ ] Test strategy signal length and allowed values for registered strategies.

### Task 4: CI and Lint Baseline

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`
- Format/lint touched Python files.

- [ ] Add pytest to CI with plugin autoload disabled.
- [ ] Add direct dev dependencies needed for local pytest reliability.
- [ ] Run focused tests and compile check.
- [ ] Run Ruff on touched files and note remaining repository-wide Ruff debt.
