// ContactCard — a single contact row with name, title/role, email, a
// verified/stale/bounced/invalid badge, and a "Report incorrect" affordance (C6).
//
// Provenance is shown (source name + URL) when available, per doc 16 §18.
// C6 adds:
// - "Report incorrect" button that opens an inline correction form.
// - The form has a kind selector + optional reason text field.
// - On submit it calls the useReportContactInvalid mutation and shows a toast.
// - The badge reflects "Bounced" or "Invalid" status from the server.
//
// Auth note: the report form needs a bearer token + workspace id. These are
// passed as optional props; if omitted the report button is hidden (read-only
// contexts such as the public directory).

"use client";

import { useState } from "react";
import { type ContactRead, getVerificationStatus } from "@/lib/contacts-api";
import { type CorrectionKind } from "@/lib/contacts-api";
import { useReportContactInvalid } from "@/hooks/use-contacts";

// ---- Badge ----

function VerificationBadge({
  contact,
}: {
  contact: Pick<ContactRead, "verified" | "last_verified_at" | "status">;
}) {
  const status = getVerificationStatus(contact);

  const styles: Record<typeof status, string> = {
    Verified:
      "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    Stale:
      "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    Bounced: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    Invalid:
      "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400",
  };

  return (
    <span
      data-testid="verification-badge"
      aria-label={status}
      className={[
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        styles[status],
      ].join(" ")}
    >
      {status}
    </span>
  );
}

// ---- Correction form ----

const KIND_LABELS: Record<CorrectionKind, string> = {
  bounced: "Email bounced",
  wrong_email: "Wrong email address",
  wrong_phone: "Wrong phone number",
  wrong_person: "Wrong person / name",
  other: "Other",
};

interface CorrectionFormProps {
  contactId: string;
  entityId?: string;
  accessToken: string;
  workspaceId: string;
  onClose: () => void;
}

function CorrectionForm({
  contactId,
  entityId,
  accessToken,
  workspaceId,
  onClose,
}: CorrectionFormProps) {
  const [kind, setKind] = useState<CorrectionKind>("other");
  const [reason, setReason] = useState("");
  const mutation = useReportContactInvalid({ entityId });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    mutation.mutate(
      {
        contactId,
        body: { kind, reason: reason || null },
        accessToken,
        workspaceId,
      },
      {
        onSuccess: () => {
          onClose();
        },
      },
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Report incorrect contact"
      className="mt-3 rounded-md border border-border bg-muted/30 p-3 text-sm"
    >
      <p className="mb-2 font-medium text-foreground">Report incorrect data</p>

      <label className="block">
        <span className="mb-1 block text-xs text-muted-foreground">
          What is wrong?
        </span>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as CorrectionKind)}
          className="w-full rounded border border-input bg-background px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
          data-testid="correction-kind-select"
        >
          {(Object.entries(KIND_LABELS) as [CorrectionKind, string][]).map(
            ([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ),
          )}
        </select>
      </label>

      <label className="mt-2 block">
        <span className="mb-1 block text-xs text-muted-foreground">
          Reason (optional)
        </span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={2000}
          rows={2}
          placeholder="Describe the problem…"
          className="w-full rounded border border-input bg-background px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
          data-testid="correction-reason-input"
        />
      </label>

      {mutation.isError && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {mutation.error instanceof Error
            ? mutation.error.message
            : "Failed to submit report. Please try again."}
        </p>
      )}

      <div className="mt-2 flex gap-2">
        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded bg-destructive px-3 py-1 text-xs font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-60"
          data-testid="correction-submit"
        >
          {mutation.isPending ? "Submitting…" : "Submit report"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-border px-3 py-1 text-xs text-muted-foreground hover:bg-muted"
          data-testid="correction-cancel"
        >
          Cancel
        </button>
      </div>

      {mutation.isSuccess && (
        <p className="mt-1 text-xs text-green-700 dark:text-green-400">
          Report submitted. Thank you!
        </p>
      )}
    </form>
  );
}

// ---- ContactCard ----

export interface ContactCardProps {
  contact: ContactRead;
  /** Pass auth context to enable the "Report incorrect" affordance (C6). */
  authContext?: {
    accessToken: string;
    workspaceId: string;
    entityId?: string;
  };
}

export function ContactCard({ contact, authContext }: ContactCardProps) {
  const [showForm, setShowForm] = useState(false);
  const displayEmail = contact.canonical_email;
  const hasSource = !!(contact.source || contact.source_url);
  const isBadStatus =
    contact.status === "bounced" || contact.status === "invalid";

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

        <div className="flex items-center gap-2">
          {/* Status badge (if not active, show it) */}
          {contact.status !== "active" && !isBadStatus && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground capitalize">
              {contact.status}
            </span>
          )}

          {/* Report incorrect affordance — only when auth context is provided (C6) */}
          {authContext && !showForm && (
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              data-testid="report-incorrect-button"
              aria-label="Report this contact as incorrect"
            >
              Report incorrect
            </button>
          )}
        </div>
      </div>

      {/* Title / Department — use || so empty strings fall through to the other field */}
      {(contact.title || contact.department) && (
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

      {/* Inline correction form (C6) */}
      {showForm && authContext && (
        <CorrectionForm
          contactId={contact.id}
          entityId={authContext.entityId}
          accessToken={authContext.accessToken}
          workspaceId={authContext.workspaceId}
          onClose={() => setShowForm(false)}
        />
      )}
    </article>
  );
}
