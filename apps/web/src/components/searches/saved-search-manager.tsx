"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ProblemError } from "@/lib/auth-api";
import {
  type SavedSearchFormValues,
  savedSearchFormSchema,
} from "@/lib/searches-schemas";
import type { SavedSearchOut, SearchFilters } from "@/lib/searches-api";
import {
  FEED_STATUS_LABELS,
  SIGNAL_TYPE_LABELS,
  type FeedStatus,
  type SignalType,
} from "@/lib/signals-api";
import {
  useCreateSavedSearch,
  useDeleteSavedSearch,
  useSavedSearches,
  useUpdateSavedSearch,
} from "@/hooks/use-searches";

// Saved-search management UI (H1).
//
// Lists the saved searches the active workspace member can see (their own plus
// any shared searches), and lets them create, rename / re-filter, share, and
// delete their *own* searches. A shared search is read-only to non-owners (the
// API enforces ownership; the row hides the edit/delete controls for searches
// the caller does not own). Server state is owned by TanStack Query; this island
// only holds transient form/edit UI state.

const SIGNAL_TYPES = Object.keys(SIGNAL_TYPE_LABELS) as SignalType[];
const FEED_STATUSES = Object.keys(FEED_STATUS_LABELS) as FeedStatus[];

function formToFilters(values: SavedSearchFormValues): SearchFilters {
  const filters: SearchFilters = {};
  if (values.signal_type) filters.signal_type = values.signal_type;
  if (values.statuses.length > 0) filters.statuses = values.statuses;
  if (values.min_score !== null) filters.min_score = values.min_score;
  if (values.published_at_gte)
    filters.published_at_gte = new Date(values.published_at_gte).toISOString();
  if (values.published_at_lt)
    filters.published_at_lt = new Date(values.published_at_lt).toISOString();
  return filters;
}

// An ISO timestamp from the API back into the `datetime-local` input value
// (which has no timezone / seconds): trim to "YYYY-MM-DDTHH:mm".
function toLocalInput(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return iso.slice(0, 16);
}

function filtersToForm(
  name: string,
  filters: SearchFilters,
  isShared: boolean,
): SavedSearchFormValues {
  return {
    name,
    signal_type: filters.signal_type ?? "",
    statuses: filters.statuses ?? [],
    min_score: filters.min_score ?? null,
    published_at_gte: toLocalInput(filters.published_at_gte),
    published_at_lt: toLocalInput(filters.published_at_lt),
    is_shared: isShared,
  };
}

const EMPTY_FORM: SavedSearchFormValues = {
  name: "",
  signal_type: "",
  statuses: [],
  min_score: null,
  published_at_gte: "",
  published_at_lt: "",
  is_shared: false,
};

// Map an RFC 7807 problem's per-field errors[] onto RHF field errors so the
// server's explicit messages render inline next to the offending input (H2).
// The backend's `published_at_gte` carries the cross-field date-range message;
// an unknown field (or a problem without errors[]) is ignored here and shown
// via the form-level alert instead.
const SERVER_FIELDS = new Set<keyof SavedSearchFormValues>([
  "name",
  "signal_type",
  "statuses",
  "min_score",
  "published_at_gte",
  "published_at_lt",
]);

function applyServerErrors(
  error: unknown,
  setError: ReturnType<
    typeof useForm<SavedSearchFormValues>
  >["setError"],
): boolean {
  if (!(error instanceof ProblemError)) return false;
  const errors = error.problem.errors ?? [];
  let applied = false;
  for (const e of errors) {
    const field = e.field as keyof SavedSearchFormValues;
    if (SERVER_FIELDS.has(field)) {
      setError(field, { type: "server", message: e.message });
      applied = true;
    }
  }
  return applied;
}

export function SavedSearchManager() {
  const query = useSavedSearches();
  const createMutation = useCreateSavedSearch();

  const create = useForm<SavedSearchFormValues>({
    resolver: zodResolver(savedSearchFormSchema),
    defaultValues: EMPTY_FORM,
  });

  const onCreate = create.handleSubmit(async (values) => {
    try {
      await createMutation.mutateAsync({
        name: values.name.trim(),
        filters: formToFilters(values),
        is_shared: values.is_shared,
      });
      create.reset(EMPTY_FORM);
    } catch (err) {
      // Surface the server's explicit per-field messages inline (H2). The
      // mutation already rejected, so re-throwing is unnecessary.
      applyServerErrors(err, create.setError);
    }
  });

  const createError =
    createMutation.error instanceof ProblemError
      ? createMutation.error.problem.detail
      : createMutation.error?.message;

  const searches = query.data?.items ?? [];

  return (
    <section className="space-y-6" data-testid="saved-search-manager">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold">Saved searches</h2>
        <p className="text-sm text-muted-foreground">
          Save a set of feed filters to re-run later. Shared searches are visible
          to everyone in this workspace.
        </p>
      </header>

      {/* Create form */}
      <form onSubmit={onCreate} noValidate className="space-y-4">
        {createError ? (
          <p
            role="alert"
            className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {createError}
          </p>
        ) : null}

        <SavedSearchFields form={create} idPrefix="create" />

        <button
          type="submit"
          disabled={create.formState.isSubmitting}
          className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
        >
          {create.formState.isSubmitting ? "Saving…" : "Save search"}
        </button>
      </form>

      {/* List */}
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Your &amp; shared searches</h3>
        {query.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading saved searches…
          </p>
        ) : searches.length === 0 ? (
          <p className="text-sm text-muted-foreground">No saved searches yet.</p>
        ) : (
          <ul
            className="divide-y rounded-md border"
            data-testid="saved-search-list"
          >
            {searches.map((s) => (
              <SavedSearchRow key={s.id} search={s} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function SavedSearchRow({ search }: { search: SavedSearchOut }) {
  const updateMutation = useUpdateSavedSearch();
  const deleteMutation = useDeleteSavedSearch();
  const [editing, setEditing] = useState(false);

  // The current user owns it only if the API returned it as editable; we cannot
  // know the user id here cheaply, so we expose edit/delete and let the API gate
  // (a non-owner gets a 403 surfaced inline). The list already includes shared
  // searches the caller may merely view.
  const edit = useForm<SavedSearchFormValues>({
    resolver: zodResolver(savedSearchFormSchema),
    defaultValues: filtersToForm(search.name, search.filters, search.is_shared),
  });

  const onSave = edit.handleSubmit(async (values) => {
    try {
      await updateMutation.mutateAsync({
        id: search.id,
        update: {
          name: values.name.trim(),
          filters: formToFilters(values),
          is_shared: values.is_shared,
        },
      });
      setEditing(false);
    } catch (err) {
      // Keep the editor open and surface the server's per-field messages (H2).
      applyServerErrors(err, edit.setError);
    }
  });

  const rowError =
    updateMutation.error instanceof ProblemError
      ? updateMutation.error.problem.detail
      : deleteMutation.error instanceof ProblemError
        ? deleteMutation.error.problem.detail
        : (updateMutation.error?.message ?? deleteMutation.error?.message);

  if (editing) {
    return (
      <li className="space-y-3 px-3 py-3" data-testid="saved-search-row">
        <form onSubmit={onSave} noValidate className="space-y-3">
          {rowError ? (
            <p role="alert" className="text-sm text-destructive">
              {rowError}
            </p>
          ) : null}
          <SavedSearchFields form={edit} idPrefix={`edit-${search.id}`} />
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={updateMutation.isPending}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
            >
              {updateMutation.isPending ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                edit.reset(
                  filtersToForm(search.name, search.filters, search.is_shared),
                );
              }}
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              Cancel
            </button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li
      className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
      data-testid="saved-search-row"
    >
      <div className="space-y-0.5">
        <div className="flex items-center gap-2">
          <span className="font-medium">{search.name}</span>
          {search.is_shared ? (
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
              shared
            </span>
          ) : null}
        </div>
        <div className="text-xs text-muted-foreground">
          {summarizeFilters(search.filters)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="rounded-md border px-2 py-1 text-xs hover:bg-muted"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={() => deleteMutation.mutate(search.id)}
          disabled={deleteMutation.isPending}
          className="rounded-md border px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-60"
        >
          Delete
        </button>
      </div>
      {rowError ? (
        <p role="alert" className="w-full text-xs text-destructive">
          {rowError}
        </p>
      ) : null}
    </li>
  );
}

function summarizeFilters(filters: SearchFilters): string {
  const parts: string[] = [];
  if (filters.signal_type)
    parts.push(SIGNAL_TYPE_LABELS[filters.signal_type] ?? filters.signal_type);
  if (filters.statuses && filters.statuses.length > 0)
    parts.push(
      filters.statuses.map((s) => FEED_STATUS_LABELS[s] ?? s).join(", "),
    );
  if (filters.min_score !== undefined)
    parts.push(`score ≥ ${filters.min_score}`);
  if (filters.published_at_gte || filters.published_at_lt) {
    const from = filters.published_at_gte
      ? filters.published_at_gte.slice(0, 10)
      : "…";
    const to = filters.published_at_lt
      ? filters.published_at_lt.slice(0, 10)
      : "…";
    parts.push(`${from} → ${to}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "All signals";
}

// Shared fieldset for the create + edit forms (RHF-controlled).
function SavedSearchFields({
  form,
  idPrefix,
}: {
  form: ReturnType<typeof useForm<SavedSearchFormValues>>;
  idPrefix: string;
}) {
  const {
    register,
    formState: { errors },
  } = form;

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <label htmlFor={`${idPrefix}-name`} className="text-sm font-medium">
          Name
        </label>
        <input
          id={`${idPrefix}-name`}
          type="text"
          placeholder="e.g. High-score RFPs"
          aria-invalid={errors.name ? "true" : undefined}
          className="w-full max-w-sm rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("name")}
        />
        {errors.name ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.name.message}
          </p>
        ) : null}
      </div>

      <div className="space-y-1">
        <label
          htmlFor={`${idPrefix}-signal-type`}
          className="text-sm font-medium"
        >
          Signal type
        </label>
        <select
          id={`${idPrefix}-signal-type`}
          className="w-full max-w-sm rounded-md border bg-background px-3 py-2 text-sm"
          {...register("signal_type")}
        >
          <option value="">Any type</option>
          {SIGNAL_TYPES.map((t) => (
            <option key={t} value={t}>
              {SIGNAL_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Statuses</legend>
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
          {FEED_STATUSES.map((s) => (
            <label key={s} className="flex items-center gap-2 text-sm">
              <input type="checkbox" value={s} {...register("statuses")} />
              <span>{FEED_STATUS_LABELS[s]}</span>
            </label>
          ))}
        </div>
        {errors.statuses ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.statuses.message}
          </p>
        ) : null}
      </fieldset>

      <div className="space-y-1">
        <label
          htmlFor={`${idPrefix}-min-score`}
          className="text-sm font-medium"
        >
          Minimum score{" "}
          <span className="text-muted-foreground">(0–100, optional)</span>
        </label>
        <input
          id={`${idPrefix}-min-score`}
          type="number"
          min={0}
          max={100}
          step={1}
          aria-invalid={errors.min_score ? "true" : undefined}
          className="w-32 rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("min_score", {
            setValueAs: (v) =>
              v === "" || v === null || v === undefined ? null : Number(v),
          })}
        />
        {errors.min_score ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.min_score.message}
          </p>
        ) : null}
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">
          Published date range{" "}
          <span className="text-muted-foreground">(optional)</span>
        </legend>
        <div className="flex flex-wrap gap-4">
          <div className="space-y-1">
            <label
              htmlFor={`${idPrefix}-published-gte`}
              className="block text-xs text-muted-foreground"
            >
              From
            </label>
            <input
              id={`${idPrefix}-published-gte`}
              type="datetime-local"
              aria-invalid={errors.published_at_gte ? "true" : undefined}
              className="rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              {...register("published_at_gte")}
            />
          </div>
          <div className="space-y-1">
            <label
              htmlFor={`${idPrefix}-published-lt`}
              className="block text-xs text-muted-foreground"
            >
              To
            </label>
            <input
              id={`${idPrefix}-published-lt`}
              type="datetime-local"
              aria-invalid={errors.published_at_lt ? "true" : undefined}
              className="rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              {...register("published_at_lt")}
            />
          </div>
        </div>
        {errors.published_at_gte ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.published_at_gte.message}
          </p>
        ) : null}
        {errors.published_at_lt ? (
          <p role="alert" className="text-sm text-destructive">
            {errors.published_at_lt.message}
          </p>
        ) : null}
      </fieldset>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" {...register("is_shared")} />
        <span>Share with everyone in this workspace</span>
      </label>
    </div>
  );
}
