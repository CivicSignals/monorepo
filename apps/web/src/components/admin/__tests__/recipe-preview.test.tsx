// @vitest-environment jsdom
// D5 — Tests for the recipe live preview UI: the request-body builder, the
// fetch wrapper's problem handling, and the results component rendering
// (extracted fields + degraded / missing-required indicators).

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { RecipePreviewResults } from "../recipe-preview-results";
import {
  type PreviewFormState,
  type PreviewResult,
  buildPreviewBody,
  fetchRecipePreview,
  PreviewError,
} from "@/lib/recipe-preview";

afterEach(() => cleanup());

const BASE_STATE: PreviewFormState = {
  recipeSource: "recipe_id",
  recipeId: "wa-state-webs",
  recipeYaml: "",
  sampleInput: "html",
  html: "<h1>hi</h1>",
  url: "",
};

function makeResult(overrides: Partial<PreviewResult> = {}): PreviewResult {
  return {
    recipe_id: "wa-state-webs",
    recipe_version: 1,
    source: "preview://pasted-html",
    ok: true,
    degraded: false,
    extraction_method: "primary",
    signal_types: ["rfp_posted"],
    fields: [
      {
        name: "title",
        value: "RFP 2025-014",
        matched: true,
        required: true,
        attr: null,
        selectors: ["h1.solicitation-title", ".bid-header > h2"],
        missing_required: false,
      },
    ],
    records: [],
    error: null,
    ...overrides,
  };
}

describe("buildPreviewBody", () => {
  it("sends recipe_id + html when those modes are selected", () => {
    expect(buildPreviewBody(BASE_STATE)).toEqual({
      recipe_id: "wa-state-webs",
      html: "<h1>hi</h1>",
    });
  });

  it("sends recipe_yaml + url when those modes are selected", () => {
    const body = buildPreviewBody({
      ...BASE_STATE,
      recipeSource: "recipe_yaml",
      recipeYaml: "recipe_id: x",
      sampleInput: "url",
      url: " https://example.gov/rfp ",
    });
    expect(body).toEqual({
      recipe_yaml: "recipe_id: x",
      url: "https://example.gov/rfp",
    });
    expect(body.recipe_id).toBeUndefined();
    expect(body.html).toBeUndefined();
  });
});

describe("fetchRecipePreview", () => {
  it("returns the parsed result and forwards the staff token header", async () => {
    const result = makeResult();
    let capturedInit: RequestInit | undefined;
    const fetchImpl = ((input: RequestInfo | URL, init?: RequestInit) => {
      void input;
      capturedInit = init;
      return Promise.resolve(
        new Response(JSON.stringify(result), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }) as typeof fetch;

    const out = await fetchRecipePreview(BASE_STATE, {
      staffToken: "tok",
      fetchImpl,
    });
    expect(out.recipe_id).toBe("wa-state-webs");
    const headers = capturedInit?.headers as Record<string, string>;
    expect(headers["X-Staff-Token"]).toBe("tok");
  });

  it("throws a PreviewError carrying the problem on a non-2xx response", async () => {
    const fetchImpl = (() =>
      Promise.resolve(
        new Response(JSON.stringify({ title: "Recipe not found", status: 404 }), {
          status: 404,
          headers: { "Content-Type": "application/problem+json" },
        }),
      )) as typeof fetch;
    await expect(
      fetchRecipePreview(BASE_STATE, { fetchImpl }),
    ).rejects.toBeInstanceOf(PreviewError);
  });
});

describe("RecipePreviewResults", () => {
  it("renders extracted field values and selectors", () => {
    render(<RecipePreviewResults result={makeResult()} />);
    expect(screen.getByText("RFP 2025-014")).toBeInTheDocument();
    expect(screen.getByText(/rfp_posted/)).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(
      screen.getByText(/h1\.solicitation-title/),
    ).toBeInTheDocument();
  });

  it("flags a degraded extraction", () => {
    render(<RecipePreviewResults result={makeResult({ degraded: true })} />);
    expect(screen.getByText(/Degraded/)).toBeInTheDocument();
  });

  it("renders the error and missing-required marker on a failed extraction", () => {
    const failed = makeResult({
      ok: false,
      error: "required field 'title' extracted no value",
      fields: [
        {
          name: "title",
          value: null,
          matched: false,
          required: true,
          attr: null,
          selectors: ["h1.solicitation-title"],
          missing_required: true,
        },
      ],
    });
    render(<RecipePreviewResults result={failed} />);
    expect(screen.getByText("Extraction failed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "required field 'title'",
    );
    expect(screen.getByText("missing required field")).toBeInTheDocument();
  });
});
