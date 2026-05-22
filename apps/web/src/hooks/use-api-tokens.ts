// API-token hooks (B8) — TanStack Query owns the server interactions (doc 06 §2).
//
// Two surfaces share the same shapes (doc 08 §1.3):
//   - personal access tokens (act as the user across workspaces);
//   - workspace tokens (admin-gated, bound to one workspace).
// The plaintext secret is returned only by the create mutation and is held in
// component state for the "revealed once" dialog — never cached in the query
// store (it is unrecoverable after the dialog closes; threat-model §4.2).

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ApiToken,
  type ApiTokenCreated,
  type CreateTokenInput,
  createPersonalToken,
  createWorkspaceToken,
  listPersonalTokens,
  listTokenScopes,
  listWorkspaceTokens,
  revokePersonalToken,
  revokeWorkspaceToken,
} from "@/lib/api-tokens-api";
import { useSessionStore } from "@/store/session";

// "personal" lists are keyed by user; "workspace" lists by workspace id.
export const apiTokensKey = (
  kind: "personal" | "workspace",
  scopeId: string | undefined,
) => ["api-tokens", kind, scopeId ?? "none"] as const;

export const tokenScopesKey = ["api-tokens", "scopes"] as const;

/** The catalog of grantable scopes (rendered as checkboxes in the create form). */
export function useTokenScopes() {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<string[]>({
    queryKey: tokenScopesKey,
    queryFn: async () => {
      if (!token) return [];
      return (await listTokenScopes(token)).scopes;
    },
    enabled: token !== null,
    staleTime: Infinity, // catalog rarely changes within a session
  });
}

/** List the current user's personal access tokens (metadata only). */
export function usePersonalTokens() {
  const token = useSessionStore((s) => s.accessToken);
  const userId = useSessionStore((s) => s.user?.id);
  return useQuery<ApiToken[]>({
    queryKey: apiTokensKey("personal", userId),
    queryFn: async () => {
      if (!token) return [];
      return (await listPersonalTokens(token)).items;
    },
    enabled: token !== null,
  });
}

/** List a workspace's API tokens (admin only; metadata only). */
export function useWorkspaceTokens(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<ApiToken[]>({
    queryKey: apiTokensKey("workspace", workspaceId),
    queryFn: async () => {
      if (!token || !workspaceId) return [];
      return (await listWorkspaceTokens(token, workspaceId)).items;
    },
    enabled: token !== null && !!workspaceId,
  });
}

/** Create a personal access token; resolves with the one-time secret. */
export function useCreatePersonalToken() {
  const token = useSessionStore((s) => s.accessToken);
  const userId = useSessionStore((s) => s.user?.id);
  const queryClient = useQueryClient();
  return useMutation<ApiTokenCreated, Error, CreateTokenInput>({
    mutationFn: (input) => {
      if (!token) return Promise.reject(new Error("not authenticated"));
      return createPersonalToken(token, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: apiTokensKey("personal", userId),
      });
    },
  });
}

/** Create a workspace API token; resolves with the one-time secret. */
export function useCreateWorkspaceToken(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<ApiTokenCreated, Error, CreateTokenInput>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("no active workspace"));
      return createWorkspaceToken(token, workspaceId, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: apiTokensKey("workspace", workspaceId),
      });
    },
  });
}

/** Revoke a token (personal or workspace) and refresh its list. */
export function useRevokeToken(
  kind: "personal" | "workspace",
  workspaceId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  const userId = useSessionStore((s) => s.user?.id);
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (tokenId) => {
      if (!token) return Promise.reject(new Error("not authenticated"));
      if (kind === "personal") return revokePersonalToken(token, tokenId);
      if (!workspaceId) return Promise.reject(new Error("no active workspace"));
      return revokeWorkspaceToken(token, workspaceId, tokenId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: apiTokensKey(
          kind,
          kind === "personal" ? userId : workspaceId,
        ),
      });
    },
  });
}
