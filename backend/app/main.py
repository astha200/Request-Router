"""OpenAI-compatible proxy that routes between a cheap and a frontier model."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import config, fireworks, routing, store
from .admin import router as admin_router
from .models import (
    CHEAP,
    FRONTIER,
    MODEL_REGISTRY,
    VIRTUAL_MODEL_ID,
    is_virtual,
    known_model_ids,
    resolve_pin,
)
from .pricing import cost_usd, estimate_tokens, frontier_baseline_usd

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("router")

app = FastAPI(title="Fireworks Router", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    if not config.has_api_key():
        log.warning("FIREWORKS_API_KEY is not set - /v1/chat/completions will return 503")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(status: int, message: str, err_type: str, code: str) -> JSONResponse:
    """OpenAI-shaped error envelope so SDK clients parse it natively."""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": config.redact(message), "type": err_type, "code": code}},
    )


def _attribution(request: Request, payload: dict[str, Any] | None = None) -> str:
    """Attribution, in priority order.

    OpenAI's own `user` body field comes first - it is the standard, typed way a
    client identifies an end user, and SDKs support it natively. Headers follow,
    because a coding harness you cannot modify can usually still set a header.
    """
    if payload:
        user = payload.get("user")
        if isinstance(user, str) and user.strip():
            return user.strip()[:128]
    for header in ("x-user-id", "x-attribution-id", "x-session-id"):
        val = request.headers.get(header)
        if val and val.strip():
            return val.strip()[:128]
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 12:
        return f"key:{auth[7:15]}"
    return "anonymous"


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database": "ok" if store.db_healthy() else "unavailable",
        "fireworks_api_key_present": config.has_api_key(),
        "virtual_model": VIRTUAL_MODEL_ID,
        "cheap_model": CHEAP.id,
        "frontier_model": FRONTIER.id,
    }


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    """Harnesses call this on startup; omitting it breaks several of them."""
    created = int(time.time())
    data = [
        {
            "id": VIRTUAL_MODEL_ID,
            "object": "model",
            "created": created,
            "owned_by": "fireworks-router",
            "description": "Routed: policy picks cheap or frontier per request.",
        }
    ] + [
        {
            "id": spec.id,
            "object": "model",
            "created": created,
            "owned_by": "fireworks",
            "description": f"Pinned {spec.category} model ({spec.display_name}).",
        }
        for spec in MODEL_REGISTRY.values()
    ]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    request_id = f"req_{uuid.uuid4().hex[:16]}"
    attribution_id = _attribution(request)

    if not config.has_api_key():
        return _error(503, "FIREWORKS_API_KEY is not configured on the proxy.",
                      "configuration_error", "missing_api_key")

    try:
        payload = await request.json()
    except Exception:
        return _error(400, "Request body must be valid JSON.", "invalid_request_error", "malformed_json")
    if not isinstance(payload, dict):
        return _error(400, "Request body must be a JSON object.", "invalid_request_error", "malformed_body")

    attribution_id = _attribution(request, payload)
    requested_model = payload.get("model")
    messages = payload.get("messages")
    if not requested_model or not isinstance(requested_model, str):
        return _error(400, "Field 'model' is required.", "invalid_request_error", "missing_model")
    if not isinstance(messages, list) or not messages:
        return _error(400, "Field 'messages' must be a non-empty array.",
                      "invalid_request_error", "missing_messages")

    # --- decide the lane. Pins are an instruction, never a suggestion. ---
    pinned = resolve_pin(requested_model)
    if pinned is not None:
        decision = routing.RouteDecision(
            model=pinned,
            mode="pinned",
            reason=f"Pinned: client explicitly requested the {pinned.category} model "
                   f"({pinned.display_name}); routing policy not consulted.",
            score=0.0,
            signals=[],
            reason_code="explicit_pin",
        )
    elif is_virtual(requested_model):
        hint = (request.headers.get("x-route-hint") or "").strip().lower() or None
        decision = routing.decide(messages, route_hint=hint)
    else:
        return _error(
            400,
            f"Unknown model '{requested_model}'. Valid models: {', '.join(known_model_ids())}.",
            "invalid_request_error",
            "model_not_found",
        )

    upstream_payload = {**payload, "model": decision.model.id}
    upstream_payload.pop("stream_options", None)

    # The cheap model is a thinking-only model: left alone it spends most of its
    # output budget on reasoning tokens, which are billed at the output rate. On
    # routine turns that is pure waste, so the router asks for low reasoning
    # effort in the cheap lane (~11x fewer output tokens for the same answer).
    # Two guardrails: we never touch a PINNED request (a pin is served exactly as
    # asked), and we never override a value the client set themselves.
    if (
        decision.mode != "pinned"
        and decision.category == "cheap"
        and "reasoning_effort" not in upstream_payload
    ):
        upstream_payload["reasoning_effort"] = "low"
    wants_stream = bool(payload.get("stream"))
    started = time.time()

    router_headers = {
        "X-Request-ID": request_id,
        "X-Router-Decision": decision.mode,
        "X-Router-Category": decision.category,
        "X-Router-Model": decision.model.id,
        "X-Router-Reason": decision.reason.replace("\n", " ")[:500],
        "X-Router-Reason-Code": decision.reason_code,
        "X-Router-Policy-Version": decision.policy_version,
        # Without this a browser fetch() cannot read any of the above.
        "Access-Control-Expose-Headers": (
            "X-Request-ID, X-Router-Decision, X-Router-Category, X-Router-Model, "
            "X-Router-Reason, X-Router-Reason-Code, X-Router-Policy-Version"
        ),
    }

    def persist(inp: int, out: int, estimated: bool, streamed: bool,
                status: str = "ok", error: str | None = None) -> None:
        store.record(
            store.RequestRecord(
                id=request_id,
                created_at=_now(),
                attribution_id=attribution_id,
                requested_model=requested_model,
                selected_model=decision.model.id,
                model_category=decision.category,
                decision_mode=decision.mode,
                route_reason=decision.reason,
                reason_code=decision.reason_code,
                policy_version=decision.policy_version,
                route_signals=decision.signals_json(),
                route_score=decision.score,
                input_tokens=inp,
                output_tokens=out,
                tokens_estimated=estimated,
                cost_usd=cost_usd(decision.model, inp, out),
                frontier_baseline_usd=frontier_baseline_usd(inp, out),
                latency_ms=int((time.time() - started) * 1000),
                streamed=streamed,
                status=status,
                error_message=error,
            )
        )

    prompt_estimate = estimate_tokens(routing.messages_to_text(messages))

    # --- streaming: pass upstream bytes through untouched ---
    if wants_stream:
        async def event_stream():
            captured: dict[str, Any] | None = None
            produced = 0
            # Held explicitly so we can close it deterministically below. Letting
            # it fall out of scope defers teardown to async-generator garbage
            # collection, which the event loop may not run promptly. Until
            # it does, Fireworks keeps streaming and billing.
            upstream = fireworks.stream_chat_completion(upstream_payload)
            try:
                async for chunk, usage in upstream:
                    produced += len(chunk)
                    if usage:
                        captured = usage
                    yield chunk
            except (asyncio.CancelledError, GeneratorExit):
                # The client went away mid-stream. Close upstream immediately so
                # we stop paying for tokens nobody will read, then record what was
                # produced instead of losing the request entirely.
                await upstream.aclose()
                persist(
                    prompt_estimate,
                    max(1, produced // 8),
                    True,
                    True,
                    "client_disconnected",
                    "client closed the connection before the stream finished",
                )
                raise
            except fireworks.UpstreamTimeout:
                persist(prompt_estimate, 0, True, True, "timeout", "upstream timeout")
                yield b'data: {"error":{"message":"Upstream timeout.","type":"timeout"}}\n\n'
                return
            except fireworks.UpstreamError as exc:
                persist(prompt_estimate, 0, True, True, "upstream_error", exc.message[:500])
                yield f'data: {{"error":{{"message":"Upstream error {exc.status_code}."}}}}\n\n'.encode()
                return

            if captured:
                persist(captured.get("prompt_tokens", 0), captured.get("completion_tokens", 0),
                        False, True)
            else:
                # Stream ended without a usage frame: bill an estimate and mark it.
                persist(prompt_estimate, max(1, produced // 8), True, True)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={**router_headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- non-streaming ---
    try:
        body = await fireworks.chat_completion(upstream_payload)
    except fireworks.UpstreamTimeout:
        persist(prompt_estimate, 0, True, False, "timeout", "upstream timeout")
        return _error(504, "Upstream Fireworks request timed out.", "timeout_error", "upstream_timeout")
    except fireworks.UpstreamError as exc:
        persist(prompt_estimate, 0, True, False, "upstream_error", exc.message[:500])
        status = exc.status_code if exc.status_code in (400, 401, 404, 429) else 502
        return _error(status, f"Fireworks upstream error: {exc.message[:300]}",
                      "upstream_error", "upstream_failure")

    usage = body.get("usage") or {}
    inp = usage.get("prompt_tokens")
    out = usage.get("completion_tokens")
    estimated = inp is None or out is None
    if estimated:
        text = "".join(c.get("message", {}).get("content") or "" for c in body.get("choices", []))
        inp, out = prompt_estimate, estimate_tokens(text)
    persist(int(inp), int(out), estimated, False)

    # Report the virtual id back so clients see the model they asked for.
    if decision.mode != "pinned":
        body["model"] = requested_model
    body.setdefault("fireworks_router", {})
    body["fireworks_router"] = {
        "request_id": request_id,
        "decision": decision.mode,
        "category": decision.category,
        "selected_model": decision.model.id,
        "reason": decision.reason,
    }
    return JSONResponse(content=body, headers=router_headers)
