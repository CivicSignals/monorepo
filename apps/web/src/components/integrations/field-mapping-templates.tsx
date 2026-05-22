// Field-mapping templates / per-connection defaults (K6).
//
// Shared between the Salesforce (K2) and HubSpot (K3) settings islands: lets an
// admin/member save the *current* in-editor field mapping as a named, reusable
// template (optionally the connection default that auto-applies on push), list
// saved templates, apply one back onto the live mapping, and delete one. All
// server state lives in TanStack Query (doc 06 §2) via the use-salesforce hooks;
// the endpoints are provider-generic, so this component is reused as-is.
"use client";

import { useState } from "react";
import {
  useApplyFieldMappingTemplate,
  useDeleteFieldMappingTemplate,
  useFieldMappingTemplates,
  useSaveFieldMappingTemplate,
} from "@/hooks/use-salesforce";
import type { FieldMapping } from "@/lib/salesforce-api";

export function FieldMappingTemplates({
  workspaceId,
  connectionId,
  targetObject,
  currentMap,
  onApplied,
}: {
  workspaceId: string;
  connectionId: string;
  targetObject: string;
  /** The mapping currently being edited — snapshotted when saving a template. */
  currentMap: Record<string, string>;
  /** Called with the live mapping after a template is applied (re-seed editor). */
  onApplied: (mapping: FieldMapping) => void;
}) {
  const templates = useFieldMappingTemplates(workspaceId, connectionId);
  const saveTemplate = useSaveFieldMappingTemplate(workspaceId, connectionId);
  const applyTemplate = useApplyFieldMappingTemplate(workspaceId, connectionId);
  const deleteTemplate = useDeleteFieldMappingTemplate(workspaceId, connectionId);

  const [name, setName] = useState("");
  const [isDefault, setIsDefault] = useState(false);

  function onSaveTemplate() {
    const trimmed = name.trim();
    if (!trimmed) return;
    saveTemplate.mutate(
      {
        name: trimmed,
        target_object: targetObject,
        field_map: currentMap,
        is_default: isDefault,
      },
      {
        onSuccess: () => {
          setName("");
          setIsDefault(false);
        },
      },
    );
  }

  function onApply(templateId: string) {
    applyTemplate.mutate(templateId, {
      onSuccess: (mapping) => onApplied(mapping),
    });
  }

  const list = templates.data ?? [];

  return (
    <div className="mt-4 rounded-md border p-3" data-testid="field-mapping-templates">
      <h4 className="text-sm font-semibold">Mapping templates</h4>
      <p className="text-sm text-muted-foreground">
        Save the current mapping as a reusable default for this connection, or
        apply a saved one.
      </p>

      {/* Save the current mapping as a named template / default. */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          aria-label="Template name"
          data-testid="template-name-input"
          className="flex-1 rounded-md border px-3 py-2 text-sm"
          placeholder="e.g. Opportunity defaults"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            data-testid="template-default-checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
          />
          Set as default
        </label>
        <button
          type="button"
          data-testid="save-template-btn"
          disabled={saveTemplate.isPending || name.trim().length === 0}
          onClick={onSaveTemplate}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
        >
          {saveTemplate.isPending ? "Saving…" : "Save as template"}
        </button>
      </div>
      {saveTemplate.isSuccess ? (
        <p role="status" className="mt-2 text-sm text-green-600">
          Template saved.
        </p>
      ) : null}
      {saveTemplate.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          Could not save the template.
        </p>
      ) : null}

      {/* List of saved templates with apply / delete. */}
      {templates.isLoading ? (
        <p className="mt-3 text-sm text-muted-foreground">Loading templates…</p>
      ) : list.length === 0 ? (
        <p
          className="mt-3 text-sm text-muted-foreground"
          data-testid="no-templates"
        >
          No saved templates yet.
        </p>
      ) : (
        <ul className="mt-3 space-y-2" data-testid="template-list">
          {list.map((t) => (
            <li
              key={t.id}
              data-testid={`template-${t.id}`}
              className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
            >
              <span>
                {t.name}
                {t.is_default ? (
                  <span
                    data-testid={`template-default-badge-${t.id}`}
                    className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs"
                  >
                    default
                  </span>
                ) : null}
                <span className="ml-2 text-muted-foreground">
                  → {t.target_object}
                </span>
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  data-testid={`apply-template-btn-${t.id}`}
                  disabled={applyTemplate.isPending}
                  onClick={() => onApply(t.id)}
                  className="rounded-md border px-3 py-1.5 hover:bg-muted disabled:opacity-50"
                >
                  Apply
                </button>
                <button
                  type="button"
                  data-testid={`delete-template-btn-${t.id}`}
                  disabled={deleteTemplate.isPending}
                  onClick={() => deleteTemplate.mutate(t.id)}
                  className="rounded-md border px-3 py-1.5 hover:bg-muted disabled:opacity-50"
                >
                  Delete
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {applyTemplate.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          Could not apply the template.
        </p>
      ) : null}
    </div>
  );
}
