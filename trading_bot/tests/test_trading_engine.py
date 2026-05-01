import pytest


@pytest.mark.asyncio
async def test_order_validator_max_trade():
    from exchange.order_validator import validate_order
    
    result = await validate_order(
        action="BUY",
        symbol="BTCUSDT",
        quantity_usd=200.0,
        confidence=0.8,
    )
    
    assert result.valid is False
    assert "exceeds" in result.error.lower()


@pytest.mark.asyncio
async def test_order_validator_valid():
    from exchange.order_validator import validate_order
    
    result = await validate_order(
        action="BUY",
        symbol="BTCUSDT",
        quantity_usd=50.0,
        confidence=0.8,
    )
    
    assert result.valid is True


@pytest.mark.asyncio
async def test_strategy_defaults():
    from trading.strategy import DEFAULT_STRATEGY, get_strategy
    
    assert DEFAULT_STRATEGY.paper_trading is True
    assert DEFAULT_STRATEGY.max_trade_usd == 100.0
    assert DEFAULT_STRATEGY.max_open_trades == 3
    assert DEFAULT_STRATEGY.confidence_threshold == 0.6


@pytest.mark.asyncio
async def test_portfolio_empty():
    from trading.portfolio import get_portfolio
    
    portfolio = await get_portfolio()
    
    assert portfolio.total_value_usd == 0.0
    assert portfolio.unrealized_pnl == 0.0
    assert isinstance(portfolio.positions, list)


def test_strategy_dataclass():
    from trading.strategy import Strategy
    
    strategy = Strategy(
        allowed_symbols=["BTCUSDT", "ETHUSDT"],
        max_trade_usd=100.0,
        max_open_trades=3,
        confidence_threshold=0.6,
        stop_loss_pct=2.0,
        take_profit_pct=4.0,
        paper_trading=True,
        trading_interval_seconds=60,
    )
    
    assert "BTCUSDT" in strategy.allowed_symbols
    assert strategy.paper_trading is True