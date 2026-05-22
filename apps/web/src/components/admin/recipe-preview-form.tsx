"use client";

// D5 — Staff recipe authoring live preview form.
// Server state (the preview call) is owned by TanStack Query (useMutation), per
// doc 06 §2 — never Zustand. Client-only form state is local component state.
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import {
  type PreviewFormState,
  type PreviewResult,
  fetchRecipePreview,
} from "@/lib/recipe-preview";
import { RecipePreviewResults } from "./recipe-preview-results";

const INITIAL_STATE: PreviewFormState = {
  recipeSource: "recipe_id",
  recipeId: "wa-state-webs",
  recipeYaml: "",
  sampleInput: "html",
  html: "",
  url: "",
};

function RadioGroup<T extends string>({
  legend,
  name,
  value,
  options,
  onChange,
}: {
  legend: string;
  name: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="flex items-center gap-4">
      <legend className="sr-only">{legend}</legend>
      {options.map((option) => (
        <label key={option.value} className="flex items-center gap-1.5 text-sm">
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}

export function RecipePreviewForm({
  staffToken,
}: {
  staffToken?: string;
}) {
  const [state, setState] = useState<PreviewFormState>(INITIAL_STATE);

  const mutation = useMutation<PreviewResult, Error, PreviewFormState>({
    mutationFn: (formState) =>
      fetchRecipePreview(formState, { staffToken }),
  });

  function set<K extends keyof PreviewFormState>(
    key: K,
    val: PreviewFormState[K],
  ) {
    setState((prev) => ({ ...prev, [key]: val }));
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    mutation.mutate(state);
  }

  return (
    <div className="space-y-6">
      <form onSubmit={onSubmit} className="space-y-5" aria-label="Recipe preview">
        <div className="space-y-2">
          <RadioGroup
            legend="Recipe source"
            name="recipe-source"
            value={state.recipeSource}
            onChange={(v) => set("recipeSource", v)}
            options={[
              { value: "recipe_id", label: "By recipe id" },
              { value: "recipe_yaml", label: "Paste recipe YAML" },
            ]}
          />
          {state.recipeSource === "recipe_id" ? (
            <input
              aria-label="Recipe id"
              className="w-full rounded-md border px-3 py-2 text-sm"
              placeholder="wa-state-webs"
              value={state.recipeId}
              onChange={(e) => set("recipeId", e.target.value)}
            />
          ) : (
            <textarea
              aria-label="Recipe YAML"
              className="h-48 w-full rounded-md border px-3 py-2 font-mono text-sm"
              placeholder="recipe_id: my-source&#10;connector: http_static&#10;version: 1&#10;..."
              value={state.recipeYaml}
              onChange={(e) => set("recipeYaml", e.target.value)}
            />
          )}
        </div>

        <div className="space-y-2">
          <RadioGroup
            legend="Sample input"
            name="sample-input"
            value={state.sampleInput}
            onChange={(v) => set("sampleInput", v)}
            options={[
              { value: "html", label: "Paste HTML" },
              { value: "url", label: "Fetch a URL" },
            ]}
          />
          {state.sampleInput === "html" ? (
            <textarea
              aria-label="Sample HTML"
              className="h-48 w-full rounded-md border px-3 py-2 font-mono text-sm"
              placeholder="<html>…</html>"
              value={state.html}
              onChange={(e) => set("html", e.target.value)}
            />
          ) : (
            <input
              aria-label="Sample URL"
              className="w-full rounded-md border px-3 py-2 text-sm"
              placeholder="https://example.gov/rfp/123"
              value={state.url}
              onChange={(e) => set("url", e.target.value)}
            />
          )}
          {state.sampleInput === "url" && (
            <p className="text-xs text-muted-foreground">
              The URL is fetched server-side, honoring the recipe&apos;s
              robots.txt and politeness settings.
            </p>
          )}
        </div>

        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
        >
          {mutation.isPending ? "Running preview…" : "Run preview"}
        </button>
      </form>

      {mutation.isError && (
        <p
          role="alert"
          className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {mutation.error.message}
        </p>
      )}

      {mutation.data && <RecipePreviewResults result={mutation.data} />}
    </div>
  );
}
