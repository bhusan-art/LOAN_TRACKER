import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { inr, when } from "../lib/format";
import type { AuditRow } from "../lib/types";
import { Card, Empty, ErrorBox, Loading, PageHead, Tag } from "../components/ui";
import type { Tone } from "../lib/format";

const TONE: Record<string, Tone> = { green: "green", red: "red", orange: "amber", purple: "violet", gray: "gray" };

function detail(r: AuditRow): string {
  const d = r.detail as Record<string, any>;
  const parts: string[] = [];
  if (d.name) parts.push(String(d.name));
  if (d.type) parts.push(String(d.type).toLowerCase().replace("_", " "));
  if (typeof d.amount_paise === "number") parts.push(inr(d.amount_paise));
  if (d.note) parts.push(String(d.note));
  if (d.via) parts.push(`via ${d.via}`);
  return parts.join(" · ");
}

export default function Activity() {
  const { session } = useSession();
  const [params] = useSearchParams();
  const loanId = params.get("loan_id");
  const q = useQuery({ queryKey: ["activity", loanId], queryFn: () => api<AuditRow[]>(`/api/ui/activity${loanId ? `?loan_id=${loanId}` : ""}`) });
  return (
    <>
      <PageHead eyebrow="Analyse" title="Activity log" scope={false}
        sub={loanId ? <>Everything that happened on <Link className="link text-[13px]" to={`/loans/${loanId}`}>LN-{loanId}</Link>. <Link className="link text-[13px]" to="/activity">Show all</Link></> : "Who changed what and when: logins, entries, reversals, documents. The latest 300."} />
      <Card>
        {q.error && <ErrorBox error={q.error} />}
        {q.isLoading ? <Loading /> : q.data?.length ? (
          <ol className="relative ml-2 border-l-2 border-line pl-5">
            {q.data.map((r) => {
              const d = r.detail as Record<string, any>;
              const loan = d.loan_id ?? (r.entity === "loan" ? r.entity_id : null);
              return (
                <li key={r.id} className="relative mb-4">
                  <span className="absolute top-1.5 -left-[27px] size-[11px] rounded-full border-[3px] border-card bg-teal" />
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Tag tone={TONE[r.tone] ?? "gray"}>{r.label}</Tag>
                    <b>{r.actor}</b>
                    {loan && <Link className="link" to={`/loans/${loan}`}>LN-{loan}</Link>}
                    <span className="ml-auto text-[11px] text-muted">{when(r.at, session?.timezone)}</span>
                  </div>
                  {detail(r) && <p className="mt-1 text-xs text-soft">{detail(r)}</p>}
                </li>
              );
            })}
          </ol>
        ) : <Empty icon="activity" title="No activity yet" />}
      </Card>
    </>
  );
}
