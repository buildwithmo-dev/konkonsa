"""Telegram listener (Telethon, user session). Runs in listeners_worker.py only.

The session account must already have joined the public channels you list.
Use a dedicated account, and respect each channel's terms.
"""
from __future__ import annotations

import asyncio
import logging
import os

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from models import SourceType
from services.ingest import BufferedIngestor, load_sources

log = logging.getLogger("telegram")
REFRESH_SECONDS = 600


async def run_telegram(ingestor: BufferedIngestor) -> None:
    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("TELEGRAM_SESSION is not authorised; generate a new session string")

    routes: dict[str, dict] = {}

    async def refresh() -> None:
        nonlocal routes
        new: dict[str, dict] = {}
        for src in await load_sources(SourceType.telegram):
            for ch in src["config"].get("channels", []):
                new[str(ch).lstrip("@").lower()] = src
        routes = new
        log.info("Telegram routing %d channel(s)", len(routes))

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            try:
                await refresh()
            except Exception:
                log.exception("route refresh failed")

    await refresh()
    asyncio.create_task(refresh_loop())

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event) -> None:
        chat = await event.get_chat()
        username = (getattr(chat, "username", None) or "").lower()
        src = routes.get(username)
        text = (event.raw_text or "").strip()
        if not src or len(text) < int(src["config"].get("min_chars", 20)):
            return
        cfg = src["config"]
        await ingestor.add(src["id"], {
            "external_id": f"{event.chat_id}:{event.message.id}",
            "title": text[:280],
            "body": text[:4000],
            "url": f"https://t.me/{username}/{event.message.id}",
            "author": None,                       # channel posts: no individual author stored
            "score": int(event.message.views or 0),
            "comment_count": 0,
            "raw_data": {"platform": "telegram", "channel": username},
            "_hint": cfg.get("country"),
        }, africa_only=bool(cfg.get("africa_only", True)))

    await client.run_until_disconnected()