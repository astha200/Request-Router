import type { RequestRow } from "../types";

export function RowBadges({ row }: { row: RequestRow }) {
  return (
    <div className="badges">
      <span className={`badge ${row.model_category}`}>{row.model_category}</span>
      {row.decision_mode === "pinned" && <span className="badge pinned">Pinned</span>}
      {row.decision_mode === "routed_with_override" && (
        <span className="badge override">Override</span>
      )}
      {row.tokens_estimated && (
        <span className="badge est" title="Upstream returned no usage block; tokens estimated">
          Est
        </span>
      )}
      {row.status !== "ok" && (
        <span className="badge err" title={row.error_message ?? ""}>
          {row.status === "timeout" ? "Timeout" : "Error"}
        </span>
      )}
    </div>
  );
}
