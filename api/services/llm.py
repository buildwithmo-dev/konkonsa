import asyncio
import json
import logging
import os
import re
from typing import Optional

import httpx

log = logging.getLogger("llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Free-tier friendly default. For harder tasks pass model="llama-3.3-70b-versatile".
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_RETRIES = 4


class LLMError(Exception):
    """Permanent failure (bad key, bad request, unparseable output)."""


class LLMTransientError(LLMError):
    """Rate limit, server error or network problem. Safe to retry later."""


def _extract_json(raw: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise


async def call_llm(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 1024,
    as_json: bool = False,
    model: Optional[str] = None,
    temperature: float = 0.2,
    json_object: bool = False,
) -> str | dict | list:
    """
    Send a prompt to Groq and return the text response.
    as_json=True parses the reply as JSON (fences and stray prose tolerated).
    json_object=True also turns on Groq's JSON mode (top-level object only).
    """
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = None

    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await client.post(GROQ_URL, headers=headers, json=payload)
            except httpx.HTTPError as e:
                if attempt == MAX_RETRIES:
                    raise LLMTransientError(f"Groq network error: {e}") from e
                await asyncio.sleep(2 ** attempt)
                continue

            if resp.status_code in (429, 500, 502, 503):
                if attempt == MAX_RETRIES:
                    raise LLMTransientError(f"Groq {resp.status_code}: {resp.text[:200]}")
                try:
                    delay = float(resp.headers.get("retry-after", ""))
                except ValueError:
                    delay = 2 ** attempt
                await asyncio.sleep(min(delay, 20))
                continue

            if resp.status_code >= 400:
                raise LLMError(f"Groq {resp.status_code}: {resp.text[:300]}")
            break

    raw = resp.json()["choices"][0]["message"]["content"] or ""
    if as_json:
        try:
            return _extract_json(raw)
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned invalid JSON: {raw[:200]}") from e
    return raw


async def call_llm_batch(prompts: list[str], max_tokens: int = 512, concurrency: int = 2) -> list:
    """Run prompts with capped concurrency; returns results (or exceptions) in order."""
    sem = asyncio.Semaphore(concurrency)

    async def one(p: str):
        async with sem:
            return await call_llm(p, max_tokens=max_tokens)

    return await asyncio.gather(*[one(p) for p in prompts], return_exceptions=True)