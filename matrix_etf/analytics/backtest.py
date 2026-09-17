"""Offline, cash-constrained historical simulations, isolated from forward signals."""

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import OrderedDict
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from matrix_etf.analytics.history import historical_reads, trim_snapshot_history
from matrix_etf.analytics.replay import (
    MARKET_TABLES,
    _shrink_to,
    capped_engine_db,
    run_strategy_checked,
)
from matrix_etf.core.logger import get_logger
from matrix_etf.strategy.hold_days import resolve_hold_days

MARKET_PORTFOLIO = "__market_portfolio__"
MODEL_VERSION = 1
logger = get_logger(__name__)


class SQLitePrices:
    """Indexed execution quotes with a bounded cache, independent of universe size."""

    def __init__(self, connection, cache_size=2048):
        self.connection = connection
        self.cache_size = cache_size
        self.cache = OrderedDict()

    def get(self, key, default=None):
        if key not in self.cache:
            self.cache[key] = self.connection.execute(
                "SELECT open, close FROM prices WHERE date=? AND symbol=?", key
            ).fetchone()
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        else:
            self.cache.move_to_end(key)
        row = self.cache[key]
        return default if row is None else row

    def has_usable_prices(self):
        return any(
            _price(opening) and _price(closing)
            for opening, closing in self.connection.execute("SELECT open, close FROM prices")
        )

    def symbols_between(self, start_date, end_date):
        return [
            row[0] for row in self.connection.execute(
                "SELECT symbol FROM prices WHERE date>? AND date<=?",
                (start_date, end_date),
            )
        ]


@contextmanager
def execution_price_store(snapshot, daily_table, start_date, symbols=None):
    """Stream a narrow sample-window projection to disk before shrinking the snapshot.

    The separate file keeps future execution quotes invisible to strategy SQL. SQLite's
    INSERT SELECT streams internally; no market-sized Python list/DataFrame is created.
    """
    fd, path = tempfile.mkstemp(
        prefix="backtest_prices_", suffix=".db", dir=Path(snapshot).resolve().parent
    )
    os.close(fd)
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("PRAGMA cache_size=-2048")
            conn.execute(
                "CREATE TABLE prices (date TEXT, symbol TEXT, open REAL, close REAL, "
                "PRIMARY KEY (date, symbol)) WITHOUT ROWID"
            )
            conn.execute("ATTACH DATABASE ? AS historical", (str(snapshot),))
            indexed = False
            for index in conn.execute(f"PRAGMA historical.index_list({daily_table})").fetchall():
                name = index[1].replace('"', '""')
                columns = [
                    row[2] for row in conn.execute(f'PRAGMA historical.index_info("{name}")')
                ]
                indexed |= columns[:2] == ["symbol", "date"]
            projection = (
                "INSERT INTO prices SELECT date, symbol, open, close "
                f"FROM historical.{daily_table} "
            )
            if indexed and symbols is not None:
                conn.executemany(
                    projection + "WHERE symbol=? AND date>=?",
                    ((symbol, start_date) for symbol in symbols),
                )
            else:
                conn.execute(projection + "WHERE date>=?", (start_date,))
            conn.commit()
            conn.execute("DETACH DATABASE historical")
            yield SQLitePrices(conn)
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(path + suffix).unlink(missing_ok=True)


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000.0
    max_positions: int = 10
    commission_bps: float = 5.0
    slippage_bps: float = 5.0
    recommendation_limit: int = 10

    def __post_init__(self):
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        if (
            isinstance(self.max_positions, bool)
            or not isinstance(self.max_positions, int)
            or not 1 <= self.max_positions <= 10
        ):
            raise ValueError("max_positions must be an integer in 1..10")
        if (
            isinstance(self.recommendation_limit, bool)
            or not isinstance(self.recommendation_limit, int)
            or not 1 <= self.recommendation_limit <= 10
        ):
            raise ValueError("recommendation_limit must be an integer in 1..10")
        for value in (self.commission_bps, self.slippage_bps):
            if not math.isfinite(value) or not 0 <= value < 10_000:
                raise ValueError("costs must be finite and in [0, 10000) basis points")


def _price(value):
    try:
        return value is not None and math.isfinite(value) and value > 0
    except (TypeError, ValueError):
        return False


def simulate_portfolio(dates, prices, signals, config=None):
    """Simulate one funded account.

    prices[(date, symbol)] = (open, close); signals[date] = [(symbol, hold_sessions)].
    Close signals are eligible at the following *market* session open only. A missing
    open cancels that order; missing exit closes defer the sale. Shares are fractional.
    H=1 exits at entry-day close. Closing proceeds cannot fund that day's opening buys.
    """
    config = config or BacktestConfig()
    if not dates or dates != sorted(set(dates)):
        raise ValueError("dates must be nonempty, unique and ascending")
    fee = config.commission_bps / 10_000
    slip = config.slippage_bps / 10_000
    cash = config.initial_capital
    positions = {}
    trades, curve, warnings = [], [], []
    skipped = {"already_held": 0, "capacity": 0, "missing_open": 0, "end_of_sample": 0}
    signal_count = sum(len(picks) for picks in signals.values())
    stale_sessions = 0
    total_cost = 0.0
    for index, day in enumerate(dates):
        # Sizing is based on yesterday's marks, never today's close.
        opening_equity = cash + sum(p["quantity"] * p["mark"] for p in positions.values())
        budget = opening_equity / config.max_positions
        orders = signals.get(dates[index - 1], []) if index else []
        for symbol, hold in orders:
            if not isinstance(hold, int) or hold < 1:
                raise ValueError(f"Invalid holding period for {symbol}: {hold}")
            if symbol in positions:
                skipped["already_held"] += 1
                continue
            if len(positions) >= config.max_positions or cash <= 1e-9:
                skipped["capacity"] += 1
                continue
            opening = prices.get((day, symbol), (None, None))[0]
            if not _price(opening):
                skipped["missing_open"] += 1
                continue
            allocation = min(cash, budget)
            execution = opening * (1 + slip)
            quantity = allocation / (execution * (1 + fee))
            entry_fee = quantity * execution * fee
            cost = entry_fee + quantity * (execution - opening)
            cash = max(0.0, cash - allocation)
            trade = {
                "symbol": symbol, "signal_date": dates[index - 1], "entry_date": day,
                "entry_price": execution, "quantity": quantity, "hold_days": hold,
                "entry_cost": allocation, "fees": entry_fee, "costs": cost,
                "exit_date": None, "exit_price": None, "pnl": None,
                "mark": opening, "mark_date": day, "due_index": index + hold - 1,
            }
            trades.append(trade)
            positions[symbol] = trade
            total_cost += cost
        for symbol, trade in list(positions.items()):
            closing = prices.get((day, symbol), (None, None))[1]
            if not _price(closing):
                stale_sessions += 1
                continue
            trade["mark"], trade["mark_date"] = closing, day
            if index >= trade["due_index"]:
                execution = closing * (1 - slip)
                gross = trade["quantity"] * execution
                exit_fee = gross * fee
                proceeds = gross - exit_fee
                cost = exit_fee + trade["quantity"] * (closing - execution)
                trade.update(
                    exit_date=day, exit_price=execution, pnl=proceeds - trade["entry_cost"],
                    fees=trade["fees"] + exit_fee, costs=trade["costs"] + cost,
                )
                total_cost += cost
                cash += proceeds
                del positions[symbol]
        equity = cash + sum(p["quantity"] * p["mark"] for p in positions.values())
        curve.append({"date": day, "cash": cash, "equity": equity})
    skipped["end_of_sample"] = len(signals.get(dates[-1], []))
    if skipped["missing_open"]:
        warnings.append(f"{skipped['missing_open']} orders cancelled: missing next-session open")
    if stale_sessions:
        warnings.append(f"{stale_sessions} position-sessions without close: last known mark carried")
    overdue = [p["symbol"] for p in positions.values() if p["due_index"] < len(dates)]
    stale_end = [p["symbol"] for p in positions.values() if p["mark_date"] != dates[-1]]
    if overdue:
        warnings.append("Unfilled due exits: " + ", ".join(overdue))
    if stale_end:
        warnings.append("Stale terminal marks: " + ", ".join(stale_end))
    peak, drawdown = config.initial_capital, 0.0
    for point in curve:
        peak = max(peak, point["equity"])
        drawdown = min(drawdown, point["equity"] / peak - 1)
    ratio = curve[-1]["equity"] / config.initial_capital
    periods = len(dates) - 1
    ann = None
    if periods:
        exponent = math.log(ratio) * 252 / periods if ratio > 0 else -math.inf
        if exponent < 709:
            ann = math.expm1(exponent)
        else:
            warnings.append("Annualized return overflow: sample too short")
    status = "ok" if trades else ("no_signals" if not signal_count else "no_trades")
    if warnings:
        status = "partial"
    return {
        "status": status, "start_date": dates[0], "end_date": dates[-1],
        "sessions": len(dates), "initial_capital": config.initial_capital,
        "final_equity": curve[-1]["equity"], "total_return": ratio - 1,
        "ann_return": ann, "max_drawdown": drawdown, "signal_count": signal_count,
        "trade_count": len(trades), "closed_trades": len(trades) - len(positions),
        "open_positions": len(positions), "costs": total_cost, "skipped": skipped,
        "warnings": warnings, "equity": curve, "trades": trades,
    }


def _failed_result(status, message, dates):
    return {
        "status": status, "error": message, "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None, "sessions": len(dates),
        "total_return": None, "ann_return": None, "max_drawdown": None,
        "trade_count": 0, "closed_trades": 0, "signal_count": 0, "open_positions": 0,
        "equity": [], "trades": [], "warnings": [],
    }


def backtest_market(engine, strategies, market, analytics, days=252, config=None):
    """Replay every strategy before ranking; persist complete results even for failures.

    A broken strategy invalidates the market's ranking (consensus is incomplete), so
    every account is explicitly failed rather than reporting misleading partial profits.
    Reporting filters must be applied *after* this function.
    """
    with ExitStack() as resources:
        return _backtest_market(engine, strategies, market, analytics, days, config, resources)


def _backtest_market(engine, strategies, market, analytics, days, config, resources):
    if market not in MARKET_TABLES or days < 1:
        raise ValueError("Unknown market or nonpositive days")
    config = config or BacktestConfig()
    names = [type(strategy).__name__ for strategy in strategies]
    if not names or len(set(names)) != len(names):
        raise ValueError("Strategies must be nonempty and have unique class names")
    dates, signals, prices, errors = [], {name: {} for name in names}, {}, []
    candidate_counts = {name: 0 for name in names}
    table = MARKET_TABLES[market][0]
    try:
        with capped_engine_db(engine, table) as snapshot:
            with closing(sqlite3.connect(snapshot)) as conn:
                # Calendar/symbol metadata are small. Reuse them instead of another
                # expensive MIN/MAX/COUNT(DISTINCT ...) scan of the entire market.
                calendar = [
                    row[0] for row in conn.execute(
                        f"SELECT DISTINCT date FROM {table} ORDER BY date"
                    ) if row[0]
                ]
                dates = calendar[-days:]
                if not dates:
                    raise ValueError("No local daily prices")
                symbol_starts = conn.execute(
                    f"SELECT symbol, MIN(date) FROM {table} GROUP BY symbol ORDER BY symbol"
                ).fetchall()
            coverage = (calendar[0], calendar[-1], len(calendar), len(symbol_starts))
            symbols = [symbol for symbol, _ in symbol_starts]
            prices = resources.enter_context(
                execution_price_store(snapshot, table, dates[0], symbols)
            )
            if not prices.has_usable_prices():
                raise ValueError("No usable positive open/close prices")
            trim_snapshot_history(
                snapshot, table, strategies, len(dates),
                symbols,
            )
            from matrix_etf.strategy.ranking import RANKING_VERSION, select_recommendations

            previous_day = dates[-1]
            for step, day in enumerate(reversed(dates), 1):
                logger.info(f"[{market}] 历史选股 {step}/{len(dates)}：as-of {day}")
                _shrink_to(snapshot, table, day, prices.symbols_between(day, previous_day))
                previous_day = day
                visible_symbols = [symbol for symbol, first in symbol_starts if first <= day]
                with historical_reads(engine, strategies, market, visible_symbols):
                    candidates = []
                    for strategy, name in zip(strategies, names):
                        try:
                            picks = run_strategy_checked(strategy)
                            candidates.append(picks)
                            candidate_counts[name] += len(set(picks))
                        except Exception as exc:
                            errors.append(f"{name} @ {day}: {type(exc).__name__}: {exc}")
                            candidates.append([])
                    if errors:
                        break
                    try:
                        ranked = select_recommendations(
                            engine, candidates, limit=config.recommendation_limit
                        )
                        if len(ranked) != len(strategies):
                            raise ValueError("Ranking returned incorrect strategy count")
                        for strategy, name, picks in zip(strategies, names, ranked):
                            hold = resolve_hold_days(strategy)
                            if hold < 1:
                                raise ValueError(f"{name}: holding period must be positive")
                            signals[name][day] = [(symbol, hold) for symbol in dict.fromkeys(picks)]
                    except Exception as exc:
                        errors.append(f"Ranking/holding period @ {day}: {type(exc).__name__}: {exc}")
                if errors:
                    break
    except (FileNotFoundError, sqlite3.DatabaseError, ValueError) as exc:
        status = "no_data" if not dates else "error"
        errors.append(f"{type(exc).__name__}: {exc}")
    else:
        status = "error" if errors else "ok"
    if errors:
        results = {name: _failed_result(status, "; ".join(errors), dates)
                   for name in [*names, MARKET_PORTFOLIO]}
    else:
        combined = {}
        for day in dates:
            picks = {}
            for name in names:
                for symbol, hold in signals[name][day]:
                    # Shared positions use the longest recommendation on the signal day.
                    picks[symbol] = max(picks.get(symbol, 0), hold)
            combined[day] = list(picks.items())
        results = {
            name: simulate_portfolio(dates, prices, daily_signals, config)
            for name, daily_signals in {**signals, MARKET_PORTFOLIO: combined}.items()
        }
        for name, result in results.items():
            result["signals"] = signals.get(name, combined)
            result["candidate_count"] = candidate_counts.get(name, sum(candidate_counts.values()))
            result["ranking_version"] = RANKING_VERSION
            result["local_coverage"] = dict(zip(
                ["first_date", "last_date", "sessions", "symbols"], coverage
            ))
    for result in results.values():
        result["config"] = asdict(config)
    payload = {
        "model_version": MODEL_VERSION, "market": market, "days": days,
        "config": asdict(config), "results": results,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    run_id = hashlib.sha256(encoded.encode()).hexdigest()[:24]
    report = format_backtest_report(results, market, run_id)
    with analytics.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO backtest_run VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, market, dates[0] if dates else None, dates[-1] if dates else None,
             json.dumps({"model_version": MODEL_VERSION, "days": days, **asdict(config)}),
             report, datetime.now(timezone.utc).isoformat()),
        )
        for name, result in results.items():
            conn.execute(
                "INSERT OR REPLACE INTO backtest_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, market, name, result["status"], result["total_return"],
                 result["ann_return"], result["max_drawdown"], result["trade_count"],
                 json.dumps(result, ensure_ascii=False, allow_nan=False)),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO backtest_equity VALUES (?, ?, ?, ?, ?)",
                [(run_id, name, p["date"], p["cash"], p["equity"]) for p in result["equity"]],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO backtest_trade VALUES (?, ?, ?, ?)",
                [(run_id, name, i, json.dumps(t)) for i, t in enumerate(result["trades"])],
            )
    return {"run_id": run_id, "market": market, "results": results, "report": report}


def format_backtest_report(results, market, run_id="", strategy_filter=None):
    """Markdown report: funded total return, never a mean of signal returns."""
    lines = [
        f"## [{market}] 历史组合回测 / SIMULATION — {run_id}",
        "独立资金账户；非实盘收益。总收益包含现金、已平仓盈亏及期末持仓市值。",
        "| 策略 | 状态 | 样本期 | 交易日 | 总收益 | 年化(252) | 最大回撤 | 开仓/平仓/未平仓 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    if results and (config := next(iter(results.values())).get("config")):
        lines.insert(2, (
            f"初始资金 {config['initial_capital']:,.2f} / 最大持仓 {config['max_positions']} / "
            f"每边佣金 {config['commission_bps']} bps / 每边滑点 {config['slippage_bps']} bps / "
            f"每日每市场推荐上限 {config['recommendation_limit']}。\n"
        ))

    def pct(value):
        return "N/A" if value is None else f"{value:+.2%}"

    for name, result in results.items():
        if strategy_filter and strategy_filter.lower() not in name.lower():
            continue
        lines.append(
            f"| {name} | {result['status']} | {result['start_date']} → {result['end_date']} "
            f"| {result['sessions']} | {pct(result['total_return'])} "
            f"| {pct(result['ann_return'])} | {pct(result['max_drawdown'])} "
            f"| {result['trade_count']}/{result['closed_trades']}/{result['open_positions']} |"
        )
        if result.get("error"):
            lines.append(f"\n**ERROR {name}:** {result['error']}\n")
        for warning in result["warnings"]:
            lines.append(f"\n**WARNING {name}:** {warning}\n")
    lines.append(
        "\nno_signals = 本地历史/预热期内没有入选信号；no_trades = 有信号但无可执行订单；"
        "no_data/error = 未能回测，不是零收益；partial = 缺价等数据问题，收益仅供参考。"
        "\n期末未平仓按最后有效价格估值，不强制卖出；年化短样本可能失真。"
        "基于本地价格与现存标的池，存在幸存者偏差及复权/退市数据局限，无基准联网。"
    )
    return "\n".join(lines)
