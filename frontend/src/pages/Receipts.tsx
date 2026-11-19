import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { dshort, inr, plural } from "../lib/format";
import type { Receipt } from "../lib/types";
import { Icon } from "../components/Icon";
import { Alert, Card, cx, Empty, ErrorBox, Loading, PageHead, PersonCell, TableWrap, Tag, useAction } from "../components/ui";

export default function Receipts() {
  const { canEdit } = useSession();
  const [q, setQ] = useState("");
  const r = useQuery({ queryKey: ["receipts", q], queryFn: () => api<{ receipts: Receipt[]; missing: number }>(`/api/ui/receipts?q=${encodeURIComponent(q)}`) });
  const backfill = useAction(() => post("/api/receipts/backfill", {}));
  return (
    <>
      <PageHead eyebrow="Records" title="Receipts" scope={false} sub="A numbered receipt for every payment you receive. Open one to print or send it." />
      {r.data && r.data.missing > 0 && canEdit && (
        <div className="mb-4"><Alert action={<button className="btn btn-sm" disabled={backfill.isPending} onClick={() => backfill.mutate(undefined)}>Issue them now</button>}>
          {plural(r.data.missing, "older payment")} {r.data.missing === 1 ? "has" : "have"} no receipt yet.</Alert></div>
      )}
      <Card>
        <div className="relative mb-4 sm:w-72"><Icon name="search" className="absolute top-3 left-3 size-4 text-muted" />
          <input className="input min-h-[38px] pl-9 text-xs" placeholder="Receipt number or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search receipts" /></div>
        {r.error && <ErrorBox error={r.error} />}
        {r.isLoading ? <Loading /> : r.data?.receipts.length ? (
          <TableWrap>
            <thead><tr>{["Receipt", "From", "Paid on", "Amount", "Interest", "Principal", ""].map((h, i) => <th key={i} className={cx("th", i >= 3 && i <= 5 && "text-right")}>{h}</th>)}</tr></thead>
            <tbody>{r.data.receipts.map((x) => (
              <tr key={x.id} className={cx("hover:bg-field", x.cancelled && "opacity-50")}>
                <td className="td"><Link to={`/receipts/${x.id}`} className="font-bold hover:text-teal">{x.number}</Link>{x.cancelled && <> <Tag tone="red">Cancelled</Tag></>}</td>
                <td className="td"><PersonCell name={x.party} sub={`LN-${x.loan_id}`} to={`/loans/${x.loan_id}`} /></td>
                <td className="td">{dshort(x.payment_date)}</td>
                <td className="td num text-right font-bold">{inr(x.amount_paise)}</td>
                <td className="td num text-right">{inr(x.interest_paise + x.penalty_paise)}</td>
                <td className="td num text-right">{inr(x.principal_paise)}</td>
                <td className="td text-right"><Link to={`/receipts/${x.id}`} className="link">Open →</Link></td>
              </tr>))}
            </tbody>
          </TableWrap>
        ) : <Empty icon="receipt" title={q ? "No receipts match" : "No receipts yet"} text="Receipts are issued automatically when you record a payment received." />}
      </Card>
    </>
  );
}
