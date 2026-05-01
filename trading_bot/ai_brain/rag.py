import json
import logging
import math
import os
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from sqlalchemy import select

from core.database import get_session
from core.models import KnowledgeDocument, KnowledgeChunk, Trade
from core.security import get_credential

logger = logging.getLogger(__name__)

# Try purely math-based cosine similarity first for zero-dependency local setups
def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


async def get_embedding(text: str) -> List[float]:
    """
    Retrieves the vector embedding for a piece of text. 
    Currently defaults to Ollama's local embeddings so it runs offline and free.
    """
    import aiohttp

    # Read the actual Ollama endpoint from credentials/env, not BotConfig.
    base_url = (
        get_credential("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://127.0.0.1:11434"
    )

    # Your local Ollama already has bge-m3:latest available and it is suitable for embeddings.
    embedding_model = (
        get_credential("OLLAMA_EMBEDDING_MODEL")
        or os.environ.get("OLLAMA_EMBEDDING_MODEL")
        or "bge-m3:latest"
    )
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url.rstrip('/')}/api/embeddings",
                json={"model": embedding_model, "prompt": text}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("embedding", [])
                else:
                    logger.warning(
                        f"Embedding failed for model '{embedding_model}' via {base_url}. "
                        "Check that the model exists in Ollama and the base URL is reachable."
                    )
                    return []
    except Exception as e:
        logger.error(f"Error calling embedding API: {e}")
        return []


async def ingest_document(
    title: str,
    doc_type: str,
    horizon: str,
    content: str,
    asset: Optional[str] = None,
    status: str = "pending_review",
    valid_until: Optional[datetime] = None,
):
    """
    Chunks a document and saves it plus its embeddings to the SQLite DB.
    """
    # 1. Save Document Metadata
    async for session in get_session():
        doc = KnowledgeDocument(
            title=title,
            doc_type=doc_type,
            horizon=horizon,
            asset=asset,
            content=content,
            status=status,
            valid_until=valid_until,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        
        # 2. Chunking Logic (very simple overlap chunking)
        chunk_size = 500
        overlap = 100
        words = content.split()
        
        chunks = []
        for i in range(0, len(words), chunk_size - overlap):
            chunk_text = " ".join(words[i : i + chunk_size])
            chunks.append(chunk_text)
            if i + chunk_size >= len(words):
                break
                
        # 3. Embed & Save Chunks
        for idx, chunk_str in enumerate(chunks):
            embedding = await get_embedding(chunk_str)
            if not embedding:
                continue
                
            chunk = KnowledgeChunk(
                document_id=doc.id,
                chunk_index=idx,
                text=chunk_str,
                embedding=json.dumps(embedding)
            )
            session.add(chunk)
            
        await session.commit()
        logger.info(f"Ingested RAG document '{title}' into {len(chunks)} embedded chunks.")


async def search_knowledge(query: str, horizon: str = None, top_k: int = 3) -> List[Dict]:
    """
    Queries the database semantically. Returns the most relevant chunks.
    """
    query_vector = await get_embedding(query)
    if not query_vector:
        return []
        
    results = []
    now_utc = datetime.utcnow()
    
    async for session in get_session():
        # Fetch all chunks (this works nicely for small DBs under 50,000 chunks)
        # If the knowledge base gets massive, implement faiss or switch to pgvector.
        db_chunks = await session.execute(select(KnowledgeChunk))
        docs = await session.execute(select(KnowledgeDocument))
        
        docs_map = {d.id: d for d in docs.scalars().all()}
        all_chunks = db_chunks.scalars().all()
        
        scored_chunks = []
        for ch in all_chunks:
            if not ch.embedding:
                continue
                
            # Filter by horizon if specified
            parent_doc = docs_map.get(ch.document_id)
            if not parent_doc or parent_doc.status != "approved":
                continue
            if parent_doc.valid_until and parent_doc.valid_until < now_utc:
                continue
            if horizon and parent_doc.horizon != horizon:
                continue
                
            chunk_vector = json.loads(ch.embedding)
            sim = cosine_similarity(query_vector, chunk_vector)
            scored_chunks.append((sim, ch, parent_doc))
            
        # Top K
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = scored_chunks[:top_k]
        
        for sim, ch, doc in top_chunks:
            if not doc:
                continue
            results.append({
                "score": sim,
                "text": ch.text,
                "source": getattr(doc, 'title', 'Unknown'),
                "horizon": getattr(doc, 'horizon', 'N/A')
            })
            
    return results


async def search_rules(query: str, asset: Optional[str] = None, top_k: int = 5) -> List[Dict]:
    """
    Specifically searches for 'logic_rule' or 'thesis' documents that are 'approved'.
    This is used by the RiskEngine for compliance checking.
    """
    query_vector = await get_embedding(query)
    if not query_vector:
        return []
        
    results = []
    now_utc = datetime.utcnow()
    async for session in get_session():
        # Optimization: Only select approved logic documents
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.status == "approved",
            KnowledgeDocument.doc_type.in_(["logic_rule", "thesis"])
        )
        docs_res = await session.execute(stmt)
        docs_map = {d.id: d for d in docs_res.scalars().all()}
        
        if not docs_map:
            return []

        # Get chunks for these documents
        chunks_stmt = select(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(list(docs_map.keys())))
        chunks_res = await session.execute(chunks_stmt)
        all_chunks = chunks_res.scalars().all()

        scored_chunks = []
        for ch in all_chunks:
            chunk_vector = json.loads(ch.embedding)
            sim = cosine_similarity(query_vector, chunk_vector)
            
            # Boost score if asset matches
            parent_doc = docs_map[ch.document_id]
            if asset and parent_doc.asset == asset:
                sim += 0.1
            if parent_doc.valid_until and parent_doc.valid_until < now_utc:
                continue
                
            scored_chunks.append((sim, ch, parent_doc))
            
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        for sim, ch, doc in scored_chunks[:top_k]:
            if not doc:
                continue
            results.append({
                "score": sim,
                "text": ch.text,
                "title": getattr(doc, 'title', 'Unknown'),
                "type": getattr(doc, 'doc_type', 'N/A'),
                "asset": getattr(doc, 'asset', None)
            })
            
    return results


async def ingest_trade_lessons():
    """
    Analyzes recent trade history and creates 'lesson' documents for the RAG system.
    """
    async for session in get_session():
        # Fetch last 30 completed trades with realized PnL
        res = await session.execute(
            select(Trade)
            .where(Trade.realized_pnl != None)
            .order_by(Trade.created_at.desc())
            .limit(30)
        )
        trades = res.scalars().all()
        
        if not trades:
            return "No trades found to learn from."

        wins = [t for t in trades if t.realized_pnl > 0]
        losses = [t for t in trades if t.realized_pnl < 0]
        
        lesson_content = f"ANALYSIS OF RECENT {len(trades)} TRADES (WINS: {len(wins)}, LOSSES: {len(losses)}):\n\n"
        
        if wins:
            lesson_content += "SUCCESSFUL PATTERNS:\n"
            for t in wins[:5]:
                lesson_content += f"- {t.symbol} {t.side}: Profit ${t.realized_pnl:.2f}. Strategy: {t.strategy or 'Unknown'}. Reason: {t.ai_reason or 'N/A'}\n"
        
        if losses:
            lesson_content += "\nFAILED PATTERNS & LESSONS:\n"
            for t in losses[:5]:
                lesson_content += f"- {t.symbol} {t.side}: Loss ${t.realized_pnl:.2f}. Strategy: {t.strategy or 'Unknown'}. Reason: {t.ai_reason or 'N/A'}\n"

        # Ingest as a special lesson document
        await ingest_document(
            title=f"Trade Lessons {datetime.utcnow().strftime('%Y-%m-%d')}",
            doc_type="lesson",
            horizon="short_term",
            content=lesson_content,
            status="approved" # Auto-approve bot lessons
        )
        return f"Ingested {len(trades)} trade lessons into RAG."
