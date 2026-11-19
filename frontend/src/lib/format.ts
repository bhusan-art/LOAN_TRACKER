// Display helpers. Money arrives from the server as integer paise; nothing here calculates
// money, it only formats it (same rules as loan_tracker/web/formatting.py).

export function indianGroup(n: number): string {
  const s = String(n);
  if (s.length <= 3) return s;
  let head = s.slice(0, -3);
  const tail = s.slice(-3);
  const parts: string[] = [];
  while (head.length > 2) {
    parts.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) parts.unshift(head);
  return parts.join(",") + "," + tail;
}

/** 10000050 -> "₹1,00,000.50"; decimals=false -> "₹1,00,000". */
export function inr(paise: number | null | undefined, decimals = true): string {
  if (paise === null || paise === undefined) return "—";
  const sign = paise < 0 ? "−" : "";
  const p = Math.abs(Math.trunc(paise));
  let out = `${sign}₹${indianGroup(Math.floor(p / 100))}`;
  if (decimals) out += "." + String(p % 100).padStart(2, "0");
  return out;
}

/** Short form for chart axes: 150000000 -> "₹15L", 2500000 -> "₹25k". */
export function inrShort(paise: number): string {
  const r = Math.round(paise / 100);
  const a = Math.abs(r);
  const sign = r < 0 ? "−" : "";
  if (a >= 1_00_00_000) return `${sign}₹${trim(a / 1_00_00_000)}Cr`;
  if (a >= 1_00_000) return `${sign}₹${trim(a / 1_00_000)}L`;
  if (a >= 1000) return `${sign}₹${trim(a / 1000)}k`;
  return `${sign}₹${a}`;
}

function trim(v: number): string {
  return v >= 10 ? String(Math.round(v)) : String(Math.round(v * 10) / 10);
}

/** Value for an amount input: 150000 -> "1500.00". */
export function rupeesInput(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return "";
  return `${Math.floor(paise / 100)}.${String(Math.abs(paise) % 100).padStart(2, "0")}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** ISO date "2026-11-05" (or a datetime) -> "05 Nov 2026" with no timezone shift. */
export function dmy(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d} ${MONTHS[Number(m) - 1]} ${y}`;
}

/** "05 Nov 26": compact date for tables on a phone. */
export function dshort(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d} ${MONTHS[Number(m) - 1]} ${y.slice(2)}`;
}

/** "05 Nov" */
export function dm(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [, m, d] = iso.slice(0, 10).split("-");
  return `${d} ${MONTHS[Number(m) - 1]}`;
}

/** A timestamp in the owner's time zone: "18 Nov 2026, 14:05". */
export function when(iso: string | null | undefined, tz = "Asia/Kolkata"): string {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(dt);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("day")} ${get("month")} ${get("year")}, ${get("hour")}:${get("minute")}`;
}

/** "2" -> "2%", rate text from the server. */
export function pct(rate: string | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${rate}%`;
}

export function rateText(rate: string, unit: string): string {
  return `${rate}% ${unit === "PER_YEAR" ? "a year" : "a month"}`;
}

/** Days between two ISO dates (b - a). */
export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b.slice(0, 10)) - Date.parse(a.slice(0, 10))) / 86_400_000);
}

export function addDaysIso(iso: string, n: number): string {
  const d = new Date(iso.slice(0, 10) + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function plural(n: number, one: string, many = one + "s"): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function initials(name: string): string {
  const w = name.replace(/\(.*?\)/g, "").trim().split(/\s+/).filter(Boolean);
  return ((w[0]?.[0] ?? "") + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase() || "?";
}

export function kb(bytes: number): string {
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Change against a previous value, for "vs last month" lines. null when there is nothing to compare. */
export function change(now: number, before: number): { pct: number; up: boolean } | null {
  if (!before) return null;
  const v = ((now - before) / Math.abs(before)) * 100;
  return { pct: Math.round(v * 10) / 10, up: v >= 0 };
}

export const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "Active", OVERDUE: "Overdue", MATURED: "Matured", CLOSED: "Closed", SETTLED: "Settled",
  WRITTEN_OFF: "Written off", PAID: "Paid", PENDING: "Pending", PARTIAL: "Part paid", WAIVED: "Waived", DUE: "Due",
};

export const STATUS_TONE: Record<string, Tone> = {
  ACTIVE: "teal", OVERDUE: "red", MATURED: "amber", CLOSED: "green", SETTLED: "gray", WRITTEN_OFF: "gray",
  PAID: "green", PENDING: "amber", PARTIAL: "amber", WAIVED: "gray", DUE: "amber",
};

export type Tone = "teal" | "green" | "amber" | "red" | "violet" | "blue" | "gray";
