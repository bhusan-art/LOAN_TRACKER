import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { change, dmy, inr, plural } from "../lib/format";
import type { Home, TodayItem } from "../lib/types";
import { Icon } from "../components/Icon";
import { Alert, Card, CardHead, cx, Empty, ErrorBox, Loading, Metric, PageHead, PersonCell, Row, Tag } from "../components/ui";
import { Donut, InterestChart } from "../components/charts";
import { useRecordPayment } from "../components/Shell";

function Trend({ now, before, invert }: { now: number; before: number; invert?: boolean }) {
  const c = change(now, before);
  if (!c || c.pct === 0) return <span className="text-muted">No change vs last month</span>;
  const good = invert ? !c.up : c.up;
  return (
    <span className={good ? "text-green" : "text-redink"}>
      {c.up ? "↑" : "↓"} {Math.abs(c.pct)}% <span className="text-muted">vs last month</span>
    </span>
  );
}

function DueRow({ item, money }: { item: TodayItem; money: "in" | "out" }) {
  const late = item.status === "OVERDUE";
  return (
    <Link to={`/loans/${item.loan_id}`} className="flex items-center justify-between gap-3 border-t border-line py-3.5 first:border-0 hover:opacity-90">
      <PersonCell name={item.party} tone={money === "in" ? "green" : "amber"}
        sub={`${item.book === "BUSINESS" ? "Business" : "Personal"} · Due ${dmy(item.due_date)}${late ? ` · ${plural(item.overdue_days, "day")} overdue` : ""}${item.count > 1 ? ` · ${item.count} dues` : ""}`} />
      <div className="shrink-0 text-right">
        <b className="num text-xs">{inr(item.amount_paise)}</b>
        <small className="mt-1 block">{late ? <Tag tone="red">Overdue</Tag> : <Tag tone="amber">Due soon</Tag>}</small>
      </div>
    </Link>
  );
}

export default function Overview() {
  const { session, canEdit } = useSession();
  const pay = useRecordPayment();
  const q = useQuery({ queryKey: ["home", session?.book], queryFn: () => api<Home>("/api/ui/home") });
  const d = q.data;
  return (
    <>
      <PageHead eyebrow="Financial workspace" title="Overview"
        sub={<>A clear view of money lent, borrowed and due. <span className="text-soft">{dmy(session?.today)}</span></>}
        actions={canEdit && (
          <>
            <Link to="/loans/new?direction=GIVEN" className="btn btn-primary"><Icon name="plus" className="size-4" />New loan</Link>
            <button type="button" className="btn" onClick={() => pay()}><Icon name="plus" className="size-4" />Record payment</button>
          </>
        )} />
      {q.isLoading && <Loading />}
      {q.error && <ErrorBox error={q.error} />}
      {d && (
        <>
          {d.warning_text.length > 0 && (
            <Card className="mb-4 border-amber/50">
              <CardHead title="Needs attention" icon="alert" iconTone="amber" right={<Tag tone="amber">{d.warning_text.length}</Tag>} />
              <div className="grid gap-2">{d.warning_text.map((w) => <Alert key={w}>{w}</Alert>)}</div>
            </Card>
          )}

          <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Total lent out" icon="up" tone="teal" value={inr(d.lent_out_paise, false)} sub="Outstanding principal"
              foot={<Trend now={d.lent_out_paise} before={d.previous.lent_out_paise} />} />
            <Metric label="Total borrowed" icon="down" tone="amber" value={inr(d.borrowed_paise, false)} sub="Outstanding liabilities"
              foot={d.borrowed_paise ? <Trend now={d.borrowed_paise} before={d.previous.borrowed_paise} invert /> : <span className="text-muted">No active borrowing</span>} />
            <Metric label="Own capital deployed" icon="diamond" tone="violet" value={inr(d.own_capital_deployed_paise, false)}
              sub="After linked borrowed funds"
              foot={<span className="text-muted">{d.lent_out_paise ? `${Math.round((d.own_capital_deployed_paise * 100) / d.lent_out_paise)}% own capital` : "Nothing lent out"}</span>} />
            <Metric label="Net interest · this month" icon="percent" tone="blue" value={inr(d.net_interest_income.month, false)}
              sub={`Earned ${inr(d.interest_earned.month, false)} · cost ${inr(d.interest_cost.month, false)}`}
              foot={<Trend now={d.net_interest_income.month} before={d.previous.net_interest_month_paise} />} />
          </div>

          <div className="mb-4 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
            <Card>
              <CardHead title="Interest performance" sub="Interest earned against interest cost, by due date" />
              <div className="mb-2 flex flex-wrap items-center gap-x-[18px] gap-y-1 text-[10px] text-soft">
                <span><i className="mr-1.5 inline-block size-2 rounded-full bg-green" />Interest earned</span>
                <span><i className="mr-1.5 inline-block size-2 rounded-full bg-amber" />Interest cost</span>
                <b className="ml-auto font-medium">FY net <strong className="text-green">{inr(d.net_interest_income.fy, false)}</strong></b>
              </div>
              {d.trend.some((t) => t.earned_paise || t.cost_paise)
                ? <InterestChart data={d.trend} />
                : <Empty icon="chart" title="No interest yet" text="No interest has fallen due in the last 12 months." />}
            </Card>
            <Card>
              <CardHead title="Portfolio health" sub="Loan status at a glance" right={<Link className="link" to="/loans">All loans →</Link>} />
              <Donut counts={d.status_counts} centre={d.active_loans} centreLabel="Open loans" />
              {d.overdue_count > 0 && (
                <div className="mt-3 flex items-center gap-2.5 rounded-[9px] bg-redsoft px-3 py-2.5 text-[11px] text-redink">
                  <Icon name="alert" className="size-[17px] shrink-0" />
                  <div><b>{plural(d.overdue_count, "overdue due")} · {inr(d.overdue_paise, false)}</b><small className="mt-0.5 block text-[10px] opacity-80">Needs your attention</small></div>
                  <Link to="/collections" className="ml-auto font-bold">Review →</Link>
                </div>
              )}
            </Card>
          </div>

          <div className="mb-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHead title="To collect" icon="down" iconTone="green" sub="Next 7 days and anything overdue" right={<Link className="link" to="/calendar">View calendar →</Link>} />
              {d.collect_soon.length ? d.collect_soon.map((i) => <DueRow key={`${i.loan_id}-${i.due_date}`} item={i} money="in" />)
                : <Empty title="Nothing to collect" text="No dues in the next 7 days." />}
            </Card>
            <Card>
              <CardHead title="To pay" icon="up" iconTone="amber" sub="Next 7 days and anything overdue" right={<Link className="link" to="/calendar">View calendar →</Link>} />
              {d.pay_soon.length ? d.pay_soon.map((i) => <DueRow key={`${i.loan_id}-${i.due_date}`} item={i} money="out" />)
                : <Empty title="No upcoming payments" text="You're all caught up." />}
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHead title="Interest" sub={`Yield (12 months) ${(d.effective_yield_bps / 100).toFixed(2)}%`} />
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr><th className="th" /><th className="th text-right">Month</th><th className="th text-right">FY</th><th className="th text-right">Lifetime</th></tr></thead>
                  <tbody>
                    {([["Earned", d.interest_earned], ["Cost", d.interest_cost], ["Net", d.net_interest_income]] as const).map(([label, v]) => (
                      <tr key={label}>
                        <td className={cx("td", label === "Net" && "font-extrabold")}>{label}</td>
                        {(["month", "fy", "lifetime"] as const).map((k) => <td key={k} className={cx("td num text-right", label === "Net" && "font-extrabold")}>{inr(v[k], false)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3">
                <Row label="Collected (12 months)">{inr(d.collected_paise, false)}</Row>
                <Row label="Pending">{inr(d.pending_paise, false)}</Row>
              </div>
            </Card>
            <Card>
              <CardHead title="Overdue aging" sub={plural(d.overdue_count, "due")} />
              {([["0_30", "0–30 days"], ["31_60", "31–60 days"], ["61_90", "61–90 days"], ["90_plus", "90+ days"]] as const).map(([k, label]) => (
                <div key={k} className="mb-3.5">
                  <div className="mb-1.5 flex justify-between text-xs"><span className="text-muted">{label}</span><b className="num">{inr(d.aging[k], false)}</b></div>
                  <div className="h-[7px] overflow-hidden rounded-full bg-chip">
                    <span className="block h-full rounded-full bg-red" style={{ width: `${d.overdue_paise ? (d.aging[k] * 100) / d.overdue_paise : 0}%` }} />
                  </div>
                </div>
              ))}
            </Card>
            <Card>
              <CardHead title="Exposure by borrower" sub="Share of money lent out" />
              {d.concentration.length ? d.concentration.slice(0, 6).map((c) => (
                <div key={c.name} className="mb-3.5">
                  <div className="mb-1.5 flex justify-between gap-2 text-xs">
                    <span className="truncate text-muted">{c.name} {c.flagged && <Tag tone="red">high</Tag>}</span><b className="num">{c.share_pct}%</b>
                  </div>
                  <div className="h-[7px] overflow-hidden rounded-full bg-chip">
                    <span className={cx("block h-full rounded-full", c.flagged ? "bg-red" : "bg-teal")} style={{ width: `${c.share_pct}%` }} />
                  </div>
                </div>
              )) : <Empty icon="people" title="No money lent out" />}
              {d.idle_cost_paise_per_cycle > 0 && <Alert>Unlinked borrowed money costs {inr(d.idle_cost_paise_per_cycle, false)}/month.</Alert>}
            </Card>
          </div>
          <p className="mt-6 text-[11px] text-muted">
            Daily job: {d.last_job ? <>last run {dmy(d.last_job.run_date)} — {d.last_job.ok ? <span className="text-green">ok</span> : <span className="text-redink">failed: {d.last_job.summary}</span>}</> : "not run yet"}
          </p>
        </>
      )}
    </>
  );
}
