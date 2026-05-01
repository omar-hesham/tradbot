import logging
import httpx
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class CMCClient:
    """
    Client for CoinMarketCap Pro API.
    Provides global market metrics, trending coins, and sentiment data.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://pro-api.coinmarketcap.com"
        self.v3_url = "https://api.coinmarketcap.com"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-CMC_PRO_API_KEY": self.api_key or "",
            "Accept": "application/json"
        }

    async def get_fear_and_greed(self) -> Dict[str, Any]:
        """Fetches the latest CMC Crypto Fear and Greed Index via Pro API v3."""
        if not self.api_key:
            return {"value": None, "value_classification": "Unknown"}

        try:
            url = f"{self.base_url}/v3/fear-and-greed/latest"
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                result = data.get("data", {})
                return {
                    "value": result.get("value"),
                    "value_classification": result.get("value_classification"),
                    "update_time": result.get("update_time")
                }
        except Exception as e:
            logger.error(f"Failed to fetch Fear & Greed: {e}")
            return {"value": None, "value_classification": "Unknown", "error": str(e)}

    async def get_fear_and_greed_history(self, limit: int = 30) -> List[Dict]:
        """Fetches Fear & Greed history for charting via Pro API v3."""
        if not self.api_key:
            return []
        try:
            url = f"{self.base_url}/v3/fear-and-greed/historical"
            params = {"limit": limit}
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                points = data.get("data", [])
                return points[-limit:] if isinstance(points, list) else []
        except Exception as e:
            logger.error(f"Failed to fetch F&G history: {e}")
            return []

    async def get_global_metrics(self) -> Dict[str, Any]:
        """Fetches global market quotes (Total MCAP, BTC Dominance, etc.)."""
        if not self.api_key:
            return {"btc_dominance": None, "total_market_cap": None}

        try:
            url = f"{self.base_url}/v1/global-metrics/quotes/latest"
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                result = data.get("data", {})
                usd = result.get("quote", {}).get("USD", {})
                return {
                    "btc_dominance": result.get("btc_dominance"),
                    "eth_dominance": result.get("eth_dominance"),
                    "active_cryptocurrencies": result.get("active_cryptocurrencies"),
                    "active_exchanges": result.get("active_exchanges"),
                    "total_market_cap": usd.get("total_market_cap"),
                    "total_volume_24h": usd.get("total_volume_24h"),
                    "total_market_cap_yesterday_pct_change": usd.get("total_market_cap_yesterday_percentage_change"),
                    "total_volume_24h_yesterday_pct_change": usd.get("total_volume_24h_yesterday_percentage_change"),
                    "defi_volume_24h": result.get("defi_volume_24h"),
                    "defi_market_cap": result.get("defi_market_cap"),
                    "stablecoin_volume_24h": result.get("stablecoin_volume_24h"),
                    "stablecoin_market_cap": result.get("stablecoin_market_cap"),
                }
        except Exception as e:
            logger.error(f"Failed to fetch Global Metrics: {e}")
            return {"btc_dominance": None, "total_market_cap": None, "error": str(e)}

    async def get_top_cryptos(self, limit: int = 20) -> List[Dict]:
        """Fetches top cryptocurrencies by market cap via CMC /v1/cryptocurrency/listings/latest."""
        if not self.api_key:
            return []
        try:
            url = f"{self.base_url}/v1/cryptocurrency/listings/latest"
            params = {"limit": limit, "convert": "USD", "sort": "market_cap"}
            async with httpx.AsyncClient(headers=self._get_headers()) as client:
                response = await client.get(url, params=params, timeout=15.0)
                response.raise_for_status()
                data = response.json()
                coins = data.get("data", [])
                return [
                    {
                        "name": c.get("name"),
                        "symbol": c.get("symbol"),
                        "cmc_rank": c.get("cmc_rank"),
                        "price": c.get("quote", {}).get("USD", {}).get("price"),
                        "market_cap": c.get("quote", {}).get("USD", {}).get("market_cap"),
                        "volume_24h": c.get("quote", {}).get("USD", {}).get("volume_24h"),
                        "change_1h": c.get("quote", {}).get("USD", {}).get("percent_change_1h"),
                        "change_24h": c.get("quote", {}).get("USD", {}).get("percent_change_24h"),
                        "change_7d": c.get("quote", {}).get("USD", {}).get("percent_change_7d"),
                        "change_30d": c.get("quote", {}).get("USD", {}).get("percent_change_30d"),
                        "market_cap_dominance": c.get("quote", {}).get("USD", {}).get("market_cap_dominance"),
                    }
                    for c in coins
                ]
        except Exception as e:
            logger.error(f"Failed to fetch top cryptos: {e}")
            return []

    async def get_trending(self) -> List[Dict]:
        """Fetches CMC trending coins via the public data-api."""
        try:
            url = f"{self.v3_url}/data-api/v3/topsearch/rank"
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                items = data.get("data", {}).get("cryptoTopSearchRanks", [])
                return [
                    {
                        "name": c.get("name"),
                        "symbol": c.get("symbol"),
                        "rank": i + 1,
                        "price": c.get("priceChange", {}).get("price"),
                        "change_24h": c.get("priceChange", {}).get("priceChange24h"),
                    }
                    for i, c in enumerate(items[:15])
                ]
        except Exception as e:
            logger.error(f"Failed to fetch trending: {e}")
            return []


# Singleton instance placeholder
cmc_client = CMCClient()
