from typing import Optional
import asyncio
from datetime import datetime, timezone

from binance.client import Client
from binance.exceptions import BinanceAPIException

from core.security import get_credential, has_credential


class CredentialsNotConfiguredError(Exception):
    pass


class BinanceClient:
    def __init__(self):
        self._client: Optional[Client] = None
        self._api_key: Optional[str] = None
        self._api_secret: Optional[str] = None

    def _ensure_credentials(self):
        if not self._api_key or not self._api_secret:
            self._api_key = get_credential("BINANCE_API_KEY")
            self._api_secret = get_credential("BINANCE_API_SECRET")
        if not self._api_key or not self._api_secret:
            raise CredentialsNotConfiguredError("Binance credentials not configured")

    def _get_client(self, authenticated: bool = True) -> Client:
        if authenticated:
            self._ensure_credentials()
            if self._client is None:
                self._client = Client(self._api_key, self._api_secret)
            return self._client
        else:
            # Public client (no keys)
            return Client("", "")

    async def get_ticker_price(self, symbol: str) -> float:
        loop = asyncio.get_event_loop()
        # Public data doesn't need auth
        client = self._get_client(authenticated=False)
        price = await loop.run_in_executor(
            None, lambda: client.get_symbol_ticker(symbol=symbol)
        )
        return float(price["price"])

    async def get_ohlcv(
        self, symbol: str, interval: str = "5m", limit: int = 20
    ) -> list[dict]:
        loop = asyncio.get_event_loop()
        client = self._get_client(authenticated=False)
        klines = await loop.run_in_executor(
            None, lambda: client.get_klines(symbol=symbol, interval=interval, limit=limit)
        )
        return [
            {
                "time": k[0] // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in klines
        ]

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[list]:
        """
        Return raw historical klines for a symbol in a datetime range.
        This keeps compatibility with services that expect Binance's native
        kline array shape.
        """
        loop = asyncio.get_event_loop()
        client = self._get_client(authenticated=False)

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        klines = await loop.run_in_executor(
            None,
            lambda: client.get_historical_klines(
                symbol=symbol,
                interval=interval,
                start_str=start_ms,
                end_str=end_ms,
            ),
        )
        return klines

    async def get_account_balances(self) -> dict[str, float]:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        account = await loop.run_in_executor(None, client.get_account)
        balances = {}
        for balance in account["balances"]:
            free = float(balance["free"])
            locked = float(balance["locked"])
            if free + locked > 0:
                balances[balance["asset"]] = free + locked
        return balances

    async def get_spot_balances(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        account = await loop.run_in_executor(None, client.get_account)
        balances = []
        # Get prices for valuation
        all_prices = {}
        try:
            tickers = await self.get_all_tickers()
            all_prices = {t["symbol"]: t["price"] for t in tickers}
        except:
            pass

        for balance in account["balances"]:
            free = float(balance["free"])
            locked = float(balance["locked"])
            total = free + locked
            if total > 0:
                asset = balance["asset"]
                usd_val = 0.0
                if asset == "USDT":
                    usd_val = total
                else:
                    price = all_prices.get(f"{asset}USDT", 0.0)
                    usd_val = total * price
                
                balances.append({
                    "asset": asset,
                    "free": free,
                    "locked": locked,
                    "total": total,
                    "usd_value": usd_val
                })
        return balances

    async def get_funding_balances(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        try:
            funding = await loop.run_in_executor(None, client.get_funding_balance)
            balances = []
            all_prices = {}
            try:
                tickers = await self.get_all_tickers()
                all_prices = {t["symbol"]: t["price"] for t in tickers}
            except:
                pass

            for item in funding:
                free = float(item.get("free", 0))
                locked = float(item.get("locked", 0))
                total = free + locked
                if total > 0:
                    asset = item.get("asset", "")
                    usd_val = 0.0
                    if asset == "USDT":
                        usd_val = total
                    else:
                        price = all_prices.get(f"{asset}USDT", 0.0)
                        usd_val = total * price

                    balances.append({
                        "asset": asset,
                        "free": free,
                        "locked": locked,
                        "total": total,
                        "usd_value": usd_val
                    })
            return balances
        except Exception:
            return []

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        orders = await loop.run_in_executor(
            None, lambda: client.get_open_orders(symbol=symbol) if symbol else client.get_open_orders()
        )
        return [
            {
                "symbol": o["symbol"],
                "orderId": o["orderId"],
                "side": o["side"],
                "type": o["type"],
                "origQty": float(o["origQty"]),
                "price": float(o["price"]),
            }
            for o in orders
        ]

    async def place_market_order(
        self, symbol: str, side: str, quantity: float
    ) -> dict:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        order = await loop.run_in_executor(
            None,
            lambda: client.order_market_symbol(
                symbol=symbol, side=side, quantity=quantity
            ),
        )
        return order

    async def cancel_order(self, symbol: str, order_id: int) -> dict:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        result = await loop.run_in_executor(
            None, lambda: client.cancel_order(symbol=symbol, orderId=order_id)
        )
        return result

    async def get_all_tickers(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        tickers = await loop.run_in_executor(None, client.get_all_tickers)
        return tickers

    async def get_symbol_info(self, symbol: str) -> dict:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        info = await loop.run_in_executor(None, client.get_symbol_info, symbol)
        filters = {f["filterType"]: f for f in info["filters"]}
        return {
            "min_qty": float(filters.get("LOT_SIZE", {}).get("minQty", "0")),
            "step_size": float(filters.get("LOT_SIZE", {}).get("stepSize", "0.01")),
            "min_notional": float(
                filters.get("NOTIONAL", {}).get("minNotional", "0.01")
            ),
        }

    async def get_all_tickers(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        client = self._get_client(authenticated=False)
        tickers = await loop.run_in_executor(None, client.get_ticker)
        return [
            {
                "symbol": t["symbol"],
                "price": float(t["lastPrice"]),
                "change": t.get("priceChangePercent", "0"),
                "volume": float(t.get("quoteVolume", 0))
            }
            for t in tickers
            if t["symbol"].endswith("USDT") and float(t.get("quoteVolume", 0)) > 0
        ][:1000]

    async def test_connection(self) -> dict:
        loop = asyncio.get_event_loop()
        client = self._get_client()
        account = await loop.run_in_executor(None, client.get_account)
        return {
            "connected": True,
            "account_type": account.get("accountType", "SPOT"),
            "balances": [
                {"asset": b["asset"], "free": float(b["free"]), "locked": float(b["locked"])}
                for b in account["balances"]
                if float(b["free"]) > 0 or float(b["locked"]) > 0
            ],
        }


binance_client = BinanceClient()


def is_configured() -> bool:
    return has_credential("BINANCE_API_KEY") and has_credential("BINANCE_API_SECRET")
