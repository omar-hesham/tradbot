import asyncio
import sys
import os

import pytest

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_brain.prompt_builder import TradingIndicators, build
from exchange.cmc_client import CMCClient

@pytest.mark.asyncio
async def test_macro_prompt():
    print("Testing Prompt Builder with Macro Data...")
    
    indicators = TradingIndicators(
        sma_7=105.0,
        sma_25=102.0,
        rsi_14=45.0,
        current_price=104.5,
        price_change_1h_pct=1.2,
        btc_dominance=52.5,
        fear_and_greed=75,
        sentiment="Greed"
    )
    
    system_prompt, user_prompt = build(
        symbol="BTCUSDT",
        current_price=104.5,
        ohlcv=[],
        positions=[],
        balance_usd=1000.0,
        last_decisions=[],
        indicators=indicators,
        max_trade_usd=100.0,
        max_open_trades=3,
        allowed_symbols=["BTCUSDT"],
        confidence_threshold=0.6,
        paper_trading=True
    )
    
    print("\n--- USER PROMPT ---")
    print(user_prompt)
    
    if "MARKET SENTIMENT (CoinMarketCap)" in user_prompt:
        print("\n[OK] SUCCESS: Macro data found in prompt.")
        if "Fear & Greed Index: 75 (Greed)" in user_prompt and "BTC Dominance: 52.5%" in user_prompt:
            print("[OK] SUCCESS: Specific values parsed correctly.")
        else:
            print("[FAIL] FAILURE: Specific values NOT matched.")
    else:
        print("\n[FAIL] FAILURE: Macro data section NOT found in prompt.")

if __name__ == "__main__":
    asyncio.run(test_macro_prompt())
