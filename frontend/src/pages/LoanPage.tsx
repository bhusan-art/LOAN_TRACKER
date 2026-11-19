import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/toast";
import { dmy, dshort, inr, plural, rateText, when } from "../lib/format";
import type { LoanPage as LP } from "../lib/types";
import { Icon } from "../components/Icon";
import {
  Alert, BookTag, Card, CardHead, Confirm, cx, DirTag, Empty, ErrorBox, Field, FormFoot, formObject, Loading, Metric,
  Modal, PageHead, Row, StatusTag, Submit, TableWrap, Tag, useAction,
} from "../components/ui";
import { DocumentsCard } from "../components/Docs";
import { PAY_MODES } from "./LoanNew";

export const CONTACT_KINDS: [string, string][] = [["CALL", "Call"], ["VISIT", "Visit"], ["WHATSAPP", "WhatsApp"], ["SMS", "SMS"], ["OTHER", "Other"]];
export const OUTCOMES: [string, string, "gray" | "amber" | "violet" | "red" | "green"][] = [
  ["REACHED", "Spoke to them", "gray"], ["NO_ANSWER", "No answer", "amber"], ["PROMISED", "Promised to pay", "violet"],
  ["DISPUTED", "Disputed", "red"], ["PAID", "Said they paid", "green"], ["OTHER", "Other", "gray"]];
const PROMISE: Record<string, [string, "violet" | "green" | "amber" | "red" | "gray"]> = {
  PENDING: ["Pending", "violet"], KEPT: ["Kept", "green"], KEPT_LATE: ["Kept late", "amber"], BROKEN: ["Broken", "red"], CANCELLED: ["Cancelled", "gray"]};

function PaymentForm({ d }: { d: LP }) {
  const { session } = useSession();
  const given = d.loan.direction === "GIVEN";
  const act = useAction((b: Record<string, string>) => post(`/api/ui/loans/${d.loan.id}/entries`, { ...b, type: "PAYMENT" }));
  return (
    <Card id="payment">
      <CardHead title={given ? "Record payment received" : "Record payment made"} icon="plus"
        sub="Clears penalty first, then the oldest interest, then principal." />
      <form onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; act.mutate(formObject(f), { onSuccess: () => f.reset() }); }}>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Field label="Amount (₹) *" hint={`Interest due ${inr(d.balances.interest_outstanding_paise + d.balances.penalty_outstanding_paise)}`}>
            {(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required placeholder="e.g. 2,000" />}</Field>
          <Field label="Date">{(id) => <input id={id} name="entry_date" type="date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
          <Field label="Mode">{(id) => <select id={id} name="payment_mode" className="input">{PAY_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
          <Field label="Reference">{(id) => <input id={id} name="reference" className="input" maxLength={200} placeholder="UTR / note" />}</Field>
        </div>
        <FormFoot><Submit busy={act.isPending}>Save payment</Submit></FormFoot>
      </form>
    </Card>
  );
}

function OtherEntry({ d }: { d: LP }) {
  const { session } = useSession();
  const [type, setType] = useState("TOPUP");
  const act = useAction((b: Record<string, string>) => post(`/api/ui/loans/${d.loan.id}/entries`, b));
  return (
    <details className="card p-5 open:pb-5">
      <summary className="cursor-pointer text-[13px] font-extrabold">+ Other entries: top-up, rate change, waiver, penalty</summary>
      <form className="mt-4" onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; act.mutate(formObject(f), { onSuccess: () => f.reset() }); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Type">{(id) => (
            <select id={id} name="type" className="input" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="TOPUP">Top-up (more money {d.loan.direction === "GIVEN" ? "lent" : "borrowed"})</option>
              <option value="RATE_CHANGE">Rate change</option><option value="WAIVER">Waive interest</option><option value="PENALTY">Late penalty</option>
            </select>)}</Field>
          <Field label="Date">{(id) => <input id={id} name="entry_date" type="date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
          {type === "RATE_CHANGE"
            ? <Field label="New rate (%) *" hint={`Now ${rateText(d.loan.rate, d.loan.rate_unit)}; applies from the date.`}>{(id) => <input id={id} name="new_rate" className="input num" inputMode="decimal" required />}</Field>
            : <Field label={`Amount (₹)${type === "WAIVER" ? "" : " *"}`} hint={type === "WAIVER" ? "Leave empty to waive the oldest unpaid due in full." : undefined}>
                {(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required={type !== "WAIVER"} />}</Field>}
          <Field label="Note">{(id) => <input id={id} name="note" className="input" maxLength={1000} />}</Field>
        </div>
        <FormFoot><Submit busy={act.isPending}>Save entry</Submit></FormFoot>
      </form>
    </details>
  );
}

function History({ d }: { d: LP }) {
  const { canEdit } = useSession();
  const [rev, setRev] = useState<number | null>(null);
  const act = useAction((v: { id: number; note: string }) => post(`/api/ui/entries/${v.id}/reverse`, { note: v.note }), { onDone: () => setRev(null) });
  const target = d.entries.find((e) => e.id === rev);
  return (
    <Card>
      <CardHead title="Ledger" sub="Every entry, newest first. Mistakes are corrected with a reversal, never edited." icon="file"
        right={d.missing_receipts > 0 && canEdit ? <BackfillButton loanId={d.loan.id} n={d.missing_receipts} /> : undefined} />
      <TableWrap>
        <thead><tr>{["Date", "Entry", "Amount", "Split", "Receipt", ""].map((h, i) => <th key={i} className={cx("th", i === 2 && "text-right")}>{h}</th>)}</tr></thead>
        <tbody>
          {d.entries.map((e) => {
            const dead = e.reversed || e.reverses_id !== null;
            return (
              <tr key={e.id} className={cx(dead && "opacity-55")}>
                <td className="td">{dshort(e.entry_date)}</td>
                <td className="td">
                  <b className={cx(e.reversed && "line-through")}>{e.reverses_id ? `Reversal of #${e.reverses_id}` : e.words}</b>
                  {e.type === "RATE_CHANGE" && e.new_rate && <span className="text-muted"> → {e.new_rate}%</span>}
                  <small className="block text-[10px] text-muted">#{e.id} · {e.payment_mode}{e.reference ? ` · ${e.reference}` : ""}{e.note ? ` · ${e.note}` : ""}</small>
                </td>
                <td className="td num text-right font-bold">{e.amount_paise !== null ? inr(e.amount_paise) : "—"}</td>
                <td className="td min-w-48 text-[11px] whitespace-normal text-muted">{e.allocations.length ? e.allocations.map((a) => `${a.bucket.toLowerCase()} ${inr(a.amount_paise)}`).join(" · ") : e.type === "PAYMENT" && !dead ? "held as credit" : ""}</td>
                <td className="td">{e.receipt ? <Link to={`/receipts/${e.receipt.id}`} className="link">{e.receipt.number}</Link> : ""}</td>
                <td className="td text-right">{canEdit && !dead && <button type="button" className="btn btn-sm" onClick={() => setRev(e.id)}>Reverse</button>}</td>
              </tr>
            );
          })}
        </tbody>
      </TableWrap>
      <Modal open={rev !== null} onClose={() => setRev(null)} title="Reverse this entry?">
        {target && (
          <form onSubmit={(e) => { e.preventDefault(); act.mutate({ id: target.id, note: String(new FormData(e.currentTarget).get("note") ?? "") }); }}>
            <p className="mb-4 text-sm text-soft">{target.words}{target.amount_paise ? ` of ${inr(target.amount_paise)}` : ""} on {dmy(target.entry_date)} will be cancelled by a new reversal entry. Balances and dues are recalculated.{target.receipt ? ` Receipt ${target.receipt.number} is cancelled too.` : ""}</p>
            <Field label="Reason">{(id) => <input id={id} name="note" className="input" maxLength={1000} placeholder="e.g. typed the wrong amount" />}</Field>
            <FormFoot><button type="button" className="btn" onClick={() => setRev(null)}>Keep it</button><button className="btn btn-danger" disabled={act.isPending}>Reverse entry</button></FormFoot>
          </form>
        )}
      </Modal>
    </Card>
  );
}

function BackfillButton({ loanId, n }: { loanId: number; n: number }) {
  const act = useAction(() => post("/api/receipts/backfill", { loan_id: loanId }));
  return <button type="button" className="btn btn-sm btn-soft" disabled={act.isPending} onClick={() => act.mutate(undefined)}>Issue {plural(n, "missing receipt")}</button>;
}

function Dues({ d }: { d: LP }) {
  const [all, setAll] = useState(false);
  const shown = all ? d.dues : d.dues.slice(0, 8);
  return (
    <Card>
      <CardHead title="Interest dues" icon="calendar" sub={d.projected_due ? `Next: ${inr(d.projected_due.interest_paise)} on ${dmy(d.projected_due.due_date)}` : "No further interest"} />
      {d.dues.length ? (
        <>
          <TableWrap>
            <thead><tr>{["Due date", "Period", "Interest", "Paid", "Left", "Status"].map((h, i) => <th key={h} className={cx("th", i >= 2 && i <= 4 && "text-right")}>{h}</th>)}</tr></thead>
            <tbody>{shown.map((x) => (
              <tr key={x.period_start}>
                <td className="td font-bold">{dshort(x.due_date)}</td>
                <td className="td text-muted">{dshort(x.period_start)} – {dshort(x.period_end)}</td>
                <td className="td num text-right">{inr(x.interest_paise + x.penalty_paise)}</td>
                <td className="td num text-right">{inr(x.paid_paise + x.waived_paise)}</td>
                <td className="td num text-right font-bold">{inr(x.outstanding_paise)}</td>
                <td className="td"><StatusTag status={x.status} />{x.overdue_days > 0 && <span className="ml-1 text-[10px] text-muted">{x.overdue_days}d</span>}</td>
              </tr>))}
            </tbody>
          </TableWrap>
          {d.dues.length > 8 && <button type="button" className="link mt-3" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${d.dues.length} dues`}</button>}
        </>
      ) : <Empty icon="calendar" title="No dues yet" text="The first interest due appears on its due date." />}
    </Card>
  );
}

function Terms({ d }: { d: LP }) {
  const { canEdit } = useSession();
  const l = d.loan;
  const act = useAction((b: Record<string, string>) => post(`/api/loans/${l.id}/status`, b));
  return (
    <Card>
      <CardHead title="Terms" icon="file" />
      <Row label="Principal">{inr(l.principal_paise)}</Row>
      <Row label="Rate">{rateText(l.rate, l.rate_unit)}</Row>
      <Row label="Interest paid">{{ MONTHLY: "Monthly", QUARTERLY: "Quarterly", AT_MATURITY: "At maturity" }[l.interest_cycle] ?? l.interest_cycle}</Row>
      <Row label="Due day">{l.due_day} of the month</Row>
      <Row label="Grace">{plural(l.grace_days, "day")}</Row>
      <Row label="Started">{dmy(l.start_date)}</Row>
      <Row label="Maturity">{dmy(l.maturity_date)}</Row>
      {l.purpose && <Row label="Purpose">{l.purpose}</Row>}
      {l.collateral && <Row label="Collateral">{l.collateral}</Row>}
      {canEdit && (
        <form className="mt-4 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); act.mutate(formObject(e.currentTarget)); }}>
          <Field label="Status" className="min-w-40 flex-1">{(id) => (
            <select id={id} name="status" className="input" defaultValue={["SETTLED", "WRITTEN_OFF"].includes(l.status) ? l.status : ""} key={l.status}>
              <option value="">From the ledger</option><option value="SETTLED">Settled</option><option value="WRITTEN_OFF">Written off</option>
            </select>)}</Field>
          <Submit busy={act.isPending}>Update</Submit>
        </form>
      )}
    </Card>
  );
}

function Collections({ d }: { d: LP }) {
  const { session, canEdit } = useSession();
  const [outcome, setOutcome] = useState("REACHED");
  const log = useAction((b: Record<string, string>) => post(`/api/ui/loans/${d.loan.id}/contacts`, b));
  const cancel = useAction((id: number) => post(`/api/ui/promises/${id}/cancel`, { note: "" }));
  if (d.loan.direction !== "GIVEN") return null;
  return (
    <Card id="collections">
      <CardHead title="Collections" icon="phone" sub="Calls, visits and promises to pay" />
      {canEdit && d.chasing && (
        <details className="mb-3 rounded-lg border border-line p-3 open:bg-field">
          <summary className="cursor-pointer text-xs font-bold text-teal">Log a contact</summary>
          <form className="mt-4" onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; log.mutate(formObject(f), { onSuccess: () => { f.reset(); setOutcome("REACHED"); } }); }}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="How">{(id) => <select id={id} name="kind" className="input">{CONTACT_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
              <Field label="Outcome">{(id) => <select id={id} name="outcome" className="input" value={outcome} onChange={(e) => setOutcome(e.target.value)}>{OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
              <Field label="Date">{(id) => <input id={id} type="date" name="contact_date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
              <Field label="Follow up on">{(id) => <input id={id} type="date" name="next_follow_up" className="input" min={session?.today} />}</Field>
              {outcome === "PROMISED" && <>
                <Field label="Promised amount (₹) *">{(id) => <input id={id} name="promise_amount" className="input num" inputMode="decimal" required />}</Field>
                <Field label="Promised by *">{(id) => <input id={id} type="date" name="promise_date" className="input" required min={session?.today} />}</Field>
              </>}
              <Field label="Note" full>{(id) => <input id={id} name="note" className="input" maxLength={2000} />}</Field>
            </div>
            <FormFoot><Submit busy={log.isPending}>Save contact</Submit></FormFoot>
          </form>
        </details>
      )}
      {d.promises.length > 0 && (
        <div className="mb-3">
          <h4 className="mb-1 text-xs font-bold text-muted">Promises</h4>
          {d.promises.map((p) => {
            const [label, tone] = PROMISE[p.state.status] ?? [p.state.status, "gray"];
            return (
              <div key={p.id} className="flex items-center gap-2 border-b border-line py-2.5 text-xs last:border-0">
                <span className="flex-1"><b>{inr(p.amount_paise)}</b> by {dmy(p.promised_date)}{p.cancel_note ? <small className="block text-muted">{p.cancel_note}</small> : null}</span>
                <Tag tone={tone}>{label}</Tag>
                {canEdit && p.state.status === "PENDING" && (
                  <Confirm text="Cancel this promise? It stays in the history." onYes={() => cancel.mutate(p.id)} label="Cancel promise">
                    {(open) => <button type="button" className="btn btn-sm" onClick={open}>Cancel</button>}
                  </Confirm>)}
              </div>
            );
          })}
        </div>
      )}
      {d.contacts.length ? d.contacts.map((c) => {
        const o = OUTCOMES.find((x) => x[0] === c.outcome);
        return (
          <div key={c.id} className="border-b border-line py-2.5 text-xs last:border-0">
            <div className="flex items-center gap-2"><b>{dmy(c.contact_date)}</b><span className="text-muted">{CONTACT_KINDS.find((k) => k[0] === c.kind)?.[1]}</span><span className="ml-auto"><Tag tone={o?.[2] ?? "gray"}>{o?.[1] ?? c.outcome}</Tag></span></div>
            {c.note && <p className="mt-1 text-soft">{c.note}</p>}
            {c.next_follow_up && <small className="mt-1 block text-muted">Follow up {dmy(c.next_follow_up)}</small>}
          </div>
        );
      }) : <p className="py-2 text-xs text-muted">No contacts logged yet.</p>}
    </Card>
  );
}

function Share({ d }: { d: LP }) {
  const { session, canEdit } = useSession();
  const toast = useToast();
  const [made, setMade] = useState<{ url: string; whatsapp: string | null; days: number } | null>(null);
  const create = useAction((days: number) => post<{ url: string; whatsapp: string | null; days: number; message: string }>(`/api/loans/${d.loan.id}/share`, { days }),
    { onDone: (r) => setMade(r) });
  const revoke = useAction((id: number) => post(`/api/share/${id}/revoke`));
  if (d.loan.direction !== "GIVEN" || !canEdit) return null;
  return (
    <Card id="share">
      <CardHead title="Statement link for the borrower" icon="link" sub="A private read-only page with their balance, dues and receipts. No login needed." />
      {made && (
        <div className="mb-3 rounded-lg border border-teal/40 bg-tealsoft p-3">
          <p className="mb-2 text-xs font-bold text-teal">New link (works for {made.days} days). Copy it now: it is shown only once.</p>
          <div className="flex gap-2">
            <input readOnly value={made.url} className="input min-h-[36px] py-1.5 text-xs" aria-label="Statement link" onFocus={(e) => e.currentTarget.select()} />
            <button type="button" className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(made.url).then(() => toast("Link copied."))}><Icon name="copy" className="size-3.5" />Copy</button>
          </div>
          {made.whatsapp && <a className="btn btn-sm mt-2" href={made.whatsapp} target="_blank" rel="noopener"><Icon name="chat" className="size-3.5" />Send on WhatsApp</a>}
        </div>
      )}
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); create.mutate(Number(new FormData(e.currentTarget).get("days"))); }}>
        <select name="days" aria-label="How long the link works" className="input min-h-[38px] w-auto py-1.5 text-xs" defaultValue="30">
          <option value="7">Works for 7 days</option><option value="30">Works for 30 days</option><option value="90">Works for 90 days</option>
        </select>
        <Submit busy={create.isPending} className="btn-sm">Create link</Submit>
      </form>
      {d.shares.length > 0 && (
        <div className="mt-3">
          {d.shares.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center gap-2 border-b border-line py-2.5 text-xs last:border-0">
              <span className="flex-1">Made {when(s.created_at, session?.timezone)} · {s.revoked_at ? "switched off" : `until ${when(s.expires_at, session?.timezone)}`}<small className="block text-muted">Opened {plural(s.view_count, "time")}{s.last_viewed_at ? `, last ${when(s.last_viewed_at, session?.timezone)}` : ""}</small></span>
              {s.live ? <Tag tone="green">Live</Tag> : <Tag>Off</Tag>}
              {s.live && <Confirm text="Switch this link off? It stops opening at once." onYes={() => revoke.mutate(s.id)} danger label="Switch off">
                {(open) => <button type="button" className="btn btn-sm" onClick={open}>Switch off</button>}</Confirm>}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function LoanPage() {
  const { id } = useParams();
  const { canEdit } = useSession();
  const q = useQuery({ queryKey: ["loan", id], queryFn: () => api<LP>(`/api/ui/loans/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error ?? "Not found"} />;
  const d = q.data;
  const l = d.loan;
  const given = l.direction === "GIVEN";
  const open = !["CLOSED", "SETTLED", "WRITTEN_OFF"].includes(d.status);
  const b = d.balances;
  return (
    <>
      <PageHead eyebrow={`LN-${l.id} · ${given ? "Money lent" : "Money borrowed"}`} scope={false}
        title={<Link to={`/people/${l.party_id}`} className="hover:text-teal">{l.party_name}</Link>}
        sub={<span className="inline-flex flex-wrap items-center gap-1.5"><DirTag direction={l.direction} /><BookTag book={l.book} /><StatusTag status={d.status} />
          <span className="ml-1">{inr(l.principal_paise, false)} at {rateText(l.rate, l.rate_unit)} since {dmy(l.start_date)}</span></span>}
        actions={<>
          {d.chasing && d.whatsapp && <a className="btn" href={d.whatsapp} target="_blank" rel="noopener"><Icon name="chat" className="size-4" />WhatsApp reminder</a>}
          {open && <Link className="btn" to={`/simulate?loan_id=${l.id}`}><Icon name="spark" className="size-4" />What if…</Link>}
          <Link className="btn" to={`/loans/${l.id}/statement`}><Icon name="print" className="size-4" />Statement</Link>
          {d.can_close && <Link className="btn btn-soft" to={`/loans/${l.id}/closure`}><Icon name="check" className="size-4" />No-dues certificate</Link>}
          <Link className="btn" to={`/activity?loan_id=${l.id}`}><Icon name="activity" className="size-4" />History</Link>
        </>} />
      {d.brief.overdue_paise > 0 && (
        <div className="mb-4"><Alert tone="red">{inr(d.brief.overdue_paise)} overdue across {plural(d.brief.overdue_count, "due")}, the oldest {plural(d.brief.overdue_days, "day")} late.</Alert></div>
      )}
      <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
        <Metric label="Principal outstanding" icon="wallet" tone="teal" value={inr(b.principal_outstanding_paise)} sub={`of ${inr(l.principal_paise, false)} ${given ? "lent" : "borrowed"}`} />
        <Metric label="Interest due now" icon="percent" tone={b.interest_outstanding_paise + b.penalty_outstanding_paise ? "red" : "green"}
          value={inr(b.interest_outstanding_paise + b.penalty_outstanding_paise)}
          sub={b.penalty_outstanding_paise ? `incl. penalty ${inr(b.penalty_outstanding_paise)}` : `${inr(b.interest_paid_paise, false)} paid so far`} />
        <Metric label="Payoff today" icon="diamond" tone="violet" value={inr(b.payoff_today_paise)}
          sub={b.credit_paise ? `after credit ${inr(b.credit_paise)}` : `incl. ${inr(b.open_period_paise)} of this period`} />
        <Metric label="Next due" icon="calendar" tone="amber" value={d.projected_due ? inr(d.projected_due.interest_paise) : "—"}
          sub={d.projected_due ? `on ${dmy(d.projected_due.due_date)}` : "Nothing more falls due"} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <div className="grid min-w-0 content-start gap-4">
          {canEdit && open && <PaymentForm d={d} />}
          {canEdit && open && <OtherEntry d={d} />}
          <History d={d} />
          <Dues d={d} />
        </div>
        <div className="grid min-w-0 content-start gap-4">
          <Terms d={d} />
          <Collections d={d} />
          <Share d={d} />
          {d.links.length > 0 && (
            <Card>
              <CardHead title="Funding links" icon="link" right={<Link to="/funding" className="link">All →</Link>} />
              {d.links.map((x) => (
                <Row key={x.id} label={<Link to={`/loans/${x.other_loan_id}`} className="hover:text-teal">{given ? "Funded by" : "Funds"} {x.other_party} · LN-{x.other_loan_id}</Link>}>{inr(x.amount_paise, false)}</Row>
              ))}
            </Card>
          )}
          <DocumentsCard docs={d.documents} loanId={l.id} partyId={l.party_id} />
        </div>
      </div>
    </>
  );
}
