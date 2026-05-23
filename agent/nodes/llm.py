"""Thin wrapper around OpenAI GPT-4o vision for structured JSON extraction."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
from typing import Any

from openai import AsyncOpenAI

from agent.nodes.docprep import to_vision_image

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "5"))
_MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "4"))

_client: AsyncOpenAI | None = None
_semaphore: asyncio.Semaphore | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = AsyncOpenAI()  # reads OPENAI_API_KEY from env
    return _client


def _get_semaphore() -> asyncio.Semaphore:
    """Caps simultaneous OpenAI calls so a burst of documents (or users) does not
    self-inflict rate-limit errors."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    return _semaphore


def _is_quota_exhausted(exc: Exception) -> bool:
    """True when the account is out of credits / billing is unconfigured. These do
    not recover by retrying — fail fast with a clear message instead."""
    code = getattr(exc, "code", None)
    if code == "insufficient_quota":
        return True
    msg = str(exc)
    return "insufficient_quota" in msg or "exceeded your current quota" in msg


def _is_retryable(exc: Exception) -> bool:
    """True for transient errors worth retrying: short-lived rate limits, server
    overload, timeouts. Quota/billing exhaustion is explicitly NOT retryable."""
    if _is_quota_exhausted(exc):
        return False
    status = getattr(exc, "status_code", None)
    if status in (429, 500, 502, 503, 504):
        return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "rate limit", "rate_limit", "429", "500", "502", "503", "504",
            "timeout", "timed out", "connection error", "overloaded",
        )
    )


async def vision_extract(
    *,
    prompt: str,
    image_bytes: bytes,
    mime_type: str,
    max_tokens: int = 1500,
) -> dict[str, Any]:
    """Send an image + prompt to GPT-4o, expect a JSON object back.

    Retries on transient errors (429 rate limit, 5xx, timeouts) with exponential
    backoff + jitter. Concurrency is capped by a shared semaphore.
    Returns the parsed JSON dict, or `{"_parse_error": "...", "_raw": "..."}` on failure.
    """
    client = _get_client()
    try:
        image_bytes, mime_type = to_vision_image(image_bytes, mime_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Document preprocessing failed: %s", exc)
        return {"_parse_error": f"document_preprocess_failed: {exc}", "_raw": ""}
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    last_exc: Exception | None = None
    response = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with _get_semaphore():
                response = await client.chat.completions.create(
                    model=_MODEL,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=0.0,
                    messages=messages,
                )
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_quota_exhausted(exc):
                logger.error("OpenAI quota exhausted — not retrying")
                return {
                    "_parse_error": (
                        "openai_quota_exhausted: the API key has no remaining credit "
                        "/ billing is not configured. Add credit to the OpenAI account."
                    ),
                    "_raw": "",
                }
            if _is_retryable(exc) and attempt < _MAX_RETRIES:
                delay = min(2 ** attempt * 4, 60) + random.uniform(0, 2)
                logger.warning(
                    "OpenAI transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    str(exc)[:120],
                )
                await asyncio.sleep(delay)
                continue
            logger.exception("OpenAI vision call failed")
            return {"_parse_error": f"openai_call_failed: {exc}", "_raw": ""}
    else:
        return {"_parse_error": f"openai_call_failed: {last_exc}", "_raw": ""}

    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        return {"_parse_error": "openai_returned_empty_response", "_raw": ""}

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse JSON from OpenAI response: %s", exc)
        return {"_parse_error": f"json_decode_failed: {exc}", "_raw": raw}


async def text_extract(
    *,
    prompt: str,
    text: str,
    max_tokens: int = 600,
) -> dict[str, Any]:
    """Text-only structured extraction. Used for parsing manual fields written
    in natural language ('I am single, Indian, belong to SC')."""
    client = _get_client()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]

    last_exc: Exception | None = None
    response = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with _get_semaphore():
                response = await client.chat.completions.create(
                    model=_MODEL,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=0.0,
                    messages=messages,
                )
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_quota_exhausted(exc) or not _is_retryable(exc) or attempt >= _MAX_RETRIES:
                logger.warning("text_extract failed: %s", str(exc)[:120])
                return {"_parse_error": f"openai_call_failed: {exc}", "_raw": ""}
            delay = min(2 ** attempt * 4, 60) + random.uniform(0, 2)
            await asyncio.sleep(delay)
    else:
        return {"_parse_error": f"openai_call_failed: {last_exc}", "_raw": ""}

    raw = (response.choices[0].message.content or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("text_extract could not parse JSON: %s", exc)
        return {"_parse_error": f"json_decode_failed: {exc}", "_raw": raw}
