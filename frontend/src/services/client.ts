import { assertSession, onSessionChange, sessionEpoch, trackSessionRequest } from "@/lib/session-lifecycle";
import { parseApiError, type ApiError } from "@/lib/api-error";
import { getAuthTokens, useAuthStore } from "@/store/auth-store";
import type { components } from "@/types/api";

/** The ONLY module in this frontend that calls `fetch`. Every other
 * service module (auth/projects/assets/research/health) goes through
 * `request()`/`requestForm()` here — see
 * `test_services_do_not_call_fetch_directly` for the guard. */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

if (import.meta.env.DEV && !BASE_URL) {
  // Fails loudly at import time rather than producing confusing
  // "Failed to fetch relative URL" errors deep in a component tree.
  throw new Error(
    "VITE_API_BASE_URL is not set. Copy frontend/.env.example to frontend/.env.",
  );
}

type TokenPair = components["schemas"]["TokenPair"];

let refreshInFlight: { epoch: number; promise: Promise<string | null> } | null = null;

/** Exchange the stored refresh token for a new access token exactly
 * once per concurrent burst of 401s — `refreshInFlight` coalesces
 * simultaneous callers instead of firing N refresh requests. Returns
 * the new access token, or `null` if refresh failed (expired/invalid
 * refresh token), in which case the caller clears the session. */
async function refreshAccessToken(epoch: number): Promise<string | null> {
  if (refreshInFlight?.epoch === epoch) return refreshInFlight.promise;
  assertSession(epoch);
  const { refreshToken } = getAuthTokens();
  if (!refreshToken) return null;
  const tracked = trackSessionRequest();
  const promise = (async () => {
    try {
      const response = await doFetch("/api/v1/auth/refresh", {
        method: "POST", signal: tracked.controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      assertSession(epoch);
      if (!response.ok) return null;
      const tokens = (await response.json()) as TokenPair;
      assertSession(epoch);
      useAuthStore.getState().rotateTokens({
        accessToken: tokens.access_token, refreshToken: tokens.refresh_token,
      });
      return tokens.access_token;
    } catch (error) {
      assertSession(epoch);
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      return null;
    } finally {
      tracked.release();
      if (refreshInFlight?.epoch === epoch) refreshInFlight = null;
    }
  })();
  refreshInFlight = { epoch, promise };
  return promise;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** Skip the Authorization header — only `login`/`register` need this. */
  auth?: boolean;
}

/** The single point where a request leaves the app.
 *
 * `ngrok-skip-browser-warning` is sent on every request because ngrok's
 * free tier answers a browser-looking request with an HTML interstitial
 * instead of proxying to the API — which would surface as a JSON parse
 * failure on every call, not as an obvious "the tunnel is showing a
 * warning page". Any other backend ignores an unrecognized request
 * header, so this is set unconditionally rather than by sniffing
 * `BASE_URL` for an ngrok hostname. The backend allows it through CORS
 * preflight (`allow_headers=["*"]`).
 */
async function doFetch(path: string, init: RequestInit): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { ...(init.headers as Record<string, string>), "ngrok-skip-browser-warning": "true" },
  });
}

/** Shared auth/401-refresh/error handler for all authenticated request types.
 * Builds auth header, calls doFetch, handles 401 with one silent retry, and
 * throws on any non-ok response. Callers handle the response body parsing
 * (JSON vs blob). */
async function fetchWithAuthAndRetry(
  path: string,
  init: RequestInit,
  auth: boolean,
  epoch: number,
  isRetry = false,
): Promise<Response> {
  const headers: Record<string, string> = (init.headers as Record<string, string>) ?? {};
  if (auth) {
    const { accessToken } = getAuthTokens();
    if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  }

  assertSession(epoch);
  const response = await doFetch(path, { ...init, headers });
  assertSession(epoch);

  if (response.status === 401 && auth && !isRetry) {
    const newToken = await refreshAccessToken(epoch);
    if (newToken) return fetchWithAuthAndRetry(path, init, auth, epoch, true);
  }

  if (!response.ok) {
    const error = (await parseApiError(response)) satisfies ApiError;
    assertSession(epoch);
    if (response.status === 401 && auth) useAuthStore.getState().clear();
    throw error;
  }

  return response;
}

/** JSON request/response. Attaches the bearer token unless
 * `auth: false`, and on a 401 attempts exactly one silent refresh +
 * retry before surfacing the error (Phase 9's "prevent refresh
 * loops" — `isRetry` guarantees a second 401 is never retried). */
/** Keep the session guard through body parsing: a response can arrive before
 * logout while its JSON/blob completes afterwards. */
async function sessionRequest<T>(path: string, init: RequestInit, auth: boolean, parse: (response: Response) => Promise<T>): Promise<T> {
  const epoch = sessionEpoch();
  const tracked = trackSessionRequest();
  try {
    const response = await fetchWithAuthAndRetry(path, { ...init, signal: tracked.controller.signal }, auth, epoch);
    const result = await parse(response);
    assertSession(epoch);
    return result;
  } finally {
    tracked.release();
  }
}

export function request<T>(path: string, { method = "GET", body, auth = true }: RequestOptions = {}): Promise<T> {
  return sessionRequest(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }, auth, async (response) => response.status === 204 ? undefined as T : await response.json() as T);
}

export function requestForm<T>(path: string, formData: FormData): Promise<T> {
  return sessionRequest(path, { method: "POST", body: formData }, true, async (response) => await response.json() as T);
}

export function requestBlob(path: string): Promise<Blob> {
  return sessionRequest(path, { method: "GET" }, true, (response) => response.blob());
}

/** Opens a Server-Sent Events stream. `EventSource` cannot send an
 * Authorization header, so the caller passes a short-lived, single-
 * stream token (issued by a normal authenticated `request()`) in the
 * path's query string — never the access token itself. Returns `null`
 * where `EventSource` does not exist (tests, very old browsers), and
 * callers fall back to polling. */
export function openEventStream(path: string): EventSource | null {
  if (typeof EventSource === "undefined") return null;
  const stream = new EventSource(`${BASE_URL}${path}`);
  const close = stream.close.bind(stream);
  const unsubscribe = onSessionChange(() => stream.close());
  stream.close = () => { unsubscribe(); close(); };
  return stream;
}
