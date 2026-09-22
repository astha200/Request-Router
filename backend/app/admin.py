"""Admin/console read APIs. Reads the same SQLite store the proxy writes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from . import store
from .models import CHEAP, FRONTIER, POLICY_VERSION, VIRTUAL_MODEL_ID

router = APIRouter(prefix="/admin", tags=["admin"])


def _fail(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": {"message": "Analytics store unavailable."}})


@router.get("/summary")
def summary(attribution_id: str | None = Query(default=None)) -> Any:
    try:
        data = store.summary(attribution_id=attribution_id)
    except Exception as exc:
        return _fail(exc)
    data["filter"] = {"attribution_id": attribution_id}
    data["policy_version"] = POLICY_VERSION
    data["models"] = {
        "virtual": VIRTUAL_MODEL_ID,
        "cheap": {
            "id": CHEAP.id, "name": CHEAP.display_name,
            "input_per_1m": CHEAP.input_per_1m, "output_per_1m": CHEAP.output_per_1m,
        },
        "frontier": {
            "id": FRONTIER.id, "name": FRONTIER.display_name,
            "input_per_1m": FRONTIER.input_per_1m, "output_per_1m": FRONTIER.output_per_1m,
        },
    }
    return data


@router.get("/requests")
def requests(
    attribution_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> Any:
    try:
        return {"data": store.recent_requests(attribution_id=attribution_id, limit=limit)}
    except Exception as exc:
        return _fail(exc)


@router.get("/attributions")
def attributions() -> Any:
    try:
        return {"data": store.attributions()}
    except Exception as exc:
        return _fail(exc)
