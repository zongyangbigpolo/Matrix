# Matrix 架构设计 | Architecture

> Matrix 是一套 ETF 推荐系统：使用 [tickflow](https://github.com/tickflow-org/tickflow)
> 作为唯一行情数据源，收盘后同步沪深 ETF 日线，运行技术型选 ETF 策略，并把结果推送到飞书。

本项目在工程结构上参考 NebulaStock，但**数据源完全改为 tickflow，彻底摒弃 baostock**，
且选股标的从个股改为 **ETF**。由于 ETF 没有 PE/PB/ROE 等基本面字段，Matrix 的策略
体系完全基于**价格、成交量、成交额**等技术与动量指标。

---

## 1. 系统分层

```mermaid
flowchart TD
    subgraph CLI["入口层 main.py (argparse)"]
        A1["日常模式"]
        A2["--backfill 回填"]
        A3["--sync-universe 同步ETF池"]
        A4["--refresh-metrics 刷新指标"]
        A5["--etf-report 生成报告"]
    end

    subgraph CORE["核心层 matrix_etf/core"]
        C1["config.py<br/>pydantic-settings"]
        C2["logger.py<br/>rich 日志"]
    end

    subgraph DATA["数据层 matrix_etf/data/engine.py"]
        D1["TickFlow 客户端<br/>free() 或 api_key"]
        D2["SQLite 存储"]
        D3["universe / klines / metrics 同步"]
    end

    subgraph STRAT["策略层 matrix_etf/strategy"]
        S1["RpsMomentum"]
        S2["TrendMa"]
        S3["BreakoutVolume"]
        S4["MeanReversion"]
        S5["Mega7 风格轮动<br/>RiskAdj/Volume/LowVol"]
        S6["EtfPoolReport"]
    end

    subgraph NOTIFY["通知层 matrix_etf/notify"]
        N1["FeishuNotifier<br/>按策略路由 webhook"]
    end

    EXT1["tickflow API<br/>docs.tickflow.org"]
    EXT2["飞书群机器人 Webhook"]

    CLI --> CORE
    CLI --> DATA
    D1 <--> EXT1
    DATA --> STRAT
    STRAT --> NOTIFY
    N1 --> EXT2
    C1 -.配置.-> DATA
    C1 -.配置.-> NOTIFY
    C2 -.日志.-> DATA
    C2 -.日志.-> STRAT
```

---

## 2. 日常运行数据流

```mermaid
sequenceDiagram
    autonumber
    participant Cron as systemd timer
    participant Main as main.py
    participant Eng as DataEngine
    participant TF as tickflow API
    participant DB as SQLite
    participant Strat as Strategies
    participant Fs as Feishu

    Cron->>Main: 交易日收盘后触发
    Main->>Eng: sync_universe()
    Eng->>TF: universes.get("CN_ETF") + instruments
    TF-->>Eng: ETF 列表 + 基础信息
    Eng->>DB: upsert etf_basic
    Main->>Eng: sync_daily(symbols)
    Eng->>TF: klines.batch(1d, 增量 count)
    TF-->>Eng: 每只 ETF 日线 DataFrame
    Eng->>DB: upsert etf_daily
    Main->>Eng: refresh_metrics(symbols)
    Eng->>DB: 计算并写入 etf_metrics
    loop 每个策略（7套）
        Main->>Strat: run()
        Strat->>DB: 读取 etf_daily / etf_metrics
        Strat-->>Main: 选出 ETF 列表
        Main->>Fs: send(symbols, strategy)
        Fs-->>Main: 推送结果
    end
```

---

## 3. 数据模型（SQLite）

```mermaid
erDiagram
    etf_basic {
        TEXT symbol PK
        TEXT code
        TEXT exchange
        TEXT name
        TEXT type
        TEXT listing_date
        REAL total_shares
        REAL float_shares
        TEXT updated_at
    }
    etf_daily {
        TEXT symbol
        TEXT date
        REAL open
        REAL high
        REAL low
        REAL close
        REAL volume
        REAL amount
    }
    etf_metrics {
        TEXT symbol PK
        TEXT update_date
        REAL close
        REAL ma20
        REAL ma50
        REAL ma200
        REAL ret_20d
        REAL ret_60d
        REAL ret_120d
        REAL vol_amount_20
        REAL amount_last
        REAL atr_pct_14
        REAL rsi_14
        REAL high_120
        REAL drawdown_60d
        INTEGER above_ma200
        INTEGER sample_days
    }
    etf_basic ||--o{ etf_daily : "symbol"
    etf_basic ||--|| etf_metrics : "symbol"
```

- `etf_daily` 以 `(symbol, date)` 唯一约束，写入使用 upsert，避免重复与误删。
- `etf_metrics` 每只 ETF 一行，预聚合趋势/动量/流动性指标，供报告和策略快速筛选。
- RPS（相对强度）为**横截面**指标，在策略/报告运行时对全体 ETF 的收益率排名计算，不落库。

---

## 4. 策略体系概览

```mermaid
flowchart LR
    U["CN_ETF 全体 ETF"] --> F["流动性过滤<br/>近20日成交额均值 ≥ 阈值"]
    F --> M["RpsMomentum<br/>横截面动量最强"]
    F --> T["TrendMa<br/>均线多头排列"]
    F --> B["BreakoutVolume<br/>放量突破新高"]
    F --> R["MeanReversion<br/>强势回踩反弹"]
    F --> G["Mega7Rotation<br/>风险调整/成交额/低波轮动"]
    M --> P["EtfPoolReport<br/>四梯队综合打分"]
    T --> P
    B --> P
    R --> P
    G --> P
    P --> OUT["飞书推送 + Markdown 报告"]
```

策略详细规则见 [etf_strategy.md](etf_strategy.md)，数据源细节见 [data_source.md](data_source.md)。

---

## 5. 部署拓扑（Alibaba Cloud Linux）

```mermaid
flowchart TD
    Timer["systemd timer<br/>matrix-etf.timer<br/>周一~周五 19:15"] --> Svc["systemd service<br/>matrix-etf.service"]
    Svc --> Sh["scripts/run_matrix.sh<br/>flock 防并发"]
    Sh --> Py[".venv/bin/python main.py"]
    Py --> TF["tickflow API (出站 HTTPS)"]
    Py --> DB[("data/matrix_etf.db")]
    Py --> Fs["飞书 Webhook (出站 HTTPS)"]
```

部署步骤见 [../README.md](../README.md)。

---

## 6. 设计取舍

| 决策 | 原因 |
|------|------|
| 数据源用 tickflow free 服务 | ETF 日线免费、无需注册、部署简单；如需实时/分钟线可切换 API key |
| 标的用 `CN_ETF` 池 | tickflow 官方维护的沪深 ETF 全集（约 1500+ 只） |
| 只做技术/动量策略 | ETF 无个股基本面字段，价量数据是唯一可靠信号 |
| 用成交额做流动性过滤 | 剔除迷你/僵尸 ETF，保证可交易性 |
| 指标预聚合进 `etf_metrics` | 报告与筛选无需每次重算长周期指标 |
| SQLite 本地存储 | 单机部署、可直接拷贝迁移、零运维 |
| systemd timer 而非 crontab | 与阿里云 Linux 原生集成、日志统一、可持久化补跑 |
