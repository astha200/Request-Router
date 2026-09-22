export interface ModelInfo {
  id: string; name: string; input_per_1m: number; output_per_1m: number;
}
export interface Summary {
  total_requests: number;
  total_spend_usd: number;
  routed_spend_usd: number;
  pinned_spend_usd: number;
  routed_frontier_baseline_usd: number;
  estimated_savings_usd: number;
  savings_pct: number;
  routed_count: number;
  pinned_count: number;
  cheap_count: number;
  frontier_count: number;
  cheap_pct: number;
  frontier_pct: number;
  avg_latency_ms: number;
  median_latency_ms: number;
  reason_codes: { reason_code: string; category: string; count: number }[];
  input_tokens: number;
  output_tokens: number;
  error_count: number;
  models: { virtual: string; cheap: ModelInfo; frontier: ModelInfo };
  policy_version?: string;
}
export interface RouteSignal { name: string; weight: number; detail: string }
export interface RequestRow {
  id: string;
  created_at: string;
  attribution_id: string;
  requested_model: string;
  selected_model: string;
  model_category: "cheap" | "frontier";
  decision_mode: "routed" | "pinned" | "routed_with_override";
  route_reason: string;
  reason_code: string;
  policy_version: string;
  route_signals: RouteSignal[];
  route_score: number | null;
  input_tokens: number;
  output_tokens: number;
  tokens_estimated: boolean;
  cost_usd: number;
  frontier_baseline_usd: number;
  latency_ms: number;
  streamed: boolean;
  status: string;
  error_message: string | null;
}
export interface Attribution {
  attribution_id: string; requests: number; spend_usd: number; savings_usd: number;
}
