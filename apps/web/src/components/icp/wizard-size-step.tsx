// Step 3 — Size band: min/max size + deal band (F2).
// "Size" is an entity-level metric (enrollment for K-12, population for cities, etc.).
// Deal band is the estimated contract value range (stored as cents for precision).

"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type SizeStepValues, sizeStepSchema } from "@/lib/icp-schemas";

interface WizardSizeStepProps {
  defaultValues: SizeStepValues;
  onNext: (values: SizeStepValues) => void;
  onBack: () => void;
}

const SIZE_PRESETS: { label: string; min: number | null; max: number | null }[] = [
  { label: "Any size", min: null, max: null },
  { label: "Small (< 1,000)", min: null, max: 1000 },
  { label: "Mid (1k – 10k)", min: 1000, max: 10000 },
  { label: "Large (10k – 50k)", min: 10000, max: 50000 },
  { label: "Very large (50k+)", min: 50000, max: null },
];

const DEAL_PRESETS: { label: string; min: number | null; max: number | null }[] = [
  { label: "Any", min: null, max: null },
  { label: "< $100k", min: null, max: 10_000_00 },
  { label: "$100k – $500k", min: 10_000_00, max: 50_000_00 },
  { label: "$500k – $2M", min: 50_000_00, max: 200_000_00 },
  { label: "$2M+", min: 200_000_00, max: null },
];

/** Convert dollars (string/number) to cents or null. */
function dollarsToCents(value: string): number | null {
  const n = parseFloat(value);
  if (isNaN(n)) return null;
  return Math.round(n * 100);
}

/** Convert cents to a dollar string for display in the input. */
function centsToDollars(cents: number | null): string {
  if (cents === null) return "";
  return (cents / 100).toString();
}

export function WizardSizeStep({
  defaultValues,
  onNext,
  onBack,
}: WizardSizeStepProps) {
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<SizeStepValues>({
    resolver: zodResolver(sizeStepSchema),
    defaultValues,
  });

  const dealMin = watch("deal_band_min_cents");
  const dealMax = watch("deal_band_max_cents");

  return (
    <form onSubmit={handleSubmit(onNext)} noValidate className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Target size band</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Optionally narrow your ICP by entity size (enrollment, population, or
          headcount) and estimated deal value. Leave blank to include all sizes.
        </p>
      </div>

      {/* Entity size */}
      <div className="space-y-3">
        <span className="text-sm font-medium">Entity size</span>

        {/* Preset chips */}
        <div className="flex flex-wrap gap-2">
          {SIZE_PRESETS.map((p) => {
            const active =
              watch("min_size") === p.min && watch("max_size") === p.max;
            return (
              <button
                key={p.label}
                type="button"
                onClick={() => {
                  setValue("min_size", p.min, { shouldValidate: true });
                  setValue("max_size", p.max, { shouldValidate: true });
                }}
                className={[
                  "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:border-primary/60",
                ].join(" ")}
              >
                {p.label}
              </button>
            );
          })}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label htmlFor="min-size" className="text-xs text-muted-foreground">
              Min (students / residents)
            </label>
            <input
              id="min-size"
              type="number"
              min={0}
              placeholder="e.g. 5000"
              aria-invalid={errors.min_size ? "true" : undefined}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              {...register("min_size", {
                setValueAs: (v) => (v === "" || v === null ? null : Number(v)),
              })}
            />
            {errors.min_size && (
              <p role="alert" className="text-xs text-destructive">
                {errors.min_size.message}
              </p>
            )}
          </div>
          <div className="space-y-1">
            <label htmlFor="max-size" className="text-xs text-muted-foreground">
              Max (students / residents)
            </label>
            <input
              id="max-size"
              type="number"
              min={0}
              placeholder="e.g. 50000"
              aria-invalid={errors.max_size ? "true" : undefined}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              {...register("max_size", {
                setValueAs: (v) => (v === "" || v === null ? null : Number(v)),
              })}
            />
            {errors.max_size && (
              <p role="alert" className="text-xs text-destructive">
                {errors.max_size.message}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Deal band */}
      <div className="space-y-3">
        <span className="text-sm font-medium">Estimated deal value</span>
        <div className="flex flex-wrap gap-2">
          {DEAL_PRESETS.map((p) => {
            const active = dealMin === p.min && dealMax === p.max;
            return (
              <button
                key={p.label}
                type="button"
                onClick={() => {
                  setValue("deal_band_min_cents", p.min, {
                    shouldValidate: true,
                  });
                  setValue("deal_band_max_cents", p.max, {
                    shouldValidate: true,
                  });
                }}
                className={[
                  "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border hover:border-primary/60",
                ].join(" ")}
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label htmlFor="deal-min" className="text-xs text-muted-foreground">
              Min deal value ($)
            </label>
            <input
              id="deal-min"
              type="number"
              min={0}
              step="1000"
              placeholder="e.g. 100000"
              value={centsToDollars(dealMin)}
              onChange={(e) =>
                setValue("deal_band_min_cents", dollarsToCents(e.target.value), {
                  shouldValidate: true,
                })
              }
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {errors.deal_band_min_cents && (
              <p role="alert" className="text-xs text-destructive">
                {errors.deal_band_min_cents.message}
              </p>
            )}
          </div>
          <div className="space-y-1">
            <label htmlFor="deal-max" className="text-xs text-muted-foreground">
              Max deal value ($)
            </label>
            <input
              id="deal-max"
              type="number"
              min={0}
              step="1000"
              placeholder="e.g. 500000"
              value={centsToDollars(dealMax)}
              onChange={(e) =>
                setValue("deal_band_max_cents", dollarsToCents(e.target.value), {
                  shouldValidate: true,
                })
              }
              className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {errors.deal_band_max_cents && (
              <p role="alert" className="text-xs text-destructive">
                {errors.deal_band_max_cents.message}
              </p>
            )}
          </div>
        </div>
      </div>

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
