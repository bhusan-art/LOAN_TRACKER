// The Android app (Capacitor). In a browser none of this runs: isNative is false and the app
// talks to its own server with the session cookie, exactly as before.
import { Capacitor } from "@capacitor/core";
import { Preferences } from "@capacitor/preferences";

// VITE_FORCE_APP=1 at build time runs the browser build in app mode, for testing the
// phone sign-in from another origin without a phone (never set for real builds).
export const isNative = Capacitor.isNativePlatform() || import.meta.env.VITE_FORCE_APP === "1";

const KEY_SERVER = "server";
const KEY_TOKEN = "token";
const KEY_LOCK = "lock";
const KEY_REMIND = "remind";

export { normaliseServer } from "./server";

export async function getServer(): Promise<string | null> {
  return (await Preferences.get({ key: KEY_SERVER })).value;
}
export async function setServer(url: string | null) {
  if (url) await Preferences.set({ key: KEY_SERVER, value: url });
  else await Preferences.remove({ key: KEY_SERVER });
}
export async function getToken(): Promise<string | null> {
  return (await Preferences.get({ key: KEY_TOKEN })).value;
}
export async function setToken(token: string | null) {
  if (token) await Preferences.set({ key: KEY_TOKEN, value: token });
  else await Preferences.remove({ key: KEY_TOKEN });
}

export async function getFlag(key: "lock" | "remind", fallback: boolean): Promise<boolean> {
  const v = (await Preferences.get({ key: key === "lock" ? KEY_LOCK : KEY_REMIND })).value;
  return v === null ? fallback : v === "1";
}
export async function setFlag(key: "lock" | "remind", on: boolean) {
  await Preferences.set({ key: key === "lock" ? KEY_LOCK : KEY_REMIND, value: on ? "1" : "0" });
}

export function deviceName(): string {
  const m = navigator.userAgent.match(/Android [\d.]+; ([^;)]+)/);
  return (m?.[1]?.replace(/ Build\/.*/, "").trim() || "Android phone").slice(0, 100);
}

function toBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** Save a downloaded file in the app's private cache and open Android's share sheet for it. */
export async function shareBlob(blob: Blob, fileName: string, title: string) {
  const { Filesystem, Directory } = await import("@capacitor/filesystem");
  const { Share } = await import("@capacitor/share");
  const safe = fileName.replace(/[^\w.\- ]+/g, "_").slice(0, 120) || "file";
  const { uri } = await Filesystem.writeFile({ path: `shared/${safe}`, data: toBase64(await blob.arrayBuffer()),
    directory: Directory.Cache, recursive: true });
  await Share.share({ title, files: [uri], dialogTitle: title });
}

/** Ask for the fingerprint / screen lock. Resolves true when the person is verified. */
export async function unlock(): Promise<boolean> {
  const { BiometricAuth } = await import("@aparajita/capacitor-biometric-auth");
  try {
    await BiometricAuth.authenticate({
      reason: "Unlock Ledgerly", androidTitle: "Unlock Ledgerly", androidSubtitle: "Use your fingerprint or screen lock",
      allowDeviceCredential: true, cancelTitle: "Cancel",
    });
    return true;
  } catch {
    return false;
  }
}

export async function canLock(): Promise<boolean> {
  const { BiometricAuth } = await import("@aparajita/capacitor-biometric-auth");
  const r = await BiometricAuth.checkBiometry();
  return r.isAvailable || r.deviceIsSecure;
}

export type DueDay = { day: string; inPaise: number; outPaise: number; people: string[] };

/**
 * Phone reminders for the next 14 days of dues, at 9 am on each day that has money in or
 * out. Rebuilt each time the app opens, so they follow payments you record. Only names and
 * amounts already on the phone are used; nothing is sent anywhere.
 */
export async function scheduleDueReminders(days: DueDay[], fmt: (p: number) => string) {
  const { LocalNotifications } = await import("@capacitor/local-notifications");
  const pending = await LocalNotifications.getPending();
  const ours = pending.notifications.filter((n) => n.id >= 7000 && n.id < 7100);
  if (ours.length) await LocalNotifications.cancel({ notifications: ours.map((n) => ({ id: n.id })) });
  const perm = await LocalNotifications.checkPermissions();
  if (perm.display !== "granted") {
    const asked = await LocalNotifications.requestPermissions();
    if (asked.display !== "granted") return false;
  }
  const now = Date.now();
  const list = days.slice(0, 14).flatMap((d, i) => {
    const at = new Date(`${d.day}T09:00:00`);
    if (at.getTime() <= now + 60_000) return [];
    const parts = [d.inPaise ? `collect ${fmt(d.inPaise)}` : "", d.outPaise ? `pay ${fmt(d.outPaise)}` : ""].filter(Boolean);
    return [{
      id: 7000 + i, title: `Today: ${parts.join(" · ")}`,
      body: d.people.slice(0, 4).join(", ") + (d.people.length > 4 ? ` and ${d.people.length - 4} more` : ""),
      schedule: { at, allowWhileIdle: true },
    }];
  });
  if (list.length) await LocalNotifications.schedule({ notifications: list });
  return true;
}

export async function cancelDueReminders() {
  const { LocalNotifications } = await import("@capacitor/local-notifications");
  const pending = await LocalNotifications.getPending();
  const ours = pending.notifications.filter((n) => n.id >= 7000 && n.id < 7100);
  if (ours.length) await LocalNotifications.cancel({ notifications: ours.map((n) => ({ id: n.id })) });
}
