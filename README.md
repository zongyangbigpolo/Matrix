# Matrix

> ETF + A 股 + 美股推荐系统：tickflow 数据同步 + SQLite 本地存储 + 多策略选股 + 飞书推送。

## 👋 想体验？扫码加入飞书群

系统每天收盘后会把 ETF、A 股与美股的策略选股结果自动推送到飞书群。**想直接体验推送效果，用飞书扫描下方二维码加入体验群即可：**

<p align="center">
  <img src="docs/assets/feishu-group-qr.png" alt="Matrix 体验群二维码" width="320" />
</p>

<p align="center">
  <b>Matrix-体验群003</b><br/>
  用飞书 App 扫一扫加入，即可实时收到每日 ETF / A 股 / 美股策略推荐卡片。<br/>
  <sub>二维码有效期至 2027/7/15；如已过期或无法加入，欢迎提 issue 联系。</sub>
</p>

---

Matrix 面向阿里云 ECS / Alibaba Cloud Linux 部署。系统每天收盘后从
[tickflow](https://github.com/tickflow-org/tickflow) 同步行情，运行内置技术型策略，
并把候选标的推送到飞书群。**数据源完全使用 tickflow，不依赖 baostock。**

系统按金融产品种类拆成三条**完全独立**的流水线：

- **ETF 线**（`main.py`）：`CN_ETF` 标的池 → `data/matrix_etf.db` → ETF 策略。
- **A 股线**（`stock_main.py`）：`CN_Equity_A` 全 A 股标的池 → `data/matrix_stock.db` → A 股策略。
- **美股线**（`us_main.py`）：`US_Equity` 标的池（约 1.2 万只）→ `data/matrix_us.db` → 美股策略。

三条线各自维护数据库、标的池与策略集，互不影响，可独立部署与定时。

## 功能概览

- 使用 tickflow 拉取 ETF（`CN_ETF`）、A 股（`CN_Equity_A`）与美股（`US_Equity`）标的池、日 K 与基础信息（免费服务，无需注册）。
- 使用本地 SQLite 保存数据：ETF `data/matrix_etf.db`、A 股 `data/matrix_stock.db`、美股 `data/matrix_us.db`，三库物理隔离。
- 支持全量回填、日常增量同步、标的池同步、缺口补拉（ETF 另有指标刷新与四梯队报告）。
- 内置七套 **ETF** 技术策略：相对强度动量、均线趋势、放量突破、强势回踩，
  以及 Mega7 风格的风险调整动量、成交额确认动量、低波趋势轮动。
- 内置六套 **A 股** 技术策略：均线放量、海龟突破、高旗形整理、涨停洗盘、上升趋势跌停、RPS 动量突破。
- 内置四套 **美股** 技术策略：美股相对强度动量、美股均线趋势、美股均线放量、美股放量突破
  （美股免费档无成交额，流动性改用「美元成交额 = close×volume」估算；美股无涨跌停，故不含涨停/跌停类策略）。
- 策略按金融产品种类分目录管理：`matrix_etf/strategy/etf/`、`matrix_etf/strategy/stock/` 与 `matrix_etf/strategy/us/`，互不引用。
- 支持按策略路由到不同飞书机器人。
- 提供 Alibaba Cloud Linux 可用的运行脚本和 systemd 定时任务模板。

Matrix 的策略体系以价格、成交量与成交额为主。
**策略总览（全部 17 套，含中英文名与飞书路由）见 [docs/strategies.md](docs/strategies.md)。**
更多细节详见 [docs/architecture.md](docs/architecture.md)、[docs/data_source.md](docs/data_source.md)、
[docs/etf_strategy.md](docs/etf_strategy.md)、[docs/stock_strategy.md](docs/stock_strategy.md)、
[docs/us_strategy.md](docs/us_strategy.md)。

## 运行环境

推荐生产环境：

- Alibaba Cloud Linux 3 / 2，或其他 systemd Linux 发行版
- Python 3.10+
- Git
- 出站网络可访问 tickflow 服务和飞书 Webhook

本项目也可在 macOS 上开发和测试，但部署说明以阿里云 Linux 为准。

## 快速开始（本地）

```bash
# 1. 安装依赖（推荐 uv）
uv sync --extra dev

# 2. 准备配置
cp .env.example .env
#   编辑 .env，至少填写 FEISHU_WEBHOOK_URL

# 3. 首次回填 ETF 历史（免费服务，约数分钟）
uv run python main.py --backfill

# 4. 日常运行（增量同步 + 跑策略 + 推送）
uv run python main.py

# 5. （可选）股票线：首次回填 A 股历史（约 5500 只，耗时较长）
uv run python stock_main.py --backfill

# 6. （可选）股票线日常运行
uv run python stock_main.py

# 7. （可选）美股线：首次回填美股历史（约 1.2 万只，耗时较长）
uv run python us_main.py --backfill

# 8. （可选）美股线日常运行
uv run python us_main.py
```

不使用 uv 时，可用标准 venv：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python main.py --backfill
```

## 命令行用法

### ETF 线（`main.py`）

| 命令 | 说明 |
|------|------|
| `python main.py` | 日常模式：增量同步 + 刷新指标 + 跑策略 + 推送（本地无数据时自动回填） |
| `python main.py --backfill` | 回填模式：同步标的池 + 拉取 CN_ETF 全量历史日 K |
| `python main.py --sync-universe` | 仅同步 ETF 标的池与基础信息 |
| `python main.py --refresh-metrics` | 仅重算 `etf_metrics` 指标 |
| `python main.py --etf-report` | 生成四梯队 ETF Markdown 报告（写入 `reports/`） |
| `python main.py --symbols 510300.SH,159915.SZ` | 仅处理指定 ETF |
| `python main.py --report-limit 20` | 控制报告每梯队展示数量 |
| `python main.py --force` | 日常模式下忽略周末/休市日保护，强制运行 |

### 股票线（`stock_main.py`，与 ETF 线完全解耦）

| 命令 | 说明 |
|------|------|
| `python stock_main.py` | 日常模式：增量同步 + 跑股票策略 + 推送（本地无数据时自动回填） |
| `python stock_main.py --backfill` | 回填模式：同步标的池 + 拉取 CN_Equity_A 全量历史日 K |
| `python stock_main.py --sync-universe` | 仅同步股票标的池与基础信息（`stock_basic`） |
| `python stock_main.py --symbols 600519.SH,000001.SZ` | 仅处理指定股票 |
| `python stock_main.py --force` | 日常模式下忽略周末/休市日保护，强制运行 |

### 美股线（`us_main.py`，与 ETF 线 / A 股线完全隔离）

| 命令 | 说明 |
|------|------|
| `python us_main.py` | 日常模式：增量同步 + 跑美股策略 + 推送（本地无数据时自动回填） |
| `python us_main.py --backfill` | 回填模式：同步标的池 + 拉取 US_Equity 全量历史日 K |
| `python us_main.py --sync-universe` | 仅同步美股标的池与基础信息（`stock_basic`，美股独立库） |
| `python us_main.py --symbols AAPL.US,MSFT.US` | 仅处理指定美股 |
| `python us_main.py --force` | 日常模式下忽略周末/休市日保护，强制运行 |

## 配置项（.env）

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `FEISHU_WEBHOOK_URL` | 是 | — | 默认飞书 Webhook（fallback） |
| `DB_PATH` | 否 | `data/matrix_etf.db` | SQLite 路径 |
| `START_DATE` | 否 | `2020-01-01` | 回填起始日期 |
| `TICKFLOW_API_KEY` | 否 | 空 | 留空用免费服务；非空用完整服务 |
| `ETF_UNIVERSE` | 否 | `CN_ETF` | tickflow ETF 标的池 id |
| `STOCK_DB_PATH` | 否 | `data/matrix_stock.db` | 股票线 SQLite 路径（与 ETF 库独立） |
| `STOCK_UNIVERSE` | 否 | `CN_Equity_A` | tickflow A 股标的池 id |
| `US_DB_PATH` | 否 | `data/matrix_us.db` | 美股线 SQLite 路径（与 ETF / A 股库独立） |
| `US_UNIVERSE` | 否 | `US_Equity` | tickflow 美股标的池 id（约 1.2 万只） |
| `LIQUIDITY_MIN_AMOUNT` | 否 | `50000000` | ETF 流动性门槛：近 20 日平均成交额（元） |
| `RPS_PERIOD` | 否 | `120` | 动量/RPS 回看天数 |
| `RPS_THRESHOLD` | 否 | `90` | RPS 百分位阈值 |
| `BREAKOUT_PERIOD` | 否 | `60` | 突破回看天数 |
| `VOLUME_SURGE` | 否 | `1.5` | 放量倍数 |
| `STOCK_LIQUIDITY_MIN_AMOUNT` | 否 | `100000000` | 股票流动性门槛：当日成交额（元） |
| `STOCK_MA_VOLUME_SURGE` | 否 | `1.5` | 股票均线放量策略的放量倍数 |
| `STOCK_RPS_PERIOD` | 否 | `120` | 股票 RPS 回看天数 |
| `STOCK_RPS_THRESHOLD` | 否 | `90` | 股票 RPS 百分位阈值 |
| `US_LIQUIDITY_MIN_DOLLAR_VOLUME` | 否 | `20000000` | 美股流动性门槛：近 20 日均「美元成交额=close×volume」（美元） |
| `US_MA_VOLUME_SURGE` | 否 | `1.5` | 美股均线放量策略的放量倍数 |
| `US_RPS_PERIOD` | 否 | `120` | 美股 RPS 回看天数 |
| `US_RPS_THRESHOLD` | 否 | `90` | 美股 RPS 百分位阈值 |
| `US_BREAKOUT_PERIOD` | 否 | `60` | 美股突破回看天数 |
| `US_VOLUME_SURGE` | 否 | `1.5` | 美股放量倍数 |
| `MEGA7_MOMENTUM_PERIODS` | 否 | `21,63,126` | Mega7 风格多周期动量窗口（日） |
| `MEGA7_TOP_N` | 否 | `10` | Mega7 风格策略最多输出数量 |
| `MEGA7_DOWNSIDE_THRESHOLD` | 否 | `0.5` | 下行频率过滤阈值 |
| `SKIP_NON_TRADING_DAY` | 否 | `true` | 日常模式是否跳过周末/配置休市日 |
| `CN_MARKET_HOLIDAYS` | 否 | 空 | 逗号分隔的 A 股休市日，格式 `YYYY-MM-DD` |
| `US_MARKET_HOLIDAYS` | 否 | 空 | 逗号分隔的美股休市日，格式 `YYYY-MM-DD` |
| `FEISHU_RETRY_ATTEMPTS` | 否 | `3` | 飞书请求对网络/临时错误的最大尝试次数 |
| `SYNC_RETRY_ATTEMPTS` | 否 | `6` | 数据同步遇 tickflow 限流（60/min）时的最大尝试次数 |
| `SYNC_RETRY_BASE_DELAY` | 否 | `2` | 同步重试的指数退避基准秒数 |
| `SYNC_RETRY_MAX_DELAY` | 否 | `60` | 同步重试单次等待上限秒数 |
| `SYNC_PERSIST_MAX_SECONDS` | 否 | `10800` | 「持续拉取直至完成」最长坚持时长（默认 3 小时） |
| `SYNC_PERSIST_ROUND_INTERVAL` | 否 | `300` | 每轮补拉之间的间隔秒数（默认 5 分钟） |
| `SYNC_PERSIST_TARGET_COVERAGE` | 否 | `0.9` | 最新交易日覆盖率达此比例即视为拉取完成 |
| `SYNC_PERSIST_MIN_COVERAGE` | 否 | `0.5` | 覆盖率收敛/超时后仍可接受的最低下限 |
| `STRATEGY_WEBHOOK_<KEY>` | 否 | — | 策略专属 webhook，KEY 见下表 |

ETF 策略与 webhook_key 对应关系：

| 策略 | webhook_key |
|------|-------------|
| RpsMomentumStrategy | `rps` |
| TrendMaStrategy | `trend` |
| BreakoutVolumeStrategy | `breakout` |
| MeanReversionStrategy | `pullback` |
| RiskAdjustedMomentumStrategy | `mega7_momentum` |
| VolumeConfirmedMomentumStrategy | `mega7_volume` |
| LowVolTrendRotationStrategy | `mega7_lowvol` |

股票策略与 webhook_key 对应关系（均带 `stock_` 前缀，与 ETF 推送解耦）：

| 策略 | webhook_key |
|------|-------------|
| MaVolumeStrategy | `stock_ma_volume` |
| TurtleTradeStrategy | `stock_turtle` |
| HighTightFlagStrategy | `stock_flag` |
| LimitUpShakeoutStrategy | `stock_shakeout` |
| UptrendLimitDownStrategy | `stock_limit_down` |
| RpsBreakoutStrategy | `stock_rps` |

美股策略与 webhook_key 对应关系（均带 `us_` 前缀，与 ETF / A 股推送解耦）：

| 策略 | webhook_key |
|------|-------------|
| UsRpsMomentumStrategy | `us_rps` |
| UsTrendMaStrategy | `us_trend` |
| UsMaVolumeStrategy | `us_ma_volume` |
| UsBreakoutVolumeStrategy | `us_breakout` |

## 部署到 Alibaba Cloud Linux

以下示例假设项目部署在 `/opt/Matrix`，使用 `root` 运行。若改用普通用户，需同步修改
systemd unit 中的路径与权限。

### 一步到位：启用 ETF + A 股 + 美股的定时筛选与飞书推送

想让服务器每个交易日收盘后**自动跑策略并把结果推到飞书**，完整流程就是下面 6 步，
详细说明见后续小节：

```bash
# 1. 装依赖（见 §1、§2）
sudo dnf install -y git curl ca-certificates gcc gcc-c++ make sqlite
curl -LsSf https://astral.sh/uv/install.sh | sh && source "$HOME/.local/bin/env"

# 2. 拉代码 + 装 Python 依赖（见 §3）
sudo git clone https://github.com/zongyangbigpolo/Matrix.git /opt/Matrix
cd /opt/Matrix && uv sync

# 3. 配飞书 webhook（见 §4，这一步决定推送去哪个群）
cp .env.example .env
vi .env                       # 至少填 FEISHU_WEBHOOK_URL

# 4. 首次回填历史数据（见 §5）
./scripts/run_matrix.sh --backfill    # ETF 线
./scripts/run_stock.sh  --backfill    # A 股线
./scripts/run_us.sh     --backfill    # 美股线

# 5. 装并启用三条线的定时任务（见 §6）
sudo cp deploy/systemd/matrix-etf.service   deploy/systemd/matrix-etf.timer   /etc/systemd/system/
sudo cp deploy/systemd/matrix-stock.service deploy/systemd/matrix-stock.timer /etc/systemd/system/
sudo cp deploy/systemd/matrix-us.service    deploy/systemd/matrix-us.timer    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-etf.timer matrix-stock.timer matrix-us.timer

# 6. 立刻手动跑一次，确认飞书能收到（见 §7）
sudo systemctl start matrix-etf.service
sudo systemctl start matrix-stock.service
sudo systemctl start matrix-us.service
```

跑通后就不用再管了：ETF 线每周一至周五 **19:15**、A 股线 **20:30**（晚间错开），
美股线放到**白天 14:00**（中国时区，此时上一美股交易日已完整收盘，且与晚间 A 股/ETF
彻底错开，避免共享 tickflow 免费档 60/min 限额时相互抢占）。三线自动执行、互不阻塞，
错过（如关机）会在开机后由 `Persistent=true` 补跑。收盘后每条线会**持续补拉**当日数据，
直到拉全或覆盖率达标才发送策略卡片；若坚持约 3 小时仍拉不全，则改发一张「数据异常」
告警卡片并跳过本次策略推送（详见 [数据源与限流说明](docs/data_source.md)）。
**只想启用其中某条线**时，跳过其余线的回填与 `enable` 即可（三条线完全独立）。

### 1. 安装系统依赖

Alibaba Cloud Linux 3：

```bash
sudo dnf update -y
sudo dnf install -y git curl ca-certificates gcc gcc-c++ make sqlite
```

Alibaba Cloud Linux 2：

```bash
sudo yum update -y
sudo yum install -y git curl ca-certificates gcc gcc-c++ make sqlite
```

### 2. 安装 uv（推荐）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

### 3. 拉取代码并安装依赖

```bash
sudo git clone https://github.com/zongyangbigpolo/Matrix.git /opt/Matrix
cd /opt/Matrix
uv sync
```

若不使用 uv：

```bash
cd /opt/Matrix
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4. 配置环境变量（飞书推送在这一步决定）

```bash
cd /opt/Matrix
cp .env.example .env
vi .env
```

**必填**：`FEISHU_WEBHOOK_URL`——所有策略默认推送到这个地址。获取方式：

1. 在目标飞书群里点「设置 → 群机器人 → 添加机器人 → 自定义机器人 Webhook」。
2. 复制生成的 Webhook 地址（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`）。
3. 填入 `.env` 的 `FEISHU_WEBHOOK_URL=`。若给机器人设置了「签名校验」，请改用飞书群自带的
   关键词/IP 白名单方式放行，本项目按自定义机器人无签名模式推送。

**可选：按策略分流到不同群**。若希望某个策略单独推到另一个群，为对应
`STRATEGY_WEBHOOK_<KEY>` 填上那个群机器人的 Webhook 即可；未配置的策略自动回退到
`FEISHU_WEBHOOK_URL`。`<KEY>` 取值见上文三张 webhook_key 对照表（ETF 如 `rps`、`trend`；
A 股如 `stock_rps`、`stock_turtle`；美股如 `us_rps`、`us_trend`）。例如：

```dotenv
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/default-xxxx
STRATEGY_WEBHOOK_RPS=https://open.feishu.cn/open-apis/bot/v2/hook/etf-rps-xxxx
STRATEGY_WEBHOOK_STOCK_RPS=https://open.feishu.cn/open-apis/bot/v2/hook/stock-rps-xxxx
STRATEGY_WEBHOOK_US_RPS=https://open.feishu.cn/open-apis/bot/v2/hook/us-rps-xxxx
```

其余配置（数据库路径、标的池、策略阈值等）均有默认值，可保持不动，详见上文
[配置项](#配置项env) 表。使用 tickflow 完整服务时再填 `TICKFLOW_API_KEY`，否则留空走免费服务。

### 5. 首次回填

ETF 线：

```bash
cd /opt/Matrix
./scripts/run_matrix.sh --backfill
```

A 股线（约 5500 只全 A 股，首次回填耗时较长）：

```bash
cd /opt/Matrix
./scripts/run_stock.sh --backfill
```

美股线（约 1.2 万只美股，首次回填耗时较长）：

```bash
cd /opt/Matrix
./scripts/run_us.sh --backfill
```

`run_matrix.sh` / `run_stock.sh` / `run_us.sh` 会自动优先使用 `.venv/bin/python`，其次
`uv run`，最后系统 `python3`，并各自通过独立 `flock` 锁文件防止定时任务并发重入（三条线互不阻塞）。

### 6. 配置 systemd 定时任务

ETF 线：

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-etf.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-etf.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-etf.timer
```

股票线（可选，与 ETF 线独立启停）：

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-stock.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-stock.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-stock.timer
```

美股线（可选，与 ETF / A 股线独立启停）：

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-us.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-us.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-us.timer
```

查看状态与日志：

```bash
systemctl list-timers 'matrix-*.timer'
systemctl status matrix-etf.service matrix-stock.service matrix-us.service
journalctl -u matrix-etf.service -n 100 --no-pager
journalctl -u matrix-stock.service -n 100 --no-pager
journalctl -u matrix-us.service -n 100 --no-pager
```

ETF 线默认在**周一至周五 19:15**、A 股线在 **20:30**（晚间错开），美股线放到**白天 14:00**
（中国时区，与晚间 A 股/ETF 彻底错开，避免共享 tickflow 免费档限速额度；此时上一美股交易日
已完整收盘）运行，`Persistent=true` 会在错过时补跑。由于收盘后各线会「持续拉取直至完成」
（默认最长坚持约 3 小时，见 `SYNC_PERSIST_*` 配置），systemd service 的 `TimeoutStartSec`
已相应放宽（ETF 4h、A 股 5h、美股 6h）。如需调整时间，编辑对应 `.timer` 的 `OnCalendar` 后
`systemctl daemon-reload`。

### 7. 验证：手动跑一次并确认飞书收到推送

装好定时任务后，不必等到收盘，立刻手动触发一次做端到端验证：

```bash
sudo systemctl start matrix-etf.service     # ETF 线
sudo systemctl start matrix-stock.service   # A 股线
sudo systemctl start matrix-us.service      # 美股线
```

然后检查执行结果与推送情况：

```bash
# 看本次运行日志，应出现「已推送 N 只标的」之类的成功记录
journalctl -u matrix-etf.service -n 100 --no-pager
journalctl -u matrix-stock.service -n 100 --no-pager
journalctl -u matrix-us.service -n 100 --no-pager

# 确认下次自动运行时间已排上
systemctl list-timers 'matrix-*.timer'
```

最后到对应飞书群确认收到了策略推送卡片。若没收到，按此顺序排查：

- 日志里若有 `FEISHU_WEBHOOK_URL` 相关报错 → `.env` 未填或地址错误；
- 日志显示各策略「选出 0 只」→ 属正常（当日无标的满足条件时不会推送）；
- 日志有 tickflow / 网络报错 → 检查 ECS 出站是否放行 tickflow 与飞书域名；
- 想临时忽略休市日保护强制跑，可 `./scripts/run_matrix.sh --force`（A 股线用
  `run_stock.sh --force`，美股线用 `run_us.sh --force`）。

## 目录结构

```
Matrix/
├── main.py                     # ETF 线 CLI 入口
├── stock_main.py               # A 股线 CLI 入口（与 ETF 线解耦）
├── us_main.py                  # 美股线 CLI 入口（与 ETF / A 股线解耦）
├── matrix_etf/
│   ├── core/                   # 配置 + 日志 + 交易日历
│   ├── data/
│   │   ├── engine.py           # ETF：tickflow 同步 + SQLite 存储
│   │   ├── stock_engine.py     # A 股：tickflow 同步 + SQLite 存储
│   │   ├── us_stock_engine.py  # 美股：tickflow 同步 + 独立 SQLite 存储
│   │   └── tickflow_client.py  # tickflow 客户端工厂（三引擎共享）
│   ├── strategy/
│   │   ├── base.py             # 共享策略基类
│   │   ├── names.py            # 英文策略名 → 中文名映射
│   │   ├── etf/                # ETF 策略（按产品种类划分）+ 四梯队报告
│   │   ├── stock/              # A 股策略（按产品种类划分）
│   │   └── us/                 # 美股策略（按产品种类划分，美元成交额口径）
│   └── notify/feishu.py        # 飞书推送（ETF / A 股 / 美股通用）
├── deploy/systemd/             # systemd service + timer（ETF / A 股 / 美股三线）
├── scripts/
│   ├── run_matrix.sh           # ETF 线运行脚本（flock 防并发）
│   ├── run_stock.sh            # A 股线运行脚本（独立锁，flock 防并发）
│   └── run_us.sh               # 美股线运行脚本（独立锁，flock 防并发）
├── docs/                       # 架构 / 数据源 / 策略文档
└── tests/                      # pytest + hypothesis
```

## 测试

```bash
uv run --extra dev pytest
# 或
pytest
```

## 常见问题

- **拉取数据超时？** tickflow 免费服务需出站 HTTPS，确认服务器可访问外网；国内机房通常正常。
- **飞书推送失败？** 检查 `.env` 中的 webhook URL，以及机器人是否被移出群、是否触发频控。
- **想要实时/分钟线？** 在 `.env` 配置 `TICKFLOW_API_KEY` 即可切换到完整服务。

## 免责声明

本项目与其输出仅用于量化研究与学习，不构成任何投资建议。ETF、A 股与美股投资均有风险，入市需谨慎。

## License

[MIT](LICENSE)
