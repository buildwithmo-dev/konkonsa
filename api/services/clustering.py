# api/services/clustering.py
import numpy as np
from datetime import datetime
from collections import Counter
from sqlalchemy import select, update

from models import Classification, FeedItem, Cluster
from database import AsyncSessionLocal
from services.embeddings import embed_texts, embed_text


async def recompute_clusters(
    eps: float = 0.3,
    min_samples: int = 2,
    max_items: int = 1000,
) -> dict:
    """
    Main clustering pipeline:
    1. Load classifications with embedded feed items
    2. Run DBSCAN
    3. Save Cluster records and assign cluster_id to classifications
    """
    from sklearn.cluster import DBSCAN
    from sklearn.preprocessing import normalize

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Classification, FeedItem)
            .join(FeedItem, FeedItem.id == Classification.feed_item_id)
            .where(Classification.failed == False)
            .limit(max_items)
        )
        rows = result.all()

    if not rows:
        return {"status": "no data", "clusters": 0, "noise": 0}

    texts = [
        f"{r.Classification.topic or ''} {r.Classification.summary or ''} {r.FeedItem.title or ''}"
        for r in rows
    ]

    vectors = []
    for r in rows:
        cached = (r.FeedItem.raw_data or {}).get("embedding")
        vectors.append(cached if cached else None)

    missing_idx = [i for i, v in enumerate(vectors) if v is None]
    if missing_idx:
        missing_texts = [texts[i] for i in missing_idx]
        computed = await embed_texts(missing_texts)
        for i, vec in zip(missing_idx, computed):
            vectors[i] = vec

    X = normalize(np.array(vectors, dtype=np.float32))

    db_scan = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine", n_jobs=-1)
    labels = db_scan.fit_predict(X)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int(np.sum(labels == -1))

    cluster_groups: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        if label == -1:
            continue
        cluster_groups.setdefault(label, []).append(idx)

    async with AsyncSessionLocal() as db:
        # Detach classifications BEFORE deleting clusters. cluster_id has no
        # ON DELETE clause, so on Postgres (unlike the SQLite test DB, which
        # doesn't enforce FKs) db.delete(c) below would otherwise raise
        # ForeignKeyViolation for any cluster still referenced.
        existing = await db.execute(select(Cluster))
        existing_clusters = existing.scalars().all()
        existing_ids = [c.id for c in existing_clusters]
        if existing_ids:
            await db.execute(
                update(Classification)
                .where(Classification.cluster_id.in_(existing_ids))
                .values(cluster_id=None)
            )
        for c in existing_clusters:
            await db.delete(c)
        await db.commit()

        label_to_cluster_id: dict[int, str] = {}

        for label, indices in cluster_groups.items():
            all_keywords = []
            for idx in indices:
                kws = rows[idx].Classification.keywords or []
                all_keywords.extend(kws)

            top_keywords = [kw for kw, _ in Counter(all_keywords).most_common(5)]
            cluster_label = ", ".join(top_keywords) if top_keywords else f"Cluster {label}"

            cluster_vecs = np.array([vectors[i] for i in indices])
            centroid = cluster_vecs.mean(axis=0).tolist()

            cluster = Cluster(
                label=cluster_label,
                keywords=top_keywords,
                item_count=len(indices),
                centroid=centroid,
            )
            db.add(cluster)
            await db.flush()
            label_to_cluster_id[label] = cluster.id

        for idx, label in enumerate(labels):
            if label == -1:
                continue
            cls = rows[idx].Classification
            cls_result = await db.execute(select(Classification).where(Classification.id == cls.id))
            cls_obj = cls_result.scalar_one_or_none()
            if cls_obj:
                cls_obj.cluster_id = label_to_cluster_id[label]

        await db.commit()

    return {
        "status": "ok",
        "total_items": len(rows),
        "clusters": n_clusters,
        "noise": n_noise,
        "timestamp": datetime.utcnow().isoformat(),
    }


async def get_cluster_neighbors(cluster_id: str, query: str, top_k: int = 5) -> list[dict]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Classification, FeedItem)
            .join(FeedItem, FeedItem.id == Classification.feed_item_id)
            .where(Classification.cluster_id == cluster_id)
        )
        rows = result.all()

    if not rows:
        return []

    query_vec = await embed_text(query)
    candidates = []
    for r in rows:
        vec = (r.FeedItem.raw_data or {}).get("embedding")
        if vec:
            from services.embeddings import cosine_similarity
            candidates.append({
                "classification_id": r.Classification.id,
                "topic": r.Classification.topic,
                "summary": r.Classification.summary,
                "similarity": cosine_similarity(query_vec, vec),
            })

    return sorted(candidates, key=lambda x: x["similarity"], reverse=True)[:top_k]