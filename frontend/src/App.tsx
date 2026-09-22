import { useCallback, useEffect, useState } from "react";

import { fetchAttributions, fetchRequests, fetchSummary } from "./api";
import { RequestTable } from "./components/RequestTable";
import { StatCards } from "./components/StatCards";
import { usd } from "./format";
import type { Attribution, RequestRow, Summary } from "./types";

const POLL_MS = 3000;

export default function App() {
  const [attribution, setAttribution] = useState("all");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [people, setPeople] = useState<Attribution[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, r, a] = await Promise.all([
        fetchSummary(attribution),
        fetchRequests(attribution),
        fetchAttributions(),
      ]);
      setSummary(s);
      setRows(r);
      setPeople(a);
      setError(null);
      setLive(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLive(false);
    } finally {
      setLoading(false);
    }
  }, [attribution]);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="wrap">
      <header className="head">
        <div>
          <h1 className="title">Fireworks Router · Cost & Routing Console</h1>
          <p className="subtitle">
            {summary ? (
              <>
                <code>{summary.models.virtual}</code> routes between{" "}
                <code>{summary.models.cheap.name}</code> (${summary.models.cheap.input_per_1m}/$
                {summary.models.cheap.output_per_1m} per 1M) and{" "}
                <code>{summary.models.frontier.name}</code> (${summary.models.frontier.input_per_1m}/$
                {summary.models.frontier.output_per_1m} per 1M).
                {summary.policy_version && (
                  <> Policy <code>{summary.policy_version}</code>.</>
                )}
              </>
            ) : (
              "Loading model configuration…"
            )}
          </p>
        </div>
        <div className="controls">
          <select
            value={attribution}
            onChange={(e) => setAttribution(e.target.value)}
            aria-label="Filter by attribution id"
          >
            <option value="all">All users ({people.reduce((n, p) => n + p.requests, 0)})</option>
            {people.map((p) => (
              <option key={p.attribution_id} value={p.attribution_id}>
                {p.attribution_id} — {usd(p.spend_usd)} ({p.requests})
              </option>
            ))}
          </select>
          <span className="live">
            <i className={`dot ${live ? "" : "stale"}`} />
            {live ? "Live" : "Offline"}
          </span>
        </div>
      </header>

      {error && (
        <div className="tablecard">
          <div className="state error">
            <h3>Can't reach the proxy</h3>
            <p>{error}</p>
            <p>Start the backend, then this page will recover on its own.</p>
            <pre>uvicorn app.main:app --reload --port 8000</pre>
          </div>
        </div>
      )}

      {!error && loading && <LoadingState />}

      {!error && !loading && summary && (
        <>
          <StatCards s={summary} />

          <div className="note">
            <b>How savings are computed.</b> Baseline = what the{" "}
            <b>{summary.routed_count} routed</b> requests would have cost at{" "}
            {summary.models.frontier.name} rates, assuming identical token counts. Savings ={" "}
            {usd(summary.routed_frontier_baseline_usd)} baseline −{" "}
            {usd(summary.routed_spend_usd)} actual.{" "}
            {summary.pinned_count > 0 ? (
              <>
                The <b>{summary.pinned_count} pinned</b> request
                {summary.pinned_count === 1 ? " is" : "s are"} excluded (
                {usd(summary.pinned_spend_usd)}) — the developer chose that model, not the
                router, so we don't claim credit. Pinned spend still counts toward total spend.
              </>
            ) : (
              <>No pinned requests in this view.</>
            )}
          </div>

          <h2 className="section-title">
            Recent requests
            <span className="count">
              {rows.length === 0 ? "none yet" : `showing ${rows.length}`}
              {attribution !== "all" && ` · filtered to ${attribution}`}
            </span>
          </h2>

          {rows.length === 0 ? <EmptyState /> : <RequestTable rows={rows} />}
        </>
      )}
    </div>
  );
}

function LoadingState() {
  return (
    <>
      <div className="cards">
        {[0, 1, 2, 3].map((i) => (
          <div className="card" key={i}>
            <div className="skeleton" style={{ width: "45%" }} />
            <div className="skeleton" style={{ width: "70%", height: 24, marginTop: 12 }} />
            <div className="skeleton" style={{ width: "60%", marginTop: 10 }} />
          </div>
        ))}
      </div>
      <div className="tablecard">
        <div className="state">
          <p>Loading traffic…</p>
        </div>
      </div>
    </>
  );
}

function EmptyState() {
  return (
    <div className="tablecard">
      <div className="state">
        <h3>No requests yet</h3>
        <p>The console fills in as soon as traffic reaches the proxy.</p>
        <p>Generate a representative mix:</p>
        <pre>python scripts/demo_traffic.py</pre>
      </div>
    </div>
  );
}
