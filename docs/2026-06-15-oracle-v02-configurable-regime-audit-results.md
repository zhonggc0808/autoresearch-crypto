# Phase 5A Results — Oracle v0.2 Configurable Regime Audit

Date: 2026-06-15
Status: **Complete**

## Oracle v0.2 — Acceptance

| Check | Result | Detail |
|-------|--------|--------|
| `--fast-days`/`--slow-days` CLI | ✅ Implemented | Defaults 50/200, validation fast>0, slow>0, fast<slow |
| Parameter threading | ✅ 3 call sites | `_generate_v21_signals`, Phase 3B filter, main pre-compute |
| v0.2 metadata in output JSON | ✅ | `oracle.version`, `oracle.regime_filter.fast_days/slow_days` |
| v0.1.0 parity | ✅ Pass | OOS/IS return/dd/sharpe 1e-8, regime returns 1e-6 |
| 16/16 test suite | ✅ Pass | All existing tests + new `test_v02_default_parity` |

## Sensitivity Results

### OOS Metrics per Regime Setting

| Setting | OOS Return | OOS DD | OOS Sharpe | Exec Parity | 12m Min Return |
|---------|------------|--------|------------|-------------|----------------|
| **50/200 (default)** | +156.6% | -33.8% | 2.35 | 0.999 | -13.0% |
| 20/100 (fast) | +115.3% | -43.7% | 1.71 | 0.999 | -29.6% |
| 100/300 (slow) | +6.7% | -42.7% | 0.11 | 0.999 | -7.1% |

### Deltas vs Default (50/200)

| Metric | 20/100 Δ | 100/300 Δ |
|--------|----------|-----------|
| OOS Return | -41.25 pp | -149.85 pp |
| OOS DD | +9.86 pp (worse) | +8.81 pp (worse) |
| OOS Sharpe | -0.64 | -2.24 |
| Bear Regime Return | +424% | -287% |

### Interpretation

- **50/200 remains optimal** for ETHUSDT 5m under the v2.1 regime framework.
- **20/100** detects bear regimes earlier (bear return +424%), but produces higher drawdown and lower overall Sharpe — too sensitive, catches noise.
- **100/300** is too slow: OOS Sharpe collapses to 0.11, nearly flat.
- **Decision:** Reject 20/100 and 100/300 as baseline alternatives. Keep default 50/200.

## Pass/Fail Criteria

| Criterion | Status | Notes |
|-----------|--------|-------|
| v0.2 default 50/200 passes parity vs v0.1.0 | ✅ Pass | return/dd/sharpe 1e-8, regime returns 1e-6 |
| Alternative settings reported as research only | ✅ Pass | No promotion to baseline |
| Track A / demo / live untouched | ✅ Pass | Verified by no-touch list |
| v2.1 balanced remains sole demo/live baseline | ✅ Pass | No checkpoint or live file modified |

## No-Touch Confirmation

| File/Directory | Expected | Actual |
|----------------|----------|--------|
| `dex/regime_permissions.py` | Not modified | ✅ Clean |
| `dex/regime_filter.py` | Not modified | ✅ Clean |
| `dex/config.py`, `dex/scoring.py` | Not modified | ✅ Clean |
| `checkpoints/channel_breakout_v2_1_balanced.pt` | Not modified | ✅ SHA256 verified |
| `live_nado_quant.py`, `live_okx_quant.py`, `live_binance_quant.py` | Not modified | ✅ Clean |
| `dex/live/common.py` | Not modified | ✅ Clean |
| `start_bitget_demo_v21*.bat` | Not modified | ✅ Clean |

## Files Changed (Phase 5A)

| File | Change | Lines |
|------|--------|-------|
| `scripts/research_oracle.py` | CLI args, threading, v0.2 metadata | +51/-10 |
| `tests/test_research_oracle.py` | Parity test + version assertion fix | +72/+1 |
| `scripts/regime_sensitivity_sweep.py` | New: subprocess-only sweep orchestration | +241 |
| `research_workspace/.gitignore` | Ignore `regime_sensitivity/` output | +3 |

## Promotion Impact

**None.** Phase 5A was a research sensitivity audit only. No baseline change,
no demo/live routing change, no checkpoint replacement.

## Phase 5B (Not Started)

Future step if oracle configuration grows to 4+ independent dimensions.
Current oracle remains at v0.2 with two configurable parameters (fast_days, slow_days).

---

## Summary

```
Phase 5A status:      complete
Oracle v0.2:          accepted
Default regime:       keep 50/200
Sensitivity result:   20/100 and 100/300 rejected as baseline alternatives
Promotion impact:     none
Track A impact:       none
Phase 5B:             not started
```
