import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useMutation } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { addDaysIso, dmy, inr } from "../lib/format";
import type { LoanBrief } from "../lib/types";
import { Icon } from "../components/Icon";
import { Alert, Card, CardHead, cx, Empty, ErrorBox, Field, FormFoot, formObject, Loading, Metric, PageHead, Row, Segmented, Submit } from "../components/ui";

const KIND_LABELS: Record<string, string> = {
  EXTRA_PAYMENT: "Part of the principal is repaid early", PAYOFF: "The loan is paid off in full", RATE_CHANGE: "The interest rate changes",
  MISS: "Interest is not paid for a few months", TOPUP: "More money is added to the loan",
};

type Proj = { dues: { due_date: string; interest_paise: number; paid: boolean }[]; interest_paise: number; payments_paise: number; topups_paise: number;
  principal_left_paise: number; interest_left_paise: number; payoff_paise: number; effective_rate: string; closed_on: string | null };
type LoanSim = { loan: { id: number; party_name: string; direction: string }; horizon: string; scenario: { kind: string }; actual: Proj; simulated: Proj;
  links: { other: { id: number; party_name?: string }; exposure_actual: number; exposure_sim: number; spread_actual: number; spread_sim: number }[];
  arrears_today_paise: number };
type Line = { low_paise: number; low_date: string | null; short_date: string | null; short_paise: number };
type NewSim = { terms: { direction: string }; flows: [string, number][]; monthly_interest_paise: number; interest_in_window_paise: number;
  before: { expected: Line; cautious: Line; buffer_paise: number; has_accounts: boolean }; after: { expected: Line; cautious: Line } };

function Diff({ a, b }: { a: number; b: number }) {
  const d = b - a;
  if (!d) return <span className="text-muted">same</span>;
  return <span>{d > 0 ? "+" : "−"}{inr(Math.abs(d))}</span>;
}

function LoanResult({ r }: { r: LoanSim }) {
  const lend = r.loan.direction === "GIVEN";
  const rows: [string, keyof Proj][] = [
    [lend ? "Interest you earn" : "Interest you pay", "interest_paise"], [lend ? "Payments received" : "Payments made", "payments_paise"],
    ["Principal left", "principal_left_paise"], ["Interest unpaid", "interest_left_paise"], ["Payoff at the end", "payoff_paise"]];
  return (
    <div className="grid gap-4">
      {r.arrears_today_paise > 0 && <Alert>{inr(r.arrears_today_paise)} of interest is unpaid today. Every payment clears the oldest unpaid interest first
        (penalty → oldest interest → principal){["EXTRA_PAYMENT", "PAYOFF"].includes(r.scenario.kind) ? ", so less of the extra money reduces the principal" : ""}, and newer dues can still show as unpaid at the end.</Alert>}
      <Card>
        <CardHead title={`${KIND_LABELS[r.scenario.kind]} · LN-${r.loan.id} ${r.loan.party_name}`} sub={`Compared up to ${dmy(r.horizon)}. Nothing is saved.`} />
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr><th className="th" /><th className="th text-right">As things are</th><th className="th text-right">What if</th><th className="th text-right">Difference</th></tr></thead>
            <tbody>
              {rows.map(([label, k]) => (
                <tr key={k}><td className="td">{label}</td><td className="td num text-right">{inr(r.actual[k] as number)}</td>
                  <td className="td num text-right font-bold">{inr(r.simulated[k] as number)}</td>
                  <td className="td num text-right font-bold"><Diff a={r.actual[k] as number} b={r.simulated[k] as number} /></td></tr>))}
              <tr><td className="td">Effective rate</td><td className="td text-right">{r.actual.effective_rate}%</td><td className="td text-right font-bold">{r.simulated.effective_rate}%</td><td className="td" /></tr>
              <tr><td className="td">Closes on</td><td className="td text-right">{dmy(r.actual.closed_on)}</td><td className="td text-right font-bold">{dmy(r.simulated.closed_on)}</td><td className="td" /></tr>
            </tbody>
          </table>
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {(["actual", "simulated"] as const).map((k) => (
          <Card key={k}>
            <CardHead title={k === "actual" ? "Dues as things are" : "Dues in the what-if"} />
            {r[k].dues.length ? r[k].dues.map((x) => <Row key={x.due_date} label={dmy(x.due_date)}>{inr(x.interest_paise)} {x.paid ? "" : <span className="ml-1 text-[10px] text-redink">unpaid</span>}</Row>)
              : <p className="text-xs text-muted">No dues in this window.</p>}
          </Card>
        ))}
      </div>
      {r.links.length > 0 && (
        <Card>
          <CardHead title="Funding links affected" />
          {r.links.map((x) => <Row key={x.other.id} label={`LN-${x.other.id}`}>spread {inr(x.spread_actual)} → {inr(x.spread_sim)} a month · exposure {inr(x.exposure_actual, false)} → {inr(x.exposure_sim, false)}</Row>)}
        </Card>
      )}
    </div>
  );
}

function NewResult({ n, days, today }: { n: NewSim; days: number; today: string }) {
  const lend = n.terms.direction === "GIVEN";
  const e0 = n.before.expected, e1 = n.after.expected, c1 = n.after.cautious;
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
        <Metric label="Interest a month" icon="percent" tone="teal" value={inr(n.monthly_interest_paise)} sub={`you ${lend ? "earn" : "pay"} for a full month`} />
        <Metric label="Interest in the window" icon="calendar" tone="blue" value={inr(n.interest_in_window_paise)} sub={`due by ${dmy(addDaysIso(today, days))}`} />
        <Metric label="Lowest balance now" icon="wallet" tone="violet" value={inr(e0.low_paise, false)} sub={e0.short_date ? `short from ${dmy(e0.short_date)}` : "without this loan"} />
        <Metric label="Lowest balance with it" icon="shield" tone={e1.short_date ? "red" : "green"} value={inr(e1.low_paise, false)} sub={e1.short_date ? `short from ${dmy(e1.short_date)}` : "you can afford it"} />
      </div>
      {e1.short_date && !e0.short_date ? <Alert tone="red">With this loan you would be {inr(e1.short_paise)} short on {dmy(e1.short_date)}{n.before.buffer_paise ? ` (your minimum ${inr(n.before.buffer_paise)})` : ""}.</Alert>
        : c1.short_date && !n.before.cautious.short_date ? <Alert>Affordable if borrowers pay on time; if late borrowers don't pay you could be {inr(c1.short_paise)} short on {dmy(c1.short_date)}.</Alert>
        : !e1.short_date ? <Alert tone="teal">Your cash stays above {n.before.buffer_paise ? "your minimum" : "zero"} for the whole window with this loan.</Alert> : null}
      {!n.before.has_accounts && <p className="text-[11px] text-muted">No cash accounts yet, so balances start from ₹0. <Link className="link" to="/cash">Add accounts</Link> for a real answer.</p>}
      <Card>
        <CardHead title="This loan's cash flows" sub="Interest assumed paid on each due date" />
        {n.flows.length ? n.flows.map(([d, amt]) => <Row key={d} label={dmy(d)}><span className={amt > 0 ? "text-green" : "text-redink"}>{amt > 0 ? "+" : "−"}{inr(Math.abs(amt))}</span></Row>)
          : <p className="text-xs text-muted">Nothing in this window.</p>}
      </Card>
    </div>
  );
}

export default function Simulate() {
  const { session } = useSession();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<"loan" | "new">(params.get("mode") === "new" ? "new" : "loan");
  const [kind, setKind] = useState("EXTRA_PAYMENT");
  const [days, setDays] = useState(60);
  const loans = useQuery({ queryKey: ["sim-loans"], queryFn: () => api<{ loans: LoanBrief[]; kinds: string[] }>("/api/ui/simulate") });
  const run = useMutation({ mutationFn: (b: Record<string, unknown>) => post<{ mode: string; result: any }>("/api/ui/simulate", b) });
  const today = session?.today ?? "";
  return (
    <>
      <PageHead eyebrow="Analyse" title="What-if simulator" scope={false}
        sub="Explore repayment and lending scenarios before you decide. Nothing here is saved."
        actions={<Segmented label="Mode" value={mode} onChange={(m) => { setMode(m); run.reset(); }} options={[["loan", "An existing loan"], ["new", "A new loan"]]} />} />
      <div className="grid gap-4 xl:grid-cols-[380px_1fr]">
        <Card className="self-start">
          {mode === "loan" ? (
            <form onSubmit={(e) => { e.preventDefault(); const b = formObject(e.currentTarget); run.mutate({ ...b, mode: "loan", loan_id: Number(b.loan_id), horizon: Number(b.horizon) }); }}>
              <CardHead title="Change one thing" icon="spark" />
              <div className="grid gap-4">
                <Field label="Loan *">{(id) => loans.isLoading ? <Loading /> : (
                  <select id={id} name="loan_id" className="input" required defaultValue={params.get("loan_id") ?? ""}>
                    <option value="">Choose a loan…</option>
                    {loans.data?.loans.map((l) => <option key={l.id} value={l.id}>LN-{l.id} · {l.party_name} · {l.direction === "GIVEN" ? "lent" : "borrowed"} {inr(l.principal_outstanding_paise, false)}</option>)}
                  </select>)}</Field>
                <Field label="What if…">{(id) => <select id={id} name="kind" className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
                  {Object.entries(KIND_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>}</Field>
                <Field label="From / on">{(id) => <input id={id} name="on" type="date" className="input" defaultValue={today} min={today} />}</Field>
                {(kind === "EXTRA_PAYMENT" || kind === "TOPUP") && <Field label="Amount (₹) *">{(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required />}</Field>}
                {kind === "RATE_CHANGE" && <Field label="New rate (%) *">{(id) => <input id={id} name="rate" className="input num" inputMode="decimal" required />}</Field>}
                {kind === "MISS" && <Field label="Months not paid">{(id) => <input id={id} name="months" type="number" min={1} max={24} defaultValue={3} className="input" />}</Field>}
                <Field label="Compare over">{(id) => <select id={id} name="horizon" className="input" defaultValue="6">
                  {[3, 6, 12, 24].map((h) => <option key={h} value={h}>{h} months</option>)}</select>}</Field>
              </div>
              <FormFoot><Submit busy={run.isPending}><Icon name="spark" className="size-4" />Simulate</Submit></FormFoot>
            </form>
          ) : (
            <form onSubmit={(e) => { e.preventDefault(); const b = formObject(e.currentTarget); setDays(Number(b.days)); run.mutate({ ...b, mode: "new", days: Number(b.days) }); }}>
              <CardHead title="A loan you are thinking about" icon="spark" sub="Can you afford it? What does it earn or cost?" />
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
                <Field label="I would">{(id) => <select id={id} name="direction" className="input"><option value="GIVEN">Lend money</option><option value="TAKEN">Borrow money</option></select>}</Field>
                <Field label="Book">{(id) => <select id={id} name="loan_book" className="input"><option value="BUSINESS">Business</option><option value="PERSONAL">Personal</option></select>}</Field>
                <Field label="Amount (₹) *">{(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required />}</Field>
                <Field label="Rate (% a month) *">{(id) => <input id={id} name="rate" className="input num" inputMode="decimal" required />}</Field>
                <Field label="Start">{(id) => <input id={id} name="start" type="date" className="input" defaultValue={today} min={today} />}</Field>
                <Field label="Due day">{(id) => <input id={id} name="due_day" type="number" min={1} max={31} className="input" placeholder="start day" />}</Field>
                <Field label="Maturity (optional)">{(id) => <input id={id} name="maturity" type="date" className="input" min={today} />}</Field>
                <Field label="Look ahead">{(id) => <select id={id} name="days" className="input" defaultValue="60">{[30, 60, 90, 180].map((d) => <option key={d} value={d}>{d} days</option>)}</select>}</Field>
              </div>
              <FormFoot><Submit busy={run.isPending}><Icon name="spark" className="size-4" />Simulate</Submit></FormFoot>
            </form>
          )}
        </Card>
        <div className={cx("min-w-0", run.isPending && "opacity-60")}>
          {run.error && <ErrorBox error={run.error} />}
          {run.data ? (run.data.mode === "new" ? <NewResult n={run.data.result} days={days} today={today} /> : <LoanResult r={run.data.result} />)
            : !run.error && <Card><Empty icon="spark" title="Nothing simulated yet" text={mode === "loan" ? "Choose a loan and one change, then press Simulate." : "Enter the loan you are thinking about, then press Simulate."} /></Card>}
        </div>
      </div>
    </>
  );
}
