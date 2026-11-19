import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { dmy, dshort, inr, rateText, when } from "../lib/format";
import type { Issuer } from "../lib/types";
import { Alert, Loading, Row, StatusTag } from "../components/ui";

type St = { statement: { loan_id: number; borrower: string; start_date: string; principal_paise: number; rate: string; rate_unit: string;
  principal_outstanding_paise: number; interest_due_paise: number; penalty_due_paise: number; payoff_paise: number; overdue_paise: number;
  overdue_since: string | null; next_due_date: string | null; next_due_paise: number; closed: boolean;
  dues: { due_date: string; interest_paise: number; paid_paise: number; outstanding_paise: number; status: string }[];
  payments: { entry_date: string; amount_paise: number; mode: string | null; receipt_id: number | null; receipt_number: string | null }[] };
  expires_at: string; today: string; issuer: Issuer };

/** The page a borrower opens from a private link: no login, no app chrome. */
export function PublicFrame({ children, issuer }: { children: React.ReactNode; issuer?: Issuer }) {
  return (
    <div className="min-h-screen bg-bg">
      <header className="no-print bg-gradient-to-r from-[#082e34] to-[#06474a] px-5 py-4 text-white">
        <div className="mx-auto flex max-w-[760px] items-center gap-3">
          <span className="grid size-9 place-items-center rounded-lg bg-[#f3a64d] font-display font-extrabold text-[#103b3d]">{(issuer?.name || "L").slice(0, 1)}</span>
          <b className="font-display text-lg">{issuer?.name || "Loan statement"}</b>
        </div>
      </header>
      <main className="mx-auto max-w-[760px] px-4 py-6">{children}</main>
    </div>
  );
}

export function PublicError({ error }: { error: unknown }) {
  return <PublicFrame><div className="card p-8 text-center"><h1 className="mb-2 text-xl font-extrabold">This page is not available</h1>
    <p className="text-sm text-muted">{error instanceof Error ? error.message : "The link may have expired or been switched off."} Ask the lender for a new link.</p></div></PublicFrame>;
}

export default function PublicStatement() {
  const { token } = useParams();
  const q = useQuery({ queryKey: ["public", token], queryFn: () => api<St>(`/api/public/s/${token}`), retry: false });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <PublicError error={q.error} />;
  const { statement: s, issuer, expires_at } = q.data;
  return (
    <PublicFrame issuer={issuer}>
      <div className="card mb-4 p-6">
        <div className="text-[10px] font-extrabold tracking-[1.5px] text-teal uppercase">Your loan statement · LN-{s.loan_id}</div>
        <h1 className="mt-1 text-2xl font-extrabold">Hello {s.borrower}</h1>
        <p className="mt-1 text-sm text-muted">{inr(s.principal_paise)} at {rateText(s.rate, s.rate_unit)} since {dmy(s.start_date)}. Figures as of {dmy(q.data.today)}.</p>
        {s.closed ? <div className="mt-4"><Alert tone="teal">This loan is closed. Nothing is due.</Alert></div>
          : s.overdue_paise > 0 && <div className="mt-4"><Alert tone="red">{inr(s.overdue_paise)} is overdue{s.overdue_since ? ` since ${dmy(s.overdue_since)}` : ""}.</Alert></div>}
        <div className="mt-4 grid gap-x-8 sm:grid-cols-2">
          <div><Row label="Principal outstanding">{inr(s.principal_outstanding_paise)}</Row><Row label="Interest due">{inr(s.interest_due_paise)}</Row>
            {s.penalty_due_paise > 0 && <Row label="Penalty due">{inr(s.penalty_due_paise)}</Row>}</div>
          <div><Row label="Next due">{s.next_due_date ? `${inr(s.next_due_paise)} on ${dmy(s.next_due_date)}` : "—"}</Row>
            <Row label="To close the loan today" strong>{inr(s.payoff_paise)}</Row></div>
        </div>
      </div>
      <div className="card mb-4 p-6">
        <h2 className="mb-3 text-[15px] font-extrabold">Your payments</h2>
        {s.payments.length ? <table className="w-full text-xs"><thead><tr><th className="th">Date</th><th className="th text-right">Amount</th><th className="th">Receipt</th></tr></thead>
          <tbody>{s.payments.map((p, i) => <tr key={i}><td className="td">{dshort(p.entry_date)}</td><td className="td num text-right">{inr(p.amount_paise)}</td>
            <td className="td">{p.receipt_id ? <Link className="link" to={`/s/${token}/receipts/${p.receipt_id}`}>{p.receipt_number}</Link> : "—"}</td></tr>)}</tbody></table>
          : <p className="text-xs text-muted">No payments yet.</p>}
      </div>
      <div className="card p-6">
        <h2 className="mb-3 text-[15px] font-extrabold">Interest dues</h2>
        <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr><th className="th">Due</th><th className="th text-right">Interest</th><th className="th text-right">Paid</th><th className="th text-right">Left</th><th className="th">Status</th></tr></thead>
          <tbody>{s.dues.map((x) => <tr key={x.due_date}><td className="td">{dshort(x.due_date)}</td><td className="td num text-right">{inr(x.interest_paise)}</td>
            <td className="td num text-right">{inr(x.paid_paise)}</td><td className="td num text-right">{inr(x.outstanding_paise)}</td><td className="td"><StatusTag status={x.status} /></td></tr>)}</tbody></table></div>
      </div>
      <p className="mt-4 text-center text-[11px] text-muted">This private link works until {when(expires_at)}. Please don't forward it.</p>
    </PublicFrame>
  );
}
