# exp_0218 Fibonacci Entry Confirmation

Scope: research-only probe. It approximates replacing v2.2 Bollinger
breakout confirmation with causal Fibonacci extension windows, then
applies the existing TimesFM exp_0068 gate. No live/config/checkpoint/
oracle/strategy/scoring/execution behavior changed.

## Replay Matrix

| variant | raw OOS | TimesFM OOS | TimesFM DD | rolling12 | trades | fib blocks | top20 | worst20 | net action | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| baseline_bb_timesfm | 501.71% | 581.55% | -43.55% | -19.93% | 221 | 0 | 0 | 0 | 0.00% | BASELINE |
| no_bollinger_raw | 419.83% | 488.81% | -43.20% | -27.71% | 222 | 0 | 0 | 0 | 0.00% | CONTROL |
| fib_ext_1p000_1p618 | 499.61% | 579.17% | -44.14% | -27.59% | 181 | 45 | 8 | 4 | -271.43% | REJECT |
| fib_ext_1p000_1p382 | 479.91% | 557.00% | -44.14% | -27.07% | 178 | 50 | 8 | 5 | -261.32% | REJECT |
| fib_ext_1p000_1p236 | 388.42% | 448.72% | -44.14% | -27.07% | 174 | 55 | 9 | 6 | -268.49% | REJECT |
| fib_ext_1p236_1p618 | 22.77% | 23.77% | -22.08% | -20.96% | 7 | 237 | 19 | 18 | -795.48% | REJECT |
| fib_not_over_1p618 | 419.83% | 488.81% | -43.20% | -27.71% | 222 | 0 | 0 | 0 | 0.00% | REJECT |

## Event Census On Current Baseline Trades

| label | N | N% | years | top20 | worst20 | pnl sum | mean return | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| fib_extension_lt_1p236 | 214 | 96.83% | 8 | 19 | 19 | 879.16% | 4.11% | OBSERVE |
| fib_extension_1p236_1p618 | 7 | 3.17% | 4 | 1 | 1 | 7.40% | 1.06% | REJECT |
| fib_extension_gt_1p618 | 0 | 0.00% | 0 | 0 | 0 | 0.00% |  | REJECT |

## Read

- `no_bollinger_raw` is the control showing what happens when BB confirmation is removed.
- A Fibonacci replacement must beat the current BB+TimesFM baseline on OOS/rolling12 and avoid top-winner damage.
- These rows are research diagnostics only; they do not authorize checkpoint/config/live changes.

Evidence:

- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_summary.csv`
- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_events.csv`
- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_paired_blocks.csv`
- `research_workspace/diagnostics/exp_0218_fibonacci_entry_confirmation/exp_0218_experiment.json`
