import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from core.database import get_session
from core.models import BotConfig, KnowledgeChunk, KnowledgeDocument

router = APIRouter(prefix="/api/rag", tags=["rag"])


# Schemas
class RagDocumentSchema(BaseModel):
    id: int
    title: str
    doc_type: str
    horizon: str
    asset: Optional[str] = None
    content: str
    status: str
    created_at: datetime
    chunk_count: Optional[int] = 0


class RagApprovalRequest(BaseModel):
    status: str  # approved | rejected | pending_review


class RagIngestRequest(BaseModel):
    title: str
    doc_type: str  # thesis | rule | postmortem | narrative ...
    horizon: str  # long_term | swing | hustle | moonshot ...
    content: str
    asset: Optional[str] = None
    auto_approve: bool = False


class RagSearchRequest(BaseModel):
    query: str
    horizon: Optional[str] = None
    top_k: int = 5


class RagStatsResponse(BaseModel):
    total: int
    pending: int
    approved: int
    rejected: int


class ReliableSourceRefreshRequest(BaseModel):
    auto_approve: bool = True
    include_binance: bool = True
    include_cmc: bool = True


def _json_load_list(value: Optional[str]) -> List:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


# Routes
@router.get("/stats", response_model=RagStatsResponse)
async def get_rag_stats():
    """Returns a summary count of documents by status."""
    async for session in get_session():
        result = await session.execute(select(KnowledgeDocument))
        docs = result.scalars().all()
        return RagStatsResponse(
            total=len(docs),
            pending=sum(1 for d in docs if d.status == "pending_review"),
            approved=sum(1 for d in docs if d.status == "approved"),
            rejected=sum(1 for d in docs if d.status == "rejected"),
        )


@router.get("/documents", response_model=List[RagDocumentSchema])
async def list_rag_documents(status: Optional[str] = None, horizon: Optional[str] = None):
    """Lists all knowledge documents, optionally filtered by status and/or horizon."""
    async for session in get_session():
        query = select(KnowledgeDocument)
        if status:
            query = query.where(KnowledgeDocument.status == status)
        if horizon:
            query = query.where(KnowledgeDocument.horizon == horizon)
        result = await session.execute(query.order_by(KnowledgeDocument.created_at.desc()))
        docs = result.scalars().all()

        output = []
        for doc in docs:
            chunk_result = await session.execute(
                select(func.count()).where(KnowledgeChunk.document_id == doc.id)
            )
            chunk_count = chunk_result.scalar() or 0
            output.append(
                RagDocumentSchema(
                    id=doc.id,
                    title=doc.title,
                    doc_type=doc.doc_type,
                    horizon=doc.horizon,
                    asset=doc.asset,
                    content=doc.content,
                    status=doc.status,
                    created_at=doc.created_at,
                    chunk_count=chunk_count,
                )
            )
        return output


@router.post("/documents")
async def ingest_rag_document(request: RagIngestRequest):
    """
    Manually ingest a document into the RAG knowledge base.
    Documents start as pending_review by default and do not ground AI
    until explicitly approved.
    """
    from ai_brain.rag import ingest_document

    status = "approved" if request.auto_approve else "pending_review"
    await ingest_document(
        title=request.title,
        doc_type=request.doc_type,
        horizon=request.horizon,
        content=request.content,
        asset=request.asset,
        status=status,
    )
    return {
        "message": f"Document '{request.title}' ingested successfully.",
        "status": status,
        "note": (
            "Document is pending review. Approve it in the RAG tab before it grounds the AI."
            if status == "pending_review"
            else "Document is immediately active in AI context."
        ),
    }


@router.post("/documents/{doc_id}/status")
async def update_rag_document_status(doc_id: int, request: RagApprovalRequest):
    """Approve, reject, or re-queue a knowledge document."""
    if request.status not in ["approved", "rejected", "pending_review"]:
        raise HTTPException(status_code=400, detail="Invalid status. Use: approved | rejected | pending_review")

    async for session in get_session():
        doc = await session.get(KnowledgeDocument, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        doc.status = request.status
        await session.commit()
        return {"message": f"Document '{doc.title}' status -> {request.status}"}


@router.post("/ingest-trade-lessons")
async def trigger_ingest_trade_lessons():
    """Trigger the RAG system to analyze recent trades and learn lessons."""
    from ai_brain.rag import ingest_trade_lessons
    message = await ingest_trade_lessons()
    return {"message": message}


@router.delete("/documents/{doc_id}")
async def delete_rag_document(doc_id: int):
    """Permanently deletes a document and all its embedded vector chunks."""
    async for session in get_session():
        doc = await session.get(KnowledgeDocument, doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        await session.execute(KnowledgeChunk.__table__.delete().where(KnowledgeChunk.document_id == doc_id))
        await session.delete(doc)
        await session.commit()
        return {"message": f"Document '{doc.title}' and all its chunks deleted."}


@router.post("/search")
async def search_rag(request: RagSearchRequest):
    """
    Performs a live semantic search against approved knowledge documents.
    Useful for testing what context the AI would receive for a given query.
    """
    from ai_brain.rag import search_knowledge

    results = await search_knowledge(
        query=request.query,
        horizon=request.horizon,
        top_k=request.top_k,
    )
    return {
        "query": request.query,
        "horizon_filter": request.horizon,
        "results": results,
        "count": len(results),
        "note": "Only approved documents are returned. Pending/rejected docs are excluded.",
    }


@router.post("/sources/reliable/refresh")
async def refresh_reliable_sources(request: ReliableSourceRefreshRequest):
    """Refreshes production-grade reliable-source RAG context from official APIs."""
    from services.rag_reliable_feeder import refresh_reliable_market_knowledge

    return await refresh_reliable_market_knowledge(
        auto_approve=request.auto_approve,
        include_binance=request.include_binance,
        include_cmc=request.include_cmc,
    )


@router.get("/sources/reliable/status")
async def get_reliable_source_status():
    keys = [
        "rag_reliable_last_refresh_utc",
        "rag_reliable_binance_usdt_symbols_json",
        "rag_reliable_binance_last_added_json",
        "rag_reliable_binance_last_removed_json",
        "rag_reliable_binance_launchpool_candidates_json",
        "rag_reliable_binance_launchpool_last_added_json",
        "rag_reliable_binance_launchpool_last_removed_json",
    ]
    values = {}
    async for session in get_session():
        result = await session.execute(select(BotConfig).where(BotConfig.key.in_(keys)))
        rows = result.scalars().all()
        values = {row.key: row.value for row in rows}
        break

    symbols = _json_load_list(values.get("rag_reliable_binance_usdt_symbols_json"))
    added = _json_load_list(values.get("rag_reliable_binance_last_added_json"))
    removed = _json_load_list(values.get("rag_reliable_binance_last_removed_json"))
    launchpool_candidates = _json_load_list(values.get("rag_reliable_binance_launchpool_candidates_json"))
    launchpool_added = _json_load_list(values.get("rag_reliable_binance_launchpool_last_added_json"))
    launchpool_removed = _json_load_list(values.get("rag_reliable_binance_launchpool_last_removed_json"))

    return {
        "last_refresh_utc": values.get("rag_reliable_last_refresh_utc"),
        "binance_usdt_symbol_count": len(symbols),
        "last_added_symbols": added[:30],
        "last_removed_symbols": removed[:30],
        "launchpool_candidate_symbol_count": len(launchpool_candidates),
        "launchpool_last_added_symbols": launchpool_added[:30],
        "launchpool_last_removed_symbols": launchpool_removed[:30],
    }

