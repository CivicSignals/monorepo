// Invitation hooks (B6) — TanStack Query owns the server interactions.
//
// Admin surface: list/create/revoke/resend invitations (workspace-scoped).
// Accept surface: consume an invite token (post-login or post-signup).

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type CreateInvitationInput,
  type Invitation,
  type InvitationStatus,
  type MemberOut,
  acceptInvitation,
  createInvitation,
  listInvitations,
  resendInvitation,
  revokeInvitation,
} from "@/lib/invitations-api";
import { useSessionStore } from "@/store/session";

// Query key factory — scoped to workspace + optional status filter so cache
// invalidations are targeted and don't bleed across workspaces.
export const invitationsQueryKey = (
  workspaceId: string | undefined,
  status?: InvitationStatus,
) => ["invitations", workspaceId ?? "none", status ?? "all"] as const;

// ---- List (admin) ----

export function useInvitations(
  workspaceId: string | undefined,
  status?: InvitationStatus,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<Invitation[]>({
    queryKey: invitationsQueryKey(workspaceId, status),
    queryFn: async () => {
      if (!token || !workspaceId) return [];
      // Drain all pages so the admin table shows the complete list.
      const all: Invitation[] = [];
      let cursor: string | undefined;
      do {
        const page = await listInvitations(token, workspaceId, {
          status,
          cursor,
          limit: 100,
        });
        all.push(...page.items);
        cursor = page.next_cursor ?? undefined;
      } while (cursor);
      return all;
    },
    enabled: !!token && !!workspaceId,
  });
}

// ---- Create (admin) ----

export function useCreateInvitation(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();

  return useMutation<Invitation, Error, CreateInvitationInput>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return createInvitation(token, workspaceId, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: invitationsQueryKey(workspaceId),
      });
    },
  });
}

// ---- Revoke (admin) ----

export function useRevokeInvitation(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (invitationId) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return revokeInvitation(token, workspaceId, invitationId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: invitationsQueryKey(workspaceId),
      });
    },
  });
}

// ---- Resend (admin) ----

export function useResendInvitation(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();

  return useMutation<Invitation, Error, string>({
    mutationFn: (invitationId) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return resendInvitation(token, workspaceId, invitationId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: invitationsQueryKey(workspaceId),
      });
    },
  });
}

// ---- Accept ----

export function useAcceptInvitation() {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();

  return useMutation<MemberOut, Error, string>({
    mutationFn: (inviteToken) => {
      if (!token) return Promise.reject(new Error("not authenticated"));
      return acceptInvitation(token, { token: inviteToken });
    },
    onSuccess: () => {
      // Invalidate workspace list so the newly-joined workspace appears.
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });
}
