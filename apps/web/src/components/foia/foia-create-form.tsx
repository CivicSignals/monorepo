// FoiaCreateForm — dialog/form for creating a new FOIA request (M4).
//
// Supports template-based and freeform creation:
//   1. Pick a jurisdiction template (optional).
//   2. Fill subject + entity_id + submission target.
//   3. Body defaults to the rendered template; user can override.
//
// Mutations: useCreateFoiaRequest + useFoiaTemplates.

"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCreateFoiaRequest, useFoiaTemplates } from "@/hooks/use-foia";
import { renderFoiaTemplate } from "@/lib/foia-api";
import { ProblemError } from "@/lib/auth-api";

interface FoiaCreateFormProps {
  onClose: () => void;
}

export function FoiaCreateForm({ onClose }: FoiaCreateFormProps) {
  const router = useRouter();
  const { data: templatesData } = useFoiaTemplates();
  const createMutation = useCreateFoiaRequest();

  const [entityId, setEntityId] = useState("");
  const [subject, setSubject] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [body, setBody] = useState("");
  const [submissionTarget, setSubmissionTarget] = useState("");
  const [submissionMethod, setSubmissionMethod] = useState("manual");
  const [renderError, setRenderError] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState(false);

  // Render template body when jurisdiction changes (if no custom body yet).
  const handleJurisdictionChange = useCallback(
    async (juri: string) => {
      setJurisdiction(juri);
      setRenderError(null);
      if (!juri) {
        setBody("");
        return;
      }
      setIsRendering(true);
      try {
        // Render with empty context first — placeholders will show as-is for
        // the user to fill in, matching typical FOIA template UX.
        const result = await renderFoiaTemplate(juri, {});
        setBody(result.rendered_body);
      } catch (err) {
        // Non-blocking: user can still type a freeform body.
        if (err instanceof ProblemError && err.problem.status === 422) {
          // Template requires placeholders — load the raw template body instead.
          const tmpl = templatesData?.items.find((t) => t.jurisdiction === juri);
          if (tmpl) setBody(tmpl.body);
        } else {
          setRenderError("Failed to load template. You can type the body manually.");
        }
      } finally {
        setIsRendering(false);
      }
    },
    [templatesData],
  );

  // Form validation
  const isValid = subject.trim().length > 0 && entityId.trim().length > 0 && body.trim().length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid) return;

    try {
      const created = await createMutation.mutateAsync({
        entity_id: entityId.trim(),
        subject: subject.trim(),
        body: body.trim(),
        ...(jurisdiction ? { jurisdiction } : {}),
        submission_method: submissionMethod,
        ...(submissionTarget.trim() ? { submission_target: submissionTarget.trim() } : {}),
      });
      onClose();
      router.push(`/foia/${created.id}`);
    } catch {
      // Error displayed via createMutation.error below.
    }
  };

  const errorMessage = createMutation.error
    ? createMutation.error instanceof ProblemError
      ? (createMutation.error.problem.detail ?? createMutation.error.problem.title)
      : createMutation.error.message
    : null;

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="space-y-4"
      aria-label="Create FOIA request"
    >
      {/* Jurisdiction / Template picker */}
      <div>
        <label htmlFor="foia-create-jurisdiction" className="block text-sm font-medium">
          Template (optional)
        </label>
        <select
          id="foia-create-jurisdiction"
          value={jurisdiction}
          onChange={(e) => void handleJurisdictionChange(e.target.value)}
          disabled={isRendering}
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        >
          <option value="">No template — freeform</option>
          {templatesData?.items.map((t) => (
            <option key={t.jurisdiction} value={t.jurisdiction}>
              {t.jurisdiction} — {t.jurisdiction_name}
            </option>
          ))}
        </select>
        {isRendering && <p className="mt-1 text-xs text-muted-foreground">Loading template…</p>}
        {renderError && <p className="mt-1 text-xs text-destructive">{renderError}</p>}
      </div>

      {/* Entity ID */}
      <div>
        <label htmlFor="foia-create-entity" className="block text-sm font-medium">
          Target entity ID <span aria-hidden>*</span>
        </label>
        <input
          id="foia-create-entity"
          type="text"
          required
          value={entityId}
          onChange={(e) => setEntityId(e.target.value)}
          placeholder="UUID of the target agency/entity"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Find the entity UUID on the{" "}
          <Link href="/entities" className="underline hover:no-underline">
            Entity Directory
          </Link>
          .
        </p>
      </div>

      {/* Subject */}
      <div>
        <label htmlFor="foia-create-subject" className="block text-sm font-medium">
          Subject <span aria-hidden>*</span>
        </label>
        <input
          id="foia-create-subject"
          type="text"
          required
          maxLength={512}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Short description of records requested"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {/* Body */}
      <div>
        <label htmlFor="foia-create-body" className="block text-sm font-medium">
          Request body <span aria-hidden>*</span>
        </label>
        <textarea
          id="foia-create-body"
          required
          rows={8}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Full text of the public records request…"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm font-mono placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {/* Submission method */}
      <div>
        <label htmlFor="foia-create-method" className="block text-sm font-medium">
          Submission method
        </label>
        <select
          id="foia-create-method"
          value={submissionMethod}
          onChange={(e) => setSubmissionMethod(e.target.value)}
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="manual">Manual (copy and send yourself)</option>
          <option value="email">Email</option>
          <option value="portal">Online portal</option>
          <option value="mail">Mail</option>
          <option value="in_person">In person</option>
        </select>
      </div>

      {/* Submission target */}
      <div>
        <label htmlFor="foia-create-target" className="block text-sm font-medium">
          Submission target (email / URL / address)
        </label>
        <input
          id="foia-create-target"
          type="text"
          maxLength={1024}
          value={submissionTarget}
          onChange={(e) => setSubmissionTarget(e.target.value)}
          placeholder="records@agency.gov or https://portal.agency.gov/…"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {/* Server error */}
      {errorMessage && (
        <div
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {errorMessage}
        </div>
      )}

      {/* Actions */}
      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!isValid || createMutation.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
        >
          {createMutation.isPending ? "Creating…" : "Create draft"}
        </button>
      </div>
    </form>
  );
}

// ---- Modal wrapper ----

interface FoiaCreateModalProps {
  open: boolean;
  onClose: () => void;
}

export function FoiaCreateModal({ open, onClose }: FoiaCreateModalProps) {
  // Trap focus on mount; close on Escape.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Create FOIA request"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div className="w-full max-w-2xl rounded-xl border bg-background p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">New FOIA request</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            ✕
          </button>
        </div>
        <FoiaCreateForm onClose={onClose} />
      </div>
    </div>
  );
}
