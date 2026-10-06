"""Render worker for streaming listeners (Discord, Telegram)."""
import asyncio
import logging
import os

from services.ingest import BufferedIngestor

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


async def main() -> None:
    ingestor = BufferedIngestor()
    tasks = [asyncio.create_task(ingestor.run())]

    if os.getenv("DISCORD_BOT_TOKEN"):
        from services.discord_listener import run_discord
        tasks.append(asyncio.create_task(run_discord(ingestor)))
    if all(os.getenv(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION")):
        from services.telegram_listener import run_telegram
        tasks.append(asyncio.create_task(run_telegram(ingestor)))

    if len(tasks) == 1:
        raise SystemExit("No listener configured: set DISCORD_BOT_TOKEN and/or TELEGRAM_* env vars")

    try:
        await asyncio.gather(*tasks)   # if one listener crashes, exit so Render restarts the worker
    finally:
        await ingestor.flush()


if __name__ == "__main__":
    asyncio.run(main())