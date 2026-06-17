# ChannelBreakout v2.1 exp_0024 风险归因研究归档

日期: 2026-06-16

## 当前结论

`exp_0024_mature_guard_bear_cooldown` original 仍是当前主父候选，但研究线暂时收束，不继续追加局部风控补丁。

> 当前问题不是一个单一 bug，而是两个不同风险源叠加: 2024 的 BULL re-entry 接刀，以及 2022-2023 的 BEAR churn。已测试的小补丁投入回报不理想，暂不继续深挖。

## 主父候选

文件:

- `research_workspace/candidates/exp_0024_mature_guard_bear_cooldown.json`

定位:

- parent: `channel_breakout_v2_1_balanced`
- strategy: `exit_logic_variant`
- status: `research_only`
- 核心组合: Mature Trend Guard + BEAR cooldown

关键参数:

```text
mature_trend_exit:
  enabled: true
  activate_mfe_pct: 1.2
  daily_ema_period: 20
  use_completed_daily_bar: true

bear_cooldown:
  enabled: true
  loss_streak: 2
  cooldown_bars: 864  # 3d
  scope: bear_entry_trades
```

重要边界:

- 保留 `exp_0024 original` 作为主父候选。
- `shift-fix` 只作为 semantic baseline，不升级为性能主线。
- 不改 permission 本体。
- 不动 MTG。
- 不处理 NEUTRAL fake breakout。
- 不做普通止损/止盈网格。

## shift-fix 结论

`shift-fix` 是语义正确化，不是性能优化。

| version | IS max DD | peak -> trough |
|---|---:|---|
| exp_0024 original | -48.63% | 2022-01-24 14:05 -> 2022-05-04 19:05 |
| exp_0024 shift-fix | -54.35% | 2021-05-12 06:00 -> 2021-07-25 14:45 |

解释:

- old behavior 多了一日延迟，语义上不够干净。
- 但这个延迟在 2021 强牛深回调中形成了有利缓冲。
- full shift-fix 把 BULL permission episode 前移，导致更早重新暴露到 falling-knife long。
- 因此 shift-fix 保留为语义对照，不并入主线。

## exp_0024 original rolling-negative 归因

数据:

- `ETHUSDT_5m_2600d.parquet`
- IS/OOS split: 70/30
- split time: `2024-06-06 14:25`
- original 通过 old daily lookup monkeypatch 复现。

整体:

| scope | max DD | peak -> trough |
|---|---:|---|
| IS / Full | -48.63% | 2022-01-24 14:05 -> 2022-05-04 19:05 |

### Worst rolling windows

| rank | window | start | end | return | max DD | trades | dominant | overlaps IS max DD |
|---:|---|---|---|---:|---:|---:|---|---|
| 1 | 6m | 2024-03-14 | 2024-09-12 | -42.11% | -43.55% | 15 | BEAR SHORT | no |
| 2 | 6m | 2024-03-13 | 2024-09-11 | -41.80% | -44.47% | 14 | BULL LONG | no |
| 3 | 6m | 2024-03-11 | 2024-09-09 | -41.24% | -44.91% | 14 | BULL LONG | no |
| 1 | 12m | 2022-07-21 | 2023-07-21 | -35.80% | -42.78% | 53 | BEAR SHORT | no |
| 2 | 12m | 2022-07-20 | 2023-07-20 | -35.42% | -45.46% | 53 | BEAR SHORT | no |
| 3 | 12m | 2022-07-19 | 2023-07-19 | -35.32% | -45.46% | 53 | BEAR SHORT | no |

结论:

- rolling-negative 与 IS max DD 不是同一段。
- worst 6m 是 2024 BULL re-entry + BEAR churn 混合问题。
- worst 12m 是 2022-2023 BEAR churn 结构性磨损。

## 2024 BULL re-entry gate v1

目标:

- 只处理 BULL risk reopen 后的新 long。
- 不影响已有 BULL long。
- 不影响 BEAR / NEUTRAL。
- 不动 MTG + BEAR cooldown。

测试方向:

- delay gate: 1d / 2d / 3d / 5d
- reclaim gate: EMA20 / channel midline
- momentum gate: 1d / 3d momentum

精确回放结果:

| version | Full Return | Full DD | IS Return | OOS Return | OOS DD | Worst 6m | Worst 12m | 2024 focus |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| original | +21620.43% | -48.63% | +4351.99% | +387.40% | -31.17% | -42.11% | -35.80% | -42.11% |
| delay_5d | +19264.24% | -48.63% | +3518.06% | +434.68% | -36.65% | -40.55% | -40.27% | -40.55% |
| EMA20 reclaim | +20493.88% | -48.63% | +4131.38% | +386.21% | -33.04% | -42.54% | -36.80% | -42.54% |

结论:

- `delay_5d` 对 2024 窗口只小幅改善，但 OOS DD 与 worst 12m 变差。
- EMA20 reclaim 没有解决接刀，反而略差。
- BULL re-entry gate v1 不升级。

## BEAR cooldown audit

窗口:

- `2022-07-21 -> 2023-07-21`

现有 cooldown 表现:

| scope | BEAR trades | BEAR pnl |
|---|---:|---:|
| cooldown 前 | 56 | -14,085 |
| cooldown 后 | 45 | -13,063 |

触发与阻挡:

| item | value |
|---|---:|
| cooldown triggers | 10 |
| blocked signal bars | 6340 |
| changed / blocked pre-trades | 11 |
| changed / blocked hypothetical pnl | -735 |
| blocked winners / losers | 5 / 6 |

方向归因:

| post-cooldown BEAR | trades | pnl | win rate | low-MFE losses | low-MFE pnl |
|---|---:|---:|---:|---:|---:|
| LONG | 22 | +29,918 | 50.0% | 7 | -123,463 |
| SHORT | 23 | -42,981 | 30.4% | 13 | -178,624 |

结论:

- 现有 BEAR cooldown 方向不算错，但力度和定位很弱。
- 它减少了交易数，但挡掉的盈利与亏损接近抵消。
- BEAR 主要坏交易是 low-MFE loss: 几乎没给利润空间就快速扩大 MAE。

## BEAR low-MFE same-direction cooldown v1

实验名建议:

- `exp_0024_bear_low_mfe_same_direction_cooldown_overlay`

触发设想:

```text
if entry_regime == BEAR
and trade_pnl < 0
and trade_MFE < mfe_threshold:
    block same_direction BEAR entries for cooldown_days
```

测试矩阵:

- `mfe_threshold`: 2%, 3%, 4%
- `cooldown_days`: 2d, 3d, 5d
- scope: same-direction only
- ablation: both-direction cooldown

轻量矩阵结论:

- 2d / 3d: 触发很多次，但几乎不挡后续同方向 entry。
- 5d: 开始挡交易，但 worst 12m 变差。
- both-direction 3d: 短冷却下也基本没有实际挡住交易。

精确确认:

| version | Full DD | OOS DD | Worst 12m | 2022-2023 BEAR pnl | blocked pnl | conclusion |
|---|---:|---:|---:|---:|---:|---|
| original | -48.63% | -31.17% | -35.80% | -13,063 | 0 | baseline |
| same_mfe2_cd5d | -50.53% | -31.17% | -37.65% | -19,912 | +1,378 | worse |
| same_mfe3_cd5d | -51.44% | -31.17% | -39.06% | -25,016 | -8,499 | worse |

结论:

- low-MFE 是有效诊断特征，但 same-direction cooldown 的作用域错了。
- BEAR churn 不是同方向连续复犯主导，而是多空来回翻转主导。
- same-direction cooldown 挡不到真正伤人的下一笔；拉长到 5d 后又误伤路径。
- 该方向暂时停止，不继续投入。

## 已排除方向

| 方向 | 状态 | 原因 |
|---|---|---|
| shift-fix 主线化 | 排除 | 语义更干净，但 IS DD 重新恶化到 2021 大趋势回吐段 |
| naive block-only / close-only permission split | 排除 | 破坏 position-aware 语义，交易数异常膨胀 |
| BULL re-entry delay gate | 排除 | 只小幅改善 2024，伤 OOS DD / worst 12m |
| BULL EMA20 reclaim gate | 排除 | 未改善 2024 接刀，指标略差 |
| BULL momentum reclaim gate | 排除 | 轻量扫描无有效改善 |
| BEAR low-MFE same-direction cooldown | 排除 | 诊断对，但 scope 错，worst 12m 与 Full DD 变差 |
| 普通 emergency stop | 降优先级 | 已验证对最大 DD 帮助弱，且伤收益 |
| 分批止盈 | 暂缓 | 当前 evaluator 只支持全仓 long/short/flat，工程面过大 |

## 当前研究状态

保留:

- `exp_0024_mature_guard_bear_cooldown` original 作为主父候选。
- `shift-fix` 作为 semantic baseline。
- Profit-Lock / Mature Trend Guard / BEAR cooldown 的实现与测试资产保留为研究分支。

暂停:

- BULL re-entry gate v1。
- BEAR low-MFE same-direction cooldown。
- NEUTRAL fake breakout。
- stateful BULL risk-off episode。
- 普通止损/止盈网格。

下一步如恢复研究，建议先不要直接继续加补丁。更合理的是重新定义 BEAR churn 问题:

```text
BEAR low-MFE failed-breakout 后，坏交易更像 immediate direction flip / regime 内震荡追逐，
不是 same-direction 重复失败。
```

如果未来继续，可只做小型诊断，不急于候选化:

- low-MFE loss 后的 immediate direction flip 统计。
- failed breakout 后下一笔 opposite-direction trade 的 PnL 分布。
- BEAR 中 breakout confirmation 强弱与 MFE/MAE 的关系。

## 一句话归档

`exp_0024 original` 仍是主父候选；shift-fix、BULL re-entry gate v1、BEAR low-MFE same-direction cooldown 均不升级。当前阶段研究已证明 rolling-negative 来自多个局部风险源，但继续追加小风控补丁的投入回报不佳，本线归档暂停。
