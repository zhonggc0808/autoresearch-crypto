# ChannelBreakout v2.1 Profit-Lock 高收益组合记录

日期: 2026-06-16

## 目的

本记录只保存本轮 Profit-Lock 研究中已经观察到 **OOS 收益率 / Sharpe 高于 v2.1 baseline** 的组合。

当前这些组合不要求通过全部风险门禁，因为下一阶段会继续研究止损策略。止损如果能把 IS DD 或 rolling negative 压下来，这些 Profit-Lock 组合可以作为优先父候选继续扩展。

## Baseline 参照

数据与评估口径:

- `ETHUSDT 5m 1300d`
- split: `70/30`
- oracle: `research_oracle v0.2`
- baseline: `channel_breakout_v2_1_balanced`

| 组合 | IS Return | IS DD | IS Sharpe | OOS Return | OOS DD | OOS Sharpe | OOS Trades |
|------|-----------|-------|-----------|------------|--------|------------|------------|
| baseline | 0.6717 | -0.5274 | 0.4197 | 1.5657 | -0.3385 | 2.3497 | 70 |

## 已固化候选

### exp_0020_profit_lock_bear_neutral_pl1

文件:

- `research_workspace/candidates/exp_0020_profit_lock_bear_neutral_pl1.json`

参数:

```text
scope: BEAR + NEUTRAL
activate_profit_pct: 0.12
giveback_ratio: 0.50
trailing_atr_multiplier: 4.0
atr_period: 14
min_hold_bars_before_lock: 72
exit_lookback: 0
```

完整 oracle 结果:

| IS Return | IS DD | IS Sharpe | OOS Return | OOS DD | OOS Sharpe | OOS Trades | Oracle Status |
|-----------|-------|-----------|------------|--------|------------|------------|---------------|
| 0.2953 | -0.5654 | 0.2036 | 2.0528 | -0.3385 | 3.0302 | 79 | REJECT |

记录理由:

- OOS Return 从 `1.5657` 提升到 `2.0528`。
- OOS Sharpe 从 `2.3497` 提升到 `3.0302`。
- OOS DD 基本不变。
- 缺点是 IS DD 到 `-0.5654`，触发 `DD_OVER_50`。
- 适合下一阶段叠加止损策略优先研究。

### exp_0021_profit_lock_bear_neutral_robust

文件:

- `research_workspace/candidates/exp_0021_profit_lock_bear_neutral_robust.json`

参数:

```text
scope: BEAR + NEUTRAL
activate_profit_pct: 0.18
giveback_ratio: 0.65
trailing_atr_multiplier: 5.0
atr_period: 14
min_hold_bars_before_lock: 72
exit_lookback: 0
```

完整 oracle 结果:

| IS Return | IS DD | IS Sharpe | OOS Return | OOS DD | OOS Sharpe | OOS Trades | Oracle Status |
|-----------|-------|-----------|------------|--------|------------|------------|---------------|
| 0.7102 | -0.4721 | 0.4455 | 1.7672 | -0.3385 | 2.5823 | 73 | REJECT |

记录理由:

- OOS Return 从 `1.5657` 提升到 `1.7672`。
- OOS Sharpe 从 `2.3497` 提升到 `2.5823`。
- IS DD 从 baseline 的 `-0.5274` 改善到 `-0.4721`。
- 仍因 rolling negative 被 oracle 拒绝。
- 比 `exp_0020` 更稳健，适合作为止损策略研究的首选父候选。

## 轻量扫描中收益提高的组合

这些组合来自同一数据和信号生成逻辑的轻量扫描，只计算 IS/OOS 汇总指标，未全部固化为候选文件。

| Scope | Activate | Giveback | ATR Mult | IS Return | IS DD | IS Sharpe | OOS Return | OOS DD | OOS Sharpe | OOS Trades | 备注 |
|-------|----------|----------|----------|-----------|-------|-----------|------------|--------|------------|------------|------|
| BEAR + NEUTRAL | 0.12 | 0.50 | 4.0 | 0.295 | -0.565 | 0.204 | 2.053 | -0.338 | 3.030 | 79 | 已固化为 `exp_0020`; 最高 OOS 收益/Sharpe |
| ALL | 0.12 | 0.50 | 4.0 | 0.144 | -0.565 | 0.104 | 1.958 | -0.339 | 2.899 | 81 | 收益高，但 IS 质量较弱 |
| NEUTRAL | 0.12 | 0.50 | 4.0 | 0.707 | -0.516 | 0.448 | 1.904 | -0.338 | 2.843 | 74 | NEUTRAL-only 值得后续搭配止损复查 |
| BEAR + NEUTRAL | 0.18 | 0.65 | 5.0 | 0.710 | -0.472 | 0.446 | 1.767 | -0.338 | 2.582 | 73 | 已固化为 `exp_0021`; 当前最稳健 |
| BEAR | 0.18 | 0.65 | 5.0 | 0.527 | -0.527 | 0.337 | 1.720 | -0.338 | 2.540 | 72 | 接近 baseline IS DD，收益略高 |
| ALL | 0.18 | 0.65 | 5.0 | 0.603 | -0.472 | 0.387 | 1.707 | -0.338 | 2.497 | 74 | 比较均衡，可作为备选 |
| BEAR | 0.12 | 0.50 | 4.0 | 0.268 | -0.554 | 0.182 | 1.697 | -0.338 | 2.518 | 75 | 高收益但 IS DD 偏深 |
| NEUTRAL | 0.18 | 0.65 | 5.0 | 0.873 | -0.472 | 0.534 | 1.610 | -0.338 | 2.391 | 71 | IS 表现较好，OOS 提升较小 |

## 后续止损研究建议

优先顺序:

1. `exp_0021_profit_lock_bear_neutral_robust`
   - 先叠加止损，因为它已经避开 `DD_OVER_50`，只剩 rolling negative 风险。
2. `exp_0020_profit_lock_bear_neutral_pl1`
   - 收益/Sharpe 最强，适合测试止损能否把 IS DD 拉回 50% 内。
3. `NEUTRAL-only 0.12/0.50/4.0`
   - 如果下一阶段重点解决 rolling negative，可以优先研究这个组合。

止损策略不要直接用过紧固定止损。建议下一阶段先研究:

- regime-aware emergency stop
- ATR stop-loss
- drawdown cooldown / risk-off filter
- profit-lock + stop-loss 的交互顺序

建议保持当前退出优先级:

```text
emergency_stop / stop-loss
profit-lock
channel exit
reverse breakout
hold
```
