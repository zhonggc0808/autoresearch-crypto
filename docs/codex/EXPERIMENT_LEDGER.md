# Experiment Ledger

Last updated: 2026-06-27

This ledger records durable experiments and verdicts. It is not a full dump of every generated artifact.

## Reporting Columns

Use these columns for new rows:

| Column | Meaning |
|---|---|
| ID | Experiment or candidate id |
| Variant | Strategy/filter/gate variant |
| Params | Key parameters |
| Sample | Data window and execution semantics |
| Result | Main OOS/return/DD/rolling evidence |
| Winner damage | Whether top winners were blocked or cut |
| Verdict | `REJECT`, `OBSERVE`, `SHADOW_CANDIDATE`, `KEEP_MAIN`, `CLOSED`, or `PAUSED` |
| Evidence | Files that reproduce or summarize the result |

## Durable Ledger

| ID | Variant | Params | Sample | Result | Winner damage | Verdict | Evidence |
|---|---|---|---|---|---|---|---|
| baseline-v2-375-432 | ChannelBreakout naked | `entry_lookback=375`, `min_hold_bars=432` | ETH 5m, long history | High return but max DD around `-63.57%` in older run; too risky naked | Not applicable | Historical reference | `AGENTS.md` history, `docs/current/*` |
| v2-regime-filter | ChannelBreakout v2 regime filter | regime filter enabled | ETH 5m, historical | Return about `+262.05%`, DD about `-47.51%`; improved return but DD too high | Not fully recorded here | Historical reference | `docs/baseline/*` |
| v2-dd-guard-25-15 | v2 + drawdown guard | max DD guard `0.25`, recovery `0.15` | ETH 5m, historical | Return about `+141.07%`, DD about `-29.66%`; only older DD-compliant version | Not fully recorded here | Historical reference | `AGENTS.md` history |
| v2.1-balanced | ChannelBreakout v2.1 balanced | balanced regime-permission/safe-exec | ETH 5m | Return about `+252.24%`, DD about `-33.60%`; safe-exec around `+257.09%/-33.24%` | Not fully recorded here | Historical oracle baseline | `docs/baseline/*`, `research_workspace/baselines/*` |
| exp_0045 | volatility gate | ATR/close p95/p97/p99 sweeps | Oracle/filter family research | Fee improved but rolling12m and OOS worsened | Blocked profitable exposure | PAUSED | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| exp_0046 | cooldown after drawdown | close-drawdown threshold plus cooldown | Oracle/filter family research | All key metrics worsened; recovery entries were blocked | Yes, recovery entries cut | CLOSED | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| exp_0053 | neutral regime entry block | block entries when NEUTRAL | Oracle/filter family research | OOS safe fell to about `+100.45%`; rolling12m worsened; IS DD broke hard threshold | Yes, blanket block cut many winners | REJECT | `docs/current/codex_handoff_2026-06-16_filter_family_closure.md` |
| exp_0068 | TimesFM conservative gate | `context=1024`, `horizon=72`, `min_edge=-1%`, `risk_floor=5%` | ETH 5m 2600d, entry/reversal gate | Improved OOS and reduced DD versus same-mouth no-gate baseline in prior matrix; current main gate | Blocks some winners but catches key tail losses | KEEP_MAIN | `research_workspace/llm_candidates/exp_0068.json`, `configs/live/timesfm_gate_exp_0068.json` |
| exp_0070 | TimesFM aggressive reference | stronger EV/rank style gate | Multi-window gate research | Strong upside and fee-stress performance, but fragile 1300d and lower retention | Higher risk of over-blocking | OBSERVE | `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md` |
| exp_0092 | Final gate shortlist | TimesFM 0068 vs TimesFM 0070 vs Moirai2 `-2/4` | next-bar open, multi-window and yearly reset | Moirai2 won 4/4 multi-window; TimesFM 0068 kept main due validation and 2021 tail catch | Moirai missed two TimesFM-only 2021 tail losers | KEEP_MAIN + SHADOW_CANDIDATE | `research_workspace/diagnostics/exp_0092_final_gate_shortlist_compare.md` |
| exp_0093 | Moirai2 primary shadow | `context=1024`, `horizon=72`, `min_edge=-2%`, `risk_floor=4%` | Research shadow plus demo-live runtime config | Candidate has 4/4 multi-window wins and 7/8 positive years in shortlist; fallback remains TimesFM 0068 | Known risk: misses 2021 TimesFM-only tail losses | SHADOW_CANDIDATE | `research_workspace/llm_candidates/exp_0093_moirai2_primary_shadow.json`, `configs/live/moirai2_gate_exp_0093.json` |
| exp_0129 | Gate focused deep dive | TimesFM `-1/5` vs Moirai `-2/3` | v2.2 base, next-bar open, focused attribution | Moirai `-2/3` had cleaner DD repair; TimesFM had stronger OOS safe in focused run | Both block `1/20` top winners and `4/20` worst losers in headline comparison | OBSERVE | `research_workspace/diagnostics/exp_0129_gate_focused_deep_dive.md` |
| exp_0130 | Gate year/DD slices | TimesFM `-1/5` vs Moirai `-2/3` vs live Moirai `-2/4` | Year and drawdown bucket attribution | Moirai live `-2/4` was helpful in 7 years and harmful in 1 by help estimate; diagnostic only | Needs per-trade review before promotion | OBSERVE | `research_workspace/diagnostics/exp_0130_gate_year_dd_slices.md` |

## Update Rule

When adding a row:

- Prefer one durable summary row over many raw artifact rows.
- Include exact evidence files.
- If a branch is rejected, state why so it is not reopened casually.
- If a branch is only diagnostic, say so explicitly.
