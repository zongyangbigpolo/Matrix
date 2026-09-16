"""Memory-bounded reads for cross-sectional stock strategies."""

from itertools import groupby

import pandas as pd


def iter_stock_histories(conn, cutoff: str):
    """Stream one symbol at a time instead of materializing the whole market."""
    cursor = conn.execute(
        "SELECT symbol, date, close, high, volume FROM stock_daily "
        "WHERE date >= ? ORDER BY symbol, date",
        (cutoff,),
    )
    for symbol, rows in groupby(cursor, key=lambda row: row[0]):
        yield symbol, pd.DataFrame.from_records(
            list(rows), columns=["symbol", "date", "close", "high", "volume"]
        ).astype({"close": float, "high": float, "volume": float})
