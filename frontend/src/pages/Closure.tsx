import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dmy, inr, rateText } from "../lib/format";
import type { Issuer, Loan, Party } from "../lib/types";
import { Icon } from "../components/Icon";
import { Alert, ErrorBox, Loading, PageHead, Row } from "../components/ui";

type C = { loan: Loan; party: Party; issuer: Issuer; status: string; closed_on: string | null; lent_paise: number; received_paise: number;
  principal_repaid_paise: number; interest_paid_paise: number; penalty_paid_paise: number; interest_waived_paise: number;
  principal_left_paise: number; receipts: number };

export default function Closure() {
  const { id } = useParams();
  const { session } = useSession();
  const q = useQuery({ queryKey: ["closure", id], queryFn: () => api<C>(`/api/ui/closure/${id}`) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error ?? "Not found"} />;
  const c = q.data, l = c.loan, closed = c.status === "CLOSED";
  return (
    <>
      <div className="no-print">
        <PageHead eyebrow="Loan closure" title="No-dues certificate" scope={false} sub={`For ${c.party.name} · LN-${l.id}. Print it or save it as PDF and share it.`}
          actions={<><Link className="btn" to={`/loans/${l.id}`}>Back to loan</Link>
            <button type="button" className="btn btn-primary" onClick={() => window.print()}><Icon name="print" className="size-4" />Print / save PDF</button></>} />
        {!c.issuer.name && <div className="mb-4"><Alert tone="teal">Your name is not on the certificate yet. Set <b>RECEIPT_NAME</b> in <code>.env</code>, then restart the app.</Alert></div>}
      </div>
      <article className="card mx-auto max-w-[820px] p-6 sm:p-10">
        <header className="flex flex-wrap justify-between gap-4 border-b border-line pb-5">
          <div>{c.issuer.name && <b className="block font-display text-xl font-extrabold">{c.issuer.name}</b>}
            {c.issuer.details && <p className="mt-1 text-xs whitespace-pre-line text-muted">{c.issuer.details}</p>}</div>
          <div className="text-right"><div className="text-[10px] font-extrabold tracking-[1.5px] text-teal uppercase">No-dues certificate</div>
            <b className="block">NDC-LN-{l.id}</b><small className="text-xs text-muted">Date: {dmy(session?.today)}</small></div>
        </header>
        <p className="py-6 text-sm leading-7">
          This is to certify that <b>{c.party.name}</b>{c.party.phone ? ` (${c.party.phone})` : ""}, who took a loan of <b>{inr(c.lent_paise)}</b> (loan LN-{l.id}, given on {dmy(l.start_date)}),{" "}
          {closed ? <>has repaid the loan in full together with all interest, and <b>nothing is due</b> from them on this loan as on {dmy(c.closed_on)}.</>
            : <>has settled this loan by mutual agreement. The loan is treated as closed and <b>no further amount is due</b> from them on it as on {dmy(session?.today)}.</>}
        </p>
        <div className="grid gap-6 sm:grid-cols-2">
          <div><h3 className="mb-2 text-sm font-extrabold">Summary</h3>
            <Row label="Loan amount">{inr(c.lent_paise)}</Row><Row label="Total received">{inr(c.received_paise)}</Row>
            <Row label="Principal repaid">{inr(c.principal_repaid_paise)}</Row><Row label="Interest paid">{inr(c.interest_paid_paise)}</Row>
            {c.penalty_paid_paise > 0 && <Row label="Penalty paid">{inr(c.penalty_paid_paise)}</Row>}
            {c.interest_waived_paise > 0 && <Row label="Interest waived">{inr(c.interest_waived_paise)}</Row>}
            {!closed && c.principal_left_paise > 0 && <Row label="Principal given up in settlement">{inr(c.principal_left_paise)}</Row>}</div>
          <div><h3 className="mb-2 text-sm font-extrabold">Loan</h3>
            <Row label="Given on">{dmy(l.start_date)}</Row><Row label={closed ? "Closed on" : "Settled on or before"}>{dmy(closed ? c.closed_on : session?.today)}</Row>
            <Row label="Interest rate">{rateText(l.rate, l.rate_unit)}</Row><Row label="Receipts issued">{c.receipts}</Row></div>
        </div>
        {l.collateral && <p className="mt-5 text-sm">Any security held against this loan ({l.collateral}) is to be returned to the borrower.</p>}
        <footer className="mt-12 flex items-end justify-between text-xs text-muted"><span>Computer-generated certificate · NDC-LN-{l.id}</span>
          <span className="border-t border-ink/40 px-6 pt-1">Authorised signature</span></footer>
      </article>
    </>
  );
}
