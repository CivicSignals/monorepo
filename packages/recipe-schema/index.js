// Convenience loader for the canonical recipe + connector JSON Schemas.
// Both the Python runner (packages/sdk-py) and TS tooling resolve from here so
// there is exactly one source of truth (doc 18 §3.1, doc 16 §17).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

export const connectorSchema = JSON.parse(
  readFileSync(join(here, "schema", "connector.schema.json"), "utf8"),
);
export const recipeSchema = JSON.parse(
  readFileSync(join(here, "schema", "recipe.schema.json"), "utf8"),
);
