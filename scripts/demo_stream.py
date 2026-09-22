#!/usr/bin/env python3
"""Prove SSE streaming works end to end, with visibly incremental output.

Streaming is the requirement that decides whether a real coding harness can use
this proxy at all - harnesses send `stream: true` by default. This prints tokens
as they arrive so a demo viewer can see it is genuinely streaming, then reports
the real token counts the router captured for billing.

    python scripts/demo_stream.py
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import httpx  # noqa: E402
from openai import OpenAI  # noqa: E402

from app.models import FRONTIER_MODEL_ID, VIRTUAL_MODEL_ID  # noqa: E402

BASE_URL = os.getenv("ROUTER_BASE_URL", "http://127.0.0.1:8000/v1")

SHORT_PROMPT = "Write a haiku about cache invalidation, then explain it in two sentences."
LONG_PROMPT = (
    "Explain how HTTP caching works: cache-control directives, ETags and "
    "conditional requests, CDN edge caching, and the common invalidation "
    "strategies. Use short paragraphs with headings."
)


def stream_raw(model: str, prompt: str, max_tokens: int) -> int:
    """Read the wire directly and print each server-sent event as it lands.

    No SDK, no buffering: this is what the proxy actually emits, which is the
    only way to show that streaming is real passthrough rather than a response
    assembled server-side and released in pieces.
    """
    print(f"POST {BASE_URL}/chat/completions   stream=True   (raw SSE frames)\n")
    started = time.time()
    frames = 0
    first = None

    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers={"Content-Type": "application/json", "X-User-ID": "demo-stream"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": True,
        },
        timeout=180,
    ) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            frames += 1
            if first is None:
                first = time.time()
            elapsed = int((time.time() - started) * 1000)
            body = line[5:].strip()
            if body == "[DONE]":
                print(f"  {elapsed:>6}ms  data: [DONE]")
                break
            # Show the part of the frame that differs. The envelope (id, object,
            # created, model) repeats on every chunk and would make each line
            # look identical; the delta is what actually arrives.
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                print(f"  {elapsed:>6}ms  {line[:104]}")
                continue
            if parsed.get("usage"):
                print(f"  {elapsed:>6}ms  usage: {json.dumps(parsed['usage'])[:88]}")
                continue
            choices = parsed.get("choices") or []
            delta = choices[0].get("delta", {}) if choices else {}
            print(f"  {elapsed:>6}ms  delta: {json.dumps(delta)[:88]}")

    total = int((time.time() - started) * 1000)
    ttft = int((first - started) * 1000) if first else None
    print(f"\n  {frames} server-sent events")
    print(f"  first frame at {ttft}ms, last at {total}ms")
    print("  Each line above arrived separately over the open connection.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", action="store_true",
                    help="ask for a longer answer so the streaming is clearly visible")
    ap.add_argument("--frontier", action="store_true",
                    help="pin the frontier model (slower per token, longer output)")
    ap.add_argument("--raw", action="store_true",
                    help="print the raw SSE frames instead of the rendered text")
    args = ap.parse_args()

    prompt = LONG_PROMPT if (args.long or args.frontier) else SHORT_PROMPT
    model = FRONTIER_MODEL_ID if args.frontier else VIRTUAL_MODEL_ID
    max_tokens = 900 if (args.long or args.frontier) else 400

    try:
        httpx.get(BASE_URL.replace("/v1", "/health"), timeout=5).raise_for_status()
    except Exception:
        print(f"Proxy not reachable at {BASE_URL}. Start it with:\n"
              "  cd backend && uvicorn app.main:app --reload --port 8000")
        return 1

    if args.raw:
        return stream_raw(model, prompt, max_tokens)

    client = OpenAI(api_key="local", base_url=BASE_URL, timeout=180)
    print(f"POST {BASE_URL}/chat/completions   model={model}  stream=True\n")

    started = time.time()
    first_token_at: float | None = None
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        stream=True,
        extra_headers={"X-User-ID": "demo-stream"},
    )

    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            if first_token_at is None:
                first_token_at = time.time()
            sys.stdout.write(chunk.choices[0].delta.content)
            sys.stdout.flush()

    total_ms = int((time.time() - started) * 1000)
    ttft_ms = int((first_token_at - started) * 1000) if first_token_at else None

    print("\n")
    print(f"  time to first token : {ttft_ms}ms" if ttft_ms else "  no content received")
    print(f"  total               : {total_ms}ms")

    # Show what the router actually recorded for this streamed request.
    row = httpx.get(f"{BASE_URL.replace('/v1', '')}/admin/requests",
                    params={"attribution_id": "demo-stream", "limit": 1},
                    timeout=10).json()["data"][0]
    print(f"  routed to           : {row['selected_model'].rsplit('/', 1)[-1]} ({row['model_category']})")
    print(f"  reason              : {row['route_reason']}")
    print(f"  tokens              : {row['input_tokens']} in / {row['output_tokens']} out "
          f"({'estimated' if row['tokens_estimated'] else 'real, from upstream usage'})")
    print(f"  cost                : ${row['cost_usd']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
