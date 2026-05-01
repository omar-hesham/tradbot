import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from sqlalchemy import delete, select

from ai_brain.rag import ingest_document
from config.settings import get_settings
from core.database import get_session
from core.models import BotConfig, KnowledgeChunk, KnowledgeDocument
from core.security import get_credential
from exchange.cmc_client import CMCClient

logger = logging.getLogger(__name__)

BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
BINANCE_LAUNCHPOOL_URL = "https://www.binance.com/bapi/earn/v1/public/launchpool/project/list"
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+USDT$")

CFG_LAST_REFRESH = "rag_reliable_last_refresh_utc"
CFG_USDT_SYMBOLS = "rag_reliable_binance_usdt_symbols_json"
CFG_LAST_ADDED = "rag_reliable_binance_last_added_json"
CFG_LAST_REMOVED = "rag_reliable_binance_last_removed_json"
CFG_LAUNCHPOOL_CANDIDATES = "rag_reliable_binance_launchpool_candidates_json"
CFG_LAUNCHPOOL_LAST_ADDED = "rag_reliable_binance_launchpool_last_added_json"
CFG_LAUNCHPOOL_LAST_REMOVED = "rag_reliable_binance_launchpool_last_removed_json"


def _to_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _onboard_to_utc_string(onboard_ms: Any) -> Optional[str]:
    try:
        onboard_ms_int = int(onboard_ms or 0)
    except (TypeError, ValueError):
        return None
    if onboard_ms_int <= 0:
        return None
    return datetime.fromtimestamp(onboard_ms_int / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _timestamp_to_utc_string(value_ms: Any) -> Optional[str]:
    try:
        value_int = int(value_ms or 0)
    except (TypeError, ValueError):
        return None
    if value_int <= 0:
        return None
    return datetime.fromtimestamp(value_int / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _compute_symbol_delta(previous_symbols: List[str], current_symbols: List[str]) -> Tuple[List[str], List[str]]:
    prev = set(previous_symbols or [])
    curr = set(current_symbols or [])
    return sorted(curr - prev), sorted(prev - curr)


async def _get_bot_config(key: str) -> Optional[str]:
    async for session in get_session():
        result = await session.execute(select(BotConfig).where(BotConfig.key == key))
        row = result.scalar_one_or_none()
        return row.value if row else None
    return None


async def _set_bot_config(key: str, value: str) -> None:
    async for session in get_session():
        await session.merge(BotConfig(key=key, value=value))
        await session.commit()
        return


async def _replace_document(
    title: str,
    doc_type: str,
    horizon: str,
    content: str,
    status: str,
    valid_until: Optional[datetime],
    asset: Optional[str] = None,
) -> None:
    async for session in get_session():
        result = await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.title == title))
        docs = result.scalars().all()
        for doc in docs:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
            await session.delete(doc)
        await session.commit()
        break

    await ingest_document(
        title=title,
        doc_type=doc_type,
        horizon=horizon,
        content=content,
        asset=asset,
        status=status,
        valid_until=valid_until,
    )


async def _fetch_binance_exchange_info() -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(BINANCE_EXCHANGE_INFO_URL) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Binance exchangeInfo failed HTTP {response.status}: {text[:200]}")
            return await response.json()


async def _fetch_binance_launchpool_page(page_no: int = 1, page_size: int = 100) -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    params = {"pageNo": max(1, page_no), "pageSize": max(1, min(page_size, 100))}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(BINANCE_LAUNCHPOOL_URL, params=params) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Binance launchpool failed HTTP {response.status}: {text[:200]}")
            payload = await response.json()
    if str(payload.get("code")) != "000000":
        raise RuntimeError(f"Binance launchpool returned error code: {payload.get('code')}")
    return payload.get("data") or {}


def _normalize_launchpool_rows(tracking_rows: List[Dict[str, Any]], completed_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dedup: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in (tracking_rows or []) + (completed_rows or []):
        symbol = str(row.get("rebateCoin") or "").upper().strip()
        if not symbol:
            continue
        try:
            trade_ms = int(row.get("coinTradeTime") or 0)
        except (TypeError, ValueError):
            trade_ms = 0
        if trade_ms <= 0:
            continue
        key = (symbol, trade_ms)
        if key in dedup:
            continue
        dedup[key] = {
            "symbol": symbol,
            "project_name": row.get("projectName"),
            "project_id": row.get("projectId"),
            "trade_time_ms": trade_ms,
            "trade_time_utc": _timestamp_to_utc_string(trade_ms),
            "status": row.get("status"),
            "coin_trading": row.get("coinTrading"),
        }
    return sorted(
        dedup.values(),
        key=lambda x: (x["trade_time_ms"], str(x["symbol"])),
        reverse=True,
    )


async def _fetch_all_launchpool_rows() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    first_data = await _fetch_binance_launchpool_page(page_no=1, page_size=100)
    tracking_info = first_data.get("tracking") or {}
    completed_info = first_data.get("completed") or {}

    tracking_rows = list(tracking_info.get("list") or [])
    completed_rows = list(completed_info.get("list") or [])

    try:
        completed_total = int(completed_info.get("total") or 0)
    except (TypeError, ValueError):
        completed_total = len(completed_rows)

    page_no = 2
    while len(completed_rows) < completed_total:
        page_data = await _fetch_binance_launchpool_page(page_no=page_no, page_size=100)
        page_tracking = (page_data.get("tracking") or {}).get("list") or []
        page_completed = (page_data.get("completed") or {}).get("list") or []
        tracking_rows.extend(page_tracking)
        if not page_completed:
            break
        completed_rows.extend(page_completed)
        page_no += 1

    return tracking_rows, completed_rows


async def _build_cmc_macro_snapshot() -> Optional[str]:
    settings = get_settings()
    cmc_key = get_credential("CMC_API_KEY") or settings.CMC_API_KEY
    if not cmc_key:
        return None

    cmc = CMCClient(api_key=cmc_key)
    fng = await cmc.get_fear_and_greed()
    global_m = await cmc.get_global_metrics()

    fng_value = fng.get("value", "N/A")
    fng_class = fng.get("value_classification", "Unknown")

    content = (
        "Source: CoinMarketCap API\n"
        f"Fetched At UTC: {_to_iso_utc(datetime.now(timezone.utc))}\n"
        "Reliability: Official API endpoint with configured API key.\n\n"
        "Macro Snapshot:\n"
        f"- Fear & Greed Index: {fng_value} ({fng_class})\n"
        f"- BTC Dominance: {global_m.get('btc_dominance', 'N/A')}\n"
        f"- ETH Dominance: {global_m.get('eth_dominance', 'N/A')}\n"
        f"- Total Market Cap USD: {global_m.get('total_market_cap_usd', 'N/A')}\n"
        f"- Total 24h Volume USD: {global_m.get('total_24h_volume_usd', 'N/A')}\n"
        f"- Altcoin Market Cap USD: {global_m.get('altcoin_market_cap_usd', 'N/A')}\n"
    )
    return content


async def refresh_reliable_market_knowledge(
    auto_approve: bool = True,
    include_binance: bool = True,
    include_cmc: bool = True,
) -> Dict[str, Any]:
    """
    Refreshes high-reliability market context documents for RAG.
    Uses official APIs only and stores compact, replace-in-place documents.
    """
    settings = get_settings()
    now_utc = datetime.now(timezone.utc)
    status = "approved" if auto_approve else "pending_review"
    valid_until = now_utc + timedelta(hours=max(1, settings.RAG_RELIABLE_DOC_TTL_HOURS))

    summary: Dict[str, Any] = {
        "success": True,
        "updated_documents": [],
        "warnings": [],
        "errors": [],
        "fetched_at_utc": _to_iso_utc(now_utc),
    }

    try:
        await _replace_document(
            title="Source Policy: Binance Listings Reliability Rules",
            doc_type="logic_rule",
            horizon="short_term",
            content=(
                "Reliability Rules for Binance listing intelligence:\n"
                "1) Treat only official Binance API/announcement data as factual listing status.\n"
                "2) A coin is considered listed only when present as TRADING in Binance Spot exchangeInfo.\n"
                "3) Rumors/social posts are unverified context and must never be used as hard facts.\n"
                "4) If no official listing signal exists, output uncertainty explicitly.\n"
                "5) Prefer newest official refresh timestamp when conflicting records appear.\n"
            ),
            status=status,
            valid_until=valid_until,
        )
        summary["updated_documents"].append("Source Policy: Binance Listings Reliability Rules")
    except Exception as e:
        logger.exception("Failed to update listing reliability policy document")
        summary["errors"].append(f"policy_document_failed: {e}")

    if include_binance:
        try:
            payload = await _fetch_binance_exchange_info()
            symbols = payload.get("symbols", [])
            server_time = payload.get("serverTime")
            timezone_name = payload.get("timezone", "UTC")

            tradable_usdt = []
            tradable_meta = []
            for item in symbols:
                if item.get("quoteAsset") != "USDT":
                    continue
                if item.get("status") != "TRADING":
                    continue
                symbol = item.get("symbol")
                if not symbol:
                    continue
                symbol = str(symbol).upper().strip()
                if not SYMBOL_PATTERN.match(symbol):
                    continue
                tradable_usdt.append(symbol)
                tradable_meta.append(
                    {
                        "symbol": symbol,
                        "base_asset": item.get("baseAsset"),
                        "onboard_date": _onboard_to_utc_string(item.get("onboardDate")),
                    }
                )

            tradable_usdt = sorted(set(tradable_usdt))
            tradable_meta.sort(key=lambda x: (x["onboard_date"] or "", x["symbol"]), reverse=True)

            previous_raw = await _get_bot_config(CFG_USDT_SYMBOLS)
            previous_symbols = json.loads(previous_raw) if previous_raw else []
            added, removed = _compute_symbol_delta(previous_symbols, tradable_usdt)

            await _set_bot_config(CFG_USDT_SYMBOLS, json.dumps(tradable_usdt))
            await _set_bot_config(CFG_LAST_ADDED, json.dumps(added))
            await _set_bot_config(CFG_LAST_REMOVED, json.dumps(removed))

            header = (
                "Source: Binance Spot ExchangeInfo API\n"
                f"Fetched At UTC: {_to_iso_utc(now_utc)}\n"
                f"Server Time (ms): {server_time}\n"
                f"Timezone: {timezone_name}\n"
                "Reliability: Official Binance public API endpoint.\n\n"
            )

            symbols_body = "\n".join(f"- {s}" for s in tradable_usdt)
            universe_content = (
                header
                + f"Current TRADING USDT Symbol Count: {len(tradable_usdt)}\n\n"
                + "Current Tradable USDT Symbols:\n"
                + symbols_body
            )

            delta_content = (
                header
                + "Symbol Delta vs previous refresh:\n"
                + f"- Added symbols count: {len(added)}\n"
                + f"- Removed symbols count: {len(removed)}\n\n"
                + "Added Symbols:\n"
                + ("\n".join(f"- {s}" for s in added) if added else "- None")
                + "\n\nRemoved Symbols:\n"
                + ("\n".join(f"- {s}" for s in removed) if removed else "- None")
            )

            newest_lines = []
            for item in tradable_meta[:80]:
                onboard = item["onboard_date"] or "Unknown"
                newest_lines.append(f"- {item['symbol']} ({item['base_asset']}) | onboard: {onboard}")
            newest_content = (
                header
                + "Newest observed TRADING USDT symbols by onboard date (top 80):\n"
                + ("\n".join(newest_lines) if newest_lines else "- None")
            )

            await _replace_document(
                title="Source: Binance API - Spot USDT Symbol Universe",
                doc_type="exchange_listing_snapshot",
                horizon="short_term",
                content=universe_content,
                status=status,
                valid_until=valid_until,
            )
            await _replace_document(
                title="Source: Binance API - Spot Listing Delta",
                doc_type="exchange_listing_delta",
                horizon="short_term",
                content=delta_content,
                status=status,
                valid_until=valid_until,
            )
            await _replace_document(
                title="Source: Binance API - Newest Spot Listings",
                doc_type="exchange_listing_snapshot",
                horizon="short_term",
                content=newest_content,
                status=status,
                valid_until=valid_until,
            )
            summary["updated_documents"].extend(
                [
                    "Source: Binance API - Spot USDT Symbol Universe",
                    "Source: Binance API - Spot Listing Delta",
                    "Source: Binance API - Newest Spot Listings",
                ]
            )
            summary["binance"] = {
                "usdt_symbol_count": len(tradable_usdt),
                "added_count": len(added),
                "removed_count": len(removed),
            }
        except Exception as e:
            logger.exception("Reliable Binance RAG refresh failed")
            summary["errors"].append(f"binance_refresh_failed: {e}")

        try:
            tracking_rows, completed_rows = await _fetch_all_launchpool_rows()
            normalized = _normalize_launchpool_rows(tracking_rows, completed_rows)

            now_ms = int(now_utc.timestamp() * 1000)
            recent_cutoff_ms = int((now_utc - timedelta(days=90)).timestamp() * 1000)
            upcoming = [row for row in normalized if row["trade_time_ms"] >= now_ms]
            recent = [row for row in normalized if row["trade_time_ms"] >= recent_cutoff_ms]

            current_candidate_symbols = sorted(set(row["symbol"] for row in recent))
            previous_launchpool_raw = await _get_bot_config(CFG_LAUNCHPOOL_CANDIDATES)
            previous_candidate_symbols = json.loads(previous_launchpool_raw) if previous_launchpool_raw else []
            lp_added, lp_removed = _compute_symbol_delta(previous_candidate_symbols, current_candidate_symbols)

            await _set_bot_config(CFG_LAUNCHPOOL_CANDIDATES, json.dumps(current_candidate_symbols))
            await _set_bot_config(CFG_LAUNCHPOOL_LAST_ADDED, json.dumps(lp_added))
            await _set_bot_config(CFG_LAUNCHPOOL_LAST_REMOVED, json.dumps(lp_removed))

            launchpool_header = (
                "Source: Binance Launchpool Public API\n"
                f"Fetched At UTC: {_to_iso_utc(now_utc)}\n"
                "Reliability: Official Binance public API endpoint.\n"
                "Interpretation: Launchpool tokens are listing-candidate signals, not guaranteed outcomes.\n\n"
            )

            upcoming_lines = [
                f"- {row['symbol']} | {row.get('project_name') or 'Unknown'} | trade_time: {row['trade_time_utc']}"
                for row in upcoming[:60]
            ]
            recent_lines = [
                f"- {row['symbol']} | {row.get('project_name') or 'Unknown'} | trade_time: {row['trade_time_utc']}"
                for row in recent[:120]
            ]

            upcoming_content = (
                launchpool_header
                + f"Upcoming Launchpool trade-time candidates: {len(upcoming)}\n\n"
                + "Upcoming Candidates:\n"
                + ("\n".join(upcoming_lines) if upcoming_lines else "- None")
            )

            recent_content = (
                launchpool_header
                + f"Recent Launchpool candidates in last 90 days: {len(recent)}\n"
                + f"Delta added symbols vs previous refresh: {len(lp_added)}\n"
                + f"Delta removed symbols vs previous refresh: {len(lp_removed)}\n\n"
                + "Recent Candidates:\n"
                + ("\n".join(recent_lines) if recent_lines else "- None")
                + "\n\nAdded Symbols:\n"
                + ("\n".join(f"- {s}" for s in lp_added) if lp_added else "- None")
                + "\n\nRemoved Symbols:\n"
                + ("\n".join(f"- {s}" for s in lp_removed) if lp_removed else "- None")
            )

            await _replace_document(
                title="Source: Binance API - Launchpool Upcoming Candidates",
                doc_type="exchange_listing_signal",
                horizon="short_term",
                content=upcoming_content,
                status=status,
                valid_until=valid_until,
            )
            await _replace_document(
                title="Source: Binance API - Launchpool Recent Candidates",
                doc_type="exchange_listing_signal",
                horizon="short_term",
                content=recent_content,
                status=status,
                valid_until=valid_until,
            )
            summary["updated_documents"].extend(
                [
                    "Source: Binance API - Launchpool Upcoming Candidates",
                    "Source: Binance API - Launchpool Recent Candidates",
                ]
            )
            summary["binance_launchpool"] = {
                "upcoming_count": len(upcoming),
                "recent_90d_count": len(recent),
                "added_count": len(lp_added),
                "removed_count": len(lp_removed),
            }
        except Exception as e:
            logger.exception("Reliable Binance Launchpool RAG refresh failed")
            summary["errors"].append(f"binance_launchpool_refresh_failed: {e}")

    if include_cmc:
        try:
            macro = await _build_cmc_macro_snapshot()
            if macro:
                await _replace_document(
                    title="Source: CMC API - Macro Snapshot",
                    doc_type="macro_snapshot",
                    horizon="long_term",
                    content=macro,
                    status=status,
                    valid_until=valid_until,
                )
                summary["updated_documents"].append("Source: CMC API - Macro Snapshot")
            else:
                summary["warnings"].append("cmc_key_missing_or_unconfigured")
        except Exception as e:
            logger.exception("Reliable CMC RAG refresh failed")
            summary["errors"].append(f"cmc_refresh_failed: {e}")

    await _set_bot_config(CFG_LAST_REFRESH, _to_iso_utc(now_utc))
    summary["success"] = len(summary["errors"]) == 0
    return summary
