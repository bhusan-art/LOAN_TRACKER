import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { dshort, inr, rateText } from "../lib/format";
import type { FundingPage } from "../lib/types";
import { Alert, Card, CardHead, cx, Empty, ErrorBox, Field, FormFoot, formObject, Loading, Metric, PageHead, Submit, TableWrap, Tag, useAction } from "../components/ui";

export default function Funding() {
  const { session, canEdit } = useSession();
  const q = useQuery({ queryKey: ["funding"], queryFn: () => api<FundingPage>("/api/ui/funding") });
  const [t, setT] = useState("");
  const [g, setG] = useState("");
  const act = useAction((b: Record<string, string>) => post("/api/ui/funding", { ...b, taken_loan_id: Number(b.taken_loan_id), given_loan_id: Number(b.given_loan_id), confirm_cross_book: b.confirm_cross_book === "1" }));
  const d = q.data;
  const taken = d?.taken.find((x) => String(x.id) === t);
  const given = d?.given.find((x) => String(x.id) === g);
  const cross = taken && given && taken.book !== given.book;
  return (
    <>
      <PageHead eyebrow="Analyse" title="Funding links" scope={false}
        sub="Link money you borrowed to the loans it paid for, to see the spread you earn and the risk if a borrower stops paying." />
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : d && (
        <div className="grid gap-4">
          <div className="grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Links" icon="link" tone="teal" value={d.links.length} sub={inr(d.links.reduce((a, l) => a + l.amount_paise, 0), false) + " linked"} />
            <Metric label="Spread a month" icon="percent" tone="green" value={inr(d.links.reduce((a, l) => a + l.spread.spread_paise_per_cycle, 0), false)} sub="Interest earned − interest paid" />
            <Metric label="Negative spreads" icon="alert" tone="red" value={d.links.filter((l) => l.spread.negative).length} sub="You pay more than you earn" />
            <Metric label="Idle borrowed money" icon="clock" tone="amber" value={inr(d.idle_cost_paise_per_cycle, false)} sub="Cost a month of unlinked borrowing" />
          </div>
          <Card>
            <CardHead title="Links" icon="link" />
            {d.links.length ? (
              <TableWrap>
                <thead><tr>{["Borrowed from", "Lent to", "Linked", "Still out", "Spread / month"].map((h, i) => <th key={h} className={cx("th", i >= 2 && "text-right")}>{h}</th>)}</tr></thead>
                <tbody>{d.links.map((l) => (
                  <tr key={l.id}>
                    <td className="td"><Link to={`/loans/${l.taken_loan_id}`} className="font-bold hover:text-teal">{l.taken.party_name}</Link><small className="block text-[10px] text-muted">LN-{l.taken_loan_id} · {rateText(l.taken.rate, l.taken.rate_unit)}</small></td>
                    <td className="td"><Link to={`/loans/${l.given_loan_id}`} className="font-bold hover:text-teal">{l.given.party_name}</Link><small className="block text-[10px] text-muted">LN-{l.given_loan_id} · {rateText(l.given.rate, l.given.rate_unit)}{l.spread.cross_book && <> · <Tag tone="amber">cross-book</Tag></>}</small></td>
                    <td className="td num text-right">{inr(l.amount_paise, false)}<small className="block text-[10px] text-muted">since {dshort(l.link_date)}</small></td>
                    <td className="td num text-right">{inr(l.spread.exposure_paise, false)}</td>
                    <td className={cx("td num text-right font-bold", l.spread.negative ? "text-redink" : "text-green")}>{inr(l.spread.spread_paise_per_cycle)}</td>
                  </tr>))}
                </tbody>
              </TableWrap>
            ) : <Empty icon="link" title="No links yet" text="Link a loan you took to the loan you gave from that money." />}
          </Card>
          {canEdit && (
            <Card>
              <CardHead title="Add a link" icon="plus" />
              <form onSubmit={(e) => { e.preventDefault(); const f = e.currentTarget; act.mutate(formObject(f), { onSuccess: () => { f.reset(); setT(""); setG(""); } }); }}>
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <Field label="Borrowed money (taken loan) *">{(id) => <select id={id} name="taken_loan_id" className="input" required value={t} onChange={(e) => setT(e.target.value)}>
                    <option value="">Choose…</option>{d.taken.map((x) => <option key={x.id} value={x.id}>LN-{x.id} · {x.party_name} · {inr(x.unlinked_paise, false)} unlinked</option>)}</select>}</Field>
                  <Field label="Lent to (given loan) *">{(id) => <select id={id} name="given_loan_id" className="input" required value={g} onChange={(e) => setG(e.target.value)}>
                    <option value="">Choose…</option>{d.given.map((x) => <option key={x.id} value={x.id}>LN-{x.id} · {x.party_name} · {inr(x.unfunded_paise, false)} unfunded</option>)}</select>}</Field>
                  <Field label="Amount (₹) *" hint={taken && given ? `Up to ${inr(Math.min(taken.unlinked_paise, given.unfunded_paise), false)}` : undefined}>
                    {(id) => <input id={id} name="amount" className="input num" inputMode="decimal" required />}</Field>
                  <Field label="Date">{(id) => <input id={id} name="link_date" type="date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
                </div>
                {cross && <div className="mt-4"><Alert>This mixes business and personal money.
                  <label className="mt-2 flex items-center gap-2 font-bold"><input type="checkbox" name="confirm_cross_book" value="1" required className="accent-teal" /> I understand, link them anyway</label></Alert></div>}
                <FormFoot><Submit busy={act.isPending}>Save link</Submit></FormFoot>
              </form>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
