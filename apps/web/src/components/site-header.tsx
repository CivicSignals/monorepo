// Site-wide navigation header. Auth state (sign in / sign out) is a client
// island (AuthNav, B1); the rest stays a server component.
import Link from "next/link";
import { AuthNav } from "@/components/auth/auth-nav";

// Internal routes that exist in the App Router.
const INTERNAL_LINKS = [{ href: "/pricing" as const, label: "Pricing" }];

// External links rendered as plain <a> to avoid typedRoutes validation.
const EXTERNAL_LINKS = [
  { href: "https://docs.civicsignals.io", label: "Docs" },
  { href: "https://github.com/CivicSignals/monorepo", label: "GitHub" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <nav
        aria-label="Main navigation"
        className="container flex h-14 items-center justify-between gap-4"
      >
        <Link
          href="/"
          className="flex items-center gap-2 font-bold focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
        >
          <span aria-label="CivicSignals home">CivicSignals</span>
        </Link>

        <ul className="flex items-center gap-1" role="list">
          {INTERNAL_LINKS.map(({ href, label }) => (
            <li key={href}>
              <Link
                href={href}
                className="rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {label}
              </Link>
            </li>
          ))}
          {EXTERNAL_LINKS.map(({ href, label }) => (
            <li key={href}>
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {label}
              </a>
            </li>
          ))}
          <AuthNav />
        </ul>
      </nav>
    </header>
  );
}
