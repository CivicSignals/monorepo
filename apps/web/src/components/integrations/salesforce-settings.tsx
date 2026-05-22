// Salesforce connection + field-mapping UI (K2; doc 04 J3/F-2, doc 08 §3.6).
//
// Admin-only settings island: connect Salesforce (OAuth redirect), then per
// connection pick a target object (from discovery), map signal/pipeline-item
// fields onto the object's writable fields (rows populated from discovery), and
// save the mapping. TanStack Query owns all server state (doc 06 §2).
"use client";

import { useEffect, useMemo, useState } from "react";
import { FieldMappingTemplates } from "@/components/integrations/field-mapping-templates";
import {
  useConnectSalesforce,
  useConnections,
  useDisconnect,
  useFieldMappings,
  useFields,
  useObjects,
  useSaveFieldMapping,
} from "@/hooks/use-salesforce";
import type { Connection } from "@/lib/salesforce-api";

// Source-field paths the mapping UI offers for the right-hand side of each row.
// These resolve against the `source` object the push endpoint receives.
const SOURCE_FIELDS: { value: string; label: string }[] = [
  { value: "", label: "— not mapped —" },
  { value: "signal.title", label: "Signal title" },
  { value: "signal.summary", label: "Signal summary" },
  { value: "signal.url", label: "Signal source URL" },
  { value: "signal.signal_type", label: "Signal type" },
  { value: "signal.entity_name", label: "Entity name" },
  { value: "signal.fields.amount_cents", label: "Amount (cents)" },
  { value: "signal.fields.due_at", label: "Due date" },
];

export function SalesforceSettings({
  workspaceId,
}: {
  workspaceId: string | undefined;
}) {
  const connections = useConnections(workspaceId);
  const salesforceConnections = useMemo(
    () =>
      (connections.data ?? []).filter(
        (c) => c.provider === "salesforce" && c.status !== "revoked",
      ),
    [connections.data],
  );

  if (!workspaceId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select or create a workspace first to manage Salesforce.
      </p>
    );
  }

  return (
    <div className="space-y-6" data-testid="salesforce-settings">
      <ConnectCard workspaceId={workspaceId} />
      {connections.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading connections…</p>
      ) : salesforceConnections.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="no-connections">
          No Salesforce connection yet. Connect one above to map fields and push
          signals.
        </p>
      ) : (
        salesforceConnections.map((conn) => (
          <ConnectionPanel
            key={conn.id}
            workspaceId={workspaceId}
            connection={conn}
          />
        ))
      )}
    </div>
  );
}

function ConnectCard({ workspaceId }: { workspaceId: string }) {
  const [name, setName] = useState("Salesforce production");
  const connect = useConnectSalesforce(workspaceId);

  function onConnect() {
    connect.mutate(name, {
      onSuccess: (created) => {
        if (created.redirect_url) {
          window.location.href = created.redirect_url;
        }
      },
    });
  }

  return (
    <div className="rounded-lg border p-4" data-testid="connect-card">
      <h2 className="text-lg font-semibold">Connect Salesforce</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Authorize CivicSignals to create and update records in your Salesforce
        org.
      </p>
      <div className="mt-3 flex gap-2">
        <input
          aria-label="Connection name"
          className="flex-1 rounded-md border px-3 py-2 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          type="button"
          data-testid="connect-salesforce-btn"
          disabled={connect.isPending || name.trim().length === 0}
          onClick={onConnect}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
        >
          {connect.isPending ? "Connecting…" : "Connect"}
        </button>
      </div>
      {connect.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          Could not start the Salesforce connection. Check that the integration
          is configured.
        </p>
      ) : null}
    </div>
  );
}

function ConnectionPanel({
  workspaceId,
  connection,
}: {
  workspaceId: string;
  connection: Connection;
}) {
  const disconnect = useDisconnect(workspaceId);
  const objects = useObjects(workspaceId, connection.id);
  const mappings = useFieldMappings(workspaceId, connection.id);

  const [targetObject, setTargetObject] = useState<string>("");

  // Default the selected object to the first default target / first discovered.
  useEffect(() => {
    if (targetObject) return;
    const fromDefault = connection.default_targets[0];
    const fromObjects = objects.data?.[0]?.name;
    if (fromDefault) setTargetObject(fromDefault);
    else if (fromObjects) setTargetObject(fromObjects);
  }, [connection.default_targets, objects.data, targetObject]);

  return (
    <div
      className="rounded-lg border p-4"
      data-testid={`connection-${connection.id}`}
    >
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-semibold">{connection.name}</h3>
          <p className="text-sm text-muted-foreground">
            Status:{" "}
            <span data-testid="connection-status">{connection.status}</span>
          </p>
        </div>
        <button
          type="button"
          data-testid="disconnect-btn"
          onClick={() => disconnect.mutate(connection.id)}
          className="rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Disconnect
        </button>
      </div>

      {connection.status === "needs_reauth" ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          This connection needs to be reconnected before you can push.
        </p>
      ) : null}

      <div className="mt-4">
        <label
          htmlFor={`object-${connection.id}`}
          className="block text-sm font-medium"
        >
          Salesforce object
        </label>
        <select
          id={`object-${connection.id}`}
          data-testid="object-select"
          className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
          value={targetObject}
          onChange={(e) => setTargetObject(e.target.value)}
        >
          {(objects.data ?? []).length === 0 ? (
            <option value={targetObject || ""}>
              {targetObject || "Opportunity"}
            </option>
          ) : (
            (objects.data ?? []).map((o) => (
              <option key={o.name} value={o.name}>
                {o.label}
                {o.custom ? " (custom)" : ""}
              </option>
            ))
          )}
        </select>
        {objects.isError ? (
          <p role="alert" className="mt-1 text-sm text-destructive">
            Could not load objects (reconnect may be required).
          </p>
        ) : null}
      </div>

      {targetObject ? (
        <FieldMappingEditor
          workspaceId={workspaceId}
          connectionId={connection.id}
          targetObject={targetObject}
          existing={
            mappings.data?.find((m) => m.target_object === targetObject)
              ?.field_map ?? {}
          }
        />
      ) : null}
    </div>
  );
}

function FieldMappingEditor({
  workspaceId,
  connectionId,
  targetObject,
  existing,
}: {
  workspaceId: string;
  connectionId: string;
  targetObject: string;
  existing: Record<string, string>;
}) {
  const fields = useFields(workspaceId, connectionId, targetObject);
  const save = useSaveFieldMapping(workspaceId, connectionId);
  const [map, setMap] = useState<Record<string, string>>(existing);

  // Re-seed the editor when the target object (and thus existing mapping) changes.
  useEffect(() => {
    setMap(existing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetObject]);

  function setRow(providerField: string, sourcePath: string) {
    setMap((prev) => {
      const next = { ...prev };
      if (sourcePath) next[providerField] = sourcePath;
      else delete next[providerField];
      return next;
    });
  }

  function onSave() {
    save.mutate({ target_object: targetObject, field_map: map });
  }

  return (
    <div className="mt-4" data-testid="field-mapping-editor">
      <h4 className="text-sm font-semibold">Field mapping</h4>
      <p className="text-sm text-muted-foreground">
        Map each Salesforce field to a signal value.
      </p>
      {fields.isLoading ? (
        <p className="mt-2 text-sm text-muted-foreground">Loading fields…</p>
      ) : (fields.data ?? []).length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground" data-testid="no-fields">
          No writable fields discovered for this object.
        </p>
      ) : (
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-1">Salesforce field</th>
              <th className="py-1">Signal value</th>
            </tr>
          </thead>
          <tbody>
            {(fields.data ?? []).map((f) => (
              <tr key={f.name} data-testid={`field-row-${f.name}`}>
                <td className="py-1 pr-2">
                  {f.label}
                  {f.required ? (
                    <span className="ml-1 text-destructive" aria-label="required">
                      *
                    </span>
                  ) : null}
                </td>
                <td className="py-1">
                  <select
                    aria-label={`Map ${f.label}`}
                    data-testid={`map-select-${f.name}`}
                    className="w-full rounded-md border px-2 py-1"
                    value={map[f.name] ?? ""}
                    onChange={(e) => setRow(f.name, e.target.value)}
                  >
                    {SOURCE_FIELDS.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          data-testid="save-mapping-btn"
          disabled={save.isPending}
          onClick={onSave}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save mapping"}
        </button>
        {save.isSuccess ? (
          <span role="status" className="text-sm text-green-600">
            Mapping saved.
          </span>
        ) : null}
        {save.isError ? (
          <span role="alert" className="text-sm text-destructive">
            Could not save the mapping.
          </span>
        ) : null}
      </div>

      <FieldMappingTemplates
        workspaceId={workspaceId}
        connectionId={connectionId}
        targetObject={targetObject}
        currentMap={map}
        onApplied={(mapping) => setMap(mapping.field_map)}
      />
    </div>
  );
}
