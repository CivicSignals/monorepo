// Step 6 — Review & confirm (F2).
// Shows a summary of all collected ICP dimensions.
// On confirm: POST /icp (or PATCH /icp/{id} in edit mode) then activate.

"use client";

import type { WizardDraft } from "@/store/icp-wizard";
import { ENTITY_KIND_LABELS, SIGNAL_TYPE_LABELS } from "@/lib/icp-api";
import { ProblemError } from "@/lib/auth-api";

interface WizardReviewStepProps {
  draft: WizardDraft;
  editingId: string | null;
  onSubmit: () => Promise<void>;
  onBack: () => void;
  isSubmitting: boolean;
  submitError: Error | null;
}

/** Format dollars from cents or return "—". */
function fmt$cents(cents: number | null): string {
  if (cents === null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

function fmtList(items: string[], emptyLabel: string): string {
  return items.length === 0 ? emptyLabel : items.join(", ");
}

interface RowProps {
  label: string;
  value: React.ReactNode;
}

function Row({ label, value }: RowProps) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
      <dt className="w-40 shrink-0 text-sm font-medium text-muted-foreground">{label}</dt>
      <dd className="text-sm">{value}</dd>
    </div>
  );
}

export function WizardReviewStep({
  draft,
  editingId,
  onSubmit,
  onBack,
  isSubmitting,
  submitError,
}: WizardReviewStepProps) {
  const entityKindLabels =
    draft.entity_kinds.length === 0
      ? "All entity types"
      : draft.entity_kinds.map((k) => ENTITY_KIND_LABELS[k]).join(", ");

  const signalTypeLabels =
    draft.signal_types.length === 0
      ? "None selected"
      : draft.signal_types.map((t) => SIGNAL_TYPE_LABELS[t]).join(", ");

  const statesLabel =
    draft.states.length === 0 ? "All US states" : draft.states.join(", ");

  const apiError =
    submitError instanceof ProblemError
      ? submitError.problem.detail ?? submitError.message
      : submitError?.message;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Review your ICP</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {editingId
            ? "Confirm the changes to your ideal customer profile."
            : "Confirm and save your ideal customer profile. CivicSignals will use this to score and filter your signal feed."}
        </p>
      </div>

      <div className="rounded-lg border bg-muted/30 p-5">
        <dl className="space-y-3">
          <Row label="Name" value={<strong>{draft.name}</strong>} />
          <Row label="Countries" value={fmtList(draft.countries, "All")} />
          <Row label="US states" value={statesLabel} />
          <Row label="Entity types" value={entityKindLabels} />
          <Row
            label="Size (min)"
            value={draft.min_size !== null ? draft.min_size.toLocaleString() : "—"}
          />
          <Row
            label="Size (max)"
            value={draft.max_size !== null ? draft.max_size.toLocaleString() : "—"}
          />
          <Row label="Signal types" value={signalTypeLabels} />
          {draft.signal_types.length > 0 && (
            <Row
              label="Signal weights"
              value={
                <ul className="space-y-0.5">
                  {draft.signal_types.map((t) => (
                    <li key={t} className="flex gap-2">
                      <span>{SIGNAL_TYPE_LABELS[t]}</span>
                      <span className="text-muted-foreground">
                        {Math.round((draft.signal_weights[t] ?? 1) * 100)}%
                      </span>
                    </li>
                  ))}
                </ul>
              }
            />
          )}
          <Row label="Deal min" value={fmt$cents(draft.deal_band_min_cents)} />
          <Row label="Deal max" value={fmt$cents(draft.deal_band_max_cents)} />
          <Row
            label="Required keywords"
            value={fmtList(draft.keywords_required, "None")}
          />
          <Row
            label="Excluded keywords"
            value={fmtList(draft.keywords_excluded, "None")}
          />
          <Row
            label="Score threshold"
            value={`${draft.threshold} / 100`}
          />
        </dl>
      </div>

      {apiError && (
        <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {apiError}
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        {editingId
          ? "Saving will update and re-activate this ICP for your workspace."
          : "Saving will create and activate this ICP. You can edit it anytime in Settings → ICP."}
      </p>

      <div className="flex justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          disabled={isSubmitting}
          className="rounded-md border px-5 py-2 text-sm font-semibold text-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60"
        >
          ← Back
        </button>
        <button
          type="button"
          onClick={onSubmit}
          disabled={isSubmitting}
          className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
        >
          {isSubmitting
            ? editingId
              ? "Saving…"
              : "Creating ICP…"
            : editingId
              ? "Save changes"
              : "Create & activate ICP"}
        </button>
      </div>
    </div>
  );
}
