// SignalDetail — client island for the /signals/[id] detail page (G2).
//
// Shows one signal in full: header (title, type badge, workspace score band),
// summary, extracted fields, source documents, suggested contacts, related
// signals, and an inspect panel (raw score breakdown + details JSON). Data is
// loaded via useSignalDetail (TanStack Query) — workspace-scoped, server state
// never touches Zustand (doc 06 §2).
//
// Seams:
// - TODO F4: a richer "Why this signal?" panel renders human-readable bullets
//   from score_breakdown; this view shows the structured breakdown raw for now.
// - TODO G4: per-signal status transitions (dismiss / pin / push) attach to the
//   header action area.
// - TODO G5: polished loading skeleton / empty / error states.

"use client";

import Link from "next/link";
import {
  SIGNAL_TYPE_LABELS,
  type SignalType,
  type SourceDocumentRead,
  type SuggestedContactRead,
  type RelatedSignalRead,
} from "@/lib/signals-api";
import { useSignalDetail } from "@/hooks/use-signals";
import { ProblemError } from "@/lib/auth-api";

// ---- Helpers ----------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function signalTypeLabel(type: string): string {
  return SIGNAL_TYPE_LABELS[type as SignalType] ?? type;
}

function scoreBand(score: number): { label: string; className: string } {
  if (score >= 80) return { label: "High", className: "bg-green-100 text-green-800" };
  if (score >= 50) return { label: "Medium", className: "bg-yellow-100 text-yellow-800" };
  return { label: "Low", className: "bg-gray-100 text-gray-600" };
}

/** Humanise a snake_case field key for display. */
function labelify(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Render a scalar field value; objects/arrays are JSON-stringified. */
function renderFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

// ---- Sub-components ---------------------------------------------------------

function DetailSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading signal" data-testid="signal-loading">
      <div className="mb-4 h-8 w-2/3 animate-pulse rounded-md bg-muted" />
      <div className="mb-2 h-4 w-1/3 animate-pulse rounded bg-muted" />
      <div className="mb-6 h-4 w-1/2 animate-pulse rounded bg-muted" />
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-5 animate-pulse rounded bg-muted" />
        ))}
      </div>
    </div>
  );
}

function SourceDocuments({ docs }: { docs: SourceDocumentRead[] }) {
  return (
    <section aria-labelledby="sources-heading" data-testid="signal-sources">
      <h2 id="sources-heading" className="mb-3 text-xl font-semibold">
        Source Documents
      </h2>
      {docs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No source documents recorded.</p>
      ) : (
        <ul className="space-y-2">
          {docs.map((doc) => (
            <li
              key={doc.raw_document_id}
              data-testid="source-doc"
              className="rounded-lg border border-gray-200 p-3 text-sm"
            >
              {doc.missing ? (
                <span className="text-gray-400 italic" data-testid="source-doc-missing">
                  Source document no longer available ({doc.raw_document_id})
                </span>
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    {doc.source_url ? (
                      <a
                        href={doc.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary underline-offset-2 hover:underline break-all"
                      >
                        {doc.source_url}
                      </a>
                    ) : (
                      <span className="text-gray-500">Document {doc.raw_document_id}</span>
                    )}
                    <div className="text-xs text-gray-400 mt-0.5">
                      {[doc.recipe_id, doc.content_type, formatDate(doc.fetched_at)]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SuggestedContacts({ contacts }: { contacts: SuggestedContactRead[] }) {
  return (
    <section aria-labelledby="contacts-heading" data-testid="signal-contacts">
      <h2 id="contacts-heading" className="mb-3 text-xl font-semibold">
        Suggested Contacts
      </h2>
      {contacts.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No contacts recorded for this entity yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {contacts.map((c) => (
            <li
              key={c.contact_id}
              data-testid="suggested-contact"
              className="rounded-lg border border-gray-200 p-3 text-sm"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-gray-900">{c.name}</span>
                {c.verified && (
                  <span className="text-xs text-green-700 bg-green-100 px-1.5 py-0.5 rounded-full">
                    Verified
                  </span>
                )}
              </div>
              {(c.title || c.department) && (
                <div className="text-xs text-gray-500">
                  {[c.title, c.department].filter(Boolean).join(" · ")}
                </div>
              )}
              {c.canonical_email && (
                <a
                  href={`mailto:${c.canonical_email}`}
                  className="text-xs text-primary underline-offset-2 hover:underline"
                >
                  {c.canonical_email}
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RelatedSignals({ related }: { related: RelatedSignalRead[] }) {
  return (
    <section aria-labelledby="related-heading" data-testid="signal-related">
      <h2 id="related-heading" className="mb-3 text-xl font-semibold">
        Related Signals
      </h2>
      {related.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No other signals for this entity.
        </p>
      ) : (
        <ul className="space-y-2">
          {related.map(({ signal }) => (
            <li key={signal.id} data-testid="related-signal">
              <Link
                href={`/signals/${signal.id}`}
                className="block rounded-lg border border-gray-200 p-3 text-sm hover:border-gray-300 hover:shadow-sm transition-all"
              >
                <span className="text-xs font-medium text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
                  {signalTypeLabel(signal.signal_type)}
                </span>
                <span className="ml-2 font-medium text-gray-900">{signal.title}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---- Main component ---------------------------------------------------------

interface SignalDetailProps {
  id: string;
}

export function SignalDetail({ id }: SignalDetailProps) {
  const { data, isLoading, error } = useSignalDetail(id);

  if (isLoading) return <DetailSkeleton />;

  if (error) {
    const message =
      error instanceof ProblemError
        ? (error.problem.detail ?? error.problem.title)
        : error.message;
    // A 404 problem reads as "not found"; surface it as a friendly empty state.
    const isNotFound =
      error instanceof ProblemError && error.problem.status === 404;
    return isNotFound ? (
      <div className="py-20 text-center text-muted-foreground" data-testid="signal-not-found">
        <p className="text-lg font-semibold">Signal not found.</p>
        <p className="mt-1 text-sm">This signal may have been merged or removed.</p>
        <Link
          href="/feed"
          className="mt-4 inline-block text-sm text-primary underline-offset-2 hover:underline"
        >
          Back to feed
        </Link>
      </div>
    ) : (
      <div
        role="alert"
        data-testid="signal-error"
        className="rounded-lg border border-destructive/50 bg-destructive/10 px-5 py-4 text-sm text-destructive"
      >
        {message}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="py-20 text-center text-muted-foreground" data-testid="signal-not-found">
        <p className="text-lg font-semibold">Signal not found.</p>
        <Link
          href="/feed"
          className="mt-4 inline-block text-sm text-primary underline-offset-2 hover:underline"
        >
          Back to feed
        </Link>
      </div>
    );
  }

  const { signal } = data;
  const fieldEntries = Object.entries(data.extracted_fields).filter(
    // title/summary/signal_type are shown in the header already.
    ([k]) => !["title", "summary", "signal_type"].includes(k),
  );

  return (
    <div className="space-y-8" data-testid="signal-detail">
      {/* Back link */}
      <nav aria-label="Breadcrumb">
        <Link
          href="/feed"
          className="text-sm text-muted-foreground hover:text-foreground hover:underline"
        >
          &larr; Signal feed
        </Link>
      </nav>

      {/* Header */}
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs font-medium text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
            {signalTypeLabel(signal.signal_type)}
          </span>
          {data.score !== null && (
            <span
              data-testid="signal-score"
              className={`text-xs font-semibold px-2 py-0.5 rounded-full ${scoreBand(data.score).className}`}
            >
              {scoreBand(data.score).label} · {data.score.toFixed(0)}/100
            </span>
          )}
          {data.status && data.status !== "new" && (
            <span className="text-xs text-gray-500 capitalize">{data.status}</span>
          )}
        </div>
        <h1 className="text-3xl font-bold tracking-tight" data-testid="signal-title">
          {signal.title}
        </h1>
        <p className="text-muted-foreground">
          {[data.entity_name, formatDate(signal.occurred_at)].filter(Boolean).join(" · ")}
        </p>
        {data.entity_id && (
          <Link
            href={`/entities/${data.entity_id}`}
            className="inline-block text-sm text-primary underline-offset-2 hover:underline"
          >
            View entity profile &rarr;
          </Link>
        )}
      </header>

      {/* Summary */}
      <section aria-labelledby="summary-heading">
        <h2 id="summary-heading" className="mb-2 text-xl font-semibold">
          Summary
        </h2>
        <p className="text-sm text-gray-700">{signal.summary}</p>
      </section>

      {/* Extracted fields */}
      <section aria-labelledby="fields-heading" data-testid="signal-fields">
        <h2 id="fields-heading" className="mb-3 text-xl font-semibold">
          Extracted Fields
        </h2>
        {fieldEntries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No additional fields extracted.</p>
        ) : (
          <dl className="space-y-2">
            {fieldEntries.map(([key, value]) => (
              <div key={key} className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
                <dt className="w-44 shrink-0 text-sm font-medium text-muted-foreground">
                  {labelify(key)}
                </dt>
                <dd className="text-sm text-foreground break-words">
                  {renderFieldValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      <SourceDocuments docs={data.source_documents} />
      <SuggestedContacts contacts={data.suggested_contacts} />
      <RelatedSignals related={data.related_signals} />

      {/* Inspect panel — raw score breakdown + details JSON (debug/transparency).
          TODO F4: replace the raw breakdown with human-readable "Why this signal?" bullets. */}
      <section aria-labelledby="inspect-heading" data-testid="signal-inspect">
        <h2 id="inspect-heading" className="mb-3 text-xl font-semibold">
          Inspect
        </h2>
        <details className="rounded-lg border border-gray-200 p-3">
          <summary className="cursor-pointer text-sm font-medium text-gray-700">
            Score breakdown &amp; raw payload
          </summary>
          <div className="mt-3 space-y-3">
            {data.matched_keywords.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Matched keywords
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {data.matched_keywords.map((kw) => (
                    <span
                      key={kw}
                      className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded-full"
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Score breakdown</p>
              <pre
                data-testid="inspect-breakdown"
                className="overflow-x-auto rounded bg-gray-50 p-2 text-[11px] text-gray-700"
              >
                {JSON.stringify(data.score_breakdown ?? {}, null, 2)}
              </pre>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Raw details</p>
              <pre className="overflow-x-auto rounded bg-gray-50 p-2 text-[11px] text-gray-700">
                {JSON.stringify(signal.details, null, 2)}
              </pre>
            </div>
          </div>
        </details>
      </section>
    </div>
  );
}
