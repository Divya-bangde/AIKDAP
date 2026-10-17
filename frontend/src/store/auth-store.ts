import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

import type { components } from "@/types/api";
import { advanceSession } from "@/lib/session-lifecycle";

type UserRead = components["schemas"]["UserRead"];

interface AuthState {
  sessionId: number;
  accessToken: string | null;
  refreshToken: string | null;
  user: UserRead | null;
  setSession: (tokens: { accessToken: string; refreshToken: string }) => void;
  rotateTokens: (tokens: { accessToken: string; refreshToken: string }) => void;
  setUser: (user: UserRead) => void;
  clear: () => void;
}

const REMEMBER_KEY = "aikdap-remember";

/** Records the login form's "Remember me" choice. Must be called
 * before `setSession`, which is what writes the session to storage. */
export function setRememberMe(remember: boolean) {
  try {
    localStorage.setItem(REMEMBER_KEY, remember ? "1" : "0");
  } catch {
    // Storage blocked: the session falls back to the default below.
  }
}

/** Absent means remembered — the behaviour before the choice existed. */
function isRemembered(): boolean {
  try {
    return localStorage.getItem(REMEMBER_KEY) !== "0";
  } catch {
    return true;
  }
}

/** "Remember me" keeps the session in `localStorage` (survives closing
 * the browser); unchecked keeps it in `sessionStorage` (ends with the
 * browser session). Every write removes the copy from the other store,
 * so a session never lives in both. */
const sessionAwareStorage: StateStorage = {
  getItem: (name) => localStorage.getItem(name) ?? sessionStorage.getItem(name),
  setItem: (name, value) => {
    const [keep, drop] = isRemembered()
      ? [localStorage, sessionStorage]
      : [sessionStorage, localStorage];
    keep.setItem(name, value);
    drop.removeItem(name);
  },
  removeItem: (name) => {
    localStorage.removeItem(name);
    sessionStorage.removeItem(name);
  },
};

/** Holds only the frontend's own authentication session: the JWT pair
 * this backend issued, and the profile `GET /auth/me` returned.
 * Nothing provider-related (Gemini/Groq/OpenRouter/Ollama credentials,
 * database/Redis URLs) is ever stored here or anywhere in the
 * frontend — those never leave the backend process. */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      sessionId: 0,
      accessToken: null,
      refreshToken: null,
      user: null,
      setSession: ({ accessToken, refreshToken }) => {
        const sessionId = advanceSession();
        set({ accessToken, refreshToken, sessionId, user: null });
      },
      rotateTokens: ({ accessToken, refreshToken }) => set({ accessToken, refreshToken }),
      setUser: (user) => set({ user }),
      clear: () => {
        const sessionId = advanceSession();
        set({ accessToken: null, refreshToken: null, user: null, sessionId });
      },
    }),
    {
      name: "aikdap-auth", storage: createJSONStorage(() => sessionAwareStorage),
      partialize: ({ accessToken, refreshToken, user }) => ({ accessToken, refreshToken, user }),
    },
  ),
);

export function getAuthTokens() {
  const { accessToken, refreshToken } = useAuthStore.getState();
  return { accessToken, refreshToken };
}
