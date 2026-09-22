import { ms, num, shortModel, timeOfDay, usd } from "../format";
import type { RequestRow } from "../types";
import { RowBadges } from "./Badges";

export function RequestTable({ rows }: { rows: RequestRow[] }) {
  return (
    <div className="tablecard">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>User</th>
            <th>Requested → served</th>
            <th>Decision</th>
            <th className="r">Tokens</th>
            <th className="r">Cost</th>
            <th className="r">Latency</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <RequestRows key={r.id} row={r} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RequestRows({ row }: { row: RequestRow }) {
  return (
    <>
      <tr className="main">
        <td className="time">
          {timeOfDay(row.created_at)}
        </td>
        <td className="user">{row.attribution_id}</td>
        <td className="mono">
          {shortModel(row.requested_model)}
          <span className="arrow">→</span>
          {shortModel(row.selected_model)}
        </td>
        <td>
          <RowBadges row={row} />
        </td>
        <td className="r dimtext">
          {num(row.input_tokens)}
          <span className="arrow">/</span>
          {num(row.output_tokens)}
        </td>
        <td className="r">{usd(row.cost_usd)}</td>
        <td className="r dimtext">{ms(row.latency_ms)}</td>
      </tr>
      {/* The reason is the product. It gets its own full-width line rather than
          being truncated into a narrow cell. */}
      <tr className="reasonrow">
        <td colSpan={7}>
          <div className={`reason ${row.model_category}`}>{row.route_reason}</div>
        </td>
      </tr>
    </>
  );
}
