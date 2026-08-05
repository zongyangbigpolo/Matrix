# Matrix

> ETF + A Share + US Stock Recommendation System: tickflow data synchronization + SQLite local storage + multi-strategy stock selection + Feishu push.

## 👋 Want to try it out? Scan the QR code to join the Feishu group

The system automatically pushes the stock selection results of ETF, A shares, and US stocks to the Feishu group after the daily market close. **If you want to experience the push effect directly, use Feishu to scan the QR code below to join the experience group:**

<p align="center">
  <img src="docs/assets/feishu-group-qr.png" alt="Matrix Experience Group QR Code" width="320" />
</p>

<p align="center">
  <b>Matrix-Experience Group 003</b><br/>
  Use the Feishu App to scan and join, and you can receive daily ETF / A shares / US stock strategy recommendation cards in real time.<br/>
  <sub>The QR code is valid until 2027/7/15; if it has expired or cannot be joined, please raise an issue to contact.</sub>
</p>

---

Matrix is deployed on Alibaba Cloud ECS / Alibaba Cloud Linux. After the daily market close, the system synchronizes market data from
[tickflow](https://github.com/tickflow-org/tickflow), runs built-in technical strategies, and pushes candidate targets to the Feishu group. **The data source is entirely based on tickflow and does not rely on baostock.**

The system is divided into three completely independent pipelines according to financial product types:

- **ETF line** (`main.py`): `CN_ETF` target pool → `data/matrix_etf.db` → ETF strategy.
- **A share line** (`stock_main.py`): `CN_Equity_A` full A share target pool → `data/matrix_stock.db` → A share strategy.
- **US stock line** (`us_main.py`): `US_Equity` target pool (about 12,000 stocks) → `data/matrix_us.db` → US stock strategy.

The three lines maintain their own databases, target pools, and strategy sets respectively, and do not affect each other. They can be deployed and scheduled independently.

## Functional Overview

- Use tickflow to pull ETF (`CN_ETF`), A shares (`CN_Equity_A`), and US stocks (`US_Equity`) target pools, daily K, and basic information (free service, no registration required).
- Use local SQLite to save data: ETF `data/matrix_etf.db`, A shares `data/matrix_stock.db`, US stocks `data/matrix_us.db`, with three databases physically isolated.
- Support full backfill, daily incremental synchronization, target pool synchronization, and gap补拉 (ETF also has indicator refresh and four-tier report).
- Built-in seven **ETF** technical strategies: relative strength momentum, moving average trend, volume breakout, strong reversal pullback,
  and Mega7 style risk-adjusted momentum, volume confirmed momentum, low volatility trend rotation.
- Built-in six **A share** technical strategies: moving average volume, turtle breakout, high flag shape consolidation, limit up shakeout, uptrend limit down, RPS momentum breakout.
- Built-in four **US stock** technical strategies: US stock relative strength momentum, US stock moving average trend, US stock moving average volume, US stock volume breakout
  (US stock free version has no turnover volume, liquidity uses "US dollar turnover volume = close×volume" estimation; US stock has no limit up/down, so it does not include limit up/down strategies).
- Strategies are categorized by financial product type and managed in separate directories: `matrix_etf/strategy/etf/`, `matrix_etf/strategy/stock/`, and `matrix_etf/strategy/us/`, with no mutual references.
- Support routing to different Feishu robots based on strategy.
- Provide Alibaba Cloud Linux compatible running scripts and systemd timer templates.

Matrix's strategy system focuses on price, volume, and turnover.
**Strategy overview (all 17 sets, including English and Chinese names and Feishu routing) can be found in [docs/strategies.md](docs/strategies.md).**
More details can be found in [docs/architecture.md](docs/architecture.md), [docs/data_source.md](docs/data_source.md),
[docs/etf_strategy.md](docs/etf_strategy.md), [docs/stock_strategy.md](docs/stock_strategy.md), [docs/us_strategy.md](docs/us_strategy.md).

## Running Environment

Recommended production environment:

- Alibaba Cloud Linux 3 / 2, or other systemd Linux distributions
- Python 3.10+
- Git
- Outbound network can access tickflow service and Feishu Webhook

This project can also be developed and tested on macOS, but the deployment instructions are based on Alibaba Cloud Linux.

## Quick Start (Local)

```bash
# 1. Install dependencies (recommended uv)
uv sync --extra dev

# 2. Prepare configuration
cp .env.example .env
#   Edit .env, at least fill FEISHU_WEBHOOK_URL

# 3. First full backfill of ETF history (free service, about a few minutes)
uv run python main.py --backfill

# 4. Daily operation (incremental sync + run strategy + push)
uv run python main.py

# 5. (Optional) Stock line: first full backfill of A share history (about 5500 stocks, takes longer)
uv run python stock_main.py --backfill

# 6. (Optional) Daily operation of stock line
uv run python stock_main.py

# 7. (Optional) US stock line: first full backfill of US stock history (about 12,000 stocks, takes longer)
uv run python us_main.py --backfill

# 8. (Optional) Daily operation of US stock line
uv run python us_main.py
```

If not using uv, you can use standard venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python main.py --backfill
```

## Command Line Usage

### ETF Line (`main.py`)

| Command | Description |
|------|-------------|
| `python main.py` | Daily mode: incremental sync + refresh indicators + run strategy + push (auto backfill when no local data) |
| `python main.py --backfill` | Backfill mode: sync target pool + pull full history daily K of CN_ETF |
| `python main.py --sync-universe` | Only sync ETF target pool and basic information |
| `python main.py --refresh-metrics` | Only recalculate `etf_metrics` indicators |
| `python main.py --etf-report` | Generate four-tier ETF Markdown report (write to `reports/`) |
| `python main.py --symbols 510300.SH,159915.SZ` | Only process specified ETFs |
| `python main.py --report-limit 20` | Control the number of stocks displayed per tier in the report |
| `python main.py --force` | In daily mode, ignore weekend/market holiday protection and force run |

### Stock Line (`stock_main.py`, completely decoupled from ETF line)

| Command | Description |
|------|-------------|
| `python stock_main.py` | Daily mode: incremental sync + run stock strategy + push (auto backfill when no local data) |
| `python stock_main.py --backfill` | Backfill mode: sync target pool + pull full history daily K of CN_Equity_A |
| `python stock_main.py --sync-universe` | Only sync stock target pool and basic information (stock_basic) |
| `python stock_main.py --symbols 600519.SH,000001.SZ` | Only process specified stocks |
| `python stock_main.py --force` | In daily mode, ignore weekend/market holiday protection and force run |

### US Stock Line (`us_main.py`, completely isolated from ETF line / A share line)

| Command | Description |
|------|-------------|
| `python us_main.py` | Daily mode: incremental sync + run US stock strategy + push (auto backfill when no local data) |
| `python us_main.py --backfill` | Backfill mode: sync target pool + pull full history daily K of US_Equity |
| `python us_main.py --sync-universe` | Only sync US stock target pool and basic information (stock_basic, US stock independent database) |
| `python us_main.py --symbols AAPL.US,MSFT.US` | Only process specified US stocks |
| `python us_main.py --force` | In daily mode, ignore weekend/market holiday protection and force run |

### Performance Analysis Line (`analytics_main.py`, independent of three stock selection lines)

Record each stock selection result as a signal when it is pushed to the database, calculate the actual realization profit as the market progresses, compare with benchmarks to summarize each strategy's score card (annualized / excess / drawdown / win rate / Sharpe / Sortino / 0-100 comprehensive score), and attach the strategy's historical performance to subsequent push cards. Design and formula details can be found in [`docs/analytics.md`](docs/analytics.md).

| Command | Description |
|------|-------------|
| `python analytics_main.py --evaluate` | Forward evaluation: sync benchmark + realize profit + score card (daily run, default mode) |
| `python analytics_main.py --sync-benchmark` | Only update benchmark market data cache (SSE 300 / S&P 500) |
| `python analytics_main.py --report` | Print the latest score cards of each strategy |
| `python analytics_main.py --replay --days 20` | Historical replay: reconstruct past 20 trading days' stock selection signals without forward bias, immediately evaluate and print each strategy's realization profit |
| `python analytics_main.py --replay --market ETF` | Only replay ETF market (avoid A share's 5500 stocks, result in seconds) |
| `python analytics_main.py --replay --market CN --strategy rps` | Only replay A share's single strategy with class name containing `rps` |

> Signal recording in the database is automatically completed by the three stock selection lines (as a rollback hook before each push), no manual operation required;
> `analytics_main.py` is only responsible for subsequent profit calculation and scoring. vectorbt historical backtesting is offline enhanced
> and not run in the server timer. 
>
> **If you want to see historical profits immediately** (instead of waiting for daily accumulation), use `--replay`: it allows each strategy to "go back to a past trading day" using only data from that day and earlier to reselect stocks, thus making up for historical signals and evaluating them, **eliminating forward bias**. Note that the holding period is 5/10/20/60 trading days, and in short window replays only the 5-day holding can be closed for earlier dates, and comprehensive scores require a sample size of ≥10, so short windows often show "insufficient sample size" -- but each stock's tick-by-tick profit is still visible. A share's 5500 stocks daily replay is slower (minutes), but if you only want to see one strategy, using `--market`/`--strategy` to narrow the scope will be much faster: `--market` specifies the market (ETF/CN/US), `--strategy` filters by class name substring (case-insensitive, e.g., `rps`, `breakout`), both can be combined.

## Configuration Items (`.env`)

| Variable | Required | Default | Description |
|------|------|------|------|
| `FEISHU_WEBHOOK_URL` | Yes | — | Default Feishu Webhook (fallback) |
| `DB_PATH` | No | `data/matrix_etf.db` | SQLite path |
| `START_DATE` | No | `2020-01-01` | Backfill start date |
| `TICKFLOW_API_KEY` | No | Empty | Leave empty for free service; non-empty for full service |
| `ETF_UNIVERSE` | No | `CN_ETF` | tickflow ETF target pool id |
| `STOCK_DB_PATH` | No | `data/matrix_stock.db` | Stock line SQLite path (independent of ETF database) |
| `STOCK_UNIVERSE` | No | `CN_Equity_A` | tickflow A share target pool id |
| `US_DB_PATH` | No | `data/matrix_us.db` | US stock line SQLite path (independent of ETF / A share databases) |
| `US_UNIVERSE` | No | `US_Equity` | tickflow US stock target pool id (about 12,000 stocks) |
| `LIQUIDITY_MIN_AMOUNT` | No | `50000000` | ETF liquidity threshold: 20-day average turnover volume (yuan) |
| `RPS_PERIOD` | No | `120` | Momentum/RPS lookback days |
| `RPS_THRESHOLD` | No | `90` | RPS percentile threshold |
| `BREAKOUT_PERIOD` | No | `60` | Breakout lookback days |
| `VOLUME_SURGE` | No | `1.5` | Volume surge multiplier |
| `STOCK_LIQUIDITY_MIN_AMOUNT` | No | `100000000` | Stock liquidity threshold: daily turnover volume (yuan) |
| `STOCK_MA_VOLUME_SURGE` | No | `1.5` | Stock moving average volume strategy surge multiplier |
| `STOCK_RPS_PERIOD` | No | `120` | Stock RPS lookback days |
| `STOCK_RPS_THRESHOLD` | No | `90` | Stock RPS percentile threshold |
| `US_LIQUIDITY_MIN_DOLLAR_VOLUME` | No | `20000000` | US stock liquidity threshold: 20-day average "US dollar turnover volume = close×volume" (USD) |
| `US_MA_VOLUME_SURGE` | No | `1.5` | US stock moving average volume strategy surge multiplier |
| `US_RPS_PERIOD` | No | `120` | US stock RPS lookback days |
| `US_RPS_THRESHOLD` | No | `90` | US stock RPS percentile threshold |
| `US_BREAKOUT_PERIOD` | No | `60` | US stock breakout lookback days |
| `US_VOLUME_SURGE` | No | `1.5` | US stock volume surge multiplier |
| `MEGA7_MOMENTUM_PERIODS` | No | `21,63,126` | Mega7 style multi-period momentum windows (days) |
| `MEGA7_TOP_N` | No | `10` | Mega7 style strategy maximum output quantity |
| `MEGA7_DOWNSIDE_THRESHOLD` | No | `0.5` | Downside frequency filtering threshold |
| `SKIP_NON_TRADING_DAY` | No | `true` | In daily mode, skip weekend/market holiday protection |
| `CN_MARKET_HOLIDAYS` | No | Empty | Comma-separated A share market holidays, format `YYYY-MM-DD` |
| `US_MARKET_HOLIDAYS` | No | Empty | Comma-separated US stock market holidays, format `YYYY-MM-DD` |
| `FEISHU_RETRY_ATTEMPTS` | No | `3` | Maximum attempts for Feishu requests in case of network/temporary errors |
| `SYNC_RETRY_ATTEMPTS` | No | `6` | Maximum attempts for data sync in case of tickflow rate limit (60/min) |
| `SYNC_RETRY_BASE_DELAY` | No | `2` | Exponential backoff base seconds for sync retries |
| `SYNC_RETRY_MAX_DELAY` | No | `60` | Maximum single wait seconds for sync retries |
| `SYNC_PERSIST_MAX_SECONDS` | No | `10800` | Maximum duration for "continuous pull until completion" (default 3 hours) |
| `SYNC_PERSIST_ROUND_INTERVAL` | No | `300` | Interval seconds between pull rounds (default 5 minutes) |
| `SYNC_PERSIST_TARGET_COVERAGE` | No | `0.9` | When the latest trading day coverage reaches this ratio, it is considered that the pull is completed |
| `SYNC_PERSIST_MIN_COVERAGE` | No | `0.5` | Minimum acceptable coverage after convergence/timeout |
| `STRATEGY_WEBHOOK_<KEY>` | No | — | Strategy-specific webhook, KEY see below table |

ETF strategy and webhook_key correspondence:

| Strategy | webhook_key |
|------|-------------|
| RpsMomentumStrategy | `rps` |
| TrendMaStrategy | `trend` |
| BreakoutVolumeStrategy | `breakout` |
| MeanReversionStrategy | `pullback` |
| RiskAdjustedMomentumStrategy | `mega7_momentum` |
| VolumeConfirmedMomentumStrategy | `mega7_volume` |
| LowVolTrendRotationStrategy | `mega7_lowvol` |

Stock strategy and webhook_key correspondence (all with `stock_` prefix, decoupled from ETF push):

| Strategy | webhook_key |
|------|-------------|
| MaVolumeStrategy | `stock_ma_volume` |
| TurtleTradeStrategy | `stock_turtle` |
| HighTightFlagStrategy | `stock_flag` |
| LimitUpShakeoutStrategy | `stock_shakeout` |
| UptrendLimitDownStrategy | `stock_limit_down` |
| RpsBreakoutStrategy | `stock_rps` |

US stock strategy and webhook_key correspondence (all with `us_` prefix, decoupled from ETF / A share push):

| Strategy | webhook_key |
|------|-------------|
| UsRpsMomentumStrategy | `us_rps` |
| UsTrendMaStrategy | `us_trend` |
| UsMaVolumeStrategy | `us_ma_volume` |
| UsBreakoutVolumeStrategy | `us_breakout` |

## Deploy on Alibaba Cloud Linux

The following example assumes the project is deployed in `/opt/Matrix` and run by `root`. If using a regular user, need to modify the paths and permissions in the systemd unit accordingly.

### One-step deployment: enable automatic daily screening and Feishu push for ETF + A shares + US stocks

If you want the server to automatically run strategies and push results to Feishu every trading day after the market close, the complete process is the following 6 steps, with detailed explanations in the following sections:

```bash
# 1. Install dependencies (see §1, §2)
sudo dnf install -y git curl ca-certificates gcc gcc-c++ make sqlite
curl -LsSf https://astral.sh/uv/install.sh | sh && source "$HOME/.local/bin/env"

# 2. Pull code + install Python dependencies (see §3)
sudo git clone https://github.com/zongyangbigpolo/Matrix.git /opt/Matrix
cd /opt/Matrix && uv sync

# 3. Configure Feishu webhook (see §4, this step determines where to push)
cp .env.example .env
vi .env                       # at least fill FEISHU_WEBHOOK_URL

# 4. First full backfill of historical data (see §5)
./scripts/run_matrix.sh --backfill    # ETF line
./scripts/run_stock.sh  --backfill    # A share line
./scripts/run_us.sh     --backfill    # US stock line

# 5. Install and enable timer tasks for three lines (see §6)
sudo cp deploy/systemd/matrix-etf.service   deploy/systemd/matrix-etf.timer   /etc/systemd/system/
sudo cp deploy/systemd/matrix-stock.service deploy/systemd/matrix-stock.timer /etc/systemd/system/
sudo cp deploy/systemd/matrix-us.service    deploy/systemd/matrix-us.timer    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-etf.timer matrix-stock.timer matrix-us.timer

# 5b. (Optional) Enable performance analysis line: automatically record signals from three stock selection lines, 21:30 calculate realization profit + score card
sudo cp deploy/systemd/matrix-analytics.service deploy/systemd/matrix-analytics.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-analytics.timer

# 6. Immediately run manually to confirm Feishu can receive (see §7)
sudo systemctl start matrix-etf.service
sudo systemctl start matrix-stock.service
sudo systemctl start matrix-us.service
```

After successful deployment, there is no need to manage further: ETF line runs every Monday to Friday at 19:15, A share line at 20:30 (evening time slots avoid overlap), and US stock line is set to run at 14:00 (China time zone, at this time the previous US stock trading day has fully closed, and it is completely separated from evening A share/ETF, avoiding mutual competition for tickflow free version's 60/min limit). Performance analysis line runs at 21:30 (later than three stock selection lines, ensuring that the signals from the day have all been recorded and the market data of each database has been synchronized to the latest trading day before evaluation). The three lines run automatically and do not interfere with each other, and will be补跑 (make up for missed runs) if the machine is turned off, thanks to `Persistent=true`. After the market close, each line will continuously补拉 (pull up to date) the day's data until all is pulled or coverage meets the target before sending strategy cards; if it still cannot pull up to date after about 3 hours, it will send a "data anomaly" alert card and skip the strategy push this time (see [Data Source and Rate Limit Details](docs/data_source.md)). **If you only want to enable one of the lines**, you can skip the backfills and `enable` of the other lines (the three lines are completely independent).

### 1. Install system dependencies

Alibaba Cloud Linux 3:

```bash
sudo dnf update -y
sudo dnf install -y git curl ca-certificates gcc gcc-c++ make sqlite
```

Alibaba Cloud Linux 2:

```bash
sudo yum update -y
sudo yum install -y git curl ca-certificates gcc gcc-c++ make sqlite
```

### 2. Install uv (recommended)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

### 3. Pull code and install dependencies

```bash
sudo git clone https://github.com/zongyangbigpolo/Matrix.git /opt/Matrix
cd /opt/Matrix
uv sync
```

If not using uv:

```bash
cd /opt/Matrix
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4. Configure environment variables (Feishu push is determined in this step)

```bash
cd /opt/Matrix
cp .env.example .env
vi .env
```

**Required**: `FEISHU_WEBHOOK_URL` - all strategies default to this address. How to get it:

1. In the target Feishu group, click "Settings → Group Robot → Add Robot → Custom Robot Webhook".
2. Copy the generated Webhook address (format like `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`).
3. Fill it in `.env` as `FEISHU_WEBHOOK_URL=`.
4. If the robot is configured with "Signature Verification", please use Feishu group's built-in keyword/IP whitelist method for release, this project pushes in the mode of custom robot without signature.

**Optional: Route strategies to different groups**. If you want a certain strategy to be pushed to another group separately, fill in the corresponding `STRATEGY_WEBHOOK_<KEY>` with that group's robot Webhook; strategies not configured will automatically fall back to `FEISHU_WEBHOOK_URL`. `<KEY>` values refer to the three webhook_key correspondence tables above (ETF like `rps`, `trend`; A share like `stock_rps`, `stock_turtle`; US stock like `us_rps`, `us_trend`). For example:

```dotenv
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/default-xxxx
STRATEGY_WEBHOOK_RPS=https://open.feishu.cn/open-apis/bot/v2/hook/etf-rps-xxxx
STRATEGY_WEBHOOK_STOCK_RPS=https://open.feishu.cn/open-apis/bot/v2/hook/stock-rps-xxxx
STRATEGY_WEBHOOK_US_RPS=https://open.feishu.cn/open-apis/bot/v2/hook/us-rps-xxxx
```

Other configurations (database paths, target pools, strategy thresholds, etc.) have defaults, can be left unchanged, refer to the [Configuration Items](#配置项env) table above. Use tickflow full service by filling in `TICKFLOW_API_KEY`, otherwise leave empty for free service.

### 5. First full backfill

ETF line:

```bash
cd /opt/Matrix
./scripts/run_matrix.sh --backfill
```

A share line (about 5500 stocks full A share, first full backfill takes longer):

```bash
cd /opt/Matrix
./scripts/run_stock.sh --backfill
```

US stock line (about 12,000 US stocks, first full backfill takes longer):

```bash
cd /opt/Matrix
./scripts/run_us.sh --backfill
```

`run_matrix.sh` / `run_stock.sh` / `run_us.sh` will automatically use `.venv/bin/python` first, then `uv run`, and finally system `python3`, and each line uses an independent `flock` lock file to prevent concurrent re-entry by timer tasks (the three lines do not interfere with each other).

### 6. Configure systemd timer tasks

ETF line:

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-etf.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-etf.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-etf.timer
```

Stock line (optional, independent of ETF line):

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-stock.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-stock.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-stock.timer
```

US stock line (optional, independent of ETF / A share line):

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-us.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-us.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-us.timer
```

Performance analysis line (optional, automatically record signals from three stock selection lines, 21:30 calculate realization profit + score card, independent):

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-analytics.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-analytics.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-analytics.timer
```

Check status and logs:

```bash
systemctl list-timers 'matrix-*.timer'
systemctl status matrix-etf.service matrix-stock.service matrix-us.service
journalctl -u matrix-etf.service -n 100 --no-pager
journalctl -u matrix-stock.service -n 100 --no-pager
journalctl -u matrix-us.service -n 100 --no-pager
journalctl -u matrix-analytics.service -n 100 --no-pager
```

ETF line runs by default at 19:15 Monday to Friday, A share line at 20:30 (evening time slots avoid overlap), US stock line at 14:00 (China time zone, at this time the previous US stock trading day has fully closed, and it is completely separated from evening A share/ETF, avoiding mutual competition for tickflow free version's limit). Performance analysis line runs at 21:30 (later than three stock selection lines, ensuring that the signals from the day have all been recorded and the market data of each database has been synchronized to the latest trading day before evaluation). Due to the fact that after the market close, each line will "continuously pull up to date" the day's data until all is pulled or coverage meets the target before sending strategy cards, the `TimeoutStartSec` of the systemd service has been correspondingly extended (ETF 4h, A share 5h, US stock 6h, analysis line 2h). If you need to adjust the time, edit the `OnCalendar` of the corresponding `.timer` and then `systemctl daemon-reload`. 

### 7. Validation: manually run once and confirm Feishu receives push

After installing the timer tasks, there is no need to wait for the market close, you can immediately trigger a manual run for end-to-end validation:

```bash
sudo systemctl start matrix-etf.service     # ETF line
sudo systemctl start matrix-stock.service   # A share line
sudo systemctl start matrix-us.service      # US stock line
```

Then check the execution results and push situation:

```bash
# Check this run's logs, should see successful records like "已推送 N 只标的"
journalctl -u matrix-etf.service -n 100 --no-pager
journalctl -u matrix-stock.service -n 100 --no-pager
journalctl -u matrix-us.service -n 100 --no-pager

# Confirm next automatic run time has been scheduled
systemctl list-timers 'matrix-*.timer'
```

Finally, check the corresponding Feishu group to confirm receiving strategy push cards. If not received, check in this order:

- If there are `FEISHU_WEBHOOK_URL` related errors in logs → `.env` not filled or URL incorrect;
- If logs show each strategy "selects 0 stocks" → this is normal (no stocks meet the conditions on that day, no push);
- If there are tickflow / network errors in logs → check if ECS outbound can access tickflow and Feishu domains;
- If you want to temporarily ignore weekend/market holiday protection and force run, you can use `./scripts/run_matrix.sh --force` (stock line use `run_stock.sh --force`, US stock line use `run_us.sh --force`).

## Directory Structure

```
Matrix/
├── main.py                     # ETF line CLI entry
├── stock_main.py               # A share line CLI entry (completely decoupled from ETF line)
├── us_main.py                  # US stock line CLI entry (completely isolated from ETF / A share line)
├── matrix_etf/
│   ├── core/                   # Configuration + logging + trading calendar
│   ├── data/
│   │   ├── engine.py           # ETF: tickflow sync + SQLite storage
│   │   ├── stock_engine.py     # A share: tickflow sync + SQLite storage
│   │   ├── us_stock_engine.py  # US stock: tickflow sync + independent SQLite storage
│   │   └── tickflow_client.py  # tickflow client factory (shared by three engines)
│   ├── strategy/
│   │   ├── base.py             # Shared strategy base class
│   │   ├── names.py            # English strategy name → Chinese name mapping
│   │   ├── etf/                # ETF strategies (by product type) + four-tier report
│   │   ├── stock/              # A share strategies (by product type)
│   │   └── us/                 # US stock strategies (by product type, US dollar turnover volume)
│   └── notify/feishu.py        # Feishu push (ETF / A share / US stock common)
├── deploy/systemd/             # systemd service + timer (ETF / A share / US stock three lines)
├── scripts/
│   ├── run_matrix.sh           # ETF line running script (flock anti-concurrent)
│   ├── run_stock.sh            # A share line running script (independent lock, flock anti-concurrent)
│   └── run_us.sh               # US stock line running script (independent lock, flock anti-concurrent)
├── docs/                       # Architecture / data source / strategy documentation
└── tests/                      # pytest + hypothesis
```

## Testing

```bash
uv run --extra dev pytest
# or
pytest
```

## Common Issues

- **Data pull timeout?** tickflow free service requires outbound HTTPS, confirm server can access internet; domestic machine rooms usually work fine.
- **Feishu push fails?** Check if `.env` has `FEISHU_WEBHOOK_URL` filled, and if the robot has been removed from the group or triggered frequency control.
- **Want real-time/minute lines?** Configure `TICKFLOW_API_KEY` in `.env` to switch to full service.

## Disclaimer

This project and its output are only for quantitative research and learning, not constituting any investment advice. ETF, A share, and US stock investments all carry risks, please invest cautiously.

## License

[MIT](LICENSE)
