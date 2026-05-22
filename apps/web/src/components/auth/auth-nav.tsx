"use client";

import Link from "next/link";
import { useSessionStore } from "@/store/session";
import { useLogout } from "@/hooks/use-auth";

// Client-side auth nav: shows Sign in / Get started when signed out, and the
// user's email + Sign out when signed in. Reads the session store (B1); becomes
// workspace-aware in B5.
export function AuthNav() {
  const user = useSessionStore((s) => s.user);
  const token = useSessionStore((s) => s.accessToken);
  const logout = useLogout();

  if (token && user) {
    return (
      <>
        <li
          className="hidden text-sm text-muted-foreground sm:block"
          aria-live="polite"
        >
          {user.email}
        </li>
        <li>
          <button
            type="button"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            className="ml-2 rounded-md px-3 py-1.5 text-sm font-semibold text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60"
          >
            {logout.isPending ? "Signing out…" : "Sign out"}
          </button>
        </li>
      </>
    );
  }

  return (
    <>
      <li>
        <Link
          href="/login"
          className="rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Sign in
        </Link>
      </li>
      <li>
        <Link
          href="/signup"
          className="ml-2 rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Get started
        </Link>
      </li>
    </>
  );
}
