#!/usr/bin/env python3
"""Generate representative traffic through the running proxy.

Every request here goes through the real proxy to the real Fireworks API, so the
console fills with real tokens, real latency and real cost.

    python scripts/demo_traffic.py            # full mix
    python scripts/demo_traffic.py --reset    # wipe router.db first
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from openai import OpenAI  # noqa: E402

from app.models import CHEAP_MODEL_ID, FRONTIER_MODEL_ID, VIRTUAL_MODEL_ID  # noqa: E402

BASE_URL = os.getenv("ROUTER_BASE_URL", "http://127.0.0.1:8000/v1")

# (attribution_id, model, label, prompt, expected_lane)
TRAFFIC: list[tuple[str, str, str, str, str]] = [
    # --- routine work: should land cheap -------------------------------------
    ("astha", VIRTUAL_MODEL_ID, "rename a variable",
     "Rename the variable `usr` to `user` in this snippet and return only the code:\n"
     "```python\nusr = get_current()\nprint(usr.name)\n```", "cheap"),
    ("astha", VIRTUAL_MODEL_ID, "summarize a function",
     "Summarize what this function does in one sentence:\n"
     "```python\ndef slugify(t):\n    return '-'.join(t.lower().split())\n```", "cheap"),
    ("priya", VIRTUAL_MODEL_ID, "write a simple unit test",
     "Write a simple unit test for this function using pytest:\n"
     "```python\ndef add(a, b):\n    return a + b\n```", "cheap"),
    ("priya", VIRTUAL_MODEL_ID, "explain a short function",
     "Explain what this short helper does:\n"
     "```python\ndef clamp(v, lo, hi):\n    return max(lo, min(v, hi))\n```", "cheap"),
    ("marcus", VIRTUAL_MODEL_ID, "add a docstring",
     "Add a docstring to this function and return only the code:\n"
     "```python\ndef retry(fn, n=3):\n    for i in range(n):\n        try:\n            "
     "return fn()\n        except Exception:\n            pass\n```", "cheap"),
    ("marcus", VIRTUAL_MODEL_ID, "fix a typo",
     "Fix the typo in this variable name: `recieved_count = 0`", "cheap"),

    # --- work that earns frontier --------------------------------------------
    ("astha", VIRTUAL_MODEL_ID, "debug a concurrency problem",
     "We have a race condition in our worker pool. Two goroutines write the same map "
     "and we intermittently see `fatal error: concurrent map writes` in production. "
     "The mutex is held during read but not during the write-back. Why does this only "
     "reproduce under load, and what is the correct fix?", "frontier"),
    ("astha", VIRTUAL_MODEL_ID, "investigate a production failure",
     "Our checkout service returns 500 intermittently in production, roughly 0.3% of "
     "requests, only during peak traffic.\n"
     "Traceback (most recent call last):\n"
     "  File \"app/payments.py\", line 88, in charge\n"
     "    token = self.session.token.refresh()\n"
     "ValueError: token expired\n"
     "It never reproduces locally. Walk me through the root cause investigation.", "frontier"),
    ("priya", VIRTUAL_MODEL_ID, "security review",
     "Do a security review of this Flask endpoint. I'm worried about SQL injection, "
     "missing authorization, and secret leakage:\n"
     "```python\n@app.route('/user/<uid>')\ndef get_user(uid):\n"
     "    q = f\"SELECT * FROM users WHERE id = {uid}\"\n"
     "    return jsonify(db.execute(q).fetchall())\n```", "frontier"),
    ("marcus", VIRTUAL_MODEL_ID, "architecture tradeoff",
     "We're deciding between event sourcing and a traditional CRUD schema for our "
     "billing ledger. We need auditability, ~50k writes/day, and the ability to "
     "reconstruct balance at any point in time. Walk me through the architecture "
     "tradeoffs at that scale and what you'd recommend.", "frontier"),
    ("astha", VIRTUAL_MODEL_ID, "multi-file refactor",
     "I need to refactor authentication across `auth/session.py`, `auth/tokens.py`, "
     "`api/middleware.py` and `tests/test_auth.py` to move from session cookies to "
     "short-lived JWTs with refresh rotation. What's the migration plan, and what "
     "breaks first if we deploy it incrementally?", "frontier"),

    # --- pins: the developer overrides the router ----------------------------
    ("marcus", CHEAP_MODEL_ID, "PINNED cheap (cost-conscious batch job)",
     "List three common Python naming conventions. Be brief.", "cheap"),
    ("priya", FRONTIER_MODEL_ID, "PINNED frontier (trivial prompt, forced)",
     "What does the `yield` keyword do in Python? One paragraph.", "frontier"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete router.db before running")
    args = ap.parse_args()

    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            p = REPO / f"router.db{suffix}"
            if p.exists():
                p.unlink()
                print(f"removed {p.name}")
        print("Restart the backend so it recreates the schema, then re-run without --reset.\n")
        return 0

    client = OpenAI(api_key="local", base_url=BASE_URL, timeout=180)

    try:
        import httpx
        h = httpx.get(BASE_URL.replace("/v1", "/health"), timeout=5).json()
        if not h.get("fireworks_api_key_present"):
            print("FIREWORKS_API_KEY missing on the proxy. Set it in .env and restart.")
            return 1
    except Exception:
        print(f"Proxy not reachable at {BASE_URL}. Start it with:\n"
              "  cd backend && uvicorn app.main:app --reload --port 8000")
        return 1

    print(f"Sending {len(TRAFFIC)} requests through {BASE_URL}\n")
    mismatches = 0

    for i, (user, model, label, prompt, expected) in enumerate(TRAFFIC, 1):
        started = time.time()
        try:
            resp = client.chat.completions.with_raw_response.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                extra_headers={"X-User-ID": user},
            )
            headers = resp.headers
            lane = headers.get("x-router-category", "?")
            mode = headers.get("x-router-decision", "?")
            elapsed = int((time.time() - started) * 1000)
            flag = " " if lane == expected else "!"
            if lane != expected:
                mismatches += 1
            tag = "pinned" if mode == "pinned" else "routed"
            print(f"{flag}{i:>3}. {user:<7} {label:<42} {lane:<8} {tag:<7} {elapsed:>6}ms")
        except Exception as exc:
            print(f"  {i:>3}. {user:<7} {label:<42} FAILED: {str(exc)[:90]}")

    print(f"\nDone. {'All lanes as expected.' if not mismatches else f'{mismatches} lane mismatch(es) - see ! rows.'}")
    print("Open the console at http://localhost:5173")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
