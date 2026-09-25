"""
Real-time fan-out for newly ingested feed items.

Konkonsa runs the web API and the ingestion scheduler as two separate Render
services with no shared memory. A plain in-process broadcast call from the
worker can't reach browser WebSocket connections held by the web process.

Ingestion calls `notify_new_items(...)`, which issues a Postgres `pg_notify`
— visible to any process connected to the same database, regardless of which
service sent it. The web process runs a background asyncpg LISTEN loop
(started in main.py's lifespan) that receives that notification and
re-broadcasts it to whatever WebSocket clients are connected to *this*
process via ConnectionManager.

On SQLite (local dev / tests) LISTEN/NOTIFY doesn't exist; everything here
degrades to a no-op rather than raising.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

from database import raw_postgres_dsn

log = logging.getLogger("realtime")

NOTIFY_CHANNEL = "konkonsa_feed"


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict) -> None:
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
_listener_conn = None  # asyncpg.Connection — held only by the web process


async def notify_new_items(source_id: str, feed_item_ids: list[str]) -> None:
    """
    Call from ingestion services after committing new FeedItems. Safe to call
    from either process (web or worker); safe to call on SQLite (no-op).
    """
    if not feed_item_ids:
        return

    dsn = raw_postgres_dsn()
    if not dsn:
        return  # local SQLite dev/tests — nothing to notify

    try:
        import asyncpg
        conn = await asyncpg.connect(dsn)
        try:
            payload = json.dumps({"source_id": source_id, "feed_item_ids": feed_item_ids[:50]})
            await conn.execute("SELECT pg_notify($1, $2)", NOTIFY_CHANNEL, payload)
        finally:
            await conn.close()
    except Exception as e:
        # Real-time is a nice-to-have; never let a notify failure break ingestion.
        log.warning("notify_new_items failed: %s", e)


async def start_listener() -> None:
    """Call once from the web process's lifespan startup. No-op on SQLite."""
    global _listener_conn

    dsn = raw_postgres_dsn()
    if not dsn:
        log.info("Realtime listener skipped (non-Postgres DATABASE_URL).")
        return

    import asyncpg

    def _on_notify(_conn, _pid, _channel, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"raw": payload}
        asyncio.create_task(manager.broadcast({"type": "new_items", "data": data}))

    try:
        _listener_conn = await asyncpg.connect(dsn)
        await _listener_conn.add_listener(NOTIFY_CHANNEL, _on_notify)
        log.info("Realtime listener connected on channel '%s'.", NOTIFY_CHANNEL)
    except Exception as e:
        log.error("Failed to start realtime listener: %s", e)
        _listener_conn = None


async def stop_listener() -> None:
    global _listener_conn
    if _listener_conn is not None:
        try:
            await _listener_conn.close()
        except Exception:
            pass
        _listener_conn = None