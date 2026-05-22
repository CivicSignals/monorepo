// /s/[id] — Public signal page (P2).
//
// The signal analogue of the C5 public entity profile (/directory/[id]): a public,
// indexable, cite-sourced page for one global signal, with a "Claim this in a
// workspace" CTA.
//
// Route choice — why /s/[id], not /signals/[id]:
//   • /feed is the authenticated, workspace-scoped signal feed (G1), and the
//     signals-ui epic (G2-G5) will likely add an authenticated /signals/[id]
//     *detail* page. To keep the public page cleanly separated from any future
//     authenticated detail route (and to avoid a collision), this public page lives
//     under a short, clearly-public segment: /s/[id]. This mirrors how /directory/*
//     is the public twin of the authenticated /entities/*.
//
// Server component: signal + source citations are fetched server-side so crawlers
// see content without JS. Fully unauthenticated — the public signal read API
// requires no auth (doc 07 §3).
//
// generateMetadata provides per-page <title>, description, canonical, OG, Twitter.
//
// TODO P3: add JSON-LD structured data (e.g. schema.org/GovernmentService /
//          Article) once SEO basics land.
// TODO P4: add Crawl-delay / rate-limit guards to the fetchPublicSignal* helpers.

import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import {
  fetchPublicSignal,
  fetchPublicSignalSources,
  isPublicSignalType,
  type PublicSignalRead,
  type PublicSignalSource,
} from "@/lib/public-signals-api";
import { SIGNAL_TYPE_LABELS, type SignalType } from "@/lib/signals-api";

// ---- Revalidate at the page level (5 minutes). ----
// Must be a literal — Next.js static analysis cannot resolve imported constants.
export const revalidate = 300;

// ---- Dynamic params ----
interface PageProps {
  params: Promise<{ id: string }>;
}

// ---- Site URL constant ----
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

// ---- Helpers ----

/** Human label for a signal type slug (falls back to title-casing the slug). */
function signalTypeLabel(slug: string): string {
  return (
    SIGNAL_TYPE_LABELS[slug as SignalType] ??
    slug.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/** Format a UTC ISO string as a short human date (e.g. "May 22, 2026"). */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Best display name for the signal's subject entity. */
function subjectName(signal: PublicSignalRead): string | null {
  return signal.entity_name ?? null;
}

// ---- SEO metadata ----

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  const signal = await fetchPublicSignal(id);

  if (!signal) {
    return {
      title: "Signal Not Found | CivicSignals",
      description: "The requested public signal could not be found in CivicSignals.",
    };
  }

  const typeLabel = signalTypeLabel(signal.signal_type);
  const subject = subjectName(signal);
  const dateStr = signal.occurred_at
    ? formatDate(signal.occurred_at)
    : formatDate(signal.observed_at);

  const descriptionParts: string[] = [];
  // Summary leads the description (it's the human-written gist of the signal).
  if (signal.summary) descriptionParts.push(signal.summary);
  const context = [typeLabel, subject].filter(Boolean).join(" · ");
  descriptionParts.push(`${context} (${dateStr}).`);
  descriptionParts.push("Sourced from official public records.");
  const description = descriptionParts.join(" ").slice(0, 300);

  const canonicalUrl = `${SITE_URL}/s/${id}`;

  return {
    title: `${signal.title} | CivicSignals`,
    description,
    alternates: {
      canonical: canonicalUrl,
    },
    openGraph: {
      title: `${signal.title} | CivicSignals`,
      description,
      url: canonicalUrl,
      siteName: "CivicSignals",
      type: "article",
    },
    twitter: {
      card: "summary",
      title: `${signal.title} | CivicSignals`,
      description,
    },
  };
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

function SourceRow({ source }: { source: PublicSignalSource }) {
  return (
    <li className="flex flex-col gap-0.5">
      <a
        href={source.source_url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm text-primary underline-offset-2 hover:underline"
      >
        {source.source_url}
      </a>
      <span className="text-xs text-muted-foreground">
        {source.recipe_id}
        {source.fetched_at && ` · fetched ${formatDate(source.fetched_at)}`}
      </span>
    </li>
  );
}

// ---- Page component ----

export default async function PublicSignalPage({ params }: PageProps) {
  const { id } = await params;

  const signal = await fetchPublicSignal(id);
  if (!signal) notFound();

  // Public-surface gate (P2; doc 13 §4.1, §4.6): only late-stage public types are
  // publicly indexable. The server is authoritative (the /public endpoint 404s a
  // non-public type, so we'd already have notFound()'d above), but guard here too so
  // a paid-tier signal can never render on the public page even if the API contract
  // ever loosens.
  if (!isPublicSignalType(signal.signal_type)) notFound();

  // Source citations are non-fatal: if the sub-fetch fails, the page still renders
  // (the signal core is the primary content). Mirrors C5's parallel-fetch fallback.
  const sourcesResult = await fetchPublicSignalSources(id).catch(() => null);
  const sources = sourcesResult?.sources ?? [];

  const typeLabel = signalTypeLabel(signal.signal_type);
  const subject = subjectName(signal);
  const occurredStr = signal.occurred_at ? formatDate(signal.occurred_at) : null;
  const observedStr = formatDate(signal.observed_at);

  return (
    <main className="container py-8">
      <div className="space-y-8">
        {/* Breadcrumb */}
        <nav aria-label="Breadcrumb">
          <Link
            href="/directory"
            className="text-sm text-muted-foreground hover:text-foreground hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            &larr; CivicSignals public directory
          </Link>
        </nav>

        {/* Header */}
        <header className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{signal.title}</h1>
            <span className="shrink-0 rounded-full bg-muted px-2.5 py-0.5 text-sm font-medium text-muted-foreground">
              {typeLabel}
            </span>
          </div>
          <p className="text-muted-foreground">
            {[subject, occurredStr ? `Occurred ${occurredStr}` : null]
              .filter(Boolean)
              .join(" · ") || `Observed ${observedStr}`}
          </p>
        </header>

        {/* Summary */}
        <section aria-labelledby="summary-heading">
          <h2 id="summary-heading" className="mb-2 text-xl font-semibold">
            Summary
          </h2>
          <p className="text-sm leading-relaxed text-foreground">{signal.summary}</p>
        </section>

        {/* Overview */}
        <section aria-labelledby="overview-heading">
          <h2 id="overview-heading" className="mb-3 text-xl font-semibold">
            Details
          </h2>
          <dl className="space-y-2">
            <DetailRow label="Signal type" value={typeLabel} />
            {subject && <DetailRow label="Entity" value={subject} />}
            {occurredStr && <DetailRow label="Occurred" value={occurredStr} />}
            <DetailRow
              label="Observed"
              value={
                <time dateTime={signal.observed_at}>{observedStr}</time>
              }
            />
          </dl>
        </section>

        {/* Source citations (P2 req — prominent, mirrors C5 entity req 3) */}
        {sources.length > 0 && (
          <section aria-labelledby="sources-heading">
            <h2 id="sources-heading" className="mb-3 text-xl font-semibold">
              Source Citations
            </h2>
            <p className="mb-2 text-sm text-muted-foreground">
              This signal was extracted from the following official public sources:
            </p>
            <ul className="space-y-2" aria-label="Source citations">
              {sources.map((source) => (
                <SourceRow key={source.document_id} source={source} />
              ))}
            </ul>
          </section>
        )}

        {/* "Claim this in a workspace" CTA (P2 req) */}
        <div className="rounded-lg border bg-card p-6">
          <h2 className="text-lg font-semibold">Claim this in a workspace</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Track this signal, score it against your Ideal Customer Profile, add
            contacts, and push it to your CRM — sign up to claim it in a CivicSignals
            workspace.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link
              href="/signup"
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Claim this in a workspace
            </Link>
            <Link
              href="/login"
              className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted"
            >
              Sign in
            </Link>
          </div>
        </div>

        {/* TODO P3: JSON-LD structured data for the signal. */}
      </div>
    </main>
  );
}
