// Zod schemas for the invitation forms (B6).

import { z } from "zod";

export const inviteSchema = z.object({
  invited_email: z.string().trim().email("Enter a valid email address"),
  role: z.enum(["admin", "member", "viewer"] as const).default("member"),
});

export type InviteValues = z.infer<typeof inviteSchema>;

export const acceptInviteSchema = z.object({
  // Token is passed via URL param, validated here before submitting.
  token: z.string().min(1, "Invitation token is required"),
});

export type AcceptInviteValues = z.infer<typeof acceptInviteSchema>;
