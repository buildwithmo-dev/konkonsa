import httpx
import os
import json
from typing import Optional

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
BASE_URL = "https://api.anthropic.com/v1/messages"
HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}


async def call_claude(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 1024,
    as_json: bool = False,
) -> str | dict:
    """
    Send a prompt to Claude and return the text response.
    If as_json=True, strips markdown fences and parses the response as JSON.
    """
    messages = [{"role": "user", "content": prompt}]
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(BASE_URL, headers=HEADERS, json=payload)
        response.raise_for_status()
        raw = response.json()["content"][0]["text"]

    if as_json:
        clean = raw.strip().removeprefix("```json").removesuffix("```").strip()
        return json.loads(clean)

    return raw


async def call_claude_batch(prompts: list[str], max_tokens: int = 512) -> list[str]:
    """Send multiple prompts concurrently and return responses in order."""
    import asyncio
    tasks = [call_claude(p, max_tokens=max_tokens) for p in prompts]
    return await asyncio.gather(*tasks, return_exceptions=True)
