// Zod schemas for the auth forms (B1). Mirror the API's validation
// (apps/api .../auth/schemas.py): email format + password length bounds.

import { z } from "zod";

export const PASSWORD_MIN_LEN = 8;
export const PASSWORD_MAX_LEN = 128;

export const signupSchema = z.object({
  name: z.string().trim().max(200).optional().or(z.literal("")),
  email: z.string().email("Enter a valid email address."),
  password: z
    .string()
    .min(
      PASSWORD_MIN_LEN,
      `Password must be at least ${PASSWORD_MIN_LEN} characters.`,
    )
    .max(
      PASSWORD_MAX_LEN,
      `Password must be at most ${PASSWORD_MAX_LEN} characters.`,
    ),
});

export type SignupValues = z.infer<typeof signupSchema>;

export const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

export type LoginValues = z.infer<typeof loginSchema>;
