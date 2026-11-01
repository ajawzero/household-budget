// The fetch wrapper every API call goes through. Sends the session cookie,
// turns errors into ApiError with the server's plain message, and sends a
// 401 to the sign-in page.

/** An HTTP error with the server's plain message. Never holds the request body. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler = () => {
  window.location.assign("/login");
};

/** The app sets this so a 401 clears the user and routes to /login. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

export interface RequestOptions {
  body?: unknown;
  /** Login and "who am I" handle 401 themselves. */
  handle401?: boolean;
}

const FALLBACK_MESSAGES: Record<number, string> = {
  400: "The request was not accepted.",
  401: "Please sign in.",
  403: "You do not have access to this.",
  404: "Not found.",
  422: "Some fields are not valid.",
  429: "Too many attempts. Try again later.",
};

async function errorMessage(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json();
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.length > 0) return detail;
    }
  } catch {
    // Not JSON. Fall through to the plain message.
  }
  return FALLBACK_MESSAGES[res.status] ?? "Something went wrong. Try again.";
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, credentials: "same-origin", headers };
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Could not reach the server.");
  }

  if (res.status === 401 && opts.handle401 !== false) {
    onUnauthorized();
  }
  if (!res.ok) {
    throw new ApiError(res.status, await errorMessage(res));
  }
  if (res.status === 204 || res.status === 205) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

/**
 * POST that answers with server-sent events (Ask). Errors and 401 work like
 * `request`. The caller reads `response.body`.
 */
export async function postStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Could not reach the server.");
  }
  if (res.status === 401) onUnauthorized();
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res;
}

/** "?a=1&b=2" from the set values only. Empty, null and undefined are left out. */
export function query(params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, String(value));
  }
  const text = qs.toString();
  return text ? `?${text}` : "";
}
