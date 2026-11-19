import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { daysBetween, dmy, dshort, inr, plural } from "../lib/format";
import type { CashAccount, CashPage } from "../lib/types";
import { Icon } from "../components/Icon";
import {
  Alert, BookTag, Card, CardHead, Confirm, Empty, ErrorBox, Field, FormFoot, formObject, Loading, Metric, Modal, PageHead, Segmented, Submit, Tag, useAction,
} from "../components/ui";
import { BalanceChart } from "../components/charts";

function BalanceForm({ acc, onClose }: { acc: CashAccount | null; onClose: () => void }) {
  const { session } = useSession();
  const act = useAction((b: Record<string, string>) => post(`/api/ui/cash/accounts/${acc?.id}/balance`, b), { onDone: onClose });
  return (
    <Modal open={!!acc} onClose={onClose} title={`Update balance · ${acc?.name ?? ""}`}>
      <form onSubmit={(e) => { e.preventDefault(); act.mutate(formObject(e.currentTarget)); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Balance (₹) *">{(id) => <input id={id} name="balance" className="input num" inputMode="decimal" required />}</Field>
          <Field label="As on">{(id) => <input id={id} name="on" type="date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
          <Field label="Note" full>{(id) => <input id={id} name="note" className="input" maxLength={500} />}</Field>
        </div>
        <FormFoot><button type="button" className="btn" onClick={onClose}>Cancel</button><Submit busy={act.isPending}>Save balance</Submit></FormFoot>
      </form>
    </Modal>
  );
}

export default function Cash() {
  const { session, canEdit, book } = useSession();
  const [days, setDays] = useState(30);
  const [edit, setEdit] = useState<CashAccount | null>(null);
  const q = useQuery({ queryKey: ["cash", book, days], queryFn: () => api<CashPage>(`/api/ui/cash?days=${days}`) });
  const addAcc = useAction((b: Record<string, string>) => post("/api/ui/cash/accounts", b));
  const active = useAction((v: { id: number; active: boolean }) => post(`/api/cash/accounts/${v.id}/active`, { active: v.active }));
  const plan = useAction((b: Record<string, string>) => post("/api/ui/cash/planned", b));
  const cancelPlan = useAction((id: number) => post<{ message?: string }>(`/api/cash/planned/${id}/cancel`).then(() => ({ message: "Planned item cancelled." })));
  const d = q.data;
  const f = d?.forecast;
  return (
    <>
      <PageHead eyebrow="Analyse" title="Cash & forecast" sub="Understand available cash and projected balances: your account balances plus expected collections, payments and planned items."
        actions={<Segmented label="Window" value={days} onChange={setDays} options={[[30, "30 days"], [60, "60 days"], [90, "90 days"]]} />} />
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : d && f && (
        <>
          {d.warnings.length > 0 && <div className="mb-4 grid gap-2">{d.warnings.map((w) => <Alert key={w} tone="red">{w}</Alert>)}</div>}
          <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Cash today" icon="wallet" tone="teal" value={inr(f.opening_paise, false)} sub={plural(f.accounts.length, "account")} />
            <Metric label="Lowest expected" icon="chart" tone={f.expected.low_paise < f.buffer_paise ? "red" : "green"} value={inr(f.expected.low_paise, false)}
              sub={f.expected.low_date ? `on ${dmy(f.expected.low_date)}` : "If everyone pays on time"} />
            <Metric label="Lowest if overdue don't pay" icon="alert" tone={f.cautious.low_paise < 0 ? "red" : "amber"} value={inr(f.cautious.low_paise, false)}
              sub={f.cautious.short_date ? `Short on ${dmy(f.cautious.short_date)}` : "Cautious view"} />
            <Metric label="Safety buffer" icon="shield" tone="violet" value={inr(f.buffer_paise, false)} sub="CASH_MIN_BALANCE in settings" />
          </div>
          <Card className="mb-4">
            <CardHead title={`Balance over the next ${days} days`} sub="Step line of the running balance"
              right={<div className="flex gap-3 text-[10px] text-soft"><span><i className="mr-1 inline-block h-0.5 w-4 bg-teal align-middle" />Expected</span>
                <span><i className="mr-1 inline-block h-0.5 w-4 border-t-2 border-dashed border-red align-middle" />Cautious</span>
                {f.buffer_paise > 0 && <span><i className="mr-1 inline-block h-0.5 w-4 border-t-2 border-dashed border-amber align-middle" />Buffer</span>}</div>} />
            {f.accounts.length ? <BalanceChart buffer={f.buffer_paise} series={[
              { name: "expected", colour: "var(--c-teal)", points: f.expected.days },
              { name: "cautious", colour: "var(--c-red)", points: f.cautious.days, dashed: true }]} />
              : <Empty icon="bank" title="Add an account first" text="Enter what is in your bank, wallet and cash so the forecast starts from real money." />}
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHead title="Accounts" icon="bank" sub="Update balances whenever you check your bank" />
              {f.accounts.map(({ account: a, balance: b }) => {
                const stale = b ? daysBetween(b.as_of, d.today) : 0;
                return (
                  <div key={a.id} className="flex flex-wrap items-center gap-3 border-b border-line py-3 last:border-0">
                    <span className="min-w-0 flex-1"><b className="block text-[13px]">{a.name}</b>
                      <small className="text-[11px] text-muted">{{ BANK: "Bank", CASH: "Cash", WALLET: "Wallet / UPI" }[a.kind] ?? a.kind} · <BookTag book={a.book} />
                        {b && <> · as on {dmy(b.as_of)} {stale > 7 && <Tag tone="amber">{stale} days old</Tag>}</>}</small></span>
                    <b className="num text-sm">{b ? inr(b.balance_paise) : "—"}</b>
                    {canEdit && <div className="flex gap-1.5">
                      <button type="button" className="btn btn-sm" onClick={() => setEdit(a)}><Icon name="edit" className="size-3.5" />Balance</button>
                      <Confirm text={`Close ${a.name}? It stops counting in the forecast. You can reopen it later.`} onYes={() => active.mutate({ id: a.id, active: false })} label="Close account">
                        {(open) => <button type="button" className="btn btn-sm" onClick={open}>Close</button>}</Confirm>
                    </div>}
                  </div>
                );
              })}
              {d.closed_accounts.length > 0 && (
                <div className="mt-3 text-xs text-muted">Closed: {d.closed_accounts.map((a) => (
                  <span key={a.id} className="mr-3">{a.name} {canEdit && <button type="button" className="link" onClick={() => active.mutate({ id: a.id, active: true })}>reopen</button>}</span>))}</div>
              )}
              {canEdit && (
                <details className="mt-3 rounded-lg border border-line p-3 open:bg-field">
                  <summary className="cursor-pointer text-xs font-bold text-teal">Add an account</summary>
                  <form className="mt-4" onSubmit={(e) => { e.preventDefault(); const fm = e.currentTarget; addAcc.mutate(formObject(fm), { onSuccess: () => fm.reset() }); }}>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Name *">{(id) => <input id={id} name="name" className="input" required maxLength={100} placeholder="e.g. HDFC current" />}</Field>
                      <Field label="Kind">{(id) => <select id={id} name="kind" className="input"><option value="BANK">Bank</option><option value="CASH">Cash</option><option value="WALLET">Wallet / UPI</option></select>}</Field>
                      <Field label="Book">{(id) => <select id={id} name="book" className="input"><option value="BUSINESS">Business</option><option value="PERSONAL">Personal</option></select>}</Field>
                      <Field label="Balance today (₹)">{(id) => <input id={id} name="balance" className="input num" inputMode="decimal" defaultValue="0" />}</Field>
                    </div>
                    <FormFoot><Submit busy={addAcc.isPending}>Add account</Submit></FormFoot>
                  </form>
                </details>
              )}
            </Card>
            <Card>
              <CardHead title="Planned money in and out" icon="calendar" sub="Rent, salaries, a big purchase: anything the forecast should know" />
              {d.planned.length ? d.planned.map((p) => (
                <div key={p.id} className="flex items-center gap-3 border-b border-line py-3 text-xs last:border-0">
                  <span className="w-16 font-bold">{dshort(p.item_date)}</span>
                  <span className="min-w-0 flex-1 truncate">{p.description} <BookTag book={p.book} /></span>
                  <b className={`num ${p.direction === "IN" ? "text-green" : "text-redink"}`}>{p.direction === "IN" ? "+" : "−"}{inr(p.amount_paise)}</b>
                  {canEdit && <Confirm text={`Cancel “${p.description}”?`} onYes={() => cancelPlan.mutate(p.id)} label="Cancel item">
                    {(open) => <button type="button" className="btn btn-sm" onClick={open} aria-label={`Cancel ${p.description}`}><Icon name="x" className="size-3.5" /></button>}</Confirm>}
                </div>
              )) : <p className="py-2 text-xs text-muted">Nothing planned in the next year.</p>}
              {canEdit && (
                <form className="mt-4 border-t border-line pt-4" onSubmit={(e) => { e.preventDefault(); const fm = e.currentTarget; plan.mutate(formObject(fm), { onSuccess: () => fm.reset() }); }}>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="What *" full>{(id) => <input id={id} name="description" className="input" required maxLength={300} placeholder="e.g. Office rent" />}</Field>
                    <Field label="Amount (₹) *">{(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required />}</Field>
                    <Field label="Date *">{(id) => <input id={id} name="item_date" type="date" className="input" required min={session?.today} />}</Field>
                    <Field label="Direction">{(id) => <select id={id} name="direction" className="input"><option value="OUT">Money out</option><option value="IN">Money in</option></select>}</Field>
                    <Field label="Book">{(id) => <select id={id} name="book" className="input"><option value="BUSINESS">Business</option><option value="PERSONAL">Personal</option></select>}</Field>
                  </div>
                  <FormFoot><Submit busy={plan.isPending}>Add to forecast</Submit></FormFoot>
                </form>
              )}
            </Card>
          </div>
        </>
      )}
      <BalanceForm acc={edit} onClose={() => setEdit(null)} />
    </>
  );
}
