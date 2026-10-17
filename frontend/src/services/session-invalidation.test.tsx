import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bindSessionCache } from "@/lib/session-lifecycle";
import { useAuth } from "@/hooks/useAuth";
import { request, requestBlob } from "@/services/client";
import { useAuthStore } from "@/store/auth-store";
import { makeUser } from "@/test/fixtures";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const signIn = (user: string) => useAuthStore.getState().setSession({ accessToken: user, refreshToken: `${user}-refresh` });
function Profile() { const auth = useAuth(); return <div>{auth.user?.email ?? "Signed out"}</div>; }

afterEach(() => { cleanup(); useAuthStore.getState().clear(); vi.restoreAllMocks(); });

describe("central session invalidation", () => {
  it("clears A profile/projects/assets on rejected refresh and loads B without cached A data", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const unbind = bindSessionCache(client);
    signIn("A");
    const bProfile = deferred<Response>();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      const path = String(url);
      if (path.endsWith("/auth/refresh") || path.endsWith("/force-expired")) return json({ detail: "Expired" }, 401);
      if ((init?.headers as Record<string, string>).Authorization === "Bearer B") return bProfile.promise;
      return json(makeUser({ id: "A", email: "a@example.com" }));
    });
    render(<QueryClientProvider client={client}><Profile /></QueryClientProvider>);
    expect(await screen.findByText("a@example.com")).toBeInTheDocument();
    client.setQueryData(["projects"], [{ id: "A-project" }]);
    client.setQueryData(["assets"], [{ id: "A-asset" }]);
    await act(async () => { await expect(request("/force-expired")).rejects.toMatchObject({ status: 401 }); });
    expect(useAuthStore.getState().user).toBeNull();
    expect(client.getQueryData(["projects"])).toBeUndefined();
    expect(client.getQueryData(["assets"])).toBeUndefined();
    act(() => signIn("B"));
    expect(screen.queryByText("a@example.com")).not.toBeInTheDocument();
    await act(async () => { bProfile.resolve(json(makeUser({ id: "B", email: "b@example.com" }))); });
    expect(await screen.findByText("b@example.com")).toBeInTheDocument();
    unbind();
  });

  it("aborts all A requests and rejects a transport that ignores abort without repopulating B cache", async () => {
    signIn("A");
    const pending = deferred<Response>();
    let signal: AbortSignal | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => { signal = init?.signal as AbortSignal; return pending.promise; });
    const client = new QueryClient();
    const unbind = bindSessionCache(client);
    const late = request("/api/v1/projects").then((data) => client.setQueryData(["projects"], data));
    const rejected = expect(late).rejects.toMatchObject({ name: "AbortError" });
    useAuthStore.getState().clear();
    expect(signal?.aborted).toBe(true);
    signIn("B");
    client.setQueryData(["projects"], [{ id: "B-project" }]);
    pending.resolve(json([{ id: "A-project" }]));
    await rejected;
    expect(client.getQueryData(["projects"])).toEqual([{ id: "B-project" }]);
    unbind();
  });

  it("cannot restore A tokens from a refresh that resolves after B signs in", async () => {
    signIn("A");
    const refresh = deferred<Response>();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => String(url).endsWith("/auth/refresh") ? refresh.promise : json({}, 401));
    const late = request("/api/v1/auth/me");
    const rejected = expect(late).rejects.toMatchObject({ name: "AbortError" });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    useAuthStore.getState().clear();
    signIn("B");
    refresh.resolve(json({ access_token: "A-new", refresh_token: "A-refresh-new" }));
    await rejected;
    expect(useAuthStore.getState().accessToken).toBe("B");
  });

  it("guards binary body parsing as well as response arrival", async () => {
    signIn("A");
    const body = deferred<Blob>();
    const response = new Response();
    vi.spyOn(response, "blob").mockImplementation(() => body.promise);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
    const late = requestBlob("/api/v1/assets/download/a");
    const rejected = expect(late).rejects.toMatchObject({ name: "AbortError" });
    await waitFor(() => expect(response.blob).toHaveBeenCalled());
    useAuthStore.getState().clear();
    body.resolve(new Blob(["A private paper"]));
    await rejected;
  });

  it("invalidates on a second rejected access token after successful refresh", async () => {
    signIn("A");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => String(url).endsWith("/auth/refresh") ? json({ access_token: "A-new", refresh_token: "A-new-refresh" }) : json({ detail: "Rejected" }, 401));
    await expect(request("/private")).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().accessToken).toBeNull();
  });
});
