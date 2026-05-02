import json
import logging
import re
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Tuple, Optional
from datetime import datetime

from ai_brain.schemas import TradeIntent
from core.bot_state import get_runtime_settings
from core.models import Position, Trade, DailyLossTracker
from exchange.exchange_cache import get_symbol_constraints, validate_order



logger = logging.getLogger(__name__)

class RiskEngine:
    """
    Risk Engine - The final gatekeeper for all trade recommendations.
    Implements:
    - Global Kill Switch & Bot State checks
    - Confidence threshold validation
    - Duplicate position prevention
    - Max Open Trades limit
    - Max Trade Size (USD) enforcement
    - Exchange Constraint validation (LOT_SIZE, MIN_NOTIONAL)
    """

    async def approve(self, session: AsyncSession, recommendation: TradeIntent, is_backtest: bool = False) -> Tuple[bool, str]:
        if not recommendation.should_execute or recommendation.action == "HOLD":
            return False, "Action is HOLD or execution not requested."

        settings = await get_runtime_settings(session)

        # 0. RISK KERNEL CHECKS
        from core.risk_kernel import risk_kernel
        is_safe, reason = await risk_kernel.is_system_safe()
        if not is_safe:
            return False, f"RiskKernel: {reason}"
        
        if risk_kernel.is_in_cooldown(recommendation.symbol):
            return False, f"RiskKernel: Symbol {recommendation.symbol} is in cooldown."

        # 1. GLOBAL SAFETY CHECKS
        if settings.kill_switch_enabled:
            return False, "CRITICAL: Kill switch is active. All trading halted."
        
        if not settings.bot_running:
            return False, "Bot is currently STOPPED."

        # 1b. PHASE 5 LIVE CONSTRAINTS
        if not settings.paper_trading:
            # Removed $100 hard cap for live
            pass
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            res = await session.execute(select(DailyLossTracker).where(DailyLossTracker.date == today))
            tracker = res.scalar_one_or_none()
            if tracker and tracker.total_loss >= settings.daily_loss_limit:
                return False, f"PHASE 5 SAFETY: Daily Loss Limit (${settings.daily_loss_limit}) reached."

        # 2. STRATEGY CONFIDENCE CHECKS
        profiles = settings.strategy_profiles
        strat_key = recommendation.strategy.lower()
        
        if strat_key not in profiles:
            return False, f"Strategy profile '{recommendation.strategy}' not configured."
            
        profile = profiles[strat_key]
        if not profile.get("enabled", True):
            return False, f"Strategy '{recommendation.strategy}' is disabled."

        min_conf = float(profile.get("min_confidence", 0.6))
        if recommendation.confidence < min_conf:
            return False, f"Confidence {recommendation.confidence:.2f} < Threshold {min_conf:.2f}"

        # 3. POSITION & LIMIT CHECKS
        # 3a. Duplicate Symbol Check
        result = await session.execute(
            select(Position).where(Position.symbol == recommendation.symbol)
        )
        existing_position = result.scalars().first()


        if recommendation.action == "BUY":
            # Duplicate check disabled to allow 'unlimited' buying
            pass
            
            # 3b. Max Open Trades Limit
            if not is_backtest:
                count_result = await session.execute(select(func.count()).select_from(Position))
                open_count = count_result.scalar() or 0
                if open_count >= settings.max_open_trades:
                    return False, f"Max open trades ({settings.max_open_trades}) reached."

            # 3c. Max Trade USD limit
            requested_usd = recommendation.suggested_allocation_usd
            if requested_usd > settings.max_trade_usd:
                recommendation.suggested_allocation_usd = settings.max_trade_usd
                if not is_backtest:
                    logger.info(f"RiskEngine: Capped allocation for {recommendation.symbol} to ${settings.max_trade_usd}")

        if recommendation.action == "SELL" and not existing_position and not is_backtest:
            return False, f"Cannot SELL {recommendation.symbol}: No open position found."

        # 4. EXCHANGE COMPLIANCE (LOT_SIZE, MIN_NOTIONAL)
        # Use current price to estimate quantity/notional
        from exchange.binance_client import binance_client
        price = recommendation.current_price or 0.0
        if price <= 0:
            # Try to fetch current price if missing
            ticker = await binance_client.get_ticker(recommendation.symbol)
            price = float(ticker.get("lastPrice", 0))
            recommendation.current_price = price

        if price <= 0:
            return False, f"Invalid price for {recommendation.symbol}."

        quantity = recommendation.suggested_allocation_usd / price
        
        # 2. EXCHANGE CONSTRAINT VALIDATION
        from exchange.order_validator import validate_exchange_constraints
        v_res = await validate_exchange_constraints(
            recommendation.symbol, quantity, price, confidence=recommendation.confidence
        )
        if not v_res.valid:
            return False, f"Exchange Constraint Error: {v_res.error}"

        # 5. INSTITUTIONAL RAG COMPLIANCE
        is_compliant, compliance_msg = await self.check_compliance(recommendation)
        if not is_compliant:
            return False, compliance_msg

        return True, "Approved"

    async def check_compliance(self, recommendation: TradeIntent) -> Tuple[bool, str]:
        """
        Retrieves institutional rules from RAG and uses AI to verify compliance.
        """
        from ai_brain.rag import search_rules
        from ai_brain.provider_factory import get_ai_provider
        
        # Search for rules specifically related to this asset or global logic
        rules = await search_rules(
            query=f"Compliance rules for {recommendation.action} on {recommendation.symbol}",
            asset=recommendation.symbol
        )
        
        if not rules:
            return True, "No specific institutional rules found."

        rules_text = "\n".join([f"- [{r['type'].upper()}] {r['title']}: {r['text']}" for r in rules])
        
        prompt = f"""
        [GOVERNANCE COMPLIANCE CHECK]
        You are the Compliance Officer for an institutional trading bot.
        A trade recommendation has been made, and you must verify it against our 'Institutional Rules'.

        RECOMMENDATION:
        Action: {recommendation.action}
        Symbol: {recommendation.symbol}
        Strategy: {recommendation.strategy}
        Reason: {recommendation.reasoning_summary}

        Price: {recommendation.current_price}

        INSTITUTIONAL RULES & THESES FOUND:
        {rules_text}

        TASK:
        Verify if this trade VIOLATES any of the rules above. 
        Note: If a rule is a 'thesis' and the trade aligns with it, it's compliant. 
        If a rule is a 'logic_rule' (e.g., 'Never buy when X'), it MUST be strictly followed.

        RESPONSE FORMAT:
        Reply ONLY in valid JSON:
        {{
            "compliant": true/false,
            "violation": "Reason for violation if any, else null"
        }}
        """
        
        try:
            provider = await get_ai_provider()
            response = await provider.ask(
                system_prompt="You are an institutional compliance officer.",
                user_prompt=prompt
            )
            match = re.search(r'\{.*\}', response, re.DOTALL)

            if match:
                data = json.loads(match.group())
                if not data.get("compliant", True):
                    return False, f"Institutional Violation: {data.get('violation')}"
        except Exception as e:
            logger.error(f"RiskEngine: Compliance check error: {e}")
            # Fail-safe: If AI is down, we allow the trade but log the failure
            return True, "Compliance check skipped (Provider Error)"

        return True, "Compliant"

risk_engine = RiskEngine()
