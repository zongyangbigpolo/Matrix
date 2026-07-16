# Matrix 策略总览 | All Strategies

> 一份文档速览 Matrix 内置的**全部 17 套选股策略**（7 套 ETF + 6 套 A 股 + 4 套美股），
> 含中英文名称、飞书路由标识（`webhook_key`）与核心思想。
> 需要完整规则、参数与指标定义时，见文末的详细手册链接。

Matrix 的所有策略都是**纯量价技术型**：只依赖 tickflow 提供的价格、成交量与成交额，
不使用基本面字段。三条流水线**相互隔离**（独立数据库、独立入口、独立策略目录、独立飞书路由），
互不影响：

- **ETF 线**（`main.py`）：`CN_ETF` 标的池 → `data/matrix_etf.db` → `matrix_etf/strategy/etf/`
- **A 股线**（`stock_main.py`）：`CN_Equity_A` 标的池 → `data/matrix_stock.db` → `matrix_etf/strategy/stock/`
- **美股线**（`us_main.py`）：`US_Equity` 标的池 → `data/matrix_us.db` → `matrix_etf/strategy/us/`

> **策略隔离原则**：三类市场各自维护独立的策略集；仅当某个策略思想能跨类别通用时，
> 才会在各自目录下按该市场的数据口径实现（例如「均线趋势 / 均线放量 / 放量突破 / RPS 动量」
> 在 ETF、A 股、美股中都有各自版本，但绝不共享数据或路由）。A 股特有的涨停 / 跌停类策略
> 因美股无涨跌停机制而**不在美股线出现**。

飞书推送卡片统一显示**中文策略名**，其英文类名 ↔ 中文名的映射集中维护在
[`matrix_etf/strategy/names.py`](../matrix_etf/strategy/names.py)。

---

## 一、ETF 策略（7 套）

所有 ETF 策略先执行**硬性过滤**：上市满 60 个交易日；近 20 日平均成交额 ≥ 流动性阈值
（`LIQUIDITY_MIN_AMOUNT`，默认 5000 万元）。

| # | 中文名 | 英文类名 | `webhook_key` | 核心思想 |
|---|--------|----------|---------------|----------|
| 1 | 相对强度动量 | `RpsMomentumStrategy` | `rps` | 欧奈尔 RPS：横截面比较中期涨幅，只买最强一档且仍处强势区 |
| 2 | 均线趋势 | `TrendMaStrategy` | `trend` | 多头排列（`close>MA50>MA200`）+ 上穿 MA50 确认进场 |
| 3 | 放量突破 | `BreakoutVolumeStrategy` | `breakout` | 突破 N 日新高 + 成交额放大 + 当日阳线，资金进场信号 |
| 4 | 强势回踩 | `MeanReversionStrategy` | `pullback` | 仅在长期上升趋势（站上 MA200）中，回踩 MA20 且 RSI 超卖时买入 |
| 5 | 风险调整动量 | `RiskAdjustedMomentumStrategy` | `mega7_momentum` | Mega7 风格：多周期正动量 / 波动率的「上涨效率」打分 |
| 6 | 成交额确认动量 | `VolumeConfirmedMomentumStrategy` | `mega7_volume` | 在风险调整动量基础上，要求短期成交额相对中期放大 |
| 7 | 低波趋势轮动 | `LowVolTrendRotationStrategy` | `mega7_lowvol` | Mega7 风格逆波动偏好：多头趋势中优先单位波动趋势强度更高者 |

> 另有 `--etf-report`：基于 `etf_metrics` 生成「四梯队」（动量领先 / 趋势健康 / 防御稳健 / 观察池）
> Markdown 综合报告，不参与飞书推送。

详细规则、排序口径、指标（`etf_metrics`）与参数：见 **[docs/etf_strategy.md](etf_strategy.md)**。

---

## 二、A 股策略（6 套）

数据来源 tickflow `CN_Equity_A`（约 5500 只全 A 股）。除 RPS 动量突破为横截面向量化排名外，
其余 5 套均为逐只遍历。股票线流动性阈值单独配置（`STOCK_LIQUIDITY_MIN_AMOUNT`，默认 1 亿元）。

| # | 中文名 | 英文类名 | `webhook_key` | 核心思想 |
|---|--------|----------|---------------|----------|
| 1 | 均线放量 | `MaVolumeStrategy` | `stock_ma_volume` | 5 日线上穿 20 日线（金叉）+ 当日放量确认趋势启动 |
| 2 | 海龟突破 | `TurtleTradeStrategy` | `stock_turtle` | 20 日新高突破 + 流动性 + 防诱多（实体阳线且真涨） |
| 3 | 高旗形整理 | `HighTightFlagStrategy` | `stock_flag` | 强动量拉升后极度收敛缩量、高位抗跌，等待再次突破 |
| 4 | 涨停洗盘 | `LimitUpShakeoutStrategy` | `stock_shakeout` | 昨日涨停后今日放量收阴但守住昨收，识别主力洗盘 |
| 5 | 上升趋势跌停 | `UptrendLimitDownStrategy` | `stock_limit_down` | 上升趋势中的放量跌停，捕捉错杀反弹机会 |
| 6 | RPS动量突破 | `RpsBreakoutStrategy` | `stock_rps` | 横截面 RPS 选强 + 阶段新高突破（唯一横截面策略） |

详细规则与参数：见 **[docs/stock_strategy.md](stock_strategy.md)**。

---

## 三、美股策略（4 套）

数据来源 tickflow `US_Equity`（约 1.2 万只美股）。**免费档美股不返回成交额**（`amount` 恒为 0），
故流动性统一改用「美元成交额 = `close × volume`」估算（`US_LIQUIDITY_MIN_DOLLAR_VOLUME`，默认 2000 万美元/日）；
美股无涨跌停，不含涨停 / 跌停类策略。

| # | 中文名 | 英文类名 | `webhook_key` | 核心思想 |
|---|--------|----------|---------------|----------|
| 1 | 美股相对强度动量 | `UsRpsMomentumStrategy` | `us_rps` | 横截面 RPS 选强 + 趋势（close≥MA50）与美元成交额过滤（唯一横截面策略） |
| 2 | 美股均线趋势 | `UsTrendMaStrategy` | `us_trend` | 多头排列（close>MA50>MA200）+ 上穿 MA50 确认进场 |
| 3 | 美股均线放量 | `UsMaVolumeStrategy` | `us_ma_volume` | 5 日线上穿 20 日线（金叉）+ 当日放量确认趋势启动 |
| 4 | 美股放量突破 | `UsBreakoutVolumeStrategy` | `us_breakout` | 突破 N 日新高 + 放量 + 当日阳线，资金进场信号 |

详细规则与参数：见 **[docs/us_strategy.md](us_strategy.md)**。

---

## 四、飞书路由说明

- 每套策略有独立 `webhook_key`。推送时优先使用 `.env` 中 `STRATEGY_WEBHOOK_<KEY>` 配置的
  专属机器人；未配置则回退到默认 `FEISHU_WEBHOOK_URL`。
- 想让**所有策略推送到同一个群**：只保留默认 `FEISHU_WEBHOOK_URL`，注释掉全部
  `STRATEGY_WEBHOOK_*` 即可。
- 想**分群推送**：把对应 `STRATEGY_WEBHOOK_<KEY>` 换成各自真实的 webhook 地址。

---

## 五、延伸阅读

- [docs/etf_strategy.md](etf_strategy.md) — ETF 策略完整手册（规则 / 指标 / 参数）
- [docs/stock_strategy.md](stock_strategy.md) — A 股策略完整说明
- [docs/us_strategy.md](us_strategy.md) — 美股策略完整说明
- [docs/architecture.md](architecture.md) — 系统架构
- [docs/data_source.md](data_source.md) — 数据源（tickflow）说明

> 免责声明：本文档与系统输出仅用于量化研究与学习，不构成任何投资建议。投资有风险，入市需谨慎。
