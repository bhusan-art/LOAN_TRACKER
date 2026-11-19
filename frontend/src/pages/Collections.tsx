import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { dmy, inr, plural } from "../lib/format";
import type { ChaseItem, CollectionsPage } from "../lib/types";
import { Icon } from "../components/Icon";
import { Card, CardHead, Empty, ErrorBox, Loading, Metric, PageHead, PersonCell } from "../components/ui";

function Item({ c }: { c: ChaseItem }) {
  const tel = c.phone?.replace(/[^\d+]/g, "");
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-line py-3.5 first:border-0">
      <div className="min-w-0 flex-1"><PersonCell name={c.party} to={`/loans/${c.loan_id}#collections`} tone="red"
        sub={<>LN-{c.loan_id} · {c.detail}{c.on_date ? ` · ${dmy(c.on_date)}` : ""}</>} /></div>
      <div className="text-right">
        <b className="num block text-xs">{inr(c.overdue_paise)}</b>
        <small className="text-[10px] text-muted">{c.short_paise ? `${inr(c.short_paise)} short of promise` : "overdue"}</small>
      </div>
      <div className="flex gap-1.5">
        {tel && <a className="btn btn-sm" href={`tel:${tel}`} aria-label={`Call ${c.party}`}><Icon name="phone" className="size-3.5" /></a>}
        {c.wa_url && <a className="btn btn-sm" href={c.wa_url} target="_blank" rel="noopener"><Icon name="chat" className="size-3.5" />Remind</a>}
        <Link className="btn btn-sm btn-soft" to={`/loans/${c.loan_id}#collections`}>Log</Link>
      </div>
    </div>
  );
}

const SECTIONS: [keyof Omit<CollectionsPage, "as_of">, string, string, string][] = [
  ["broken", "Broken promises", "They promised a date and didn't pay in full.", "alert"],
  ["promises_due", "Promises due", "Payment promised for today or the next few days.", "clock"],
  ["follow_ups", "Follow-ups due", "You planned to contact them again.", "phone"],
  ["not_contacted", "Overdue, not contacted", "Overdue with no contact logged since it fell due.", "inbox"],
];

export default function Collections() {
  const { session } = useSession();
  const q = useQuery({ queryKey: ["collections", session?.book], queryFn: () => api<CollectionsPage>("/api/ui/collections") });
  const d = q.data;
  const total = d ? SECTIONS.reduce((a, [k]) => a + d[k].length, 0) : 0;
  return (
    <>
      <PageHead eyebrow="Workspace" title="Collections" sub="Track incoming dues, follow-ups and overdue accounts." />
      {q.error && <ErrorBox error={q.error} />}
      {q.isLoading ? <Loading /> : d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-2.5 sm:gap-3.5 xl:grid-cols-4">
            <Metric label="Broken promises" icon="alert" tone="red" value={d.broken.length} sub={inr(d.broken.reduce((a, c) => a + c.short_paise, 0), false) + " short"} />
            <Metric label="Promises due" icon="clock" tone="violet" value={d.promises_due.length} sub="Today and next few days" />
            <Metric label="Follow-ups" icon="phone" tone="blue" value={d.follow_ups.length} sub="Planned contacts" />
            <Metric label="Not contacted" icon="inbox" tone="amber" value={d.not_contacted.length} sub={inr(d.not_contacted.reduce((a, c) => a + c.overdue_paise, 0), false) + " overdue"} />
          </div>
          {total === 0 ? <Card><Empty title="Nothing to chase" text="No broken promises, follow-ups or uncontacted overdue loans." /></Card> : (
            <div className="grid gap-4 lg:grid-cols-2">
              {SECTIONS.filter(([k]) => d[k].length).map(([k, title, sub, icon]) => (
                <Card key={k}>
                  <CardHead title={title} sub={`${sub} ${plural(d[k].length, "loan")}.`} icon={icon} iconTone={k === "broken" ? "red" : "teal"} />
                  {d[k].map((c) => <Item key={`${k}-${c.loan_id}`} c={c} />)}
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
