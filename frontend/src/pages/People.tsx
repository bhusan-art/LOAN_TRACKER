import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { inr, plural } from "../lib/format";
import type { PartyRow } from "../lib/types";
import { Icon } from "../components/Icon";
import { Card, Empty, ErrorBox, Loading, PageHead, PersonCell, TableWrap, Tag } from "../components/ui";
import { AddPerson } from "./LoanNew";

export default function People() {
  const { session, canEdit } = useSession();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const rows = useQuery({ queryKey: ["parties", session?.book, q], queryFn: () => api<PartyRow[]>(`/api/ui/parties?q=${encodeURIComponent(q)}`) });
  return (
    <>
      <PageHead eyebrow="Workspace" title="People" sub="Borrowers, lenders and their contact details."
        actions={canEdit && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}><Icon name="plus" className="size-4" />Add person</button>} />
      <Card>
        <div className="relative mb-4 sm:w-72">
          <Icon name="search" className="absolute top-3 left-3 size-4 text-muted" />
          <input className="input min-h-[38px] pl-9 text-xs" placeholder="Name or phone" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
        </div>
        {rows.error && <ErrorBox error={rows.error} />}
        {rows.isLoading ? <Loading /> : rows.data?.length ? (
          <TableWrap>
            <thead><tr><th className="th">Person</th><th className="th text-right">Loans</th><th className="th text-right">You lent</th><th className="th text-right">You owe</th><th className="th text-right">Net position</th><th className="th" /></tr></thead>
            <tbody>
              {rows.data.map((p) => (
                <tr key={p.id} className="hover:bg-field">
                  <td className="td"><PersonCell name={p.name} sub={p.phone ?? "No phone"} to={`/people/${p.id}`} /></td>
                  <td className="td num text-right">{p.loan_count}</td>
                  <td className="td num text-right">{p.lent_paise ? inr(p.lent_paise, false) : "—"}</td>
                  <td className="td num text-right">{p.taken_paise ? inr(p.taken_paise, false) : "—"}</td>
                  <td className="td num text-right font-bold">
                    {p.net_position_paise === 0 ? <Tag>Even</Tag> : p.net_position_paise > 0
                      ? <span className="text-green">{inr(p.net_position_paise, false)} owed to you</span>
                      : <span className="text-amber">{inr(-p.net_position_paise, false)} you owe</span>}
                  </td>
                  <td className="td text-right"><Link to={`/people/${p.id}`} className="link">Open →</Link></td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        ) : <Empty icon="people" title={q ? "Nobody matches" : "No people yet"} text={q ? `Nothing for “${q}”.` : "Add the people you lend to or borrow from."} />}
        {!!rows.data?.length && <p className="mt-3 text-[11px] text-muted">{plural(rows.data.length, "person", "people")}</p>}
      </Card>
      <AddPerson open={adding} onClose={() => setAdding(false)} onAdded={(id) => nav(`/people/${id}`)} />
    </>
  );
}
