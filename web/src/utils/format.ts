const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const num1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

/** ₹ with Indian grouping; compact (₹12.4 L / ₹1.2 Cr) when `compact`. */
export function inr(v: number | null | undefined, compact = false): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (compact && a >= 1e7) return `${sign}₹${num1.format(a / 1e7)} Cr`;
  if (compact && a >= 1e5) return `${sign}₹${num1.format(a / 1e5)} L`;
  return `${sign}₹${inr0.format(a)}`;
}

export function kg(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  return a >= 10000 ? `${sign}${num1.format(a / 1000)} t` : `${sign}${inr0.format(a)} kg`;
}

/** Fraction 0–1 → "42.3%". */
export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

/** Signed percentage-point difference of two fractions. */
export function pp(v: number, digits = 1): string {
  const s = Math.abs(v * 100).toFixed(digits);
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${s} pp`;
}

/** Prefix a formatted magnitude with an explicit sign. */
export function signed(v: number, fmt: (x: number) => string): string {
  if (v === 0) return `±${fmt(0)}`;
  return `${v > 0 ? "+" : "−"}${fmt(Math.abs(v))}`;
}

export function clock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function day(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export const PLANNER_LABEL: Record<string, string> = {
  freshroute: "FreshRoute",
  ours_no_calendar: "FreshRoute − calendar",
  fefo_greedy: "B3 · FEFO greedy",
  chase_price: "B2 · chase price",
  fifo_nearest: "B1 · FIFO nearest",
};

/** Fixed categorical order — colour follows the planner, never its rank. */
export const PLANNER_ORDER = ["freshroute", "fefo_greedy", "chase_price", "ours_no_calendar", "fifo_nearest"];

export function titleCase(id: string): string {
  return id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
