#!/usr/bin/env python3
"""Pre-demo preflight: confirm both configured models exist and actually answer.

The Fireworks serverless catalog rotates; a retired model returns 404. Run this
before recording a demo or trusting any number on the dashboard.

    python scripts/verify_models.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app import config  # noqa: E402
from app.models import CHEAP, FRONTIER, MODEL_REGISTRY, VIRTUAL_MODEL_ID  # noqa: E402

PRICING_DOC = "https://docs.fireworks.ai/serverless/pricing"


def main() -> int:
    if not config.has_api_key():
        print("FAIL  FIREWORKS_API_KEY is not set. Copy .env.example to .env.")
        return 1

    headers = {"Authorization": f"Bearer {config.FIREWORKS_API_KEY}"}
    ok = True

    print("Catalog check")
    try:
        r = httpx.get(f"{config.FIREWORKS_BASE_URL}/models", headers=headers, timeout=30)
        r.raise_for_status()
        available = {m["id"] for m in r.json().get("data", [])}
    except Exception as exc:
        print(f"  FAIL  could not list models: {config.redact(str(exc))}")
        return 1

    for spec in MODEL_REGISTRY.values():
        mark = "ok  " if spec.id in available else "FAIL"
        if spec.id not in available:
            ok = False
        print(f"  {mark}  {spec.category:8} {spec.id}")

    print("\nLive completion check  (2 real API calls, up to ~15s each)")
    for spec in MODEL_REGISTRY.values():
        # Print the label before the call so the user sees progress, not silence.
        print(f"  {spec.display_name:16} ", end="", flush=True)
        started = time.time()
        try:
            r = httpx.post(
                f"{config.FIREWORKS_BASE_URL}/chat/completions",
                headers=headers,
                json={
                    "model": spec.id,
                    "messages": [{"role": "user", "content": "Reply with the word OK."}],
                    "max_tokens": 64,
                },
                timeout=config.TIMEOUT_SECONDS,
            )
            elapsed = int((time.time() - started) * 1000)
            if r.status_code != 200:
                ok = False
                print(f"FAIL  HTTP {r.status_code} {config.redact(r.text[:160])}")
                continue
            usage = r.json().get("usage", {})
            print(f"ok  {elapsed:>6}ms  "
                  f"in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}")
        except Exception as exc:
            ok = False
            print(f"FAIL  {config.redact(str(exc))}")

    print("\nConfigured pricing (USD per 1M tokens) - VERIFY against")
    print(f"  {PRICING_DOC}")
    print(f"  {'model':18} {'input':>8} {'output':>8}")
    for spec in MODEL_REGISTRY.values():
        print(f"  {spec.display_name:18} {spec.input_per_1m:>8.2f} {spec.output_per_1m:>8.2f}")

    ratio_in = FRONTIER.input_per_1m / CHEAP.input_per_1m
    ratio_out = FRONTIER.output_per_1m / CHEAP.output_per_1m
    print(f"\n  frontier/cheap ratio: {ratio_in:.0f}x input, {ratio_out:.0f}x output")
    print(f"  virtual model id:     {VIRTUAL_MODEL_ID}")

    print("\nPASS - safe to demo." if ok else "\nFAILED - fix before demoing.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
