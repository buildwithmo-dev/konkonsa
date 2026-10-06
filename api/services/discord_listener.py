"""Discord gateway listener. Runs in listeners_worker.py only."""
from __future__ import annotations

import asyncio
import logging
import os

import discord

from models import SourceType
from services.ingest import BufferedIngestor, hash_identity, load_sources

log = logging.getLogger("discord")
REFRESH_SECONDS = 600


class DiscordListener(discord.Client):
    def __init__(self, ingestor: BufferedIngestor):
        intents = discord.Intents.default()
        intents.message_content = True  # privileged: enable in the Developer Portal
        super().__init__(intents=intents)
        self.ingestor = ingestor
        self._routes: dict[int, dict] = {}

    async def setup_hook(self) -> None:
        await self._refresh_routes()
        asyncio.create_task(self._refresh_loop())

    async def _refresh_routes(self) -> None:
        routes: dict[int, dict] = {}
        for src in await load_sources(SourceType.discord):
            for gid in src["config"].get("guild_ids", []):
                routes[int(gid)] = src
        self._routes = routes
        log.info("Discord routing %d guild(s)", len(routes))

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            try:
                await self._refresh_routes()
            except Exception:
                log.exception("route refresh failed")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        src = self._routes.get(message.guild.id)
        if not src:
            return
        cfg = src["config"]

        allowed = {int(c) for c in cfg.get("channel_ids", [])}
        channel_ids = {message.channel.id, getattr(message.channel, "parent_id", None)}
        if allowed and not (allowed & channel_ids):
            return

        text = message.content.strip()
        if len(text) < int(cfg.get("min_chars", 20)):
            return

        await self.ingestor.add(src["id"], {
            "external_id": str(message.id),
            "title": text[:280],
            "body": text[:4000],
            "url": message.jump_url,
            "author": hash_identity(message.author.id),   # pseudonymised, never the username
            "score": 0,
            "comment_count": 0,
            "raw_data": {
                "platform": "discord",
                "guild_id": str(message.guild.id),
                "channel": getattr(message.channel, "name", None),
            },
            "_hint": cfg.get("country"),
        }, africa_only=bool(cfg.get("africa_only", True)))


async def run_discord(ingestor: BufferedIngestor) -> None:
    async with DiscordListener(ingestor) as client:
        await client.start(os.environ["DISCORD_BOT_TOKEN"])