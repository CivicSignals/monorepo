// Client-side session store (B1).
//
// The API issues bearer JWTs in the login/signup response body (doc 08 §1.3).
// The web app is a separate origin from the API, so the backend does not set an
// HttpOnly cookie for us; we hold the access token client-side. Per doc 06 §2,
// client-only state lives in Zustand (server state is TanStack Query). The token
// is persisted to localStorage so a refresh keeps the user signed in.
//
// Security note: localStorage is readable by JS, so this trades some XSS
// exposure for simplicity in the MVP foundation. The hardening path (a real
// HttpOnly `cs_session` cookie set by a same-origin Next route handler / BFF) is
// a documented follow-up — the rest of the app reads the token only through this
// store, so swapping the storage backend is a localized change. The threat
// model (§4.2) tracks token-theft-via-XSS as the main residual risk here.

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { User } from "@/lib/auth-api";

interface SessionState {
  accessToken: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  setUser: (user: User | null) => void;
  clear: () => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      accessToken: null,
      user: null,
      setSession: (accessToken, user) => set({ accessToken, user }),
      setUser: (user) => set({ user }),
      clear: () => set({ accessToken: null, user: null }),
    }),
    {
      name: "cs.session",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
