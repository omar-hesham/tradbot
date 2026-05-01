from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Set
import asyncio
import json


class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._price_task: asyncio.Task = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

    async def start_price_stream(self):
        from exchange.binance_client import binance_client, is_configured

        while self.active_connections:
            try:
                if is_configured():
                    tickers = await binance_client.get_all_tickers()
                    for ticker in tickers[:10]:
                        await self.broadcast({
                            "type": "price",
                            "symbol": ticker["symbol"],
                            "price": ticker["price"],
                        })
                else:
                    await self.broadcast({
                        "type": "price",
                        "symbol": "BTCUSDT",
                        "price": 0.0,
                        "note": "Binance not configured",
                    })
            except Exception:
                pass

            await asyncio.sleep(5)

    async def send_bot_status(self):
        from trading.engine import get_bot_running
        from trading.strategy import get_strategy

        running = await get_bot_running()
        strategy = await get_strategy()
        mode = "PAPER" if strategy.paper_trading else "LIVE"

        await self.broadcast({
            "type": "bot_status",
            "running": running,
            "mode": mode,
        })

    async def broadcast_alert(self, message: str, level: str = "info", symbol: str = None, pnl: float = None):
        """Broadcasts a toast-style alert to all connected dashboard clients."""
        payload = {"type": "alert", "message": message, "level": level}
        if symbol:
            payload["symbol"] = symbol
        if pnl is not None:
            payload["pnl"] = round(pnl, 2)
        await self.broadcast(payload)


manager = ConnectionManager()

router = APIRouter(prefix="/ws", tags=["websocket"])


@router.websocket("/prices")
async def websocket_prices(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                data = json.loads(message) if message else {}
                if data.get("action") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Send a heartbeat so the connection stays alive through proxies
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)