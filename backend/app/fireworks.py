"""Fireworks upstream client. The only module that touches the network."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from . import config


class UpstreamError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = config.redact(message)
        super().__init__(self.message)


class UpstreamTimeout(Exception):
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.FIREWORKS_API_KEY}",
        "Content-Type": "application/json",
    }


async def chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    """Non-streaming completion. Returns the upstream JSON body verbatim."""
    url = f"{config.FIREWORKS_BASE_URL}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=_headers(), json=payload)
    except httpx.TimeoutException as exc:
        raise UpstreamTimeout(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(502, f"upstream request failed: {exc}") from exc

    if resp.status_code != 200:
        raise UpstreamError(resp.status_code, resp.text)
    return resp.json()


async def stream_chat_completion(
    payload: dict[str, Any],
) -> AsyncIterator[tuple[bytes, dict[str, Any] | None]]:
    """Stream a completion, yielding (raw_sse_bytes, usage_or_None).

    We pass the upstream SSE bytes through untouched so the client sees exactly
    what Fireworks sent - no re-serialisation, no dropped fields. We only *peek*
    at each frame to capture the usage block that `stream_options.include_usage`
    appends to the final chunk, which is what lets us bill a streamed request
    with real token counts instead of an estimate.
    """
    url = f"{config.FIREWORKS_BASE_URL}/chat/completions"
    body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
    try:
        async with httpx.AsyncClient(timeout=config.TIMEOUT_SECONDS) as client:
            async with client.stream("POST", url, headers=_headers(), json=body) as resp:
                if resp.status_code != 200:
                    raw = await resp.aread()
                    raise UpstreamError(resp.status_code, raw.decode("utf-8", "replace"))
                # If the consumer of this generator is closed (client hung up),
                # exiting these context managers tears down the upstream
                # connection, which is what actually stops the billing.
                async for line in resp.aiter_lines():
                    if line == "":
                        continue
                    usage = None
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data and data != "[DONE]":
                            try:
                                parsed = json.loads(data)
                                if parsed.get("usage"):
                                    usage = parsed["usage"]
                            except json.JSONDecodeError:
                                pass
                    yield (line + "\n\n").encode("utf-8"), usage
    except httpx.TimeoutException as exc:
        raise UpstreamTimeout(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(502, f"upstream stream failed: {exc}") from exc
