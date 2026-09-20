"""Fresh metadata discovery, selected-only daily sync, and indexed SQLite reads."""

import sqlite3
from datetime import date, datetime

import pandas as pd

from matrix_etf.core.config import Settings
from matrix_etf.data.engine import DataEngine
from matrix_etf.data.tickflow_client import create_tickflow_client
from matrix_etf.us_etf.listing import classify, finite_number, make_quote

MAX_UNIVERSE = 5000
MAX_CANDIDATES = 200
METADATA_BATCH = 250
DAILY_BATCH = 20
DAILY_COUNT = 30


class SourceError(RuntimeError):
    pass


class USEtfSource:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = DataEngine(settings)
        self.client = create_tickflow_client(
            settings.tickflow_api_key, timeout=20.0, max_retries=0,
        )

    def discover(self):
        detail = self.client.universes.get("CN_ETF")
        symbols = detail.get("symbols") if isinstance(detail, dict) else None
        if (not isinstance(symbols, list) or not symbols or len(symbols) > MAX_UNIVERSE
                or any(not isinstance(s, str) for s in symbols)):
            raise SourceError("CN_ETF 标的池为空、异常或超过安全上限")
        symbols = sorted(set(symbols))
        selected = []
        for start in range(0, len(symbols), METADATA_BATCH):
            chunk = symbols[start:start + METADATA_BATCH]
            records = self.client.instruments.batch(chunk)
            if not isinstance(records, list) or len(records) != len(chunk):
                raise SourceError("ETF 当前元数据不完整，不能确认分类覆盖")
            by_symbol = {r.get("symbol"): r for r in records if isinstance(r, dict)}
            if set(by_symbol) != set(chunk):
                raise SourceError("ETF 当前元数据代码不匹配")
            for symbol in chunk:
                record = by_symbol[symbol]
                if not record.get("name") or not (
                    record.get("type") or record.get("instrument_type")
                ):
                    raise SourceError("ETF 当前元数据缺少名称或类型，不能确认分类")
                product = classify(record)
                if product:
                    selected.append(product)
        if not selected or len(selected) > MAX_CANDIDATES:
            raise SourceError("未发现可明确分类的境内美股 ETF，或候选超过安全上限")
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.executemany(
                """INSERT INTO etf_basic (symbol, code, exchange, name, type, updated_at)
                   VALUES (?, ?, ?, ?, 'etf', ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                   code=excluded.code, exchange=excluded.exchange, name=excluded.name,
                   type=excluded.type, updated_at=excluded.updated_at""",
                [(p.symbol, p.symbol[:6], p.symbol[-2:], p.name,
                  datetime.now().isoformat()) for p in selected],
            )
        return selected

    def fetch(self, expected: date):
        products = self.discover()
        quotes = []
        for start in range(0, len(products), DAILY_BATCH):
            chunk = products[start:start + DAILY_BATCH]
            data = self.client.klines.batch(
                [p.symbol for p in chunk], period="1d", count=DAILY_COUNT,
                adjust="forward", as_dataframe=True, max_workers=1, batch_size=DAILY_BATCH,
            )
            # TickFlow may suppress failed batches. Missing keys are failures, not empty quotes.
            if not isinstance(data, dict) or any(p.symbol not in data for p in chunk):
                raise SourceError("ETF 日线请求失败或响应缺少标的；未使用旧缓存")
            for product in chunk:
                frame = data[product.symbol]
                if not isinstance(frame, pd.DataFrame) or len(frame) > DAILY_COUNT:
                    raise SourceError("ETF 日线响应格式异常")
                if frame.empty:
                    quotes.append(make_quote(product, [], expected, self.settings))
                    continue
                required = {"trade_date", "open", "high", "low", "close", "volume", "amount"}
                if not required.issubset(frame.columns):
                    raise SourceError("ETF 日线响应缺少字段")
                frame = frame.copy()
                days = []
                for value in frame["trade_date"]:
                    try:
                        day = date.fromisoformat(str(value))
                    except ValueError:
                        raise SourceError("ETF 日线交易日期无效") from None
                    if day > expected:
                        raise SourceError("ETF 日线包含尚未收盘或未来日期")
                    days.append(day.isoformat())
                if len(set(days)) != len(days):
                    raise SourceError("ETF 日线出现重复日期")
                frame["trade_date"] = days
                for column in ("open", "high", "low", "close", "volume", "amount"):
                    frame[column] = frame[column].map(
                        lambda v, c=column: finite_number(v, positive=c != "amount" and c != "volume")
                    )
                normalized = frame.rename(columns={"trade_date": "date"})
                normalized["symbol"] = product.symbol
                # Like DataEngine, do not overwrite a valid stored close with missing data.
                # Still surface that bad observation in this run instead of reviving the cache.
                invalid = {
                    row.date: (row.date, None, finite_number(row.amount))
                    for row in normalized.itertuples(index=False)
                    if finite_number(row.close, positive=True) is None
                }
                self.engine._upsert_daily(normalized[normalized["close"].notna()])
                with sqlite3.connect(self.engine.db_path) as conn:
                    rows = conn.execute(
                        """SELECT date, close, amount FROM etf_daily
                           WHERE symbol = ? AND date IN ({})
                           ORDER BY date DESC LIMIT ?""".format(",".join("?" for _ in days)),
                        [product.symbol, *days, DAILY_COUNT],
                    ).fetchall()
                rows = [row for row in rows if row[0] not in invalid] + list(invalid.values())
                quotes.append(make_quote(product, rows, expected, self.settings))
        return quotes

    def close(self):
        self.client.close()
