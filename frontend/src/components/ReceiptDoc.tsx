import { dmy, inr } from "../lib/format";
import type { ReceiptPage } from "../lib/types";

/** The printable receipt itself; shared by the owner's page and the borrower's link. */
export function ReceiptDoc({ r }: { r: ReceiptPage }) {
  const rows: [string, number][] = [["Penalty", r.penalty_paise], ["Interest", r.interest_paise], ["Principal", r.principal_paise], ["Held as credit", r.credit_paise]];
  return (
    <article className="card relative mx-auto max-w-[720px] overflow-hidden p-6 sm:p-10">
      {r.cancelled && <div className="pointer-events-none absolute inset-0 grid place-items-center text-6xl font-extrabold tracking-widest text-red/20 -rotate-12">CANCELLED</div>}
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-5">
        <div>
          <b className="block font-display text-xl font-extrabold">{r.issuer.name || "Receipt"}</b>
          {r.issuer.details && <p className="mt-1 text-xs whitespace-pre-line text-muted">{r.issuer.details}</p>}
        </div>
        <div className="text-right">
          <div className="text-[10px] font-extrabold tracking-[1.5px] text-teal uppercase">Payment receipt</div>
          <b className="num block text-lg">{r.number}</b>
          <small className="text-xs text-muted">Issued {dmy(r.issued_on)}</small>
        </div>
      </header>
      <p className="py-5 text-sm leading-relaxed">
        Received with thanks from <b>{r.party}</b> the sum of <b>{inr(r.amount_paise)}</b> ({r.amount_in_words}) on <b>{dmy(r.payment_date)}</b>
        {r.payment_mode ? <> by {r.payment_mode.toLowerCase()}</> : null}{r.reference ? <> (ref. {r.reference})</> : null}, towards loan <b>LN-{r.loan_id}</b>.
      </p>
      <table className="w-full text-sm">
        <tbody>
          {rows.filter(([, v]) => v).map(([k, v]) => <tr key={k}><td className="border-b border-line py-2 text-muted">{k}</td><td className="num border-b border-line py-2 text-right">{inr(v)}</td></tr>)}
          <tr><td className="py-2 font-bold">Total received</td><td className="num py-2 text-right font-extrabold">{inr(r.amount_paise)}</td></tr>
        </tbody>
      </table>
      <div className="mt-5 grid gap-3 rounded-lg bg-field p-4 text-sm sm:grid-cols-2">
        <div><small className="block text-xs text-muted">Principal outstanding after this</small><b className="num">{inr(r.principal_left_paise)}</b></div>
        <div><small className="block text-xs text-muted">Interest due after this</small><b className="num">{inr(r.interest_left_paise)}</b></div>
      </div>
      {r.cancelled && <p className="mt-4 text-sm text-redink">This receipt was cancelled{r.cancel_reason ? `: ${r.cancel_reason}` : "."}</p>}
      <footer className="mt-8 flex items-end justify-between text-xs text-muted">
        <span>Computer-generated receipt.</span>
        <span className="border-t border-line pt-1">{r.issuer.name}</span>
      </footer>
    </article>
  );
}
