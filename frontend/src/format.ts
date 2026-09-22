/** Spend here is fractions of a cent; ordinary currency formatting rounds it to $0.00. */
export function usd(value: number): string {
  if (value === 0) return "$0.00";
  if (Math.abs(value) < 0.01) return `$${value.toFixed(6)}`;
  if (Math.abs(value) < 1) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
export const pct = (v: number) => `${v.toFixed(1)}%`;
export const ms = (v: number) => `${Math.round(v).toLocaleString("en-US")}ms`;
export const num = (v: number) => v.toLocaleString("en-US");

export function timeOfDay(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleTimeString("en-US", { hour12: false });
}
export const shortModel = (id: string) => id.split("/").pop() ?? id;
