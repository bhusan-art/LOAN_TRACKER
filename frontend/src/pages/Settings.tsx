import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/toast";
import { when } from "../lib/format";
import { cancelDueReminders, canLock, getFlag, getServer, isNative, setFlag, unlock } from "../lib/native";
import { Icon } from "../components/Icon";
import { Alert, Card, CardHead, Confirm, Empty, ErrorBox, Loading, PageHead, Row, Tag, useAction } from "../components/ui";

type Device = { id: number; name: string; user: string; created_at: string; last_used_at: string | null; revoked_at: string | null;
  live: boolean; state: "LIVE" | "SIGNED_OUT" | "ENDED" | "IDLE"; current: boolean };

const STATE: Record<Device["state"], [string, "green" | "gray" | "amber"]> = {
  LIVE: ["Signed in", "green"], SIGNED_OUT: ["Signed out", "gray"],
  ENDED: ["Ended: signed in elsewhere or password changed", "gray"], IDLE: ["Ended: not used for a while", "amber"] };

function Toggle({ label, sub, on, onChange, disabled }: { label: string; sub: string; on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex cursor-pointer items-center gap-3 border-b border-line py-3 last:border-0">
      <span className="min-w-0 flex-1"><b className="block text-[13px]">{label}</b><small className="text-[11px] text-muted">{sub}</small></span>
      <input type="checkbox" className="peer sr-only" checked={on} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="relative h-6 w-11 shrink-0 rounded-full bg-chip transition peer-checked:bg-teal peer-disabled:opacity-50 after:absolute after:top-0.5 after:left-0.5 after:size-5 after:rounded-full after:bg-white after:shadow after:transition peer-checked:after:translate-x-5" />
    </label>
  );
}

function ThisPhone() {
  const toast = useToast();
  const qc = useQueryClient();
  const [server, setServer] = useState<string | null>(null);
  const [lock, setLock] = useState(false);
  const [remind, setRemind] = useState(true);
  const [lockable, setLockable] = useState(true);
  useEffect(() => {
    getServer().then(setServer);
    getFlag("lock", false).then(setLock);
    getFlag("remind", true).then(setRemind);
    canLock().then(setLockable).catch(() => setLockable(false));
  }, []);
  return (
    <Card>
      <CardHead title="This phone" icon="phone" />
      <Row label="Server">{server ?? "…"}</Row>
      <div className="mb-2 text-right"><button type="button" className="link" onClick={() => window.dispatchEvent(new Event("ledgerly:change-server"))}>Change server</button></div>
      <Toggle label="Lock with fingerprint" disabled={!lockable}
        sub={lockable ? "Ask for your fingerprint or screen lock when the app opens, and after 30 seconds away." : "Set a screen lock on this phone first."}
        on={lock} onChange={async (v) => {
          if (v && !(await unlock())) return toast("Not verified, so the lock stays off.", "err");
          await setFlag("lock", v); setLock(v);
          toast(v ? "The app will ask for your fingerprint." : "Fingerprint lock is off.");
        }} />
      <Toggle label="Due reminders" sub="A notification at 9 am on days when money is due in or out (next 14 days, all books)."
        on={remind} onChange={async (v) => {
          await setFlag("remind", v); setRemind(v);
          if (!v) await cancelDueReminders().catch(() => {});
          else await qc.invalidateQueries({ queryKey: ["reminder-calendar"] });
          toast(v ? "Reminders are on. They refresh each time you open the app." : "Reminders are off.");
        }} />
    </Card>
  );
}

export default function Settings() {
  const { session, canEdit } = useSession();
  const q = useQuery({ queryKey: ["devices"], queryFn: () => api<Device[]>("/api/devices") });
  const revoke = useAction((id: number) => post(`/api/devices/${id}/revoke`));
  const live = (q.data ?? []).filter((d) => d.state === "LIVE");
  const old = (q.data ?? []).filter((d) => d.state !== "LIVE");
  return (
    <>
      <PageHead eyebrow="Account" title="Settings" scope={false} sub={`Signed in as ${session?.user} · ${session?.role === "OWNER" ? "owner" : "read-only viewer"}`} />
      <div className="grid gap-4 xl:grid-cols-2">
        {isNative && <ThisPhone />}
        <Card>
          <CardHead title="Phones with the Ledgerly app" icon="shield"
            sub={canEdit ? "Sign out a phone you lost or no longer use; it must sign in again with password and code." : "Your own phones."} />
          {q.error && <ErrorBox error={q.error} />}
          {q.isLoading ? <Loading /> : live.length ? live.map((d) => (
            <div key={d.id} className="flex flex-wrap items-center gap-3 border-b border-line py-3 last:border-0">
              <span className="grid size-9 place-items-center rounded-lg bg-tealsoft text-teal"><Icon name="phone" className="size-4" /></span>
              <span className="min-w-0 flex-1"><b className="block text-[13px]">{d.name} {d.current && <Tag tone="teal">This phone</Tag>}</b>
                <small className="text-[11px] text-muted">{d.user} · since {when(d.created_at, session?.timezone)} · last used {when(d.last_used_at, session?.timezone)}</small></span>
              {canEdit && !d.current && (
                <Confirm text={`Sign out ${d.name}? It must sign in again with password and code.`} danger label="Sign out" onYes={() => revoke.mutate(d.id)}>
                  {(open) => <button type="button" className="btn btn-sm" onClick={open}>Sign out</button>}
                </Confirm>)}
            </div>
          )) : <Empty icon="phone" title="No phones signed in" text="Install the Android app and sign in to see it here." />}
          {old.length > 0 && (
            <details className="mt-3 text-xs"><summary className="cursor-pointer font-bold text-muted">Earlier sign-ins ({old.length})</summary>
              {old.map((d) => <div key={d.id} className="flex items-center gap-2 border-b border-line py-2 last:border-0">
                <span className="flex-1">{d.name} · {d.user}</span><Tag tone={STATE[d.state][1]}>{STATE[d.state][0]}</Tag></div>)}
            </details>
          )}
        </Card>
        {!isNative && (
          <Card>
            <CardHead title="Get the Android app" icon="phone" />
            <Alert tone="teal">Download the latest <b>ledgerly-*.apk</b> from your GitHub repository's Releases page, install it, and connect it to
              this server's Tailscale https address. The README has the steps.</Alert>
          </Card>
        )}
      </div>
    </>
  );
}
