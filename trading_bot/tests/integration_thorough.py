import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from core.database import get_session, Base, init_db
from core.models import BotConfig, Position, Trade, LiveTradeRequest, DailyLossTracker, KnowledgeDocument, KnowledgeChunk
from core.bot_state import get_runtime_settings
from ai_brain.schemas import TradeRecommendation
from services.risk_engine import risk_engine
from trading.safe_runner import safe_agent_runner, process_recommendation

# ─── Mocks ───
@pytest.fixture
def mock_binance():
    with patch("exchange.binance_client.binance_client") as mock:
        mock.get_account_balances = AsyncMock(return_value={"USDT": 1000.0, "BTC": 0.1})
        mock.get_ticker = AsyncMock(return_value={"lastPrice": "50000.0"})
        mock.execute_order = AsyncMock(return_value={"orderId": 12345, "status": "FILLED", "price": "50000.0"})
        mock.place_market_order = AsyncMock(return_value={"orderId": 12345, "status": "FILLED", "fills": [{"price": "50000.0", "qty": "0.002"}]})
        yield mock

@pytest.fixture
def mock_exchange_info():
    # Mock ExchangeCache to return valid constraints for any symbol
    info = {
        "symbol": "BTCUSDT", "min_qty": 0.00001, "max_qty": 1000.0, "step_size": 0.00001,
        "min_notional": 10.0, "tick_size": 0.01, "status": "TRADING"
    }
    with patch("exchange.exchange_cache.get_symbol_constraints", return_value=info):

        yield info

@pytest.fixture
def mock_embeddings():
    with patch("ai_brain.rag.get_embedding", return_value=[0.1]*1536):
        yield

@pytest.fixture
def mock_ai_provider():

    with patch("ai_brain.provider_factory.get_ai_provider") as mock_factory:
        mock_p = AsyncMock()
        mock_p.ask = AsyncMock(return_value='{"compliant": true, "violation": null}')
        mock_factory.return_value = mock_p
        yield mock_p

# ─── Database Setup ───
@pytest.fixture(autouse=True)
async def setup_test_db():
    import os
    os.environ["DB_PATH"] = "data/test_thorough.db"
    from core.database import init_db
    await init_db()
    yield

async def clear_db(session):
    for model in [Position, Trade, LiveTradeRequest, DailyLossTracker, BotConfig, KnowledgeDocument]:
        await session.execute(model.__table__.delete())
    await session.commit()

async def upsert_config(session, key, value):
    from sqlalchemy.dialects.sqlite import insert
    from core.models import BotConfig
    stmt = insert(BotConfig).values(key=key, value=value).on_conflict_do_update(
        index_elements=["key"],
        set_={"value": value}
    )
    await session.execute(stmt)


@pytest.mark.asyncio
async def test_phase_5_live_approval_flow(mock_binance, mock_ai_provider, mock_exchange_info, mock_embeddings):

    """
    Verifies that in LIVE mode, trades are queued and NOT executed until approved.
    """
    async for session in get_session():
        await clear_db(session)
        # 1. Set bot to LIVE mode

        await upsert_config(session, "bot_running", "true")
        await upsert_config(session, "paper_trading", "false")
        await upsert_config(session, "live_trading_confirmed", "true")
        await upsert_config(session, "max_trade_usd", "500.0")

        await upsert_config(session, "strategy_profiles", '{"short_term": {"enabled": true, "min_confidence": 0.5}}')
        await session.commit()


        # 2. Simulate an Agent Recommendation
        rec = TradeRecommendation(
            symbol="BTCUSDT",
            action="BUY",
            current_price=50000.0,
            suggested_allocation_usd=200.0, # Will be capped at $100
            confidence=0.9,
            strategy="short_term",
            reasoning_summary="Institutional bid detected",
            entry_conditions=["Volume spike"],
            risk_factors=["Overbought"],
            stop_loss_pct=2.0,
            take_profit_pct=5.0,
            max_holding_period_minutes=60,
            should_execute=True
        )

        # 3. Process recommendation (the entry point for agents)
        await process_recommendation(session, "short_term", rec)


        # 4. ASSERT: No trade recorded, but a request exists
        res = await session.execute(select(Trade))
        assert res.scalar_one_or_none() is None

        res = await session.execute(select(LiveTradeRequest))
        req = res.scalar_one_or_none()
        assert req is not None
        assert req.symbol == "BTCUSDT"
        assert req.allocation_usd == 100.0  # Verify PHASE 5 CAP WAS APPLIED
        assert req.status == "pending"

        # 5. Approve the request manually (simulating the API endpoint logic)
        from services.execution_engine import execute_live_trade
        await execute_live_trade(session, rec, req.price, req.quantity)
        
        req.status = "executed"
        await session.commit()

        # 6. ASSERT: Trade is now filled
        await session.refresh(req)
        assert req.status == "executed"

        
        res = await session.execute(select(Trade))
        trade = res.scalar_one_or_none()
        assert trade is not None
        assert trade.status == "filled"
        assert trade.quantity > 0



@pytest.mark.asyncio
async def test_daily_loss_limit_gatekeeper(mock_binance, mock_ai_provider, mock_exchange_info, mock_embeddings):

    """
    Verifies that exceeding the daily loss limit halts all trading.
    """
    async for session in get_session():
        await clear_db(session)
        # 1. Setup loss limit

        await upsert_config(session, "bot_running", "true")
        await upsert_config(session, "paper_trading", "false")
        await upsert_config(session, "daily_loss_limit", "50.0")
        await upsert_config(session, "strategy_profiles", '{"short_term": {"enabled": true, "min_confidence": 0.5}}')
        await session.commit()
        
        # 2. Simulate a realized loss today
        from datetime import datetime
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        session.add(DailyLossTracker(date=today, total_loss=60.0)) # Exceeds $50 limit
        await session.commit()

        # 3. Request a new trade
        rec = TradeRecommendation(
            symbol="ETHUSDT", action="BUY", current_price=2000.0, suggested_allocation_usd=50.0,
            confidence=0.9, strategy="short_term", reasoning_summary="test", entry_conditions=[],
            risk_factors=[], stop_loss_pct=1, take_profit_pct=2, max_holding_period_minutes=10, should_execute=True
        )

        # 4. Check RiskEngine approval
        approved, reason = await risk_engine.approve(session, rec)
        
        assert approved is False
        assert "Daily Loss Limit" in reason

@pytest.mark.asyncio
async def test_rag_compliance_interception(mock_binance, mock_ai_provider, mock_exchange_info, mock_embeddings):

    """
    Verifies that if the AI Compliance Officer returns a violation, the trade is rejected.
    """
    async for session in get_session():
        await clear_db(session)
        # 1. Setup bot

        await upsert_config(session, "bot_running", "true")
        await upsert_config(session, "paper_trading", "true")
        await upsert_config(session, "strategy_profiles", '{"short_term": {"enabled": true, "min_confidence": 0.5}}')
        
        doc = KnowledgeDocument(
            title="XRP Ban",
            content="Never buy XRP due to regulatory uncertainty.",
            doc_type="logic_rule",
            horizon="short_term",
            status="approved",
            asset="XRPUSDT"
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        session.add(KnowledgeChunk(
            document_id=doc.id,
            chunk_index=0,
            text="Never buy XRP due to regulatory uncertainty.",
            embedding=json.dumps([0.1] * 1536) # Dummy embedding
        ))
        await session.commit()


        
        # 2. Mock AI to return a VIOLATION

        mock_ai_provider.ask = AsyncMock(return_value='{"compliant": false, "violation": "Restricted asset by regulator"}')

        rec = TradeRecommendation(
            symbol="XRPUSDT", action="BUY", current_price=0.5, suggested_allocation_usd=100.0,
            confidence=0.9, strategy="short_term", reasoning_summary="Moon mission", entry_conditions=[],
            risk_factors=[], stop_loss_pct=1, take_profit_pct=2, max_holding_period_minutes=10, should_execute=True
        )

        # 3. Check RiskEngine approval
        approved, reason = await risk_engine.approve(session, rec)
        
        assert approved is False
        assert "Institutional Violation" in reason
        assert "Restricted asset" in reason
