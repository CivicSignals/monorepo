// Step 5 — Keywords + threshold (F2).
// Required and excluded keyword lists (tag-input) and the scoring threshold.

"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type KeywordsStepValues, keywordsStepSchema } from "@/lib/icp-schemas";

interface WizardKeywordsStepProps {
  defaultValues: KeywordsStepValues;
  onNext: (values: KeywordsStepValues) => void;
  onBack: () => void;
}

/** Tag-input helper: displays chips + an input to add new items. */
function TagInput({
  label,
  description,
  value,
  onChange,
  placeholder,
  id,
}: {
  label: string;
  description: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  id: string;
}) {
  const [inputValue, setInputValue] = useState("");

  const addTag = () => {
    const trimmed = inputValue.trim();
    if (!trimmed || value.includes(trimmed)) {
      setInputValue("");
      return;
    }
    onChange([...value, trimmed]);
    setInputValue("");
  };

  const removeTag = (tag: string) => {
    onChange(value.filter((t) => t !== tag));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag();
    }
    if (e.key === "Backspace" && !inputValue && value.length > 0) {
      removeTag(value[value.length - 1]);
    }
  };

  return (
    <div className="space-y-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <p className="text-xs text-muted-foreground">{description}</p>
      <div className="flex flex-wrap gap-1.5 rounded-md border bg-background p-2 focus-within:ring-2 focus-within:ring-ring">
        {value.map((tag) => (
          <span
            key={tag}
            className="flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary"
          >
            {tag}
            <button
              type="button"
              aria-label={`Remove ${tag}`}
              onClick={() => removeTag(tag)}
              className="ml-0.5 rounded-full p-0.5 hover:bg-primary/20 focus-visible:outline focus-visible:outline-2"
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={addTag}
          placeholder={value.length === 0 ? placeholder : ""}
          className="min-w-32 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Press Enter or comma to add. Backspace removes the last tag.
      </p>
    </div>
  );
}

const THRESHOLD_PRESETS = [
  { label: "Broad (30)", value: 30 },
  { label: "Balanced (50)", value: 50 },
  { label: "Focused (70)", value: 70 },
  { label: "Tight (85)", value: 85 },
];

export function WizardKeywordsStep({
  defaultValues,
  onNext,
  onBack,
}: WizardKeywordsStepProps) {
  const {
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<KeywordsStepValues>({
    resolver: zodResolver(keywordsStepSchema),
    defaultValues,
  });

  const required = watch("keywords_required") ?? [];
  const excluded = watch("keywords_excluded") ?? [];
  const threshold = watch("threshold") ?? 50;

  return (
    <form onSubmit={handleSubmit(onNext)} noValidate className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Keywords & scoring threshold</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Optionally require or exclude keywords in signal text. Set a minimum
          score for signals to appear in your feed (0–100 scale).
        </p>
      </div>

      <TagInput
        id="keywords-required"
        label="Required keywords"
        description="Signals must contain at least one of these terms to reach your feed."
        placeholder="e.g. analytics, learning, curriculum…"
        value={required}
        onChange={(v) => setValue("keywords_required", v, { shouldValidate: true })}
      />

      <TagInput
        id="keywords-excluded"
        label="Excluded keywords"
        description="Signals containing any of these terms are filtered out."
        placeholder="e.g. pre-school, charter, private…"
        value={excluded}
        onChange={(v) => setValue("keywords_excluded", v, { shouldValidate: true })}
      />

      {/* Threshold */}
      <div className="space-y-3">
        <span className="text-sm font-medium">
          Score threshold:{" "}
          <span className="font-semibold text-primary">{threshold}</span> / 100
        </span>
        <p className="text-xs text-muted-foreground">
          Only signals scoring at or above this threshold appear in your feed.
          Lower = more signals; higher = only your best matches.
        </p>
        <div className="flex flex-wrap gap-2">
          {THRESHOLD_PRESETS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setValue("threshold", p.value, { shouldValidate: true })}
              className={[
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                threshold === p.value
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border hover:border-primary/60",
              ].join(" ")}
            >
              {p.label}
            </button>
          ))}
        </div>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={threshold}
          onChange={(e) =>
            setValue("threshold", parseInt(e.target.value, 10), {
              shouldValidate: true,
            })
          }
          aria-label="Score threshold"
          className="w-full accent-primary"
        />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>0 — all signals</span>
          <span>100 — perfect match only</span>
        </div>
        {errors.threshold && (
          <p role="alert" className="text-sm text-destructive">
            {errors.threshold.message}
          </p>
        )}
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
