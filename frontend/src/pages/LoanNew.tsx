import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { Card, CardHead, Field, FormFoot, formObject, Modal, PageHead, Segmented, Submit, useAction } from "../components/ui";

export const PAY_MODES: [string, string][] = [["CASH", "Cash"], ["UPI", "UPI"], ["BANK", "Bank transfer"]];

export function AddPerson({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: (id: number, name: string) => void }) {
  const act = useAction((b: Record<string, string>) => post<{ id: number; name: string; message?: string }>("/api/parties", b),
    { onDone: (r) => { onAdded(r.id, r.name); onClose(); } });
  return (
    <Modal open={open} onClose={onClose} title="Add a person">
      <form onSubmit={(e) => { e.preventDefault(); act.mutate(formObject(e.currentTarget)); }}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name *" full>{(id) => <input id={id} name="name" className="input" required maxLength={200} />}</Field>
          <Field label="Phone">{(id) => <input id={id} name="phone" className="input" inputMode="tel" maxLength={40} />}</Field>
          <Field label="Address">{(id) => <input id={id} name="address" className="input" maxLength={500} />}</Field>
          <Field label="Notes" full>{(id) => <textarea id={id} name="notes" className="input min-h-20" maxLength={2000} />}</Field>
        </div>
        <FormFoot><button type="button" className="btn" onClick={onClose}>Cancel</button><Submit busy={act.isPending}>Add person</Submit></FormFoot>
      </form>
    </Modal>
  );
}

export default function LoanNew() {
  const { session } = useSession();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [direction, setDirection] = useState(params.get("direction") === "TAKEN" ? "TAKEN" : "GIVEN");
  const [party, setParty] = useState(params.get("party_id") ?? "");
  const [cycle, setCycle] = useState("MONTHLY");
  const [adding, setAdding] = useState(false);
  const people = useQuery({ queryKey: ["parties-min"], queryFn: () => api<{ id: number; name: string; phone: string | null }[]>("/api/ui/parties-min") });
  const act = useAction((b: Record<string, string>) => post<{ id: number; message: string }>("/api/ui/loans",
    { ...b, party_id: Number(b.party_id), grace_days: Number(b.grace_days || 3) }), { onDone: (r) => nav(`/loans/${r.id}`) });
  const given = direction === "GIVEN";
  return (
    <>
      <PageHead eyebrow="Quick action" title={given ? "Lend money" : "Borrow money"} scope={false}
        sub={given ? "Record money you gave someone. Interest dues are worked out from these terms." : "Record money you took from someone, so you know what you owe and when."} />
      <form onSubmit={(e) => { e.preventDefault(); act.mutate(formObject(e.currentTarget)); }} className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHead title="Loan terms" right={<Segmented label="Direction" value={direction} onChange={setDirection} options={[["GIVEN", "I lent"], ["TAKEN", "I borrowed"]]} />} />
          <input type="hidden" name="direction" value={direction} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={given ? "Borrower *" : "Lender *"} full hint={<>Not in the list? <button type="button" className="link" onClick={() => setAdding(true)}>Add a new person</button></>}>
              {(id) => (
                <select id={id} name="party_id" className="input" required value={party} onChange={(e) => setParty(e.target.value)}>
                  <option value="">Choose a person…</option>
                  {(people.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}{p.phone ? ` · ${p.phone}` : ""}</option>)}
                </select>)}
            </Field>
            <Field label="Book *">{() => (
              <div className="flex gap-2">
                {[["BUSINESS", "Business"], ["PERSONAL", "Personal"]].map(([v, l], i) => (
                  <label key={v} className="flex flex-1 cursor-pointer items-center gap-2 rounded-lg border border-line bg-field px-3 py-2.5 text-xs font-semibold has-[:checked]:border-teal has-[:checked]:bg-tealsoft has-[:checked]:text-teal">
                    <input type="radio" name="book" value={v} defaultChecked={i === 0} className="accent-teal" />{l}
                  </label>))}
              </div>)}
            </Field>
            <Field label="Amount (₹) *">{(id) => <input id={id} name="principal" className="input num" inputMode="decimal" required placeholder="1,00,000" />}</Field>
            <Field label="Start date">{(id) => <input id={id} name="start_date" type="date" className="input" defaultValue={session?.today} max={session?.today} />}</Field>
            <Field label="Interest rate (%) *">{(id) => <input id={id} name="rate" className="input num" inputMode="decimal" required placeholder="2" />}</Field>
            <Field label="Per">{(id) => <select id={id} name="rate_unit" className="input"><option value="PER_MONTH">Month</option><option value="PER_YEAR">Year</option></select>}</Field>
            <Field label="Interest paid">{(id) => (
              <select id={id} name="interest_cycle" className="input" value={cycle} onChange={(e) => setCycle(e.target.value)}>
                <option value="MONTHLY">Monthly</option><option value="QUARTERLY">Quarterly</option><option value="AT_MATURITY">At maturity</option>
              </select>)}
            </Field>
            <Field label="Due day of month" hint="Empty: the start date's day.">{(id) => <input id={id} name="due_day" type="number" min={1} max={31} className="input" />}</Field>
            <Field label="Grace days">{(id) => <input id={id} name="grace_days" type="number" min={0} max={60} defaultValue={3} className="input" />}</Field>
            <Field label={`Maturity date${cycle === "AT_MATURITY" ? " *" : ""}`} hint='Optional. Required for "at maturity".'>{(id) => (
              <input id={id} name="maturity_date" type="date" className="input" required={cycle === "AT_MATURITY"} />)}
            </Field>
          </div>
        </Card>
        <Card>
          <CardHead title="Details" sub="Optional, for your records" />
          <div className="grid gap-4">
            <Field label={given ? "Paid out by" : "Received by"}>{(id) => (
              <select id={id} name="payment_mode" className="input">{PAY_MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>)}
            </Field>
            <Field label="Purpose">{(id) => <input id={id} name="purpose" className="input" maxLength={500} />}</Field>
            <Field label="Collateral">{(id) => <input id={id} name="collateral" className="input" maxLength={500} placeholder="e.g. gold chain 20 g" />}</Field>
          </div>
          <FormFoot>
            <Link to="/loans" className="btn">Cancel</Link>
            <Submit busy={act.isPending}>{given ? "Save loan given" : "Save loan taken"}</Submit>
          </FormFoot>
        </Card>
      </form>
      <AddPerson open={adding} onClose={() => setAdding(false)} onAdded={(id) => { setParty(String(id)); people.refetch(); }} />
    </>
  );
}
