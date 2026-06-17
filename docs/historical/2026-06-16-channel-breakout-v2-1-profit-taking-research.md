# ChannelBreakout v2.1 止盈优化研究备忘录

日期: 2026-06-16

## 背景

`channel_breakout_v2_1_balanced` 是当前冻结的 v2.1 基线。它不是单一策略参数，而是
`regime_permission_channel_breakout`:

- BULL: `375/432` long-only, 连续 3 个已完成日线 close < EMA50 时禁用/关闭多头。
- BEAR: `375/432` dual, 多空都允许。
- NEUTRAL: `375/432` directional, close 与 EMA50 slope 同向时才允许对应方向。

它的核心优势来自 ChannelBreakout 的趋势捕捉，尤其是 BEAR regime。现有 OOS 指标较强，但止盈/锁盈机制偏弱:

- checkpoint 参数里没有显式启用 `take_profit_pct`。
- `ChannelBreakoutTrendStrategy` 构造函数虽然接收 `take_profit_pct`, `stop_loss_pct`, `max_hold_bars`，但当前信号生成逻辑主要使用反向突破、可选 `exit_lookback`、可选 `emergency_stop_pct`。
- live 层可以感知部分 TP/SL 字段，但如果回测信号层没有同等语义，直接在 checkpoint 里加固定 TP 会造成研究/实盘语义不一致。

因此，止盈优化不能只靠“往 checkpoint 加 take_profit_pct”。需要先把 exit 语义定义清楚，再做候选研究。

## 当前退出机制的问题

当前 v2.1 balanced 的退出更像“趋势失效退出”，不是“利润管理退出”:

1. **利润回吐不可控**
   反向通道突破通常比较慢。趋势已经贡献较大浮盈后，若市场快速反抽，策略可能等到反向突破才离场，MFE 到最终 PnL 的转化率不稳定。

2. **固定止盈会伤害趋势策略**
   如果直接加 5% 或 8% 固定止盈，可能更早锁住小利润，但会截断 ChannelBreakout 最有价值的大趋势尾部。它可能提高胜率，却降低总收益和 Sharpe。

3. **NEUTRAL 弱窗口更像假突破问题**
   现有诊断显示，最差 6 个月窗口由 NEUTRAL 主导。这个问题未必能靠止盈完全解决，因为亏损来源可能是反复假突破，而不是单笔盈利回吐。

4. **BEAR 是 alpha 核心，不能过度收紧**
   v2.1 的收益高度集中在 BEAR。任何 exit 优化都必须证明不会破坏 BEAR 捕捉大趋势的能力。

## 候选方向

### 方向 A: 调整 `exit_lookback`

用更短的 Donchian exit channel 退出趋势，例如持有多头时跌破较短周期低点就平仓，持有空头时突破较短周期高点就平仓。

优点:

- 已被 `ChannelBreakoutTrendStrategy` 支持，改动最小。
- 与通道突破体系一致，不引入额外指标。
- 可以直接按 regime 设置不同 `exit_lookback`。

缺点:

- 它是趋势退出，不是真正的利润锁定。
- 过短会频繁扫出，过长又接近当前行为。
- 对快速利润回吐的响应仍可能偏慢。

适合做为基线对照，但不建议作为第一主线。

### 方向 B: 固定止盈 + 固定止损

在单笔持仓达到固定收益阈值时平仓，例如 `take_profit_pct=0.08`; 达到固定亏损阈值时止损，例如 `stop_loss_pct=0.10`。

优点:

- 逻辑简单，容易解释。
- 有助于提升胜率和减少单笔回吐。
- 项目已有 `exit_logic_variant` schema 预留了 `take_profit_pct` / `stop_loss_pct`。

缺点:

- 固定止盈天然不适合趋势突破策略，容易砍掉最重要的右尾收益。
- 参数对市场波动率敏感，ETH 5m 在不同时期的 8% 含义不同。
- 如果 TP/SL 只在 live 层生效，回测与实盘会不一致。

不建议作为主线，只适合做一个窄范围反证实验: 验证固定止盈是否确实损害趋势尾部。

### 方向 C: Profit-Lock / Trailing Exit

当浮盈达到启动阈值后，不立即止盈，而是开始跟踪最高浮盈。如果从峰值回吐超过一定比例或超过 ATR trailing distance，则平仓。

示例规则:

```text
long:
  unrealized_profit = close / entry_price - 1
  mfe = max(mfe, unrealized_profit)
  if mfe >= activate_profit_pct:
      exit if unrealized_profit <= mfe * (1 - giveback_ratio)
      or close <= highest_after_entry - atr_multiplier * ATR

short:
  unrealized_profit = entry_price / close - 1
  mfe = max(mfe, unrealized_profit)
  if mfe >= activate_profit_pct:
      exit if unrealized_profit <= mfe * (1 - giveback_ratio)
      or close >= lowest_after_entry + atr_multiplier * ATR
```

候选参数:

- `activate_profit_pct`: 0.06, 0.08, 0.12, 0.18
- `giveback_ratio`: 0.25, 0.35, 0.50
- `trailing_atr_multiplier`: 2.0, 3.0, 4.0
- `min_hold_bars_before_profit_lock`: 0, 72, 144

优点:

- 保留趋势右尾，不在刚盈利时过早退出。
- 浮盈变大后才保护利润，和“尽快翻倍但不要白白回吐”的目标更一致。
- 可以分 regime 设置强弱: BULL 宽松，BEAR 中等，NEUTRAL 更紧。

缺点:

- 需要在信号层明确实现，不能只改 checkpoint。
- 增加状态变量: entry price, MFE, highest/lowest after entry。
- 需要新的指标评估，比如 MFE capture ratio，不能只看收益和 Sharpe。

这是最值得继续研究的方向。

### 方向 D: 分批止盈 / 仓位缩放

达到第一档利润时减仓一部分，剩余仓位继续跟踪趋势。

优点:

- 理论上能兼顾现金落袋和趋势尾部。
- 比全平固定止盈更柔和。

缺点:

- 当前 `StrategyEvaluator` 是单一全仓 long/short/flat 模型，不支持部分仓位。
- 会牵涉交易模拟器、实盘下单、仓位状态和 checkpoint 结构，工程面更大。

不建议现在做。除非先改 evaluator 支持 position size，否则这个方向会把研究问题扩大成交易系统重构。

## 推荐研究方向

推荐先研究 **方向 C: Profit-Lock / Trailing Exit**。

理由:

1. 它正对“止盈不够优秀”的核心问题: 盈利后如何防止大幅回吐。
2. 它比固定止盈更适合 ChannelBreakout，因为不会主动截断刚开始的趋势。
3. 它可以自然接到现有 `exit_logic_variant` 研究族，不需要推翻 v2.1 balanced。
4. 它可以先做成可选 overlay，保证冻结基线完全不变。

## 研究边界

这轮研究只处理退出逻辑，不改 entry 和 regime permission:

- 不改 `entry_lookback=375`。
- 不改 `min_hold_bars=432`。
- 不改 BULL / BEAR / NEUTRAL 权限规则。
- 不覆盖 `checkpoints/channel_breakout_v2_1_balanced.pt`。
- 不修改 baseline oracle 指标。

目标是回答一个窄问题:

> 在保持 v2.1 balanced 入场和 regime 权限不变的情况下，profit-lock/trailing exit 能否提升利润捕获效率，并降低利润回吐，而不显著损害 BEAR 趋势收益？

## 评估指标

除了现有指标外，需要增加面向止盈质量的指标:

| 指标 | 目的 |
|------|------|
| OOS return | 总收益是否保持或提升 |
| OOS Sharpe | 收益路径是否更平滑 |
| OOS max drawdown | 是否降低深回撤 |
| Trades/year | 是否导致过度交易 |
| BEAR return | 是否保住核心 alpha |
| NEUTRAL rolling 6m worst return | 是否改善弱窗口 |
| MFE capture ratio | 单笔最大浮盈有多少被实际拿到 |
| Profit giveback pct | 从峰值浮盈到离场回吐了多少 |
| Time-to-2x | 是否更符合“尽快翻倍”的目标 |

`MFE capture ratio` 定义建议:

```text
if MFE > 0:
    capture = realized_trade_return / MFE
else:
    capture = 0
```

按交易和按权益曲线都可以算，第一版先用按交易口径。

## 初步实验矩阵

先控制候选数量，避免过拟合:

| 方案 | activate_profit_pct | giveback_ratio | trailing_atr_multiplier | 预期 |
|------|---------------------|----------------|-------------------------|------|
| PL-1 宽松 | 0.12 | 0.50 | 4.0 | 保留趋势，轻度防回吐 |
| PL-2 平衡 | 0.08 | 0.35 | 3.0 | 优先候选 |
| PL-3 紧凑 | 0.06 | 0.25 | 2.0 | 改善回撤，但可能损害收益 |
| PL-4 BEAR-only | 0.08 | 0.35 | 3.0 | 只作用于 BEAR，保护核心收益 |
| PL-5 NEUTRAL-tight | 0.06 | 0.25 | 2.0 | 针对 NEUTRAL 弱窗口 |

第一轮不超过 5 个候选，符合现有研究节奏。

## 实现草图

如果进入实现，建议新增可选 overlay，而不是直接改旧 checkpoint 行为:

1. 在 `ChannelBreakoutTrendStrategy` 或一个派生策略里实现 profit-lock 状态机。
2. 参数默认全部关闭，保证旧 checkpoint 行为不变。
3. 在 `exit_logic_variant` 中扩展字段:
   - `activate_profit_pct`
   - `giveback_ratio`
   - `trailing_atr_multiplier`
   - `min_hold_bars_before_profit_lock`
4. 在研究脚本中把 v2.1 balanced 参数作为 parent，生成 5 个候选。
5. 用 oracle/replay 比较 baseline 与候选。

## 风险与注意事项

1. **过早退出风险**
   Profit-lock 参数过紧会把趋势策略变成短线策略，可能降低总收益。

2. **实盘一致性风险**
   必须确保回测信号、signal-only、OKX live 看到的是同一套 exit 语义。

3. **状态恢复风险**
   Profit-lock 依赖 entry price、MFE、highest/lowest after entry。实盘重启后需要从历史 K 线和持仓状态重建这些字段，否则可能错误退出。

4. **NEUTRAL 问题可能不是止盈能解决的**
   如果 NEUTRAL 的亏损主要来自假突破入场，exit 优化只能缓解，不能根治。后续仍可能需要 ADX / 多时间框架 / 波动过滤。

## 下一步建议

下一步建议只推进一个方向:

**Profit-Lock / Trailing Exit overlay**。

先写一个非常小的技术设计，明确状态机和参数默认值，再决定是否实现。不要先跑大规模搜索；先做 3 到 5 个手工候选，让结果告诉我们这个方向是否值得扩大。
