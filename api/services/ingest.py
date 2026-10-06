"""Single write path for every ingestion source."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import AsyncSessionLocal
from models import FeedItem, Source, SourceType
from services.african_context import analyze
from services.realtime import notify_new_items

log = logging.getLogger("ingest")


def hash_identity(value: Any) -> str:
    """Stable pseudonym for chat users. Set AUTHOR_HASH_SALT in production."""
    salt = os.getenv("AUTHOR_HASH_SALT", "")
    return hmac.new(salt.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:16]


async def load_sources(source_type: SourceType) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Source).where(Source.type == source_type, Source.is_active.is_(True))
        )).scalars().all()
    return [{"id": s.id, "name": s.name, "config": s.config or {}} for s in rows]


async def _insert(db, rows: list[dict]) -> list[str]:
    objs = [FeedItem(**r) for r in rows]
    try:
        async with db.begin_nested():
            db.add_all(objs)
            await db.flush()
        return [o.id for o in objs]
    except IntegrityError:
        # Another process inserted some of these between our check and insert.
        ids: list[str] = []
        for r in rows:
            obj = FeedItem(**r)
            try:
                async with db.begin_nested():
                    db.add(obj)
                    await db.flush()
                ids.append(obj.id)
            except IntegrityError:
                continue
        return ids


async def persist_items(source_id: str, items: list[dict], *, africa_only: bool = False) -> int:
    """Score, dedupe and store items. Returns the number of NEW rows.

    Items are FeedItem kwargs plus an optional "_hint" (subreddit / country code)
    that feeds the context scorer.
    """
    prepared: list[dict] = []
    for raw in items:
        item = dict(raw)
        hint = item.pop("_hint", None)
        ctx = analyze(f"{item.get('title') or ''}\n{item.get('body') or ''}", hint=hint)
        if africa_only and not ctx.is_relevant:
            continue
        item["relevance_score"] = ctx.score
        item["primary_country"] = ctx.primary_country
        item["countries"] = ctx.countries
        item["raw_data"] = {**(item.get("raw_data") or {}), "african_context": ctx.signals}
        prepared.append(item)

    inserted_ids: list[str] = []
    async with AsyncSessionLocal() as db:
        external_ids = [p["external_id"] for p in prepared if p.get("external_id")]
        existing: set[str] = set()
        if external_ids:
            existing = set((await db.execute(
                select(FeedItem.external_id).where(
                    FeedItem.source_id == source_id, FeedItem.external_id.in_(external_ids)
                )
            )).scalars())

        fresh, seen = [], set()
        for p in prepared:
            eid = p.get("external_id")
            if eid and (eid in existing or eid in seen):
                continue
            seen.add(eid)
            fresh.append(p)

        if fresh:
            inserted_ids = await _insert(db, fresh)

        source = await db.get(Source, source_id)
        if source:
            source.last_fetched_at = datetime.utcnow()
            source.total_items_fetched = (source.total_items_fetched or 0) + len(inserted_ids)
            source.error_message = None
        await db.commit()

    if inserted_ids:
        await notify_new_items(source_id, inserted_ids)
    return len(inserted_ids)


class BufferedIngestor:
    """Batches messages from streaming listeners (Discord/Telegram) into few DB writes."""

    def __init__(self, flush_interval: float = 10.0, max_buffer: int = 200, max_retained: int = 2000):
        self.flush_interval, self.max_buffer, self.max_retained = flush_interval, max_buffer, max_retained
        self._buf: dict[tuple[str, bool], list[dict]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def add(self, source_id: str, item: dict, africa_only: bool = False) -> None:
        async with self._lock:
            self._buf[(source_id, africa_only)].append(item)
            full = sum(len(v) for v in self._buf.values()) >= self.max_buffer
        if full:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            batches, self._buf = self._buf, defaultdict(list)
        for (source_id, africa_only), items in batches.items():
            try:
                n = await persist_items(source_id, items, africa_only=africa_only)
                if n:
                    log.info("stored %d new item(s) for source %s", n, source_id)
            except Exception:
                log.exception("flush failed for source %s; re-queueing", source_id)
                async with self._lock:
                    if sum(len(v) for v in self._buf.values()) < self.max_retained:
                        self._buf[(source_id, africa_only)].extend(items)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval)
            await self.flush()