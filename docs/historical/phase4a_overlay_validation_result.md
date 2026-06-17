# Phase 4A Overlay Validation Result

Date: 2026-06-16

## Setup

- Strategy checkpoint: `checkpoints/channel_breakout_v2_1_balanced.pt`
- Symbol / interval: `ETHUSDT 5m`
- Backtest window: last 7 days from `data/crypto/ETHUSDT_5m_2600d.parquet`
- Window range: `2026-06-05 03:00:00` to `2026-06-12 02:55:00`
- Market-intel source: temporary manual sanity CSV, not committed
- Max intel age: `999h`
- Unknown mode: `cautious`

Manual sanity schedule:

| available_at | mode |
|---|---|
| `2026-06-05 00:00:00` | `normal` |
| `2026-06-07 00:00:00` | `cautious` |
| `2026-06-09 00:00:00` | `block_new_entries` |
| `2026-06-11 00:00:00` | `normal` |

## Results

| metric | baseline | overlay |
|---|---:|---:|
| final equity | `9816.33` | `9870.14` |
| total return | `-1.84%` | `-1.30%` |
| annualized return | `-62.33%` | `-49.76%` |
| annualized vol | `28.22%` | `23.99%` |
| Sharpe | `-2.2089` | `-2.0747` |
| max drawdown | `-3.90%` | `-3.38%` |
| win rate | `50.6%` | `51.7%` |
| closed trades | `83` | `60` |

Overlay diagnostics:

| item | value |
|---|---:|
| normal bars | `844` |
| cautious bars | `576` |
| block_new_entries bars | `576` |
| defensive bars | `0` |
| vetoed new entries | `28` |
| half-size new entries | `24` |
| expired intel bars | `0` |
| missing intel bars | `0` |
| vetoed baseline trade PnL | `n=23 avg=-3.50 sum=-80.48 USDT` |
| half-size baseline trade PnL | `n=24 avg=+4.43 sum=+106.42 USDT` |

## Read

Sanity behavior is correct:

- `available_at <= decision_time` matching worked with no expired or missing rows.
- `cautious` created half-size entries.
- `block_new_entries` vetoed new entries.
- Baseline and overlay reused the same signal stream.
- Overlay reduced closed trades from `83` to `60`.

This sanity table is artificial, so the result does not prove market-intel value.
It only proves the overlay machinery behaves as intended in a full CLI run.

## Conclusion

Phase 4A implementation is ready for real frozen market-intel validation.

Next required input: a real frozen market-intel CSV/Parquet with `available_at`
and `mode` covering the backtest window. Without that file, do not add realtime
LLM, live trading changes, or random veto control yet.

## OHLCV-Derived Frozen Intel Validation

After the sanity run, a reproducible market-intel table was generated from local
OHLCV statistics:

```bash
uv run python scripts/build_market_intel_from_market_stats.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --output data/market_intel/ETHUSDT_1h.parquet
```

Generated rows:

| mode | rows |
|---|---:|
| `normal` | `1084` |
| `cautious` | `320` |
| `block_new_entries` | `37` |

Backtest command:

```bash
uv run python backtest_quant.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt \
  --market-intel data/market_intel/ETHUSDT_1h.parquet \
  --market-intel-max-age-hours 2 \
  --market-intel-unknown-mode cautious
```

Results:

| metric | baseline | overlay |
|---|---:|---:|
| final equity | `5968.88` | `6415.93` |
| total return | `-40.31%` | `-35.84%` |
| annualized return | `-95.68%` | `-93.30%` |
| annualized vol | `18.83%` | `14.87%` |
| Sharpe | `-5.0808` | `-6.2738` |
| max drawdown | `-40.41%` | `-35.94%` |
| win rate | `38.9%` | `39.2%` |
| closed trades | `819` | `784` |

Overlay diagnostics:

| item | value |
|---|---:|
| normal bars | `12976` |
| cautious bars | `3840` |
| block_new_entries bars | `444` |
| defensive bars | `0` |
| vetoed new entries | `38` |
| half-size new entries | `199` |
| expired intel bars | `0` |
| missing intel bars | `0` |
| vetoed baseline trade PnL | `n=36 avg=-5.55 sum=-199.90 USDT` |
| half-size baseline trade PnL | `n=198 avg=-3.61 sum=-714.20 USDT` |

Read:

- The OHLCV-derived overlay reduced drawdown and total loss in this 60-day window.
- Vetoed trades were negative on average, which is the main signal to continue.
- Half-size trades were also negative on average, so cautious mode is directionally useful.
- This is still a price/volume risk layer, not a full LLM market-intelligence layer.

Decision: continue to the next validation step with random-veto control before
adding live LLM/news inputs.

## Random Veto Control

Random control was added to `backtest_quant.py` with:

```bash
--market-intel-random-control
--market-intel-random-seed 42
```

It uses the same baseline signal stream and applies the same number of vetoed
entries and half-size entries as the market-intel overlay, but chooses the entry
steps randomly.

Command:

```bash
uv run python backtest_quant.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 60 \
  --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt \
  --market-intel data/market_intel/ETHUSDT_1h.parquet \
  --market-intel-max-age-hours 2 \
  --market-intel-unknown-mode cautious \
  --market-intel-random-control \
  --market-intel-random-seed 42
```

Results:

| metric | baseline | overlay | random control |
|---|---:|---:|---:|
| final equity | `5968.88` | `6415.93` | `6503.16` |
| total return | `-40.31%` | `-35.84%` | `-34.97%` |
| annualized vol | `18.83%` | `14.87%` | `16.42%` |
| Sharpe | `-5.0808` | `-6.2738` | `-5.6459` |
| max drawdown | `-40.41%` | `-35.94%` | `-36.05%` |
| closed trades | `819` | `784` | `783` |

Entry attribution:

| bucket | overlay | random control |
|---|---:|---:|
| vetoed entries | `38` | `38` |
| half-size entries | `199` | `199` |
| vetoed trade PnL | `n=36 avg=-5.55 sum=-199.90` | `n=38 avg=-3.36 sum=-127.57` |
| half-size trade PnL | `n=198 avg=-3.61 sum=-714.20` | `n=199 avg=-4.12 sum=-819.78` |

Read:

- The overlay veto bucket selected worse trades than random veto for seed `42`.
- The overlay half-size bucket was slightly less bad than random half-size.
- One random seed is not enough to claim superiority; total return was slightly
  better for this random control run.
- The current OHLCV-derived overlay shows signal, but it is not yet proven
  stronger than random risk reduction.

Decision: run a small multi-seed random-control sweep before adding external
funding/OI/news inputs.

## Multi-Seed Random Control Sweep

Ran random controls for seeds `0..19`, with the same `38` vetoed entries and
`199` half-size entries as the OHLCV-derived overlay.

Summary:

| metric | baseline | overlay | random min | random mean | random max |
|---|---:|---:|---:|---:|---:|
| total return | `-40.31%` | `-35.84%` | `-37.93%` | `-35.13%` | `-33.04%` |
| max drawdown | `-40.41%` | `-35.94%` | `-38.02%` | `-35.47%` | `-33.71%` |
| annualized vol | `18.83%` | `14.87%` | `15.95%` | `16.63%` | `17.48%` |

Counts:

- Overlay return beat `6 / 20` random controls.
- Overlay max drawdown beat `8 / 20` random controls.
- Overlay volatility was lower than all 20 random controls.

Read:

- The current OHLCV-derived overlay reduces exposure and volatility.
- It does not yet prove better selection than random risk reduction on return
  or drawdown.
- The strongest positive sign remains attribution: overlay-vetoed trades were
  more negative than seed-42 random-vetoed trades, but that needs a broader
  attribution sweep too.

Decision: keep the overlay framework, but do not promote this OHLCV-only rule
as a real market-intelligence edge. Next useful step is adding a better frozen
data source such as funding rate / open interest before realtime LLM or live
trading.

## Phase 4A-3 Derivatives Stats Status

`scripts/build_market_intel_from_market_stats.py` now supports derivatives
inputs:

- `funding_rate`
- `funding_zscore`
- `open_interest`
- `oi_change_1h`
- `oi_change_4h`
- `oi_zscore`
- `price_return_1h`
- `realized_vol_1h`

Modes still output the same overlay contract: `available_at`, `symbol`, `mode`,
and `reason`.

Two input paths are supported:

```bash
--include-derivatives
```

Fetch Binance USD-M funding/open-interest via `ccxt`.

```bash
--derivatives-input data/market_intel/ETHUSDT_derivatives.parquet
```

Use a pre-frozen derivatives CSV/Parquet.

Current environment note:

- Binance USD-M is reachable after proxy changes.
- Binance funding history works for the tested ETHUSDT window.
- Binance open-interest history rejects older windows with `startTime is invalid`;
  a 25-day window worked.
- Bybit and OKX both expose funding/OI history through `ccxt`, but their OI
  history behavior needs separate exchange-specific validation before use.

Decision: use Binance for recent funding/OI validation where its OI history
window allows it; otherwise use `--derivatives-input` with a pre-frozen file.

## Binance Funding/OI 25-Day Validation

Generated a recent derivatives-enhanced table:

```bash
uv run python scripts/build_market_intel_from_market_stats.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 25 \
  --include-derivatives \
  --output data/market_intel/ETHUSDT_1h_derivatives_25d.parquet
```

Generated rows:

| mode | rows |
|---|---:|
| `normal` | `409` |
| `cautious` | `178` |
| `block_new_entries` | `14` |

Compared against OHLCV-only 25-day overlay:

| metric | baseline | OHLCV-only overlay | funding/OI overlay |
|---|---:|---:|---:|
| total return | `-14.37%` | `-15.89%` | `-14.13%` |
| max drawdown | `-14.37%` | `-15.89%` | `-14.13%` |
| annualized vol | `22.16%` | `17.56%` | `16.00%` |
| closed trades | `325` | `309` | `309` |

Funding/OI overlay diagnostics:

| item | value |
|---|---:|
| normal bars | `4876` |
| cautious bars | `2136` |
| block_new_entries bars | `168` |
| vetoed new entries | `16` |
| half-size new entries | `94` |
| expired intel bars | `0` |
| missing intel bars | `0` |
| vetoed baseline trade PnL | `n=16 avg=+4.83 sum=+77.35` |
| half-size baseline trade PnL | `n=94 avg=-1.82 sum=-170.89` |

Random seed `42` control:

| metric | funding/OI overlay | random control |
|---|---:|---:|
| total return | `-14.13%` | `-9.50%` |
| max drawdown | `-14.13%` | `-10.50%` |
| annualized vol | `16.00%` | `18.58%` |

20-seed random-control sweep:

| metric | baseline | funding/OI overlay | random min | random mean | random max |
|---|---:|---:|---:|---:|---:|
| total return | `-14.37%` | `-14.13%` | `-15.42%` | `-11.63%` | `-9.21%` |
| max drawdown | `-14.37%` | `-14.13%` | `-15.42%` | `-12.04%` | `-10.16%` |
| annualized vol | `22.16%` | `16.00%` | `17.28%` | `19.05%` | `20.41%` |

Counts:

- Overlay return beat `1 / 20` random controls.
- Overlay max drawdown beat `1 / 20` random controls.
- Overlay annualized volatility beat `20 / 20` random controls.

Read:

- Binance funding/OI data is now usable for recent windows.
- The current rule strongly reduces volatility.
- It still does not meet the proposed edge threshold of beating `70%` of random
  controls on return or max drawdown.
- Veto attribution is bad: vetoed trades were profitable on average in this
  25-day window.

Decision: keep the funding/OI ingestion path, but revise the derivatives mode
rules before treating this as a tradable edge.

## Derivatives Rule Revision

Attribution showed the old `block_new_entries` bucket was mostly triggered by
pure OHLCV volatility and had positive average PnL. The rule was changed:

- Pure OHLCV stress now maps to `cautious`.
- `block_new_entries` is reserved for derivatives-confirmed stress:
  extreme funding, or fast OI expansion combined with price/volatility shock.

Regenerated table:

```bash
uv run python scripts/build_market_intel_from_market_stats.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 25 \
  --include-derivatives \
  --output data/market_intel/ETHUSDT_1h_derivatives_25d.parquet
```

Rows:

| mode | rows |
|---|---:|
| `normal` | `409` |
| `cautious` | `192` |
| `block_new_entries` | `0` |

Backtest:

| metric | baseline | revised funding/OI overlay | random seed 42 |
|---|---:|---:|---:|
| total return | `-14.37%` | `-13.74%` | `-10.63%` |
| max drawdown | `-14.37%` | `-13.74%` | `-10.63%` |
| annualized vol | `22.16%` | `16.62%` | `18.84%` |
| closed trades | `325` | `325` | `325` |

Overlay diagnostics:

| item | value |
|---|---:|
| vetoed new entries | `0` |
| half-size new entries | `110` |
| expired intel bars | `0` |
| missing intel bars | `0` |
| half-size baseline trade PnL | `n=110 avg=-0.85 sum=-93.54` |

20-seed random-control sweep:

| metric | baseline | revised overlay | random min | random mean | random max |
|---|---:|---:|---:|---:|---:|
| total return | `-14.37%` | `-13.74%` | `-15.69%` | `-11.85%` | `-10.04%` |
| max drawdown | `-14.37%` | `-13.74%` | `-15.69%` | `-12.17%` | `-10.71%` |
| annualized vol | `22.16%` | `16.62%` | `17.37%` | `19.19%` | `20.46%` |

Counts:

- Revised overlay return beat `1 / 20` random controls.
- Revised overlay max drawdown beat `1 / 20` random controls.
- Revised overlay annualized volatility beat `20 / 20` random controls.

Read:

- The bad veto behavior is removed.
- The overlay still acts mostly as volatility reduction, not proven edge.
- Funding/OI rules need better selectivity before any live or LLM layer.

## Derivatives Attribution Report

Generated baseline-trade attribution files:

```bash
uv run python scripts/analyze_market_intel_rule_attribution.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 25 \
  --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt \
  --market-intel data/market_intel/ETHUSDT_1h_derivatives_25d.parquet \
  --output-md reports/market_intel_rule_attribution_ETHUSDT_25d.md \
  --output-csv data/research/market_intel_rule_attribution_ETHUSDT_25d.csv
```

Files:

- `reports/market_intel_rule_attribution_ETHUSDT_25d.md`
- `data/research/market_intel_rule_attribution_ETHUSDT_25d.csv`

Scope:

- Analyzes all baseline closed trades, not only overlay-affected trades.
- Labels each trade with the latest intel row where `available_at <= entry_time`.
- Reports count, mean PnL, median PnL, win rate, total PnL, and average holding bars.
- Splits long and short sides.

Notable buckets from the 25-day Binance funding/OI table:

| bucket | count | mean | median | win rate | total |
|---|---:|---:|---:|---:|---:|
| long, `oi_down_price_up` | `13` | `-25.76` | `-20.05` | `7.7%` | `-334.90` |
| short, `oi_up_price_down` | `15` | `-10.72` | `-2.32` | `40.0%` | `-160.83` |
| all, abs funding z >= 2 | `28` | `-4.25` | `-4.19` | `46.4%` | `-119.13` |
| all, cautious mode | `110` | `-0.85` | `+0.16` | `50.0%` | `-93.54` |
| all, normal mode | `215` | `-5.22` | `-3.55` | `37.7%` | `-1121.74` |

Read:

- No bucket cleanly satisfies the stricter success standard yet.
- `long + oi_down_price_up` is the strongest negative candidate, but count is
  only `13`, below the proposed `count >= 20` bar.
- `short + oi_up_price_down` is directionally plausible but also below `20`
  trades and median is only mildly negative.
- Abs funding z-score >= 2 has enough trades but does not have win rate below
  baseline; it is not strong enough alone.

Decision: do not tune rules from this 25-day sample. Treat these as candidate
hypotheses for a longer window or another exchange data source.

## Phase 4A.4 Candidate Hypothesis Validation

Frozen candidates were validated without threshold tuning:

- `long_oi_down_price_up`: baseline entry side is long, OI 1h change <= 0,
  price return 1h > 0.
- `short_oi_up_price_down`: baseline entry side is short, OI 1h change > 0,
  price return 1h <= 0.
- `abs_funding_z_ge_2`: abs funding z-score >= 2.

Validation script:

```bash
uv run python scripts/validate_market_intel_hypotheses.py \
  --symbol ETHUSDT \
  --interval 5m \
  --days 25 \
  --checkpoint checkpoints/channel_breakout_v2_1_balanced.pt \
  --market-intel data/market_intel/ETHUSDT_1h_derivatives_25d.parquet \
  --random-trials 100 \
  --output-md reports/market_intel_hypothesis_validation_ETHUSDT_25d.md \
  --output-csv data/research/market_intel_hypothesis_validation_ETHUSDT_25d.csv
```

The same script was also run for BTCUSDT and SOLUSDT after generating their
25-day Binance funding/OI frozen tables.

Summary:

| symbol | candidate | count | mean | median | win rate | baseline win rate | min count? | random as bad/worse |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | `long_oi_down_price_up` | `17` | `-9.70` | `-11.01` | `29.4%` | `37.9%` | no | `6 / 100` |
| BTCUSDT | `short_oi_up_price_down` | `8` | `-3.22` | `-6.14` | `37.5%` | `37.9%` | no | `54 / 100` |
| BTCUSDT | `abs_funding_z_ge_2` | `18` | `-0.44` | `-4.90` | `38.9%` | `37.9%` | no | `70 / 100` |
| ETHUSDT | `long_oi_down_price_up` | `13` | `-25.76` | `-20.05` | `7.7%` | `41.8%` | no | `0 / 100` |
| ETHUSDT | `short_oi_up_price_down` | `15` | `-10.72` | `-2.32` | `40.0%` | `41.8%` | no | `13 / 100` |
| ETHUSDT | `abs_funding_z_ge_2` | `28` | `-4.25` | `-4.19` | `46.4%` | `41.8%` | yes | `36 / 100` |
| SOLUSDT | `long_oi_down_price_up` | `5` | `+1.07` | `+0.50` | `60.0%` | `45.5%` | no | `60 / 100` |
| SOLUSDT | `short_oi_up_price_down` | `6` | `-2.10` | `-4.02` | `33.3%` | `45.5%` | no | `49 / 100` |
| SOLUSDT | `abs_funding_z_ge_2` | `38` | `-8.00` | `-9.61` | `31.6%` | `45.5%` | yes | `6 / 100` |

Time-split read:

- `long_oi_down_price_up` was negative in both halves for BTC and ETH, but
  sample counts were below the minimum `20`-trade bar.
- `short_oi_up_price_down` was negative in both halves for ETH, but weak or
  too sparse elsewhere.
- `abs_funding_z_ge_2` failed stability: ETH had no first-half samples, BTC
  improved in the second half, and SOL was strongly negative in the first half
  but positive in the second half.

Volatility-only control:

- The OI/price candidates were not fully explained by selecting the same number
  of highest-realized-vol trades; ETH and SOL volatility-only buckets were
  positive for the OI/price candidates.
- BTC high-vol buckets were also negative, so BTC alone cannot prove an
  independent derivatives signal.
- `abs_funding_z_ge_2` is mixed: SOL looks worse than volatility-only, but BTC
  and ETH do not support a stable cross-symbol rule.

Decision:

- Do not promote any candidate into a production overlay rule.
- Do not tune OI/funding thresholds from this sample.
- Keep `long_oi_down_price_up` and `abs_funding_z_ge_2` as frozen watchlist
  hypotheses for larger windows or alternate exchange-derived OI datasets.
- Current derivatives overlay remains an experimental volatility throttle, not
  a demonstrated edge.
