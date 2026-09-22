"""SQLite persistence. The only module that knows the database exists.

Analytics must never break a developer's request: every write is wrapped so a
DB failure logs and returns rather than propagating into the proxy response.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from . import config

log = logging.getLogger("router.store")
_init_lock = threading.Lock()
_initialised = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id                    TEXT PRIMARY KEY,
  created_at            TEXT NOT NULL,
  attribution_id        TEXT NOT NULL,
  requested_model       TEXT NOT NULL,
  selected_model        TEXT NOT NULL,
  model_category        TEXT NOT NULL,
  decision_mode         TEXT NOT NULL,
  route_reason          TEXT NOT NULL,
  reason_code           TEXT NOT NULL DEFAULT 'unclassified',
  policy_version        TEXT NOT NULL DEFAULT 'v1',
  route_signals         TEXT,
  route_score           REAL,
  input_tokens          INTEGER NOT NULL,
  output_tokens         INTEGER NOT NULL,
  tokens_estimated      INTEGER NOT NULL DEFAULT 0,
  cost_usd              REAL NOT NULL,
  frontier_baseline_usd REAL NOT NULL,
  latency_ms            INTEGER NOT NULL,
  streamed              INTEGER NOT NULL DEFAULT 0,
  status                TEXT NOT NULL DEFAULT 'ok',
  error_message         TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at DESC);
-- Composite: the console's hot query is "recent requests for one attribution id".
CREATE INDEX IF NOT EXISTS idx_requests_attr_created
  ON requests(attribution_id, created_at DESC);
"""

# Columns added after the first release. SQLite has no "ADD COLUMN IF NOT EXISTS",
# so we attempt each and ignore the duplicate-column error.
_MIGRATIONS = [
    "ALTER TABLE requests ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'unclassified'",
    "ALTER TABLE requests ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'v1'",
]


@dataclass
class RequestRecord:
    id: str
    created_at: str
    attribution_id: str
    requested_model: str
    selected_model: str
    model_category: str
    decision_mode: str
    route_reason: str
    reason_code: str
    policy_version: str
    route_signals: list[dict[str, Any]]
    route_score: float | None
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    cost_usd: float
    frontier_baseline_usd: float
    latency_ms: int
    streamed: bool
    status: str = "ok"
    error_message: str | None = None


@contextmanager
def connect(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path or config.DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    global _initialised
    with _init_lock:
        with connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for statement in _MIGRATIONS:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            conn.commit()
        _initialised = True


def db_healthy(db_path: str | None = None) -> bool:
    try:
        with connect(db_path) as conn:
            conn.execute("SELECT 1 FROM requests LIMIT 1")
        return True
    except Exception:
        return False


def record(rec: RequestRecord, db_path: str | None = None) -> None:
    """Persist one request. Never raises: analytics must not break the proxy."""
    try:
        with connect(db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO requests (
                     id, created_at, attribution_id, requested_model, selected_model,
                     model_category, decision_mode, route_reason, reason_code, policy_version,
                     route_signals, route_score, input_tokens, output_tokens, tokens_estimated,
                     cost_usd, frontier_baseline_usd, latency_ms, streamed, status, error_message
                   ) VALUES
                   (:id,:created_at,:attribution_id,:requested_model,:selected_model,
                    :model_category,:decision_mode,:route_reason,:reason_code,:policy_version,
                    :route_signals,:route_score,:input_tokens,:output_tokens,:tokens_estimated,
                    :cost_usd,:frontier_baseline_usd,:latency_ms,:streamed,:status,:error_message)""",
                {
                    **rec.__dict__,
                    "route_signals": json.dumps(rec.route_signals),
                    "tokens_estimated": int(rec.tokens_estimated),
                    "streamed": int(rec.streamed),
                },
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover
        log.error("failed to persist request %s: %s", rec.id, config.redact(str(exc)))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["route_signals"] = json.loads(d["route_signals"]) if d.get("route_signals") else []
    d["tokens_estimated"] = bool(d["tokens_estimated"])
    d["streamed"] = bool(d["streamed"])
    return d


def recent_requests(
    attribution_id: str | None = None, limit: int = 100, db_path: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM requests"
    params: list[Any] = []
    if attribution_id:
        sql += " WHERE attribution_id = ?"
        params.append(attribution_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with connect(db_path) as conn:
        return [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def summary(attribution_id: str | None = None, db_path: str | None = None) -> dict[str, Any]:
    """Aggregate spend, savings and route mix.

    Savings are computed over ROUTED requests only. A pinned request was an
    explicit instruction from the developer - the router did not make that call,
    so claiming credit for it would be dishonest. Pinned spend is reported
    separately so the two numbers still reconcile to total spend.
    """
    where = " WHERE attribution_id = ?" if attribution_id else ""
    params: list[Any] = [attribution_id] if attribution_id else []

    with connect(db_path) as conn:
        # Median, not just mean: latency here is long-tailed (frontier calls run
        # ~10s, cheap ~2s), so a mean describes no actual request.
        lat_rows = conn.execute(
            f"SELECT latency_ms FROM requests{where} ORDER BY latency_ms", params
        ).fetchall()
        codes = conn.execute(
            f"""SELECT reason_code, model_category, COUNT(*) AS n FROM requests{where}
                GROUP BY reason_code, model_category ORDER BY n DESC""",
            params,
        ).fetchall()
        agg = conn.execute(
            f"""SELECT
                  COUNT(*)                                             AS total_requests,
                  COALESCE(SUM(cost_usd), 0)                           AS total_spend,
                  COALESCE(SUM(latency_ms), 0)                         AS total_latency,
                  COALESCE(SUM(input_tokens), 0)                       AS input_tokens,
                  COALESCE(SUM(output_tokens), 0)                      AS output_tokens,
                  COALESCE(SUM(model_category = 'cheap'), 0)           AS cheap_count,
                  COALESCE(SUM(model_category = 'frontier'), 0)        AS frontier_count,
                  COALESCE(SUM(decision_mode = 'pinned'), 0)           AS pinned_count,
                  COALESCE(SUM(CASE WHEN decision_mode = 'pinned'
                                    THEN cost_usd ELSE 0 END), 0)      AS pinned_spend,
                  COALESCE(SUM(CASE WHEN decision_mode != 'pinned'
                                    THEN cost_usd ELSE 0 END), 0)      AS routed_spend,
                  COALESCE(SUM(CASE WHEN decision_mode != 'pinned'
                                    THEN frontier_baseline_usd ELSE 0 END), 0) AS routed_baseline,
                  COALESCE(SUM(decision_mode != 'pinned'), 0)          AS routed_count,
                  COALESCE(SUM(status != 'ok'), 0)                     AS error_count
                FROM requests{where}""",
            params,
        ).fetchone()

    latencies = [r["latency_ms"] for r in lat_rows]
    if latencies:
        mid = len(latencies) // 2
        median_latency = (
            latencies[mid]
            if len(latencies) % 2
            else round((latencies[mid - 1] + latencies[mid]) / 2)
        )
    else:
        median_latency = 0

    d = dict(agg)
    routed_count = d["routed_count"] or 0
    savings = d["routed_baseline"] - d["routed_spend"]
    decided = (d["cheap_count"] or 0) + (d["frontier_count"] or 0)

    return {
        "total_requests": d["total_requests"],
        "total_spend_usd": round(d["total_spend"], 8),
        "routed_spend_usd": round(d["routed_spend"], 8),
        "pinned_spend_usd": round(d["pinned_spend"], 8),
        "routed_frontier_baseline_usd": round(d["routed_baseline"], 8),
        "estimated_savings_usd": round(savings, 8),
        "savings_pct": round(100 * savings / d["routed_baseline"], 2)
        if d["routed_baseline"] > 0
        else 0.0,
        "routed_count": routed_count,
        "pinned_count": d["pinned_count"],
        "cheap_count": d["cheap_count"],
        "frontier_count": d["frontier_count"],
        "cheap_pct": round(100 * d["cheap_count"] / decided, 1) if decided else 0.0,
        "frontier_pct": round(100 * d["frontier_count"] / decided, 1) if decided else 0.0,
        "avg_latency_ms": round(d["total_latency"] / d["total_requests"]) if d["total_requests"] else 0,
        "median_latency_ms": median_latency,
        "reason_codes": [
            {"reason_code": r["reason_code"], "category": r["model_category"], "count": r["n"]}
            for r in codes
        ],
        "input_tokens": d["input_tokens"],
        "output_tokens": d["output_tokens"],
        "error_count": d["error_count"],
    }


def attributions(db_path: str | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT attribution_id,
                      COUNT(*) AS requests,
                      COALESCE(SUM(cost_usd), 0) AS spend_usd,
                      COALESCE(SUM(CASE WHEN decision_mode != 'pinned'
                             THEN frontier_baseline_usd - cost_usd ELSE 0 END), 0) AS savings_usd
               FROM requests GROUP BY attribution_id ORDER BY spend_usd DESC"""
        ).fetchall()
    return [
        {
            "attribution_id": r["attribution_id"],
            "requests": r["requests"],
            "spend_usd": round(r["spend_usd"], 8),
            "savings_usd": round(r["savings_usd"], 8),
        }
        for r in rows
    ]
