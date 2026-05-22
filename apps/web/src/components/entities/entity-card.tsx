// EntityCard — a compact row/card for the entity directory list (C3).

import Link from "next/link";
import type { EntityRead } from "@/lib/entities-api";

interface EntityCardProps {
  entity: EntityRead;
}

/** Format a USD dollar amount into a human-readable short form (e.g. $312M). */
function formatBudget(usd: number): string {
  if (usd >= 1_000_000_000) return `$${(usd / 1_000_000_000).toFixed(1)}B`;
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(0)}M`;
  if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}

/** Format a number with locale commas. */
function formatNum(n: number): string {
  return n.toLocaleString("en-US");
}

/** Capitalise a slug/type string for display. */
function labelify(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Status badge colour mapping. */
function statusClass(status: string): string {
  if (status === "active")
    return "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400";
  if (status === "dissolved")
    return "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400";
  return "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400";
}

export function EntityCard({ entity }: EntityCardProps) {
  const geo: string[] = [];
  if (entity.state) geo.push(entity.state);
  if (entity.region && entity.region !== entity.state) geo.push(entity.region);

  const meta: string[] = [];
  if (entity.enrollment != null) meta.push(`${formatNum(entity.enrollment)} enrolled`);
  else if (entity.population != null) meta.push(`${formatNum(entity.population)} residents`);
  if (entity.annual_budget_usd != null)
    meta.push(`Budget ${formatBudget(entity.annual_budget_usd)}`);

  return (
    <article
      className="group flex flex-col gap-1 rounded-lg border bg-card px-5 py-4 shadow-sm transition-shadow hover:shadow-md focus-within:ring-2 focus-within:ring-ring"
      aria-label={entity.name}
    >
      <div className="flex items-start justify-between gap-3">
        <Link
          href={`/entities/${entity.id}`}
          className="text-base font-semibold leading-snug text-foreground hover:underline focus-visible:outline-none focus-visible:underline"
        >
          {entity.name}
        </Link>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${statusClass(entity.status)}`}
        >
          {labelify(entity.status)}
        </span>
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

      {entity.primary_website && (
        <a
          href={entity.primary_website}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-0.5 w-fit truncate text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:underline"
          aria-label={`${entity.name} website`}
        >
          {entity.primary_website.replace(/^https?:\/\//, "")}
        </a>
      )}
    </article>
  );
}
