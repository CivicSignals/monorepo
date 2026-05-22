// Step 2 — Segments: entity kinds (F2).
// Chip-toggle multi-select. Empty = all entity kinds (no filtering).

"use client";

import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type SegmentsStepValues, segmentsStepSchema } from "@/lib/icp-schemas";
import {
  ENTITY_KINDS,
  ENTITY_KIND_LABELS,
  type EntityKind,
} from "@/lib/icp-api";

// Brief description shown under each chip.
const ENTITY_KIND_DESCRIPTIONS: Record<EntityKind, string> = {
  k12_district: "Pre-K through 12th grade public school districts",
  community_college: "Two-year public colleges and vocational schools",
  university: "Four-year colleges and research universities",
  city: "Municipalities, towns, and city governments",
  county: "County governments and regional authorities",
  state_agency: "State-level departments and public agencies",
  special_district: "Special purpose districts (water, transit, fire, etc.)",
};

interface WizardSegmentsStepProps {
  defaultValues: SegmentsStepValues;
  onNext: (values: SegmentsStepValues) => void;
  onBack: () => void;
}

export function WizardSegmentsStep({
  defaultValues,
  onNext,
  onBack,
}: WizardSegmentsStepProps) {
  const {
    control,
    handleSubmit,
    formState: { errors },
  } = useForm<SegmentsStepValues>({
    resolver: zodResolver(segmentsStepSchema),
    defaultValues,
  });

  return (
    <form onSubmit={handleSubmit(onNext)} noValidate className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Who are you selling to?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Select the types of public-sector entities in your target market.
          Leave blank to include all entity types.
        </p>
      </div>

      <Controller
        control={control}
        name="entity_kinds"
        render={({ field }) => (
          <div
            role="group"
            aria-label="Entity types"
            className="grid gap-3 sm:grid-cols-2"
          >
            {ENTITY_KINDS.map((kind) => {
              const selected = field.value.includes(kind);
              return (
                <button
                  key={kind}
                  type="button"
                  role="checkbox"
                  aria-checked={selected}
                  onClick={() => {
                    const next = selected
                      ? field.value.filter((k) => k !== kind)
                      : [...field.value, kind];
                    field.onChange(next);
                  }}
                  className={[
                    "flex flex-col items-start rounded-lg border-2 p-4 text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                    selected
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/50 hover:bg-muted/50",
                  ].join(" ")}
                >
                  <span className="font-semibold text-sm">
                    {ENTITY_KIND_LABELS[kind]}
                  </span>
                  <span className="mt-1 text-xs text-muted-foreground">
                    {ENTITY_KIND_DESCRIPTIONS[kind]}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      />

      {errors.entity_kinds && (
        <p role="alert" className="text-sm text-destructive">
          {errors.entity_kinds.message}
        </p>
      )}

      {/* Helpful note when nothing selected */}
      <p className="text-xs text-muted-foreground">
        💡 No selection = signals from all entity types reach your feed.
      </p>

      <div className="flex justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-md border px-5 py-2 text-sm font-semibold text-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          ← Back
        </button>
        <button
          type="submit"
          className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Continue →
        </button>
      </div>
    </form>
  );
}
