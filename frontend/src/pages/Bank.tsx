import { useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, upload } from "../lib/api";
import { useSession } from "../lib/session";
import { dshort, when } from "../lib/format";
import type { BankImport, CashAccount } from "../lib/types";
import { Icon } from "../components/Icon";
import { Card, CardHead, Empty, ErrorBox, Field, FormFoot, Loading, PageHead, Submit, TableWrap, Tag, useAction } from "../components/ui";

export default function Bank() {
  const { session, canEdit } = useSession();
  const nav = useNavigate();
  const form = useRef<HTMLFormElement>(null);
  const q = useQuery({ queryKey: ["bank"], queryFn: () => api<{ accounts: CashAccount[]; imports: BankImport[] }>("/api/ui/bank") });
  const act = useAction((fd: FormData) => upload<{ id: number; message: string }>("/api/bank/upload", fd), { onDone: (r) => nav(`/bank/imports/${r.id}`) });
  const d = q.data;
  return (
    <>
      <PageHead eyebrow="Analyse" title="Bank import" scope={false}
        sub="Upload a bank statement (CSV). Each credit or debit is matched to a loan payment, so you record money from the bank in a few clicks." />
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : d && (
        <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
          <Card className="self-start">
            <CardHead title="Upload a statement" icon="upload" sub="CSV from your bank's net banking. Lines already imported are skipped." />
            {!canEdit ? <p className="text-xs text-muted">Only the owner can import statements.</p> : d.accounts.length ? (
              <form ref={form} onSubmit={(e) => { e.preventDefault(); act.mutate(new FormData(e.currentTarget)); }}>
                <div className="grid gap-4">
                  <Field label="Account *">{(id) => <select id={id} name="account_id" className="input" required>{d.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>}</Field>
                  <Field label="Statement file (CSV) *">{(id) => <input id={id} name="file" type="file" className="input" required accept=".csv,text/csv" />}</Field>
                  <label className="flex items-center gap-2 text-xs"><input type="checkbox" name="update_balance" value="1" defaultChecked className="accent-teal" />Set the account balance from the statement's last line</label>
                </div>
                <FormFoot><Submit busy={act.isPending}><Icon name="upload" className="size-4" />Import</Submit></FormFoot>
              </form>
            ) : <Empty icon="bank" title="Add a bank account first" text="Statements are imported into a bank or wallet account." action={<Link to="/cash" className="btn btn-primary">Go to Cash & forecast</Link>} />}
          </Card>
          <Card>
            <CardHead title="Imported statements" icon="file" />
            {d.imports.length ? (
              <TableWrap>
                <thead><tr><th className="th">File</th><th className="th">Period</th><th className="th text-right">Lines</th><th className="th">Open</th><th className="th" /></tr></thead>
                <tbody>{d.imports.map((i) => (
                  <tr key={i.id} className="hover:bg-field">
                    <td className="td"><Link to={`/bank/imports/${i.id}`} className="font-bold hover:text-teal">{i.file_name}</Link><small className="block text-[10px] text-muted">{i.account} · {when(i.created_at, session?.timezone)}</small></td>
                    <td className="td">{dshort(i.period_start)} – {dshort(i.period_end)}</td>
                    <td className="td num text-right">{i.new_count}{i.row_count !== i.new_count && <small className="block text-[10px] text-muted">of {i.row_count}</small>}</td>
                    <td className="td">{i.open ? <Tag tone="amber">{i.open} to review</Tag> : <Tag tone="green">Done</Tag>}</td>
                    <td className="td text-right"><Link to={`/bank/imports/${i.id}`} className="link">Review →</Link></td>
                  </tr>))}
                </tbody>
              </TableWrap>
            ) : <Empty icon="file" title="No statements yet" />}
          </Card>
        </div>
      )}
    </>
  );
}
