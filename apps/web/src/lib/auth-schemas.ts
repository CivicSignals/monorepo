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

// --- B3: Password reset -------------------------------------------------------

export const forgotPasswordSchema = z.object({
  email: z.string().email("Enter a valid email address."),
});

export type ForgotPasswordValues = z.infer<typeof forgotPasswordSchema>;

export const resetPasswordSchema = z
  .object({
    token: z.string().min(1, "Reset token is required."),
    new_password: z
      .string()
      .min(
        PASSWORD_MIN_LEN,
        `Password must be at least ${PASSWORD_MIN_LEN} characters.`,
      )
      .max(
        PASSWORD_MAX_LEN,
        `Password must be at most ${PASSWORD_MAX_LEN} characters.`,
      ),
    confirm_password: z.string().min(1, "Confirm your new password."),
  })
  .refine((data) => data.new_password === data.confirm_password, {
    message: "Passwords do not match.",
    path: ["confirm_password"],
  });

export type ResetPasswordValues = z.infer<typeof resetPasswordSchema>;
