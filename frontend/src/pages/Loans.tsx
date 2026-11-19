import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dmy, inr, plural, rateText } from "../lib/format";
import type { LoanBrief } from "../lib/types";
import { Icon } from "../components/Icon";
import { BookTag, Card, DirTag, Empty, ErrorBox, Loading, Metric, PageHead, PersonCell, Segmented, StatusTag, TableWrap } from "../components/ui";

export default function Loans() {
  const { session, canEdit } = useSession();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const direction = params.get("direction") ?? "";
  const status = params.get("status") ?? "";
  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v); else p.delete(k);
    setParams(p, { replace: true });
  };
  const loans = useQuery({ queryKey: ["loans", session?.book], queryFn: () => api<LoanBrief[]>("/api/ui/loans") });
  const all = loans.data ?? [];
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter((l) => (!direction || l.direction === direction) && (!status || l.status === status)
      && (!needle || l.party_name.toLowerCase().includes(needle) || `ln-${l.id}` === needle || String(l.id) === needle.replace(/^ln-?/, "")));
  }, [all, q, direction, status]);
  const open = all.filter((l) => !["CLOSED", "SETTLED", "WRITTEN_OFF"].includes(l.status));
  const sum = (xs: LoanBrief[], f: (l: LoanBrief) => number) => xs.reduce((a, l) => a + f(l), 0);
  const statuses = [...new Set(all.map((l) => l.status))];
  return (
    <>
      <PageHead eyebrow="Workspace" title="Loans" sub="Manage lending and borrowing across your books."
        actions={canEdit && (
          <>
            <Link to="/loans/new?direction=GIVEN" className="btn btn-primary"><Icon name="up" className="size-4" />Lend money</Link>
            <Link to="/loans/new?direction=TAKEN" className="btn"><Icon name="down" className="size-4" />Borrow money</Link>
          </>
        )} />
      {loans.error && <ErrorBox error={loans.error} />}
      {loans.isLoading ? <Loading /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Open loans" icon="wallet" tone="teal" value={open.length} sub={`${plural(all.length - open.length, "closed loan")}`} />
            <Metric label="Lent out" icon="up" tone="green" value={inr(sum(open.filter((l) => l.direction === "GIVEN"), (l) => l.principal_outstanding_paise), false)} sub="Principal outstanding" />
            <Metric label="Borrowed" icon="down" tone="amber" value={inr(sum(open.filter((l) => l.direction === "TAKEN"), (l) => l.principal_outstanding_paise), false)} sub="Principal outstanding" />
            <Metric label="Overdue" icon="alert" tone="red" value={inr(sum(all, (l) => l.overdue_paise), false)} sub={plural(all.filter((l) => l.overdue_paise > 0).length, "loan")} />
          </div>
          <Card>
            <div className="mb-4 flex flex-wrap items-center gap-2.5">
              <Segmented label="Direction" value={direction} onChange={(v) => set("direction", v)}
                options={[["", "All"], ["GIVEN", "Lent"], ["TAKEN", "Borrowed"]]} />
              <select aria-label="Status" className="input w-auto min-h-[36px] py-1.5 text-xs" value={status} onChange={(e) => set("status", e.target.value)}>
                <option value="">Any status</option>
                {statuses.map((s) => <option key={s} value={s}>{s.charAt(0) + s.slice(1).toLowerCase().replace("_", " ")}</option>)}
              </select>
              <div className="relative ml-auto w-full sm:w-64">
                <Icon name="search" className="absolute top-3 left-3 size-4 text-muted" />
                <input className="input min-h-[38px] pl-9 text-xs" placeholder="Name or LN-12" value={q} aria-label="Search loans"
                  onChange={(e) => setQ(e.target.value)} />
              </div>
            </div>
            {rows.length ? (
              <TableWrap>
                <thead><tr>{["Person", "Type", "Principal", "Outstanding", "Rate", "Next due", "Overdue", "Status"].map((h, i) =>
                  <th key={h} className={`th ${i >= 2 && i <= 3 || i === 6 ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
                <tbody>
                  {rows.map((l) => (
                    <tr key={l.id} className="hover:bg-field">
                      <td className="td"><PersonCell name={l.party_name} to={`/loans/${l.id}`} tone={l.direction === "GIVEN" ? "teal" : "amber"}
                        sub={<>LN-{l.id} · {l.book === "BUSINESS" ? "Business" : "Personal"}</>} /></td>
                      <td className="td"><DirTag direction={l.direction} /></td>
                      <td className="td num text-right">{inr(l.principal_paise, false)}</td>
                      <td className="td num text-right font-bold">{inr(l.principal_outstanding_paise, false)}</td>
                      <td className="td">{rateText(l.rate, l.rate_unit)}</td>
                      <td className="td">{l.next_due_date ? <>{dmy(l.next_due_date)}<small className="block text-[10px] text-muted">{inr(l.next_due_paise)}</small></> : "—"}</td>
                      <td className="td num text-right">{l.overdue_paise ? <span className="font-bold text-redink">{inr(l.overdue_paise)}<small className="block text-[10px] font-normal">{plural(l.overdue_days, "day")}</small></span> : "—"}</td>
                      <td className="td"><StatusTag status={l.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </TableWrap>
            ) : <Empty icon="wallet" title={all.length ? "No loans match" : "No loans yet"}
              text={all.length ? "Change the filters or the search text." : "Record money you lent or borrowed to start."}
              action={!all.length && canEdit ? <Link to="/loans/new?direction=GIVEN" className="btn btn-primary">Lend money</Link> : undefined} />}
            {rows.length > 0 && <p className="mt-3 text-[11px] text-muted">Showing {plural(rows.length, "loan")} · <BookTag book="BUSINESS" /> and <BookTag book="PERSONAL" /> follow the book switch above.</p>}
          </Card>
        </>
      )}
    </>
  );
}
