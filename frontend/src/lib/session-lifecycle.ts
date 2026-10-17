import type { QueryClient } from "@tanstack/react-query";

let epoch = 0;
const requests = new Set<AbortController>();
const cleanups = new Set<() => void>();

export const sessionEpoch = () => epoch;

export function assertSession(expected: number) {
  if (expected !== epoch) throw new DOMException("Session changed", "AbortError");
}

export function trackSessionRequest() {
  const controller = new AbortController();
  requests.add(controller);
  return { controller, release: () => requests.delete(controller) };
}

export function onSessionChange(cleanup: () => void) {
  cleanups.add(cleanup);
  return () => { cleanups.delete(cleanup); };
}

export function bindSessionCache(client: QueryClient) {
  return onSessionChange(() => {
    void client.cancelQueries();
    client.clear();
  });
}

/** The common boundary for logout, rejected credentials, and a new login. */
export function advanceSession() {
  epoch += 1;
  for (const controller of requests) controller.abort();
  requests.clear();
  for (const cleanup of cleanups) cleanup();
  return epoch;
}
