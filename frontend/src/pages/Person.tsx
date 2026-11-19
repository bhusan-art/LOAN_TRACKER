import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { dmy, inr, rateText } from "../lib/format";
import type { PartyPage } from "../lib/types";
import { Icon } from "../components/Icon";
import { Avatar, Card, CardHead, DirTag, Empty, ErrorBox, Field, FormFoot, formObject, Loading, Metric, PageHead, Row, StatusTag, Submit, TableWrap, useAction } from "../components/ui";
import { DocumentsCard } from "../components/Docs";

export default function Person() {
  const { id } = useParams();
  const { canEdit, book } = useSession();
  const q = useQuery({ queryKey: ["party", id, book], queryFn: () => api<PartyPage>(`/api/ui/parties/${id}`) });
  const save = useAction((b: Record<string, string>) => post(`/api/parties/${id}`, b));
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error ?? "Not found"} />;
  const { party: p, loans, summary: s } = q.data;
  const tel = p.phone?.replace(/[^\d+]/g, "");
  return (
    <>
      <PageHead eyebrow="Person" title={<span className="flex items-center gap-3"><Avatar name={p.name} size={44} />{p.name}</span>}
        sub={<>{p.phone ?? "No phone"}{p.address ? ` · ${p.address}` : ""}{p.risk_tag ? ` · ${p.risk_tag}` : ""}</>}
        actions={<>
          {tel && <a className="btn" href={`tel:${tel}`}><Icon name="phone" className="size-4" />Call</a>}
          {canEdit && <Link className="btn btn-primary" to={`/loans/new?direction=GIVEN&party_id=${p.id}`}><Icon name="up" className="size-4" />Lend</Link>}
          {canEdit && <Link className="btn" to={`/loans/new?direction=TAKEN&party_id=${p.id}`}><Icon name="down" className="size-4" />Borrow</Link>}
        </>} />
      <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
        <Metric label="They owe you" icon="up" tone="teal" value={inr(s.lent_paise, false)} sub="Principal outstanding" />
        <Metric label="You owe them" icon="down" tone="amber" value={inr(s.taken_paise, false)} sub="Principal outstanding" />
        <Metric label="Net position" icon="diamond" tone="violet" value={inr(Math.abs(s.net_paise), false)} sub={s.net_paise >= 0 ? "In your favour" : "You owe overall"} />
        <Metric label="Overdue" icon="alert" tone="red" value={inr(s.overdue_paise, false)} sub={`${s.open_loans} open of ${s.loans} loans`} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <div className="grid min-w-0 content-start gap-4">
          <Card>
            <CardHead title="Loans" sub="Follows the book switch" />
            {loans.length ? (
              <TableWrap>
                <thead><tr>{["Loan", "Type", "Outstanding", "Rate", "Next due", "Status"].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
                <tbody>{loans.map((l) => (
                  <tr key={l.id} className="hover:bg-field">
                    <td className="td"><Link to={`/loans/${l.id}`} className="font-bold hover:text-teal">LN-{l.id}</Link><small className="block text-[10px] text-muted">{l.book === "BUSINESS" ? "Business" : "Personal"} · since {dmy(l.start_date)}</small></td>
                    <td className="td"><DirTag direction={l.direction} /></td>
                    <td className="td num font-bold">{inr(l.principal_outstanding_paise, false)}{l.overdue_paise > 0 && <small className="block text-[10px] font-normal text-redink">{inr(l.overdue_paise)} overdue</small>}</td>
                    <td className="td">{rateText(l.rate, l.rate_unit)}</td>
                    <td className="td">{dmy(l.next_due_date)}</td>
                    <td className="td"><StatusTag status={l.status} /></td>
                  </tr>))}
                </tbody>
              </TableWrap>
            ) : <Empty icon="wallet" title="No loans in this book" />}
          </Card>
          <DocumentsCard docs={q.data.documents} partyId={p.id} />
        </div>
        <Card>
          <CardHead title="Details" sub={`Added ${dmy(p.created_at)}`} />
          {canEdit ? (
            <form key={p.id + p.name} onSubmit={(e) => { e.preventDefault(); save.mutate(formObject(e.currentTarget)); }}>
              <div className="grid gap-4">
                <Field label="Name *">{(i) => <input id={i} name="name" className="input" required defaultValue={p.name} maxLength={200} />}</Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Phone">{(i) => <input id={i} name="phone" className="input" defaultValue={p.phone ?? ""} inputMode="tel" maxLength={40} />}</Field>
                  <Field label="Risk tag">{(i) => <input id={i} name="risk_tag" className="input" defaultValue={p.risk_tag ?? ""} maxLength={40} placeholder="e.g. careful" />}</Field>
                </div>
                <Field label="Address">{(i) => <input id={i} name="address" className="input" defaultValue={p.address ?? ""} maxLength={500} />}</Field>
                <Field label="ID proof reference">{(i) => <input id={i} name="id_proof_ref" className="input" defaultValue={p.id_proof_ref ?? ""} maxLength={200} />}</Field>
                <Field label="Guarantor">{(i) => <input id={i} name="guarantor" className="input" defaultValue={p.guarantor ?? ""} maxLength={200} />}</Field>
                <Field label="Notes">{(i) => <textarea id={i} name="notes" className="input min-h-24" defaultValue={p.notes ?? ""} maxLength={2000} />}</Field>
              </div>
              <FormFoot><Submit busy={save.isPending}>Save details</Submit></FormFoot>
            </form>
          ) : (
            <div>
              {([["Phone", p.phone], ["Address", p.address], ["ID proof", p.id_proof_ref], ["Guarantor", p.guarantor], ["Risk tag", p.risk_tag], ["Notes", p.notes]] as const)
                .map(([k, v]) => <Row key={k} label={k}>{v || "—"}</Row>)}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
