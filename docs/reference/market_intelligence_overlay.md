# Phase 4A: Frozen Market Intelligence Overlay Backtest

This phase tests a frozen market-intelligence risk layer on top of the existing
technical strategy. It does not call an LLM, change live trading, or change the
strategy signal logic.

## Market Intel Table

Supported formats: `.csv` and `.parquet`.

Required columns:

| column | meaning |
|---|---|
| `available_at` | Time when this row is visible to the backtest. Preferred. |
| `mode` | One of `normal`, `cautious`, `defensive`, `block_new_entries`. |

Compatibility time columns: `timestamp` or `datetime`. If used, they are treated
as `available_at`. If multiple time columns exist, `available_at` wins.

Future data rule:

```text
available_at <= signal_decision_time
```

The implementation uses `merge_asof(direction="backward")`, so same-time rows
are allowed and future rows are not.

## Mode Mapping

| mode | entry size |
|---|---:|
| `normal` | `1.0` |
| `cautious` | `0.5` |
| `defensive` | `0.0` |
| `block_new_entries` | `0.0` |

Unknown or expired intel uses `--market-intel-unknown-mode`, default
`cautious`. Invalid `mode` values in the table fail fast.

Entry size is frozen at entry. Later mode changes do not resize an existing
position. A direct flip first closes the old position, then opens the new side
only if the current entry size is greater than zero.

## CLI

Build an OHLCV-derived frozen intel table:

```bash
uv run python scripts/build_market_intel_from_market_stats.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --output data/market_intel/ETHUSDT_1h.parquet
```

Build with frozen derivatives stats:

```bash
uv run python scripts/build_market_intel_from_market_stats.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --derivatives-input data/market_intel/ETHUSDT_derivatives.parquet \
  --output data/market_intel/ETHUSDT_1h_derivatives.parquet
```

Or try fetching Binance USD-M funding/open-interest through `ccxt`:

```bash
uv run python scripts/build_market_intel_from_market_stats.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --include-derivatives \
  --output data/market_intel/ETHUSDT_1h_derivatives.parquet
```

If Binance returns a region restriction or rate-limit error, use
`--derivatives-input` with a pre-frozen CSV/Parquet instead.

Run the overlay backtest:

```bash
uv run python backtest_quant.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt \
  --market-intel data/market_intel/ETHUSDT_1h.parquet
```

Optional flags:

```bash
--market-intel-max-age-hours 3
--market-intel-unknown-mode cautious
--market-intel-random-control
--market-intel-random-seed 42
```

When enabled, the script prints baseline metrics and overlay metrics from the
same generated signal stream. Reported overlay diagnostics include vetoed
entries, half-size entries, expired intel bars, missing intel bars, and the
baseline post-trade PnL for vetoed/half-size entries.

## Scope

Skipped in Phase 4A:

- realtime LLM calls
- live trading changes
- random veto control CLI
- automatic news/social/funding ingestion

Add those only after the frozen overlay shows useful risk-filtering value.
