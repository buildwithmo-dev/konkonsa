import asyncio
import json
import numpy as np
from typing import Optional
from functools import lru_cache

# Lazy-loaded model — only downloaded on first use
_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")   # Fast, 384-dim, ~80MB
    return _model


async def embed_text(text: str) -> list[float]:
    """Embed a single string. Returns a list of floats."""
    model = get_model()
    vector = await asyncio.get_event_loop().run_in_executor(
        None, lambda: model.encode(text, normalize_embeddings=True)
    )
    return vector.tolist()


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings efficiently."""
    model = get_model()
    vectors = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False),
    )
    return vectors.tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    va = np.array(a)
    vb = np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


async def find_most_similar(
    query: str,
    candidates: list[dict],   # each must have "id" and "text" keys
    top_k: int = 5,
) -> list[dict]:
    """
    Given a query string and a list of candidate dicts,
    return the top_k most semantically similar candidates.

    Example:
        candidates = [{"id": "abc", "text": "I hate slow apps"}]
        results = await find_most_similar("performance issues", candidates)
    """
    if not candidates:
        return []

    query_vec = await embed_text(query)
    texts = [c["text"] for c in candidates]
    candidate_vecs = await embed_texts(texts)

    scored = [
        {**c, "similarity": cosine_similarity(query_vec, vec)}
        for c, vec in zip(candidates, candidate_vecs)
    ]

    return sorted(scored, key=lambda x: x["similarity"], reverse=True)[:top_k]


async def embed_and_store_classifications(db) -> int:
    """
    Fetch all Classifications without embeddings and compute + store them.
    Stores the embedding as JSON in the Classification.raw metadata.
    Returns count of items embedded.
    """
    from sqlalchemy import select
    from models import Classification, FeedItem

    result = await db.execute(
        select(Classification, FeedItem)
        .join(FeedItem, FeedItem.id == Classification.feed_item_id)
        .where(Classification.cluster_id == None)       # unassigned items
        .limit(200)
    )
    rows = result.all()
    if not rows:
        return 0

    texts = [
        f"{r.Classification.topic or ''} {r.Classification.summary or ''} {r.FeedItem.title or ''}"
        for r in rows
    ]
    vectors = await embed_texts(texts)

    for row, vec in zip(rows, vectors):
        # Store embedding as JSON string in a new column (requires migration to add)
        # For now, store in raw_data of the FeedItem
        item = row.FeedItem
        if not item.raw_data:
            item.raw_data = {}
        item.raw_data["embedding"] = vec

    await db.commit()
    return len(rows)
