"use client";

// B2 — Google OAuth2 callback page.
//
// The API redirects here after a successful Google sign-in:
//   {web_base_url}/auth/callback/google#access_token=...&refresh_token=...&expires_in=...
//
// The token is in the URL *fragment* (not query string) so it never appears in
// server logs or the Referer header. This page reads the fragment client-side,
// stores the session via Zustand, and redirects to the dashboard.
//
// On error the API redirects to /login?error=...&detail=... instead of here.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/session";
import { getMe } from "@/lib/auth-api";
import { useQueryClient } from "@tanstack/react-query";
import { ME_QUERY_KEY } from "@/hooks/use-auth";

export default function GoogleCallbackPage() {
  const router = useRouter();
  const setSession = useSessionStore((s) => s.setSession);
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Fragment is only available in the browser (not during SSR).
    const fragment = window.location.hash.slice(1); // drop leading '#'
    // Immediately clear the fragment from browser history so the token
    // doesn't linger in the address bar or browser history entries.
    history.replaceState(null, "", window.location.pathname + window.location.search);

    if (!fragment) {
      setError("No token received from Google sign-in.");
      return;
    }

    const params = new URLSearchParams(fragment);
    const accessToken = params.get("access_token");
    if (!accessToken) {
      setError("Missing access token in Google sign-in response.");
      return;
    }

    // Fetch the user object so we can populate the session store.
    getMe(accessToken)
      .then((user) => {
        setSession(accessToken, user);
        queryClient.setQueryData(ME_QUERY_KEY, user);
        router.replace("/");
      })
      .catch(() => {
        setError("Failed to fetch account details after Google sign-in.");
      });
  }, [router, setSession, queryClient]);

  if (error) {
    return (
      <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
        <div
          className="w-full max-w-sm space-y-3 text-center"
          data-testid="oauth-callback-error"
        >
          <h1 className="text-2xl font-bold tracking-tight">Sign-in failed</h1>
          <p className="text-sm text-destructive">{error}</p>
          <a
            href="/login"
            className="text-sm font-medium underline underline-offset-4"
          >
            Back to sign in
          </a>
        </div>
      </main>
    );
  }

  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="oauth-callback-loading"
      >
        <p className="text-sm text-muted-foreground">Completing sign-in…</p>
      </div>
    </main>
  );
}
