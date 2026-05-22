// N6 — Pricing hero section
export function PricingHero() {
  return (
    <section className="border-b bg-muted/30 py-20 text-center">
      <div className="container">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          Pricing that&apos;s actually public
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">
          Every plan is posted here — no &ldquo;contact sales&rdquo; wall for
          SMB tiers. CivicSignals is{" "}
          <strong>5–10× cheaper</strong> than GovWin or Starbridge for 80% of
          SLED vendors. Self-host is always free under{" "}
          <a
            href="https://www.gnu.org/licenses/agpl-3.0.html"
            className="underline underline-offset-4 hover:text-foreground"
            target="_blank"
            rel="noopener noreferrer"
          >
            AGPL-3.0
          </a>
          .
        </p>
        <p className="mt-3 text-sm text-muted-foreground">
          Prices are illustrative — final prices confirmed at launch (see tasks
          N1/N2/LC-13).
        </p>
      </div>
    </section>
  );
}
