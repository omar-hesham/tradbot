import asyncio
import logging
from typing import Dict, Optional, List
from binance import AsyncClient, BinanceSocketManager
from core.security import get_credential, has_credential

logger = logging.getLogger(__name__)

class BinanceDataStream:
    """
    Binance Data Stream - Handles real-time WebSocket updates for:
    1. Prices (AggTrade) for tracked symbols.
    2. User Data (Order execution reports).
    """
    def __init__(self):
        self._price_cache: Dict[str, float] = {}
        self._client: Optional[AsyncClient] = None
        self._bm: Optional[BinanceSocketManager] = None
        self._stop_event = asyncio.Event()
        self._symbols: List[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"] # Default

    async def update_symbols(self, symbols: List[str]):
        """Updates the list of symbols to track."""
        self._symbols = list(set(symbols + ["BTCUSDT"])) # Always track BTC
        if self._active():
            # Restart stream to include new symbols
            await self.stop()
            await self.start()

    def _active(self) -> bool:
        return self._client is not None

    async def start(self):
        if not has_credential("BINANCE_API_KEY"):
            logger.warning("Binance credentials not found. DataStream skipped.")
            return

        api_key = get_credential("BINANCE_API_KEY")
        api_secret = get_credential("BINANCE_API_SECRET")

        try:
            self._client = await AsyncClient.create(api_key, api_secret)
            self._bm = BinanceSocketManager(self._client)
            self._stop_event.clear()

            # Start streams
            asyncio.create_task(self._run_price_stream())
            asyncio.create_task(self._run_user_data_stream())
            
            logger.info(f"Binance DataStream started for symbols: {self._symbols}")
        except Exception as e:
            logger.error(f"Failed to start Binance DataStream: {e}")

    async def _run_price_stream(self):
        # We use a combined multiplex stream for efficiency
        streams = [f"{s.lower()}@aggTrade" for s in self._symbols]
        ms = self._bm.multiplex_socket(streams)
        
        try:
            async with ms as mscm:
                while not self._stop_event.is_set():
                    res = await mscm.recv()
                    if res and 'data' in res:
                        data = res['data']
                        symbol = data['s']
                        price = float(data['p'])
                        self._price_cache[symbol] = price
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"Price stream error: {e}")
                await asyncio.sleep(5)
                asyncio.create_task(self._run_price_stream())

    async def _run_user_data_stream(self):
        us = self._bm.user_socket()
        try:
            async with us as uscm:
                while not self._stop_event.is_set():
                    res = await uscm.recv()
                    if res:
                        event_type = res.get('e')
                        if event_type == 'executionReport':
                            logger.info(f"REAL-TIME ORDER UPDATE: {res['s']} {res['S']} {res['X']} status={res['X']} price={res['L']} qty={res['l']}")
                        elif event_type == 'outboundAccountPosition':
                            logger.info("REAL-TIME BALANCE UPDATE")
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"User data stream error: {e}")
                await asyncio.sleep(5)
                asyncio.create_task(self._run_user_data_stream())

    def get_price(self, symbol: str) -> Optional[float]:
        return self._price_cache.get(symbol)

    async def stop(self):
        self._stop_event.set()
        if self._client:
            await self._client.close_connection()
            self._client = None
            self._bm = None

data_stream = BinanceDataStream()
