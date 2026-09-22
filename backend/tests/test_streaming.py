"""Streaming path: passthrough fidelity, usage capture, and failure handling.

This is the code path a real coding harness actually exercises - harnesses send
`stream: true` by default - so it gets the same scrutiny as the rest.
"""
import json

import pytest

from app import fireworks
from app.models import CHEAP_MODEL_ID, FRONTIER_MODEL_ID, VIRTUAL_MODEL_ID

SIMPLE = [{"role": "user", "content": "Rename usr to user."}]
COMPLEX = [{"role": "user", "content":
            "Production outage. Traceback (most recent call last): ValueError: boom. "
            "Why is this failing intermittently under load?"}]


def _frame(**payload) -> str:
    return f"data: {json.dumps(payload)}"


def _fake_stream(frames, usage=None):
    """Build a stand-in for fireworks.stream_chat_completion."""
    async def gen(payload):
        for f in frames:
            parsed = None
            if f.startswith("data: ") and f[6:].strip() not in ("", "[DONE]"):
                try:
                    body = json.loads(f[6:])
                    parsed = body.get("usage")
                except json.JSONDecodeError:
                    parsed = None
            yield (f + "\n\n").encode(), parsed
    return gen


CONTENT_FRAMES = [
    _frame(choices=[{"index": 0, "delta": {"role": "assistant"}}]),
    _frame(choices=[{"index": 0, "delta": {"content": "user"}}]),
    _frame(choices=[{"index": 0, "delta": {"content": " = 5"}}]),
]
USAGE_FRAME = _frame(choices=[], usage={"prompt_tokens": 1000, "completion_tokens": 500})


def _stream(client, model, messages, **kw):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": messages, "stream": True},
        headers={"X-User-ID": kw.pop("user", "streamer")},
    )


def test_streamed_bytes_pass_through_untouched(client, monkeypatch):
    """We must not re-serialise upstream frames - clients parse them directly."""
    monkeypatch.setattr(fireworks, "stream_chat_completion",
                        _fake_stream(CONTENT_FRAMES + [USAGE_FRAME, "data: [DONE]"]))
    r = _stream(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    for frame in CONTENT_FRAMES:
        assert frame in body
    assert "data: [DONE]" in body


def test_streamed_response_carries_router_headers(client, monkeypatch):
    monkeypatch.setattr(fireworks, "stream_chat_completion",
                        _fake_stream(CONTENT_FRAMES + [USAGE_FRAME]))
    r = _stream(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert r.headers["x-router-category"] == "cheap"
    assert r.headers["x-router-decision"] == "routed"
    assert len(r.headers["x-router-reason"]) > 30
    assert "X-Router-Reason" in r.headers["access-control-expose-headers"]


def test_streaming_captures_real_usage_not_an_estimate(client, monkeypatch):
    """The whole point of include_usage: bill streamed requests on real tokens."""
    monkeypatch.setattr(fireworks, "stream_chat_completion",
                        _fake_stream(CONTENT_FRAMES + [USAGE_FRAME]))
    _stream(client, VIRTUAL_MODEL_ID, SIMPLE, user="usage-real")
    row = client.get("/admin/requests?attribution_id=usage-real").json()["data"][0]
    assert row["streamed"] is True
    assert row["tokens_estimated"] is False
    assert row["input_tokens"] == 1000 and row["output_tokens"] == 500
    assert row["cost_usd"] == pytest.approx(0.0004)       # cheap rates
    assert row["status"] == "ok"


def test_streaming_without_a_usage_frame_is_flagged_estimated(client, monkeypatch):
    """A stream that dies before usage must be marked, not silently billed as exact."""
    monkeypatch.setattr(fireworks, "stream_chat_completion", _fake_stream(CONTENT_FRAMES))
    _stream(client, VIRTUAL_MODEL_ID, SIMPLE, user="usage-est")
    row = client.get("/admin/requests?attribution_id=usage-est").json()["data"][0]
    assert row["tokens_estimated"] is True
    assert row["output_tokens"] >= 1


def test_streaming_respects_a_pin(client, monkeypatch):
    seen = {}

    async def gen(payload):
        seen["model"] = payload["model"]
        for f in CONTENT_FRAMES + [USAGE_FRAME]:
            yield (f + "\n\n").encode(), None

    monkeypatch.setattr(fireworks, "stream_chat_completion", gen)
    r = _stream(client, CHEAP_MODEL_ID, COMPLEX)
    assert r.headers["x-router-decision"] == "pinned"
    assert seen["model"] == CHEAP_MODEL_ID


def test_streaming_routes_complex_work_to_frontier(client, monkeypatch):
    seen = {}

    async def gen(payload):
        seen["model"] = payload["model"]
        yield (USAGE_FRAME + "\n\n").encode(), {"prompt_tokens": 10, "completion_tokens": 5}

    monkeypatch.setattr(fireworks, "stream_chat_completion", gen)
    r = _stream(client, VIRTUAL_MODEL_ID, COMPLEX)
    assert r.headers["x-router-category"] == "frontier"
    assert seen["model"] == FRONTIER_MODEL_ID


def test_upstream_error_mid_stream_is_recorded_and_reported(client, monkeypatch):
    async def boom(payload):
        raise fireworks.UpstreamError(500, "upstream exploded")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(fireworks, "stream_chat_completion", boom)
    r = _stream(client, VIRTUAL_MODEL_ID, SIMPLE, user="stream-err")
    assert "error" in r.text
    row = client.get("/admin/requests?attribution_id=stream-err").json()["data"][0]
    assert row["status"] == "upstream_error"


def test_stream_timeout_is_recorded(client, monkeypatch):
    async def slow(payload):
        raise fireworks.UpstreamTimeout("timed out")
        yield  # pragma: no cover

    monkeypatch.setattr(fireworks, "stream_chat_completion", slow)
    _stream(client, VIRTUAL_MODEL_ID, SIMPLE, user="stream-timeout")
    row = client.get("/admin/requests?attribution_id=stream-timeout").json()["data"][0]
    assert row["status"] == "timeout"


@pytest.mark.asyncio
async def test_client_disconnect_mid_stream_is_recorded(monkeypatch):
    """An abandoned stream must still produce a row, and must close upstream.

    Exercised at the generator level: a client hangup closes the response body
    iterator, which is precisely what this asserts. Going through an ASGI test
    transport would not reproduce it, because those buffer the response.
    """
    import json as _json

    from starlette.requests import Request

    from app import config, fireworks, store
    from app.main import chat_completions

    monkeypatch.setattr(config, "FIREWORKS_API_KEY", "fw_test_key_not_real")
    store.init_db()

    upstream_closed = {"value": False}

    async def gen(payload):
        try:
            while True:
                yield (CONTENT_FRAMES[1] + "\n\n").encode(), None
        finally:
            # Exiting this generator is what tears down the upstream request,
            # so Fireworks stops billing for tokens nobody will read.
            upstream_closed["value"] = True

    monkeypatch.setattr(fireworks, "stream_chat_completion", gen)

    body = _json.dumps(
        {"model": VIRTUAL_MODEL_ID, "messages": SIMPLE, "stream": True}
    ).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [(b"content-type", b"application/json"), (b"x-user-id", b"hangup")],
            "query_string": b"",
        },
        receive,
    )

    response = await chat_completions(request)
    iterator = response.body_iterator

    first = await iterator.__anext__()          # one chunk reaches the client
    assert b"data:" in first

    await iterator.aclose()                      # ...then the client hangs up

    assert upstream_closed["value"], "upstream stream must be closed on client hangup"

    rows = store.recent_requests(attribution_id="hangup")
    assert rows, "a disconnected stream must still be recorded"
    assert rows[0]["status"] == "client_disconnected"
    assert rows[0]["tokens_estimated"] is True
    assert rows[0]["streamed"] is True
