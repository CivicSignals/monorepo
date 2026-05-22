// loading.tsx — skeleton for /s/[id] while the server component streams (P2).
//
// Next.js App Router renders this instantly while the async page.tsx fetches data,
// so users don't see a blank page. Mirrors the /directory/[id] loading skeleton.

export default function PublicSignalLoading() {
  return (
    <main className="container py-8">
      <div className="space-y-8 animate-pulse">
        {/* Back link skeleton */}
        <div className="h-4 w-48 rounded bg-muted" />

        {/* Header skeleton */}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <div className="h-9 w-80 rounded bg-muted" />
            <div className="h-6 w-24 rounded-full bg-muted" />
          </div>
          <div className="h-4 w-56 rounded bg-muted" />
        </div>

        {/* Summary skeleton */}
        <div className="space-y-2">
          <div className="h-6 w-28 rounded bg-muted" />
          <div className="h-4 w-full rounded bg-muted" />
          <div className="h-4 w-3/4 rounded bg-muted" />
        </div>

        {/* Details skeleton */}
        <div className="space-y-3">
          <div className="h-6 w-24 rounded bg-muted" />
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex gap-4">
              <div className="h-4 w-36 rounded bg-muted" />
              <div className="h-4 w-48 rounded bg-muted" />
            </div>
          ))}
        </div>

        {/* Source citations skeleton */}
        <div className="space-y-2">
          <div className="h-6 w-36 rounded bg-muted" />
          <div className="h-4 w-72 rounded bg-muted" />
          <div className="h-4 w-56 rounded bg-muted" />
        </div>
      </div>
    </main>
  );
}
