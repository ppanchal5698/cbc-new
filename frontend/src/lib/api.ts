/**
 * The only way this app talks to Django.
 *
 * Authentication is the Django **session cookie**, not a token held in
 * JavaScript: `SessionAuthentication` is already first in the DRF defaults and
 * `LoginView` already calls Django's `login()`, so there is nothing to store,
 * nothing to refresh, and nothing for an XSS to read out of `localStorage`.
 *
 * Unsafe methods carry the CSRF token from the `csrftoken` cookie. `GET
 * /api/health/` is decorated with `ensure_csrf_cookie`, so calling it once on
 * boot is what puts that cookie in place before the first login POST.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function cookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const hit = document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null;
}

/**
 * Django answers a bad request with `{"detail": "..."}` and the intake path in
 * particular returns a *specific* reason (bad magic bytes, checksum mismatch).
 * Surfacing that verbatim is the point — a generic "upload failed" throws away
 * the only useful half of the response.
 */
function messageOf(body: unknown, fallback: string): string {
  if (typeof body === "string" && body) return body;
  if (body && typeof body === "object") {
    const rec = body as Record<string, unknown>;
    if (typeof rec.detail === "string") return rec.detail;
    const first = Object.values(rec)[0];
    if (typeof first === "string") return first;
    if (Array.isArray(first) && typeof first[0] === "string") return first[0];
  }
  return fallback;
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const token = cookie("csrftoken");
    if (token) headers.set("X-CSRFToken", token);
  }
  // FormData sets its own multipart boundary; setting the header by hand breaks it.
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* a proxy error page, a traceback — keep the text */
  }

  if (!res.ok) {
    throw new ApiError(res.status, body, messageOf(body, `${res.status} ${res.statusText}`));
  }
  return body as T;
}

/** DRF pagination envelope (`common.pagination.StandardPagination`). */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/** Puts the CSRF cookie in place. Call once, before anything unsafe. */
export const primeCsrf = () => apiFetch("/api/health/").catch(() => undefined);
