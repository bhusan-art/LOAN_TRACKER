import { useEffect, useState, type ReactNode } from "react";
import { configureApp, post } from "./lib/api";
import { getServer, getToken, isNative, setServer, setToken } from "./lib/native";
import { Loading } from "./components/ui";
import AppSetup from "./pages/AppSetup";

/**
 * In the Android app: load the saved server address and phone token before anything else
 * talks to the API; ask for the address on first launch. In a browser: nothing to do.
 */
export function AppBoot({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"loading" | "setup" | "ready">(isNative ? "loading" : "ready");
  const [server, setServerState] = useState<string | null>(null);
  useEffect(() => {
    if (!isNative) return;
    (async () => {
      const [s, t] = await Promise.all([getServer(), getToken()]);
      setServerState(s);
      if (!s) return setState("setup");
      // every fresh start of the app asks for the password again: the previous sign-in is
      // ended on the server (best effort: it may be offline) and forgotten here
      if (t) {
        configureApp(s, t);
        await post("/api/device/logout").catch(() => {});
        await setToken(null);
      }
      configureApp(s, null);
      setState("ready");
    })();
    const change = () => { setState("setup"); };
    window.addEventListener("ledgerly:change-server", change);
    return () => window.removeEventListener("ledgerly:change-server", change);
  }, []);
  if (state === "loading") return <Loading label="Starting…" />;
  if (state === "setup") {
    return <AppSetup current={server} onCancel={server ? () => setState("ready") : undefined} onSaved={async (url) => {
      await setToken(null);                                   // after choosing the server: sign in
      await setServer(url);
      setServerState(url);
      configureApp(url, null);
      window.location.replace("/login");
    }} />;
  }
  return <>{children}</>;
}
