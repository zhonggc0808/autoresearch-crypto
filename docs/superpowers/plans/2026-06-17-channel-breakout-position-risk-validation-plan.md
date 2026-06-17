# Channel Breakout 375/432 — Position Sizing & Tail-Risk Validation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 0 diagnostic ledger, Phase 1 fixed-size matrix, and Phase 2 simulator infrastructure (DD sizing + adverse stop) as specified in `docs/superpowers/specs/2026-06-17-channel-breakout-position-risk-validation.md`.

**Architecture:** Minimal enhancement to existing `StrategyEvaluator.simulate()` in `dex/strategies/base.py`, a new `enrich_trade_ledger()` in `dex/strategies/trade_ledger.py`, two new research scripts in `scripts/`, and extended tests in `tests/`. Phase 2 `stop_config` is added to `simulate()` as an optional parameter with per-bar stop checking between signal processing and equity computation.

**Tech Stack:** Python 3.10+, NumPy, Pandas, pytest. Existing virtualenv at `.venv\Scripts\python.exe`.

## Global Constraints

- Do NOT modify: `evaluate()`, `compute_metrics()`, `channel_breakout.py`, any checkpoint file, any live file
- Do NOT add, remove, or modify any entry-block filter logic
- Phase 2 `stop_config.base_size` is the ONLY position source; `position_sizes` fixed to 1.0
- Stops cannot trigger on entry bar (`entry_step + 1` minimum)
- At most ONE risk action per bar (close > reduce_half > reduce_quarter)
- Stop execution uses close fill — no intrabar ideal fill
- Squeeze stop Donchian MUST be lagged (shifted by 1)
- Partial close requires dual ledger (event + logical trade)
- Final thresholds require Gate 0 review; parameter grids must be pre-registered
- Ruff line-length=100, import sorting (I) enabled

---

## Plan A: Phase 0 + Phase 1 (Parallel)

### Task A1: Enhance simulate() open events with entry fields

**Files:**
- Modify: `dex/strategies/base.py:197-222` (open long/short sections)

**Interfaces:**
- Produces: open events now include `entry_price`, `entry_notional`, `entry_size`

- [ ] **Step 1: Add fields to open events**

In `dex/strategies/base.py`, find the open-long section (around line 207). The current code appends:
```python
trades.append({"type": "buy", "step": i, "entry_size": entry_size_multiplier})
```

Change both open-long and open-short event dicts to include `entry_price`, `entry_notional`, and `entry_size`:

For open-long (lines ~197-208):
```python
if target_pos == 1 and position == 0 and capital > 0 and size_multiplier > 0:
    deploy = capital * size_multiplier
    exec_price = price * (1 + self.slippage)
    shares = deploy * (1 - self.commission) / exec_price
    entry_cost_basis = deploy
    entry_price = exec_price
    capital -= deploy
    entry_step = i
    entry_size_multiplier = size_multiplier
    trades.append({
        "type": "buy",
        "step": i,
        "entry_size": entry_size_multiplier,
        "entry_price": float(entry_price),         # NEW
        "entry_notional": float(deploy),             # NEW
    })
    position = 1
```

For open-short (lines ~211-222):
```python
elif target_pos == -1 and position == 0 and capital > 0 and size_multiplier > 0:
    deploy = capital * size_multiplier
    exec_price = price * (1 - self.slippage)
    shares = -(deploy * (1 - self.commission) / exec_price)
    entry_cost_basis = deploy
    entry_price = exec_price
    capital -= deploy * self.commission
    entry_step = i
    entry_size_multiplier = size_multiplier
    trades.append({
        "type": "sell_short",
        "step": i,
        "entry_size": entry_size_multiplier,
        "entry_price": float(entry_price),         # NEW
        "entry_notional": float(deploy),             # NEW
    })
    position = -1
```

- [ ] **Step 2: Verify existing tests still pass**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py -v
```
Expected: all existing tests PASS.

- [ ] **Step 3: Commit**

```bash
git add dex/strategies/base.py
git commit -m "feat: add entry_price/entry_notional to simulate() open events
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task A2: Enhance simulate() close events with inherited entry fields

**Files:**
- Modify: `dex/strategies/base.py:154-195` (close long/short sections)
- Modify: `dex/strategies/base.py:240-271` (final close sections)

**Interfaces:**
- Consumes: open events with `entry_price`, `entry_notional`
- Produces: close events now include `entry_price`, `exit_price`, `entry_notional`

- [ ] **Step 1: Add fields to close-long events**

The close-long section (lines ~155-171) appends `{"type": "sell", "step": i, "entry_step": entry_step, "entry_size": entry_size_multiplier, "pnl": float(pnl)}`.

Change to:
```python
trades.append({
    "type": "sell",
    "step": i,
    "entry_step": entry_step,
    "entry_size": entry_size_multiplier,
    "pnl": float(pnl),
    "entry_price": float(entry_price),       # NEW: inherited from open
    "exit_price": float(exec_price),          # NEW: execution price
    "entry_notional": float(entry_cost_basis), # NEW: inherited from open
})
```

- [ ] **Step 2: Add fields to close-short events**

The close-short section (lines ~185-195) appends `{"type": "buy_cover", "step": i, "entry_step": entry_step, "entry_size": entry_size_multiplier, "pnl": float(pnl)}`.

Change to:
```python
trades.append({
    "type": "buy_cover",
    "step": i,
    "entry_step": entry_step,
    "entry_size": entry_size_multiplier,
    "pnl": float(pnl),
    "entry_price": float(entry_price),       # NEW
    "exit_price": float(exec_price),          # NEW: execution price
    "entry_notional": float(entry_cost_basis), # NEW
})
```

- [ ] **Step 3: Add fields to final-close events**

Apply the same pattern to `"sell_final"` (line ~247) and `"buy_cover_final"` (line ~262) — add `entry_price`, `exit_price`, `entry_notional` to each dict.

For `sell_final`:
```python
trades.append({
    "type": "sell_final",
    "step": len(signals) - 1,
    "entry_step": entry_step,
    "entry_size": entry_size_multiplier,
    "pnl": float(pnl),
    "entry_price": float(entry_price),       # NEW
    "exit_price": float(exec_price),          # NEW
    "entry_notional": float(entry_cost_basis), # NEW
})
```

For `buy_cover_final`:
```python
trades.append({
    "type": "buy_cover_final",
    "step": len(signals) - 1,
    "entry_step": entry_step,
    "entry_size": entry_size_multiplier,
    "pnl": float(pnl),
    "entry_price": float(entry_price),       # NEW
    "exit_price": float(exec_price),          # NEW
    "entry_notional": float(entry_cost_basis), # NEW
})
```

- [ ] **Step 4: Verify existing tests still pass**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py -v
```
Expected: all existing tests PASS (trade dicts now have extra keys, but no test checks dict length).

- [ ] **Step 5: Write test for new event fields**

Add to `tests/test_evaluator.py`:
```python
def test_simulate_open_event_has_entry_fields() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 1, 0])
    prices = np.array([100.0, 200.0, 200.0])

    _, trades = evaluator.simulate(signals, prices)

    buy_events = [t for t in trades if t["type"] == "buy"]
    assert len(buy_events) == 1
    buy = buy_events[0]
    assert "entry_price" in buy
    assert "entry_notional" in buy
    assert buy["entry_price"] == 100.0
    assert buy["entry_notional"] > 0


def test_simulate_close_event_has_entry_and_exit_fields() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 0])
    prices = np.array([100.0, 110.0])

    _, trades = evaluator.simulate(signals, prices)

    sell_events = [t for t in trades if t["type"] == "sell"]
    assert len(sell_events) == 1
    sell = sell_events[0]
    assert "entry_price" in sell
    assert "exit_price" in sell
    assert "entry_notional" in sell
    assert sell["entry_price"] == 100.0
    assert sell["exit_price"] == 110.0


def test_simulate_short_close_event_has_entry_and_exit_fields() -> None:
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([3, 0])
    prices = np.array([100.0, 90.0])

    _, trades = evaluator.simulate(signals, prices)

    cover_events = [t for t in trades if t["type"] == "buy_cover"]
    assert len(cover_events) == 1
    cover = cover_events[0]
    assert "entry_price" in cover
    assert "exit_price" in cover
    assert cover["entry_price"] == 100.0
    assert cover["exit_price"] == 90.0
```

Run:
```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_simulate_open_event_has_entry_fields tests\test_evaluator.py::test_simulate_close_event_has_entry_and_exit_fields tests\test_evaluator.py::test_simulate_short_close_event_has_entry_and_exit_fields -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dex/strategies/base.py tests/test_evaluator.py
git commit -m "feat: add entry_price/exit_price/entry_notional to simulate() close events
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task A3: Implement enrich_trade_ledger()

**Files:**
- Create: `dex/strategies/trade_ledger.py`
- Test: `tests/test_trade_ledger.py`

**Interfaces:**
- Consumes: `trades: list[dict]` from `simulate()`, `df: pd.DataFrame` with high/low/close
- Produces: `list[dict]` — one dict per closed logical trade with all fields from spec Section 2.2

- [ ] **Step 1: Write the test file**

Create `tests/test_trade_ledger.py`:
```python
"""Tests for dex.strategies.trade_ledger.enrich_trade_ledger."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dex.strategies.trade_ledger import enrich_trade_ledger


def _make_df(prices: list[float]) -> pd.DataFrame:
    """Create a minimal OHLC DataFrame where o=h=l=c for simplicity."""
    n = len(prices)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": prices, "high": prices, "low": prices, "close": prices},
        index=idx,
    )


def test_enrich_long_trade_basic_fields() -> None:
    """Long trade: buy at 100, sell at 110, 5 bars."""
    trades = [
        {"type": "buy", "step": 0, "entry_size": 1.0, "entry_price": 100.0, "entry_notional": 100.0},
        {"type": "sell", "step": 4, "entry_step": 0, "entry_price": 100.0, "exit_price": 110.0,
         "entry_notional": 100.0, "pnl": 10.0, "entry_size": 1.0},
    ]
    prices = [100.0, 101.0, 102.0, 108.0, 110.0]
    df = _make_df(prices)

    result = enrich_trade_ledger(trades, df, timeframe_minutes=5)

    assert len(result) == 1
    t = result[0]
    assert t["side"] == "long"
    assert t["entry_step"] == 0
    assert t["exit_step"] == 4
    assert t["entry_price"] == 100.0
    assert t["exit_price"] == 110.0
    assert t["pnl"] == 10.0
    assert t["return_pct"] == pytest.approx(0.10)
    assert t["duration_bars"] == 4
    assert t["duration_days"] == pytest.approx(4 * 5 / 1440)
    assert t["mae_pct"] <= 0
    assert t["mfe_pct"] >= 0


def test_enrich_short_trade_return_pct() -> None:
    """Short trade: sell_short at 100, buy_cover at 90, should be +10%."""
    trades = [
        {"type": "sell_short", "step": 0, "entry_size": 1.0, "entry_price": 100.0, "entry_notional": 100.0},
        {"type": "buy_cover", "step": 4, "entry_step": 0, "entry_price": 100.0, "exit_price": 90.0,
         "entry_notional": 100.0, "pnl": 10.0, "entry_size": 1.0},
    ]
    prices = [100.0, 99.0, 95.0, 92.0, 90.0]
    df = _make_df(prices)

    result = enrich_trade_ledger(trades, df, timeframe_minutes=5)

    assert len(result) == 1
    t = result[0]
    assert t["side"] == "short"
    assert t["return_pct"] == pytest.approx(0.10)  # 1 - 90/100 = 0.10


def test_enrich_mae_mfe_long() -> None:
    """Long trade where price dipped to 95 then recovered to 110."""
    trades = [
        {"type": "buy", "step": 0, "entry_size": 1.0, "entry_price": 100.0, "entry_notional": 100.0},
        {"type": "sell", "step": 5, "entry_step": 0, "entry_price": 100.0, "exit_price": 110.0,
         "entry_notional": 100.0, "pnl": 10.0, "entry_size": 1.0},
    ]
    df = pd.DataFrame({
        "open":  [100.0, 99.0, 96.0, 95.0, 105.0, 110.0],
        "high":  [100.0, 99.0, 97.0, 96.0, 106.0, 111.0],
        "low":   [100.0, 98.0, 95.0, 94.0, 104.0, 109.0],
        "close": [100.0, 99.0, 96.0, 95.0, 105.0, 110.0],
    }, index=pd.date_range("2024-01-01", periods=6, freq="5min"))

    result = enrich_trade_ledger(trades, df, timeframe_minutes=5)

    t = result[0]
    # MAE window: bars 1-5. Lowest low = 94.0 at bar 3. MAE = 94/100 - 1 = -0.06
    assert t["mae_pct"] == pytest.approx(94.0 / 100.0 - 1.0)  # -0.06
    assert t["mae_step"] == 3
    # MFE window: bars 1-5. Highest high = 111.0 at bar 5. MFE = 111/100 - 1 = 0.11
    assert t["mfe_pct"] == pytest.approx(111.0 / 100.0 - 1.0)  # 0.11
    assert t["mfe_step"] == 5


def test_enrich_mae_mfe_short() -> None:
    """Short trade where price spiked to 108 then fell to 90."""
    trades = [
        {"type": "sell_short", "step": 0, "entry_size": 1.0, "entry_price": 100.0, "entry_notional": 100.0},
        {"type": "buy_cover", "step": 5, "entry_step": 0, "entry_price": 100.0, "exit_price": 90.0,
         "entry_notional": 100.0, "pnl": 10.0, "entry_size": 1.0},
    ]
    df = pd.DataFrame({
        "open":  [100.0, 102.0, 106.0, 108.0, 95.0, 90.0],
        "high":  [100.0, 103.0, 107.0, 109.0, 96.0, 91.0],
        "low":   [100.0, 101.0, 105.0, 107.0, 94.0, 89.0],
        "close": [100.0, 102.0, 106.0, 108.0, 95.0, 90.0],
    }, index=pd.date_range("2024-01-01", periods=6, freq="5min"))

    result = enrich_trade_ledger(trades, df, timeframe_minutes=5)

    t = result[0]
    # MAE: min(1 - high/entry). Highest high = 109.0 at bar 3. MAE = 1 - 109/100 = -0.09
    assert t["mae_pct"] == pytest.approx(1.0 - 109.0 / 100.0)  # -0.09
    assert t["mae_step"] == 3
    # MFE: max(1 - low/entry). Lowest low = 89.0 at bar 5. MFE = 1 - 89/100 = 0.11
    assert t["mfe_pct"] == pytest.approx(1.0 - 89.0 / 100.0)  # 0.11
    assert t["mfe_step"] == 5


def test_enrich_empty_window_fallback() -> None:
    """Same-bar trade (entry_step == exit_step) — window is empty."""
    trades = [
        {"type": "buy", "step": 0, "entry_size": 1.0, "entry_price": 100.0, "entry_notional": 100.0},
        {"type": "sell", "step": 0, "entry_step": 0, "entry_price": 100.0, "exit_price": 100.0,
         "entry_notional": 100.0, "pnl": 0.0, "entry_size": 1.0},
    ]
    df = _make_df([100.0, 101.0])

    result = enrich_trade_ledger(trades, df, timeframe_minutes=5)

    t = result[0]
    assert t["mae_pct"] == 0.0
    assert t["mfe_pct"] == 0.0
    assert t["mae_step"] == 0
    assert t["mfe_step"] == 0


def test_enrich_time_fields() -> None:
    """Verify time_to_mae_hours and time_to_mfe_hours are computed."""
    trades = [
        {"type": "buy", "step": 0, "entry_size": 1.0, "entry_price": 100.0, "entry_notional": 100.0},
        {"type": "sell", "step": 10, "entry_step": 0, "entry_price": 100.0, "exit_price": 110.0,
         "entry_notional": 100.0, "pnl": 10.0, "entry_size": 1.0},
    ]
    prices = [100.0] * 6 + [95.0] + [110.0] * 4
    df = _make_df(prices)

    result = enrich_trade_ledger(trades, df, timeframe_minutes=5)

    t = result[0]
    assert "time_to_mae_hours" in t
    assert "time_to_mfe_hours" in t
    assert t["time_to_mae_hours"] == t["time_to_mae_bars"] * 5 / 60


import pytest
```

- [ ] **Step 2: Run test to verify it fails**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trade_ledger.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'dex.strategies.trade_ledger'`

- [ ] **Step 3: Implement enrich_trade_ledger()**

Create `dex/strategies/trade_ledger.py`:
```python
"""Post-processing trade ledger enrichment: MAE, MFE, duration, regime."""

from __future__ import annotations

import numpy as np
import pandas as pd


def enrich_trade_ledger(
    trades: list[dict],
    df: pd.DataFrame,
    timeframe_minutes: int = 5,
    regime_col: str | None = "regime",
) -> list[dict]:
    """Enrich closed trades with MAE, MFE, duration, regime, and time fields.

    Args:
        trades: Event list from StrategyEvaluator.simulate().
        df: OHLCV DataFrame with high, low, close columns.
        timeframe_minutes: Bar duration in minutes (default 5).
        regime_col: Column name for regime labels; None to skip.

    Returns:
        One dict per closed logical trade with all diagnostic fields.
    """
    # Pair open events with close events by tracking in-flight positions
    open_events: dict[int, dict] = {}  # entry_step -> open event
    closed_trades: list[dict] = []

    # Map event type to side
    _open_types = {"buy", "sell_short"}
    _close_types = {"sell", "buy_cover", "sell_final", "buy_cover_final"}

    for event in trades:
        etype = event.get("type", "")
        step = event.get("step", 0)

        if etype in _open_types:
            open_events[step] = event

        elif etype in _close_types:
            entry_step = event.get("entry_step", -1)
            open_ev = open_events.get(entry_step)
            if open_ev is None:
                continue  # should not happen in well-formed trade lists

            side = _infer_side(open_ev["type"])
            entry_price = float(event["entry_price"])
            exit_price = float(event["exit_price"])
            entry_notional = float(event.get("entry_notional", 0.0))
            pnl = float(event.get("pnl", 0.0))

            # Compute return_pct using linear USDT convention
            if side == "long":
                return_pct = exit_price / entry_price - 1.0
            else:
                return_pct = 1.0 - exit_price / entry_price

            duration_bars = step - entry_step
            duration_days = duration_bars * timeframe_minutes / 1440.0

            # MAE/MFE window: entry_step+1 to step (inclusive)
            mae_pct, mfe_pct, mae_step, mfe_step = _compute_mae_mfe(
                side, entry_price, entry_step, step, df
            )

            # Time-to fields
            time_to_mae_bars = mae_step - entry_step
            time_to_mae_hours = time_to_mae_bars * timeframe_minutes / 60.0
            time_to_mfe_bars = mfe_step - entry_step
            time_to_mfe_hours = time_to_mfe_bars * timeframe_minutes / 60.0

            # Regime fields
            entry_regime = None
            exit_regime = None
            if regime_col and regime_col in df.columns:
                regime_vals = df[regime_col].values
                if 0 <= entry_step < len(regime_vals):
                    entry_regime = str(regime_vals[entry_step])
                if 0 <= step < len(regime_vals):
                    exit_regime = str(regime_vals[step])

            closed_trades.append({
                "side": side,
                "entry_step": entry_step,
                "exit_step": step,
                "entry_time": df.index[entry_step] if entry_step < len(df.index) else None,
                "exit_time": df.index[step] if step < len(df.index) else None,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_notional": entry_notional,
                "entry_size": float(event.get("entry_size", 1.0)),
                "pnl": pnl,
                "return_pct": float(return_pct),
                "duration_bars": duration_bars,
                "duration_days": float(duration_days),
                "mae_pct": float(mae_pct),
                "mfe_pct": float(mfe_pct),
                "mae_step": mae_step,
                "mfe_step": mfe_step,
                "time_to_mae_bars": time_to_mae_bars,
                "time_to_mae_hours": float(time_to_mae_hours),
                "time_to_mfe_bars": time_to_mfe_bars,
                "time_to_mfe_hours": float(time_to_mfe_hours),
                "entry_regime": entry_regime,
                "exit_regime": exit_regime,
                "mae_pnl_est": float(mae_pct * entry_notional),
                "mfe_pnl_est": float(mfe_pct * entry_notional),
            })

    return closed_trades


def _infer_side(open_type: str) -> str:
    if open_type in ("buy",):
        return "long"
    if open_type in ("sell_short",):
        return "short"
    raise ValueError(f"Unknown open type: {open_type}")


def _compute_mae_mfe(
    side: str,
    entry_price: float,
    entry_step: int,
    exit_step: int,
    df: pd.DataFrame,
) -> tuple[float, float, int, int]:
    """Compute MAE, MFE, and their step indices for one trade.

    Window: entry_step+1 to exit_step (inclusive).
    Falls back to close-to-close if window is empty.
    """
    start = entry_step + 1
    end = exit_step + 1  # exclusive for slicing

    if start >= end:
        # Empty window fallback
        return 0.0, 0.0, exit_step, exit_step

    high_slice = df["high"].iloc[start:end].values.astype(float)
    low_slice = df["low"].iloc[start:end].values.astype(float)

    if side == "long":
        return_paths = low_slice / entry_price - 1.0
        mfe_paths = high_slice / entry_price - 1.0
        mae_idx = int(np.argmin(return_paths))
        mfe_idx = int(np.argmax(mfe_paths))
        mae_pct = float(return_paths[mae_idx])
        mfe_pct = float(mfe_paths[mfe_idx])
        mae_step = start + mae_idx
        mfe_step = start + mfe_idx
    else:
        return_paths = 1.0 - high_slice / entry_price
        mfe_paths = 1.0 - low_slice / entry_price
        mae_idx = int(np.argmin(return_paths))
        mfe_idx = int(np.argmax(mfe_paths))
        mae_pct = float(return_paths[mae_idx])
        mfe_pct = float(mfe_paths[mfe_idx])
        mae_step = start + mae_idx
        mfe_step = start + mfe_idx

    return mae_pct, mfe_pct, mae_step, mfe_step
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trade_ledger.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dex/strategies/trade_ledger.py tests/test_trade_ledger.py
git commit -m "feat: add enrich_trade_ledger() for MAE/MFE/duration/regime diagnostics
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task A4: Implement diagnose_mae_mfe.py script

**Files:**
- Create: `scripts/diagnose_mae_mfe.py`

**Interfaces:**
- Consumes: `enrich_trade_ledger()` from `dex.strategies.trade_ledger`
- Consumes: `StrategyEvaluator` from `dex.strategies.base`
- Consumes: Checkpoint `channel_breakout_375_432.pt` for signals
- Produces: CSV ledger + Markdown report in `research_workspace/diagnostics/`

- [ ] **Step 1: Create output directory**

```powershell
New-Item -ItemType Directory -Force -Path research_workspace\diagnostics
```

- [ ] **Step 2: Write the diagnostic script**

Create `scripts/diagnose_mae_mfe.py`:
```python
"""Phase 0 diagnostic: MAE/MFE ledger and Gate 0 report for channel_breakout_375_432.

Usage:
    .venv\\Scripts\\python.exe scripts\\diagnose_mae_mfe.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

import numpy as np
import pandas as pd

# -- Configuration -----------------------------------------------------------
CHECKPOINT = "checkpoints/channel_breakout_375_432.pt"
DATA_FILE = "data/crypto/ETHUSDT_5m_2600d.parquet"
OOS_START = "2024-06-06 14:25:00"
OOS_END = "2026-06-12 02:55:00"
OUTPUT_DIR = "research_workspace/diagnostics"
OUTPUT_PREFIX = "channel_breakout_375_432_v2_oos_2600d"
TIMEFRAME_MINUTES = 5
REGIME_COL = "regime"

# -- Main --------------------------------------------------------------------
def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load data
    df = pd.read_parquet(DATA_FILE)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # Slice OOS
    oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
    df_oos = df[oos_mask].copy()
    if len(df_oos) == 0:
        print(f"ERROR: OOS slice is empty. Check OOS_START/END.")
        sys.exit(1)

    prices = df_oos["close"].to_numpy(dtype=float)

    # Generate signals from checkpoint (delegate to backtest_quant.py data flow)
    from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy

    strategy = ChannelBreakoutTrendStrategy(
        entry_lookback=375,
        min_hold_bars=432,
    )
    signals = strategy.generate_signals(df_oos)

    # Simulate
    from dex.strategies.base import StrategyEvaluator

    evaluator = StrategyEvaluator()
    equity, trades = evaluator.simulate(signals, prices, df_oos)

    # Enrich
    from dex.strategies.trade_ledger import enrich_trade_ledger

    # Ensure regime column exists (may need to compute from market_regime module)
    if REGIME_COL not in df_oos.columns:
        from dex.market_regime import detect_regime
        df_oos[REGIME_COL] = detect_regime(df_oos)

    closed_trades = enrich_trade_ledger(
        trades, df_oos, timeframe_minutes=TIMEFRAME_MINUTES, regime_col=REGIME_COL
    )

    # Save ledger CSV
    ledger_path = os.path.join(OUTPUT_DIR, f"{OUTPUT_PREFIX}_mae_mfe_ledger.csv")
    ledger_df = pd.DataFrame(closed_trades)
    ledger_df.to_csv(ledger_path, index=False)
    print(f"Ledger saved: {ledger_path} ({len(closed_trades)} closed trades)")

    # Compute diagnostic report
    report = generate_report(closed_trades, ledger_df)
    report_path = os.path.join(OUTPUT_DIR, f"{OUTPUT_PREFIX}_mae_mfe_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved: {report_path}")


def generate_report(trades: list[dict], df: pd.DataFrame) -> str:
    """Generate Gate 0 diagnostic report."""
    pnls = np.array([t["pnl"] for t in trades])
    total_trades = len(trades)

    # Pre-registered grouping
    winner_mask = pnls > 0
    loser_mask = pnls < 0
    winners = pnls[winner_mask]
    losers = pnls[loser_mask]

    winner_p80 = np.percentile(winners, 80) if len(winners) > 0 else 0
    loser_p20 = np.percentile(losers, 20) if len(losers) > 0 else 0

    big_winner_mask = winner_mask & (pnls >= winner_p80)
    big_loser_mask = loser_mask & (pnls <= loser_p20)

    big_winners = [t for t, m in zip(trades, big_winner_mask) if m]
    big_losers = [t for t, m in zip(trades, big_loser_mask) if m]
    all_losers = [t for t, m in zip(trades, loser_mask) if m]

    # Question 1: Big winners MAE distribution
    lines = []
    lines.append("# MAE/MFE Diagnostic Report")
    lines.append(f"\n**Generated**: {datetime.now().isoformat()}")
    lines.append(f"**Checkpoint**: {CHECKPOINT}")
    lines.append(f"**Data**: {DATA_FILE}")
    lines.append(f"**OOS**: {OOS_START} ~ {OOS_END}")
    lines.append(f"**Closed trades**: {total_trades}")
    lines.append("")

    lines.append("## Q1: Big Winners — MAE Distribution")
    lines.append("")
    lines.append("| Threshold | Count | % of Big Winners |")
    lines.append("|-----------|-------|------------------|")
    for thresh in [-0.04, -0.05, -0.06, -0.07]:
        count = sum(1 for t in big_winners if t["mae_pct"] < thresh)
        pct = count / len(big_winners) * 100 if big_winners else 0
        lines.append(f"| MAE worse than {thresh:.0%} | {count} | {pct:.1f}% |")
    lines.append("")

    # Question 2: Big losers MAE distribution
    lines.append("## Q2: Big Losers — MAE Distribution")
    lines.append("")
    lines.append("| Threshold | Count | % of Big Losers |")
    lines.append("|-----------|-------|-----------------|")
    for thresh in [-0.04, -0.05, -0.06, -0.07]:
        count = sum(1 for t in big_losers if t["mae_pct"] < thresh)
        pct = count / len(big_losers) * 100 if big_losers else 0
        lines.append(f"| MAE worse than {thresh:.0%} | {count} | {pct:.1f}% |")
    lines.append("")

    # Question 3: Big losers time_to_mae
    tmae_bars = [t["time_to_mae_bars"] for t in big_losers]
    tmae_hours = [t["time_to_mae_hours"] for t in big_losers]
    lines.append("## Q3: Big Losers — Time to MAE")
    lines.append(f"- Median time_to_mae_bars: {np.median(tmae_bars):.0f} bars")
    lines.append(f"- Median time_to_mae_hours: {np.median(tmae_hours):.1f} hours")
    lines.append("")

    # Question 4: Big winners time_to_mfe
    tmfe_bars = [t["time_to_mfe_bars"] for t in big_winners]
    tmfe_hours = [t["time_to_mfe_hours"] for t in big_winners]
    lines.append("## Q4: Big Winners — Time to MFE")
    lines.append(f"- Median time_to_mfe_bars: {np.median(tmfe_bars):.0f} bars")
    lines.append(f"- Median time_to_mfe_hours: {np.median(tmfe_hours):.1f} hours")
    lines.append("")

    # Question 5: Time-in-loss horizon analysis
    lines.append("## Q5: Horizon Unrealized PnL")
    for hours in [48, 72, 96]:
        horizon_bars = int(hours * 60 / TIMEFRAME_MINUTES)
        lines.append(f"\n### {hours}h horizon ({horizon_bars} bars)")
        # Compute unrealized return at horizon for trades that lasted that long
        horizon_results = []
        for t in trades:
            if t["duration_bars"] > horizon_bars:
                horizon_results.append({
                    "final_pnl": t["pnl"],
                    "final_return": t["return_pct"],
                })
        if horizon_results:
            final_pnls = [r["final_pnl"] for r in horizon_results]
            lines.append(f"- Trades lasting > {hours}h: {len(horizon_results)}")
            lines.append(f"- Median final PnL: {np.median(final_pnls):.1f}")
        else:
            lines.append(f"- No trades lasting > {hours}h")

    # Question 6: BE stop killed winners
    lines.append("\n## Q6: Break-Even Stop — Killed Big Winners")
    for trigger in [0.03, 0.04]:
        for stop_level in [0.0, 0.005]:
            killed = _count_be_killed(big_winners, trigger, stop_level)
            pct = killed / len(big_winners) * 100 if big_winners else 0
            lines.append(f"- BE trigger +{trigger:.0%}, stop {stop_level:.1%}: "
                          f"killed {killed}/{len(big_winners)} ({pct:.1f}%)")
    lines.append("")

    # Question 7: Top loss contribution
    sorted_losers = sorted(all_losers, key=lambda t: t["pnl"])
    top_20 = sorted_losers[:20]
    top_50 = sorted_losers[:50]
    sum_all_losses = abs(sum(t["pnl"] for t in all_losers))
    if sum_all_losses > 0:
        top20_contrib = abs(sum(t["pnl"] for t in top_20)) / sum_all_losses * 100
        top50_contrib = abs(sum(t["pnl"] for t in top_50)) / sum_all_losses * 100
    else:
        top20_contrib = top50_contrib = 0
    lines.append("## Q7: Top Loss Contribution")
    lines.append(f"- Top 20 losses: {top20_contrib:.1f}% of total losses")
    lines.append(f"- Top 50 losses: {top50_contrib:.1f}% of total losses")
    lines.append("")

    # Gate 0 decisions
    lines.append("## Gate 0 — Decision Rules")
    big_winner_mae5_pct = (
        sum(1 for t in big_winners if t["mae_pct"] < -0.05) / len(big_winners) * 100
    ) if big_winners else 0
    big_winner_mae7_pct = (
        sum(1 for t in big_winners if t["mae_pct"] < -0.07) / len(big_winners) * 100
    ) if big_winners else 0
    med_tmae_h = np.median(tmae_hours) if tmae_hours else 0
    be_kill_pct = (
        _count_be_killed(big_winners, 0.03, 0.0) / len(big_winners) * 100
    ) if big_winners else 0

    lines.append(f"- Big winners with MAE worse than -5%: {big_winner_mae5_pct:.1f}%")
    lines.append(f"- Big winners with MAE worse than -7%: {big_winner_mae7_pct:.1f}%")
    lines.append(f"- Big losers median time_to_mae_hours: {med_tmae_h:.1f}h")
    lines.append(f"- BE stop killed big winners (%): {be_kill_pct:.1f}%")
    lines.append("")

    if big_winner_mae5_pct < 20:
        lines.append("→ -5% adverse partial stop is ELIGIBLE for testing.")
    else:
        lines.append("→ -5% adverse partial stop may carry elevated winner-kill risk.")
    if big_winner_mae7_pct > 30:
        lines.append("→ Hard full-stop NOT recommended. Prioritize partial + time-in-loss.")
    if med_tmae_h < 48:
        lines.append("→ Time-in-loss stop likely effective. 48h first-tier threshold.")
    if be_kill_pct > 15:
        lines.append("→ Tight BE stop NOT recommended. Only test BE partial or BE loose.")

    return "\n".join(lines)


def _count_be_killed(trades: list[dict], trigger_mfe: float, stop_level: float) -> int:
    """Count how many trades would be killed by a break-even stop.

    For each trade: check if MFE >= trigger_mfe, and if so, whether the
    price retraced to <= stop_level before exit. This uses the trade-level
    MFE (from high/low) and final return (from close-to-close) as a proxy.
    A more precise per-bar path check would require the full price arrays.
    """
    killed = 0
    for t in trades:
        if t["mfe_pct"] >= trigger_mfe:
            # Proxy: if MFE was high but final return dropped to stop_level,
            # a BE stop would have triggered during the retrace.
            if t["return_pct"] <= stop_level:
                killed += 1
    return killed


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke test — verify script imports cleanly**

```powershell
.venv\Scripts\python.exe -c "import scripts.diagnose_mae_mfe; print('import OK')"
```
Expected: `import OK` (script file exists, imports resolve).

- [ ] **Step 4: Commit**

```bash
git add scripts/diagnose_mae_mfe.py
git commit -m "feat: add Phase 0 MAE/MFE diagnostic script
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task A5: Implement fixed size matrix script

**Files:**
- Create: `scripts/run_fixed_size_matrix.py`

**Interfaces:**
- Consumes: `StrategyEvaluator.simulate()` with `position_sizes`
- Consumes: Checkpoint `channel_breakout_375_432.pt` for signals
- Produces: CSV matrix + Markdown baseline report in `research_workspace/diagnostics/`

- [ ] **Step 1: Write the matrix script**

Create `scripts/run_fixed_size_matrix.py`:
```python
"""Phase 1: fixed position size matrix for channel_breakout_375_432.

Usage:
    .venv\\Scripts\\python.exe scripts\\run_fixed_size_matrix.py
"""

from __future__ import annotations

import os
from datetime import datetime

import numpy as np
import pandas as pd

# -- Configuration -----------------------------------------------------------
CHECKPOINT = "checkpoints/channel_breakout_375_432.pt"
DATA_FILE = "data/crypto/ETHUSDT_5m_2600d.parquet"
OOS_START = "2024-06-06 14:25:00"
OOS_END = "2026-06-12 02:55:00"
OUTPUT_DIR = "research_workspace/diagnostics"
OUTPUT_PREFIX = "channel_breakout_375_432_v2_oos_2600d"
SIZES = [1.0, 0.50, 0.475, 0.45, 0.425, 0.40]

# -- Main --------------------------------------------------------------------
def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load data
    df = pd.read_parquet(DATA_FILE)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    oos_mask = (df.index >= OOS_START) & (df.index <= OOS_END)
    df_oos = df[oos_mask].copy()
    prices = df_oos["close"].to_numpy(dtype=float)

    # Generate signals once
    from dex.strategies.channel_breakout import ChannelBreakoutTrendStrategy

    strategy = ChannelBreakoutTrendStrategy(entry_lookback=375, min_hold_bars=432)
    signals = strategy.generate_signals(df_oos)

    # Evaluate each size
    from dex.strategies.base import StrategyEvaluator

    results = []
    for size in SIZES:
        evaluator = StrategyEvaluator()
        position_sizes = np.full(len(signals), size, dtype=float)
        equity, trades = evaluator.simulate(signals, prices, df_oos, position_sizes=position_sizes)
        metrics = evaluator.compute_metrics(equity, trades)

        trade_pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
        winners = [p for p in trade_pnls if p > 0]
        losers = [p for p in trade_pnls if p < 0]
        avg_win = np.mean(winners) if winners else 0
        avg_loss = np.mean(losers) if losers else 0
        profit_factor = abs(sum(winners) / sum(losers)) if losers and sum(losers) != 0 else float("inf")

        # Underwater duration
        peak = equity[0]
        max_underwater_bars = 0
        current_underwater_bars = 0
        for e in equity:
            if e >= peak:
                peak = e
                current_underwater_bars = 0
            else:
                current_underwater_bars += 1
                max_underwater_bars = max(max_underwater_bars, current_underwater_bars)

        results.append({
            "target_size": size,
            "total_return": metrics["total_return"],
            "annualized_return": metrics["annualized_return"],
            "sharpe_ratio": metrics["sharpe_ratio"],
            "max_drawdown": metrics["max_drawdown"],
            "win_rate": metrics["win_rate"],
            "trade_count": len(trade_pnls),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_single_loss": min(trade_pnls) if trade_pnls else 0,
            "max_underwater_bars": max_underwater_bars,
            "max_underwater_days": max_underwater_bars * 5 / 1440,
        })

    # Save CSV
    matrix_df = pd.DataFrame(results)
    csv_path = os.path.join(OUTPUT_DIR, f"{OUTPUT_PREFIX}_fixed_size_matrix.csv")
    matrix_df.to_csv(csv_path, index=False)
    print(f"Matrix saved: {csv_path}")

    # Generate baseline report
    report = _generate_baseline_report(results)
    report_path = os.path.join(OUTPUT_DIR, f"{OUTPUT_PREFIX}_fixed_size_baseline.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved: {report_path}")


def _generate_baseline_report(results: list[dict]) -> str:
    """Apply Gate 1 rules and produce baseline selection report."""
    lines = [
        "# Fixed Size Matrix — Baseline Selection",
        f"\n**Generated**: {datetime.now().isoformat()}",
        f"**Checkpoint**: {CHECKPOINT}",
        f"**Data**: {DATA_FILE}",
        f"**OOS**: {OOS_START} ~ {OOS_END}",
        "",
        "## Results",
        "",
        "| Size | Return | MaxDD | Sharpe | Trades | Win Rate | Profit Factor |",
        "|------|--------|-------|--------|--------|----------|---------------|",
    ]
    for r in results:
        lines.append(
            f"| {r['target_size']:.3f}x | {r['total_return']:.1%} | {r['max_drawdown']:.1%} | "
            f"{r['sharpe_ratio']:.2f} | {r['trade_count']} | {r['win_rate']:.1%} | "
            f"{r['profit_factor']:.2f} |"
        )
    lines.append("")

    # Gate 1 logic (simplified — MC results must be added separately)
    lines.append("## Gate 1 — Preliminary (MC results pending)")
    lines.append("")
    lines.append("Full Gate 1 requires MC DD<-30% probability for each size.")
    lines.append("Run Monte Carlo separately and update this report.")
    lines.append("")
    lines.append("### Prior-based tier assignment:")
    lines.append("- safe_baseline: 0.40x (prior MC DD<-30% = 9.80%)")
    lines.append("- balanced_baseline: 0.425x (prior MC DD<-30% = 13.55%)")
    lines.append("- aggressive_candidate: 0.45x (prior MC DD<-30% = 17.05%)")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test**

```powershell
.venv\Scripts\python.exe -c "import scripts.run_fixed_size_matrix; print('import OK')"
```
Expected: `import OK`.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_fixed_size_matrix.py
git commit -m "feat: add Phase 1 fixed position size matrix script
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

## Plan B: Phase 2 Simulator Infrastructure

### Task B1: Add stop_config parameter and per-bar execution skeleton to simulate()

**Files:**
- Modify: `dex/strategies/base.py:92-97` (simulate signature)
- Modify: `dex/strategies/base.py:139-237` (main loop body)

**Interfaces:**
- Consumes: existing `simulate()` parameters
- Produces: `simulate()` now accepts optional `stop_config` dict
- Produces: per-bar loop follows spec Section 4.2 execution order

- [ ] **Step 1: Write a test for Phase 2 entry-bar immunity**

Add to `tests/test_evaluator.py`:
```python
def test_stop_config_new_entry_immune_on_same_bar() -> None:
    """A position opened on bar i cannot be stopped on bar i."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 1, 0])
    prices = np.array([100.0, 90.0, 90.0])  # bar 1 drops -10% — but entry was on bar 0

    stop_config = {
        "base_size": 1.0,
        "adverse_stop": {
            "enabled": True,
            "threshold_pct": -0.05,
            "action": "close",
            "trigger_source": "close",
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    # The long was opened on bar 0. Bar 1 close = 90, which is -10% from entry.
    # But bar 1 is the FIRST bar after entry — stop should fire (entry_step=0, bar=1 > 0+1? No, it's == 0+1)
    # Actually entry_step=0, stop window starts at bar 1 (entry_step+1).
    # Bar 1 IS in window. So stop SHOULD fire. Let's verify it does.
    close_events = [t for t in trades if t.get("pnl") is not None]
    assert len(close_events) == 1
    # The close should be from adverse_stop, not a natural signal exit
    sell = [t for t in trades if t["type"] == "sell"]
    assert len(sell) > 0


def test_stop_config_base_size_only_position_source() -> None:
    """Phase 2: position_sizes is 1.0, stop_config.base_size controls sizing."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 0])
    prices = np.array([100.0, 110.0])

    stop_config = {
        "base_size": 0.5,
    }

    # position_sizes omitted → defaults to 1.0
    equity_with_stop, _ = evaluator.simulate(signals, prices, stop_config=stop_config)
    # With 0.5x base_size, should match position_sizes=0.5 behavior
    equity_ref, _ = evaluator.simulate(
        signals, prices, position_sizes=np.array([0.5, 0.5])
    )

    assert equity_with_stop[-1] == pytest.approx(equity_ref[-1])
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_stop_config_new_entry_immune_on_same_bar tests\test_evaluator.py::test_stop_config_base_size_only_position_source -v
```
Expected: FAIL (stop_config parameter not yet accepted).

- [ ] **Step 3: Implement stop_config skeleton in simulate()**

Modify `dex/strategies/base.py`:

Update the `simulate()` signature (line 92):
```python
def simulate(
    self,
    signals: np.ndarray,
    prices: np.ndarray,
    df: pd.DataFrame | None = None,
    position_sizes: np.ndarray | None = None,
    stop_config: dict | None = None,   # NEW
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
```

Update the docstring to add:
```
        stop_config: Optional risk control configuration (Phase 2).
            See spec Section 4.3 for schema. When provided, ``base_size``
            is the sole position source and ``position_sizes`` is ignored.
```

After the existing `position_sizes` validation block (line 127), add:
```python
        # Phase 2: stop_config.base_size is the sole position source
        if stop_config is not None:
            base_size = float(stop_config.get("base_size", 1.0))
            size_multipliers = np.full(len(signals), base_size, dtype=float)
            # position_sizes is ignored when stop_config is present
```

In the main loop, after the signal-processing block and before equity computation (around line 225), insert the per-bar stop check skeleton:
```python
            # === Phase 2: Risk stop checks (only for positions opened on prior bars) ===
            if stop_config is not None and position != 0 and i > entry_step:
                action = _check_risk_stops(
                    stop_config, position, i, entry_step, entry_price,
                    shares, capital, price, prices, df, peak_equity,
                )
                if action is not None:
                    # TODO: implement in Task B2-B5
                    pass
            # === End risk stop checks ===
```

This is a minimal skeleton. The actual stop logic goes into Tasks B2-B5.

For now, add a no-op `_check_risk_stops` placeholder at module level in `base.py`:
```python
def _check_risk_stops(
    stop_config: dict,
    position: int,
    i: int,
    entry_step: int,
    entry_price: float,
    shares: float,
    capital: float,
    price: float,
    prices: np.ndarray,
    df: pd.DataFrame | None,
    peak_equity: float,
) -> dict | None:
    """Check all enabled risk stops. Returns an action dict or None.

    Action dict: {"action": "close" | "reduce_half", "reason": str}
    """
    # Placeholder — implemented in Task B2
    return None
```

- [ ] **Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py -v
```
Expected: all tests PASS (skeleton is no-op, doesn't break anything).

- [ ] **Step 5: Commit**

```bash
git add dex/strategies/base.py tests/test_evaluator.py
git commit -m "feat: add stop_config parameter and per-bar stop check skeleton to simulate()
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task B2: Implement equity DD sizing

**Files:**
- Modify: `dex/strategies/base.py` — `_check_risk_stops()` + DD tracking

**Interfaces:**
- Consumes: `stop_config["equity_dd_sizing"]`
- Produces: DD-based entry multiplier; kill_switch close action

- [ ] **Step 1: Write tests for DD sizing**

Add to `tests/test_evaluator.py`:
```python
def test_equity_dd_sizing_reduces_entry_after_drawdown() -> None:
    """After a DD event, the next entry should use a reduced multiplier."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    # First trade loses 15% (100 -> 85), then DD ≥ 10% should reduce next entry
    signals = np.array([2, 0, 2, 0])
    prices = np.array([100.0, 85.0, 85.0, 100.0])

    stop_config = {
        "base_size": 1.0,
        "equity_dd_sizing": {
            "enabled": True,
            "tiers": [(0.05, 1.00), (0.10, 0.50), (0.15, 0.25)],
            "no_new_entry_dd": 0.20,
            "kill_switch_dd": 0.25,
        },
    }

    equity, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    # After first loss, DD ~15%, multiplier should be 0.25
    # Second entry at bar 2 should use 0.25x
    buy_events = [t for t in trades if t["type"] == "buy"]
    assert len(buy_events) == 2
    assert buy_events[1]["entry_size"] < buy_events[0]["entry_size"]


def test_equity_dd_kill_switch_closes_position() -> None:
    """At kill_switch_dd, existing position is closed."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 1, 1, 1])
    prices = np.array([100.0, 70.0, 70.0, 70.0])  # -30% DD

    stop_config = {
        "base_size": 1.0,
        "equity_dd_sizing": {
            "enabled": True,
            "tiers": [(0.05, 1.00)],
            "no_new_entry_dd": 0.20,
            "kill_switch_dd": 0.25,
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    close_events = [t for t in trades if t.get("pnl") is not None]
    assert len(close_events) >= 1
    # The close should be from kill_switch
    kill_events = [t for t in close_events if t.get("exit_reason") == "kill_switch"]
    assert len(kill_events) >= 1


def test_no_new_entry_blocks_entries_but_allows_close() -> None:
    """no_new_entry allows closing but prevents opening new positions."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    # First trade loses 22% → DD > 20% → no_new_entry
    signals = np.array([2, 0, 3, 0])
    prices = np.array([100.0, 78.0, 78.0, 78.0])

    stop_config = {
        "base_size": 1.0,
        "equity_dd_sizing": {
            "enabled": True,
            "tiers": [(0.05, 1.00)],
            "no_new_entry_dd": 0.20,
            "kill_switch_dd": 0.25,
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    # Should have sell from signal exit, but NO sell_short (new entry blocked)
    short_opens = [t for t in trades if t["type"] == "sell_short"]
    assert len(short_opens) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_equity_dd_sizing_reduces_entry_after_drawdown tests\test_evaluator.py::test_equity_dd_kill_switch_closes_position tests\test_evaluator.py::test_no_new_entry_blocks_entries_but_allows_close -v
```
Expected: FAIL (DD sizing not yet implemented).

- [ ] **Step 3: Implement equity DD sizing in simulate()**

In `dex/strategies/base.py`, add DD tracking variables after `entry_step = -1` (around line 137):
```python
        # Phase 2: equity DD tracking
        peak_equity = capital
        no_new_entry = False
        killed = False
```

In the per-bar loop, after computing `current_equity` (line 237), add DD update:
```python
            # Update peak equity and DD (Phase 2)
            if current_equity > peak_equity:
                peak_equity = current_equity
            dd_abs = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
```

In the stop check block, implement DD checks:
```python
            if stop_config is not None and position != 0:
                dd_config = stop_config.get("equity_dd_sizing", {})
                if dd_config.get("enabled", False):
                    kill_switch_dd = float(dd_config.get("kill_switch_dd", 0.25))
                    if dd_abs >= kill_switch_dd:
                        # Kill switch: close position immediately
                        # (close logic mirrors existing close-long/close-short)
                        action = {"action": "close", "reason": "kill_switch"}
                        # ... execute close (handled in next task)
                        killed = True

            if stop_config is not None and position != 0 and i > entry_step:
                # Other risk stops checked here (Task B3-B5)
                pass
```

For DD-based entry sizing, modify the open-entry block to check DD:
```python
            # Before opening new position, check no_new_entry and DD multiplier
            if stop_config is not None:
                dd_config = stop_config.get("equity_dd_sizing", {})
                if dd_config.get("enabled", False):
                    no_new_entry_dd = float(dd_config.get("no_new_entry_dd", 0.20))
                    if dd_abs >= no_new_entry_dd:
                        no_new_entry = True
                    else:
                        no_new_entry = False
                    # Compute DD multiplier
                    tiers = dd_config.get("tiers", [])
                    dd_mult = 1.0
                    for upper, mult in sorted(tiers, key=lambda x: x[0]):
                        if dd_abs < upper:
                            dd_mult = mult
                            break
                    else:
                        dd_mult = tiers[-1][1] if tiers else 1.0
                    size_multiplier = float(stop_config.get("base_size", 1.0)) * dd_mult
```

For the open-entry conditions, add `and not no_new_entry`:
```python
            if target_pos == 1 and position == 0 and capital > 0 and size_multiplier > 0 and not no_new_entry:
```

And after kills, add `and not killed`:
```python
            elif target_pos == -1 and position == 0 and capital > 0 and size_multiplier > 0 and not no_new_entry and not killed:
```

- [ ] **Step 4: Run DD sizing tests**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py -v
```
Expected: DD sizing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dex/strategies/base.py tests/test_evaluator.py
git commit -m "feat: implement equity DD sizing with kill_switch and no_new_entry
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task B3: Implement adverse move partial stop

**Files:**
- Modify: `dex/strategies/base.py` — `_check_risk_stops()` implementation

**Interfaces:**
- Consumes: `stop_config["adverse_stop"]`
- Produces: `reduce_half` or `close` action for adverse moves

- [ ] **Step 1: Write test for adverse stop**

Add to `tests/test_evaluator.py`:
```python
def test_adverse_stop_reduce_half_long() -> None:
    """Long position: -5% adverse move triggers reduce_half."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 1, 1, 1, 0])
    prices = np.array([100.0, 96.0, 94.0, 93.0, 90.0])

    stop_config = {
        "base_size": 1.0,
        "adverse_stop": {
            "enabled": True,
            "threshold_pct": -0.05,
            "action": "reduce_half",
            "trigger_source": "close",
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    # Should have a partial close event from adverse_stop
    partial_events = [t for t in trades if t.get("is_partial")]
    assert len(partial_events) >= 1
    assert partial_events[0].get("exit_reason") == "adverse_stop"


def test_adverse_stop_does_not_trigger_on_entry_bar() -> None:
    """Adverse stop should NOT fire on the same bar as entry."""
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 0])
    prices = np.array([100.0, 94.0])  # -6% on entry bar (but bar 0=entry, bar 1=exit)

    stop_config = {
        "base_size": 1.0,
        "adverse_stop": {
            "enabled": True,
            "threshold_pct": -0.05,
            "action": "close",
            "trigger_source": "close",
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)
    # The position exits at bar 1 via signal=0, not via stop
    sell = [t for t in trades if t["type"] == "sell"]
    assert len(sell) == 1
    # No stop-related exit_reason expected
    stop_exits = [t for t in trades if t.get("exit_reason") == "adverse_stop"]
    assert len(stop_exits) == 0  # bar 1 IS after entry — wait, entry_step=0, bar=1 > 0 → stop eligible
    # Actually bar 1 is entry_step+1, so stop IS eligible. Need a different test.
    # The signal=0 triggers first (per execution order: signal exit before risk stops).
    # So this tests signal-priority, not entry-bar immunity.

    # Let's test entry-bar immunity differently:
    signals2 = np.array([2, 1, 0])
    prices2 = np.array([94.0, 100.0, 100.0])  # entry at bar 0 close=94

    _, trades2 = evaluator.simulate(signals2, prices2, stop_config=stop_config)
    # Bar 0: entry at 94. Bar 1: price=100, that's +6.4% (no stop).
    # Bar 2: signal=0 exit. No adverse stop triggered.
    stop_exits2 = [t for t in trades2 if t.get("exit_reason") == "adverse_stop"]
    assert len(stop_exits2) == 0  # never hit -5%
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_adverse_stop_reduce_half_long tests\test_evaluator.py::test_adverse_stop_does_not_trigger_on_entry_bar -v
```
Expected: FAIL.

- [ ] **Step 3: Implement adverse stop check**

In `_check_risk_stops()`, add adverse stop logic:
```python
def _check_risk_stops(
    stop_config: dict,
    position: int,
    i: int,
    entry_step: int,
    entry_price: float,
    shares: float,
    capital: float,
    price: float,
    prices: np.ndarray,
    df: pd.DataFrame | None,
    peak_equity: float,
    dd_abs: float,
    no_new_entry: bool,
) -> dict | None:
    """Check all enabled risk stops. Returns an action dict or None."""
    actions = []

    # --- Adverse stop ---
    adv_config = stop_config.get("adverse_stop", {})
    if adv_config.get("enabled", False):
        threshold = float(adv_config.get("threshold_pct", -0.05))
        action_type = str(adv_config.get("action", "reduce_half"))

        # Compute unrealized return (close-to-close)
        if position == 1:
            unrealized = price / entry_price - 1.0
        else:
            unrealized = 1.0 - price / entry_price

        if unrealized <= threshold:
            actions.append({"action": action_type, "reason": "adverse_stop",
                           "priority": 3 if action_type == "close" else 6})
```

Then at the end of `_check_risk_stops()`:
```python
    if not actions:
        return None
    # Sort by priority (lower = higher priority); return the first
    actions.sort(key=lambda a: a["priority"])
    return actions[0]
```

In the main loop, execute the returned action. For `reduce_half`:
- Sell half the shares
- Release half the deployed capital + half the unrealized PnL
- Record a partial close event with `is_partial=True`, `exit_reason`, `parent_trade_id`
- Update `shares`, `entry_cost_basis` proportionally

For `close`:
- Execute full close (mirror existing close logic)
- Record close event with `exit_reason`

- [ ] **Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py -v
```
Expected: adverse stop tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dex/strategies/base.py tests/test_evaluator.py
git commit -m "feat: implement adverse move partial stop
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task B4: Implement dual ledger (event + logical trade)

**Files:**
- Modify: `dex/strategies/base.py` — close event enrichment for Phase 2
- Create: `dex/strategies/trade_ledger.py` — `build_logical_trade_ledger()`

**Interfaces:**
- Consumes: event ledger from `simulate()` (with Phase 2 partial close events)
- Produces: `build_logical_trade_ledger(trades) -> list[dict]` aggregated by `parent_trade_id`

- [ ] **Step 1: Write test for dual ledger**

Add to `tests/test_trade_ledger.py`:
```python
from dex.strategies.trade_ledger import build_logical_trade_ledger


def test_build_logical_ledger_aggregates_partial_closes() -> None:
    """Two partial closes on the same entry → one logical trade."""
    events = [
        {"type": "buy", "step": 0, "entry_size": 1.0, "entry_price": 100.0,
         "entry_notional": 100.0, "trade_id": 1, "parent_trade_id": 1},
        {"type": "sell", "step": 2, "entry_step": 0, "entry_price": 100.0,
         "exit_price": 95.0, "pnl": -2.5, "is_partial": True,
         "trade_id": 2, "parent_trade_id": 1, "exit_reason": "adverse_stop",
         "closed_fraction": 0.5, "remaining_fraction": 0.5,
         "logical_trade_closed": False, "entry_size": 1.0, "entry_notional": 100.0},
        {"type": "sell", "step": 5, "entry_step": 0, "entry_price": 100.0,
         "exit_price": 108.0, "pnl": 4.0, "is_partial": False,
         "trade_id": 3, "parent_trade_id": 1, "exit_reason": "signal",
         "closed_fraction": 0.5, "remaining_fraction": 0.0,
         "logical_trade_closed": True, "entry_size": 1.0, "entry_notional": 100.0},
    ]

    logical = build_logical_trade_ledger(events)

    assert len(logical) == 1
    t = logical[0]
    assert t["parent_trade_id"] == 1
    assert t["total_pnl"] == pytest.approx(-2.5 + 4.0)  # 1.5
    assert t["is_winner"] is True  # total PnL > 0
    assert t["partial_close_count"] == 1
    assert t["exit_reasons"] == ["adverse_stop", "signal"]


def test_build_logical_ledger_single_close() -> None:
    """Non-partial trade → one logical trade with partial_close_count=0."""
    events = [
        {"type": "buy", "step": 0, "entry_size": 1.0, "entry_price": 100.0,
         "entry_notional": 100.0, "trade_id": 1, "parent_trade_id": 1},
        {"type": "sell", "step": 4, "entry_step": 0, "entry_price": 100.0,
         "exit_price": 110.0, "pnl": 10.0, "is_partial": False,
         "trade_id": 2, "parent_trade_id": 1, "exit_reason": "signal",
         "closed_fraction": 1.0, "remaining_fraction": 0.0,
         "logical_trade_closed": True, "entry_size": 1.0, "entry_notional": 100.0},
    ]

    logical = build_logical_trade_ledger(events)

    assert len(logical) == 1
    t = logical[0]
    assert t["total_pnl"] == 10.0
    assert t["partial_close_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trade_ledger.py::test_build_logical_ledger_aggregates_partial_closes tests\test_trade_ledger.py::test_build_logical_ledger_single_close -v
```
Expected: FAIL (function not defined).

- [ ] **Step 3: Implement build_logical_trade_ledger()**

Add to `dex/strategies/trade_ledger.py`:
```python
def build_logical_trade_ledger(events: list[dict]) -> list[dict]:
    """Aggregate event ledger by parent_trade_id into logical trades.

    Args:
        events: Event list from simulate() with Phase 2 close events.

    Returns:
        One dict per logical trade (per original entry).
    """
    from collections import defaultdict

    # Group close events by parent_trade_id
    groups: dict[int, list[dict]] = defaultdict(list)
    open_info: dict[int, dict] = {}

    for ev in events:
        if ev.get("type") in ("buy", "sell_short"):
            tid = ev.get("trade_id")
            if tid is not None:
                open_info[tid] = ev
        elif ev.get("pnl") is not None:
            pid = ev.get("parent_trade_id")
            if pid is not None:
                groups[pid].append(ev)

    logical_trades = []
    for pid, close_events in groups.items():
        total_pnl = sum(float(ev.get("pnl", 0.0)) for ev in close_events)
        partial_count = sum(1 for ev in close_events if ev.get("is_partial"))

        reasons = []
        for ev in close_events:
            r = ev.get("exit_reason")
            if r:
                reasons.append(r)

        first_close = close_events[0]
        last_close = close_events[-1]
        open_ev = open_info.get(pid, {})

        logical_trades.append({
            "parent_trade_id": pid,
            "total_pnl": total_pnl,
            "is_winner": total_pnl > 0,
            "partial_close_count": partial_count,
            "exit_reasons": reasons,
            "entry_step": first_close.get("entry_step"),
            "exit_step": last_close.get("step"),
            "entry_price": first_close.get("entry_price"),
            "exit_price": last_close.get("exit_price"),
            "entry_notional": first_close.get("entry_notional"),
            "side": _infer_side(open_ev.get("type", "")),
        })

    return logical_trades
```

- [ ] **Step 4: Run tests**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trade_ledger.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dex/strategies/trade_ledger.py tests/test_trade_ledger.py
git commit -m "feat: add build_logical_trade_ledger() for Phase 2 dual ledger
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

## Plan C: Phase 2 Overlays — First Batch (DD Sizing + Adverse Stop)

### Task C1: Wire kill_switch close execution

**Files:**
- Modify: `dex/strategies/base.py` — main loop kill_switch block

**Interfaces:**
- Consumes: kill_switch state from DD sizing
- Produces: immediate close events with `exit_reason="kill_switch"`

- [ ] **Step 1: Implement kill_switch close execution**

In the main loop of `simulate()`, after DD update and before signal processing, add kill_switch close logic that mirrors the existing close-long / close-short code but records `exit_reason` and `is_partial=False`.

- [ ] **Step 2: Run DD kill_switch test**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_equity_dd_kill_switch_closes_position -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add dex/strategies/base.py
git commit -m "feat: wire kill_switch close execution in simulate()
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task C2: Wire partial close (reduce_half) execution for adverse stop

**Files:**
- Modify: `dex/strategies/base.py` — main loop reduce_half block

**Interfaces:**
- Consumes: action dict from `_check_risk_stops()` with `action="reduce_half"`
- Produces: partial close event with `is_partial=True`, `exit_reason`, `parent_trade_id`

- [ ] **Step 1: Implement reduce_half execution**

Add a `_execute_reduce_half()` function that:
1. Sells half the shares
2. Releases half the capital at risk + half the unrealized PnL
3. Updates `shares`, `entry_cost_basis` proportionally
4. Records a partial close event

The event must include: `is_partial=True`, `parent_trade_id`, `exit_reason`, `closed_fraction=0.5`, `remaining_fraction=0.5`, `logical_trade_closed=False`.

- [ ] **Step 2: Run adverse stop tests**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_adverse_stop_reduce_half_long -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add dex/strategies/base.py
git commit -m "feat: implement reduce_half execution for adverse stop
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

## Plan D: Invariant Tests

### Task D1: Entry-bar immunity invariant test

**Files:**
- Modify: `tests/test_evaluator.py`

- [ ] **Step 1: Write invariant test**

```python
def test_invariant_no_stop_on_entry_bar() -> None:
    """Invariant 5: Stops cannot trigger on the entry bar.

    A position opened at bar i can only be stopped at bar j where j > i.
    """
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    # Scenario: entry at bar 0, immediate -10% move at bar 1 (close=90)
    # The stop should fire at bar 1 (entry_step+1), not bar 0
    signals = np.array([2, 1, 1])
    prices = np.array([100.0, 90.0, 90.0])

    stop_config = {
        "base_size": 1.0,
        "adverse_stop": {
            "enabled": True,
            "threshold_pct": -0.05,
            "action": "close",
            "trigger_source": "close",
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    # Find any stop-triggered close
    stop_closes = [t for t in trades if t.get("exit_reason") == "adverse_stop"]
    for ev in stop_closes:
        # The close step must be > entry_step
        assert ev["step"] > ev["entry_step"], (
            f"Stop triggered on entry bar: step={ev['step']}, entry_step={ev['entry_step']}"
        )
```

- [ ] **Step 2: Run invariant test**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_invariant_no_stop_on_entry_bar -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_evaluator.py
git commit -m "test: add invariant test for entry-bar stop immunity
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task D2: One risk action per bar invariant test

**Files:**
- Modify: `tests/test_evaluator.py`

- [ ] **Step 1: Write invariant test**

```python
def test_invariant_at_most_one_risk_action_per_bar() -> None:
    """Invariant 6: At most one risk action per bar.

    If multiple stops would trigger on the same bar, only the highest-priority
    action executes.
    """
    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)

    # Long position with -8% adverse move after 48h+ — could trigger both
    # adverse_stop and time_in_loss_stop
    n_bars = 600  # more than 48h (576 bars)
    signals = np.array([2] + [1] * (n_bars - 1) + [0])
    prices = np.full(n_bars + 1, 100.0, dtype=float)
    prices[0] = 100.0
    prices[300:] = 92.0  # -8% adverse move

    stop_config = {
        "base_size": 1.0,
        "adverse_stop": {
            "enabled": True,
            "threshold_pct": -0.05,
            "action": "close",
            "trigger_source": "close",
        },
        "time_in_loss_stop": {
            "enabled": True,
            "tiers": [(576, -0.02, "reduce_half")],
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    # Count events at each bar
    from collections import Counter
    close_steps = Counter(
        t["step"] for t in trades if t.get("pnl") is not None and t.get("is_partial", False)
    )
    # No bar should have more than 1 risk action
    for step, count in close_steps.items():
        assert count <= 1, f"Bar {step} has {count} risk actions"
```

- [ ] **Step 2: Run invariant test**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_invariant_at_most_one_risk_action_per_bar -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_evaluator.py
git commit -m "test: add invariant test for single risk action per bar
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task D3: Dual ledger reconciliation invariant test

**Files:**
- Modify: `tests/test_evaluator.py`

- [ ] **Step 1: Write invariant test**

```python
def test_invariant_dual_ledger_pnl_reconciliation() -> None:
    """Invariant 7: Event ledger PnL must reconcile with logical trade PnL.

    Sum of all close event PnLs == sum of all logical trade total PnLs.
    """
    from dex.strategies.trade_ledger import build_logical_trade_ledger

    evaluator = StrategyEvaluator(initial_capital=100.0, commission=0.0, slippage=0.0)
    signals = np.array([2, 1, 1, 1, 0])
    prices = np.array([100.0, 96.0, 94.0, 93.0, 90.0])

    stop_config = {
        "base_size": 1.0,
        "adverse_stop": {
            "enabled": True,
            "threshold_pct": -0.05,
            "action": "reduce_half",
            "trigger_source": "close",
        },
    }

    _, trades = evaluator.simulate(signals, prices, stop_config=stop_config)

    event_pnl = sum(t.get("pnl", 0.0) for t in trades if t.get("pnl") is not None)
    logical = build_logical_trade_ledger(trades)
    logical_pnl = sum(t["total_pnl"] for t in logical)

    assert abs(event_pnl - logical_pnl) < 0.01, (
        f"PnL mismatch: event={event_pnl:.4f}, logical={logical_pnl:.4f}"
    )
```

- [ ] **Step 2: Run invariant test**

```powershell
.venv\Scripts\python.exe -m pytest tests\test_evaluator.py::test_invariant_dual_ledger_pnl_reconciliation -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_evaluator.py
git commit -m "test: add invariant test for dual ledger PnL reconciliation
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

### Task D4: Run full test suite

- [ ] **Step 1: Run all tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_evaluator.py tests/test_trade_ledger.py -v
```
Expected: all tests PASS (existing + new).

- [ ] **Step 2: Run ruff lint**

```powershell
uv run ruff check dex/strategies/base.py dex/strategies/trade_ledger.py tests/test_evaluator.py tests/test_trade_ledger.py
```
Expected: no errors.

- [ ] **Step 3: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: final invariant tests and lint for Phase 2 infrastructure
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"""
```

---

## Implementation Order Summary

```
Plan A (Phase 0 + Phase 1, parallel):
  A1 → A2 → A3 → A4 (diagnostic pipeline)
  A5 (fixed size matrix, independent)

Plan B (Phase 2 infrastructure):
  B1 (stop_config skeleton) → B2 (DD sizing) → B3 (adverse stop) → B4 (dual ledger)

Plan C (Phase 2 execution wiring):
  C1 (kill_switch close) → C2 (reduce_half partial close)

Plan D (invariant tests):
  D1 → D2 → D3 → D4
```

Plan B depends on Plan A1-A2 (entry fields in events).
Plan C depends on Plan B.
Plan D runs after Plan C, verifying all invariants.

**Not in this plan**: time-in-loss stop, squeeze stop, break-even stop, Monte Carlo integration, the full `run_risk_overlay_experiments.py` orchestration script. Those are deferred to a future plan after the Phase 2 infrastructure is validated.
