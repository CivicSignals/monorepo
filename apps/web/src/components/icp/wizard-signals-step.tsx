// Step 4 — Signal types + weights (F2).
// Cards for each signal type (toggle on/off). Slider for per-type weight.
// At least one type must be selected.

"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type SignalsStepValues, signalsStepSchema } from "@/lib/icp-schemas";
import {
  SIGNAL_TYPES,
  SIGNAL_TYPE_LABELS,
  SIGNAL_TYPE_DESCRIPTIONS,
  type SignalType,
} from "@/lib/icp-api";

interface WizardSignalsStepProps {
  defaultValues: SignalsStepValues;
  onNext: (values: SignalsStepValues) => void;
  onBack: () => void;
}

export function WizardSignalsStep({
  defaultValues,
  onNext,
  onBack,
}: WizardSignalsStepProps) {
  const {
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<SignalsStepValues>({
    resolver: zodResolver(signalsStepSchema),
    defaultValues: {
      signal_types: defaultValues.signal_types,
      signal_weights: defaultValues.signal_weights ?? {},
    },
  });

  const selectedTypes = watch("signal_types") ?? [];
  const signalWeights = watch("signal_weights") ?? {};

  const toggleType = (type: SignalType) => {
    if (selectedTypes.includes(type)) {
      setValue(
        "signal_types",
        selectedTypes.filter((t) => t !== type),
        { shouldValidate: true },
      );
      // Remove weight when type is deselected.
      const next = { ...signalWeights };
      delete next[type];
      setValue("signal_weights", next);
    } else {
      setValue("signal_types", [...selectedTypes, type], { shouldValidate: true });
      // Default weight = 1.0 (fully relevant).
      setValue("signal_weights", { ...signalWeights, [type]: 1.0 });
    }
  };

  const setWeight = (type: SignalType, weight: number) => {
    setValue("signal_weights", { ...signalWeights, [type]: weight });
  };

  return (
    <form onSubmit={handleSubmit(onNext)} noValidate className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Which signals matter to you?</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Select the signal types relevant to your sales motion. You can also
          weight each type to tune your feed score. Select at least one.
        </p>
      </div>

      {errors.signal_types && (
        <p role="alert" className="text-sm text-destructive">
          {errors.signal_types.message}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {SIGNAL_TYPES.map((type) => {
          const selected = selectedTypes.includes(type);
          const weight = signalWeights[type] ?? 1.0;
          return (
            <div
              key={type}
              className={[
                "rounded-lg border-2 p-4 transition-colors",
                selected ? "border-primary bg-primary/5" : "border-border",
              ].join(" ")}
            >
              <button
                type="button"
                role="checkbox"
                aria-checked={selected}
                onClick={() => toggleType(type)}
                className="w-full flex items-start gap-3 text-left"
              >
                <span
                  className={[
                    "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                    selected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-muted-foreground",
                  ].join(" ")}
                  aria-hidden
                >
                  {selected && <span className="text-xs leading-none">✓</span>}
                </span>
                <div>
                  <span className="font-semibold text-sm">
                    {SIGNAL_TYPE_LABELS[type]}
                  </span>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {SIGNAL_TYPE_DESCRIPTIONS[type]}
                  </p>
                </div>
              </button>

              {/* Weight slider — only shown when type is selected */}
              {selected && (
                <div className="mt-3 space-y-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Importance</span>
                    <span>{Math.round(weight * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={weight}
                    onChange={(e) => setWeight(type, parseFloat(e.target.value))}
                    aria-label={`${SIGNAL_TYPE_LABELS[type]} importance weight`}
                    className="w-full accent-primary"
                  />
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>Low</span>
                    <span>High</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
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
