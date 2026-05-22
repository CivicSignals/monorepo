// D5 — Recipe authoring live preview: types + API call.
//
// Mirrors the API's `PreviewRequest` / `PreviewResult` / `FieldPreview` shapes
// (apps/api/.../modules/recipes/schemas.py). Once the OpenAPI doc is wired into
// SDK generation (TODO Q3), these can be replaced by the generated types; until
// then we hand-roll them so the staff UI is real and typed today.

export interface FieldPreview {
  name: string;
  value: string | null;
  matched: boolean;
  required: boolean;
  attr: string | null;
  selectors: string[];
  missing_required: boolean;
}

/** Mirrors `EntityRef` in `apps/api/.../modules/recipes/schemas.py`. */
export interface EntityRef {
  entity_id: string | null;
  name: string | null;
  state: string | null;
  kind: string | null;
}

/**
 * Mirrors `CanonicalRecord` in `apps/api/.../modules/recipes/schemas.py`.
 *
 * `dead_letters` and `degraded_fields` are present in the API model (added by
 * D11) but the preview endpoint surfaces them only via the top-level `degraded`
 * flag and `FieldPreview.missing_required` — they are included here for type
 * completeness so callers are not surprised by unexpected JSON keys.
 */
export interface CanonicalRecord {
  record_type: string;
  recipe_id: string;
  recipe_version: number;
  source_url: string;
  entity: EntityRef;
  signal_types: string[];
  degraded: boolean;
  degraded_fields: string[];
  dead_letters: unknown[];
  fields: Record<string, string | null>;
}

export interface PreviewResult {
  recipe_id: string;
  recipe_version: number;
  source: string;
  ok: boolean;
  degraded: boolean;
  extraction_method: string | null;
  signal_types: string[];
  fields: FieldPreview[];
  records: CanonicalRecord[];
  error: string | null;
}

// Mode toggles in the form pick which recipe source and sample input to send.
export type RecipeSource = "recipe_id" | "recipe_yaml";
export type SampleInput = "html" | "url";

export interface PreviewFormState {
  recipeSource: RecipeSource;
  recipeId: string;
  recipeYaml: string;
  sampleInput: SampleInput;
  html: string;
  url: string;
}

export interface PreviewRequestBody {
  recipe_id?: string;
  recipe_yaml?: string;
  html?: string;
  url?: string;
}

/** RFC 7807 problem+json body (doc 06 §5). */
export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
}

export class PreviewError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: Problem,
  ) {
    super(problem.detail || problem.title || `Preview failed (${status})`);
    this.name = "PreviewError";
  }
}

/**
 * Build the POST body from form state. Sends exactly one recipe source and one
 * sample input, matching the endpoint's one-of contract.
 */
export function buildPreviewBody(state: PreviewFormState): PreviewRequestBody {
  const body: PreviewRequestBody = {};
  if (state.recipeSource === "recipe_id") {
    body.recipe_id = state.recipeId.trim();
  } else {
    body.recipe_yaml = state.recipeYaml;
  }
  if (state.sampleInput === "html") {
    body.html = state.html;
  } else {
    body.url = state.url.trim();
  }
  return body;
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/**
 * Call the staff preview endpoint. The optional `staffToken` is sent as
 * `X-Staff-Token` (the D5 interim staff gate; real RBAC is TODO B7).
 */
export async function fetchRecipePreview(
  state: PreviewFormState,
  options: { staffToken?: string; fetchImpl?: typeof fetch } = {},
): Promise<PreviewResult> {
  const doFetch = options.fetchImpl ?? fetch;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (options.staffToken) headers["X-Staff-Token"] = options.staffToken;

  const res = await doFetch(`${API_BASE_URL}/recipes/preview`, {
    method: "POST",
    headers,
    body: JSON.stringify(buildPreviewBody(state)),
  });

  if (!res.ok) {
    const problem: Problem = await res
      .json()
      .catch(() => ({ title: res.statusText }));
    throw new PreviewError(res.status, problem);
  }
  return (await res.json()) as PreviewResult;
}
