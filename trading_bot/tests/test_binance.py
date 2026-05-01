import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_calculate_indicators():
    from ai_brain.prompt_builder import calculate_indicators
    
    ohlcv = [
        {"time": 1700000000 + i * 300000, "open": "45000", "high": "45500", "low": "44500", "close": "45000", "volume": "1000"}
        for i in range(20)
    ]
    
    indicators = calculate_indicators(ohlcv)
    
    assert indicators.current_price == 45000.0
    assert indicators.sma_7 is not None
    assert indicators.rsi_14 is not None


@pytest.mark.asyncio
async def test_parse_response_valid():
    from ai_brain.prompt_builder import parse_response
    
    response = '{"action": "BUY", "symbol": "BTCUSDT", "quantity_usd": 50.0, "reason": "Test", "confidence": 0.8}'
    parsed = parse_response(response)
    
    assert parsed is not None
    assert parsed["action"] == "BUY"
    assert parsed["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_parse_response_invalid():
    from ai_brain.prompt_builder import parse_response
    
    response = "not valid json"
    parsed = parse_response(response)
    
    assert parsed is None


@pytest.mark.asyncio
async def test_prompt_build():
    from ai_brain.prompt_builder import build
    from ai_brain.prompt_builder import TradingIndicators
    
    indicators = TradingIndicators(
        sma_7=45000.0,
        sma_25=44500.0,
        rsi_14=55.0,
        current_price=45000.0,
        price_change_1h_pct=1.5,
    )
    
    system_prompt, user_prompt = build(
        symbol="BTCUSDT",
        current_price=45000.0,
        ohlcv=[],
        positions=[],
        balance_usd=1000.0,
        last_decisions=[],
        indicators=indicators,
        max_trade_usd=100.0,
        max_open_trades=3,
        allowed_symbols=["BTCUSDT"],
        confidence_threshold=0.6,
        paper_trading=True,
    )
    
    assert "BTCUSDT" in user_prompt
    assert "45000" in user_prompt


def test_sma_calculation():
    from ai_brain.prompt_builder import sma
    
    closes = [100, 102, 101, 103, 105, 104, 106, 108]
    
    # SMA-7 of last 7 values: [102,101,103,105,104,106,108] = 729/7 ≈ 104.1429
    result = sma(closes, 7)
    assert result == pytest.approx(104.142857, rel=1e-4)


def test_rsi_calculation():
    from ai_brain.prompt_builder import rsi
    
    closes = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 110, 108, 111, 112, 113]
    
    result = rsi(closes, 14)
    assert result is not None
    assert 0 <= result <= 100