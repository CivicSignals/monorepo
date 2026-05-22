// ContactCard — a single contact row with name, title/role, email, and a
// verified/stale badge derived from provenance fields (C4).
//
// Provenance is also shown (source name + URL) when available, per doc 16 §18.

"use client";

import { type ContactRead, getVerificationStatus } from "@/lib/contacts-api";

// ---- Badge ----

function VerificationBadge({
  contact,
}: {
  contact: Pick<ContactRead, "verified" | "last_verified_at">;
}) {
  const status = getVerificationStatus(contact);
  const isVerified = status === "Verified";

  return (
    <span
      data-testid="verification-badge"
      aria-label={isVerified ? "Verified" : "Stale"}
      className={[
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        isVerified
          ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
          : "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
      ].join(" ")}
    >
      {isVerified ? "Verified" : "Stale"}
    </span>
  );
}

// ---- ContactCard ----

export interface ContactCardProps {
  contact: ContactRead;
}

export function ContactCard({ contact }: ContactCardProps) {
  const displayEmail = contact.canonical_email;
  const hasSource = !!(contact.source || contact.source_url);

  return (
    <article
      data-testid="contact-card"
      className="rounded-lg border border-border bg-card px-4 py-3 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        {/* Name + verification badge */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">
            {contact.name}
          </span>
          <VerificationBadge contact={contact} />
        </div>

        {/* Status badge (if not active, show it) */}
        {contact.status !== "active" && (
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground capitalize">
            {contact.status}
          </span>
        )}
      </div>

      {/* Title / Department */}
      {(contact.title ?? contact.department) && (
        <p className="mt-0.5 text-xs text-muted-foreground">
          {[contact.title, contact.department].filter(Boolean).join(" · ")}
        </p>
      )}

      {/* Email */}
      {displayEmail && (
        <p className="mt-1 text-xs">
          <a
            href={`mailto:${displayEmail}`}
            className="text-primary underline-offset-2 hover:underline"
          >
            {displayEmail}
          </a>
        </p>
      )}

      {/* Provenance */}
      {hasSource && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          Source:{" "}
          {contact.source_url ? (
            <a
              href={contact.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="underline-offset-2 hover:underline"
            >
              {contact.source ?? contact.source_url}
            </a>
          ) : (
            <span>{contact.source}</span>
          )}
          {contact.last_verified_at && (
            <>
              {" "}
              &mdash; last verified{" "}
              {new Date(contact.last_verified_at).toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
            </>
          )}
        </p>
      )}
    </article>
  );
}
