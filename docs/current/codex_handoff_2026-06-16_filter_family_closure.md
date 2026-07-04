# Codex Handoff — 2026-06-16: Filter Family Closure & Next Direction

**接手这条线之前，先读完本文。** 现有文档（CLAUDE.md、Phase 3C/4/5 设计文档、OKX handoff）覆盖了架构、v2.1 基线和旧阶段，但**不足以接住今天 filter-family 闭环后的正确方向**。本文是那条缺失的桥。

---

## 1. 当前状态：三个 Filter Family 最终 Verdict

以下 verdict 已通过 commit 写入代码库，**不可逆转**：

### volatility_gate — PAUSED（暂停）

- **做了什么**: 在 ATR/close 比率超过阈值时阻止入场
- **threshold sweep 结果**: p95/p97/p99 全部失败
  - fee 确实改善（+0.72% at 10bp）
  - 但 rolling12m 始终恶化（约 -19%）
  - OOS return 也持续下降
- **结论**: fee-vs-rolling tradeoff 无法通过阈值调节解决
- **状态**: paused（不是 closed — 如果未来有新机制能解释如何绕过这个 tradeoff，可以重新审视）
- **Commit**: `4330086 chore: pause volatility_gate family`

### cooldown_after_drawdown — CLOSED（关闭）

- **做了什么**: 在 close drawdown 超过阈值后启动冷却期，阻止入场
- **结果**: 全指标恶化
  - exp_0046: 所有评估指标都比 baseline 差
  - 冷却期阻止的是恢复性入场，而不是亏损入场
- **结论**: 这个方向从根本上就是反的 — 回撤后的入场恰恰是盈利来源
- **状态**: closed
- **Commit**: `1b3ad74 feat: filter attribution diagnostics`（含 exp_0046 negative learning）

### neutral_regime_entry_block — CLOSED（关闭）⚠️ 最重要

- **做了什么**: 在 NEUTRAL 市场状态下阻止所有入场（block_entries_when_neutral）
- **结果**: 太激进了 — NEUTRAL 占据了数据集中太大的比例

| 指标 | 数值 |
|------|------|
| neutral_bars | 102,496 |
| blocked_long_entries | 34,039 |
| blocked_short_entries | 43,554 |
| **signals_changed_total** | **77,593** |
| OOS safe return | +100.45%（baseline +157% → 减半） |
| Rolling 12m min | -27.18%（baseline -13% → 恶化） |
| IS DD | -50.14%（突破 -50% 硬门槛） |
| **Status** | **REJECT** |

- **为什么这不是 "block-all 后再试 directional" 的问题**: block-all 已经证明 blanket neutral blocking 太宽。NEUTRAL 覆盖了太多数据，一刀切砍掉了大量盈利交易。directional sweep（只 block long / 只 block short）**不作为主线继续**。
- **Commit**: `1be209c chore: close neutral_regime_entry_block family`

---

## 2. 不要再做什么 ⛔

这是给接手者的硬约束，不是建议：

1. **不要再做 entry-block filter。** 三个 family 的结论是一致的：阻止入场无法改善 rolling/OOS。这条路已经穷尽。

2. **不要继续 directional sweep。** neutral_regime_entry_block 之后不要试图 "block longs only" 或 "block shorts only" — 那不是下一步，是同一口井里继续挖。

3. **不要回到 adaptive lookback。** Phase 3C 已经是旧交接内容（`docs/historical/phase3c_design.md`），exp_0012 已证明 discrete switching 在 2600d 上不稳定。不要把它当作当前主线。

4. **不要回到 regime audit / sensitivity sweep。** Phase 5A/5B 已完成（`docs/historical/2026-06-15-oracle-v02-configurable-regime-audit-results.md`），结果清楚：keep 50/200。不需要再扫。

5. **不要从全策略默认参数裸扫开始。** 搜索空间已经收窄了很多轮，从头裸扫是浪费。

6. **不要碰 Track A / demo 文件。** 以下文件不可修改：
   - `checkpoints/channel_breakout_v2_1_balanced.pt`
   - `live_okx_quant.py` / `live_binance_quant.py` / `live_nado_quant.py`
   - `dex/live/common.py`
   - `dex/regime_filter.py` / `dex/regime_permissions.py`
   - `start_bitget_demo_v21*.bat`

---

## 3. 最新可执行路径

**候选在 `research_workspace\llm_candidates\`，不是 `research_workspace\candidates\`。**

### Windows venv 直接执行（推荐，避免 uv 路径问题）

```powershell
# 评估指定候选（最新可用候选是 exp_0053）
.venv\Scripts\python.exe scripts\research_oracle.py --candidate research_workspace\llm_candidates\exp_0053.json

# 评估 v2.1 基线
.venv\Scripts\python.exe scripts\research_oracle.py --baseline

# 归因分析
.venv\Scripts\python.exe scripts\diagnose_oracle_vs_replay.py

# Smoke test
.venv\Scripts\python.exe -m pytest tests\test_research_oracle.py -v
```

### 为什么用 `.venv\Scripts\python.exe` 而不是 `uv run`

- `uv run` 在某些 Windows 环境下路径解析不一致
- `.venv\Scripts\python.exe` 直接使用项目虚拟环境，行为确定
- 如果接手者使用不同 shell 或终端，直接 python 路径更可靠

### 可用候选清单

最新候选在 `research_workspace\llm_candidates\`：
- `exp_0053.json` — neutral_regime_entry_block（已 REJECT，保留作为参考）
- `exp_0045.json` — volatility_gate（已 PAUSED）
- `exp_0046.json` — cooldown_after_drawdown（已 CLOSED）

**注意**: 这三个候选都是已关闭/暂停的 family。当前没有活跃候选。下一步需要从新方向创建。

---

## 2026-06-24 Supersession Note

Current ETH operating baseline is now `channel_breakout_v2_2_m375_bbm375_1p5`
plus TimesFM gate `exp_0068`.

- checkpoint: `checkpoints/channel_breakout_v2_2_m375_bbm375_1p5.json`
- live/demo gate config: `configs/live/timesfm_gate_exp_0068.json`
- gate params: `context=1024`, `horizon=72`, `min_edge_pct=-0.01`,
  `risk_floor_pct=0.05`

The older “entry filters closed / v2.2 observation-only” conclusion below is
historical where it conflicts with this baseline.

## 4. 下一步方向

明确转向：**不再做 entry-block filter。下一类方向应该先定义 contract，再手动校准/测试，不走 LLM 自动发散。**

### 2026-06-17 Monte Carlo 口径更新

最新 Monte Carlo 结论把主矛盾进一步收窄：

> 策略不是不赚钱；主问题是在当前仓位/杠杆口径下，回撤分布太厚，几乎必然触发 `-30%` 硬阈值。

同一口径：`ETHUSDT_5m_2600d`，OOS `2024-06-06 14:25:00` ~ `2026-06-12 02:55:00`，`2000` 次，`1d block bootstrap`，`seed=2202`。

| 版本 | 基准收益 | 基准 MaxDD | MC 亏损概率 | MC 跌破 -30% DD 概率 | 判断 |
|------|----------|------------|-------------|------------------------|------|
| v2 裸跑 `375/432` | `+514.95%` | `-32.97%` | `2.25%` | `97.20%` | 主信号候选，收益能力最强，但原仓位风险太大 |
| v2.2 `mtg_bcd` | `+392.90%` | `-32.48%` | `2.50%` | `94.90%` | 过滤了部分收益交易，但没有真正压住尾部回撤 |
| v2.1 balanced | `+256.21%` | `-33.60%` | `8.10%` | `98.30%` | 暂时降级，收益/风险都不如前两者 |

v2.2 相比裸跑 v2：

- 交易数 `219 -> 107`;
- 基准收益 `+514.95% -> +392.90%`;
- 基准 MaxDD 只从 `-32.97%` 改到 `-32.48%`;
- MC 跌破 `-30%` 概率只从 `97.20%` 改到 `94.90%`。

结论：v2.2 的附加条件目前更像规则过滤尝试，未击中主问题。不要继续优先堆入场过滤条件；下一步优先做 `position sizing + drawdown control`。

仓位缩放补测只测 v2 裸跑 `375/432`：

| 仓位 | 基准收益 | 基准 MaxDD | MC 收益 P5/P50/P95 | MC 回撤 P5/P50/P95 | MC 跌破 -30% DD 概率 |
|------|----------|------------|---------------------|---------------------|------------------------|
| `0.4x` | `+129.28%` | `-14.84%` | `+27.01% / +129.16% / +323.03%` | `-33.46% / -20.86% / -13.93%` | `9.80%` |
| `0.425x` | `+140.34%` | `-15.69%` | `+28.45% / +140.27% / +360.62%` | `-35.16% / -22.01% / -14.73%` | `13.55%` |
| `0.45x` | `+151.80%` | `-16.52%` | `+29.90% / +151.77% / +401.27%` | `-36.82% / -23.18% / -15.52%` | `17.05%` |
| `0.475x` | `+163.66%` | `-17.34%` | `+31.29% / +163.63% / +445.19%` | `-38.44% / -24.31% / -16.31%` | `21.75%` |
| `0.5x` | `+175.93%` | `-18.16%` | `+32.54% / +176.03% / +492.64%` | `-40.02% / -25.41% / -17.09%` | `26.15%` |

Current ranking:

1. v2 裸跑 `375/432`: 主 signal core。
2. v2.2: 保留观察，不作为当前优选。
3. v2.1 balanced: 暂时降级。

Position-sizing interpretation:

- If the risk target is `P(MC DD < -30%) < 15%`, `0.425x` is the current best tested point.
- If accepting about `17%` breach probability, `0.45x` is the higher-return balance point.
- `0.5x` already shows tail-risk hardening; do not treat it as safe merely because historical MaxDD is below `-20%`.

### 候选方向（按优先级）

| 方向 | 简述 | 为什么 |
|------|------|--------|
| **fixed_position_scaler** | 先把 v2 裸跑按固定仓位缩放到 `0.425x` ~ `0.45x` | 最小机制已经把 MC 尾部风险拉回可讨论区间 |
| **drawdown_control_scaler** | equity DD > 10%/20% 后阶梯降仓，新高后恢复 | 主问题是回撤分布太厚，不是信号没有 edge |
| **exit_loss_attribution** | 不先动入场，先拆 v2 裸跑亏损单来源 | 判断大亏来自趋势反转、震荡假突破还是持仓过久 |
| **regime_exposure_scaler** | 在不同 regime 下缩放仓位暴露，而非阻止入场 | 如果固定缩放不够，再看 regime 是否能解释尾部暴露 |

### Contract 草案模板

新方向必须先用以下模板定义 contract，通过人工评审后再创建候选：

```markdown
# Contract: <方向名称>

## Hypothesis
<一句话假设，必须解释为什么不同于 entry-block>

## Why Entry-Block Failed
<引用三个 filter family 的具体失败模式，说明本方向如何避开>

## Mechanism
<具体机制：改什么、不改什么、为什么可能有效>

## Expected Metrics
<预期对 OOS return / DD / rolling12m / fee robustness 的影响方向和幅度>

## Failure Modes
<什么情况会判定本方向失败，提前定义 exit criteria>

## Calibration Plan
<手动校准步骤：先测什么、再调什么、什么阈值范围>
```

---

## 5. 当前研究假设（原文）

> **真正病灶可能在 regime transition / 持仓暴露上，不是 entry quality。**

三个 entry-block filter 全部失败指向同一个结论：问题不在 "哪些入场是坏的"，而在 "入场之后发生了什么"。具体来说：

- NEUTRAL regime 覆盖了大量数据（102k bars），一刀切阻止入场砍掉了太多盈利交易
- 但在 NEUTRAL 中确实存在一段表现特别差的窗口（最差 12m rolling）
- 这说明问题可能不是 "NEUTRAL 入场都差"，而是 "NEUTRAL 中的某些 sub-regime 或 transition 期间持仓暴露不对"

**这否定了 entry quality 假说，转向 position management / regime transition 假说。**

---

## 6. 现有文档索引

接手者应该知道每个文档覆盖什么，以及它**不覆盖**什么：

| 文档 | 覆盖 | 不覆盖 |
|------|------|--------|
| `CLAUDE.md` | 项目架构、策略演进、v2.1 基线、Phase 1-2 sandbox | filter family verdict、当前方向 |
| `docs/baseline/2026-06-11-okx-trend-handoff.md` | OKX demo 设置、checkpoint 管理、通道突破策略演进 | 2026-06-15 之后的 filter family |
| `docs/baseline/2026-06-12-channel-breakout-v2-1-regime-permission.md` | v2.1 regime permission 参数 | 同上 |
| `docs/historical/2026-06-15-oracle-v02-configurable-regime-audit-results.md` | Phase 5A/5B oracle v0.2 敏感性审计 | filter family 闭环 |
| `docs/historical/phase3c_design.md` | 旧 Phase 3C adaptive lookback 设计 | **已过时，不要作为主线** |
| `docs/historical/phase4_design.md` | Phase 4 regime routing 设计 | **已被 filter family 结论覆盖** |
| `docs/historical/candidate_status.md` | 2026-06-15 的候选状态快照 | 2026-06-16 filter family closure |
| `docs/reference/llm_research_contract.md` | LLM 研究契约 | 同上 |
| `research_workspace/README.md` | LLM agent 工作规则、双通道评审 | 当前方向 |
| `research_agents/prompts/candidate_generator.md` | LLM 生成器 prompt v0.4（含已关闭 family 约束） | — |

**本文（`docs/current/codex_handoff_2026-06-16_filter_family_closure.md`）是唯一记录 filter family 闭环后方向的文档。**

---

## 7. 接手检查清单

- [ ] 读 `CLAUDE.md`（架构 + v2.1 基线）
- [ ] 读本文（filter family closure + 方向）
- [ ] 确认 `.venv\Scripts\python.exe scripts\research_oracle.py --baseline` 可以跑通
- [ ] 确认 `research_workspace\llm_candidates\` 目录存在且包含 exp_0045/0046/0053
- [ ] 确认 git log 最后几个 commit 对应三个 filter family 的 verdict
- [ ] **不要**开始新的 entry-block filter 实验
- [ ] **不要**修改 Track A / demo / live 文件
- [ ] 如果要开新方向，先按 contract 模板写设计文档，人工评审通过后再创建候选
