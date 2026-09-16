from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from pydantic import ValidationError

import main
import stock_main
import us_main
from matrix_etf.core.config import Settings
from matrix_etf.data.sync_runner import SyncOutcome
from matrix_etf.strategy.ranking import _features, select_recommendations


def history(growth=0.002, amount=1e8):
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-01", periods=21).strftime("%Y-%m-%d"),
        "close": [100 * (1 + growth) ** i for i in range(21)],
        "volume": [1e6] * 21,
        "amount": [amount] * 21,
    })


def test_market_union_is_capped_and_strategies_keep_only_their_own_picks():
    symbols = [f"{i:06}.SH" for i in range(25)]
    engine = SimpleNamespace(get_ohlcv=lambda symbol: history())
    candidates = [symbols[:15], symbols[10:], [symbols[24], symbols[24]]]
    result = select_recommendations(engine, candidates)
    assert len(set().union(*map(set, result))) == 10
    assert all(len(picks) <= 10 and len(picks) == len(set(picks)) for picks in result)
    assert all(set(picks) <= set(raw) for picks, raw in zip(result, candidates))
    assert symbols[24] in result[2]  # Agreement counts, duplicate votes do not.
    assert result == select_recommendations(engine, [list(reversed(p)) for p in candidates])


def test_stronger_candidate_beats_input_order():
    engine = SimpleNamespace(get_ohlcv=lambda symbol: history(
        growth=0.004 if symbol == "BEST" else -0.003,
        amount=2e8 if symbol == "BEST" else 1e8,
    ))
    assert select_recommendations(engine, [["WORST", "BEST"]], limit=1) == [["BEST"]]


def test_consensus_counts_each_strategy_once_and_ties_use_symbol():
    engine = SimpleNamespace(get_ohlcv=lambda symbol: history())
    assert select_recommendations(engine, [["B"] * 5 + ["A"]], 1) == [["A"]]
    assert select_recommendations(engine, [["A", "B"], ["B"]], 1) == [["B"], ["B"]]


def test_invalid_history_excluded_and_read_errors_propagate():
    frame = history()
    frame.loc[20, "close"] = float("inf")
    engine = SimpleNamespace(get_ohlcv=lambda symbol: frame)
    assert select_recommendations(engine, [["BAD"]]) == [[]]
    engine.get_ohlcv = MagicMock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(RuntimeError, match="database unavailable"):
        select_recommendations(engine, [["BAD"]])


def test_us_uses_dollar_volume_and_only_latest_21_bars():
    frame = history(amount=0)
    engine = SimpleNamespace(get_ohlcv=lambda symbol: frame.iloc[::-1])
    features = _features(engine, "AAPL.US")
    assert features["liquidity"] == pytest.approx(
        (frame.close * frame.volume).tail(20).mean()
    )
    assert features["drawdown"] == 0
    assert _features(engine, "510300.SH") is None
    frame.loc[0, "close"] = float("nan")
    assert _features(engine, "AAPL.US") is None


@pytest.mark.parametrize("limit", [0, 11, -1])
def test_limit_cannot_exceed_ten(limit):
    with pytest.raises(ValidationError):
        Settings(feishu_webhook_url="https://example.com", recommendation_limit=limit)
    with pytest.raises(ValueError):
        select_recommendations(None, [], limit)


def test_empty_candidates_and_smaller_limit():
    engine = SimpleNamespace(get_ohlcv=lambda symbol: history())
    assert select_recommendations(engine, [[], []]) == [[], []]
    assert select_recommendations(engine, [["C", "B", "A"]], 2) == [["A", "B"]]


@pytest.mark.parametrize("module,engine_name,builder,market", [
    (main, "DataEngine", "build_strategies", "ETF"),
    (stock_main, "StockDataEngine", "_build_strategies", "CN"),
    (us_main, "UsStockDataEngine", "_build_strategies", "US"),
])
def test_all_pipelines_record_and_send_only_market_top_ten(
    monkeypatch, module, engine_name, builder, market
):
    settings = Settings(
        feishu_webhook_url="https://example.com", recommendation_limit=10,
    )
    symbols = [f"S{i:02}" for i in range(24)]
    engine = MagicMock()
    engine.get_local_symbols.return_value = symbols
    engine.sync_universe_and_get_symbols.return_value = symbols
    engine.get_ohlcv.side_effect = lambda symbol: history(growth=0.001 + int(symbol[1:]) / 10000)
    strategies = [
        SimpleNamespace(run=lambda: symbols[:16], webhook_key="first"),
        SimpleNamespace(run=lambda: symbols[8:], webhook_key="second"),
    ]
    notifier = MagicMock()
    analytics = MagicMock()
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, engine_name, lambda settings: engine)
    monkeypatch.setattr(module, builder, lambda engine, settings: strategies)
    monkeypatch.setattr(module, "FeishuNotifier", lambda *a, **kw: notifier)
    monkeypatch.setattr(module, "AnalyticsHook", lambda *a, **kw: analytics)
    monkeypatch.setattr(module, "sync_until_stable", lambda *a, **kw: SyncOutcome(
        True, 24, 24, "2026-01-29", 1, 1.0, "covered"
    ))
    monkeypatch.setattr("sys.argv", [module.__file__, "--force"])
    module.main()
    expected = select_recommendations(engine, [symbols[:16], symbols[8:]], 10)
    assert [call.kwargs["symbols"] for call in notifier.send.call_args_list] == expected
    assert [call.args[1] for call in analytics.record_and_perf_line.call_args_list] == expected
    assert len(set().union(*map(set, expected))) == 10
