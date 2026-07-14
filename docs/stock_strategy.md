# 股票策略说明 | Stock Strategies

> 股票线（`stock_main.py`）内置 6 套 A 股技术型选股策略，思路借鉴
> [NebulaStock](https://github.com/zongyangbigpolo)（同作者项目），代码为原创重写，
> 适配 tickflow 数据源。全部策略仅依赖价格、成交量与成交额，不使用基本面字段。

数据来源：tickflow `CN_Equity_A` 标的池（约 5500 只全 A 股），日 K 存入
`stock_daily`，基础信息与名称存入 `stock_basic`。策略统一通过
`engine.get_ohlcv(symbol)` / `engine.get_local_symbols()` 读取数据。

策略代码位于 `matrix_etf/strategy/stock/`，与 ETF 策略（`matrix_etf/strategy/etf/`）
按金融产品种类分目录管理。

---

## 策略一览

| 策略类 | 文件 | webhook_key | 类型 |
|--------|------|-------------|------|
| `MaVolumeStrategy` | `ma_volume.py` | `stock_ma_volume` | 逐只遍历 |
| `TurtleTradeStrategy` | `turtle_trade.py` | `stock_turtle` | 逐只遍历 |
| `HighTightFlagStrategy` | `high_tight_flag.py` | `stock_flag` | 逐只遍历 |
| `LimitUpShakeoutStrategy` | `limit_up_shakeout.py` | `stock_shakeout` | 逐只遍历 |
| `UptrendLimitDownStrategy` | `uptrend_limit_down.py` | `stock_limit_down` | 逐只遍历 |
| `RpsBreakoutStrategy` | `rps_breakout.py` | `stock_rps` | 横截面 |

---

## 1. 均线放量 `MaVolumeStrategy`

短期均线金叉且当日放量确认，捕捉趋势启动。

- 5 日均线上穿 20 日均线（昨日 `ma5 < ma20`，今日 `ma5 > ma20`）。
- 当日成交量 > 20 日均量 × `STOCK_MA_VOLUME_SURGE`（默认 1.5）。
- 至少需要 21 根 K 线。

## 2. 海龟突破 `TurtleTradeStrategy`

20 日新高突破 + 流动性 + 防诱多过滤（A 股改良版），结果按当日涨幅排序。

- 今日 `close` > 前 20 个交易日 `high` 的最大值（不含当日）。
- 今日成交额 > `STOCK_LIQUIDITY_MIN_AMOUNT`（默认 1 亿元）。
- 防诱多：今日为实体阳线（`close > open`）且相对昨日真涨（`close > 昨日 close`）。

## 3. 高旗形整理 `HighTightFlagStrategy`

强动量拉升后极度收敛缩量，等待再次突破。

- 强动量：过去 40 天区间 `high / low > 1.6`（区间涨幅超 60%）。
- 极度收敛：最近 10 天区间 `high / low < 1.15`（振幅低于 15%）。
- 高位抗跌：最近 10 天最低价 ≥ 40 天最高价 × 0.8。
- 缩量：今日成交量 < 过去 20 日均量 × 0.6。

## 4. 涨停洗盘 `LimitUpShakeoutStrategy`

昨日涨停后今日放量收阴但守住昨收，识别主力洗盘。

- 昨日涨停：昨日 `close ≥ 前日 close × 1.095`。
- 今日收阴：今日 `close < open`。
- 今日放量：今日 `volume > 昨日 volume × 2.0`。
- 支撑不破：今日 `low ≥ 昨日 close`。

## 5. 上升趋势跌停 `UptrendLimitDownStrategy`

上升趋势中的放量跌停，捕捉错杀反弹机会。

- 上升趋势：昨日 20 日均线 > 昨日 60 日均线。
- 放量跌停：今日 `close ≤ 昨日 close × 0.905`，且今日 `volume > 20 日均量 × 2.0`。
- 至少需要 60 根 K 线。

## 6. RPS 动量突破 `RpsBreakoutStrategy`

横截面相对强度选强 + 阶段新高突破（唯一横截面策略）。

1. 计算每只股票近 `STOCK_RPS_PERIOD`（默认 120）个交易日的涨幅。
2. 横截面按涨幅百分位排名得到 RPS，保留 RPS ≥ `STOCK_RPS_THRESHOLD`（默认 90）的强势股。
3. 在强势股中保留今日 `close ≥ 阶段滚动最高价 × 0.90` 的突破标的。

与其他逐只遍历的策略不同，本策略一次性读取全市场 `stock_daily` 做向量化排名。

---

## 与 NebulaStock 的差异

- 数据源由 baostock 改为 tickflow，标的 symbol 带交易所后缀（如 `600519.SH`）。
- 成交额字段统一用 `amount`（元），不再使用 `turnover`。
- 阈值参数（流动性、放量倍数、RPS 周期/阈值）改为通过 `.env` 配置。
- 中线四池基本面报告（`midterm_pool`）依赖估值/财务/行业数据，tickflow 暂未完整提供，
  当前未移植；股票线聚焦纯量价策略。

## 免责声明

本文档与策略仅用于量化研究与学习，不构成任何投资建议。股票投资有风险，入市需谨慎。
