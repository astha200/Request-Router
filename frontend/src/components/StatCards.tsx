import { ms, num, pct, usd } from "../format";
import type { Summary } from "../types";

export function StatCards({ s }: { s: Summary }) {
  const cheap = s.cheap_pct;
  const frontier = s.frontier_pct;
  return (
    <div className="cards">
      <div className="card">
        <div className="label">Total spend</div>
        <div className="value">{usd(s.total_spend_usd)}</div>
        <div className="sub">
          {num(s.total_requests)} requests · {num(s.input_tokens + s.output_tokens)} tokens
        </div>
      </div>

      <div className="card">
        <div className="label">Saved vs all-frontier</div>
        <div className="value good">{usd(s.estimated_savings_usd)}</div>
        <div className="sub">
          {pct(s.savings_pct)} below {usd(s.routed_frontier_baseline_usd)} baseline
        </div>
      </div>

      <div className="card">
        <div className="label">Route mix</div>
        <div className="value">{pct(cheap)}</div>
        <div className="mixbar">
          <i className="c" style={{ width: `${cheap}%` }} />
          <i className="f" style={{ width: `${frontier}%` }} />
        </div>
        <div className="mixlegend">
          <span>
            <i className="swatch" style={{ background: "var(--cheap)" }} />
            cheap {s.cheap_count}
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--frontier)" }} />
            frontier {s.frontier_count}
          </span>
        </div>
      </div>

      <div className="card">
        {/* Median, not mean: cheap calls run ~2s and frontier ~12s, so a mean
            describes no request that actually happened. */}
        <div className="label">Median latency</div>
        <div className="value">{ms(s.median_latency_ms)}</div>
        <div className="sub">
          mean {ms(s.avg_latency_ms)} · {num(s.routed_count)} routed · {num(s.pinned_count)} pinned
          {s.error_count > 0 && ` · ${num(s.error_count)} errors`}
        </div>
      </div>
    </div>
  );
}
