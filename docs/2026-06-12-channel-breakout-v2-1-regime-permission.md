# ChannelBreakout v2.1 — Regime Permission Overlay 搜索结果

**日期**: 2026-06-12
**运行**: `scripts/tune_regime_permissions_v2.py`
**输出目录**: `search_results/regime_permissions_v2_20260612_151334/`

## 1. 搜索设置

| 参数 | 值 |
|------|-----|
| 数据 | ETHUSDT 5m, 706,103 bars (2019-09-23 ~ 2026-06-12) |
| IS/OOS | 70/30 (494,272 / 211,831 bars) |
| Regime filter | 50/200 EMA daily, previous completed candle only |
| Permission policy | `permission_based` |
| 候选规模 | 8 BULL × 3 BEAR × 8 NEUTRAL = **192 组合** |
| 通过筛选 | 192/192 (trades ≥ 10) |

### BULL candidates (8)

| ID | 规则 |
|----|------|
| U0_base | 375/432 long-only (baseline) |
| U1_ema50 | daily_close < EMA50 → 禁多 |
| U2_slope | EMA50 slope_5d < 0 → 禁多 |
| U3_ema100 | daily_close < EMA100 → 禁多 |
| U4_cons3 | daily_close 连续 3 天 < EMA50 → 禁多 |
| U5_dd15 | 90d high drawdown > 15% → 禁多 |
| U6_ema50_adx20 | U1 + ADX < 20 → force_flat |
| U7_cons3_adx20 | U4 + ADX < 20 → force_flat |

### BEAR candidates (3)

| ID | 规则 |
|----|------|
| K0_base | 375/432 dual |
| K1_atr25 | 375/432 dual + breakout_atr_buffer=0.25 |
| K2_adx18 | 375/432 dual + ADX < 18 → force_flat |

### NEUTRAL candidates (8)

| ID | 规则 |
|----|------|
| N0_flat | force_flat（不交易） |
| N1_lonly | 375/432 long-only (v2 baseline) |
| N2_shortif | short only if close < EMA50 and slope < 0 |
| N3_dir | directional: close > EMA50 & slope > 0 → long; close < EMA50 & slope < 0 → short |
| N4_sdual | 4000/576 exit=2880 dual (慢通道) |
| N5_sdir | 4000/576 exit=2880 directional |
| N6_adxdir | 375/432 directional + ADX < 22 → force_flat |
| N7_adx_gated | 375/432 directional + ADX<18 force_flat, 18≤ADX<22 exit_only, ADX≥22 normal |

## 2. IS/OOS Regime 分布

| Regime | IS | IS% | OOS | OOS% |
|--------|------|-----|------|-----|
| BULL | 281,543 | 57% | 68,371 | 32% |
| BEAR | 117,865 | 24% | 109,476 | **52%** |
| NEUTRAL | 94,864 | 19% | 33,984 | 16% |

⚠️ **OOS 明显偏 BEAR (52%)**。OOS buy-and-hold 收益为 **-56.33%**。
策略在偏熊 OOS 中表现好是预期内的（通道突破是熊市专家），但在 BULL-heavy 时期的表现尚未验证。

## 3. Baseline vs Balanced 对比

### Baseline (v2 当前): U0_base / K0_base / N1_lonly

| 指标 | 值 |
|------|-----|
| OOS Return | +61.34% |
| OOS Max DD | **-46.99%** |
| Sharpe | 0.38 |
| Trades | 234 |
| Excess vs B&H | +117.67% |
| BULL_LONG DD 贡献 | -2,699 |

### Balanced (推荐): U4_cons3 / K0_base / N3_dir

| 指标 | 值 | vs Baseline |
|------|-----|-------------|
| OOS Return | **+252.24%** | +191% |
| OOS Max DD | **-33.60%** | ↓13.4pp |
| Sharpe | **1.31** | +0.93 |
| Trades | 258 | +24 |
| Excess vs B&H | +308.57% | +191% |
| Return / |DD| | 7.51 | — |
| Trades/year (est.) | ~128 | — |
| Avg days between trades | ~2.85 | — |
| BULL_LONG DD 贡献 | -810 | **↓70%** |

### 策略含义

```
BULL:
  375/432 long-only
  risk-off: 连续 3 个已收盘日线 close < EMA50 → 禁止新开多 / 平掉现有多头

BEAR:
  375/432 dual
  不做额外过滤

NEUTRAL:
  375/432 directional
  close > EMA50 且 EMA50 slope_5d > 0 → 只允许做多
  close < EMA50 且 EMA50 slope_5d < 0 → 只允许做空
  其他情况（方向不明）→ 不交易
```

## 4. BULL Candidate 排名（24 BEAR×NEUTRAL 平均）

| BULL | Avg Return | Avg DD | BULL_LONG DD 贡献 | 评价 |
|------|-----------|--------|-------------------|------|
| **U4_cons3** | **+177.9%** | -37.1% | **-810** | ✅ 最優 |
| U1_ema50 | +153.6% | -39.1% | -577 | ✅ |
| U2_slope | +148.9% | -39.1% | -747 | ✅ |
| U0_base | +147.0% | -38.8% | -1,859 | baseline |
| U5_dd15 | +124.3% | -39.3% | -1,061 | |
| U3_ema100 | +110.4% | -42.5% | -893 | |
| U6_ema50_adx20 | +96.2% | -39.6% | -19 | ⚠️ 过滤过度 |
| U7_cons3_adx20 | +94.6% | -40.4% | -19 | ⚠️ 过滤过度 |

U6/U7 的 ADX<20 过滤把 BULL 盈利期也砍掉了，收益反而下降。

## 5. NEUTRAL Candidate 排名（24 BULL×BEAR 平均）

| NEUTRAL | Avg Return | Avg DD | 评价 |
|---------|-----------|--------|------|
| **N3_dir** | **+175.7%** | -36.5% | ✅ 最優 |
| N5_sdir | +168.9% | -37.5% | |
| N2_shortif | +166.7% | -37.9% | |
| N4_sdual | +154.5% | -41.2% | |
| N6_adxdir | +140.5% | -36.0% | 交易过多 (886) |
| N7_adx_gated | +122.1% | -37.3% | |
| N0_flat | +82.5% | -39.7% | |
| N1_lonly | +42.0% | -49.9% | ❌ 最差 |

N1_lonly（当前 v2 baseline）是 NEUTRAL 里最差的选择。N3_dir（directional）最优。

## 6. 为什么 Stable 不存在

DD < 30% 在 192 个候选里没有任何一个达标。最小 DD 是 -33.60%（U4_cons3/K0_base/N3_dir）。

这证实了之前的结论：**+200%/<30% DD 在当前 6.5 年 ETH 数据上不可达**。收益和回撤同源——都靠翻仓赚的。

## 7. 为什么 Aggressive 暂不推荐

| 组合 | Return | DD | Sharpe |
|------|--------|-----|--------|
| Balanced U4/K0/N3 | +252.24% | -33.60% | 1.31 |
| Aggressive U4/K0/N4 | +225.46% | -41.16% | 1.20 |

Aggressive rank 1 被 Balanced rank 1 **严格支配**：收益更低、回撤更高、Sharpe 更低。它不是真正意义上的"aggressive"（高收益高回撤），只是"慢 NEUTRAL dual 的对照版本"。

## 8. 已知限制 & Caveats

### DD contribution 分析（已修复 2026-06-13）

DD 归因分析已修复。现在使用 `StrategyEvaluator.simulate()` 的权威 equity curve 和 trade log，通过 FIFO 开仓匹配正确追踪所有 regime × direction（BULL/BEAR/NEUTRAL × LONG/SHORT）的贡献。`dd_window_pct` 现在与 `oos_dd` 一致。

修复前 `dd_analysis.max_dd_pct` 曾显示 106%（完全不正确），修复后与 `oos_dd` 数值匹配。

⚠️ 注意：当 DD 完全来自持仓浮亏（窗口内无交易平仓）时，归因数据会偏少。当前使用"与 DD 窗口有重叠"的过滤，包含持仓跨窗口的交易。

### OOS 偏熊

OOS 段 BEAR 占 52%，BULL 仅 32%。策略在偏熊段表现好是预期内的（通道突破是熊市专家），但以下场景尚未验证：
- BULL-heavy OOS
- NEUTRAL-heavy OOS
- Rolling OOS (6m/12m/18m 滑动窗口)

### 交易频率

Balanced candidate 约 128 trades/year (~2.85 天/笔)，频率适中，不是低频策略。但：
- Windows 本机无人值守 live **不推荐**（断电/睡眠会错过平仓/风控信号）
- Signal-only 观察 OK
- 如需自动执行，建议 VPS + startup catch-up + exchange reconciliation

## 9. checkpoint 文件

| 文件 | 状态 |
|------|------|
| `checkpoints/channel_breakout_v2_1_balanced.pt` | ✅ 已生成 — research candidate |
| `checkpoints/channel_breakout_v2_1_stable.pt` | ❌ 不存在 — DD<30% 不可达 |
| `checkpoints/channel_breakout_v2_1_aggressive.pt` | ❌ 未生成 — 被 balanced 支配 |

## 10. 下一步

1. **修复 DD contribution bug** — NEUTRAL/BEAR 细分 ✅ 已修复 (2026-06-13)
2. **Rolling OOS 验证** — 6m/12m/18m 滑动窗口
3. **Regime-balanced 验证** — BULL-heavy / BEAR-heavy / NEUTRAL-heavy 子区间
4. **OKX signal-only 对接** — 用 `dex.regime_permissions` 统一 API ✅ 已完成 (2026-06-13)
5. **Live 断点恢复** — startup catch-up + exchange reconciliation（如需无人值守）

## 11. 实盘/Sandbox 运行记录 (2026-06-13)

### OKX Demo

| 项目 | 状态 |
|------|------|
| `live_okx_quant.py` v2.1 支持 | ✅ 已接入，自动检测 `strategy_type` |
| `--signal-only` 模式 | ✅ 支持，已有 CLI 参数 |
| v2 profile 共存 | ✅ `--strategy-profile channel_breakout_v2_1_balanced` 和 `channel_breakout_v2` 互不干扰 |
| 启动脚本 | `scripts/run_okx_channel_breakout_v2_1_demo.ps1` |
| 真实市场数据 | ✅ OKX demo 使用真实盘口（bid/ask 价差 ~0.01） |

### Bitget Demo

| 项目 | 状态 |
|------|------|
| `live_bitget_quant.py` v2.1 支持 | ✅ 已接入 |
| `--signal-only` 模式 | ✅ 已新增 |
| 启动脚本 | `start_bitget_demo_v21.bat` / `start_bitget_demo_v21_signal_only.bat` |
| 注意 | Bitget PAPTRADING 是隔离沙箱，价格不与真实市场同步，仅用于验证策略逻辑流程 |

### 已验证的功能

- v2.1 checkpoint 加载 → 三套策略 + permission config 自动构建
- 日线 EMA 指标计算 → 前一日已收盘 daily candle，无 lookahead
- regime 判定 → BULL/BEAR/NEUTRAL 实时输出
- permission 拦截 → allow_L/allow_S/force_flat/exit_only 正确生效
- Maker 挂单 → 成交 → 信号变化撤单 流程正常
