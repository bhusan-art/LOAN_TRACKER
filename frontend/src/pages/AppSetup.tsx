import { useState } from "react";
import { normaliseServer } from "../lib/native";
import { Alert, Field } from "../components/ui";

/** First launch of the Android app: where is your Ledgerly server? */
export default function AppSetup({ current, onSaved, onCancel }: {
  current?: string | null; onSaved: (url: string) => void; onCancel?: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const { url, error } = normaliseServer(String(new FormData(e.currentTarget).get("server") ?? ""));
    if (!url) return setError(error ?? "Check the address.");
    setBusy(true);
    setError(null);
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 10_000);
      const r = await fetch(url + "/healthz", { signal: ctl.signal, credentials: "omit" });
      clearTimeout(t);
      const body = await r.json().catch(() => null);
      if (!r.ok || !body || body.ok !== true) throw new Error("not ledgerly");
      onSaved(url);
    } catch (err) {
      setError(err instanceof Error && err.message === "not ledgerly"
        ? "Something answered at that address, but it is not a Ledgerly server."
        : "Could not reach the server. Is Tailscale on (on this phone and the PC), and is the app running?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-bg p-5 pt-[calc(1.25rem+var(--sat))] pb-[calc(1.25rem+var(--sab))]">
      <form onSubmit={submit} className="card w-full max-w-[420px] p-7">
        <div className="mb-6 flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-[#f3a64d] font-display text-xl font-extrabold text-[#103b3d]">L</span>
          <b className="font-display text-xl font-extrabold">Ledgerly</b>
        </div>
        <h1 className="text-[24px] font-extrabold tracking-[-1px]">Connect to your server</h1>
        <p className="mt-1 mb-5 text-[13px] text-muted">The address of the PC running Ledgerly. With Tailscale it looks like
          <b className="text-soft"> https://my-pc.tail1234.ts.net</b>.</p>
        {error && <div className="mb-4"><Alert tone="red">{error}</Alert></div>}
        <Field label="Server address" hint="Only https addresses are allowed, so nothing is sent unencrypted.">{(id) => (
          <input id={id} name="server" className="input" inputMode="url" autoCapitalize="off" autoCorrect="off" spellCheck={false}
            defaultValue={current ?? ""} placeholder="https://my-pc.tail1234.ts.net" required autoFocus />)}
        </Field>
        <button className="btn btn-primary mt-6 w-full py-3 text-sm" disabled={busy}>{busy ? "Checking…" : "Connect"}</button>
        {onCancel && <button type="button" className="btn mt-2 w-full py-3 text-sm" onClick={onCancel}>Cancel: keep {current}</button>}
      </form>
    </div>
  );
}
