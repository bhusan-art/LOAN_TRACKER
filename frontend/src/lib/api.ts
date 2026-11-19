// Fetch wrapper: same-origin cookie session; every request that changes something carries
// the session's CSRF token in X-CSRF-Token. Errors carry the server's readable message.

let csrfToken = "";
export function setCsrf(token: string | undefined) {
  if (token) csrfToken = token;
}

// The Android app: the server's address and this phone's token (see lib/native.ts). In a
// browser both stay empty: same-origin requests with the session cookie and CSRF token.
let apiBase = "";
let bearer = "";
let bookHeader = "";
export function configureApp(base: string, token: string | null) {
  apiBase = base.replace(/\/+$/, "");
  bearer = token ?? "";
}
export function setBookHeader(book: string) {
  bookHeader = book;
}
export const appToken = () => bearer;
export const url = (path: string) => apiBase + path;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Opts = { method?: string; json?: unknown; form?: FormData; signal?: AbortSignal };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? (opts.json !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  if (apiBase) {
    // the Android app: a phone token (once signed in) instead of cookies; never the CSRF
    // header, which only protects cookie sessions and is not allowed across origins
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    if (bookHeader) headers["X-Book"] = bookHeader;
  } else if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  const res = await fetch(apiBase + path, { method, headers, body, credentials: apiBase ? "omit" : "same-origin", signal: opts.signal });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/public/") && !path.startsWith("/api/login") && !path.startsWith("/api/device/login")) {
      if (data?.reason && data?.detail) {
        try {
          // keep a reason already noted (an idle sign-out explains itself better)
          if (!sessionStorage.getItem("signedOutReason")) sessionStorage.setItem("signedOutReason", data.detail);
        } catch {
          /* private mode */
        }
      }
      window.dispatchEvent(new Event("ledgerly:logged-out"));
    }
    throw new ApiError(res.status, (data && (data.detail as string)) || `Request failed (${res.status})`);
  }
  if (data && typeof data === "object" && "csrf" in data) setCsrf(data.csrf);
  return data as T;
}

export const post = <T = { message?: string }>(path: string, json: unknown = {}) => api<T>(path, { json });
export const upload = <T = { message?: string }>(path: string, form: FormData) => api<T>(path, { form });

/** Choose the book (All / Business / Personal): the server reads it from this cookie. */
export function setBookCookie(book: string) {
  document.cookie = `book=${book}; path=/; max-age=${365 * 86400}; samesite=lax`;
}

/** Fetch a file the way the API does (cookie or phone token), for sharing or saving. */
export async function fetchFile(path: string): Promise<{ blob: Blob; name: string }> {
  const headers: Record<string, string> = {};
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  if (apiBase && bookHeader) headers["X-Book"] = bookHeader;
  const res = await fetch(apiBase + path, { headers, credentials: apiBase ? "omit" : "same-origin" });
  if (!res.ok) {
    let msg = `Download failed (${res.status})`;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const cd = res.headers.get("content-disposition") ?? "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i) ?? cd.match(/filename="?([^";]+)"?/i);
  return { blob: await res.blob(), name: m ? decodeURIComponent(m[1]) : path.split("/").pop() || "file" };
}
