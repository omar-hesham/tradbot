"""
Analytics API — aggregates Binance + CMC data for the Analytics Hub dashboard.
CMC data is cached for 60s to avoid rate limits; Binance data cached for 5s.
"""
import asyncio
import time
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from exchange.binance_client import binance_client, is_configured
from exchange.cmc_client import CMCClient
from config.settings import get_settings
from core.security import get_credential
from core.database import get_session
from core.models import Trade, Position, ScannedAsset
from ai_brain.ai_runtime import manual_ai_session
from ai_brain.rag import ingest_trade_lessons

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

# ── Server-side caches ──
_cmc_cache = {"data": {}, "timestamp": 0, "ttl": 60}     # 60s
_binance_cache = {"data": {}, "timestamp": 0, "ttl": 5}   # 5s


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _process_binance_tickers(tickers):
    """Process raw Binance tickers into gainers/losers/volume/stats."""
    usdt_tickers = [
        t for t in tickers
        if t["symbol"].endswith("USDT") and _safe_float(t.get("volume", 0)) > 0
    ]

    gainers = sorted(usdt_tickers, key=lambda x: _safe_float(x.get("change", 0)), reverse=True)
    losers = sorted(usdt_tickers, key=lambda x: _safe_float(x.get("change", 0)))
    vol_sorted = sorted(usdt_tickers, key=lambda x: _safe_float(x.get("volume", 0)), reverse=True)

    def _to_list(items, limit=10):
        return [
            {
                "symbol": t["symbol"],
                "price": _safe_float(t["price"]),
                "change": _safe_float(t.get("change", 0)),
                "volume": _safe_float(t.get("volume", 0)),
            }
            for t in items[:limit]
        ]

    return {
        "top_gainers": _to_list(gainers),
        "top_losers": _to_list(losers),
        "volume_leaders": _to_list(vol_sorted),
        "binance_stats": {
            "total_pairs": len(usdt_tickers),
            "avg_change": round(
                sum(_safe_float(t.get("change", 0)) for t in usdt_tickers) / max(len(usdt_tickers), 1), 2
            ),
            "total_volume": round(sum(_safe_float(t.get("volume", 0)) for t in usdt_tickers), 0),
            "green_count": sum(1 for t in usdt_tickers if _safe_float(t.get("change", 0)) > 0),
            "red_count": sum(1 for t in usdt_tickers if _safe_float(t.get("change", 0)) < 0),
        },
    }


@router.get("/overview")
async def analytics_overview():
    """
    Returns a comprehensive analytics snapshot combining Binance market
    data with CoinMarketCap global metrics, fear & greed, trending coins,
    and top-movers.  Both data sources are cached server-side.
    """
    result = {
        "global_metrics": {},
        "fear_greed": {},
        "fear_greed_history": [],
        "trending": [],
        "top_cryptos": [],
        "top_gainers": [],
        "top_losers": [],
        "volume_leaders": [],
        "binance_stats": {},
    }

    now = time.time()

    # ── CMC Data (cached 60s) ──
    cmc_fresh = (now - _cmc_cache["timestamp"]) < _cmc_cache["ttl"]
    if cmc_fresh and _cmc_cache["data"]:
        for k, v in _cmc_cache["data"].items():
            result[k] = v
    else:
        settings = get_settings()
        cmc_key = get_credential("CMC_API_KEY") or settings.CMC_API_KEY
        if cmc_key:
            try:
                cmc = CMCClient(api_key=cmc_key)
                tasks = {
                    "global_metrics": cmc.get_global_metrics(),
                    "fear_greed": cmc.get_fear_and_greed(),
                    "fear_greed_history": cmc.get_fear_and_greed_history(30),
                    "top_cryptos": cmc.get_top_cryptos(25),
                    "trending": cmc.get_trending(),
                }
                cmc_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
                keys = list(tasks.keys())
                cmc_data = {}
                for i, key in enumerate(keys):
                    if isinstance(cmc_results[i], Exception):
                        logger.warning(f"CMC {key} failed: {cmc_results[i]}")
                        continue
                    result[key] = cmc_results[i]
                    cmc_data[key] = cmc_results[i]
                _cmc_cache["data"] = cmc_data
                _cmc_cache["timestamp"] = now
            except Exception as e:
                logger.error(f"CMC fetch failed: {e}")

    # ── Binance Data (cached 5s) ──
    binance_fresh = (now - _binance_cache["timestamp"]) < _binance_cache["ttl"]
    if binance_fresh and _binance_cache["data"]:
        for k, v in _binance_cache["data"].items():
            result[k] = v
    elif is_configured():
        try:
            tickers = await binance_client.get_all_tickers()
            binance_data = _process_binance_tickers(tickers)
            for k, v in binance_data.items():
                result[k] = v
            _binance_cache["data"] = binance_data
            _binance_cache["timestamp"] = now
        except Exception as e:
            logger.error(f"Binance analytics fetch failed: {e}")
            # Serve stale cache if available
            if _binance_cache["data"]:
                for k, v in _binance_cache["data"].items():
                    result[k] = v

    return result


@router.get("/pnl")
async def get_pnl_analytics(session: AsyncSession = Depends(get_session)):
    """Calculates cumulative realized PnL curve for charting."""
    result = await session.execute(
        select(Trade).where(Trade.realized_pnl != None).order_by(Trade.created_at)
    )
    trades = result.scalars().all()
    
    curve = []
    cumulative = 0.0
    for t in trades:
        cumulative += t.realized_pnl
        curve.append({
            "t": t.created_at.strftime("%Y-%m-%d %H:%M"),
            "y": round(cumulative, 2)
        })
        
    win_count = sum(1 for t in trades if t.realized_pnl > 0)
    loss_count = sum(1 for t in trades if t.realized_pnl <= 0)
    
    return {
        "curve": curve,
        "summary": {
            "total_realized_pnl": round(cumulative, 2),
            "win_rate": round(win_count / max(len(trades), 1) * 100, 1),
            "trade_count": len(trades),
            "win_count": win_count,
            "loss_count": loss_count
        }
    }


@router.get("/scanner")
async def get_scanned_assets(session: AsyncSession = Depends(get_session)):
    """Returns the most recent market scanner results."""
    result = await session.execute(
        select(ScannedAsset).order_by(ScannedAsset.score.desc()).limit(20)
    )
    assets = result.scalars().all()
    return assets
class BacktestRequest(BaseModel):
    symbols: List[str]
    days: int = 7
    interval: str = "1h"
    capital: float = 1000.0
    use_ai: bool = False

@router.post("/backtest")
async def run_historical_backtest(req: BacktestRequest):
    """
    Triggers a historical replay simulation.
    """
    from services.backtest_engine import backtest_engine
    from datetime import datetime, timedelta
    
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=req.days)
    
    try:
        # Backtests are manual user actions; serialize AI calls and pause auto agents.
        async with manual_ai_session(reason="manual backtest", pause_seconds=180):
            result = await backtest_engine.run(
                symbols=req.symbols,
                start_date=start_date,
                end_date=end_date,
                initial_capital=req.capital,
                interval=req.interval,
                use_ai=req.use_ai
            )
        return result
    except Exception as e:
        logger.error(f"Backtest execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/learn")
async def trigger_rag_learning():
    """
    Triggers the AI to analyze past trades and ingest lessons into RAG.
    """
    try:
        msg = await ingest_trade_lessons()
        return {"success": True, "message": msg}
    except Exception as e:
        logger.exception("RAG learning failed")
        raise HTTPException(status_code=500, detail=str(e))
