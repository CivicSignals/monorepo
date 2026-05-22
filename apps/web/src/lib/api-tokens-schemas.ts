// Zod schemas for the API-token create form (B8).
//
// The web form validates the name + selected scopes before calling the API;
// the server re-validates scopes against its catalog and is the source of truth
// (doc 08 §1.3). ``expires_at`` is optional (an empty value = no expiry).

import { z } from "zod";

export const createTokenSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Give the token a name")
    .max(120, "Name is too long"),
  // The catalog is server-driven, so we don't enumerate scope strings here; the
  // form supplies the checked subset and the API rejects anything unknown.
  scopes: z.array(z.string()).default([]),
  // datetime-local yields "YYYY-MM-DDTHH:mm"; empty string = no expiry.
  expires_at: z.string().optional(),
});

export type CreateTokenValues = z.infer<typeof createTokenSchema>;
