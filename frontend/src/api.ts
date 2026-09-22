import type { Attribution, RequestRow, Summary } from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

const q = (attribution: string) =>
  attribution === "all" ? "" : `?attribution_id=${encodeURIComponent(attribution)}`;

export const fetchSummary = (a: string) => get<Summary>(`/admin/summary${q(a)}`);
export const fetchRequests = (a: string) =>
  get<{ data: RequestRow[] }>(
    `/admin/requests${q(a)}${q(a) ? "&" : "?"}limit=100`,
  ).then((r) => r.data);
export const fetchAttributions = () =>
  get<{ data: Attribution[] }>("/admin/attributions").then((r) => r.data);
