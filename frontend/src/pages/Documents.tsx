import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { inr } from "../lib/format";
import type { Doc } from "../lib/types";
import { Icon } from "../components/Icon";
import { Alert, Card, CardHead, ErrorBox, Field, Loading, PageHead } from "../components/ui";
import { DocList, DocUpload } from "../components/Docs";

type Page = { documents: Doc[]; problem: string | null; people: { id: number; name: string }[];
  loans: { id: number; party_id: number; direction: string; principal_paise: number }[] };

export default function Documents() {
  const [q, setQ] = useState("");
  const [party, setParty] = useState("");
  const r = useQuery({ queryKey: ["documents"], queryFn: () => api<Page>("/api/ui/documents") });
  const docs = useMemo(() => {
    const n = q.trim().toLowerCase();
    return (r.data?.documents ?? []).filter((d) => !n || d.title.toLowerCase().includes(n) || (d.party_name ?? "").toLowerCase().includes(n));
  }, [r.data, q]);
  return (
    <>
      <PageHead eyebrow="Records" title="Documents" scope={false}
        sub="ID proofs, agreements and cheque photos, encrypted on disk. Add one below, or from a person's or loan's page." />
      {r.error && <ErrorBox error={r.error} />}
      {r.isLoading ? <Loading /> : r.data && (r.data.problem ? <Alert>Documents are switched off: {r.data.problem}. Run <code>python -m loan_tracker.cli gen-doc-key</code>, put the key in <code>.env</code> as DOCUMENT_KEY and restart.</Alert> : (
        <div className="grid gap-4">
          <Card id="upload">
            <CardHead title="Add a document" icon="shield" sub="Encrypted on disk · PDF or photo up to 10 MB" />
            {r.data.people.length ? (
              <DocUpload extra={<>
                <Field label="Person *">{(id) => (
                  <select id={id} name="party_id" className="input" required value={party} onChange={(e) => setParty(e.target.value)}>
                    <option value="">Choose a person…</option>{r.data.people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>)}</Field>
                <Field label="Loan (optional)">{(id) => (
                  <select id={id} name="loan_id" className="input" key={party}>
                    <option value="">Not for one loan</option>
                    {r.data.loans.filter((l) => String(l.party_id) === party).map((l) => (
                      <option key={l.id} value={l.id}>LN-{l.id} · {l.direction === "GIVEN" ? "lent" : "borrowed"} {inr(l.principal_paise, false)}</option>))}
                  </select>)}</Field>
              </>} />
            ) : <p className="text-xs text-muted">Add a person first; documents belong to a person or a loan.</p>}
          </Card>
          <Card>
            <CardHead title="All documents" right={
              <div className="relative w-56"><Icon name="search" className="absolute top-3 left-3 size-4 text-muted" />
                <input className="input min-h-[38px] pl-9 text-xs" placeholder="Title or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search documents" /></div>} />
            <DocList docs={docs} showLoan />
          </Card>
        </div>
      ))}
    </>
  );
}
