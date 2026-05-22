"use client";

// InviteForm — admin UI to send a workspace invitation (B6).
// React Hook Form + Zod; TanStack Query mutation.

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ProblemError } from "@/lib/auth-api";
import { type InviteValues, inviteSchema } from "@/lib/invitations-schemas";
import { useCreateInvitation } from "@/hooks/use-invitations";

export function InviteForm({ workspaceId }: { workspaceId: string }) {
  const createInvitation = useCreateInvitation(workspaceId);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { invited_email: "", role: "member" },
  });

  async function onSubmit(values: InviteValues) {
    setSuccessMsg(null);
    try {
      await createInvitation.mutateAsync(values);
      setSuccessMsg(`Invitation sent to ${values.invited_email}.`);
      reset();
    } catch (err) {
      // Error is surfaced below via createInvitation.error
      void err;
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="space-y-4">
      <h2 className="text-lg font-semibold">Invite a member</h2>

      <div className="flex flex-col gap-1">
        <label htmlFor="invited_email" className="text-sm font-medium">
          Email address
        </label>
        <input
          id="invited_email"
          type="email"
          autoComplete="off"
          placeholder="teammate@example.com"
          {...register("invited_email")}
          className="rounded border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
        />
        {errors.invited_email && (
          <p className="text-xs text-destructive">
            {errors.invited_email.message}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="role" className="text-sm font-medium">
          Role
        </label>
        <select
          id="role"
          {...register("role")}
          className="rounded border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
        >
          <option value="viewer">Viewer — read-only access</option>
          <option value="member">Member — read + write workspace data</option>
          <option value="admin">Admin — manage workspace, members, tokens</option>
        </select>
      </div>

      {createInvitation.error && (
        <p className="rounded bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {createInvitation.error instanceof ProblemError
            ? createInvitation.error.problem.detail ??
              createInvitation.error.problem.title
            : "Failed to send invitation. Please try again."}
        </p>
      )}
      {successMsg && (
        <p className="rounded bg-green-100 px-3 py-2 text-sm text-green-800">
          {successMsg}
        </p>
      )}

      <button
        type="submit"
        disabled={isSubmitting || createInvitation.isPending}
        className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60"
      >
        {isSubmitting || createInvitation.isPending
          ? "Sending…"
          : "Send invitation"}
      </button>
    </form>
  );
}
