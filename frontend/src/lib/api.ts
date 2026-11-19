// Fetch wrapper: same-origin cookie session; every request that changes something carries
// the session's CSRF token in X-CSRF-Token. Errors carry the server's readable message.

let csrfToken = "";
export function setCsrf(token: string | undefined) {
  if (token) csrfToken = token;
}

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
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  const res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/public/") && !path.startsWith("/api/login")) {
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
