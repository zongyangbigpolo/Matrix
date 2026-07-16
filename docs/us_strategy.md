# 美股策略说明 | US Stock Strategies

> 美股线（`us_main.py`）内置 4 套技术型选股策略，与 A 股线 / ETF 线**完全隔离**：
> 独立数据库、独立标的池、独立入口、独立飞书路由。全部策略仅依赖价格与成交量。

数据来源：tickflow `US_Equity` 标的池（约 1.2 万只美股），日 K 存入 `stock_daily`，
基础信息与中文名称存入 `stock_basic`（均在美股独立数据库 `data/matrix_us.db` 内）。
策略统一通过 `engine.get_ohlcv(symbol)` / `engine.get_local_symbols()` 读取数据。

策略代码位于 `matrix_etf/strategy/us/`，与 A 股（`matrix_etf/strategy/stock/`）、
ETF（`matrix_etf/strategy/etf/`）按金融产品种类分目录管理，互不引用。

---

## ⚠️ 关键数据限制：美股无成交额，改用「美元成交额」

免费档 tickflow 对美股**不返回成交额**（日 K 的 `amount` 字段恒为 `0`）。因此：

- 美股策略的流动性过滤**不能使用 `amount`**，统一改用
  **美元成交额 = `close × volume`** 估算（近 20 日均值），阈值为
  `US_LIQUIDITY_MIN_DOLLAR_VOLUME`（默认 2000 万美元/日）。
- 该逻辑封装在 `BaseStrategy._passes_dollar_volume(close, volume, threshold)`。

此外，美股**没有涨跌停机制**，故不复用 A 股的「涨停洗盘 / 上升趋势跌停」等策略；
美股策略集是针对美股市场特性单独挑选的一组趋势 / 动量 / 突破策略。

---

## 策略一览

| 策略类 | 文件 | webhook_key | 类型 |
|--------|------|-------------|------|
| `UsRpsMomentumStrategy` | `rps_momentum.py` | `us_rps` | 横截面 |
| `UsTrendMaStrategy` | `trend_ma.py` | `us_trend` | 逐只遍历 |
| `UsMaVolumeStrategy` | `ma_volume.py` | `us_ma_volume` | 逐只遍历 |
| `UsBreakoutVolumeStrategy` | `breakout_volume.py` | `us_breakout` | 逐只遍历 |

---

## 1. 美股相对强度动量 `UsRpsMomentumStrategy`

横截面 RPS 选强 + 趋势与流动性过滤（欧奈尔思路，唯一横截面策略）。

1. 计算每只美股近 `US_RPS_PERIOD`（默认 120）个交易日涨幅，横截面按百分位排名得 RPS。
2. 保留 RPS ≥ `US_RPS_THRESHOLD`（默认 90）的强势股。
3. 趋势过滤：今日 `close ≥ MA50`（仍处上升趋势）。
4. 流动性过滤：近 20 日平均美元成交额（`close × volume`）≥ `US_LIQUIDITY_MIN_DOLLAR_VOLUME`。

结果按 RPS 从高到低排序。与逐只遍历策略不同，本策略一次性读取全市场
`stock_daily` 做向量化排名。

## 2. 美股均线趋势 `UsTrendMaStrategy`

多头排列且当日上穿 MA50，捕捉趋势重启，结果按趋势强度排序。

- 多头排列：`close > MA50 > MA200`。
- 上穿确认：昨日 `close ≤ MA50` 且今日 `close > MA50`。
- 流动性：近 20 日平均美元成交额 ≥ `US_LIQUIDITY_MIN_DOLLAR_VOLUME`。
- 排序：按趋势强度 `close / MA200 - 1` 从高到低。
- 至少需要 200 根 K 线。

## 3. 美股均线放量 `UsMaVolumeStrategy`

短期均线金叉且当日放量确认，捕捉趋势启动。

- 5 日均线上穿 20 日均线（昨日 `ma5 < ma20`，今日 `ma5 > ma20`）。
- 当日成交量 > 20 日均量 × `US_MA_VOLUME_SURGE`（默认 1.5）。
- 流动性：近 20 日平均美元成交额 ≥ `US_LIQUIDITY_MIN_DOLLAR_VOLUME`。
- 至少需要 21 根 K 线。

## 4. 美股放量突破 `UsBreakoutVolumeStrategy`

突破阶段新高 + 放量 + 阳线确认资金进场，结果按当日涨幅排序。

- 突破新高：今日 `close` > 前 `US_BREAKOUT_PERIOD`（默认 60）个交易日 `high` 的最大值（不含当日）。
- 放量：今日成交量 > `US_BREAKOUT_PERIOD` 日均量 × `US_VOLUME_SURGE`（默认 1.5）。
- 阳线：今日 `close > open`。
- 流动性：近 20 日平均美元成交额 ≥ `US_LIQUIDITY_MIN_DOLLAR_VOLUME`。

---

## 运行与调度

```bash
python us_main.py --backfill        # 首次：回填 US_Equity 全量历史日 K
python us_main.py                   # 日常：增量同步 + 跑美股策略 + 飞书推送
python us_main.py --symbols AAPL.US,MSFT.US   # 仅处理指定美股
```

- 免费档美股日 K 为历史数据，日常运行在北京时间晚间取到的是**上一个已收盘的美股交易日**。
- systemd 定时器 `matrix-us.timer` 默认 `Mon..Fri 21:45`，与 ETF（19:15）、A 股（20:30）错开。

## 免责声明

本文档与策略仅用于量化研究与学习，不构成任何投资建议。美股投资有风险，入市需谨慎。
