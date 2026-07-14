# Matrix

> ETF 推荐系统：tickflow 数据同步 + SQLite 本地存储 + 多策略选 ETF + 飞书推送。

Matrix 面向阿里云 ECS / Alibaba Cloud Linux 部署。系统每天收盘后从
[tickflow](https://github.com/tickflow-org/tickflow) 同步沪深 ETF 日线，运行内置技术型策略，
并把候选 ETF 推送到飞书群。**数据源完全使用 tickflow，不依赖 baostock。**

## 功能概览

- 使用 tickflow 拉取 `CN_ETF` 标的池、ETF 日 K 与基础信息（免费服务，无需注册）。
- 使用本地 SQLite 保存数据，默认路径 `data/matrix_etf.db`。
- 支持全量回填、日常增量同步、标的池同步、指标刷新与四梯队报告。
- 内置七套技术策略：相对强度动量、均线趋势、放量突破、强势回踩，
  以及 Mega7 风格的风险调整动量、成交额确认动量、低波趋势轮动。
- 支持按策略路由到不同飞书机器人。
- 提供 Alibaba Cloud Linux 可用的运行脚本和 systemd 定时任务模板。

由于 ETF 没有 PE/PB/ROE 等基本面字段，Matrix 的策略体系完全基于价格、成交量与成交额。
详见 [docs/architecture.md](docs/architecture.md)、[docs/data_source.md](docs/data_source.md)、
[docs/etf_strategy.md](docs/etf_strategy.md)。

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
```

不使用 uv 时，可用标准 venv：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python main.py --backfill
```

## 命令行用法

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

## 配置项（.env）

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `FEISHU_WEBHOOK_URL` | 是 | — | 默认飞书 Webhook（fallback） |
| `DB_PATH` | 否 | `data/matrix_etf.db` | SQLite 路径 |
| `START_DATE` | 否 | `2020-01-01` | 回填起始日期 |
| `TICKFLOW_API_KEY` | 否 | 空 | 留空用免费服务；非空用完整服务 |
| `ETF_UNIVERSE` | 否 | `CN_ETF` | tickflow ETF 标的池 id |
| `LIQUIDITY_MIN_AMOUNT` | 否 | `50000000` | 流动性门槛：近 20 日平均成交额（元） |
| `RPS_PERIOD` | 否 | `120` | 动量/RPS 回看天数 |
| `RPS_THRESHOLD` | 否 | `90` | RPS 百分位阈值 |
| `BREAKOUT_PERIOD` | 否 | `60` | 突破回看天数 |
| `VOLUME_SURGE` | 否 | `1.5` | 放量倍数 |
| `MEGA7_MOMENTUM_PERIODS` | 否 | `21,63,126` | Mega7 风格多周期动量窗口（日） |
| `MEGA7_TOP_N` | 否 | `10` | Mega7 风格策略最多输出数量 |
| `MEGA7_DOWNSIDE_THRESHOLD` | 否 | `0.5` | 下行频率过滤阈值 |
| `SKIP_NON_TRADING_DAY` | 否 | `true` | 日常模式是否跳过周末/配置休市日 |
| `CN_MARKET_HOLIDAYS` | 否 | 空 | 逗号分隔的 A 股休市日，格式 `YYYY-MM-DD` |
| `FEISHU_RETRY_ATTEMPTS` | 否 | `3` | 飞书请求对网络/临时错误的最大尝试次数 |
| `STRATEGY_WEBHOOK_<KEY>` | 否 | — | 策略专属 webhook，KEY 见下表 |

策略与 webhook_key 对应关系：

| 策略 | webhook_key |
|------|-------------|
| RpsMomentumStrategy | `rps` |
| TrendMaStrategy | `trend` |
| BreakoutVolumeStrategy | `breakout` |
| MeanReversionStrategy | `pullback` |
| RiskAdjustedMomentumStrategy | `mega7_momentum` |
| VolumeConfirmedMomentumStrategy | `mega7_volume` |
| LowVolTrendRotationStrategy | `mega7_lowvol` |

## 部署到 Alibaba Cloud Linux

以下示例假设项目部署在 `/opt/Matrix`，使用 `root` 运行。若改用普通用户，需同步修改
systemd unit 中的路径与权限。

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

### 4. 配置环境变量

```bash
cd /opt/Matrix
cp .env.example .env
vi .env   # 至少填写 FEISHU_WEBHOOK_URL
```

### 5. 首次回填

```bash
cd /opt/Matrix
./scripts/run_matrix.sh --backfill
```

`run_matrix.sh` 会自动优先使用 `.venv/bin/python`，其次 `uv run`，最后系统 `python3`，
并通过 `flock` 防止定时任务并发重入。

### 6. 配置 systemd 定时任务

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-etf.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-etf.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-etf.timer
```

查看状态与日志：

```bash
systemctl list-timers matrix-etf.timer
systemctl status matrix-etf.service
journalctl -u matrix-etf.service -n 100 --no-pager
```

定时任务默认在**周一至周五 19:15**（收盘后）运行，`Persistent=true` 会在错过时补跑。
如需调整时间，编辑 `matrix-etf.timer` 的 `OnCalendar` 后 `systemctl daemon-reload`。

### 7. 手动触发一次

```bash
sudo systemctl start matrix-etf.service
```

## 目录结构

```
Matrix/
├── main.py                     # CLI 入口
├── matrix_etf/
│   ├── core/                   # 配置 + 日志
│   ├── data/engine.py          # tickflow 同步 + SQLite 存储
│   ├── strategy/               # 七套策略 + 四梯队报告
│   └── notify/feishu.py        # 飞书推送
├── deploy/systemd/             # systemd service + timer
├── scripts/run_matrix.sh       # 运行脚本（flock 防并发）
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

本项目与其输出仅用于量化研究与学习，不构成任何投资建议。ETF 投资有风险，入市需谨慎。

## License

[MIT](LICENSE)
