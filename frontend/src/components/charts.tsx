import { useState } from "react";
import { dm, inr, inrShort, STATUS_LABEL } from "../lib/format";

/** "Nice" top of an axis: 18,700 -> 20,000. */
function niceTop(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const n = v / mag;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return step * mag;
}

/** Interest earned vs interest cost per month (design: emerald area + amber line). */
export function InterestChart({ data }: { data: { label: string; earned_paise: number; cost_paise: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 700, H = 190;
  const top = niceTop(Math.max(1, ...data.map((d) => Math.max(d.earned_paise, d.cost_paise))));
  const n = data.length;
  const x = (i: number) => (n <= 1 ? W / 2 : (i * W) / (n - 1));
  const y = (v: number) => H - (v / top) * (H - 10);
  const line = (key: "earned_paise" | "cost_paise") => data.map((d, i) => `${i ? "L" : "M"}${x(i)} ${y(d[key])}`).join(" ");
  const ticks = [1, 0.75, 0.5, 0.25, 0].map((f) => top * f);
  return (
    <div className="relative pb-6 pl-11" onMouseLeave={() => setHover(null)}>
      <div className="absolute top-[-5px] bottom-[18px] left-0 flex flex-col justify-between text-[9px] text-muted">
        {ticks.map((t) => <span key={t}>{inrShort(t)}</span>)}
      </div>
      <div className="relative h-[190px]">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-full w-full overflow-visible" role="img"
          aria-label="Interest earned and interest cost for the last 12 months">
          <defs>
            <linearGradient id="earnedArea" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="var(--c-green)" stopOpacity=".24" />
              <stop offset="1" stopColor="var(--c-green)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((t) => <line key={t} x1="0" x2={W} y1={y(t)} y2={y(t)} stroke="var(--c-line)" vectorEffect="non-scaling-stroke" />)}
          {n > 0 && <path d={`${line("earned_paise")} L${x(n - 1)} ${H} L${x(0)} ${H}Z`} fill="url(#earnedArea)" />}
          <path d={line("earned_paise")} fill="none" stroke="var(--c-green)" strokeWidth="2.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          <path d={line("cost_paise")} fill="none" stroke="var(--c-amber)" strokeWidth="2.2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1="0" y2={H} stroke="var(--c-muted)" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />}
        </svg>
        <div className="absolute inset-0 flex">
          {data.map((d, i) => (
            <div key={d.label} className="h-full flex-1" onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={-1} />
          ))}
        </div>
        {hover !== null && data[hover] && (
          <div className="pointer-events-none absolute top-1 z-10 rounded-lg bg-deep px-3 py-2 text-[11px] text-white shadow-lg"
            style={{ left: `clamp(0px, calc(${(hover / Math.max(1, n - 1)) * 100}% - 70px), calc(100% - 150px))` }}>
            <b className="block">{data[hover].label}</b>
            <span className="block">Earned {inr(data[hover].earned_paise, false)}</span>
            <span className="block">Cost {inr(data[hover].cost_paise, false)}</span>
          </div>
        )}
      </div>
      <div className="absolute right-0 bottom-0 left-11 flex justify-between text-[9px] text-muted">
        {data.map((d, i) => <span key={d.label} className={i % 2 && n > 8 ? "hidden sm:inline" : ""}>{d.label.split(" ")[0]}</span>)}
      </div>
    </div>
  );
}

const DONUT_COLOURS: [string, string][] = [
  ["ACTIVE", "var(--c-teal)"], ["OVERDUE", "var(--c-red)"], ["MATURED", "var(--c-amber)"],
  ["CLOSED", "var(--c-green)"], ["SETTLED", "#9fb3b5"], ["WRITTEN_OFF", "#6f8587"],
];

export function Donut({ counts, centre, centreLabel }: { counts: Record<string, number>; centre: number; centreLabel: string }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  let acc = 0;
  const parts = DONUT_COLOURS.filter(([k]) => counts[k]).map(([k, c]) => {
    const from = (acc / total) * 100;
    acc += counts[k];
    return `${c} ${from}% ${(acc / total) * 100}%`;
  });
  const bg = total ? `conic-gradient(${parts.join(",")})` : "conic-gradient(var(--c-line) 0 100%)";
  return (
    <div className="flex flex-wrap items-center justify-around gap-6 py-3">
      <div className="grid size-[132px] shrink-0 place-items-center rounded-full" style={{ background: bg }}>
        <div className="grid size-[88px] place-content-center justify-items-center rounded-full bg-card">
          <b className="font-display text-2xl font-extrabold">{centre}</b>
          <small className="text-[10px] text-muted">{centreLabel}</small>
        </div>
      </div>
      <div className="min-w-[150px] flex-1 text-[11px]">
        {DONUT_COLOURS.filter(([k]) => counts[k]).map(([k, c]) => (
          <div key={k} className="flex items-center border-b border-line py-2.5">
            <i className="mr-2 inline-block size-2 rounded-full" style={{ background: c }} />{STATUS_LABEL[k] ?? k}
            <b className="ml-auto">{counts[k]}</b>
          </div>
        ))}
        {!total && <p className="text-muted">No loans yet.</p>}
      </div>
    </div>
  );
}

/** Running cash balance per day, with the safety buffer and zero lines. */
export function BalanceChart({ series, buffer }: {
  series: { name: string; colour: string; points: { day: string; running_paise: number }[]; dashed?: boolean }[];
  buffer: number;
}) {
  const all = series.flatMap((s) => s.points.map((p) => p.running_paise));
  if (!all.length) return null;
  const lo = Math.min(0, buffer, ...all), hi = Math.max(buffer, ...all, 1);
  const W = 700, H = 200, pad = 6;
  // x by calendar date, so series with different numbers of points line up; each runs to the end
  const days = [...new Set(series.flatMap((s) => s.points.map((p) => p.day)))].sort();
  const n = days.length;
  const t0 = Date.parse(days[0]), t1 = Date.parse(days[n - 1]);
  const x = (day: string) => (t1 === t0 ? 0 : ((Date.parse(day) - t0) / (t1 - t0)) * W);
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * pad);
  const path = (pts: { day: string; running_paise: number }[]) =>
    pts.map((p, i) => (i ? `H${x(p.day)} V${y(p.running_paise)}` : `M${x(p.day)} ${y(p.running_paise)}`)).join(" ") + ` H${W}`;
  return (
    <div>
      <div className="relative h-[200px] pl-12">
        <div className="absolute top-0 bottom-0 left-0 flex flex-col justify-between text-[9px] text-muted">
          <span>{inrShort(hi)}</span><span>{inrShort((hi + lo) / 2)}</span><span>{inrShort(lo)}</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-full w-full" role="img" aria-label="Expected cash balance">
          {lo < 0 && <line x1="0" x2={W} y1={y(0)} y2={y(0)} stroke="var(--c-red)" strokeWidth="1" vectorEffect="non-scaling-stroke" />}
          {buffer > 0 && <line x1="0" x2={W} y1={y(buffer)} y2={y(buffer)} stroke="var(--c-amber)" strokeDasharray="5 4" vectorEffect="non-scaling-stroke" />}
          {series.map((s) => (
            <path key={s.name} d={path(s.points)} fill="none" stroke={s.colour} strokeWidth="2.4" strokeDasharray={s.dashed ? "6 4" : undefined}
              vectorEffect="non-scaling-stroke" />
          ))}
        </svg>
      </div>
      <div className="mt-1.5 flex justify-between pl-12 text-[9px] text-muted">
        <span>{dm(days[0])}</span><span>{dm(days[Math.floor(n / 2)])}</span><span>{dm(days[n - 1])}</span>
      </div>
    </div>
  );
}
