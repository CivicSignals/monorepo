// D5 — Staff recipe authoring live preview page (/admin/recipes/preview).
//
// Lets a recipe author paste a recipe (by id or inline YAML) + a sample input
// (pasted HTML or a URL), runs it through the staff preview endpoint, and shows
// the extracted fields + degraded indicators (doc 18 §3).
//
// TODO B7: this is a staff/admin surface. Real route-level RBAC gating lands
// with auth (B1) + roles (B7); the API endpoint enforces the interim staff
// token gate today.
import { RecipePreviewForm } from "@/components/admin/recipe-preview-form";

export const metadata = {
  title: "Recipe preview · CivicSignals admin",
};

export default function RecipePreviewPage() {
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

      <RecipePreviewForm />
    </main>
  );
}
