# ChannelBreakout v2.1 Profit-Lock 技术设计

日期: 2026-06-16

## 目标

在不修改 `channel_breakout_v2_1_balanced` 冻结基线行为的前提下，为 v2.1 研究分支实现一套可选的优秀止盈方式:

**Profit-Lock / Trailing Exit overlay**。

它的目标不是简单固定止盈，而是在浮盈达到一定阈值后开始保护利润，让趋势继续奔跑，同时减少大幅利润回吐。

## 非目标

- 不覆盖 `checkpoints/channel_breakout_v2_1_balanced.pt`。
- 不改变 baseline oracle 的既有指标。
- 不改 v2.1 的 regime permission 规则。
- 不实现分批止盈或部分仓位，当前 evaluator 仍是全仓 long/short/flat 模型。
- 第一阶段不做大规模参数搜索。

## 设计原则

1. **默认关闭**
   所有新增参数默认关闭或无效果，旧 checkpoint 和旧回测必须 bit-compatible 或行为一致。

2. **回测和实盘语义一致**
   止盈必须在信号层产生 `0=close`，不能只在 live 层挂 TP 单。live 层可以执行信号，但不能拥有另一套独立止盈逻辑。

3. **先全平，不做分批**
   先用现有信号编码表达退出，避免触碰仓位大小模拟器。

4. **分 regime 可配置**
   v2.1 的 alpha 分布不均衡，Profit-Lock 必须允许 BULL / BEAR / NEUTRAL 不同强度。

## 新增参数

建议扩展 `exit_logic_variant` 的 `exit_logic` 配置:

```json
{
  "exit_lookback": 0,
  "take_profit_pct": 0.0,
  "stop_loss_pct": 0.0,
  "max_hold_bars": 0,
  "trailing_stop": true,
  "profit_lock": {
    "enabled": true,
    "activate_profit_pct": 0.08,
    "giveback_ratio": 0.35,
    "trailing_atr_multiplier": 3.0,
    "atr_period": 14,
    "min_hold_bars_before_lock": 72
  }
}
```

字段含义:

| 字段 | 含义 | 默认 |
|------|------|------|
| `enabled` | 是否启用 Profit-Lock | `false` |
| `activate_profit_pct` | 浮盈达到多少后开始锁盈 | `0.0` |
| `giveback_ratio` | 允许从 MFE 回吐的比例 | `1.0` |
| `trailing_atr_multiplier` | ATR 追踪止盈距离 | `0.0` |
| `atr_period` | ATR 周期 | `14` |
| `min_hold_bars_before_lock` | 入场后至少持有多少 bar 才允许锁盈退出 | `0` |

`giveback_ratio=0.35` 的含义:

- 若最大浮盈 `MFE=20%`
- 当前浮盈回落到 `20% * (1 - 0.35) = 13%` 或更低
- 触发 Profit-Lock 平仓

## 状态机

每个持仓维护以下状态:

```text
position
entry_bar
entry_price
highest_after_entry
lowest_after_entry
mfe_pct
profit_lock_active
```

### 入场

当策略从空仓进入多头或空头:

```text
entry_bar = i
entry_price = close[i]
highest_after_entry = high[i]
lowest_after_entry = low[i]
mfe_pct = 0
profit_lock_active = false
```

### 多头持仓

每根 K 线更新:

```text
highest_after_entry = max(highest_after_entry, high[i])
unrealized_profit = close[i] / entry_price - 1
mfe_pct = max(mfe_pct, highest_after_entry / entry_price - 1)
bars_held = i - entry_bar

if bars_held >= min_hold_bars_before_lock and mfe_pct >= activate_profit_pct:
    profit_lock_active = true

giveback_exit = profit_lock_active and unrealized_profit <= mfe_pct * (1 - giveback_ratio)
atr_exit = profit_lock_active and trailing_atr_multiplier > 0 and close[i] <= highest_after_entry - trailing_atr_multiplier * atr[i]

if giveback_exit or atr_exit:
    signal = 0
    position = 0
```

### 空头持仓

每根 K 线更新:

```text
lowest_after_entry = min(lowest_after_entry, low[i])
unrealized_profit = entry_price / close[i] - 1
mfe_pct = max(mfe_pct, entry_price / lowest_after_entry - 1)
bars_held = i - entry_bar

if bars_held >= min_hold_bars_before_lock and mfe_pct >= activate_profit_pct:
    profit_lock_active = true

giveback_exit = profit_lock_active and unrealized_profit <= mfe_pct * (1 - giveback_ratio)
atr_exit = profit_lock_active and trailing_atr_multiplier > 0 and close[i] >= lowest_after_entry + trailing_atr_multiplier * atr[i]

if giveback_exit or atr_exit:
    signal = 0
    position = 0
```

### 与已有退出规则的优先级

建议顺序:

1. `emergency_stop_pct`
2. Profit-Lock / ATR trailing exit
3. `exit_lookback`
4. 反向 breakout flip
5. hold

原因:

- 灾难止损优先级最高。
- Profit-Lock 是利润保护，应早于慢速通道退出。
- 反向 breakout flip 仍保留趋势策略核心。

## 代码落点

### 第一选择: `ChannelBreakoutTrendStrategy` 可选参数

文件: `dex/strategies/channel_breakout.py`

新增构造参数:

```python
profit_lock_enabled: bool = False
profit_lock_activate_pct: float = 0.0
profit_lock_giveback_ratio: float = 1.0
profit_lock_atr_multiplier: float = 0.0
profit_lock_atr_period: int = 14
profit_lock_min_hold_bars: int = 0
```

优点:

- 旧策略默认行为不变。
- checkpoint loader 已经会过滤 constructor 参数，新增字段可以自然进入策略实例。
- 回测、oracle、live 只要使用同一策略类，就共享语义。

注意:

- 需要把 `atr_window` 条件扩展为 `breakout_atr_buffer > 0 or profit_lock_atr_multiplier > 0`。
- 如果 `profit_lock_enabled=False`，不应计算 ATR 或改变 warmup。

### 第二选择: 新增派生类

文件: `dex/strategies/profit_lock_channel_breakout.py`

新增 `ProfitLockChannelBreakoutStrategy(ChannelBreakoutTrendStrategy)`。

优点:

- 不污染原始类。
- 更容易和冻结基线隔离。

缺点:

- 需要注册新别名、checkpoint loader、live profile。
- v2.1 regime routing 当前显式实例化 `ChannelBreakoutTrendStrategy`，接入成本更高。

推荐第一选择: 在原策略内增加默认关闭的可选参数。

## v2.1 配置接入

v2.1 checkpoint 当前是:

```text
strategy_type = regime_permission_channel_breakout
```

Profit-Lock 研究候选可以使用:

```text
strategy_type = exit_logic_channel_breakout
```

并保留原始:

- `regime_change_policy`
- `regime_filter`
- `bull.strategy_params`
- `bear.strategy_params`
- `neutral.strategy_params`
- `permission`

转换规则:

1. 从 `channel_breakout_v2_1_balanced_params.json` 复制 BULL/BEAR/NEUTRAL。
2. 在每个 regime 的 `strategy_params` 注入 Profit-Lock 参数。
3. 或者在 replay/oracle 构建策略实例时，把 top-level `exit_logic.profit_lock` 展开到三个 regime。

推荐第二种: top-level `exit_logic` 控制 overlay，避免重复写入三个 regime。

## 研究候选

第一轮只做 5 个手工候选:

| ID | 作用范围 | activate | giveback | ATR mult | min hold | 目的 |
|----|----------|----------|----------|----------|----------|------|
| PL-1 | all | 0.12 | 0.50 | 4.0 | 72 | 宽松保护，不伤趋势 |
| PL-2 | all | 0.08 | 0.35 | 3.0 | 72 | 平衡方案 |
| PL-3 | all | 0.06 | 0.25 | 2.0 | 72 | 紧保护，检验回撤改善 |
| PL-4 | BEAR only | 0.08 | 0.35 | 3.0 | 72 | 保护核心 alpha |
| PL-5 | NEUTRAL only | 0.06 | 0.25 | 2.0 | 0 | 攻击 NEUTRAL 弱窗口 |

## 测试计划

### 单元测试

新增或扩展 `tests/test_strategy_contracts.py` / 新建 `tests/test_channel_breakout_profit_lock.py`:

1. 默认关闭时信号与原始 ChannelBreakout 完全一致。
2. 多头浮盈未达到 activation 时不退出。
3. 多头达到 activation 后，回吐超过阈值触发 `0`。
4. 空头达到 activation 后，回吐超过阈值触发 `0`。
5. ATR trailing 对多空方向正确。
6. `profit_lock_min_hold_bars` 生效。

### 集成测试

1. 使用 v2.1 balanced checkpoint 跑 oracle/replay，确认 baseline 输出不变。
2. 使用一个临时 Profit-Lock 候选跑 replay，确认能产生 Profit-Lock close 信号。
3. 确认 `exit_logic_variant` schema 接受新增字段，并拒绝越界参数。

## 验收标准

实现阶段完成的最低标准:

1. 旧 v2.1 balanced 行为不变。
2. Profit-Lock 可由参数开启和关闭。
3. 回测信号层能表达 Profit-Lock 退出。
4. 单元测试覆盖多空 Profit-Lock 触发。
5. 研究候选可以用现有 oracle/replay 跑出可比较指标。

研究阶段通过标准:

1. OOS return 不低于 baseline 的 90%。
2. OOS Sharpe 不低于 baseline。
3. OOS max drawdown 有改善，或 MFE capture ratio 明显改善。
4. BEAR return 不显著下降。
5. NEUTRAL worst 6m rolling return 有改善，或确认止盈不能解决 NEUTRAL 假突破问题。

## 下一步

先实现最小闭环:

1. 在 `ChannelBreakoutTrendStrategy` 增加默认关闭的 Profit-Lock 参数。
2. 写多空触发单元测试。
3. 扩展 `exit_logic_variant` schema 和 family registry。
4. 只创建 1 个临时 PL-2 候选做 smoke replay。

如果 smoke 结果不明显恶化，再扩展到 5 个候选。

