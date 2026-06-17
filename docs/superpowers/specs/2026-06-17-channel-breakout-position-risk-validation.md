# Channel Breakout 375/432 — Position Sizing & Tail-Risk Validation Spec

**Date**: 2026-06-17
**Status**: design spec (no implementation yet)
**Checkpoint**: `checkpoints/channel_breakout_375_432.pt` (v2 naked)
**Data**: ETHUSDT 5m 2600d, OOS `2024-06-06 14:25` ~ `2026-06-12 02:55`

---

## 1. Scope & Architecture

This spec covers position sizing and tail-loss risk overlays for the v2 naked
`375/432` Donchian breakout strategy. It does **not** touch entry filters, signal
generation, checkpoints, or live/demo routing.

Three research layers connected by phase gates:

```
Phase 0 — Diagnostic Ledger
  Extend trade records with MAE/MFE/duration/regime.
  Answer: can big winners tolerate adverse stops?

Phase 1 — Fixed Position Size Matrix
  Use existing position_sizes to sweep 0.40x–0.50x.
  Select safe and balanced baselines for Phase 2.

Phase 2 — Risk Control Stack
  Layer dynamic protection on top of the Phase 1 baseline.
  Equity DD sizing → adverse partial stop → time-in-loss → squeeze → break-even.
```

### 1.1 Dependency Model

```
Phase 0 ───────────────────────────────────────────┐
  │  Does NOT block Phase 1.                        │
  │  Blocks Phase 2 final threshold promotion only. │
  ▼                                                 │
Phase 1 ─────────────────────┐                      │
  │  Can run immediately.     │                      │
  │  Produces fixed-size      │                      │
  │  baselines for Phase 2.   │                      │
  ▼                           ▼                      ▼
Phase 2 ────────────────────────────────────────────┘
  Mechanism implementation may start before Phase 0.
  Parameter grids must be pre-registered.
  Final thresholds cannot be promoted before:
    - Phase 0 diagnostics (Gate 0 review)
    - Phase 1 baseline selection (Gate 1)
```

### 1.2 Out of Scope

- Entry filter variants of any kind
- Signal core modifications (`channel_breakout.py`)
- New checkpoint creation
- Live/demo routing or execution
- Production deployment
- Scoring logic changes (`evaluate()` / `compute_metrics()`)

### 1.3 Code Change Surface (minimum)

| File | Change |
|------|--------|
| `dex/strategies/base.py` | `simulate()`: add `entry_price`/`exit_price` to events, add `stop_config` support |
| `scripts/diagnose_mae_mfe.py` | New: Phase 0 MAE/MFE diagnostic |
| `scripts/run_risk_overlay_experiments.py` | New: Phase 1 matrix + Phase 2 experiments |
| `dex/strategies/base.py` | `enrich_trade_ledger()`: post-process MAE/MFE/duration/regime |

No changes to: `evaluate()`, `compute_metrics()`, `channel_breakout.py`, any
checkpoint, any live file.

---

## 2. Phase 0 — Diagnostic Ledger

**Goal**: Extend trade records with per-trade MAE, MFE, duration, and regime
context. Answer the questions that gate Phase 2 threshold selection.

**Gate 0**: Does NOT block Phase 1. Blocks Phase 2 final threshold promotion.

### 2.1 simulate() Minimal Enhancement

Add three fields to existing event dicts. No new event types.

```
open events (buy / sell_short):
  + entry_price: float      # execution price (after slippage)
  + entry_notional: float   # actual deploy amount at entry
  + entry_size: float       # position multiplier used at entry

close events (sell / buy_cover / sell_final / buy_cover_final):
  + entry_price: float      # inherited from matching open event
  + exit_price: float       # execution price (after slippage)
  + entry_notional: float   # inherited from matching open event
```

`compute_metrics()` is unaffected — it only reads `pnl`. **(This applies to
Phase 0 only. In Phase 2, partial close events must NOT be passed directly to
trade-level metrics without logical trade aggregation. See Section 4.9.)**

### 2.2 enrich_trade_ledger()

Post-processing function. Reads trades + OHLCV DataFrame, outputs one row per
closed logical trade.

```python
def enrich_trade_ledger(
    trades: list[dict],
    df: pd.DataFrame,         # must have high, low, close; regime column optional
    timeframe_minutes: int = 5,
    regime_col: str | None = "regime",
) -> list[dict]:
```

**Output fields per closed trade**:

| Field | Type | Source |
|-------|------|--------|
| `side` | `"long"` \| `"short"` | Inferred from event type |
| `entry_step` | int | Existing |
| `exit_step` | int | Existing (`step` on close event) |
| `entry_time` | datetime | `df.index[entry_step]` |
| `exit_time` | datetime | `df.index[exit_step]` |
| `entry_price` | float | Open event |
| `exit_price` | float | Close event |
| `entry_notional` | float | Actual deploy amount at entry (from simulator) |
| `entry_size` | float | Position size multiplier at entry |
| `pnl` | float | Existing |
| `return_pct` | float | See 2.3 |
| `duration_bars` | int | `exit_step - entry_step` |
| `duration_days` | float | `duration_bars * timeframe_minutes / 1440` |
| `mae_pct` | float | See 2.3 (≤ 0) |
| `mfe_pct` | float | See 2.3 (≥ 0) |
| `mae_step` | int | Bar index where MAE occurred |
| `mfe_step` | int | Bar index where MFE occurred |
| `time_to_mae_bars` | int | `mae_step - entry_step` |
| `time_to_mae_hours` | float | `time_to_mae_bars * timeframe_minutes / 60` |
| `time_to_mfe_bars` | int | `mfe_step - entry_step` |
| `time_to_mfe_hours` | float | `time_to_mfe_bars * timeframe_minutes / 60` |
| `entry_regime` | str \| None | `df[regime_col][entry_step]` if available |
| `exit_regime` | str \| None | `df[regime_col][exit_step]` if available |
| `mae_pnl_est` | float | `mae_pct * entry_notional` (auxiliary) |
| `mfe_pnl_est` | float | `mfe_pct * entry_notional` (auxiliary) |

### 2.3 MAE/MFE Calculation

Uses the same linear PnL convention as `simulate()` (USDT-linear contracts).
Long/short unified: MAE ≤ 0, MFE ≥ 0.

**Return path**:
```
long_return_path  = price / entry_price - 1
short_return_path = 1 - price / entry_price
```

**MAE/MFE window**: `entry_step + 1` to `exit_step` (inclusive).
Entry bar high/low occurs before entry execution (close-fill); excluding it
prevents diagnostic contamination.

**Formulas**:
```
long:
  MAE = min(low[entry+1 : exit+1] / entry_price - 1)
  MFE = max(high[entry+1 : exit+1] / entry_price - 1)

short:
  MAE = min(1 - high[entry+1 : exit+1] / entry_price)
  MFE = max(1 - low[entry+1 : exit+1] / entry_price)
```

**Empty window fallback**: If `exit_step <= entry_step`, fall back to
close-to-close return only:
```
mae_pct = min(0, return_pct)
mfe_pct = max(0, return_pct)
mae_step = exit_step
mfe_step = exit_step
```

### 2.4 Pre-Registered Grouping

```python
GROUPS = {
    "big_winners":  "pnl > 0 AND pnl >= winner_p80",
    "big_losers":   "pnl < 0 AND pnl <= loser_p20",
    "top_loss_20":  "lowest 20 trades by pnl",
    "top_loss_50":  "lowest 50 trades by pnl",
    "all_winners":  "pnl > 0",
    "all_losers":   "pnl < 0",
}

SUBGROUP_BY = ["side", "entry_regime"]
```

### 2.5 Diagnostic Output

Script: `scripts/diagnose_mae_mfe.py`

**File 1**: `research_workspace/diagnostics/
channel_breakout_375_432_v2_oos_2600d_mae_mfe_ledger.csv`
— one row per closed trade.

**File 2**: `research_workspace/diagnostics/
channel_breakout_375_432_v2_oos_2600d_mae_mfe_report.md`
— answers these questions:

| # | Question | Method |
|---|----------|--------|
| 1 | Big winners: % with MAE worse than -4%/-5%/-6%/-7% | `sum(mae_pct < threshold) / len(big_winners)` for each threshold |
| 2 | Big losers: % with MAE worse than -4%/-5%/-6%/-7% | Same, for big_losers |
| 3 | Big losers: median time_to_mae | `median(time_to_mae_bars)` for big_losers |
| 4 | Big winners: median time_to_mfe | `median(time_to_mfe_bars)` for big_winners |
| 5 | Horizon unrealized PnL: at 48h/72h/96h, how many trades still losing, and how did they finish? | For each horizon: compute unrealized return at horizon bar close; filter unrealized < 0 / < -1% / < -2% / < -3%; summarize final pnl distribution |
| 6 | BE stop: how many big winners would a break-even stop have killed? | For each big_winner: detect if after MFE ≥ +3%, price retraced to ≤ 0% (tight) or ≤ +0.5% (loose) before final exit |
| 7 | Top 20 / Top 50 loss contribution | `abs(sum(top_N.pnl)) / abs(sum(all_losers.pnl))` |

### 2.6 Gate 0 — Decision Rules

```
Gate 0 does NOT block Phase 1.
Gate 0 blocks Phase 2 final threshold promotion.

Rules:
  IF big_winners with MAE worse than -5% < 20%:
    → -5% adverse partial stop is eligible for testing
    → Phase 2 should prioritize -5%/-6% partial stop

  IF big_winners with MAE worse than -7% is high:
    → Hard full-stop carries significant winner-kill risk
    → Phase 2 should prioritize partial stop + time-in-loss stop
    → -4% hard full-stop is NOT recommended

  IF big_losers median time_to_mae_hours < 48:
    → time-in-loss stop is likely effective
    → 48h becomes the first-tier time-in-loss threshold

  IF BE stop would kill > 15% of big_winners:
    → Tight BE stop is NOT recommended
    → Only test BE partial or BE loose
```

---

## 3. Phase 1 — Fixed Position Size Matrix

**Goal**: Sweep fixed position sizes using the existing `position_sizes`
parameter. Select safe and balanced baselines for Phase 2.

**Gate 1**: Selects the fixed-size baseline(s) that Phase 2 experiments run on
top of.

### 3.1 Experiment Matrix

| Size | `position_sizes` | Purpose |
|------|-------------------|---------|
| 1.0x | 1.0 (already run) | Naked reference |
| 0.50x | 0.50 | Aggressive upper bound |
| 0.475x | 0.475 | Near 0.50x, check tail jump |
| **0.45x** | 0.45 | **Aggressive boundary / possible sweet spot after risk overlay** |
| 0.425x | 0.425 | Balanced baseline candidate |
| 0.40x | 0.40 | Safe / defensive baseline |

**Fixed parameters**:
- Checkpoint: `channel_breakout_375_432.pt` (v2 naked)
- Data: ETHUSDT 5m 2600d
- OOS window: `2024-06-06 14:25` ~ `2026-06-12 02:55`
- Signals unchanged, no regime filter, no stops

### 3.2 Implementation

```python
position_sizes = np.full(len(signals), target_size)
equity, trades = evaluator.simulate(signals, prices, df, position_sizes=position_sizes)
```

No simulator changes required. The matrix script may compute additional
diagnostics (underwater duration, loss contribution, etc.) from the equity
curve and trade ledger.

### 3.3 Metrics Per Size

| Category | Metrics |
|----------|---------|
| Return | Total Return, Annualized Return, Sharpe |
| Risk | MaxDD, MaxDD duration (days), Longest underwater (days) |
| Trade | Trade count, Win rate, Profit Factor, Avg Win / Avg Loss |
| Tail | Top 5/10/20/50 loss contribution, Max single loss, Worst consecutive loss streak |
| Monte Carlo | MC Return P5/P50/P95, MC DD P5/P50/P95, MC loss probability, MC DD<-30% probability |
| vs Baseline | Return delta / DD delta / Sharpe delta vs 1.0x naked |

### 3.4 Monte Carlo Setup

- Data: ETHUSDT 5m 2600d, OOS window as above
- Simulations: 2000 (1d block bootstrap, seed=2202)
- **Each size must be independently simulated** then bootstrapped from its own
  equity return series. Do NOT rescale 1.0x trade PnL.

If any size's MC results deviate materially from the 2026-06-17 prior runs,
diagnose the methodology difference before promotion.

### 3.5 Gate 1 — Baseline Selection

**Outputs**: Three tiers, not one.

| Tier | Description | Prior expectation |
|------|-------------|-------------------|
| `safe_baseline` | Lowest-risk qualified size | 0.40x |
| `balanced_baseline` | Highest-return qualified size | 0.425x or 0.45x |
| `aggressive_candidate` | Nearest size above threshold | 0.45x (if not balanced) |

**Hard filters** (must pass all three):

1. MC DD<-30% probability < 15%
2. MC loss probability < 5%
3. No unacceptable degradation in MaxDD / underwater duration vs the next safer
   size

**Borderline rule**:

- If MC DD<-30% is between 14% and 16%, mark as *borderline*.
- Borderline candidates require either: (a) rerun with 5000 MC simulations, or
  (b) rerun with an alternate seed, before promotion.

**Tie-break among qualified sizes**:

1. Lower MC DD<-30% probability
2. Lower MaxDD / underwater duration
3. Higher Total Return
4. Higher Sharpe

**Rule 3 (return preference)**: Only applies among sizes that pass the hard
filters. Must not override the MC DD<-30% < 15% gate.

**Fallback**: If no size passes all hard filters, mark `no_qualified_fixed_size`
and default Phase 2 baseline to 0.40x (best-known safe tier). Phase 2 may still
proceed since DD sizing could pull breach probability below threshold.

### 3.6 Prior Data (reference only — must be rerun)

| Size | MC DD<-30% | Status |
|------|-----------|--------|
| 0.40x | 9.80% | ✅ Qualified |
| 0.425x | 13.55% | ✅ Qualified / borderline-adjacent |
| 0.45x | 17.05% | ❌ Aggressive candidate |
| 0.475x | 21.75% | ❌ |
| 0.50x | 26.15% | ❌ |

### 3.7 Output

- `research_workspace/diagnostics/
  channel_breakout_375_432_v2_oos_2600d_fixed_size_matrix.csv`
- `research_workspace/diagnostics/
  channel_breakout_375_432_v2_oos_2600d_fixed_size_baseline.md`

Both files include run metadata: checkpoint, dataset, OOS range, target_size,
MC seed, simulations, block size, fee/slippage, git commit hash.

---

## 4. Phase 2 — Risk Control Stack

**Goal**: Layer dynamic protection on top of the Phase 1 baseline. Reduce tail
risk without destroying the signal edge.

**Gate 2**: Each overlay must pass deterministic OOS screening before MC.
Combos only test top candidates from single-overlay validation.

### 4.1 Design Principles

1. **Signals untouched.** All stops read signals + prices; they do not modify
   signal generation.
2. **Mechanisms first, thresholds later.** Code and parameter grids can be
   pre-registered. Final thresholds require Gate 0 review.
3. **Conservative execution.** Stop trigger may use bar high/low (per stop
   type), but execution always uses **current bar close**. No intrabar ideal
   fill.
4. **Phase 2 uses `stop_config.base_size` as the ONLY base position source.**
   `position_sizes` is fixed to 1.0 or omitted entirely — no double scaling.
5. **Stops only act on existing positions.** New entries on the current bar
   cannot trigger stops on the same bar.
6. **At most one risk action per bar.** If multiple stops trigger, close
   takes priority over reduce.

### 4.2 Per-Bar Execution Order

```
For each bar i:
  1. Mark existing position to close[i] (unrealized PnL).
  2. Update peak_equity and equity_dd.
  3. If a position is open:
     a. Check kill_switch (equity DD ≥ kill_switch_dd → close position).
     b. Check strategy signal for exit/reverse.
     c. If strategy does NOT exit, check risk stops.
     d. If multiple risk stops trigger, execute at most ONE:
        Priority: close > reduce_half > reduce_quarter.
        Record all trigger reasons even if only one action executes.
  4. If flat after step 3, and no kill_switch / no_new_entry block:
     a. Process new entry signal.
     b. New position is immune to risk stops on bar i.
  5. Record equity and trade events.
```

### 4.3 stop_config Schema

```python
stop_config = {
    "base_size": 0.425,          # Phase 2 ONLY source of base size
    "equity_dd_sizing": {
        "enabled": True,
        "tiers": [
            # (dd_abs_upper, multiplier)
            # dd_abs = (peak_equity - current_equity) / peak_equity  (positive)
            (0.05, 1.00),        # dd < 5%: 100% base
            (0.10, 0.90),        # 5% ≤ dd < 10%: 90%
            (0.15, 0.75),        # 10% ≤ dd < 15%: 75%
            (0.20, 0.50),        # 15% ≤ dd < 20%: 50%
        ],
        "no_new_entry_dd": 0.20, # dd ≥ 20%: close existing allowed, no new entries
        "kill_switch_dd": 0.25,  # dd ≥ 25%: close all positions, block everything
    },
    "adverse_stop": {
        "enabled": True,
        "threshold_pct": -0.05,  # unrealized return ≤ -5%
        "action": "reduce_half", # reduce_half | close
        "trigger_source": "close",  # v1: close unrealized PnL
    },
    "time_in_loss_stop": {
        "enabled": True,
        "tiers": [
            # (bars_since_entry, loss_threshold_pct, action)
            (576,  -0.02, "reduce_half"),  # 48h, loss ≥ 2%
            (864,  -0.03, "close"),         # 72h, loss ≥ 3%
            (1152, -0.01, "close"),         # 96h, loss ≥ 1%
        ],
    },
    "squeeze_stop": {
        "enabled": True,
        "atr_multiple": 2.5,
        "require_channel_break": True,
        "channel_upper_lagged": True,   # MUST use lagged Donchian
        "action": "reduce_half",
    },
    "break_even_stop": {
        "enabled": True,
        "trigger_mfe_pct": 0.03,     # mfe ≥ +3%
        "stop_level_pct": 0.00,      # retrace to ≤ 0%
        "mfe_source": "high_low",    # MFE uses bar high/low
        "retrace_source": "close",   # retrace trigger uses bar close
        "action": "reduce_half",
    },
}
```

### 4.4 Layer A: Equity DD Sizing

**Mechanism**: Does not touch individual trades. When equity drawdown exceeds a
tier threshold, subsequent entries deploy a reduced fraction of base_size. Open
positions are unaffected (only new entries scale down).

**DD formula** (positive):
```
dd_abs = (peak_equity - current_equity) / peak_equity
```

**Effective entry multiplier**:
```
multiplier = lookup_tier(dd_abs)
deploy = base_size * multiplier * equity_at_entry
```

**Parameter grids**:
```
tiers_v1:  [0.05→1.0, 0.10→0.90, 0.15→0.75, 0.20→0.50]
tiers_v2:  [0.05→0.95, 0.10→0.85, 0.15→0.65, 0.20→0.40]  # more aggressive reduction
tiers_v3:  [0.05→1.0, 0.08→0.90, 0.12→0.75, 0.18→0.50]  # earlier trigger
```

**no_new_entry**: At `dd_abs ≥ no_new_entry_dd`, allowed to close/reverse-flat
existing positions, but NOT allowed to open new positions. A reverse signal
closes the old position and stays flat.

**kill_switch**: At `dd_abs ≥ kill_switch_dd`, close all positions immediately.
Entries resume when `dd_abs < no_new_entry_dd` (research mode; production may
require manual reset).

**Complexity**: Low. Only tracks peak equity; adjusts deploy at entry time. No
mid-trade position adjustment.

### 4.5 Layer B: Adverse Move Partial Stop

**Mechanism**: During a position, if unrealized return (close-to-close) reaches
the threshold, reduce or close.

**Trigger**: `unrealized_return_pct ≤ threshold_pct` (using bar close price).
**Execution**: Current bar close.

**Parameter grids**:
```
threshold: [-0.04, -0.05, -0.06]      # raw pct
action:    [reduce_half, close]         # first-trigger action
```

**Partial close semantics**: Selling half the shares, releasing half the
remaining exposure plus half the unrealized PnL. The retained position can
trigger a second stop (which would then close fully).

**Complexity**: Medium. Requires tracking unrealized PnL per bar and
implementing partial share reduction in the simulator.

### 4.6 Layer C: Time-in-Loss Stop

**Mechanism**: If a position has been open for N bars AND unrealized return at
bar close is still below threshold, reduce or close.

**Horizon bars** (5m data):
```
horizon_bars = int(hours * 60 / timeframe_minutes)
```

| Horizon | Bars (5m) | Loss threshold | Action |
|---------|-----------|----------------|--------|
| 48h | 576 | < -2% | reduce_half |
| 72h | 864 | < -3% | close |
| 96h | 1152 | < -1% | close |

**Parameter grids**:
```
tiers_v1:  [(48h, -0.02, reduce_half), (72h, -0.03, close), (96h, -0.01, close)]
tiers_v2:  [(24h, -0.015, reduce_half), (48h, -0.02, close), (72h, -0.025, close)]  # more aggressive
```

**Complexity**: Medium. Requires tracking `entry_step` and counting bars since
entry.

### 4.7 Layer D: Squeeze Stop

**Mechanism**: Detects short-squeeze conditions using price action only (no OI
or funding data). If a short position faces a sharp adverse move that also
breaks back above the channel, reduce exposure.

**Trigger** (all conditions must hold):
1. `side == short`
2. `(high - entry_price) / entry_price > atr_multiple * (atr / entry_price)`
   or equivalently: `high - entry_price > atr_multiple * atr`
3. `close > donchian_upper_lagged` (lagged = previous bar, does NOT include
   current bar high)

**Execution**: Current bar close.
**Parameter grids**:
```
atr_multiple:          [2.0, 2.5, 3.0]
require_channel_break: [True, False]
```

**CRITICAL**: `donchian_upper` MUST be lagged (shifted by 1). Using a Donchian
upper that includes the current bar high would make the condition either
untriggerable or a look-ahead bias.

**Complexity**: Higher. Requires ATR and lagged Donchian channel values passed
into the simulator.

### 4.8 Layer E: Break-Even Partial Stop

**Mechanism**: Once a position has reached a favorable MFE threshold, if price
retraces to near break-even, reduce exposure to protect against turning a
winner into a loser.

**MFE tracking**: Uses bar high/low to update `mfe_since_entry`.
**Retrace trigger**: Uses bar close to check `unrealized_return ≤ stop_level`.
**Execution**: Current bar close.

**Parameter grids**:
```
trigger_mfe  stop_level  action
+2.5%        0%          reduce_half    (tight partial)
+3.0%        0%          reduce_half    (baseline)
+4.0%        +0.5%       reduce_half    (loose partial)
+3.0%        0%          close          (tight full — test with caution)
```

**Long example**:
```
mfe_since_entry = max(mfe_since_entry, high / entry_price - 1)
if mfe_since_entry >= trigger_mfe_pct:
    if close / entry_price - 1 <= stop_level_pct:
        reduce_half at close
```

**Short example**:
```
mfe_since_entry = max(mfe_since_entry, 1 - low / entry_price)
if mfe_since_entry >= trigger_mfe_pct:
    if 1 - close / entry_price <= stop_level_pct:
        reduce_half at close
```

**Complexity**: Medium. Requires per-trade MFE tracking and activation flag.

### 4.9 Partial Close Accounting (Dual Ledger)

Partial close introduces a critical accounting problem: one logical trade
produces multiple close events. If `compute_metrics()` counts each close event
as a trade, win rate and trade count will be distorted.

**Requirement**: Phase 2 must maintain two ledgers. Both open and close events
carry a `trade_id` for identity tracking.

**Open events** record:
```
trade_id: int, parent_trade_id: int (= trade_id for non-partial entries)
```

**Event ledger** — every partial/full close event, as produced by `simulate()`.
Used for equity curve accounting only. Each event has:
```
type, step, entry_step, entry_price, exit_price, pnl,
trade_id: int, parent_trade_id: int,
is_partial: bool,
exit_reason: "signal" | "adverse_stop" | "time_in_loss" | "squeeze_stop" |
             "break_even_stop" | "kill_switch",
closed_fraction: float, remaining_fraction: float,
logical_trade_closed: bool
```

**Logical trade ledger** — aggregated by `parent_trade_id`. One row per
original entry. Used for: win rate, Avg Win/Loss, Profit Factor, MAE/MFE,
killed winner analysis, trade count.

The logical ledger is produced by aggregating event ledger entries that share
the same `parent_trade_id`. Total PnL = sum of all partial PnLs. The logical
trade is "winning" if total PnL > 0.

`parent_trade_id` is preferred over `(entry_step, side)` as the aggregation
key because it is robust to same-bar close-then-open sequences and future
multi-instrument extensions.

### 4.10 Validation Matrix

**Stage A — Deterministic screening** (all parameter grids):
- Run all single-overlay parameter combinations on both `safe_baseline` and
  `balanced_baseline`.
- Compute all metrics WITHOUT Monte Carlo.
- Reject candidates meeting any of these Stage A conditions:
  1. Total Return drops by > 35% vs same-size no-overlay baseline, **unless**
     MaxDD improves by > 25%.
  2. MaxDD improves by < 5% AND MC-relevant tail metrics (top 20 / top 50
     loss contribution) show no improvement.
  3. Trade count changes by > 30% due purely to accounting artifacts (partial
     close splitting), NOT due to genuine stop exits.
  4. Logical trade ledger total PnL cannot be reconciled with event ledger PnL
     (sum mismatch > 0.1% of initial capital).

**Stage B — Monte Carlo** (top 2–3 per overlay):
- Run 2000 MC on Stage A survivors.
- Select best parameter set per overlay.

**Stage C — Combos** (only Stage B survivors):
```
combo_conservative: safe_baseline + DD_sizing_best + adverse_stop_best
combo_balanced:     balanced_baseline + DD_sizing_best + adverse_stop_best
combo_full:         balanced_baseline + DD_sizing_best + adverse_stop_best
                    + time_in_loss_best
```

Each combo: deterministic OOS → 2000 MC → full metrics vs no-overlay baseline.

### 4.11 Additional Metrics (Phase 2 only)

| Metric | Definition |
|--------|-----------|
| Stop triggers | Count per stop type |
| Partial close count | reduce_half events vs close events |
| Damaged winners | Baseline big_winners whose overlay PnL ≤ 0 or overlay PnL < 50% baseline PnL |
| Whipsaw count | Stop triggered, then within 288 bars (24h) price moved ≥ +2% in the original direction |

### 4.12 Implementation Order vs Research Priority

**Implementation order** (by code complexity, avoid blocking):

1. Equity DD sizing (lowest complexity)
2. Adverse move partial stop
3. Time-in-loss stop
4. Squeeze stop
5. Break-even partial stop

**Research priority** (by expected risk/reward):

1. Equity DD sizing (does not touch individual trades)
2. Time-in-loss stop (best match to trade loss profile)
3. Adverse move partial stop (needs MAE/MFE calibration)
4. Squeeze stop (more complex, wait for baseline results)
5. Break-even partial stop (most likely to kill trend winners)

---

## 5. Implementation Invariants

These rules must not be violated during implementation. If a rule turns out to
be infeasible, update this spec before proceeding.

1. **No entry filter changes.** Do not add, remove, or modify any entry-block
   logic.
2. **No signal core changes.** Do not modify `channel_breakout.py` or the
   `375/432` signal generation.
3. **No new checkpoint.** The source checkpoint is
   `channel_breakout_375_432.pt`. Do not create new checkpoints.
4. **Phase 2 base_size is the only position source.** `position_sizes` must be
   fixed to 1.0 in Phase 2. `stop_config.base_size` is the sole size control.
5. **Stops cannot trigger on the entry bar.** New positions opened on bar `i`
   are immune to risk stops on bar `i`. Stop detection window starts at
   `entry_step + 1`, matching the Phase 0 MAE/MFE window.
6. **At most one risk action per bar.** If multiple stops trigger, execute only
   one (close > reduce_half > reduce_quarter).
7. **Partial close requires dual ledger.** Event ledger for equity, logical
   trade ledger for trade statistics.
8. **Stop execution uses close fill.** No intrabar ideal stop fill.
9. **Squeeze stop channel must be lagged.** Donchian upper used in squeeze
   detection must NOT include the current bar high.
10. **Final thresholds require Gate 0 review.** Parameter grids can be
    pre-registered; final promotion requires Phase 0 diagnostic data.

---

## 6. Output Files

All outputs go under `research_workspace/diagnostics/` with the prefix
`channel_breakout_375_432_v2_oos_2600d_`.

| File | Phase | Content |
|------|-------|---------|
| `_mae_mfe_ledger.csv` | 0 | One row per closed trade, enriched |
| `_mae_mfe_report.md` | 0 | Diagnostic report answering 7 questions |
| `_fixed_size_matrix.csv` | 1 | One row per fixed size |
| `_fixed_size_baseline.md` | 1 | Gate 1 decision + selected baselines |
| `_risk_overlay_stage_a.csv` | 2A | Deterministic screening results |
| `_risk_overlay_stage_b.csv` | 2B | MC results for top candidates |
| `_risk_overlay_stage_c.csv` | 2C | Combo results |
| `_risk_overlay_final.md` | 2 | Final recommendation |

Every output file includes run metadata: checkpoint, dataset, OOS range,
parameters, MC seed/simulations/block_size, fee/slippage, git commit hash,
script version.

---

## 7. Acceptance Criteria

A candidate risk stack is eligible for promotion only if:

1. It keeps the original `channel_breakout_375_432.pt` signal unchanged.
2. It reduces MC DD<-30% probability versus the fixed-size baseline (no
   overlay).
3. It does not materially destroy total return or Sharpe.
4. It lowers top 20 / top 50 loss contribution.
5. Damaged big_winners < 15% of baseline big_winners. A baseline big_winner is
   *damaged* if under the overlay its logical trade PnL ≤ 0, **or** its overlay
   PnL < 50% of its baseline PnL.
6. It passes deterministic OOS screening first, then Monte Carlo.
7. It produces both event ledger and logical trade ledger.
8. It uses conservative close-fill stop execution.

---

## 8. Phase Checklist

- [ ] Phase 0: `simulate()` enhanced with `entry_price` / `exit_price`
- [ ] Phase 0: `enrich_trade_ledger()` implemented
- [ ] Phase 0: `diagnose_mae_mfe.py` run, report produced
- [ ] Phase 0: Gate 0 review completed, thresholds registered
- [ ] Phase 1: Fixed size matrix run (0.40x–0.50x)
- [ ] Phase 1: Gate 1 completed, safe/balanced/aggressive baselines selected
- [ ] Phase 2: `stop_config` support added to `simulate()`
- [ ] Phase 2: Per-bar execution order implemented
- [ ] Phase 2: Dual ledger (event + logical trade) implemented
- [ ] Phase 2: Stage A — deterministic screening of all single overlays
- [ ] Phase 2: Stage B — MC on top 2–3 per overlay
- [ ] Phase 2: Stage C — combo validation
- [ ] Phase 2: Final recommendation with Gate 0 + Gate 1 sign-off
- [ ] Spec updated if any invariant was violated during implementation
