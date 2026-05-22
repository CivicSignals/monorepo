// D5 — Presentational results panel for the recipe live preview.
// Pure component (no data fetching) so it renders deterministically in tests.
import { cn } from "@/lib/utils";
import type { FieldPreview, PreviewResult } from "@/lib/recipe-preview";

function StatusBadge({ ok, degraded }: { ok: boolean; degraded: boolean }) {
  if (!ok) {
    return (
      <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-800">
        Extraction failed
      </span>
    );
  }
  if (degraded) {
    return (
      <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-semibold text-amber-800">
        Degraded (fallback selector)
      </span>
    );
  }
  return (
    <span className="rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-semibold text-green-800">
      OK
    </span>
  );
}

function FieldRow({ field }: { field: FieldPreview }) {
  return (
    <tr
      className={cn(
        "border-b last:border-0",
        field.missing_required && "bg-red-50",
      )}
    >
      <td className="py-2 pr-4 align-top font-mono text-sm">
        {field.name}
        {field.required && (
          <span className="ml-1 text-xs text-muted-foreground">(required)</span>
        )}
      </td>
      <td className="py-2 pr-4 align-top text-sm">
        {field.matched ? (
          <span>{field.value}</span>
        ) : (
          <span
            className={cn(
              "italic",
              field.missing_required
                ? "font-semibold text-red-700"
                : "text-muted-foreground",
            )}
          >
            {field.missing_required ? "missing required field" : "no match"}
          </span>
        )}
      </td>
      <td className="py-2 align-top font-mono text-xs text-muted-foreground">
        {field.selectors.join(" → ")}
        {field.attr && <span> [@{field.attr}]</span>}
      </td>
    </tr>
  );
}

export function RecipePreviewResults({ result }: { result: PreviewResult }) {
  return (
    <section aria-label="Preview results" className="rounded-lg border p-5">
      <header className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">
          {result.recipe_id}{" "}
          <span className="text-sm font-normal text-muted-foreground">
            v{result.recipe_version}
          </span>
        </h2>
        <StatusBadge ok={result.ok} degraded={result.degraded} />
        <span className="text-xs text-muted-foreground">{result.source}</span>
      </header>

      {result.error && (
        <p
          role="alert"
          className="mb-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {result.error}
        </p>
      )}

      <div className="mb-4">
        <h3 className="mb-1 text-sm font-medium">Signal types</h3>
        <p className="text-sm text-muted-foreground">
          {result.signal_types.length > 0
            ? result.signal_types.join(", ")
            : "(none declared)"}
        </p>
      </div>

      <h3 className="mb-1 text-sm font-medium">Extracted fields</h3>
      {result.fields.length > 0 ? (
        <table className="w-full table-fixed">
          <thead>
            <tr className="border-b text-left text-xs uppercase text-muted-foreground">
              <th className="w-1/4 py-1.5 pr-4 font-medium">Field</th>
              <th className="w-2/5 py-1.5 pr-4 font-medium">Value</th>
              <th className="py-1.5 font-medium">Selectors</th>
            </tr>
          </thead>
          <tbody>
            {result.fields.map((field) => (
              <FieldRow key={field.name} field={field} />
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-sm text-muted-foreground">
          This recipe declares no fields.
        </p>
      )}

      <div className="mt-4">
        <h3 className="mb-1 text-sm font-medium">
          Canonical records ({result.records.length})
        </h3>
        {result.records.length > 0 ? (
          <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
            {JSON.stringify(result.records, null, 2)}
          </pre>
        ) : (
          <p className="text-sm text-muted-foreground">
            No records produced (extraction did not complete).
          </p>
        )}
      </div>
    </section>
  );
}
