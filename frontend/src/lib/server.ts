// The Android app's server address (kept free of Capacitor so it can be unit-tested).

/**
 * Check and tidy a server address typed on the phone. Only https is allowed (Tailscale's
 * `tailscale serve` gives the PC an https address), so nothing is ever sent in plain text.
 */
export function normaliseServer(input: string): { url?: string; error?: string } {
  let v = input.trim();
  if (!v) return { error: "Enter your server's address." };
  if (!/^[a-z]+:\/\//i.test(v)) v = "https://" + v;
  let u: URL;
  try {
    u = new URL(v);
  } catch {
    return { error: "That is not a web address." };
  }
  if (u.protocol !== "https:") return { error: "The address must start with https:// (see the README: tailscale serve)." };
  if (u.username || u.password) return { error: "Leave the user name and password out of the address." };
  if (u.search || u.hash) return { error: "Use just the address, like https://my-pc.tail1234.ts.net" };
  return { url: u.origin + u.pathname.replace(/\/+$/, "") };
}
