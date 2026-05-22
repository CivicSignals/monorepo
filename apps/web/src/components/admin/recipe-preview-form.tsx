"use client";

// D5 — Staff recipe authoring live preview form.
// Server state (the preview call) is owned by TanStack Query (useMutation), per
// doc 06 §2 — never Zustand. Client-only form state is local component state.
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
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

export function RecipePreviewForm() {
  const [state, setState] = useState<PreviewFormState>(INITIAL_STATE);
  // The staff token is client-only state — never injected server-side to avoid
  // leaking it in the RSC payload. Staff paste it directly into the form.
  // TODO B7: remove this field and gate on the session credential instead.
  const [staffToken, setStaffToken] = useState<string>("");

  const mutation = useMutation<PreviewResult, Error, PreviewFormState>({
    mutationFn: (formState) =>
      fetchRecipePreview(formState, { staffToken: staffToken || undefined }),
  });

  function set<K extends keyof PreviewFormState>(
    key: K,
    val: PreviewFormState[K],
  ) {
    setState((prev) => ({ ...prev, [key]: val }));
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate(state);
  }

  return (
    <div className="space-y-6">
      <form onSubmit={onSubmit} className="space-y-5" aria-label="Recipe preview">
        {/* Staff token gate (TODO B7). Always shown; staff paste the token
            directly in non-development environments where the API gate is
            enabled. The value is never sent to the server as a prop — it
            stays in client-only React state to avoid RSC payload exposure. */}
        <div className="space-y-1">
          <label
            htmlFor="staff-token"
            className="block text-xs font-medium text-muted-foreground"
          >
            Staff token{" "}
            <span className="font-normal">(X-Staff-Token; TODO B7)</span>
          </label>
          <input
            id="staff-token"
            type="password"
            autoComplete="off"
            className="w-full rounded-md border px-3 py-2 text-sm font-mono"
            placeholder="leave blank in development"
            value={staffToken}
            onChange={(e) => setStaffToken(e.target.value)}
          />
        </div>

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
