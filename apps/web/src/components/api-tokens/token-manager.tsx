"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ProblemError } from "@/lib/auth-api";
import {
  type CreateTokenValues,
  createTokenSchema,
} from "@/lib/api-tokens-schemas";
import type { ApiToken, ApiTokenCreated } from "@/lib/api-tokens-api";
import {
  useCreatePersonalToken,
  useCreateWorkspaceToken,
  usePersonalTokens,
  useRevokeToken,
  useTokenScopes,
  useWorkspaceTokens,
} from "@/hooks/use-api-tokens";

// API-token management UI (B8).
//
// `kind="personal"`  → /auth/tokens (any signed-in user, acts as them).
// `kind="workspace"` → /workspaces/{id}/api-tokens (admin only, one tenant).
//
// The plaintext secret is shown ONCE in a copy-once dialog right after creation
// and is never retrievable again (doc 08 §1.3, threat-model §4.2). Server state
// (the token list, scope catalog) is owned by TanStack Query; the just-created
// secret is local component state only — never cached.
export function TokenManager({
  kind,
  workspaceId,
}: {
  kind: "personal" | "workspace";
  workspaceId?: string;
}) {
  const personal = usePersonalTokens();
  const workspace = useWorkspaceTokens(workspaceId);
  const query = kind === "personal" ? personal : workspace;

  const { data: catalog } = useTokenScopes();
  const createPersonal = useCreatePersonalToken();
  const createWorkspace = useCreateWorkspaceToken(workspaceId);
  const createMutation = kind === "personal" ? createPersonal : createWorkspace;
  const revoke = useRevokeToken(kind, workspaceId);

  // The one-time secret to reveal; cleared when the dialog is dismissed.
  const [revealed, setRevealed] = useState<ApiTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateTokenValues>({
    resolver: zodResolver(createTokenSchema),
    defaultValues: { name: "", scopes: [] },
  });

  const onSubmit = handleSubmit(async (values) => {
    const created = await createMutation.mutateAsync({
      name: values.name.trim(),
      scopes: values.scopes ?? [],
      expires_at: values.expires_at ? values.expires_at : null,
    });
    setRevealed(created);
    setCopied(false);
    reset({ name: "", scopes: [] });
  });

  const apiError =
    createMutation.error instanceof ProblemError
      ? createMutation.error.problem.detail
      : createMutation.error?.message;

  const tokens = query.data ?? [];
  const scopes = catalog ?? [];

  async function copySecret() {
    if (!revealed) return;
    try {
      await navigator.clipboard.writeText(revealed.token);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="space-y-6" data-testid="token-manager">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold">
          {kind === "personal"
            ? "Personal access tokens"
            : "Workspace API tokens"}
        </h2>
        <p className="text-sm text-muted-foreground">
          {kind === "personal"
            ? "Tokens that act as you across your workspaces. The secret is shown once."
            : "Server-to-server tokens scoped to this workspace. The secret is shown once."}
        </p>
      </header>

      {/* Revealed-once dialog */}
      {revealed ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="New token secret"
          data-testid="token-reveal"
          className="space-y-3 rounded-md border border-primary/40 bg-primary/5 p-4"
        >
          <p className="text-sm font-medium">
            Copy your new token now — it won&apos;t be shown again.
          </p>
          <code
            data-testid="token-secret"
            className="block w-full overflow-x-auto rounded bg-background px-3 py-2 font-mono text-sm"
          >
            {revealed.token}
          </code>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={copySecret}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              onClick={() => {
                setRevealed(null);
                setCopied(false);
              }}
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              Done
            </button>
          </div>
        </div>
      ) : null}

      {/* Create form */}
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {apiError ? (
          <p
            role="alert"
            className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {apiError}
          </p>
        ) : null}

        <div className="space-y-1">
          <label htmlFor="token-name" className="text-sm font-medium">
            Token name
          </label>
          <input
            id="token-name"
            type="text"
            placeholder="e.g. Salesforce push"
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

        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Scopes</legend>
          {scopes.length === 0 ? (
            <p className="text-sm text-muted-foreground">Loading scopes…</p>
          ) : (
            <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
              {scopes.map((scope) => (
                <label key={scope} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    value={scope}
                    {...register("scopes")}
                  />
                  <span className="font-mono">{scope}</span>
                </label>
              ))}
            </div>
          )}
        </fieldset>

        <div className="space-y-1">
          <label htmlFor="token-expires" className="text-sm font-medium">
            Expires <span className="text-muted-foreground">(optional)</span>
          </label>
          <input
            id="token-expires"
            type="datetime-local"
            className="rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            {...register("expires_at")}
          />
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
        >
          {isSubmitting ? "Creating…" : "Create token"}
        </button>
      </form>

      {/* Token list */}
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Existing tokens</h3>
        {query.isLoading ? (
          <p className="text-sm text-muted-foreground" aria-live="polite">
            Loading tokens…
          </p>
        ) : tokens.length === 0 ? (
          <p className="text-sm text-muted-foreground">No tokens yet.</p>
        ) : (
          <ul className="divide-y rounded-md border" data-testid="token-list">
            {tokens.map((token) => (
              <TokenRow
                key={token.id}
                token={token}
                onRevoke={() => revoke.mutate(token.id)}
                revoking={revoke.isPending}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function TokenRow({
  token,
  onRevoke,
  revoking,
}: {
  token: ApiToken;
  onRevoke: () => void;
  revoking: boolean;
}) {
  const isRevoked = token.revoked_at !== null;
  return (
    <li
      className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm"
      data-testid="token-row"
    >
      <div className="space-y-0.5">
        <div className="flex items-center gap-2">
          <span className="font-medium">{token.name}</span>
          <code className="font-mono text-xs text-muted-foreground">
            {token.token_prefix}…
          </code>
          {isRevoked ? (
            <span className="rounded bg-destructive/10 px-1.5 py-0.5 text-xs text-destructive">
              revoked
            </span>
          ) : null}
        </div>
        <div className="text-xs text-muted-foreground">
          {token.scopes.length > 0 ? token.scopes.join(", ") : "no scopes"}
          {" · "}
          {token.last_used_at
            ? `last used ${new Date(token.last_used_at).toLocaleString()}`
            : "never used"}
        </div>
      </div>
      {!isRevoked ? (
        <button
          type="button"
          onClick={onRevoke}
          disabled={revoking}
          className="rounded-md border px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-60"
        >
          Revoke
        </button>
      ) : null}
    </li>
  );
}
