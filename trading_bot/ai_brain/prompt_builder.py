import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class TradingIndicators:
    sma_7: Optional[float]
    sma_25: Optional[float]
    rsi_14: Optional[float]
    current_price: float
    price_change_1h_pct: float
    # Macro indicators (Optional)
    btc_dominance: Optional[float] = None
    fear_and_greed: Optional[int] = None
    sentiment: Optional[str] = "Unknown"


def sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_indicators(ohlcv: list[dict]) -> TradingIndicators:
    closes = [float(c["close"]) for c in ohlcv]
    current_price = closes[-1] if closes else 0.0

    if len(ohlcv) >= 12:
        price_change_1h_pct = ((closes[-1] - closes[-12]) / closes[-12]) * 100
    else:
        price_change_1h_pct = 0.0

    return TradingIndicators(
        sma_7=sma(closes, 7),
        sma_25=sma(closes, 25),
        rsi_14=rsi(closes, 14),
        current_price=current_price,
        price_change_1h_pct=price_change_1h_pct,
    )


SYSTEM_PROMPT = """You are an expert cryptocurrency trading assistant. Your job is to analyze market data and decide whether to BUY, SELL, or HOLD a position.

STRICT OUTPUT FORMAT - you must return ONLY valid JSON, no explanations outside the JSON:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "symbol": "BTCUSDT",
  "quantity_usd": 50.0,
  "reason": "Brief explanation max 200 chars",
  "confidence": 0.0-1.0,
  "stop_loss_pct": 2.0,
  "take_profit_pct": 4.0
}}

RISK RULES:
- Never risk more than {max_trade_usd} USD per trade
- Maximum {max_open_trades} open positions at once
- Only trade allowed symbols: {allowed_symbols}
- Confidence must be at least {confidence_threshold} to execute

PAPER TRADING MODE: {paper_trading_msg}"""


def build(
    symbol: str,
    current_price: float,
    ohlcv: list[dict],
    positions: list[dict],
    balance_usd: float,
    last_decisions: list[dict],
    indicators: TradingIndicators,
    max_trade_usd: float,
    max_open_trades: int,
    allowed_symbols: list[str],
    confidence_threshold: float,
    paper_trading: bool,
) -> tuple[str, str]:
    system_prompt = SYSTEM_PROMPT.format(
        max_trade_usd=max_trade_usd,
        max_open_trades=max_open_trades,
        allowed_symbols=", ".join(allowed_symbols),
        confidence_threshold=confidence_threshold,
        paper_trading_msg="ACTIVE - No real money will be used" if paper_trading else "INACTIVE - REAL TRADING",
    )

    candles_table = "\n".join(
        f"{c['time']} | {c['open']} | {c['high']} | {c['low']} | {c['close']} | {c['volume']}"
        for c in ohlcv
    )

    positions_text = "\n".join(
        f"{p['symbol']}: {p['quantity']} @ ${p['avg_entry_price']} (unrealized PnL: ${p['unrealized_pnl']})"
        for p in positions
    ) or "No open positions"

    decisions_text = "\n".join(
        f"- {d['action']} {d.get('symbol', 'N/A')}: {d.get('reason', '')}"
        for d in last_decisions[-3:]
    ) or "No previous decisions"

    _sma7 = f"{indicators.sma_7:.2f}" if indicators.sma_7 is not None else "N/A"
    _sma25 = f"{indicators.sma_25:.2f}" if indicators.sma_25 is not None else "N/A"
    _rsi = f"{indicators.rsi_14:.2f}" if indicators.rsi_14 is not None else "N/A"
    indicators_text = f"""Current Price: ${indicators.current_price:.2f}
SMA-7: {_sma7}
SMA-25: {_sma25}
RSI-14: {_rsi}
1h Price Change: {indicators.price_change_1h_pct:.2f}%"""

    macro_text = ""
    if indicators.fear_and_greed is not None:
        macro_text = f"""
MARKET SENTIMENT (CoinMarketCap):
- Fear & Greed Index: {indicators.fear_and_greed} ({indicators.sentiment})
- BTC Dominance: {f"{indicators.btc_dominance:.1f}%" if indicators.btc_dominance else "N/A"}
"""

    user_prompt = f"""Current Symbol: {symbol}

PRICE DATA:
{indicators_text}
{macro_text}

LAST 20 CANDLES:
{candles_table}

CURRENT POSITIONS:
{positions_text}

AVAILABLE BALANCE: ${balance_usd:.2f}

RECENT DECISIONS:
{decisions_text}

Based on this data, should I BUY, SELL, or HOLD? Return your decision as JSON."""

    return system_prompt, user_prompt


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_json_payload(response: str) -> Any:
    decoder = json.JSONDecoder()
    if response is None:
        raise json.JSONDecodeError("Empty response", "", 0)
    text = response.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # LLMs often wrap the JSON in prose or fences. Try every object/array start
    # and return the first valid complete JSON payload.
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
            return payload
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError("No JSON payload found", text, 0)


def _normalize_scanner_item(item: dict, market_mood: str = "Neutral") -> Optional[dict]:
    symbol = item.get("symbol") or item.get("asset") or item.get("coin") or item.get("ticker")
    if not symbol:
        return None

    allocation = (
        item.get("suggested_allocation_usd")
        or item.get("allocation_usd")
        or item.get("allocation")
        or item.get("budget_usd")
        or item.get("amount_usd")
        or 0
    )
    return {
        "symbol": str(symbol).upper(),
        "suggested_allocation_usd": _to_float(allocation),
        "reason": item.get("reason") or item.get("narrative") or item.get("thesis") or "",
        "confidence": _to_float(item.get("confidence")),
        "sentiment": item.get("sentiment") or item.get("market_mood") or market_mood,
    }


def _normalize_scanner_response(data: Any) -> Optional[list[dict]]:
    if isinstance(data, list):
        items = data
        market_mood = "Neutral"
    elif isinstance(data, dict):
        market_mood = data.get("market_mood") or data.get("sentiment") or "Neutral"
        items = None
        for key in ("opportunities", "recommendations", "portfolio", "picks", "assets", "coins"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if items is None and ("symbol" in data or "asset" in data or "coin" in data or "ticker" in data):
            items = [data]
        if items is None:
            return None
    else:
        return None

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_item = _normalize_scanner_item(item, market_mood)
        if normalized_item:
            normalized.append(normalized_item)
    return normalized


def parse_response(response: str) -> Optional[dict]:
    try:
        data = _extract_json_payload(response)
        
        # Handle list responses (for scanner)
        if isinstance(data, list):
            return _normalize_scanner_response(data)

        # If it's a dict, handle it
        if isinstance(data, dict):
            # Special case: AI server errors (Ollama/OpenRouter)
            if "error" in data:
                return data

            required_fields = ["action", "symbol", "quantity_usd", "reason", "confidence"]
            # If it's a recommendation object (for scanner) it might have different fields
            if "action" not in data and "suggested_allocation_usd" in data:
                return _normalize_scanner_response(data)

            # Scanner-style structured object
            for key in ("opportunities", "recommendations", "portfolio", "picks", "assets", "coins"):
                if isinstance(data.get(key), list):
                    return _normalize_scanner_response(data)
                
            for field in required_fields:
                if field not in data:
                    return None

            if data["action"] not in ["BUY", "SELL", "HOLD"]:
                return None

            data["quantity_usd"] = _to_float(data["quantity_usd"])
            data["confidence"] = _to_float(data["confidence"])
            data["stop_loss_pct"] = _to_float(data.get("stop_loss_pct", 2.0), 2.0)
            data["take_profit_pct"] = _to_float(data.get("take_profit_pct", 4.0), 4.0)
            return data

        return None

    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        return None


SCANNER_MACRO_INSTR = """You are a high-level Macro Portfolio Strategist.
- Focus on Global Context: Use Fear & Greed and BTC Dominance to judge overall risk.
- Stability: Prioritize top-tier coins showing consistent volume and steady gains.
- Diversification: Spread the budget across 3-5 stable assets."""

SCANNER_SWING_INSTR = """You are a Swing Trading Specialist targeting 1-7 day moves.
- Momentum: Look for coins with strong 24H volume surges and clear trend direction.
- Catalyst-Driven: Prioritize coins with narrative momentum (ecosystem plays, protocol updates).
- Risk Management: Aim for 2:1 reward-to-risk; avoid coins with >5% bid/ask spread."""

SCANNER_HUSTLE_INSTR = """You are a Short-Term Hustle Trader targeting intraday to 48-hour setups.
- Speed: Focus on high-volatility coins with volume breakouts in the last few hours.
- Tight Risk: Small positions, quick entries; stop-loss within 1.5% of entry.
- Liquidity First: Only coins with >$5M daily volume to ensure fast fills."""

SCANNER_MOONSHOT_INSTR = """You are an aggressive Volatility Scalper.
- High Velocity: Focus on coins with high 24H volume spikes and extreme hourly volatility.
- Breakout Logic: Identify coins showing strong bullish breakouts despite higher risk.
- Concentration: You can suggest fewer, higher-conviction picks if a true 'moonshot' is found."""

_SCANNER_INSTR_MAP = {
    "macro": SCANNER_MACRO_INSTR,
    "swing": SCANNER_SWING_INSTR,
    "hustle": SCANNER_HUSTLE_INSTR,
    "moonshot": SCANNER_MOONSHOT_INSTR,
}

SCANNER_SYSTEM_PROMPT_TEMPLATE = """{strategy_instructions}

STRICT OUTPUT FORMAT - you must return ONLY a JSON ARRAY of recommendations. 
Individual trade objects are FORBIDDEN. You must return a list.

Example Output:
[
  {{
    "symbol": "SOLUSDT",
    "suggested_allocation_usd": 250.0,
    "sentiment": "Strong Bullish",
    "confidence": 0.85,
    "reason": "Top mover with strong RSI breakout."
  }},
  {{
    "symbol": "ETHUSDT",
    "suggested_allocation_usd": 150.0,
    "sentiment": "Bullish",
    "confidence": 0.72,
    "reason": "Layer 1 leader showing accumulation."
  }}
]

RULES:
- You MUST provide between 5 and 10 individual assets.
- Total suggested_allocation_usd must NOT exceed {total_budget} USD.
- Prioritize current market conditions according to your assigned persona.
- Avoid low liquidity coins below $1M daily volume."""


def build_portfolio_scanner_prompt(
    tickers: list[dict],
    total_budget: float,
    indicators: TradingIndicators,
    strategy_type: str = "macro",
    gainers: list[dict] = None,
    losers: list[dict] = None,
    trending: list[dict] = None,
    rag_context: str = "",
    positions_summary: str = ""
) -> tuple[str, str]:
    instr = _SCANNER_INSTR_MAP.get(strategy_type.lower(), SCANNER_MACRO_INSTR)
    system_prompt = SCANNER_SYSTEM_PROMPT_TEMPLATE.format(
        strategy_instructions=instr, 
        total_budget=total_budget
    )
    
    # Top movers summary
    gainers_text = ", ".join([f"{t['symbol']} (+{t['change']}%)" for t in (gainers or [])[:5]]) or "N/A"
    losers_text = ", ".join([f"{t['symbol']} ({t['change']}%)" for t in (losers or [])[:5]]) or "N/A"
    trending_text = ", ".join([str(t) for t in (trending or [])[:5]]) or "N/A"

    # Concise ticker table for context (Top 60 max to avoid token bloat)
    ticker_table = "\n".join(
        f"{t['symbol']}|${t['price']:.4f}|{t['change']}%|Vol:{t['volume']:.0f}"
        for t in tickers[:60]
    )
    
    # List of additional symbols to show we scanned 250
    other_symbols = ", ".join([t['symbol'] for t in tickers[100:250]])
    
    macro_context = ""
    if indicators.fear_and_greed is not None:
        macro_context = f"\nGLOBAL CONTEXT:\n- Fear & Greed Index: {indicators.fear_and_greed} ({indicators.sentiment})\n- BTC Dominance: {indicators.btc_dominance}%\n"

    rag_text = f"\nINTELLIGENCE FROM PAST TRADES & KNOWLEDGE BASE:\n{rag_context}\n" if rag_context else ""
    portfolio_text = f"\nCURRENT PORTFOLIO POSITIONS:\n{positions_summary}\n" if positions_summary else ""

    user_prompt = f"""{macro_context}{rag_text}{portfolio_text}
MARKET PULSE (Binance & CMC):
- Top Gainers: {gainers_text}
- Top Losers: {losers_text}
- Trending: {trending_text}

Here is the data for the Top 100 Market Movers (by volume):
SYMBOL | PRICE | 24H CHANGE | VOLUME
{ticker_table}

OTHER SYMBOLS SCANNED (250 Assets Total):
{other_symbols}

    TASK:
    Analyze these tickers, considering the global context, Market Heatmap (performance distribution), lessons from past trades, and current portfolio. 
    CRITICAL: You MUST provide between 5 and 10 individual portfolio recommendations (diversity is key!). 
    - Providing fewer than 5 is UNACCEPTABLE.
    - DO NOT output a single "PORTFOLIO", "BASKET", or "INDEX" symbol. You must list actual individual assets (e.g., BTCUSDT, SOLUSDT).
    - If you see a strong narrative, pick the top 3 assets within that narrative.
    Stay within the ${total_budget} budget. 

    Return ONLY the JSON array. Do not include any text outside the JSON. """

    return system_prompt, user_prompt
