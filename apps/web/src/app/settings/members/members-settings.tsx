"use client";

// Members settings island (B6).
//
// Combines the invite form and pending-invitation list. When the user is not
// an admin the API will return 403; this island handles that gracefully by
// showing a read-only view (the InviteForm and action buttons are hidden).

import { useActiveWorkspace, useWorkspaces } from "@/hooks/use-workspaces";
import { InviteForm } from "@/components/invitations/invite-form";
import { InvitationList } from "@/components/invitations/invitation-list";

export function MembersSettings() {
  const { data: workspaces } = useWorkspaces();
  const active = useActiveWorkspace(workspaces);

  if (!active) {
    return (
      <p className="text-sm text-muted-foreground">
        Select or create a workspace first to manage its members.
      </p>
    );
  }

  const isAdmin =
    active.role === "admin" || active.role === "owner";

  return (
    <div className="space-y-8">
      {isAdmin ? (
        <section>
          <InviteForm workspaceId={active.id} />
        </section>
      ) : (
        <p className="rounded border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          You need the <strong>admin</strong> or <strong>owner</strong> role to
          invite members.
        </p>
      )}

      <section>
        <h2 className="mb-4 text-lg font-semibold">Pending invitations</h2>
        <InvitationList workspaceId={active.id} />
      </section>
    </div>
  );
}
