// not-found.tsx — shown when /s/[id] calls notFound() (P2).
//
// Next.js App Router catches the notFound() throw and renders this component.
// Provides a user-friendly 404 message with links back to public surfaces.

import Link from "next/link";

export default function SignalNotFound() {
  return (
    <main className="container py-24 text-center">
      <h1 className="text-4xl font-bold tracking-tight">Signal not found</h1>
      <p className="mt-4 text-muted-foreground">
        We couldn&apos;t find the signal you&apos;re looking for. It may have been merged,
        removed, or the URL may be incorrect.
      </p>
      <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
        <Link
          href="/directory"
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Browse the directory
        </Link>
        <Link
          href="/"
          className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted"
        >
          Go home
        </Link>
      </div>
    </main>
  );
}
