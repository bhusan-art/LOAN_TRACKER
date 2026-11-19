import { useRef } from "react";
import { upload, post } from "../lib/api";
import { useSession } from "../lib/session";
import { kb, when } from "../lib/format";
import type { Doc } from "../lib/types";
import { Icon } from "./Icon";
import { FileButton } from "./FileButton";
import { isNative } from "../lib/native";
import { Alert, Card, CardHead, Confirm, Empty, Field, Submit, useAction } from "./ui";

export const DOC_KINDS: [string, string][] = [["ID_PROOF", "ID proof"], ["AGREEMENT", "Agreement"], ["CHEQUE", "Cheque"], ["PHOTO", "Photo"], ["OTHER", "Other"]];
export const DOC_ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp";

export function DocList({ docs, showLoan }: { docs: Doc[]; showLoan?: boolean }) {
  const { session } = useSession();
  const del = useAction((id: number) => post(`/api/documents/${id}/delete`));
  if (!docs.length) return <Empty icon="file" title="No documents yet" text="ID proofs, agreements and cheque photos are stored encrypted." />;
  return (
    <div className="divide-y divide-line">
      {docs.map((d) => (
        <div key={d.id} className="flex flex-wrap items-center gap-3 py-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-tealsoft text-teal"><Icon name="file" className="size-4" /></span>
          <div className="min-w-0 flex-1">
            <b className="block truncate text-[13px]">{d.title}</b>
            <small className="block truncate text-[11px] text-muted">
              {d.kind_label} · {kb(d.size_bytes)} · {when(d.created_at, session?.timezone)}{d.party_name ? ` · ${d.party_name}` : ""}{showLoan && d.loan_id ? ` · LN-${d.loan_id}` : ""}
            </small>
          </div>
          <div className="flex gap-1.5">
            <FileButton className="btn btn-sm" path={`/api/documents/${d.id}/file`} label="Open" icon="eye" />
            {!isNative && <FileButton className="btn btn-sm" path={`/api/documents/${d.id}/file?download=1`} label="Download" icon="download" />}
            <Confirm text={`Delete “${d.title}”? The encrypted file is erased and cannot be recovered.`} danger label="Delete" onYes={() => del.mutate(d.id)}>
              {(open) => <button type="button" className="btn btn-sm btn-danger" onClick={open} aria-label={`Delete ${d.title}`}><Icon name="trash" className="size-3.5" /></button>}
            </Confirm>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Upload form. With partyId/loanId set, the document is filed there; otherwise the caller adds those fields. */
export function DocUpload({ partyId, loanId, extra }: { partyId?: number; loanId?: number; extra?: React.ReactNode }) {
  const form = useRef<HTMLFormElement>(null);
  const act = useAction((fd: FormData) => upload("/api/documents", fd), { onDone: () => form.current?.reset() });
  return (
    <form ref={form} onSubmit={(e) => { e.preventDefault(); act.mutate(new FormData(e.currentTarget)); }}>
      {partyId !== undefined && <input type="hidden" name="party_id" value={partyId} />}
      {loanId !== undefined && <input type="hidden" name="loan_id" value={loanId} />}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {extra}
        <Field label="Kind">{(id) => <select id={id} name="kind" className="input">{DOC_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>}</Field>
        <Field label="Title">{(id) => <input id={id} name="title" className="input" maxLength={200} placeholder="e.g. Aadhaar front" />}</Field>
        <Field label="File *" full hint="PDF or photo up to 10 MB. Stored encrypted.">{(id) => <input id={id} name="file" type="file" className="input" required accept={DOC_ACCEPT} />}</Field>
      </div>
      <div className="mt-4 flex justify-end"><Submit busy={act.isPending}><Icon name="upload" className="size-4" />Store encrypted</Submit></div>
    </form>
  );
}

export function DocumentsCard({ docs, partyId, loanId }: { docs: Doc[] | null; partyId?: number; loanId?: number }) {
  const { session, canEdit } = useSession();
  if (docs === null || !canEdit) return null;
  return (
    <Card id="documents">
      <CardHead title="Documents" icon="shield" sub="Encrypted on disk · only you can open them" />
      {session?.documents_problem ? <Alert tone="amber">Documents are switched off: {session.documents_problem}. Run <code>python -m loan_tracker.cli gen-doc-key</code>, put the key in <code>.env</code> as DOCUMENT_KEY and restart.</Alert> : (
        <>
          <DocList docs={docs} />
          <details className="mt-3 rounded-lg border border-line p-3 open:bg-field">
            <summary className="cursor-pointer text-xs font-bold text-teal">Add a document</summary>
            <div className="mt-4"><DocUpload partyId={partyId} loanId={loanId} /></div>
          </details>
        </>
      )}
    </Card>
  );
}
