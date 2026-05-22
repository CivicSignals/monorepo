// EntityProfile — client island for the /entities/[id] profile page (C3/C4).
//
// Shows: name, kind, geo, hierarchy (parent + children), key stats, source URLs,
// and a Contacts section (C4) with verified/stale badge and load-more pagination.
// Children are loaded via useEntityChildren (infinite query, cursor-paginated).
// Contacts are loaded via ContactList (useEntityContacts, cursor-paginated).

"use client";

import Link from "next/link";
import { useEntity, useEntityChildren } from "@/hooks/use-entities";
import { EntityCard } from "@/components/entities/entity-card";
import { ContactList } from "@/components/contacts/contact-list";
import { ProblemError } from "@/lib/auth-api";

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

/** Status badge colour. */
function statusClass(status: string): string {
  if (status === "active")
    return "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400";
  if (status === "dissolved")
    return "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400";
  return "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400";
}

// ---- Detail row helper ----
function DetailRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
      <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm text-foreground">{value}</dd>
    </div>
  );
}

// ---- Loading skeleton ----
function ProfileSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading entity">
      <div className="mb-4 h-8 w-2/3 animate-pulse rounded-md bg-muted" />
      <div className="mb-2 h-4 w-1/3 animate-pulse rounded bg-muted" />
      <div className="mb-6 h-4 w-1/4 animate-pulse rounded bg-muted" />
      <div className="space-y-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-5 animate-pulse rounded bg-muted" />
        ))}
      </div>
    </div>
  );
}

// ---- Main component ----
interface EntityProfileProps {
  id: string;
}

export function EntityProfile({ id }: EntityProfileProps) {
  const { data: entity, isLoading, error } = useEntity(id);
  const {
    data: childrenData,
    isLoading: childrenLoading,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useEntityChildren(entity ? id : undefined);

  const children = childrenData?.pages.flatMap((p) => p.items) ?? [];

  if (isLoading) return <ProfileSkeleton />;

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-destructive/50 bg-destructive/10 px-5 py-4 text-sm text-destructive"
      >
        {error instanceof ProblemError
          ? (error.problem.detail ?? error.problem.title)
          : error.message}
      </div>
    );
  }

  if (!entity) {
    return (
      <div className="py-20 text-center text-muted-foreground">
        <p className="text-lg font-semibold">Entity not found.</p>
        <p className="mt-1 text-sm">
          This entity may have been removed or the ID is incorrect.
        </p>
        <Link
          href="/entities"
          className="mt-4 inline-block text-sm text-primary underline-offset-2 hover:underline"
        >
          Back to directory
        </Link>
      </div>
    );
  }

  const geo: string[] = [];
  if (entity.state) geo.push(entity.state);
  if (entity.region && entity.region !== entity.state) geo.push(entity.region);

  return (
    <div className="space-y-8">
      {/* Back link */}
      <nav aria-label="Breadcrumb">
        <Link
          href="/entities"
          className="text-sm text-muted-foreground hover:text-foreground hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        >
          &larr; Entity directory
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

      {/* Parent entity */}
      {entity.parent_id && (
        <section aria-labelledby="parent-heading">
          <h2 id="parent-heading" className="mb-3 text-xl font-semibold">
            Parent Entity
          </h2>
          <Link
            href={`/entities/${entity.parent_id}`}
            className="text-sm text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            View parent entity &rarr;
          </Link>
        </section>
      )}

      {/* Children */}
      <section aria-labelledby="children-heading">
        <h2 id="children-heading" className="mb-3 text-xl font-semibold">
          Sub-entities / Children
        </h2>

        {childrenLoading && (
          <ul aria-busy="true" className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <li key={i} className="h-16 animate-pulse rounded-lg border bg-muted" />
            ))}
          </ul>
        )}

        {!childrenLoading && children.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No sub-entities recorded.
          </p>
        )}

        {!childrenLoading && children.length > 0 && (
          <>
            <ul className="space-y-3" aria-label="Child entities">
              {children.map((child) => (
                <li key={child.id}>
                  <EntityCard entity={child} />
                </li>
              ))}
            </ul>

            {hasNextPage && (
              <div className="mt-4 flex justify-center">
                <button
                  type="button"
                  onClick={() => void fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
                >
                  {isFetchingNextPage ? "Loading…" : "Load more children"}
                </button>
              </div>
            )}
          </>
        )}
      </section>

      {/* Source citations */}
      {entity.source_urls.length > 0 && (
        <section aria-labelledby="sources-heading">
          <h2 id="sources-heading" className="mb-3 text-xl font-semibold">
            Source Citations
          </h2>
          <ul className="space-y-1">
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

      {/* Contacts (C4) — verified/stale indicator + cursor-paginated list */}
      <section aria-labelledby="contacts-heading">
        <h2 id="contacts-heading" className="mb-3 text-xl font-semibold">
          Contacts
        </h2>
        <ContactList entityId={id} />
      </section>
    </div>
  );
}
