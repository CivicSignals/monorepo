// /directory/[id] — Public entity profile page (C5).
//
// Server component: fetches entity + first page of children server-side.
// Fully server-rendered so crawlers see name, description, source citations.
//
// generateMetadata: per-entity <title>, description, canonical, OG.
//
// Source citations (entity.source_urls + contact source_url) are cited
// prominently as required by C5 req 3.
//
// TODO P3: add JSON-LD Organization/GovernmentOrganization structured data.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import {
  fetchPublicEntity,
  fetchPublicEntityChildren,
} from "@/lib/public-entities-api";
import type { EntityRead } from "@/lib/entities-api";

// ---- Revalidate at the page level (5 minutes). ----
// Must be a literal — Next.js static analysis cannot resolve imported constants.
export const revalidate = 300;

// ---- Dynamic params ----
interface PageProps {
  params: Promise<{ id: string }>;
}

// ---- SEO metadata ----

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  const entity = await fetchPublicEntity(id);

  if (!entity) {
    return {
      title: "Entity Not Found | CivicSignals",
    };
  }

  const geo = [entity.state, entity.region]
    .filter(Boolean)
    .filter((v, i, arr) => arr.indexOf(v) === i)
    .join(", ");

  const typeLabel = entity.type
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const descriptionParts: string[] = [];
  if (entity.enrollment != null)
    descriptionParts.push(`${entity.enrollment.toLocaleString("en-US")} students`);
  else if (entity.population != null)
    descriptionParts.push(`Population: ${entity.population.toLocaleString("en-US")}`);
  if (entity.annual_budget_usd != null) {
    const b =
      entity.annual_budget_usd >= 1_000_000_000
        ? `$${(entity.annual_budget_usd / 1_000_000_000).toFixed(1)}B`
        : entity.annual_budget_usd >= 1_000_000
          ? `$${(entity.annual_budget_usd / 1_000_000).toFixed(0)}M`
          : `$${entity.annual_budget_usd}`;
    descriptionParts.push(`Annual budget ${b}`);
  }

  const description = `${typeLabel} in ${geo || entity.country}.${descriptionParts.length > 0 ? " " + descriptionParts.join(". ") + "." : ""} Data sourced from official public records.`;

  const canonicalUrl = `${SITE_URL}/directory/${id}`;

  return {
    title: `${entity.name} | CivicSignals`,
    description,
    alternates: {
      canonical: canonicalUrl,
    },
    openGraph: {
      title: `${entity.name} | CivicSignals`,
      description,
      url: canonicalUrl,
      siteName: "CivicSignals",
      type: "website",
    },
  };
}

// ---- Helpers ----

function labelify(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatBudget(usd: number): string {
  if (usd >= 1_000_000_000) return `$${(usd / 1_000_000_000).toFixed(1)}B`;
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(0)}M`;
  if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}

function statusClass(status: string): string {
  if (status === "active")
    return "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400";
  if (status === "dissolved")
    return "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400";
  return "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400";
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
      <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className="text-sm text-foreground">{value}</dd>
    </div>
  );
}

// ---- Child entity row ----

function ChildEntityRow({ entity }: { entity: EntityRead }) {
  return (
    <li>
      <article
        className="flex flex-col gap-1 rounded-lg border bg-card px-5 py-3 shadow-sm hover:shadow-md transition-shadow"
        aria-label={entity.name}
      >
        <Link
          href={`/directory/${entity.id}`}
          className="text-sm font-semibold text-foreground hover:underline focus-visible:outline-none focus-visible:underline"
        >
          {entity.name}
        </Link>
        <p className="text-xs text-muted-foreground">
          {labelify(entity.type)}
          {entity.enrollment != null &&
            ` · ${entity.enrollment.toLocaleString("en-US")} enrolled`}
        </p>
      </article>
    </li>
  );
}

// ---- Page component ----

export default async function PublicEntityProfilePage({ params }: PageProps) {
  const { id } = await params;

  const entity = await fetchPublicEntity(id);
  if (!entity) notFound();

  // Fetch first page of children server-side.
  let children: EntityRead[] = [];
  let childrenNextCursor: string | null = null;
  try {
    const childPage = await fetchPublicEntityChildren(id, { limit: 10 });
    children = childPage.items;
    childrenNextCursor = childPage.next_cursor;
  } catch {
    // Non-fatal; render without children.
  }

  const geo = [entity.state, entity.region]
    .filter(Boolean)
    .filter((v, i, arr) => arr.indexOf(v) === i);

  return (
    <main className="container py-8">
      <div className="space-y-8">
        {/* Back link + breadcrumb */}
        <nav aria-label="Breadcrumb">
          <Link
            href="/directory"
            className="text-sm text-muted-foreground hover:text-foreground hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            &larr; Public entity directory
          </Link>
        </nav>

        {/* Header */}
        <header className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{entity.name}</h1>
            <span
              className={`rounded-full px-2.5 py-0.5 text-sm font-medium ${statusClass(entity.status)}`}
            >
              {labelify(entity.status)}
            </span>
          </div>
          <p className="text-muted-foreground">
            {[labelify(entity.type), ...geo].join(" · ")}
          </p>
        </header>

        {/* Overview */}
        <section aria-labelledby="overview-heading">
          <h2 id="overview-heading" className="mb-3 text-xl font-semibold">
            Overview
          </h2>
          <dl className="space-y-2">
            {entity.enrollment != null && (
              <DetailRow
                label="Enrollment"
                value={entity.enrollment.toLocaleString("en-US") + " students"}
              />
            )}
            {entity.population != null && (
              <DetailRow
                label="Population"
                value={entity.population.toLocaleString("en-US")}
              />
            )}
            {entity.annual_budget_usd != null && (
              <DetailRow
                label="Annual budget"
                value={formatBudget(entity.annual_budget_usd)}
              />
            )}
            {entity.board_meeting_cadence && (
              <DetailRow
                label="Board meetings"
                value={entity.board_meeting_cadence}
              />
            )}
            {entity.primary_website && (
              <DetailRow
                label="Website"
                value={
                  <a
                    href={entity.primary_website}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary underline-offset-2 hover:underline"
                  >
                    {entity.primary_website}
                  </a>
                }
              />
            )}
            {entity.procurement_portal_url && (
              <DetailRow
                label="Procurement portal"
                value={
                  <a
                    href={entity.procurement_portal_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary underline-offset-2 hover:underline"
                  >
                    {entity.procurement_portal_url}
                  </a>
                }
              />
            )}
            {entity.nces_leaid && (
              <DetailRow label="NCES LEAID" value={entity.nces_leaid} />
            )}
            {entity.ipeds_unitid && (
              <DetailRow label="IPEDS Unit ID" value={entity.ipeds_unitid} />
            )}
            {entity.census_gid && (
              <DetailRow label="Census GID" value={entity.census_gid} />
            )}
          </dl>
        </section>

        {/* Source citations — prominent, required by C5 req 3 */}
        {entity.source_urls.length > 0 && (
          <section aria-labelledby="sources-heading">
            <h2 id="sources-heading" className="mb-3 text-xl font-semibold">
              Source Citations
            </h2>
            <p className="mb-2 text-sm text-muted-foreground">
              Data on this page is derived from the following public sources:
            </p>
            <ul className="space-y-1" aria-label="Source citations">
              {entity.source_urls.map((url) => (
                <li key={url}>
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-primary underline-offset-2 hover:underline"
                  >
                    {url}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Parent entity */}
        {entity.parent_id && (
          <section aria-labelledby="parent-heading">
            <h2 id="parent-heading" className="mb-3 text-xl font-semibold">
              Parent Entity
            </h2>
            <Link
              href={`/directory/${entity.parent_id}`}
              className="text-sm text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
            >
              View parent entity &rarr;
            </Link>
          </section>
        )}

        {/* Sub-entities / Children */}
        {(children.length > 0 || childrenNextCursor) && (
          <section aria-labelledby="children-heading">
            <h2 id="children-heading" className="mb-3 text-xl font-semibold">
              Sub-entities
            </h2>
            <ul className="space-y-3" aria-label="Child entities">
              {children.map((child) => (
                <ChildEntityRow key={child.id} entity={child} />
              ))}
            </ul>
            {childrenNextCursor && (
              <p className="mt-3 text-sm text-muted-foreground">
                More sub-entities available. Sign in to browse the full list.
              </p>
            )}
          </section>
        )}

        {/* CTA linking to authenticated app */}
        <div className="border-t pt-8 text-sm text-muted-foreground">
          <p>
            Want signal tracking, contact intelligence, and CRM push for{" "}
            <strong>{entity.name}</strong>?{" "}
            <Link
              href="/signup"
              className="text-primary underline-offset-2 hover:underline"
            >
              Sign up for CivicSignals
            </Link>{" "}
            or{" "}
            <Link
              href={`/entities/${entity.id}`}
              className="text-primary underline-offset-2 hover:underline"
            >
              view in the app
            </Link>
            .
          </p>
        </div>

        {/* TODO P3: JSON-LD Organization/GovernmentOrganization structured data */}
      </div>
    </main>
  );
}
