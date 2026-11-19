import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { inr } from "../lib/format";
import { getFlag, isAndroid, isDesktop, openExternal, scheduleDueReminders, unlock } from "../lib/native";
import type { CalendarPage } from "../lib/types";
import { useSession } from "../lib/session";
import { Icon } from "./Icon";
import { idleExpired } from "./IdleSignOut";

const LOCK_AFTER_MS = 30_000;          // back from the background after this long -> unlock again

/** Fingerprint / screen-lock gate for the Android app (Settings → "Lock with fingerprint"). */
function AppLock() {
  const [locked, setLocked] = useState(false);
  const [failed, setFailed] = useState(false);
  const hiddenAt = useRef<number | null>(null);

  async function tryUnlock() {
    setFailed(false);
    if (await unlock()) setLocked(false);
    else setFailed(true);
  }

  useEffect(() => {
    let off: (() => void) | undefined;
    let gone = false;                       // the screen closed before setup finished
    (async () => {
      if (gone) return;
      if (await getFlag("lock", false)) {
        setLocked(true);
        tryUnlock();
      }
      const { App } = await import("@capacitor/app");
      const h = await App.addListener("appStateChange", async ({ isActive }) => {
        if (!isActive) {
          hiddenAt.current = Date.now();
          return;
        }
        const away = hiddenAt.current ? Date.now() - hiddenAt.current : 0;
        hiddenAt.current = null;
        if (away >= LOCK_AFTER_MS && !idleExpired() && (await getFlag("lock", false))) {
          setLocked(true);
          tryUnlock();
        }
      });
      if (gone) h.remove();
      else off = () => h.remove();
    })();
    return () => { gone = true; off?.(); };
  }, []);

  if (!locked) return null;
  return (
    <div className="fixed inset-0 z-[500] grid place-items-center bg-gradient-to-b from-[#082e34] via-[#06474a] to-[#07353a] p-6 text-center text-white">
      <div>
        <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-[#f3a64d] font-display text-3xl font-extrabold text-[#103b3d]">L</span>
        <h1 className="mt-5 font-display text-2xl font-extrabold">Ledgerly is locked</h1>
        <p className="mt-2 text-sm text-[#b9d1ce]">{failed ? "Not verified. Try again." : "Use your fingerprint or screen lock."}</p>
        <button type="button" className="btn btn-primary mt-6 px-8 py-3 text-sm" onClick={tryUnlock}><Icon name="shield" className="size-4" />Unlock</button>
      </div>
    </div>
  );
}

/** Android back button: go back a screen, or leave the app from the first screen. */
function BackButton() {
  useEffect(() => {
    let off: (() => void) | undefined;
    let gone = false;
    (async () => {
      const { App } = await import("@capacitor/app");
      const h = await App.addListener("backButton", ({ canGoBack }) => {
        const dialog = document.querySelector<HTMLButtonElement>('[role=dialog] button[aria-label="Close"]');
        if (dialog) dialog.click();                         // close an open dialog first
        else if (canGoBack && window.location.pathname !== "/") window.history.back();
        else App.minimizeApp();
      });
      if (gone) h.remove();
      else off = () => h.remove();
    })();
    return () => { gone = true; off?.(); };
  }, []);
  return null;
}

/** Due reminders as phone notifications: rebuilt from the cash calendar whenever it loads. */
function Reminders() {
  const { session } = useSession();
  const nav = useNavigate();
  // every book, whatever the screen shows, so no due is missed
  const cal = useQuery({
    queryKey: ["reminder-calendar"], enabled: !!session?.user, staleTime: 10 * 60_000,
    queryFn: () => api<CalendarPage>("/api/ui/calendar?days=30&book=all"),
  });
  useEffect(() => {
    if (!cal.data) return;
    const days = cal.data.calendar.filter((d) => d.money_in.length || d.money_out.length).map((d) => ({
      day: d.day,
      inPaise: d.money_in.reduce((a, i) => a + i.amount_paise, 0),
      outPaise: d.money_out.reduce((a, i) => a + i.amount_paise, 0),
      people: [...new Set([...d.money_in, ...d.money_out].map((i) => i.party))],
    }));
    // the switch in Settings is read each time, so turning it on or off applies at once
    getFlag("remind", true).then((on) => (on ? scheduleDueReminders(days, (p) => inr(p, false)) : null)).catch(() => {});
    // dataUpdatedAt changes on every refetch, even when the data is the same (Settings switch)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cal.dataUpdatedAt]);
  // tapping a reminder opens the cash calendar
  useEffect(() => {
    let off: (() => void) | undefined;
    let gone = false;
    (async () => {
      const { LocalNotifications } = await import("@capacitor/local-notifications");
      const h = await LocalNotifications.addListener("localNotificationActionPerformed", () => nav("/calendar"));
      if (gone) h.remove();
      else off = () => h.remove();
    })();
    return () => { gone = true; off?.(); };
  }, [nav]);
  return null;
}

/**
 * Windows app: links to other sites (WhatsApp reminders, receipts on WhatsApp, phone numbers)
 * open in the default browser or app. The app window itself never leaves Ledgerly.
 */
function DesktopLinks() {
  useEffect(() => {
    const click = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a) return;
      const href = a.getAttribute("href") ?? "";
      const external = /^(tel:|mailto:)/i.test(href) || (/^https?:/i.test(href) && new URL(href).origin !== window.location.origin);
      if (!external) return;
      e.preventDefault();
      openExternal(href).catch(() => {});
    };
    document.addEventListener("click", click, true);
    return () => document.removeEventListener("click", click, true);
  }, []);
  return null;
}

export function NativeExtras() {
  if (isDesktop) return <DesktopLinks />;
  if (!isAndroid) return null;
  return <><AppLock /><BackButton /><Reminders /></>;
}
