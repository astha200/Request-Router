"""Proxy contract: pinning, routing, compatibility and error handling.

Upstream is mocked - these assert OUR behaviour, not Fireworks'.
"""
import pytest

from app import config, fireworks
from app.models import CHEAP_MODEL_ID, FRONTIER_MODEL_ID, VIRTUAL_MODEL_ID

SIMPLE = [{"role": "user", "content": "Rename the variable usr to user."}]
COMPLEX = [{"role": "user", "content":
            "Production outage. Traceback (most recent call last): ValueError: boom. "
            "Why is this failing intermittently under load?"}]


def _post(client, model, messages, **kw):
    return client.post("/v1/chat/completions",
                       json={"model": model, "messages": messages, **kw.pop("json_extra", {})},
                       headers={"X-User-ID": "astha", **kw.pop("headers", {})})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.json()["database"] == "ok"


def test_health_never_leaks_the_key(client):
    assert "fw_test_key_not_real" not in client.get("/health").text


def test_models_endpoint_lists_all_three_ids(client):
    ids = {m["id"] for m in client.get("/v1/models").json()["data"]}
    assert ids == {VIRTUAL_MODEL_ID, CHEAP_MODEL_ID, FRONTIER_MODEL_ID}


# --- routing -----------------------------------------------------------------

def test_virtual_model_routes_simple_request_to_cheap(client):
    r = _post(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert r.status_code == 200
    assert r.headers["x-router-category"] == "cheap"
    assert r.headers["x-router-decision"] == "routed"
    assert client.upstream_calls[-1]["model"] == CHEAP_MODEL_ID


def test_virtual_model_routes_debugging_request_to_frontier(client):
    r = _post(client, VIRTUAL_MODEL_ID, COMPLEX)
    assert r.headers["x-router-category"] == "frontier"
    assert client.upstream_calls[-1]["model"] == FRONTIER_MODEL_ID


def test_every_response_carries_a_route_reason(client):
    r = _post(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert len(r.headers["x-router-reason"]) > 30
    assert len(r.json()["fireworks_router"]["reason"]) > 30


# --- pinning: an instruction, never a suggestion -----------------------------

def test_cheap_pin_stays_cheap_even_for_a_complex_prompt(client):
    r = _post(client, CHEAP_MODEL_ID, COMPLEX)
    assert r.headers["x-router-category"] == "cheap"
    assert r.headers["x-router-decision"] == "pinned"
    assert client.upstream_calls[-1]["model"] == CHEAP_MODEL_ID


def test_frontier_pin_stays_frontier_even_for_a_trivial_prompt(client):
    r = _post(client, FRONTIER_MODEL_ID, SIMPLE)
    assert r.headers["x-router-category"] == "frontier"
    assert r.headers["x-router-decision"] == "pinned"
    assert client.upstream_calls[-1]["model"] == FRONTIER_MODEL_ID


def test_route_hint_cannot_override_a_pin(client):
    """A pin outranks the override header."""
    r = _post(client, CHEAP_MODEL_ID, COMPLEX, headers={"X-Route-Hint": "frontier"})
    assert r.headers["x-router-decision"] == "pinned"
    assert client.upstream_calls[-1]["model"] == CHEAP_MODEL_ID


def test_pinned_requests_are_not_reshaped(client):
    """We never inject reasoning_effort into a request the developer pinned."""
    _post(client, CHEAP_MODEL_ID, SIMPLE)
    assert "reasoning_effort" not in client.upstream_calls[-1]


def test_routed_cheap_requests_get_low_reasoning_effort(client):
    _post(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert client.upstream_calls[-1]["reasoning_effort"] == "low"


def test_client_reasoning_effort_is_never_overridden(client):
    _post(client, VIRTUAL_MODEL_ID, SIMPLE, json_extra={"reasoning_effort": "high"})
    assert client.upstream_calls[-1]["reasoning_effort"] == "high"


# --- compatibility -----------------------------------------------------------

def test_response_keeps_openai_shape(client):
    body = _post(client, VIRTUAL_MODEL_ID, SIMPLE).json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "ok"
    assert body["usage"]["prompt_tokens"] == 1000


def test_routed_response_echoes_the_requested_virtual_id(client):
    """Clients that validate the echoed model must not see a surprise value."""
    assert _post(client, VIRTUAL_MODEL_ID, SIMPLE).json()["model"] == VIRTUAL_MODEL_ID


def test_bare_slug_is_accepted_as_a_pin(client):
    r = _post(client, "kimi-k3", SIMPLE)
    assert r.headers["x-router-decision"] == "pinned"
    assert client.upstream_calls[-1]["model"] == FRONTIER_MODEL_ID


# --- errors ------------------------------------------------------------------

def test_unknown_model_is_rejected_with_valid_options(client):
    r = _post(client, "gpt-4o", SIMPLE)
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "gpt-4o" in msg and VIRTUAL_MODEL_ID in msg


def test_missing_messages_rejected(client):
    r = client.post("/v1/chat/completions", json={"model": VIRTUAL_MODEL_ID})
    assert r.status_code == 400


def test_missing_model_rejected(client):
    r = client.post("/v1/chat/completions", json={"messages": SIMPLE})
    assert r.status_code == 400


def test_malformed_json_rejected(client):
    r = client.post("/v1/chat/completions", content=b"{not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_upstream_failure_returns_502_without_leaking_the_key(client, monkeypatch):
    async def boom(payload):
        raise fireworks.UpstreamError(500, f"boom key={config.FIREWORKS_API_KEY}")
    monkeypatch.setattr(fireworks, "chat_completion", boom)
    r = _post(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert r.status_code == 502
    assert config.FIREWORKS_API_KEY not in r.text
    assert "REDACTED" in r.text


def test_upstream_timeout_returns_504(client, monkeypatch):
    async def slow(payload):
        raise fireworks.UpstreamTimeout("timed out")
    monkeypatch.setattr(fireworks, "chat_completion", slow)
    assert _post(client, VIRTUAL_MODEL_ID, SIMPLE).status_code == 504


def test_missing_api_key_returns_503(client, monkeypatch):
    monkeypatch.setattr(config, "FIREWORKS_API_KEY", "")
    r = _post(client, VIRTUAL_MODEL_ID, SIMPLE)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "missing_api_key"


# --- persistence + attribution ----------------------------------------------

def test_requests_are_persisted_with_attribution_and_cost(client):
    _post(client, VIRTUAL_MODEL_ID, SIMPLE, headers={"X-User-ID": "zoe"})
    rows = client.get("/admin/requests?attribution_id=zoe").json()["data"]
    assert len(rows) >= 1
    row = rows[0]
    assert row["attribution_id"] == "zoe"
    assert row["input_tokens"] == 1000 and row["output_tokens"] == 500
    assert row["cost_usd"] == pytest.approx(0.0004)          # cheap rates
    assert row["frontier_baseline_usd"] == pytest.approx(0.0105)
    assert row["latency_ms"] >= 0
    assert row["route_reason"] and row["route_signals"]
    assert row["tokens_estimated"] is False


def test_attribution_falls_back_to_anonymous(client):
    client.post("/v1/chat/completions", json={"model": VIRTUAL_MODEL_ID, "messages": SIMPLE})
    ids = {a["attribution_id"] for a in client.get("/admin/attributions").json()["data"]}
    assert "anonymous" in ids


def test_failed_upstream_is_still_recorded(client, monkeypatch):
    async def boom(payload):
        raise fireworks.UpstreamError(500, "nope")
    monkeypatch.setattr(fireworks, "chat_completion", boom)
    _post(client, VIRTUAL_MODEL_ID, SIMPLE, headers={"X-User-ID": "errcase"})
    rows = client.get("/admin/requests?attribution_id=errcase").json()["data"]
    assert rows and rows[0]["status"] == "upstream_error"


def test_summary_reconciles_routed_and_pinned_spend(client):
    s = client.get("/admin/summary").json()
    assert s["total_spend_usd"] == pytest.approx(
        s["routed_spend_usd"] + s["pinned_spend_usd"], abs=1e-9)
