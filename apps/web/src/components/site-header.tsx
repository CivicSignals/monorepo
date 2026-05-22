// Site-wide navigation header. Intentionally minimal; expands as auth (B1) lands.
import Link from "next/link";

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
          <li>
            {/* TODO B1: replace with /signup route once auth lands */}
            <a
              href="/signup"
              className="ml-2 rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              Get started
            </a>
          </li>
        </ul>
      </nav>
    </header>
  );
}
