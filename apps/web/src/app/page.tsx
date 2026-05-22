// Server Component (default). The signal feed, Kanban, and smart search become
// client components as they are built (TODO G1, J2, I3).
export default function HomePage() {
  return (
    <main className="container flex min-h-screen flex-col items-center justify-center gap-4 py-24 text-center">
      <h1 className="text-4xl font-bold tracking-tight">CivicSignals</h1>
      <p className="max-w-prose text-muted-foreground">
        Open-source public-sector sales intelligence. This is the scaffolded
        web app (TODO A1). Product surfaces land in later epics.
      </p>
      <code className="rounded-md bg-muted px-3 py-1 text-sm">
        apps/web · Next.js 15 · React 19
      </code>
    </main>
  );
}
