import { useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, configureApp } from "../lib/api";
import { deviceName, getServer, isNative, setToken } from "../lib/native";
import { useSession } from "../lib/session";
import { Alert, Field } from "../components/ui";

export default function Login() {
  const { session } = useSession();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [server, setServerShown] = useState<string | null>(null);
  useEffect(() => { if (isNative) getServer().then(setServerShown); }, []);
  const next = params.get("next");
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  if (session?.user) return <Navigate to={target} replace />;

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const creds = { username: f.get("username"), password: f.get("password"), code: f.get("code") ?? "" };
      if (isNative) {
        // the app gets its own sign-in (a token for this phone) instead of a browser cookie
        const r = await api<{ token: string }>("/api/device/login", { json: { ...creds, device_name: deviceName() } });
        await setToken(r.token);
        configureApp(server ?? "", r.token);
      } else {
        await api("/api/login", { json: creds });
      }
      await qc.invalidateQueries();
      nav(target, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="safe-top grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="hidden flex-col justify-between bg-gradient-to-b from-[#082e34] via-[#06474a] to-[#07353a] p-12 text-[#e9f4f1] lg:flex">
        <div className="flex items-center gap-3">
          <span className="grid size-11 place-items-center rounded-xl bg-[#f3a64d] font-display text-2xl font-extrabold text-[#103b3d]">L</span>
          <span><b className="block font-display text-2xl font-extrabold">Ledgerly</b><small className="text-[11px] tracking-[1.1px] text-[#9db9b6]">FINANCIAL WORKSPACE</small></span>
        </div>
        <div>
          <h2 className="max-w-md font-display text-[34px] leading-tight font-extrabold tracking-[-1px]">Every rupee lent, borrowed and due, in one clear ledger.</h2>
          <p className="mt-4 max-w-md text-[#b9d1ce]">Interest, payments, reminders and reports for your business and personal books.</p>
        </div>
        <small className="text-[#83aaa6]">Private workspace · amounts calculated by the ledger engine</small>
      </div>
      <div className="grid place-items-center bg-bg p-5">
        <form onSubmit={submit} className="card w-full max-w-[420px] p-7 sm:p-9">
          <div className="mb-6 flex items-center gap-3 lg:hidden">
            <span className="grid size-10 place-items-center rounded-xl bg-[#f3a64d] font-display text-xl font-extrabold text-[#103b3d]">L</span>
            <b className="font-display text-xl font-extrabold">Ledgerly</b>
          </div>
          <h1 className="text-[26px] font-extrabold tracking-[-1px]">Sign in</h1>
          <p className="mt-1 mb-6 text-[13px] text-muted">Use your username, password and the 6-digit code from your authenticator app.</p>
          {error && <div className="mb-4"><Alert tone="red">{error}</Alert></div>}
          <div className="grid gap-4">
            <Field label="Username">{(id) => <input id={id} name="username" className="input" autoComplete="username" required autoFocus />}</Field>
            <Field label="Password">{(id) => <input id={id} name="password" type="password" className="input" autoComplete="current-password" required />}</Field>
            <Field label="Authenticator code" hint="Leave empty if two-step login is not set up for your user.">{(id) => (
              <input id={id} name="code" className="input num tracking-[4px]" inputMode="numeric" autoComplete="one-time-code" maxLength={8} pattern="[0-9]*" />)}
            </Field>
          </div>
          <button className="btn btn-primary mt-6 w-full py-3 text-sm" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          {isNative && (
            <p className="mt-4 text-center text-[11px] break-all text-muted">Server: {server ?? "…"} ·{" "}
              <button type="button" className="link" onClick={() => window.dispatchEvent(new Event("ledgerly:change-server"))}>Change</button></p>
          )}
        </form>
      </div>
    </div>
  );
}
