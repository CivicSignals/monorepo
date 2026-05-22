// /directory/[id] — Public entity profile page (C5 scaffold, P1 polish).
//
// P1 adds over C5:
//   1. Richer entity profile: hierarchy with parent link, public contacts
//      section (name, title, department, source attribution per-contact).
//   2. Recent signals teaser (public, unauthenticated, 3-signal preview).
//   3. Stronger SEO: Twitter card, more complete OG, short_name display.
//   4. Parallel server-side data fetching (entity + children + contacts + signals).
//
// Server component: all data fetched server-side (no client-only data fetch for
// core content). Fully unauthenticated — the public API requires no auth.
//
// TODO P2: link signal teasers to public signal pages once G1+P2 ship.
// TODO P3: add JSON-LD Organization/GovernmentOrganization structured data.
// TODO P4: add Crawl-delay / rate-limit guards to fetchPublicEntity* helpers.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import {
  fetchPublicEntity,
  fetchPublicEntityChildren,
  fetchPublicEntityContacts,
  fetchPublicEntitySignals,
  type PublicContactRead,
  type PublicSignalRead,
} from "@/lib/public-entities-api";
import type { EntityRead } from "@/lib/entities-api";

// ---- Revalidate at the page level (5 minutes). ----
// Must be a literal — Next.js static analysis cannot resolve imported constants.
export const revalidate = 300;

// ---- Dynamic params ----
interface PageProps {
  params: Promise<{ id: string }>;
}

// ---- Site URL constant ----
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

// ---- SEO metadata (P1 req 4: title/description/canonical + OG + Twitter card) ----

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  const entity = await fetchPublicEntity(id);

  if (!entity) {
    return {
      title: "Entity Not Found | CivicSignals",
      description: "The requested public entity could not be found in the CivicSignals directory.",
    };
  }

  const geo = [entity.state, entity.region]
    .filter(Boolean)
    .filter((v, i, arr) => arr.indexOf(v) === i)
    .join(", ");

  const typeLabel = labelify(entity.type);

  const descriptionParts: string[] = [
    `${typeLabel} in ${geo || entity.country}.`,
  ];
  if (entity.enrollment != null)
    descriptionParts.push(`${entity.enrollment.toLocaleString("en-US")} students enrolled.`);
  else if (entity.population != null)
    descriptionParts.push(`Population: ${entity.population.toLocaleString("en-US")}.`);
  if (entity.annual_budget_usd != null)
    descriptionParts.push(`Annual budget ${formatBudget(entity.annual_budget_usd)}.`);
  descriptionParts.push("Data sourced from official public records.");

  const description = descriptionParts.join(" ");
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
    // P1 req 4: Twitter card (X) for social sharing.
    twitter: {
      card: "summary",
      title: `${entity.name} | CivicSignals`,
      description,
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

/** Format a UTC ISO string as a short human date (e.g. "May 22, 2026"). */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ---- Sub-components ----

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
      <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className="text-sm text-foreground">{value}</dd>
    </div>
  );
}

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

// P1 req 2 & 3: Contact row with per-contact source attribution.
function ContactRow({ contact }: { contact: PublicContactRead }) {
  return (
    <li className="flex flex-col gap-0.5 rounded-lg border bg-card px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-foreground">{contact.name}</p>
          {(contact.title || contact.department) && (
            <p className="text-xs text-muted-foreground">
              {[contact.title, contact.department].filter(Boolean).join(" · ")}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {contact.verified && (
            <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
              Verified
            </span>
          )}
          {/* P1 req 3: per-contact source citation link */}
          {contact.source_url ? (
            <a
              href={contact.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              aria-label={`Source for ${contact.name}${contact.source ? `: ${contact.source}` : ""}`}
            >
              {contact.source ?? "Source"}
            </a>
          ) : contact.source ? (
            <span className="text-xs text-muted-foreground">{contact.source}</span>
          ) : null}
        </div>
      </div>
    </li>
  );
}

// P1 req 2: Recent signal teaser (public, unauthenticated).
// TODO P2: link signal titles to full public signal pages.
function SignalTeaserRow({ signal }: { signal: PublicSignalRead }) {
  const typeLabel = labelify(signal.signal_type);
  const dateStr = signal.occurred_at
    ? formatDate(signal.occurred_at)
    : formatDate(signal.observed_at);
  return (
    <li className="flex flex-col gap-0.5 rounded-lg border bg-card px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground line-clamp-2">{signal.title}</p>
          <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{signal.summary}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
            {typeLabel}
          </span>
          <time
            dateTime={signal.occurred_at ?? signal.observed_at}
            className="text-xs text-muted-foreground"
          >
            {dateStr}
          </time>
        </div>
      </div>
    </li>
  );
}

// ---- Page component ----

export default async function PublicEntityProfilePage({ params }: PageProps) {
  const { id } = await params;

  const entity = await fetchPublicEntity(id);
  if (!entity) notFound();

  // Fetch children, contacts, and signals server-side — all non-fatal fallbacks.
  // Run in parallel for minimal latency (P1: parallel fetch pattern).
  const [childrenResult, contactsResult, signalsResult] = await Promise.allSettled([
    fetchPublicEntityChildren(id, { limit: 10 }),
    fetchPublicEntityContacts(id, { limit: 5 }),
    fetchPublicEntitySignals(id, { limit: 3 }),
  ]);

  const children =
    childrenResult.status === "fulfilled" ? childrenResult.value.items : [];
  const childrenNextCursor =
    childrenResult.status === "fulfilled" ? childrenResult.value.next_cursor : null;
  const contacts =
    contactsResult.status === "fulfilled" ? contactsResult.value.items : [];
  const contactsNextCursor =
    contactsResult.status === "fulfilled" ? contactsResult.value.next_cursor : null;
  const signals =
    signalsResult.status === "fulfilled" ? signalsResult.value.items : [];
  const signalsNextCursor =
    signalsResult.status === "fulfilled" ? signalsResult.value.next_cursor : null;

  const geo = [entity.state, entity.region]
    .filter(Boolean)
    .filter((v, i, arr) => arr.indexOf(v) === i);

  return (
    <main className="container py-8">
      <div className="space-y-8">
        {/* Breadcrumb */}
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
            {[labelify(entity.type), ...geo, entity.country].join(" · ")}
          </p>
        </header>

        {/* Overview */}
        <section aria-labelledby="overview-heading">
          <h2 id="overview-heading" className="mb-3 text-xl font-semibold">
            Overview
          </h2>
          <dl className="space-y-2">
            {entity.short_name && entity.short_name !== entity.name && (
              <DetailRow label="Also known as" value={entity.short_name} />
            )}
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

        {/* Hierarchy — Parent entity (P1 req 2) */}
        {entity.parent_id && (
          <section aria-labelledby="parent-heading">
            <h2 id="parent-heading" className="mb-3 text-xl font-semibold">
              Part of
            </h2>
            <Link
              href={`/directory/${entity.parent_id}`}
              className="inline-flex items-center gap-1 text-sm text-primary underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
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
                More sub-entities available.{" "}
                <Link href="/signup" className="text-primary underline-offset-2 hover:underline">
                  Sign in
                </Link>{" "}
                to browse the full list.
              </p>
            )}
          </section>
        )}

        {/* Public contacts (P1 req 2 & 3 — with per-contact source attribution) */}
        {contacts.length > 0 && (
          <section aria-labelledby="contacts-heading">
            <h2 id="contacts-heading" className="mb-1 text-xl font-semibold">
              Key Contacts
            </h2>
            <p className="mb-3 text-sm text-muted-foreground">
              Publicly listed contacts sourced from official directories.
            </p>
            <ul className="space-y-2" aria-label="Public contacts">
              {contacts.map((contact) => (
                <ContactRow key={contact.id} contact={contact} />
              ))}
            </ul>
            {contactsNextCursor && (
              <p className="mt-3 text-sm text-muted-foreground">
                More contacts available.{" "}
                <Link href="/signup" className="text-primary underline-offset-2 hover:underline">
                  Sign in
                </Link>{" "}
                to view the full directory.
              </p>
            )}
          </section>
        )}

        {/* Recent signals teaser (P1 req 2 — public signal preview) */}
        {signals.length > 0 && (
          <section aria-labelledby="signals-heading">
            <h2 id="signals-heading" className="mb-1 text-xl font-semibold">
              Recent Activity
            </h2>
            <p className="mb-3 text-sm text-muted-foreground">
              Public procurement and policy signals detected for this entity.
            </p>
            <ul className="space-y-2" aria-label="Recent signals">
              {signals.map((signal) => (
                <SignalTeaserRow key={signal.id} signal={signal} />
              ))}
            </ul>
            {signalsNextCursor && (
              <p className="mt-3 text-sm text-muted-foreground">
                {/* TODO P2: link to public signal list page when G1+P2 ships */}
                More activity available.{" "}
                <Link href="/signup" className="text-primary underline-offset-2 hover:underline">
                  Sign in
                </Link>{" "}
                for the full signal feed.
              </p>
            )}
          </section>
        )}

        {/* Source citations (P1 req 3 — prominent, required by C5 req 3) */}
        {entity.source_urls.length > 0 && (
          <section aria-labelledby="sources-heading">
            <h2 id="sources-heading" className="mb-3 text-xl font-semibold">
              Source Citations
            </h2>
            <p className="mb-2 text-sm text-muted-foreground">
              Data on this page is derived from the following official public sources:
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
