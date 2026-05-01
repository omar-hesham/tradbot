from datetime import datetime, timedelta, timezone

import pytest

from ai_brain.schemas import TradeRecommendation
from services.backtest_engine import BacktestEngine


def _kline(ts_ms: int, close_price: str) -> list:
    return [ts_ms, "0", "0", "0", close_price, "0", 0, 0, 0, 0, 0, 0]


@pytest.mark.asyncio
async def test_backtest_returns_empty_summary_when_no_klines(monkeypatch):
    engine = BacktestEngine()

    async def fake_get_klines(symbol, interval, start_date, end_date):
        return []

    monkeypatch.setattr("services.backtest_engine.binance_client.get_klines", fake_get_klines)

    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc)
    result = await engine.run(["BTCUSDT"], start, end, initial_capital=1000.0, interval="1h")

    assert result["summary"]["initial_capital"] == 1000.0
    assert result["summary"]["final_equity"] == 1000.0
    assert result["summary"]["total_trades"] == 0
    assert result["summary"]["pnl_pct"] == 0.0
    assert result["trades"] == []


@pytest.mark.asyncio
async def test_backtest_skips_none_recommendations(monkeypatch):
    engine = BacktestEngine()
    now = datetime.now(timezone.utc)
    ts0 = int((now - timedelta(hours=1)).timestamp() * 1000)
    ts1 = int(now.timestamp() * 1000)

    async def fake_get_klines(symbol, interval, start_date, end_date):
        return [_kline(ts0, "100"), _kline(ts1, "110")]

    async def fake_analyze(symbol, price):
        return None

    async def fake_approve(session, recommendation, is_backtest=False):
        return True, "ok"

    async def fake_get_session():
        yield object()

    monkeypatch.setattr("services.backtest_engine.binance_client.get_klines", fake_get_klines)
    monkeypatch.setattr("services.backtest_engine.analyze_agent", fake_analyze)
    monkeypatch.setattr("services.backtest_engine.risk_engine.approve", fake_approve)
    monkeypatch.setattr("services.backtest_engine.get_session", fake_get_session)

    start = now - timedelta(days=1)
    end = now
    result = await engine.run(["BTCUSDT"], start, end, initial_capital=1000.0, interval="1h")

    assert result["summary"]["total_trades"] == 0
    assert result["summary"]["final_equity"] == 1000.0
    assert len(result["curve"]) >= 2
