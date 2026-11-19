import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dshort, inr, plural } from "../lib/format";
import type { Loan, TodayItem } from "../lib/types";
import { Icon } from "../components/Icon";
import { BookTag, Card, CardHead, DirTag, Empty, ErrorBox, Loading, PageHead, PersonCell, TableWrap } from "../components/ui";

type R = { fy: { fy: string; book: string; earned: number; paid: number; received: number; paid_out: number; net: number }[];
  overdue: TodayItem[]; cross_book: { link: { id: number; amount_paise: number; link_date: string }; taken: Loan; given: Loan }[] };

export default function Reports() {
  const { book } = useSession();
  const q = useQuery({ queryKey: ["reports", book], queryFn: () => api<R>("/api/ui/reports") });
  const d = q.data;
  return (
    <>
      <PageHead eyebrow="Analyse" title="Reports" sub="Interest by financial year (for your ITR), overdue dues and cross-book funding."
        actions={<a className="btn" href="/export.csv"><Icon name="download" className="size-4" />Export CSV</a>} />
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : d && (
        <div className="grid gap-4">
          <Card>
            <CardHead title="Interest by financial year" icon="percent" sub="Earned / cost by due date; received / paid by payment date. April to March." />
            {d.fy.length ? (
              <TableWrap>
                <thead><tr>{["Year", "Book", "Earned", "Received", "Cost", "Paid", "Net"].map((h, i) => <th key={h} className={`th ${i > 1 ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
                <tbody>{d.fy.map((r) => (
                  <tr key={r.fy + r.book}><td className="td font-bold">{r.fy}</td><td className="td"><BookTag book={r.book} /></td>
                    <td className="td num text-right">{inr(r.earned)}</td><td className="td num text-right">{inr(r.received)}</td>
                    <td className="td num text-right">{inr(r.paid)}</td><td className="td num text-right">{inr(r.paid_out)}</td>
                    <td className="td num text-right font-extrabold">{inr(r.net)}</td></tr>))}
                </tbody>
              </TableWrap>
            ) : <Empty icon="percent" title="No interest yet" />}
          </Card>
          <Card>
            <CardHead title="Overdue" icon="alert" iconTone="red" sub={`${plural(d.overdue.length, "loan")} with unpaid dues past grace`} />
            {d.overdue.length ? (
              <TableWrap>
                <thead><tr><th className="th">Who</th><th className="th">Type</th><th className="th">Due date</th><th className="th text-right">Left</th><th className="th text-right">Late</th></tr></thead>
                <tbody>{d.overdue.map((o) => (
                  <tr key={`${o.loan_id}-${o.due_date}`}><td className="td"><PersonCell name={o.party} to={`/loans/${o.loan_id}`} sub={`LN-${o.loan_id}${o.count > 1 ? ` · ${o.count} dues` : ""}`} tone="red" /></td>
                    <td className="td"><DirTag direction={o.direction} /></td><td className="td">{dshort(o.due_date)}</td>
                    <td className="td num text-right font-bold">{inr(o.amount_paise)}</td><td className="td num text-right text-redink">{plural(o.overdue_days, "day")}</td></tr>))}
                </tbody>
              </TableWrap>
            ) : <Empty title="Nothing overdue" />}
          </Card>
          <Card>
            <CardHead title="Cross-book funding" icon="link" sub="Business money used for personal lending, or the other way round" />
            {d.cross_book.length ? d.cross_book.map((c) => (
              <div key={c.link.id} className="flex flex-wrap items-center gap-2 border-b border-line py-3 text-xs last:border-0">
                <Link to={`/loans/${c.taken.id}`} className="font-bold hover:text-teal">{c.taken.party_name} (LN-{c.taken.id})</Link> <BookTag book={c.taken.book} />
                <span className="text-muted">funds</span>
                <Link to={`/loans/${c.given.id}`} className="font-bold hover:text-teal">{c.given.party_name} (LN-{c.given.id})</Link> <BookTag book={c.given.book} />
                <b className="num ml-auto">{inr(c.link.amount_paise, false)}</b>
              </div>)) : <Empty title="No cross-book links" text="Every link keeps business and personal money apart." />}
          </Card>
        </div>
      )}
    </>
  );
}
