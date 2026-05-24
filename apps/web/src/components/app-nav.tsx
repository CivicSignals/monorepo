"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useSessionStore } from "@/store/session";

// Primary in-app navigation for authenticated users (G1/G2 feed + the other
// authed destinations that already exist as pages). Rendered as a client island
// next to AuthNav so it can gate on the Zustand session store (B1) the same way
// AuthNav does — the marketing links in SiteHeader stay a server component. When
// signed out this renders nothing so the public header is unchanged.
//
// Hrefs are written as explicit literals (not mapped from an array) so Next's
// typedRoutes can statically check each one against the generated route map;
// `/icp` and `/settings` have no index page, so we link their real entry points
// (`/icp/new`, `/settings/integrations`).

const linkClass =
  "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

export function AppNav(): ReactNode {
  const user = useSessionStore((s) => s.user);
  const token = useSessionStore((s) => s.accessToken);

  // Only show the authed app nav when a session exists (mirrors AuthNav).
  if (!token || !user) {
    return null;
  }

  return (
    <>
      <li>
        <Link href="/feed" data-testid="nav-feed" className={linkClass}>
          Feed
        </Link>
      </li>
      <li>
        <Link href="/pipeline" data-testid="nav-pipeline" className={linkClass}>
          Pipeline
        </Link>
      </li>
      <li>
        <Link href="/directory" data-testid="nav-directory" className={linkClass}>
          Directory
        </Link>
      </li>
      <li>
        <Link href="/icp/new" data-testid="nav-icp" className={linkClass}>
          ICP
        </Link>
      </li>
      <li>
        <Link
          href="/settings/integrations"
          data-testid="nav-settings"
          className={linkClass}
        >
          Settings
        </Link>
      </li>
    </>
  );
}
