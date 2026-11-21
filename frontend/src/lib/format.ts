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

/** Integer paisa → rupees. The display edge only: money stays integer paisa everywhere else. */
export function paisaToRupees(paisa: number): number {
  if (!Number.isInteger(paisa)) throw new Error(`paisa must be an integer, got ${paisa}`);
  return paisa / 100;
}

const rupeeFormat = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Integer paisa → "₹1,234.50" (display only). */
export function formatRupees(paisa: number): string {
  return rupeeFormat.format(paisaToRupees(paisa));
}

const priceFormat = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Integer paisa → "25,514.30" without a currency sign (index points, chart axes). */
export function formatPrice(paisa: number): string {
  return priceFormat.format(paisaToRupees(paisa));
}

const istDate = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  year: "numeric",
});

/** Unix seconds → "19 Nov 2026" (IST trading date). */
export function formatIstDate(unixSeconds: number): string {
  return istDate.format(new Date(unixSeconds * 1000));
}
