import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dm, dmy, inr, inrShort } from "../lib/format";
import type { CalendarPage, CalendarDay } from "../lib/types";
import { Card, CardHead, cx, Empty, ErrorBox, Loading, Metric, PageHead, Segmented } from "../components/ui";

const WEEK = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function weeks(cal: CalendarDay[], start: string, end: string) {
  const byDay = new Map(cal.map((d) => [d.day, d]));
  const s = new Date(start + "T00:00:00Z");
  const first = new Date(s);
  first.setUTCDate(s.getUTCDate() - s.getUTCDay());
  const e = new Date(end + "T00:00:00Z");
  const out: { iso: string; day: CalendarDay | undefined; inRange: boolean }[][] = [];
  for (let d = new Date(first); d <= e || d.getUTCDay() !== 0; d.setUTCDate(d.getUTCDate() + 1)) {
    if (d.getUTCDay() === 0) out.push([]);
    const iso = d.toISOString().slice(0, 10);
    out[out.length - 1].push({ iso, day: byDay.get(iso), inRange: iso >= start && iso <= end });
  }
  return out;
}

export default function CalendarView() {
  const { book } = useSession();
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["calendar", book, days], queryFn: () => api<CalendarPage>(`/api/ui/calendar?days=${days}`) });
  const d = q.data;
  const active = d?.calendar.filter((c) => c.money_in.length || c.money_out.length) ?? [];
  const end = d ? (d.running.at(-1)?.day ?? d.today) : "";
  return (
    <>
      <PageHead eyebrow="Planning" title="Cash calendar"
        sub="Expected interest (and principal at maturity), assuming no other payments, plus your planned items. Unpaid past dues show on today."
        actions={<Segmented label="Window" value={days} onChange={setDays} options={[[30, "30 days"], [60, "60 days"], [90, "90 days"]]} />} />
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Expected collections" icon="down" tone="green" value={inr(d.totals.in_paise, false)} sub={`Next ${days} days incl. arrears`} />
            <Metric label="Expected payments" icon="up" tone="amber" value={inr(d.totals.out_paise, false)} sub={`Next ${days} days incl. arrears`} />
            <Metric label="Projected net" icon="chart" tone="teal" value={inr(d.totals.net_paise, false)} sub="Collections − payments" />
            <Metric label="Lowest balance" icon="clock" tone={d.totals.low_paise < 0 ? "red" : "violet"} value={inr(d.totals.low_paise, false)}
              sub={d.totals.low_day ? `Short on ${dmy(d.totals.low_day)}` : `Starting from ${inr(d.opening_paise, false)} in your accounts`} />
          </div>
          <Card className="mb-4">
            <CardHead title="Month view" sub="Green: money in · red: money out" />
            <div className="grid grid-cols-7 gap-1 sm:gap-1.5">
              {WEEK.map((w) => <div key={w} className="py-1.5 text-center text-[10px] font-bold text-muted">{w}</div>)}
              {weeks(d.calendar, d.today, end).flat().map(({ iso, day, inRange }) => (
                <div key={iso} className={cx("flex min-h-[64px] flex-col gap-1 rounded-lg border p-1 sm:min-h-[96px] sm:p-2",
                  !inRange ? "border-transparent opacity-40" : iso === d.today ? "border-teal ring-1 ring-teal" : "border-line bg-card")}>
                  <b className="text-[10px] sm:text-xs">{iso.endsWith("-01") || iso === d.today ? dm(iso) : Number(iso.slice(8))}</b>
                  {day?.money_in.length ? <span className="truncate rounded bg-greensoft px-1 py-0.5 text-[9px] font-bold text-green sm:text-[10px]">+{inrShort(day.money_in.reduce((a, i) => a + i.amount_paise, 0))}</span> : null}
                  {day?.money_out.length ? <span className="truncate rounded bg-redsoft px-1 py-0.5 text-[9px] font-bold text-redink sm:text-[10px]">−{inrShort(day.money_out.reduce((a, i) => a + i.amount_paise, 0))}</span> : null}
                </div>
              ))}
            </div>
          </Card>
          <Card>
            <CardHead title="Scheduled" sub={`Starts from ${inr(d.opening_paise, false)} in your accounts`} />
            {active.length ? active.map((c) => (
              <div key={c.day} className="border-t border-line py-2 first:border-0">
                {[...c.money_in.map((i) => ({ i, sign: 1 })), ...c.money_out.map((i) => ({ i, sign: -1 }))].map(({ i, sign }) => (
                  <Link key={`${i.loan_id}-${i.party}-${sign}-${i.due_date}`} to={i.loan_id ? `/loans/${i.loan_id}` : "/cash"} className="flex items-center gap-3 py-2 hover:opacity-80">
                    <span className={cx("grid size-9 shrink-0 place-items-center rounded-lg text-xs font-extrabold", sign > 0 ? "bg-tealsoft text-teal" : "bg-redsoft text-redink")}>{Number(c.day.slice(8))}</span>
                    <span className="min-w-0 flex-1"><b className="block truncate text-xs">{i.party}</b>
                      <small className="block truncate text-[10px] text-muted">{i.loan_id ? <>{sign > 0 ? "Collection" : "Payment"} · LN-{i.loan_id} · </> : "Planned · "}{i.book === "BUSINESS" ? "Business" : "Personal"}{i.count > 1 ? ` · ${i.count} unpaid dues` : ""}</small></span>
                    <span className="text-right"><b className={cx("num block text-xs", sign > 0 ? "text-green" : "text-redink")}>{sign > 0 ? "+" : "−"}{inr(i.amount_paise)}</b>
                      <small className="text-[10px] text-muted">running {inr(c.running_paise, false)}</small></span>
                  </Link>
                ))}
              </div>
            )) : <Empty icon="calendar" title="Nothing scheduled" text={`No money in or out in the next ${days} days.`} />}
          </Card>
        </>
      )}
    </>
  );
}
