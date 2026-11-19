import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { configureApp, post } from "./api";
import { cancelDueReminders, getServer, isAndroid, isNative, setToken } from "./native";
import type { SessionInfo } from "./types";

const REASON_KEY = "signedOutReason";

/** Remember why we signed out, to show it on the sign-in screen (once). */
export function noteSignedOut(message: string | null | undefined) {
  if (!message) return;
  try {
    sessionStorage.setItem(REASON_KEY, message);
  } catch {
    /* private mode */
  }
}

/** Why we were signed out; kept until the next sign-in (the sign-in screen may open twice). */
export function signedOutReason(): string | null {
  try {
    return sessionStorage.getItem(REASON_KEY);
  } catch {
    return null;
  }
}

export function clearSignedOutReason() {
  try {
    sessionStorage.removeItem(REASON_KEY);
  } catch {
    /* private mode */
  }
}

let signingOut = false;          // one sign-out at a time (the idle timer may fire again meanwhile)

/** Sign out here: end the server session (browser) or this phone's sign-in (app), forget it locally. */
export function useSignOut() {
  const qc = useQueryClient();
  const nav = useNavigate();
  return useCallback(async (reason?: string) => {
    if (signingOut) return;
    signingOut = true;
    try {
      await signOutNow(reason);
    } finally {
      signingOut = false;
    }
  }, [qc, nav]);                          // eslint-disable-line react-hooks/exhaustive-deps

  async function signOutNow(reason?: string) {
    noteSignedOut(reason);                  // first: requests still in flight may fail meanwhile
    try {
      await post(isNative ? "/api/device/logout" : "/api/logout");
    } catch {
      /* already signed out on the server */
    }
    if (isNative) {
      await setToken(null);
      configureApp((await getServer()) ?? "", null);
      if (isAndroid) await cancelDueReminders().catch(() => {});
    }
    // mark the session signed out *before* leaving: the sign-in screen sends a signed-in
    // user back to the app, and cached money figures must not survive the sign-out
    qc.setQueryData<SessionInfo>(["session"], (old) => (old ? { ...old, user: null, role: undefined, device_id: null } : old));
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
    nav("/login", { replace: true });
    qc.invalidateQueries({ queryKey: ["session"] });     // fresh CSRF token for the next sign-in
  }
}
