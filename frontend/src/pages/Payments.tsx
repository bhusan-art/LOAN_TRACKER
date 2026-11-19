import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dshort, inr, plural } from "../lib/format";
import type { PaymentRow } from "../lib/types";
import { Icon } from "../components/Icon";
import { Card, cx, Empty, ErrorBox, Loading, Metric, PageHead, PersonCell, Segmented, TableWrap, Tag } from "../components/ui";
import { useRecordPayment } from "../components/Shell";

export default function Payments() {
  const { canEdit, book } = useSession();
  const pay = useRecordPayment();
  const [params, setParams] = useSearchParams();
  const [dir, setDir] = useState<"" | "IN" | "OUT">("");
  const [q, setQ] = useState("");
  const [month, setMonth] = useState("");
  useEffect(() => {
    if (params.get("record") === "1" && canEdit) { pay(); setParams({}, { replace: true }); }
  }, [params, canEdit, pay, setParams]);
  const r = useQuery({ queryKey: ["payments", book], queryFn: () => api<PaymentRow[]>("/api/ui/payments") });
  const all = r.data ?? [];
  const months = [...new Set(all.map((p) => p.entry_date.slice(0, 7)))];
  const rows = useMemo(() => all.filter((p) => (!dir || p.direction === dir) && (!month || p.entry_date.startsWith(month))
    && (!q.trim() || p.party_name.toLowerCase().includes(q.trim().toLowerCase()) || (p.reference ?? "").toLowerCase().includes(q.trim().toLowerCase()))), [all, dir, month, q]);
  const live = rows.filter((p) => !p.reversed);
  const sum = (d: string) => live.filter((p) => p.direction === d).reduce((a, p) => a + p.amount_paise, 0);
  const split = (k: string) => live.filter((p) => p.direction === "IN").reduce((a, p) => a + (p.split?.[k] ?? 0), 0);
  return (
    <>
      <PageHead eyebrow="Workspace" title="Payments" sub="Payments received from borrowers and payments you made to lenders."
        actions={canEdit && <button type="button" className="btn btn-primary" onClick={() => pay()}><Icon name="plus" className="size-4" />Record payment</button>} />
      {r.error && <ErrorBox error={r.error} />}
      {r.isLoading ? <Loading /> : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Received" icon="down" tone="green" value={inr(sum("IN"), false)} sub={plural(live.filter((p) => p.direction === "IN").length, "payment")} />
            <Metric label="Paid out" icon="up" tone="amber" value={inr(sum("OUT"), false)} sub={plural(live.filter((p) => p.direction === "OUT").length, "payment")} />
            <Metric label="Interest received" icon="percent" tone="teal" value={inr(split("INTEREST") + split("PENALTY"), false)} sub="incl. penalties" />
            <Metric label="Principal received" icon="wallet" tone="violet" value={inr(split("PRINCIPAL"), false)} sub="Money coming back" />
          </div>
          <Card>
            <div className="mb-4 flex flex-wrap items-center gap-2.5">
              <Segmented label="Direction" value={dir} onChange={setDir} options={[["", "All"], ["IN", "Received"], ["OUT", "Paid out"]]} />
              <select aria-label="Month" className="input min-h-[36px] w-auto py-1.5 text-xs" value={month} onChange={(e) => setMonth(e.target.value)}>
                <option value="">All months</option>
                {months.map((m) => <option key={m} value={m}>{new Date(m + "-01T00:00:00Z").toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" })}</option>)}
              </select>
              <div className="relative ml-auto w-full sm:w-64">
                <Icon name="search" className="absolute top-3 left-3 size-4 text-muted" />
                <input className="input min-h-[38px] pl-9 text-xs" placeholder="Name or reference" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search payments" />
              </div>
            </div>
            {rows.length ? (
              <TableWrap>
                <thead><tr>{["Date", "Person", "Direction", "Amount", "Split", "Mode", "Receipt"].map((h, i) => <th key={h} className={cx("th", i === 3 && "text-right")}>{h}</th>)}</tr></thead>
                <tbody>{rows.map((p) => (
                  <tr key={p.id} className={cx("hover:bg-field", p.reversed && "opacity-50")}>
                    <td className="td">{dshort(p.entry_date)}</td>
                    <td className="td"><PersonCell name={p.party_name} to={`/loans/${p.loan_id}`} tone={p.direction === "IN" ? "green" : "amber"} sub={`LN-${p.loan_id} · ${p.book === "BUSINESS" ? "Business" : "Personal"}`} /></td>
                    <td className="td">{p.direction === "IN" ? <Tag tone="green">Received</Tag> : <Tag tone="amber">Paid out</Tag>}{p.reversed && <> <Tag tone="red">Reversed</Tag></>}</td>
                    <td className={cx("td num text-right font-bold", p.reversed && "line-through")}>{inr(p.amount_paise)}</td>
                    <td className="td text-[11px] text-muted">{p.split ? Object.entries(p.split).filter(([, v]) => v).map(([k, v]) => `${k.toLowerCase()} ${inr(v)}`).join(" · ") : "—"}</td>
                    <td className="td">{p.payment_mode}{p.reference && <small className="block text-[10px] text-muted">{p.reference}</small>}</td>
                    <td className="td">{p.receipt ? <Link className="link" to={`/receipts/${p.receipt.id}`}>{p.receipt.number}</Link> : "—"}</td>
                  </tr>))}
                </tbody>
              </TableWrap>
            ) : <Empty icon="outbox" title={all.length ? "No payments match" : "No payments yet"} text={all.length ? "Change the filters." : "Payments you record show up here."} />}
          </Card>
        </>
      )}
    </>
  );
}
