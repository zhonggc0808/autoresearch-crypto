# 2026-06-11 OKX Demo 与趋势策略交接

## 结论

当前最值得继续推进的是低频趋势突破策略 `ChannelBreakoutTrendStrategy`，不是均值回归或短周期趋势跟随。

原因很直接：当前 ETH 365 天 5m 数据整体下跌约 `-40.37%`，最近 30 天约 `-28.0%`，趋势段和反弹都很剧烈。短周期均值回归、Scalp、EMA pullback 类策略在这种行情里容易频繁止损；长周期 Donchian 通道突破虽然交易少，但更能吃到大方向。

最新 `checkpoints/eth_optimal.pt` 已回到低频长通道版本：

| 项目 | 数值 |
| --- | --- |
| 策略 | `channelbreakout` |
| 核心参数 | `entry_lookback=8000`, `enable_short=True` |
| 回测收益 | `+133.89%` |
| 买入持有基准 | `-36.26%`，同一有效区间含手续费+滑点 |
| 超额收益 | `+170.14%` |
| Sharpe | `2.4309` |
| 最大回撤 | `-29.87%` |
| 胜率 | `80.0%` |
| 交易次数 | `5` |

这个策略是“等待大趋势破位”的低频策略，不是每天都有交易的策略。OKX Demo 当前信号是 `HOLD`，没有下单。

## 本轮关键优化

### 1. 完善趋势策略

新增/完善文件：

- `dex/strategies/channel_breakout.py`
- `dex/checkpoints.py`
- `dex/strategy_signals.py`
- `docs/reference/STRATEGIES.md`

`ChannelBreakoutTrendStrategy` 当前支持：

- Donchian 通道突破翻仓：上破做多，下破做空。
- 默认长通道参数：`entry_lookback=8000`。
- 可选过滤器：`exit_lookback`、`breakout_buffer_pct`、`breakout_atr_buffer`、`trend_ma_period`、`trend_slope_lookback`、`adx_threshold`、`require_di_alignment`。
- 默认过滤器关闭，保证旧 checkpoint 行为不漂移。
- `take_profit_pct=0`、`stop_loss_pct=0`、`max_hold_bars=0` 表示趋势持仓不被实盘默认止盈/止损/超时提前截断。

### 2. 修正策略搜索保存逻辑

修改文件：

- `search_eth_optimal.py`

关键点：

- `search_channel_breakout()` 会优先评估已知强趋势种子参数，避免 `--quick` 模式只扫到短通道就覆盖冠军。
- checkpoint 排名保存从“WF 优先”改成“风险调整综合评分优先，WF 作稳定性兜底”。
- `checkpoints/eth_optimal.pt` 只保存能跑赢买入持有基准的冠军。

### 3. 接入并验证 OKX Demo

修改文件：

- `live_okx_quant.py`
- `dex/live/common.py`

关键点：

- OKX 默认不再强制使用 `127.0.0.1:50830` 代理，只有设置 `OKX_HTTP_PROXY` 时才启用代理。
- OKX K 线拉取支持分页，能为 `entry_lookback=8000` 拉取约 `8010` 根 5m K 线。
- API 数据不足时会尝试合并本地 parquet 历史。
- 对 `channelbreakout` 不再注入默认 `max_hold_bars=48`，避免趋势仓位被 4 小时强制退出。
- `take_profit_pct <= 0` 时不挂 TP 单。
- `stop_loss_pct <= 0`、`max_hold_bars <= 0` 时不触发对应实盘退出。
- OKX 日志已从固定“布林带”显示改为策略感知显示，例如 `Donchian通道: 上沿=... 中线=... 下沿=...`。

## 已验证命令

没有跑 `pytest`，因为用户明确要求“不要跑测试，现在先尽可能把策略补全”。本轮只做了格式、语法、搜索、回测和 OKX Demo 单轮验证。

### 格式与语法

```powershell
C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe run ruff format dex\strategies\channel_breakout.py dex\checkpoints.py search_eth_optimal.py dex\live\common.py live_okx_quant.py
C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe run python -m py_compile search_eth_optimal.py dex\strategies\channel_breakout.py dex\checkpoints.py dex\live\common.py live_okx_quant.py
```

结果：通过。

### 快速策略搜索

```powershell
C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe run python search_eth_optimal.py --quick
```

结果：

- 冠军：`ChannelBreakout`
- 全量收益：`+133.89%`
- Sharpe：`2.43`
- 最大回撤：`-29.9%`
- 交易：`5`
- checkpoint 已保存到 `checkpoints/eth_optimal.pt`
- 报告已保存到 `search_results/eth_optimal_report.json`

### 官方回测

```powershell
C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 365 --checkpoint checkpoints\eth_optimal.pt
```

结果：

- 策略：`channelbreakout`
- 最终权益：`23388.61 USDT`
- 总收益：`+133.89%`
- 买入持有基准：`-36.26%`
- 超额收益：`+170.14%`
- 信号统计：`HOLD 8221`、`LONG 44559`、`SHORT 52340`

### OKX Demo 单轮验证

```powershell
C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe run python live_okx_quant.py --symbol ETH-USDT-SWAP --interval 5m --demo --once --capital 10 --leverage 1 --checkpoint checkpoints\eth_optimal.pt
```

结果：

- Demo Trading 启动成功。
- OKX API 连接成功。
- ETH-USDT-SWAP 杠杆 `cross 1.0x` 设置成功。
- 策略加载成功：`window=8000`, `entry_lookback=8000`, `enable_short=True`。
- K 线需求：`8010`。
- 最新单轮信号：`HOLD`。
- 当前持仓：空仓。
- 本轮没有下单。

## 迁移到另一台机器

### 建议一起带走的内容

至少需要这些文件/目录：

- `dex/`
- `scripts/`
- `scripts/run_okx_channel_breakout_v2_demo.ps1`
- `search_eth_optimal.py`
- `backtest_quant.py`
- `live_okx_quant.py`
- `prepare_crypto.py`
- `train_quant.py`
- `pyproject.toml`
- `uv.lock`
- `checkpoints/eth_optimal.pt`
- `checkpoints/channel_breakout_375_432.pt`
- `checkpoints/channel_breakout_500_432.pt`
- `search_results/eth_optimal_report.json`
- `search_results/channel_breakout_candidates_730d.json`
- `data/crypto/ETHUSDT_5m_365d.parquet`
- `data/crypto/ETHUSDT_5m_730d.parquet`
- `docs/baseline/2026-06-11-okx-trend-handoff.md`
- `docs/reference/STRATEGIES.md`

不要把 `.env` 直接发到不可信环境。新机器上重新创建 `.env`，只填必要变量：

```dotenv
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_PASSPHRASE=...
# 可选，需要代理才设置
OKX_HTTP_PROXY=http://127.0.0.1:端口
```

### Windows 打包建议

在当前项目父目录执行，建议排除虚拟环境、缓存、日志和 `.env`：

```powershell
Compress-Archive -Path autoresearch-crypto -DestinationPath autoresearch-crypto-handoff.zip -Force
```

如果想更干净，先手动确认压缩包里不要包含：

- `.env`
- `.venv`
- `.git`
- `logs/*.lock`
- 临时缓存目录

数据文件 `data/crypto/ETHUSDT_5m_365d.parquet` 和 checkpoint 建议保留，否则新机器第一次跑需要重新下载数据或重新搜索。

### 新机器恢复步骤

1. 解压代码。
2. 安装 `uv`。
3. 在项目根目录执行：

```powershell
uv sync
```

4. 创建 `.env` 并填 OKX Demo API 环境变量。
5. 先跑本地回测确认环境：

```powershell
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 365 --checkpoint checkpoints\eth_optimal.pt
```

6. 再跑 OKX Demo 单轮：

```powershell
uv run python live_okx_quant.py --symbol ETH-USDT-SWAP --interval 5m --demo --once --capital 10 --leverage 1 --checkpoint checkpoints\eth_optimal.pt
```

7. 单轮确认正常后，再去掉 `--once` 让它持续运行：

```powershell
uv run python live_okx_quant.py --symbol ETH-USDT-SWAP --interval 5m --demo --capital 10 --leverage 1 --checkpoint checkpoints\eth_optimal.pt
```

## 下一位 Codex 接手建议

优先顺序：

1. 不要先大改架构。当前关键路径已经是：数据 -> checkpoint -> 回测 -> OKX Demo。
2. 先确认新机器上 `uv sync`、回测、OKX Demo `--once` 三步都通。
3. 如果要继续完善策略，建议围绕 `ChannelBreakoutTrendStrategy` 做，而不是回到均值回归。
4. 下一步可以做“风控参数外置”：例如用 CLI 控制 `capital`、最大单笔保证金、是否允许实盘、是否 signal-only。
5. 可以加一个 `--signal-only` 或 `--dry-run` 模式，持续记录 OKX 信号但不下单，适合迁移后观察 1 到 2 天。
6. 可以对 `search_eth_optimal.py` 增加“已有 checkpoint 防降级比较”，不仅靠种子参数，还读取当前 checkpoint 与新冠军比较后再决定是否覆盖。

## 风险与注意

- 这个策略交易频率极低，长时间 HOLD 是正常现象。
- 当前 OKX Demo 单轮只验证到“生成 HOLD 信号、不下单”，还没有验证真实开仓、挂单成交、平仓全链路。
- 当前仓库是脏工作树，很多文件在本轮开始前就已有改动；迁移前最好整体打包，不要只拷贝少数文件。
- 不要把聊天中出现过的 OKX secret 写进代码或文档。
- 如果新机器网络或代理不同，优先检查 `OKX_HTTP_PROXY`；默认不启用代理。

## 2026-06-12 更新：短通道候选与 Signal-Only

在 730 天 ETH 5m 数据上进一步扫描后，小周期 Donchian 通道不再只是“惊喜点”，而是出现了 `375-500` 附近的一片可用区域。其中 `min_hold_bars=432` 很关键，约等于 1.5 天，可以减少短周期反复翻仓。

新增脚本：

```powershell
uv run python scripts\create_channel_breakout_candidates.py
```

该脚本读取：

```text
data/crypto/ETHUSDT_5m_730d.parquet
```

并生成：

```text
checkpoints/channel_breakout_375_432.pt
checkpoints/channel_breakout_500_432.pt
search_results/channel_breakout_candidates_730d.json
```

候选表现：

| Checkpoint | 全样本收益 | 全样本回撤 | 全样本交易 | OOS 收益 | OOS 回撤 | OOS 交易 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `channel_breakout_375_432.pt` | `+500.22%` | `-32.97%` | `214` | `+94.88%` | `-22.80%` | `62` |
| `channel_breakout_500_432.pt` | `+301.03%` | `-37.50%` | `188` | `+34.13%` | `-28.85%` | `56` |

注意：官方 `backtest_quant.py` 的“综合评分”对超过 30% 回撤有硬门槛，因此这两个候选在官方回测里可能显示 `综合评分: 0.0000`。这不是加载失败；要看收益、回撤、交易次数、OOS 报告和 JSON 报告。

已验证回测命令：

```powershell
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 730 --checkpoint checkpoints\channel_breakout_375_432.pt
uv run python backtest_quant.py --symbol ETHUSDT --interval 5m --days 730 --checkpoint checkpoints\channel_breakout_500_432.pt
```

OKX 新增只观察模式：

```powershell
uv run python live_okx_quant.py --symbol ETH-USDT-SWAP --interval 5m --demo --once --signal-only --checkpoint checkpoints\channel_breakout_375_432.pt
```

也可以使用别名：

```powershell
uv run python live_okx_quant.py --symbol ETH-USDT-SWAP --interval 5m --demo --once --dry-run --checkpoint checkpoints\channel_breakout_375_432.pt
```

`--signal-only` / `--dry-run` 行为：

- 只初始化 OKX 公共行情 API。
- 不读取 OKX API key/secret/passphrase。
- 不设置杠杆。
- 不查询余额。
- 不同步持仓。
- 不撤单。
- 不下单。
- 不保存 `logs/live_okx_state.json`。
- 只拉 K 线、生成策略信号、打印盘口和目标方向。

最新单轮验证结果：

```text
checkpoint: checkpoints/channel_breakout_375_432.pt
signal: 2 做多 (LONG)
行为: Signal-Only，不执行持仓同步、撤单、下单或状态保存
```

当前建议：

- 先不要覆盖 `checkpoints/eth_optimal.pt`。
- 用 `channel_breakout_375_432.pt` 和 `channel_breakout_500_432.pt` 跑 1-2 天 `--signal-only`，观察信号频率、信号持续性和行情贴合度。
- 如果要进入 OKX Demo 自动下单，优先从 `channel_breakout_500_432.pt` 开始更保守；如果接受更活跃交易和更高换手，再试 `375/432`。

## 2026-06-12 更新：ChannelBreakout 1300d 研究收敛结论

这一轮研究把 `ChannelBreakout` 从早期 `entry_lookback=8000` 的长通道，推进到短通道
`entry_lookback=375` + `min_hold_bars=432`，并做了牛熊状态分桶、regime filter 与
DD 熔断实验。结论是：方向已经比较清楚，可以暂时收手，不建议继续在同一条线上硬追
`+200%` 收益且最大回撤 `< 30%`。

### 策略核心

Donchian 通道突破翻仓：价格突破通道上沿做多，跌破下沿做空，始终持仓，只在方向变化时翻仓。

当前最优裸跑参数：

| 参数 | 数值 |
| --- | ---: |
| `entry_lookback` | `375` |
| `min_hold_bars` | `432` |

### 跨币种适用性

| 币种 | 适用性 | 说明 |
| --- | --- | --- |
| ETH | 最佳 | 走势相对有序，趋势持续性够长 |
| BTC | 勉强 | 两年横盘，收益有限 |
| SOL | 不适合 | 暴涨暴跌噪音太大 |

这个策略目前只对 ETH 有效，不应当当成全天候跨币种策略。BTC 和 SOL 的数据不支持继续投入太多精力。

### 市场状态分桶

基于 ETH `1300d` 数据的入场状态分桶：

| 入场状态 | 方向 | 次数 | 总盈利 | 每笔平均 | 胜率 |
| --- | --- | ---: | ---: | ---: | ---: |
| 牛市 | LONG | `79` | `+2,223` | `+28` | `50.6%` |
| 牛市 | SHORT | `84` | `-1,275` | `-15` | `36.9%` |
| 熊市 | LONG | `62` | `+3,123` | `+50` | `38.7%` |
| 熊市 | SHORT | `59` | `+7,624` | `+129` | `47.5%` |
| 中性 | LONG | `50` | `+1,162` | `+23` | `44.0%` |
| 中性 | SHORT | `48` | `-3,777` | `-79` | `35.4%` |

关键发现：

- 熊市 LONG 是赚钱的，`+3,123`，不能简单禁用。
- 牛市 SHORT 和中性 SHORT 是主要出血点，合计约 `-5,052`。
- 因此 regime filter 的重点不是“熊市禁多”，而是减少牛市/中性阶段的无效做空。

### 版本演进

ETH `1300d` 回测结果：

| 版本 | 收益 | Sharpe | 最大回撤 | 交易 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| 裸跑 `375/432` | `+87.69%` | `0.33` | `-63.57%` | `382` | 回撤太大 |
| v1 牛市禁空 | `+195.44%` | `0.67` | `-43.26%` | `305` | 回撤仍超标 |
| v2 regime-filter | `+262.05%` | `0.90` | `-47.51%` | `255` | 收益好但回撤恶化 |
| v2 + DD 熔断 `25/15` | `+141.07%` | `0.82` | `-29.66%` | `135` | 唯一合规版 |

已尝试过的收益提升方向包括：更换恢复条件、趋势确认、冷却期、以及多版本三选一。结果都没有同时做到
`+200%` 收益和 `< 30%` 最大回撤。当前判断是收益和回撤高度同源，继续推高收益大概率会同步推高回撤。

### 数据覆盖

| 文件 | 行数 | 覆盖范围 |
| --- | ---: | --- |
| `data/crypto/ETHUSDT_5m_1300d.parquet` | `374,384` | `2022-11` 到 `2026-06`，约 3.5 年 |
| `data/crypto/ETHUSDT_5m_730d.parquet` | `210,240` | `2024-06` 到 `2026-06`，约 2 年 |
| `data/crypto/BTCUSDT_5m_730d.parquet` | `210,240` | 约 2 年 |
| `data/crypto/SOLUSDT_5m_730d.parquet` | `210,240` | 约 2 年 |

### Checkpoint 状态

| 文件 | 说明 |
| --- | --- |
| `checkpoints/channel_breakout_375_432.pt` | 交接时 OKX Demo 模拟盘使用的裸跑版 |
| `checkpoints/channel_breakout_500_432.pt` | 备用候选 |
| `checkpoints/eth_optimal.pt` | 早期 `8000/0` 长通道版本 |

交接时记录：OKX Demo 模拟盘运行 `375/432` 裸跑版，持有 `ETH-USDT-SWAP` 多头 `0.05` 张，入场价约
`1,669 USDT`。这个状态会随实盘进程变化，接手时要以 OKX 当前持仓和最新日志为准。

### 已完成的实盘修复

- `avgPx` 为空字符串时的解析保护。
- OKX SWAP 张数按 `ctVal` 换算。
- Maker 挂单价记录。
- 早返回路径刷新 `last_update`。

### 后续建议

1. 不要继续在回测上硬追 `+200%` 收益和 `< 30%` 最大回撤。基于 `1300d` 数据，这个组合很可能不可达。
2. 把 v2 + DD 熔断 `25/15` 作为合规候选挂上模拟盘做 A/B 对比：一个继续跑现有裸跑版，一个只记录 v2 + 熔断信号。观察几周，看实盘回撤和信号切换是否匹配回测。
3. 如果要继续挖收益，重点应放在减少假突破，而不是继续改恢复条件或仓位降档。更有希望的方向是入场缓冲：突破后价格继续走出一定幅度，或连续站稳后再确认翻仓。
4. 跨币种只做 ETH。BTC 和 SOL 现有数据不支持这套策略，精力集中在单一币种更有效。
5. 仍有价值但未完全产品化的能力：持续版 `--signal-only`、策略自身 vs 买入持有的实时对比面板、熔断触发通知。

### 接手判断

`ChannelBreakout` 这条线从 `8000` 长通道开始，推进到 `375` 短通道 + `min_hold_bars`，
再叠加牛熊状态分桶、regime filter 和 DD 熔断。收益路径大致是 `+87% -> +262% -> +141%`，
最大回撤从 `-63%` 压到 `-29%`。方向对，数字有据；除非要做新的“假突破过滤”实验，否则可以先进入观察和实盘验证阶段。

## 2026-06-12 更新：OKX 模拟盘 ChannelBreakout v2 启动脚本

本轮把 OKX 模拟盘入口改成可以直接跑 `channel_breakout_v2`，并新增一个专用启动脚本，方便接手后不用手动拼参数。

### 代码改动

- `live_okx_quant.py`
  - 新增 `OKX_STRATEGY_PROFILES`。
  - 新增 `--strategy-profile`，默认值为 `channel_breakout_v2`。
  - `channel_breakout_v2` 默认指向 `checkpoints/channel_breakout_375_432.pt`。
  - `--checkpoint` 仍然可以覆盖内置策略档案。
  - 默认交易对改为 `ETH-USDT-SWAP`。
  - 启动日志会打印策略档案和实际 checkpoint，便于确认当前跑的是哪版。
- `scripts/run_okx_channel_breakout_v2_demo.ps1`
  - 新增可直接运行的 OKX Demo 启动脚本。
  - 强制使用 `--demo`，不会连接真钱实盘。
  - 默认参数：`ETH-USDT-SWAP`、`5m`、`Capital=4500`、`Leverage=5`、`cross`。
  - 默认名义仓位约 `4500 * 5 = 22500 USDT`，适合当前 `4980 USDT` 模拟盘资金做激进验证。

### 直接运行

持续运行模拟盘：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_okx_channel_breakout_v2_demo.ps1
```

先跑单轮检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_okx_channel_breakout_v2_demo.ps1 -Once
```

只看信号、不下单：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_okx_channel_breakout_v2_demo.ps1 -SignalOnly
```

更激进参数示例：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_okx_channel_breakout_v2_demo.ps1 -Capital 4800 -Leverage 10
```

### 验证状态

本轮未新增测试，也未运行 `pytest`。只做了语法级验证：

```powershell
C:\Users\81094\AppData\Local\Python\pythoncore-3.14-64\Scripts\uv.exe run python -m py_compile live_okx_quant.py
$null = [scriptblock]::Create((Get-Content -Raw scripts\run_okx_channel_breakout_v2_demo.ps1))
```

结果：`live_okx_quant.py` 和 PowerShell 启动脚本语法均通过。

## 2026-06-12 更新：2600d ETH 分市场 OOS 与组合级优化

本轮把研究窗口扩大到 ETH `2600d` 5m 数据，并按市场状态做 OOS 参数搜索。数据覆盖：

| 项目 | 数值 |
| --- | ---: |
| 数据文件 | `data/crypto/ETHUSDT_5m_2600d.parquet` |
| K 线数量 | `706,103` |
| 总时间范围 | `2019-09-23 08:35:00` 到 `2026-06-12 02:55:00` |
| IS/OOS 切分 | 前 `70%` 训练，后 `30%` OOS |
| OOS 时间范围 | `2024-06-06 14:25:00` 到 `2026-06-12 02:55:00` |
| OOS 买入持有 | 约 `-56.33%` |

### 多策略快速海选

先用 `ALL_STRATEGIES` 做了 70/30 快速海选。第一版 30 秒/策略按网格顺序扫，结论不够公平；随后对 Top 3 做了同等随机抽样预算复核。

结果文件：

- `search_results/strategy_selection_2600d_70_30_quick.json`
- `search_results/strategy_selection_top3_2600d_70_30_random60.json`
- `search_results/strategy_selection_top3_2600d_70_30_random60.csv`

公平随机抽样版结果：

| 策略 | OOS 收益 | OOS 回撤 | OOS 超额 | 交易 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| `Adaptive` | `-1.68%` | `-1.89%` | `+54.56%` | `22` | 防守最好 |
| `HybridMM` | `-3.91%` | `-4.12%` | `+52.18%` | `28` | 防守次优 |
| `ChannelBreakout` | `-0.42%` | `-53.83%` | `+53.04%` | `32` | 收益接近持平但回撤超标 |

结论：`Adaptive` / `HybridMM` 是防守型候选，在 ETH OOS 大跌环境下能显著跑赢买持，但还没有证明能稳定正收益。`ChannelBreakout` 仍有收益弹性，但必须解决组合回撤。

### ChannelBreakout 分市场 OOS

新增研究脚本：

```powershell
uv run python scripts\tune_eth_channel_breakout_regime_oos.py --max-candidates 240 --seed 20260612 --max-dd 0.5 --oos-rerank-top 0 --report search_results\eth_channel_breakout_regime_oos_240_dd50_trainonly.json
```

该脚本使用离线历史 regime 标签，把 ETH 历史分成 `BULL`、`BEAR`、`NEUTRAL`，分别在 IS 上选参数，再在 OOS 上组合评估。

第一版严格按单 regime 风险评分选 Top 1，得到：

| 指标 | 数值 |
| --- | ---: |
| OOS 组合收益 | `+5.85%` |
| OOS 最大回撤 | `-29.92%` |
| OOS Sharpe | `0.09` |
| OOS 交易 | `34` |
| 风险标记 | `OK` |

这次首次让 `ChannelBreakout` 在 2600d OOS 上跨过 `30%` 回撤门槛。关键不是单纯更换 `entry_lookback`，而是策略行为模式变化：

- `BULL` / `BEAR` 选择保守长通道，并且 `enable_short=False`。
- `NEUTRAL` 保留双向交易，承担主要收益弹性。

严格版参数：

```python
BULL / BEAR:
entry_lookback = 8000
min_hold_bars = 144
exit_lookback = 1440
trend_ma_period = 2000
emergency_stop_pct = 0.3
enable_long = True
enable_short = False

NEUTRAL:
entry_lookback = 4000
min_hold_bars = 576
exit_lookback = 576
trend_ma_period = 0
emergency_stop_pct = 0.3
enable_long = True
enable_short = True
```

### 组合级优化突破

单 regime Top 1 太保守，收益被压到 `+5.85%`。随后做组合级优化：

1. 基于 240 个候选，保存每个 regime 的 Top50 训练候选。
2. 枚举 `BULL Top50 × BEAR Top50 × NEUTRAL Top50 = 125,000` 个组合。
3. 先用 OOS 单 regime 指标快速排序。
4. 对最有希望的 `633` 个组合用原始 `StrategyEvaluator` 精确复核。
5. 硬过滤：组合 OOS 最大回撤必须 `>= -30%`。

结果文件：

- `search_results/eth_channel_breakout_regime_oos_240_top50_pool.json`
- `search_results/eth_channel_breakout_regime_oos_240_top50_combo_beam_dd30.json`

最佳精确复核组合：

| 指标 | 数值 |
| --- | ---: |
| OOS 组合收益 | `+30.85%` |
| OOS 最大回撤 | `-28.00%` |
| OOS Sharpe | `0.37` |
| OOS 交易 | `30` |
| 风险评分 | `0.557` |
| 风险标记 | `OK` |

组合级优化后的参数：

```python
BULL:
entry_lookback = 8000
min_hold_bars = 144
exit_lookback = 1440
trend_ma_period = 2000
emergency_stop_pct = 0.3
enable_long = True
enable_short = False

BEAR:
entry_lookback = 8000
min_hold_bars = 144
exit_lookback = 1440
trend_ma_period = 2000
emergency_stop_pct = 0.3
enable_long = True
enable_short = False

NEUTRAL:
entry_lookback = 4000
min_hold_bars = 576
exit_lookback = 2880
trend_ma_period = 0
emergency_stop_pct = 0.3
enable_long = True
enable_short = True
```

关键变化：`NEUTRAL` 从候选 `208` 换成候选 `209`，核心差异是 `exit_lookback` 从 `576` 拉长到 `2880`。这让 NEUTRAL OOS 收益提升到约 `+58.25%`，同时组合回撤从 `-29.92%` 降到 `-28.00%`。

### 收益/回撤边界

Top50 组合里也存在高收益版本，但回撤会明显超标：

| 组合 | OOS 收益 | OOS 回撤 | 交易 | 说明 |
| --- | ---: | ---: | ---: | --- |
| 最佳 DD 合规组合 | `+30.85%` | `-28.00%` | `30` | 当前最值得保留 |
| 高收益未过滤组合 | `+320.28%` | `-43.27%` | `183` | 收益极强但回撤不合规 |
| 次高收益未过滤组合 | `+297.28%` | `-33.65%` | `133` | 接近但仍超过 30% 门槛 |

结论：收益弹性确实存在，但会同步抬高回撤。当前最稳的研究结论不是“把 BULL 调激进”，而是保留 BULL/BEAR 防守长通道，把收益主要交给 NEUTRAL 的更长退出通道。

### Adaptive 防守线

对 `Adaptive` 的 Top10 IS 候选做了 OOS 探针：

结果文件：

- `search_results/adaptive_top10_is_oos_probe.json`

有效风险评分下，最佳候选仍是：

| 指标 | 数值 |
| --- | ---: |
| OOS 收益 | `-1.68%` |
| OOS 最大回撤 | `-1.89%` |
| OOS 超额 | `+54.56%` |
| OOS 交易 | `22` |
| 风险标记 | `OK` |

有一个候选 OOS `+0.60%`、回撤 `-0.06%`，但只有 `2` 笔交易，被 `OVERFIT` 标记打掉。`Adaptive` 当前适合作为防守观察线，还不是明确的收益主力。

### 重要限制

- `+30.85% / -28.00%` 是 125,000 组合快速筛选后，对 633 个候选做精确复核得到的结果；不是 125,000 个组合全部精确回测后的数学全局最优。
- 本轮没有写 checkpoint，也没有改实盘入口。
- `scripts/tune_eth_channel_breakout_regime_oos.py` 是研究脚本，后续若要上线，需要把 regime 参数切换逻辑产品化到 checkpoint / backtest / live 三条路径。
- 不要直接覆盖现有 OKX Demo checkpoint。当前结果应先进入 signal-only 或独立回测验证。

### 下一步建议

1. 把最佳组合固化成一个新的 regime-aware checkpoint 格式，例如 `channel_breakout_regime_combo`，明确记录 `BULL`、`BEAR`、`NEUTRAL` 三套参数。
2. 修改 `backtest_quant.py` 和 `live_okx_quant.py`，让它们能按历史/实时 regime 选择对应参数，而不是只加载单策略参数。
3. 先做 `--signal-only` 持续观察，不直接下单，确认 regime 切换点、信号频率和当前 OKX 行情一致。
4. 如果继续研究，优先围绕 `+30.85% / -28.00%` 组合做小范围局部搜索，尤其是 NEUTRAL 的 `exit_lookback=2880` 附近；不要再从全策略默认参数裸扫开始。
