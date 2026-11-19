import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { dshort, inr, plural } from "../lib/format";
import type { BankImport as BI, BankTxn } from "../lib/types";
import { Card, CardHead, cx, Empty, ErrorBox, Loading, Metric, PageHead, Segmented, Tag, useAction } from "../components/ui";

type Page = { import: BI; counts: Record<string, number>; show: string; exact: number; txns: BankTxn[];
  loans: { id: number; party_name: string; direction: string; book: string }[] };

const STATUS: Record<string, [string, "amber" | "green" | "violet" | "gray"]> = {
  NEW: ["Open", "amber"], LINKED: ["Linked", "green"], RECORDED: ["Recorded", "violet"], IGNORED: ["Not loan money", "gray"] };

function Txn({ t, loans }: { t: BankTxn; loans: Page["loans"] }) {
  const { canEdit } = useSession();
  const [loan, setLoan] = useState("");
  const [note, setNote] = useState("");
  const link = useAction((entry: number) => post(`/api/bank/txns/${t.id}/link`, { entry_id: entry }).then(() => ({ message: `Linked to entry #${entry}.` })));
  const record = useAction((loanId: number) => post<{ entry_id: number }>(`/api/bank/txns/${t.id}/record`, { loan_id: loanId }).then(() => ({ message: `Payment of ${inr(Math.abs(t.amount_paise))} recorded on LN-${loanId}.` })));
  const ignore = useAction(() => post(`/api/bank/txns/${t.id}/ignore`, { note }).then(() => ({ message: "Marked as not loan money." })));
  const undo = useAction(() => post(`/api/bank/txns/${t.id}/undo`).then(() => ({ message: "Back to open." })));
  const credit = t.amount_paise > 0;
  const busy = link.isPending || record.isPending || ignore.isPending || undo.isPending;
  const [label, tone] = STATUS[t.status];
  return (
    <div className={cx("border-b border-line py-4 last:border-0", busy && "opacity-60")}>
      <div className="flex flex-wrap items-start gap-3">
        <span className="w-16 shrink-0 text-xs font-bold">{dshort(t.txn_date)}</span>
        <span className="min-w-0 flex-1 text-xs"><b className="block break-words">{t.description}</b>
          <small className="text-[10px] text-muted">Line {t.line_no}{t.reference ? ` · ${t.reference}` : ""}{t.ledger_entry_id ? <> · entry #{t.ledger_entry_id}{t.entry_loan_id && <> on <Link className="link" to={`/loans/${t.entry_loan_id}`}>LN-{t.entry_loan_id}</Link></>}</> : ""}{t.note ? ` · ${t.note}` : ""}</small></span>
        <b className={cx("num text-sm", credit ? "text-green" : "text-redink")}>{credit ? "+" : "−"}{inr(Math.abs(t.amount_paise))}</b>
        <Tag tone={tone}>{label}</Tag>
      </div>
      {canEdit && t.status === "NEW" && (
        <div className="mt-3 grid gap-2 sm:pl-[76px]">
          {t.suggestions.map((s, i) => (
            <div key={i} className={cx("flex flex-wrap items-center gap-2 rounded-lg border p-2.5 text-xs", s.strong ? "border-teal bg-tealsoft" : "border-line")}>
              <span className="min-w-0 flex-1">{s.kind === "EXISTING" ? "Already in your ledger: " : `${credit ? "Payment from" : "Payment to"} ${s.party_name ?? ""} (LN-${s.loan_id}): `}<span className="text-muted">{s.reason}</span></span>
              {s.kind === "EXISTING" && s.entry_id
                ? <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => link.mutate(s.entry_id!)}>Link to entry #{s.entry_id}</button>
                : <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => record.mutate(s.loan_id)}>Record on LN-{s.loan_id}</button>}
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label="Record on another loan" className="input min-h-[34px] w-auto flex-1 py-1 text-xs" value={loan} onChange={(e) => setLoan(e.target.value)}>
              <option value="">Record on another loan…</option>
              {loans.filter((l) => (credit ? l.direction === "GIVEN" : l.direction === "TAKEN")).map((l) => <option key={l.id} value={l.id}>LN-{l.id} · {l.party_name}</option>)}
            </select>
            <button type="button" className="btn btn-sm" disabled={!loan || busy} onClick={() => record.mutate(Number(loan))}>Record</button>
            <input aria-label="Why it is not loan money" className="input min-h-[34px] w-auto flex-1 py-1 text-xs" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => ignore.mutate(undefined)}>Not loan money</button>
          </div>
        </div>
      )}
      {canEdit && t.status !== "NEW" && t.status !== "RECORDED" && (
        <div className="mt-2 sm:pl-[76px]"><button type="button" className="link" disabled={busy} onClick={() => undo.mutate(undefined)}>Undo</button></div>
      )}
    </div>
  );
}

export default function BankImportPage() {
  const { id } = useParams();
  const { canEdit } = useSession();
  const [show, setShow] = useState<"open" | "all">("open");
  const q = useQuery({ queryKey: ["bank-import", id, show], queryFn: () => api<Page>(`/api/ui/bank/imports/${id}?show=${show}`) });
  const exact = useAction(() => post(`/api/bank/imports/${id}/link-exact`));
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error ?? "Not found"} />;
  const d = q.data, c = d.counts;
  return (
    <>
      <PageHead eyebrow={`Bank import · ${d.import.account}`} title={d.import.file_name} scope={false}
        sub={<>{dshort(d.import.period_start)} – {dshort(d.import.period_end)} · {plural(d.import.new_count, "line")} imported · <Link className="link text-[13px]" to="/bank">All statements</Link></>}
        actions={<Segmented label="Show" value={show} onChange={setShow} options={[["open", `Open (${c.NEW})`], ["all", "All lines"]]} />} />
      <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
        <Metric label="To review" icon="inbox" tone="amber" value={c.NEW} sub="Open lines" />
        <Metric label="Linked" icon="link" tone="green" value={c.LINKED} sub="Already in the ledger" />
        <Metric label="Recorded" icon="plus" tone="violet" value={c.RECORDED} sub="New payments from the bank" />
        <Metric label="Not loan money" icon="x" tone="gray" value={c.IGNORED} sub="Ignored lines" />
      </div>
      <Card>
        <CardHead title={show === "open" ? "Lines to review" : "All lines"} sub="Suggestions use the amount, date, and the name or phone in the narration."
          right={canEdit && d.exact > 0 && <button type="button" className="btn btn-sm btn-primary" disabled={exact.isPending} onClick={() => exact.mutate(undefined)}>Link {d.exact} already recorded</button>} />
        {d.txns.length ? d.txns.map((t) => <Txn key={t.id} t={t} loans={d.loans} />) : <Empty title="All reviewed" text="Every line of this statement is linked, recorded or marked as not loan money." />}
      </Card>
    </>
  );
}
