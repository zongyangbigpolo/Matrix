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
**单次限流自动重试**：被限流不会直接放弃当日数据，而是按「服务端建议等待时间」与
「指数退避」中的较大者等待后重试，最多重试 `SYNC_RETRY_ATTEMPTS` 次（默认 6 次），
尽力把这一次调用拉成功。批量请求被限流时**不会**退化成上百次单只请求，以免把额度打得更死。

相关配置：`SYNC_RETRY_ATTEMPTS`、`SYNC_RETRY_BASE_DELAY`、`SYNC_RETRY_MAX_DELAY`
（见 README 配置项表）。首次全量回填仍建议尽量**避开每日定时窗口**（如放到深夜或周末），
减少与日常增量互抢额度。

### 持续拉取直至完成（收盘后）

在上面「单次调用重试」之外，收盘后触发的日常任务还包了一层**「持续拉取直至完成」**外循环
（`matrix_etf/data/sync_runner.py` 的 `sync_until_stable`）：不再「拉不到就直接用旧数据发卡片」，
而是坚持补拉，直到当日数据基本拉全才发送策略卡片。流程如下：

1. **第 1 轮**执行完整增量同步；若最新交易日覆盖率 ≥ `SYNC_PERSIST_TARGET_COVERAGE`
   （默认 0.9）→ 立即成功，健康日几乎零额外延迟。
2. 否则每隔 `SYNC_PERSIST_ROUND_INTERVAL` 秒（默认 5 分钟）补拉一轮：
   - 已拉到当日、只是部分标的被限流缺口 → 只补缺口标的（`repair_latest_gaps`）；
   - 连当日数据都没拉到（整体失败，本地最新日仍停在昨日）→ 再跑一次完整增量。
3. 覆盖率不再提升（收敛）且 ≥ `SYNC_PERSIST_MIN_COVERAGE`（默认 0.5）即视为拉全——
   正常情况本就有少量新上市/停牌标的永远覆盖不到最新交易日，故完成判据用「收敛+下限」
   而非「必须 100%」。
4. 坚持 `SYNC_PERSIST_MAX_SECONDS`（默认 10800 秒 = 3 小时）仍拉不全：改发一张红色
   **「数据异常」告警卡片**到飞书主群，并**跳过本次策略推送**（不再发基于旧数据的卡片）。

因此 A 股/ETF 的仓库定时时间保持不变（19:15 / 20:30），但实际发卡时间可能因持续补拉而顺延；
systemd service 的 `TimeoutStartSec` 已相应放宽（ETF 4h、A 股 5h、美股 6h）。美股线改到
**白天 14:00（中国时区）**运行，与晚间 A 股/ETF 彻底错开，避免共享 60/min 限额时相互抢占；
且美股免费档为历史数据、预期最新交易日在中国时区不易精确推断，故美股仅按覆盖率收敛判定完成
（不做「必须拉到当日」的强校验）。

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
