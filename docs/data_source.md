# tickflow 数据源说明 | Data Source

Matrix 的唯一行情数据源是 [tickflow](https://github.com/tickflow-org/tickflow)
（官方文档 https://docs.tickflow.org ）。本项目**不使用 baostock**。

## 1. 为什么选 tickflow

- 官方 Python SDK，支持 A 股、ETF、美股、港股。
- 提供 **免费服务**（`TickFlow.free()`）：无需注册即可获取历史日 K 和标的信息。
- 维护了现成的 ETF 标的池 `CN_ETF`，省去自己拼装 ETF 列表。
- 需要实时行情/分钟线时，只要配置 API key 即可平滑升级到完整服务。

## 2. 安装

```bash
pip install "tickflow[all]" --upgrade
```

`[all]` 附带 pandas 和进度条支持。SDK 要求 Python 3.9+，本项目要求 3.10+。

## 3. 两种服务模式

| 模式 | 初始化 | 能力 | Matrix 用途 |
|------|--------|------|-------------|
| 免费服务 | `TickFlow.free()` | 历史日 K、标的信息、标的池 | 默认模式，收盘后跑 ETF 推荐足够 |
| 完整服务 | `TickFlow(api_key=...)` | 实时行情、分钟 K、更高频率 | 可选升级 |

Matrix 在 `TICKFLOW_API_KEY` 为空时使用免费服务，非空时使用完整服务。

### 限流与自动重试（60/min）

免费服务对请求频率有硬限制（约 **60 次/分钟，按来源 IP 计**）。当 ETF / A 股 / 美股
多条线在同一台机器上并发拉数（尤其是首次全量回填）时，容易触发
`请求频率超限 (60/min)，请 XXXms 后重试`。

Matrix 的数据引擎对所有 tickflow 网络调用（标的池、基础信息、日 K 批量/单只）都做了
**限流自动重试**：被限流不会直接放弃当日数据，而是按「服务端建议等待时间」与
「指数退避」中的较大者等待后重试，最多重试 `SYNC_RETRY_ATTEMPTS` 次（默认 6 次），
尽力把当日数据拉全；只有多次仍失败时才降级用本地历史数据，并在飞书卡片顶部标注
数据日期。批量请求被限流时**不会**退化成上百次单只请求，以免把额度打得更死。

相关配置：`SYNC_RETRY_ATTEMPTS`、`SYNC_RETRY_BASE_DELAY`、`SYNC_RETRY_MAX_DELAY`
（见 README 配置项表）。首次全量回填仍建议尽量**避开每日定时窗口**（如放到深夜或周末），
减少与日常增量互抢额度。

## 4. 统一标的代码

格式：`代码.市场后缀`，例如：

- `510300.SH`（沪深 300ETF）
- `159915.SZ`（创业板 ETF）

| 后缀 | 市场 |
|------|------|
| SH | 上海证券交易所 |
| SZ | 深圳证券交易所 |

## 5. Matrix 使用到的接口

### 5.1 ETF 标的池

```python
detail = tf.universes.get("CN_ETF")
# detail: dict(id, name, description, region, category, symbol_count, symbols)
symbols = detail["symbols"]   # ['158000.SZ', '159001.SZ', ...] 约 1500+ 只
```

### 5.2 标的基础信息

```python
ins = tf.instruments.get("510300.SH")
# {
#   'symbol': '510300.SH', 'exchange': 'SH', 'code': '510300',
#   'name': '沪深300ETF华泰柏瑞', 'region': 'CN', 'type': 'etf',
#   'ext': {'listing_date': '2012-05-28', 'total_shares': ..., 'float_shares': ...,
#           'tick_size': 0.001, 'limit_up': ..., 'limit_down': ...}
# }
```

### 5.3 日 K 线

```python
# 单只
df = tf.klines.get("510300.SH", period="1d", count=250, as_dataframe=True)

# 批量（返回 dict{symbol: DataFrame}）
dfs = tf.klines.batch(symbols, period="1d", count=250, as_dataframe=True)
```

日 K DataFrame 列：

| 列 | 类型 | 说明 |
|----|------|------|
| symbol | str | 标的代码 |
| name | str | 标的名称 |
| timestamp | int64 | 毫秒时间戳 |
| trade_date | str | 交易日 `YYYY-MM-DD` |
| trade_time | str | 交易时间 |
| open / high / low / close | float | 开高低收 |
| volume | int | 成交量（份/股） |
| amount | float | 成交额（元） |

> 单次单标的最多 10000 根 K 线；`klines.batch` 支持 `max_workers`、`batch_size` 控制并发与分批。

## 6. 关键限制（影响策略设计）

- **ETF 没有 PE/PB/PS/ROE 等基本面字段**，Matrix 策略只使用价量数据。
- 免费服务**不提供实时行情和分钟线**，因此 Matrix 定位为**收盘后的日线级选 ETF**。
- 免费日 K 为历史数据，盘中不实时更新；建议在收盘后（如 19:00 后）运行。

## 7. Matrix 数据引擎映射

| tickflow 返回 | 写入的 SQLite 表 |
|---------------|------------------|
| `universes.get("CN_ETF").symbols` + `instruments` | `etf_basic` |
| `klines.get/batch(period="1d")` | `etf_daily` |
| 由 `etf_daily` 计算 | `etf_metrics` |

引擎实现见 `matrix_etf/data/engine.py`。
