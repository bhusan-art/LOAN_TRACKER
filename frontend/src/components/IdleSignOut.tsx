import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSession } from "../lib/session";
import { useSignOut } from "../lib/signout";

const ACTIVE_KEY = "lastActive";     // shared by every open tab, so using one tab keeps all alive

function lastActive(local: number): number {
  try {
    return Math.max(local, Number(localStorage.getItem(ACTIVE_KEY)) || 0);
  } catch {
    return local;
  }
}

/**
 * Sign out after the server's idle limit without any use (taps, keys, scrolling), even if
 * the screen makes no requests, so money figures don't stay open on an unattended screen.
 * The server enforces the same limit on its side.
 */
export function IdleSignOut() {
  const { session } = useSession();
  const signOut = useSignOut();
  const qc = useQueryClient();
  const local = useRef(Date.now());
  const pinged = useRef(Date.now());
  const minutes = session?.idle_minutes ?? 30;

  useEffect(() => {
    let lastWrite = 0;
    const mark = () => {
      const now = Date.now();
      local.current = now;
      if (now - lastWrite > 5000) {
        lastWrite = now;
        try {
          localStorage.setItem(ACTIVE_KEY, String(now));
          localStorage.setItem("idleMinutes", String(minutes));
        } catch {
          /* not shared between tabs then */
        }
      }
    };
    mark();
    const events = ["pointerdown", "keydown", "wheel", "touchstart", "scroll"];
    events.forEach((e) => window.addEventListener(e, mark, { passive: true, capture: true }));
    const check = () => {
      const now = Date.now();
      const idleFor = now - lastActive(local.current);
      if (idleFor > minutes * 60_000) {
        signOut("You were signed out because the app was not used for a while. Please sign in again.");
        return;
      }
      // reading or scrolling makes no requests: tell the server we are still here, so its own
      // idle limit (the same number of minutes) does not end a session that is being used
      const every = Math.min(5, Math.max(1, minutes / 3)) * 60_000;
      if (idleFor < every && now - pinged.current > every) {
        pinged.current = now;
        qc.invalidateQueries({ queryKey: ["session"] });
      }
    };
    const timer = window.setInterval(check, 15_000);
    const onShow = () => document.visibilityState === "visible" && check();   // back from background
    document.addEventListener("visibilitychange", onShow);
    return () => {
      events.forEach((e) => window.removeEventListener(e, mark, { capture: true }));
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [minutes, signOut, qc]);
  return null;
}

/** True when the app has been unused for longer than the idle limit (it is about to sign out). */
export function idleExpired(): boolean {
  try {
    const last = Number(localStorage.getItem(ACTIVE_KEY)) || 0;
    const minutes = Number(localStorage.getItem("idleMinutes")) || 30;
    return last > 0 && Date.now() - last > minutes * 60_000;
  } catch {
    return false;
  }
}
