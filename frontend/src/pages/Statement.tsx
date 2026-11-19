import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dmy, dshort, inr, rateText } from "../lib/format";
import type { Issuer, LoanPage } from "../lib/types";
import { Icon } from "../components/Icon";
import { cx, ErrorBox, Loading, PageHead, Row, StatusTag } from "../components/ui";

export default function Statement() {
  const { id } = useParams();
  const { session } = useSession();
  const q = useQuery({ queryKey: ["statement", id], queryFn: () => api<LoanPage & { issuer: Issuer }>(`/api/ui/statement/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error ?? "Not found"} />;
  const d = q.data, l = d.loan, b = d.balances;
  return (
    <>
      <div className="no-print"><PageHead eyebrow="Statement" title={`LN-${l.id} · ${l.party_name}`} scope={false}
        sub="Everything on this loan as of today. Print it or save it as a PDF."
        actions={<><Link className="btn" to={`/loans/${l.id}`}>Back to loan</Link>
          <button type="button" className="btn btn-primary" onClick={() => window.print()}><Icon name="print" className="size-4" />Print / save PDF</button></>} /></div>
      <article className="card mx-auto max-w-[900px] p-6 sm:p-10">
        <header className="flex flex-wrap justify-between gap-4 border-b border-line pb-5">
          <div><b className="block font-display text-xl font-extrabold">{d.issuer.name || "Loan statement"}</b>
            {d.issuer.details && <p className="mt-1 text-xs whitespace-pre-line text-muted">{d.issuer.details}</p>}</div>
          <div className="text-right"><div className="text-[10px] font-extrabold tracking-[1.5px] text-teal uppercase">Loan statement</div>
            <b className="block">LN-{l.id}</b><small className="text-xs text-muted">As of {dmy(session?.today)}</small></div>
        </header>
        <div className="grid gap-6 py-5 sm:grid-cols-2">
          <div>
            <h3 className="mb-2 text-sm font-extrabold">{l.direction === "GIVEN" ? "Borrower" : "Lender"}</h3>
            <p className="text-sm"><b>{l.party_name}</b>{l.party_phone ? <><br />{l.party_phone}</> : null}</p>
            <div className="mt-3">
              <Row label="Amount">{inr(l.principal_paise)}</Row><Row label="Rate">{rateText(l.rate, l.rate_unit)}</Row>
              <Row label="Started">{dmy(l.start_date)}</Row><Row label="Status"><StatusTag status={d.status} /></Row>
            </div>
          </div>
          <div>
            <h3 className="mb-2 text-sm font-extrabold">Balances</h3>
            <Row label="Principal outstanding">{inr(b.principal_outstanding_paise)}</Row>
            <Row label="Interest due">{inr(b.interest_outstanding_paise)}</Row>
            {b.penalty_outstanding_paise > 0 && <Row label="Penalty due">{inr(b.penalty_outstanding_paise)}</Row>}
            <Row label="Interest paid so far">{inr(b.interest_paid_paise)}</Row>
            <Row label="Payoff today" strong>{inr(b.payoff_today_paise)}</Row>
          </div>
        </div>
        <h3 className="mt-2 mb-2 text-sm font-extrabold">Entries</h3>
        <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr><th className="th">Date</th><th className="th">Entry</th><th className="th text-right">Amount</th><th className="th">Split</th></tr></thead>
          <tbody>{[...d.entries].reverse().map((e) => (
            <tr key={e.id} className={cx((e.reversed || e.reverses_id !== null) && "opacity-50")}>
              <td className="td">{dshort(e.entry_date)}</td><td className="td">{e.reverses_id ? `Reversal of #${e.reverses_id}` : e.words}{e.new_rate ? ` → ${e.new_rate}%` : ""}</td>
              <td className="td num text-right">{e.amount_paise !== null ? inr(e.amount_paise) : "—"}</td>
              <td className="td min-w-40 whitespace-normal text-muted">{e.allocations.map((a) => `${a.bucket.toLowerCase()} ${inr(a.amount_paise)}`).join(" · ")}</td>
            </tr>))}</tbody></table></div>
        <h3 className="mt-6 mb-2 text-sm font-extrabold">Interest dues</h3>
        <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr><th className="th">Due</th><th className="th text-right">Interest</th><th className="th text-right">Paid</th><th className="th text-right">Left</th><th className="th">Status</th></tr></thead>
          <tbody>{[...d.dues].reverse().map((x) => (
            <tr key={x.period_start}><td className="td">{dshort(x.due_date)}</td><td className="td num text-right">{inr(x.interest_paise + x.penalty_paise)}</td>
              <td className="td num text-right">{inr(x.paid_paise + x.waived_paise)}</td><td className="td num text-right">{inr(x.outstanding_paise)}</td><td className="td"><StatusTag status={x.status} /></td></tr>))}</tbody></table></div>
        <p className="mt-6 text-[11px] text-muted">Computer-generated statement. Amounts calculated by the ledger engine.</p>
      </article>
    </>
  );
}
