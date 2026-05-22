// loading.tsx — skeleton for /directory/[id] while the server component streams (P1 req 4).
//
// Next.js App Router renders this instantly while the async page.tsx fetches data.
// Provides a sensible loading state so users don't see a blank page.

export default function PublicEntityProfileLoading() {
  return (
    <main className="container py-8">
      <div className="space-y-8 animate-pulse">
        {/* Back link skeleton */}
        <div className="h-4 w-40 rounded bg-muted" />

        {/* Header skeleton */}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <div className="h-9 w-64 rounded bg-muted" />
            <div className="h-6 w-16 rounded-full bg-muted" />
          </div>
          <div className="h-4 w-48 rounded bg-muted" />
        </div>

        {/* Overview skeleton */}
        <div className="space-y-3">
          <div className="h-6 w-28 rounded bg-muted" />
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="flex gap-4">
              <div className="h-4 w-36 rounded bg-muted" />
              <div className="h-4 w-48 rounded bg-muted" />
            </div>
          ))}
        </div>

        {/* Contacts skeleton */}
        <div className="space-y-3">
          <div className="h-6 w-32 rounded bg-muted" />
          {[1, 2].map((i) => (
            <div key={i} className="rounded-lg border bg-card px-4 py-3">
              <div className="h-4 w-40 rounded bg-muted" />
              <div className="mt-1 h-3 w-24 rounded bg-muted" />
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
