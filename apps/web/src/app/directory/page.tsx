// /directory — Public entity directory index (C5).
//
// Design choice: separate /directory route (distinct from /entities) so that:
//   • /entities/*  — authenticated app pages, client-side React with TanStack Query
//   • /directory/* — public, server-rendered for SEO, no auth required
//
// This page is a Next.js Server Component that fetches the first page of
// entities at request time and renders them fully server-side so crawlers
// see content without JavaScript execution.
//
// generateMetadata provides per-page <title>, description, OG, and canonical.
//
// TODO P3: add JSON-LD Organization/GovernmentOrganization structured data.

import type { Metadata } from "next";
import Link from "next/link";
import { fetchPublicEntities } from "@/lib/public-entities-api";
import type { EntityRead } from "@/lib/entities-api";

// ---- Revalidate at the page level (5 minutes). ----
// Must be a literal — Next.js static analysis cannot resolve imported constants.
export const revalidate = 300;

// ---- SEO metadata ----

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

export const metadata: Metadata = {
  title: "Public Entity Directory | CivicSignals",
  description:
    "Browse the open public directory of school districts, cities, counties, and other government entities tracked by CivicSignals.",
  alternates: {
    canonical: `${SITE_URL}/directory`,
  },
  openGraph: {
    title: "Public Entity Directory | CivicSignals",
    description:
      "Open, citable directory of public-sector entities: school districts, cities, counties, and more.",
    url: `${SITE_URL}/directory`,
    siteName: "CivicSignals",
    type: "website",
  },
};

// ---- Helpers ----

/** Capitalise a slug/type string for display. */
function labelify(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Format USD budget into a short human-readable string. */
function formatBudget(usd: number): string {
  if (usd >= 1_000_000_000) return `$${(usd / 1_000_000_000).toFixed(1)}B`;
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(0)}M`;
  if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}

// ---- Public entity row component ----

function PublicEntityRow({ entity }: { entity: EntityRead }) {
  const geo: string[] = [];
  if (entity.state) geo.push(entity.state);

  const meta: string[] = [];
  if (entity.enrollment != null)
    meta.push(`${entity.enrollment.toLocaleString("en-US")} enrolled`);
  else if (entity.population != null)
    meta.push(`${entity.population.toLocaleString("en-US")} residents`);
  if (entity.annual_budget_usd != null)
    meta.push(`Budget ${formatBudget(entity.annual_budget_usd)}`);

  return (
    <li>
      <article
        className="group flex flex-col gap-1 rounded-lg border bg-card px-5 py-4 shadow-sm transition-shadow hover:shadow-md focus-within:ring-2 focus-within:ring-ring"
        aria-label={entity.name}
      >
        <div className="flex items-start justify-between gap-3">
          <Link
            href={`/directory/${entity.id}`}
            className="text-base font-semibold leading-snug text-foreground hover:underline focus-visible:outline-none focus-visible:underline"
          >
            {entity.name}
          </Link>
          {entity.status === "active" && (
            <span className="shrink-0 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-400">
              Active
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">
          <span>{labelify(entity.type)}</span>
          {geo.length > 0 && (
            <>
              <span aria-hidden>·</span>
              <span>{geo.join(", ")}</span>
            </>
          )}
          {meta.map((m) => (
            <span key={m} className="flex items-center gap-1">
              <span aria-hidden>·</span>
              {m}
            </span>
          ))}
        </div>

        {/* Source citation on the card itself for transparency */}
        {entity.source_urls.length > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            Source:{" "}
            <a
              href={entity.source_urls[0]}
              target="_blank"
              rel="noopener noreferrer"
              className="underline-offset-2 hover:underline"
            >
              {entity.source_urls[0]}
            </a>
            {entity.source_urls.length > 1 &&
              ` (+${entity.source_urls.length - 1} more)`}
          </p>
        )}
      </article>
    </li>
  );
}

// ---- Page component ----

export default async function PublicDirectoryPage() {
  // Fetch first page of active entities server-side (crawlers see content immediately).
  let entities: EntityRead[] = [];
  let nextCursor: string | null = null;
  let fetchError: string | null = null;

  try {
    const page = await fetchPublicEntities({ status: "active", limit: 25 });
    entities = page.items;
    nextCursor = page.next_cursor;
  } catch {
    fetchError = "The entity directory is temporarily unavailable.";
  }

  return (
    <main className="container py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">
          Public Entity Directory
        </h1>
        <p className="mt-1 text-muted-foreground">
          Open, citable directory of school districts, cities, counties, and
          other public-sector entities. Data is sourced from official government
          websites and public records.
        </p>
      </div>

      {/* Error state */}
      {fetchError && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/50 bg-destructive/10 px-5 py-4 text-sm text-destructive"
        >
          {fetchError}
        </div>
      )}

      {/* Empty state */}
      {!fetchError && entities.length === 0 && (
        <div className="py-16 text-center text-muted-foreground">
          <p className="text-lg font-medium">No entities available yet.</p>
          <p className="mt-1 text-sm">
            The entity directory populates as data is ingested.
          </p>
        </div>
      )}

      {/* Entity list — server-rendered for crawlers */}
      {entities.length > 0 && (
        <>
          <p className="mb-4 text-sm text-muted-foreground">
            Showing {entities.length.toLocaleString("en-US")} entities
            {nextCursor ? " (first page)" : ""}
          </p>

          <ul className="space-y-3" aria-label="Public entity directory">
            {entities.map((entity) => (
              <PublicEntityRow key={entity.id} entity={entity} />
            ))}
          </ul>

          {/* Pagination note — crawlers follow /directory/[id] links above */}
          {nextCursor && (
            <p className="mt-6 text-sm text-muted-foreground">
              This is the first page of results. Use the{" "}
              {/* /sitemap.xml is not a typed Next.js route — use <a> */}
              <a
                href="/sitemap.xml"
                className="text-primary underline-offset-2 hover:underline"
              >
                sitemap
              </a>{" "}
              for the complete index of all entities.
            </p>
          )}
        </>
      )}

      {/* CTA for authenticated users */}
      <div className="mt-12 border-t pt-8 text-center text-sm text-muted-foreground">
        <p>
          Want filtering, signal tracking, and contact intelligence?{" "}
          <Link
            href="/signup"
            className="text-primary underline-offset-2 hover:underline"
          >
            Sign up for CivicSignals
          </Link>{" "}
          or{" "}
          <Link
            href="/entities"
            className="text-primary underline-offset-2 hover:underline"
          >
            browse the full directory
          </Link>
          .
        </p>
      </div>

      {/* TODO P3: JSON-LD structured data (Organization/GovernmentOrganization) */}
    </main>
  );
}
