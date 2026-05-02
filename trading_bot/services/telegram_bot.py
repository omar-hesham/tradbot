import logging
import httpx
import asyncio
from config.settings import get_settings
from trading.portfolio import get_portfolio

logger = logging.getLogger(__name__)

async def send_telegram_message(message: str):
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return
        
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code != 200:
                logger.warning(f"Telegram alert failed: {response.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")

async def telegram_polling():
    """
    Background task to poll for Telegram commands (e.g., /status).
    """
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.info("Telegram polling skipped: TELEGRAM_BOT_TOKEN not set.")
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getUpdates"
    offset = None

    logger.info("Started Telegram command polling.")

    async with httpx.AsyncClient() as client:
        while True:
            try:
                params = {"timeout": 30}
                if offset:
                    params["offset"] = offset

                response = await client.get(url, params=params, timeout=40.0)
                if response.status_code == 200:
                    data = response.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        
                        message = update.get("message")
                        if not message or "text" not in message:
                            continue
                            
                        chat_id = str(message["chat"]["id"])
                        
                        # Only respond to the authorized chat ID
                        if settings.TELEGRAM_CHAT_ID and chat_id != settings.TELEGRAM_CHAT_ID:
                            continue
                            
                        text = message["text"].strip()
                        
                        if text == "/status":
                            # Fetch portfolio
                            portfolio = await get_portfolio()
                            status_msg = (
                                f"🤖 <b>Bot Status</b>\n\n"
                                f"<b>Portfolio Value:</b> ${portfolio.total_value_usd:.2f}\n"
                                f"<b>Unrealized PnL:</b> ${portfolio.unrealized_pnl:+.2f}\n"
                                f"<b>Open Positions:</b> {len(portfolio.positions)}\n"
                                f"<b>Mode:</b> {'Paper Trading' if settings.PAPER_TRADING else 'Live Trading'}"
                            )
                            await send_telegram_message(status_msg)
                            
                        elif text == "/ping":
                            await send_telegram_message("🏓 Pong! Bot is active and running.")
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(1)
