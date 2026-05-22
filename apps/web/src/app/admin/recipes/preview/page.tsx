// D5 — Staff recipe authoring live preview page (/admin/recipes/preview).
//
// Lets a recipe author paste a recipe (by id or inline YAML) + a sample input
// (pasted HTML or a URL), runs it through the staff preview endpoint, and shows
// the extracted fields + degraded indicators (doc 18 §3).
//
// TODO B7: this is a staff/admin surface. Real route-level RBAC gating lands
// with auth (B1) + roles (B7); the API endpoint enforces the interim staff
// token gate today. Until B7 lands, the staff token can be injected via the
// NEXT_PUBLIC_RECIPE_PREVIEW_STAFF_TOKEN env var or entered directly in the
// form below.
import { RecipePreviewForm } from "@/components/admin/recipe-preview-form";

export const metadata = {
  title: "Recipe preview · CivicSignals admin",
};

export default function RecipePreviewPage() {
  // Read the token from the server-only env var so it is never bundled into
  // client JS. The form receives it as an initial value for the token input
  // (which users can override); the actual fetch happens client-side so the
  // header is sent at runtime, not baked into the bundle. Operators set this in
  // their deployment env; in local dev the API gate is open when the var is
  // absent (environment == "development"). TODO B7: replace with a session-
  // scoped credential once real RBAC is in place.
  const envToken = process.env.RECIPE_PREVIEW_STAFF_TOKEN ?? "";

  return (
    <main className="container max-w-4xl py-12">
      <header className="mb-8">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Staff tooling
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight">
          Recipe live preview
        </h1>
        <p className="mt-2 max-w-prose text-sm text-muted-foreground">
          Dry-run a recipe against a sample page to see what it extracts before
          you commit it. Nothing is fetched or stored unless you choose the URL
          mode; HTML mode runs entirely against the snapshot you paste.
        </p>
      </header>

      {/* Pass the env token as the default; the form lets staff override it inline
          so the page stays usable in environments where the env var is not set
          (TODO B7: remove once real RBAC is in place). */}
      <RecipePreviewForm defaultStaffToken={envToken} />
    </main>
  );
}
