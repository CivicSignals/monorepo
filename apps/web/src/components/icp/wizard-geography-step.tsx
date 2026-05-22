// Step 1 — Geography: countries + US state filter (F2).
// Users targeting US public-sector entities can optionally restrict to specific
// states. The spec (doc 04 J2 §1) defaults to "all US states" which maps to
// countries=["US"] states=[] in the API.

"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type GeographyStepValues, geographyStepSchema } from "@/lib/icp-schemas";

// US states (abbreviation → label) for the multi-select.
const US_STATES: [string, string][] = [
  ["AL", "Alabama"], ["AK", "Alaska"], ["AZ", "Arizona"], ["AR", "Arkansas"],
  ["CA", "California"], ["CO", "Colorado"], ["CT", "Connecticut"], ["DE", "Delaware"],
  ["FL", "Florida"], ["GA", "Georgia"], ["HI", "Hawaii"], ["ID", "Idaho"],
  ["IL", "Illinois"], ["IN", "Indiana"], ["IA", "Iowa"], ["KS", "Kansas"],
  ["KY", "Kentucky"], ["LA", "Louisiana"], ["ME", "Maine"], ["MD", "Maryland"],
  ["MA", "Massachusetts"], ["MI", "Michigan"], ["MN", "Minnesota"], ["MS", "Mississippi"],
  ["MO", "Missouri"], ["MT", "Montana"], ["NE", "Nebraska"], ["NV", "Nevada"],
  ["NH", "New Hampshire"], ["NJ", "New Jersey"], ["NM", "New Mexico"], ["NY", "New York"],
  ["NC", "North Carolina"], ["ND", "North Dakota"], ["OH", "Ohio"], ["OK", "Oklahoma"],
  ["OR", "Oregon"], ["PA", "Pennsylvania"], ["RI", "Rhode Island"], ["SC", "South Carolina"],
  ["SD", "South Dakota"], ["TN", "Tennessee"], ["TX", "Texas"], ["UT", "Utah"],
  ["VT", "Vermont"], ["VA", "Virginia"], ["WA", "Washington"], ["WV", "West Virginia"],
  ["WI", "Wisconsin"], ["WY", "Wyoming"], ["DC", "D.C."],
];

interface WizardGeographyStepProps {
  defaultValues: GeographyStepValues;
  onNext: (values: GeographyStepValues) => void;
}

export function WizardGeographyStep({
  defaultValues,
  onNext,
}: WizardGeographyStepProps) {
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<GeographyStepValues>({
    resolver: zodResolver(geographyStepSchema),
    defaultValues,
  });

  const selectedStates = watch("states") ?? [];
  const isUSSelected = (watch("countries") ?? []).includes("US");

  const toggleState = (code: string) => {
    if (selectedStates.includes(code)) {
      setValue(
        "states",
        selectedStates.filter((s) => s !== code),
        { shouldValidate: true },
      );
    } else {
      setValue("states", [...selectedStates, code], { shouldValidate: true });
    }
  };

  const selectAllStates = () =>
    setValue("states", US_STATES.map(([code]) => code), { shouldValidate: true });

  const clearAllStates = () =>
    setValue("states", [], { shouldValidate: true });

  return (
    <form onSubmit={handleSubmit(onNext)} noValidate className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Name your ICP</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Give this ideal customer profile a name (e.g. &quot;K-12 Texas, 5k+ students&quot;).
        </p>
      </div>

      <div className="space-y-1">
        <label htmlFor="icp-name" className="text-sm font-medium">
          ICP name
        </label>
        <input
          id="icp-name"
          type="text"
          placeholder="My ICP"
          aria-invalid={errors.name ? "true" : undefined}
          aria-describedby={errors.name ? "name-error" : undefined}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("name")}
        />
        {errors.name && (
          <p id="name-error" role="alert" className="text-sm text-destructive">
            {errors.name.message}
          </p>
        )}
      </div>

      <div>
        <h2 className="text-xl font-semibold">Geography</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Where are your target entities located? The US is pre-selected — CivicSignals
          currently focuses on US public-sector data. Leave states blank to target all 50 states.
        </p>
      </div>

      {/* Country row (US locked for now, MVP is US-only per doc 14 §3.1) */}
      <div className="space-y-2">
        <span className="text-sm font-medium">Country</span>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={true}
            readOnly
            disabled
            className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
          />
          <span>United States</span>
          <span className="ml-auto text-xs text-muted-foreground">(MVP scope)</span>
        </label>
        {errors.countries && (
          <p role="alert" className="text-sm text-destructive">
            {errors.countries.message}
          </p>
        )}
      </div>

      {/* US state filter */}
      {isUSSelected && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              US states{" "}
              <span className="font-normal text-muted-foreground">
                ({selectedStates.length === 0 ? "all states" : `${selectedStates.length} selected`})
              </span>
            </span>
            <div className="flex gap-2 text-xs">
              <button
                type="button"
                onClick={selectAllStates}
                className="text-primary underline underline-offset-2"
              >
                All
              </button>
              <button
                type="button"
                onClick={clearAllStates}
                className="text-muted-foreground underline underline-offset-2"
              >
                Clear
              </button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Leave blank to match all US states.
          </p>
          <div
            className="grid grid-cols-3 gap-x-4 gap-y-1 sm:grid-cols-4 md:grid-cols-5 max-h-64 overflow-y-auto rounded-md border p-3"
            role="group"
            aria-label="US states"
          >
            {US_STATES.map(([code, label]) => (
              <label key={code} className="flex items-center gap-1.5 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedStates.includes(code)}
                  onChange={() => toggleState(code)}
                  className="h-4 w-4 shrink-0 rounded border-gray-300"
                />
                <span title={label}>{code}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-end pt-2">
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
