/**
 * The one formatter (UX_DESIGN: no per-component formatters). Grows into paisa→₹,
 * pct and sentiment in Phase 8; for now the counts, percentages and times the data
 * panels need.
 */
const intFormat = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function formatInt(n: number): string {
  return intFormat.format(n);
}

export function formatPct(n: number, digits = 1): string {
  return `${n.toFixed(digits)}%`;
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

const istTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatIstTime(iso: string): string {
  return `${istTime.format(new Date(iso))} IST`;
}

const istDateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function formatIstDateTime(iso: string): string {
  return `${istDateTime.format(new Date(iso))} IST`;
}
