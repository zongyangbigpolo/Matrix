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

### 绩效分析线（`analytics_main.py`，独立于三条选股线）

把每次选股结果落库为信号，随行情推进计算真实兑现收益，对比基准汇总为每个策略的
评分卡（年化 / 超额 / 回撤 / 胜率 / 夏普 / Sortino / 0–100 综合评分），并在后续推送
卡片时附上该策略历史战绩。设计与公式详见 [`docs/analytics.md`](docs/analytics.md)。

每日推送先汇总同一市场的全部策略候选，再择优保留 **最多 10 个不同标的**。
ETF、A 股、美股独立限额；同一标的命中多个策略时仍保留各策略归属，但不重复占名额。
`RECOMMENDATION_LIMIT` 可设为 1～10。最终推送与信号台账使用相同名单。
评分对候选池各指标做百分位标准化，再按 **40% 近 20 日收益/波动率 + 20% 近 20 日
最大回撤（越小越好）+ 20% 平均成交额 + 20% 策略共识数** 加权；同分按代码排序。
美股成交额使用收盘价×成交量估算；不足 21 根有效日线、无流动性或数据异常者不推荐。
该评分是可解释的筛选规则，不代表收益保证，也不根据未来回测成绩择股。

| 命令 | 说明 |
|------|------|
| `python analytics_main.py --evaluate` | 前向评估：同步基准 + 兑现收益 + 评分卡（每日运行，默认模式） |
| `python analytics_main.py --sync-benchmark` | 仅更新基准行情缓存（沪深300 / 标普500） |
| `python analytics_main.py --report` | 打印各策略最新评分卡 |
| `python analytics_main.py --backtest --days 60` | 离线回测全部 17 种策略，保存组合总收益与净值（不需要联网） |
| `python analytics_main.py --replay --days 20` | `--backtest` 的兼容别名：模拟最近 20 个交易日的组合，不再写入日常信号台账 |
| `python analytics_main.py --backtest --market ETF` | 只回测 ETF 市场 |
| `python analytics_main.py --backtest --market CN --strategy rps` | 回测 A 股，报告类名含 `rps` 的策略 |

> 信号落库由三条选股线自动完成（每次推送前的容错 hook），无需手动操作；
> `--backtest` 独立记录模拟组合净值，不混入日常信号台账；`--report` 可查看已保存的回测。
> 它计算有限本金下的组合总收益；旧版 `--replay` 的逐笔平均收益不是组合总收益。
> 完整交易口径与数据局限见 [`docs/analytics.md`](docs/analytics.md)。
>
> **无需等日常信号积累**：`--backtest` 让策略只读取历史当日及之前的数据，按次日开盘价
> 模拟买入、建议持有期模拟卖出，计入手续费、滑点和资金占用，展示总收益、年化与最大回撤。
> 无信号的策略也列入报告，数据不足会明确标注，不用逐笔收益连乘冒充组合总收益。
> `--market` 指定市场，`--strategy` 按类名子串筛选报告；为保持与每日推荐一致，
> 同一市场仍运行全部策略再联合择优，不因只查看一个策略而改变候选池。
> 大市场长窗口可能耗时数小时，建议用后台服务运行。历史模拟不是实际账户收益，
> 未复权、停牌、幸存者偏差等数据限制仍需注意，短窗口结果不代表长期表现。

### 境内场内美股 ETF 收盘清单（`us_etf_main.py`）

这是一条面向普通境内证券账户的**沪深上市美股ETF交易参考清单**，不是美国账户清单、
不是一级申购清单，也不是新策略；不写推荐历史/绩效信号，也不占用通用
ETF 策略最多10只的名额。每次从 TickFlow 当前 `CN_ETF` 元数据发现名称明确的境内
交易所美股权益 ETF，复用 `DB_PATH` 下的 `etf_basic` / `etf_daily`，不建新库或迁移。
免费服务无需 API Key；这里只使用历史日线，**不是实时行情，也不是场外基金申购清单**。

按名称证据标注**纳斯达克100、标普500、其他美股指数/行业**（每类显示数量）。
实际“纳指ETF/纳斯达克ETF”宽基简称归入纳斯达克100；“纳指科技/生物科技”等行业
限定名称归入其他类，不能因为含“纳指”就标成100。美国50、道琼斯工业也归入其他类。
标普油气、标普消费、标普生物科技等海外股票ETF归入行业类，不混入宽基类。
标普油气股票ETF不是原油期货：这些简称只按已核对的代码与当前名称同时匹配纳入，
不把所有“标普”或“油气”产品都认作美股。
例如富国标普油气对应 S&P Oil & Gas Exploration & Production Select Industry，
景顺标普消费对应标普500消费精选指数；它们均为海外股票指数产品。
排除联接、LOF、外币份额、美国上市 ETF、全球/亚洲/香港基金、商品、债券及模糊名称；
不是所有 QDII 或所有“标普”基金，更不声称覆盖全部美股基金。
发现范围严格限于当前 `CN_ETF` 池；该池可能少于交易所ETF目录，不代表覆盖所有已知产品。
按每只最新日线的**人民币成交额降序**，未知置后，同额按代码排序；
不超过50只全部展示，超过则展示前50只，与 `RECOMMENDATION_LIMIT` 无关。

客户卡片与场外申购卡分开发送，标题明确标注“沪深市场”。每行突出基金名称、
可点击的六位代码（打开同花顺对应基金介绍）、上交所/深交所、分类、不复权人民币收盘价及涨跌幅。
买入单位为100份/手，并按该历史收盘价显示1手参考金额，费用另计，不冒充实时委托价。
共同行情日期只在顶部显示，滞后行情及非单日涨跌幅逐只注明日期。
不在客户卡片展示最大条数、候选数量、内部排序及分页规则；截断时仅说明为部分产品。
缺失报价的新基金仍显示“暂无行情”；
共享策略库仍使用前复权日线；客户交易参考单独查询不复权日线，不能用前复权价格估算实际买入金额。
不复权价格及其涨跌幅仅用于本次卡片，不写入共享策略库。两种序列均显式截止到最近已收盘日。
上次有效报价非上一境内交易日时，明确标为“非单日涨跌幅”。
滞后报价逐只标注，不用查询时间冒充行情日期。不展示未经核实的净值、溢价或实时剩余额度。
日线窗口为最近30条、不拉全市场历史或计算策略指标；最多5000条池元数据、
200个明确候选，超过安全边界报错而非静默截断。元数据分批250只、日线分批20只；
单请求超时20秒、不自动重试，CLI整体12分钟、服务15分钟上限。
外部空池/元数据不全/分类无结果/请求失败均报错且返回非零，不使用历史缓存掩盖失败。
正常返回但滞后的日线可展示，标明应有日期与实际日期。仅从**本次响应日期**
做有界 SQLite 读取，空响应不会复活本地旧报价。飞书按实际 JSON 字节自动分页，
每条不超过20 KiB，不因消息过长丢弃选中产品。

**普通股民在东方财富等券商输入代码买入，是二级市场交易，不是向基金公司申购整篮子ETF。**
因此日常卡片不再查询或显示一级申购开放/暂停、百万份最小申购单位或PCF申购限额；
不能用这些字段判断普通证券账户是否可以买入。`subscription.py` 的官方PCF解析能力保留为内部工具，
不参与普通客户推送。场外基金仍按天天基金渠道显示真实当日申购上限。
实际可买数量取决于账户可用资金、实时成交价格、盘口及券商规则；持仓市值不能当成可用现金。
当前日线不能核实实时停牌、溢价及账户权限，卡片明确要求下单前核对，不伪造“可买/不限额”状态。

**港股通与香港证券账户分开核验**：同一任务另发“港股市场 · 账户渠道核验”卡，
可选路由 `STRATEGY_WEBHOOK_HK_ETF`，默认仍使用原机器人。不额外创建定时任务。
客户为普通内地券商证券账户，不默认拥有香港证券账户，也不因持仓约100万元就认定已经开通港股通。
从 HKEX 官方证券目录筛选名称明确的普通做多美股指数ETF，再分别核对上交所、深交所
港股通买入名单。香港上市、港币交易或每手份额均不是港股通买入资格的证明。
只把核验通过且资料日期适用的产品列为港股通标的；未确认或仅适用于香港证券账户的产品，
不混进普通客户的可买清单。没有核实到符合条件的产品时如实发送范围明确的结果，
不把某个接口失败当成“全市场不存在”。目录提前公布下一交易日资料时明确标注日期。

HKEX 目录使用官方 `ListOfSecurities.xlsx`；沪港通使用上交所
`COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L`；深港通使用深交所 `SGT_GGTBDQD` 的JSON元数据及完整XLSX。
核对表头、代码、日期、记录数量和分页完整性，使用标准库解析文档，不新增依赖。
香港ETF的最新价格、停牌、溢价未核实时不填零、不沿用内地行情，也不沿用100份/手规则；
每手份额和币种以香港目录为准。资格核验不完整时卡片明确说明，任务返回非零，但不隐藏已成功发送的沪深卡。

| 命令 | 用途 |
|---|---|
| `python us_etf_main.py --dry-run` | 在线查询、更新共享行情库、核对港股通并打印两类卡片；不发送飞书 |
| `python us_etf_main.py --dry-run --force` | 周末/节假日也手工验证，仍显示实际行情日 |
| `bash scripts/run_us_etf.sh --force` | 持共享ETF锁查询并推送；Linux要求 `flock` |

默认按北京时间跳过周末及 `CN_MARKET_HOLIDAYS`，与现有交易日工具一致，
**不是完整交易所节假日日历**；可用 `--force` 手工覆盖。
可选路由 `STRATEGY_WEBHOOK_US_ETF`，不配置则使用默认飞书群，与场外
`STRATEGY_WEBHOOK_FUND_US` 分开。计划时间为周一至周五 **09:35 Asia/Shanghai**，
用于当日场内交易参考；场外基金09:30、通用ETF策略19:15不变。`Persistent=false` 不补发漏跑清单。
runner遵循 `MATRIX_ETF_HOME` / `MATRIX_ETF_LOCK_FILE`，与通用ETF和更新器
共用 `.matrix_etf.lock`，锁忙跳过，不新增第六个业务锁。直接调用Python不持锁；
服务器上请总是使用runner。

**首次安装必须人工维护发布**：自动更新器会拒绝本次脚本/unit/`.env.example`/更新器
变化。先暂停 `matrix-update.timer`，等待更新服务及所有业务服务空闲，在已有五个
业务锁保护下按维护流程发布已审核源码，保留 `.env`、数据库、虚拟环境和现有timer。
本功能无新增依赖；不可用强制reset覆盖线上内容。发布后在 `/opt/Matrix` 执行：

```bash
command -v flock
chmod +x scripts/run_us_etf.sh
# 重新安装更新器私有副本，使新服务被纳入空闲状态检查
sudo install -o root -g root -m 700 scripts/update_matrix.py /usr/local/libexec/matrix-update.py
sudo install -m 644 deploy/systemd/matrix-us-etf.service deploy/systemd/matrix-us-etf.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/matrix-us-etf.service /etc/systemd/system/matrix-us-etf.timer
sudo systemctl daemon-reload
# 释放维护锁后，先验证数据和卡片；此命令不发送飞书
bash scripts/run_us_etf.sh --dry-run --force
# 确认输出和日期正确后，手动推送一次（即使当天为周日）
bash scripts/run_us_etf.sh --force
sudo systemctl enable --now matrix-us-etf.timer
systemctl list-timers matrix-us-etf.timer --no-pager
```

最后仅在维护前已启用更新器时恢复它；不要顺带重装或修改原有timer。
服务资源限额为 `MemoryMax=256M` / `MemorySwapMax=64M`，低CPU/IO优先级。

### 境内美股基金申购监控（`fund_main.py`）

独立查询**天天基金公开渠道**的人民币场外申购状态与日累计限额，免费、无需 API Key，
不登录交易账户、不自动下单。不代表支付宝、银行、基金公司直销的限额，也不保证实际成交。
当前目录覆盖纳斯达克100、标普500及标普500等权 **24 个产品、57 类份额**；
不声称覆盖所有美股主动/行业主题基金，也不混入美元份额或场内 ETF 买卖。

本地仅维护 `config/us_funds.json`：同一产品的 A/C/D 等份额代码为一组，不保存名称、
净值、费率、额度、昨日快照或新的基金数据库。每次在线查询，**不超过50个可买产品就全部
发送，超过50个则展示额度最高的50个**，同产品符合条件的份额合并展示。
基金清单不受 `RECOMMENDATION_LIMIT` 影响；股票、ETF 等策略推荐仍保持原来的最多10个。
按各组最大单类份额公布上限降序、代码打破同分，不根据单一申购费率判断哪类份额最优。
卡片顶部依次展示**标普合计、纳斯达克合计、美股总计**三行，每行同时给出单日额度和基金数量。
标普合计包含标普500等权基金；所有汇总覆盖目录内全部已确认可买产品，
即使超过50只，也不只统计展示出来的产品。**合计采用每只基金只选一个份额的方案，取该基金最高的
已确认单类份额上限**：例如A类和C类各限100元，这只基金计100元；A类100、C类200则计200元。
这是一种明确的统计方式，不是断言所有基金的A/C必定共用额度。
标题明确标注“场外申购”，与场内ETF卡片分开。明细优先显示当日申购上限、起购金额，
点击名称或A/C等份额代码打开同花顺对应基金介绍；不同份额限额不同时分别显示。
额度数据仍来自天天基金，不因详情链接切换而冒充同花顺渠道额度。
显示的是渠道公布上限，不是扣除用户当日已购金额后的个人剩余额度。
客户卡片不显示最大条数、内部排序及分页规则；实际截断时说明顶部合计包含全部可申购产品。
超过飞书单条20 KiB限制时自动分条发送，保留全部选中产品及连续编号，不因消息长度丢弃基金。
单条可容纳时仍只发一条；多条推送中途失败会明确报错，不把部分发送视为成功。
推送中不放通用风险长文，仅保留更新时间、渠道、统计方式和未确认数据数量等必要信息。

仅纳入“开放申购/限大额”且起购金额、日累计上限均明确、满足人民币金额精度的产品。
**暂停申购优先于残留限额数字**；缺失、零值、超大未解释占位值、未知状态和字段异常
均列为待核实，不当成“不限额”。完整批次缺失、接口改版、网络失败时告警且退出非零，
不回退到昨日额度。部分目录代码缺失会在卡片明确标注；全部缺失视为查询失败。
公开信息可能滞后，卡片查询时间不是公告生效时间；单笔限制、定投例外、跨份额合并规则
及个人剩余额度并不能由此接口完整获得，最终以交易页面与公告为准。

| 命令 | 说明 |
|------|------|
| `python fund_main.py` | 查询并向飞书发送当前申购清单；无可确认产品也明确发送结果 |
| `python fund_main.py --dry-run` | 在线查询、打印卡片，不推送，不写入动态基金信息 |
| `python fund_main.py --discover --dry-run` | 对照免费基金名称目录，打印未纳入的候选 |
| `python fund_main.py --discover` | 仅有新增候选时发待审核卡片；不自动修改批准目录 |
| `python fund_main.py --catalog config/us_funds.json` | 指定代码目录 |

数据入口是网站使用的免费公开入口，**不是有 SLA 的官方授权开放 API**：
申购状态使用 `https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx`，
参数 `t=8&page=1,50000&js=reData&sort=fcode,asc`；新增候选使用
`https://fund.eastmoney.com/js/fundcode_search.js`。仅解析数据，不执行远端 JavaScript；
限制响应大小、超时和重试，不绕过登录、验证码或访问控制。不依赖 AKShare 或付费服务。

默认复用已有飞书机器人，也可配置 `STRATEGY_WEBHOOK_FUND_US` 指向专属群。
`FUND_CATALOG_PATH`、`FUND_SOURCE_TIMEOUT_SECONDS`、`FUND_SOURCE_ATTEMPTS` 见 `.env.example`。
部署到已有 `/opt/Matrix` 后：

```bash
sudo install -m 644 deploy/systemd/matrix-funds.service deploy/systemd/matrix-funds.timer /etc/systemd/system/
sudo install -m 644 deploy/systemd/matrix-fund-catalog.service deploy/systemd/matrix-fund-catalog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-funds.timer matrix-fund-catalog.timer
```

每天北京时间 **09:30** 查询并推送，周日 **10:00** 检查新增候选；周末/配置休市日仍查询，
卡片提醒委托与份额确认可能延后，不能仅根据中国工作日推断 QDII 是否开放。
新候选只提示核验，不直接加入可申购清单；核实投资范围、币种和份额归组后更新代码目录。
由于不保留历史额度，**首版不做14点“仅变化补发”或额度涨跌比较**。
定时器不补跑错过的时点，避免重启后重复推送；两项任务共用独立运行锁，
各自5分钟超时、192 MB内存和64 MB交换空间上限，不占用策略回测的锁。

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
| `RECOMMENDATION_LIMIT` | 否 | `10` | 每个市场每日跨策略不同推荐标的上限（1～10） |
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

# 5b.（可选）启用绩效分析线：随三条选股线自动落库信号，21:30 计算兑现收益 + 评分卡
sudo cp deploy/systemd/matrix-analytics.service deploy/systemd/matrix-analytics.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-analytics.timer

# 6. 立刻手动跑一次，确认飞书能收到（见 §7）
sudo systemctl start matrix-etf.service
sudo systemctl start matrix-stock.service
sudo systemctl start matrix-us.service
```

跑通后就不用再管了：ETF 线每周一至周五 **19:15**、A 股线 **20:30**（晚间错开），
美股线放到**白天 14:00**（中国时区，此时上一美股交易日已完整收盘，且与晚间 A 股/ETF
彻底错开，避免共享 tickflow 免费档 60/min 限额时相互抢占）。绩效分析线放到 **21:30**
（晚于三条选股线，确保当日信号已全部落库、各行情库已同步到最新交易日再评估）。
三线自动执行、互不阻塞，
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

绩效分析线（可选，随三条选股线自动落库信号，独立评估兑现收益 + 评分卡）：

```bash
sudo cp /opt/Matrix/deploy/systemd/matrix-analytics.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-analytics.timer /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-backtest.service /etc/systemd/system/
sudo cp /opt/Matrix/deploy/systemd/matrix-backtest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-analytics.timer
sudo systemctl enable --now matrix-backtest.timer
```

查看状态与日志：

```bash
systemctl list-timers 'matrix-*.timer'
systemctl status matrix-etf.service matrix-stock.service matrix-us.service
journalctl -u matrix-etf.service -n 100 --no-pager
journalctl -u matrix-stock.service -n 100 --no-pager
journalctl -u matrix-us.service -n 100 --no-pager
journalctl -u matrix-analytics.service -n 100 --no-pager
journalctl -u matrix-backtest.service -n 100 --no-pager
```

ETF 线默认在**周一至周五 19:15**、A 股线在 **20:30**（晚间错开），美股线放到**白天 14:00**
（中国时区，与晚间 A 股/ETF 彻底错开，避免共享 tickflow 免费档限速额度；此时上一美股交易日
已完整收盘）运行，`Persistent=true` 会在错过时补跑。绩效分析线在 **21:30** 运行（晚于三条
选股线，确保当日信号已全部落库、各行情库已同步到最新交易日再评估兑现收益与评分卡）。
组合回测每周日 **04:00** 离线重算最近 60 个交易日，低优先级运行，最长 12 小时；
回测服务设置 400 MB 内存上限，股票 RPS 按标的流式计算，避免全市场 DataFrame 占满小内存服务器。
与绩效评估共用分析锁，避免重复执行。首次安装可手动运行
`./scripts/run_analytics.sh --backtest --days 20` 先生成短窗口报告，再按需扩大窗口。
回测不会发送选股通知，也不会改写行情库或日常信号台账。
由于收盘后各线会「持续拉取直至完成」（默认最长坚持约 3 小时，见 `SYNC_PERSIST_*` 配置），
systemd service 的 `TimeoutStartSec` 已相应放宽（ETF 4h、A 股 5h、美股 6h、分析线 2h）。
如需调整时间，编辑对应 `.timer` 的 `OnCalendar` 后 `systemctl daemon-reload`。

### 6b. 可选：服务器每 30 分钟自行更新 main

`matrix-update.timer` **默认不启用**。启用后检查 GitHub 上的 `main`，网络恢复后下一次
检查即可自动快进；不依赖 SSH、开发电脑或 Copilot 会话。检查从启动后 5 分钟开始，
以后每次检查结束后约 30 分钟再次检查（1 分钟计时精度）。在已运行的机器上首次启用
可能立即检查。网络失败本次明确报错退出，不无限重试，也不把失败记成更新成功。

这是保守的**源码更新器，不是完整发布管理器**：

- 自动允许应用/测试 Python 源码、现有入口文件、README、LICENSE 和文档/文档图片更新。
  下载后在私有目录暂存完整目标树，核对 Git 对象内容，并使用现有 `.venv/bin/python`
  编译检查全部 Python 文件；**不导入应用、不运行策略/测试、不读取环境配置执行代码，
  不启动任何业务服务或发送飞书**。语法检查不是功能测试，合入 main 前仍需运行测试。
- **依赖、`pyproject.toml`、`uv.lock`、Python 版本、配置（包括基金目录）、
  `.env.example`、运行脚本、更新器自身、systemd unit 或其他未批准路径一旦改变，
  整次更新在改动线上文件前停止**，日志逐项列出需人工发布的文件。
  不会跳过依赖安装后假装发布成功。管理员须在维护窗口一起完成对应依赖/配置/unit
  安装和验证，再恢复自动更新；更新器私有副本及其 unit 也须重新安装。
- 只接受精确 origin `https://github.com/zongyangbigpolo/Matrix.git`、完整历史的 `main`、
  干净的已跟踪文件、无本地超前/分叉提交。未提交修改、特殊 index 标志、未完成 Git
  操作、符号链接/子模块等不安全目标会拒绝。不会 reset、强制覆盖或自动解决冲突。
- 更新器自己有独占锁，并在任何服务状态检查前取得五个业务 `flock`；
  分析/回测共用分析锁，基金/发现共用基金锁，通用ETF/境内美股ETF清单共用ETF锁。
  八个业务服务均纳入检查。锁被占用或任一业务服务处于
  active/activating/reloading/deactivating 时记录 `SKIP`，留待下次，不杀任务。
  检查和更新期间这些锁始终保持，碰巧启动的业务脚本可能跳过一次执行。
- 只快进已批准的目标提交，显式禁止覆盖 ignored/untracked 文件。保留 `.env`、
  数据库、报告、日志、虚拟环境和其他未跟踪文件；`.env` 只在更新前后计算摘要核对，
  不复制其内容、不输出摘要。旧版全部已跟踪源码/配置备份至
  `/opt/matrix-backups/update-*/source.tar`，私有 `manifest.json` 记录新旧提交及摘要。
  **不复制运行中的数据库，也不提供数据库回滚。**
- 只保留最近三份成功源码备份；失败备份不自动删除。有未完成备份时后续运行报错暂停，
  避免上次中断后误报成功。暂存树每个文件上限 8 MiB，总计上限 64 MiB；
  检查可用磁盘，Git fetch 最长 90 秒、语法验证最长 60 秒，服务总限时 10 分钟，
  内存上限 256 MiB、swap 上限 64 MiB，低 CPU/IO 优先级，适合与 Java/MySQL 共用小内存主机。

**安装前提：** Linux/systemd、Git、util-linux 的 `runuser`/`flock`、已验证可用的
`.venv`（包括当前锁文件所需依赖）。目录必须是 `/opt/Matrix` 的普通完整 Git checkout，
不是 worktree/符号链接。**仓库根目录、`.git` 及需由 Git 修改的已跟踪文件必须归
`admin:admin` 所有**；协调器以 root 运行，但每条 Git 命令均通过 `runuser -u admin`
执行。不设置全局 `safe.directory`，不更改网络/凭据。
上文 root clone 的旧安装须先在维护窗口妥善移交仓库所有权；不要为了修 Git 权限而
盲目递归修改 `.env`、数据库、日志和虚拟环境的所有权。
现有七个业务 unit 应使用默认脚本和锁路径，`.env`、unit drop-in、手工命令中均不能
覆盖 `MATRIX_*_HOME` / `MATRIX_*_LOCK_FILE`；不要绕过脚本直接运行 Python，也不要同时
手工操作 Git。初次安装须先按维护部署流程把包含本功能的 main 发布到线上并验证，
自动更新器不能代替首次依赖和业务 unit 的安装。

确认上述前提后，在 `/opt/Matrix` 执行：

```bash
stat -c '%U:%G %a %n' /opt/Matrix /opt/Matrix/.git
sudo -u admin git -C /opt/Matrix status --short --untracked-files=no
sudo -u admin git -C /opt/Matrix branch --show-current
sudo -u admin git -C /opt/Matrix remote get-url origin

sudo install -d -m 755 /usr/local/libexec
sudo install -o root -g root -m 700 scripts/update_matrix.py /usr/local/libexec/matrix-update.py
sudo install -d -o root -g root -m 700 /opt/matrix-backups
sudo install -o root -g root -m 644 deploy/systemd/matrix-update.service deploy/systemd/matrix-update.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/matrix-update.service /etc/systemd/system/matrix-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now matrix-update.timer

systemctl list-timers matrix-update.timer --no-pager
sudo journalctl -u matrix-update.service -n 80 --no-pager
# 可选：立即检查代码更新；不会触发基金或其他业务推送
sudo systemctl start matrix-update.service
```

入口使用 checkout 外的 root 私有 Python 副本，且程序一次性载入，不存在 shell 脚本
执行到一半被 Git 替换的问题。更新服务不加载 `.env`，不调整已有业务 timer；
基金每日 **09:30**、目录发现周日 **10:00** 的日程和启用状态保持不变。

**失败处理与回滚：**

1. 用 `journalctl -u matrix-update.service` 查看 `ERROR`/`SKIP` 和备份路径。
   连不上 GitHub 会失败，定时器仍保留，30 分钟后再次检查；不需要 SSH 会话保持在线。
   依赖/unit/config 拦截需要人工维护部署，不会随着网络恢复自动解决。
2. 暂停自动检查：`sudo systemctl disable --now matrix-update.timer`。
   等已运行的 updater 自然结束，勿在 Git 修改文件过程中随意终止进程。需要修复/回滚时，
   在维护窗口暂停业务调度、等待任务结束并持有五个锁；不要启动业务服务来“测试”更新器。
3. 首选在上游 main 提交经过测试的源码 revert，再让服务器安全快进。
   紧急本地回滚可依据私有 manifest 的 `old`，由 admin 使用
   `git restore --source=<old> --staged --worktree -- <逐项审核的已跟踪改动路径>` 恢复源码，
   这会留下明确的本地变更并自动阻止后续更新，直到管理员完成协调。
   不要整体解压备份覆盖 `/opt/Matrix`，不要 `reset --hard` 或覆盖 `.env`/运行数据。
4. 掉电、OOM、磁盘故障等可能中断 Git 的文件更新；源码快进不是跨文件事务。
   有未完成备份时须人工核查 HEAD、index、源码、`.env` 和依赖，必要时使用旧源码备份恢复，
   确认一致后把该失败备份**移到备份目录外的安全位置保留**，再重新启用 timer。
   可人工清理中断留下的 `.stage-*`（确认 updater 未运行）；切勿删除业务锁文件。
   源码更新无法自动回滚后续业务运行产生的数据变更；代码/数据库迁移须另行安排维护发布。

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
