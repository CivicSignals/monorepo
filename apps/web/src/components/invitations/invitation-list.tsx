"use client";

// InvitationList — pending invitation management UI for admins (B6).
// Lists pending invitations with revoke + resend actions.

import { useInvitations, useRevokeInvitation, useResendInvitation } from "@/hooks/use-invitations";
import type { Invitation } from "@/lib/invitations-api";

function InvitationRow({
  invitation,
  onRevoke,
  onResend,
  isLoading,
}: {
  invitation: Invitation;
  onRevoke: (id: string) => void;
  onResend: (id: string) => void;
  isLoading: boolean;
}) {
  const expires = new Date(invitation.expires_at);
  const isExpired = expires < new Date();

  return (
    <tr className="border-b last:border-0">
      <td className="py-2 pr-4 text-sm">{invitation.invited_email}</td>
      <td className="py-2 pr-4 text-sm capitalize">{invitation.role}</td>
      <td className="py-2 pr-4 text-sm">
        <span
          className={
            invitation.status === "pending" && !isExpired
              ? "text-yellow-600"
              : "text-muted-foreground"
          }
        >
          {isExpired && invitation.status === "pending"
            ? "expired"
            : invitation.status}
        </span>
      </td>
      <td className="py-2 pr-4 text-xs text-muted-foreground">
        {expires.toLocaleDateString()}
      </td>
      <td className="py-2 text-right">
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => onResend(invitation.id)}
            disabled={isLoading || invitation.status === "accepted"}
            className="text-xs text-blue-600 hover:underline disabled:opacity-40"
          >
            Resend
          </button>
          <button
            type="button"
            onClick={() => onRevoke(invitation.id)}
            disabled={
              isLoading ||
              invitation.status === "accepted" ||
              invitation.status === "revoked"
            }
            className="text-xs text-destructive hover:underline disabled:opacity-40"
          >
            Revoke
          </button>
        </div>
      </td>
    </tr>
  );
}

export function InvitationList({ workspaceId }: { workspaceId: string }) {
  const { data: invitations, isLoading } = useInvitations(workspaceId);
  const revoke = useRevokeInvitation(workspaceId);
  const resend = useResendInvitation(workspaceId);

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (!invitations || invitations.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No invitations yet.</p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b text-xs uppercase text-muted-foreground">
            <th className="pb-2 pr-4 font-medium">Email</th>
            <th className="pb-2 pr-4 font-medium">Role</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium">Expires</th>
            <th className="pb-2 text-right font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {invitations.map((inv) => (
            <InvitationRow
              key={inv.id}
              invitation={inv}
              onRevoke={(id) => void revoke.mutate(id)}
              onResend={(id) => void resend.mutate(id)}
              isLoading={revoke.isPending || resend.isPending}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
