"use client";

// B3: Forgot-password form — POST /auth/password-reset/request.
// Always returns 204 regardless of whether the email is registered (no user
// enumeration). The UI shows a "check your email" message on submission.

import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type ForgotPasswordValues, forgotPasswordSchema } from "@/lib/auth-schemas";
import { ProblemError } from "@/lib/auth-api";
import { usePasswordResetRequest } from "@/hooks/use-auth";

export function ForgotPasswordForm() {
  const resetRequest = usePasswordResetRequest();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordValues>({
    resolver: zodResolver(forgotPasswordSchema),
  });

  const onSubmit = handleSubmit((values) => {
    resetRequest.mutate({ email: values.email });
  });

  // Always show the "check your email" success state once submitted (even on 204
  // for an unknown address — no enumeration in the UI either).
  if (resetRequest.isSuccess) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="forgot-password-success"
      >
        <h1 className="text-2xl font-bold tracking-tight">Check your email</h1>
        <p className="text-sm text-muted-foreground">
          If that address is registered you&apos;ll receive a password-reset
          link shortly. Check your spam folder if it doesn&apos;t arrive within
          a few minutes.
        </p>
        <p className="text-sm text-muted-foreground">
          <Link href="/login" className="font-medium underline underline-offset-4">
            Back to sign in
          </Link>
        </p>
      </div>
    );
  }

  const apiError =
    resetRequest.error instanceof ProblemError
      ? resetRequest.error.problem.detail
      : resetRequest.error?.message;

  return (
    <form onSubmit={onSubmit} noValidate className="w-full max-w-sm space-y-4">
      <div className="space-y-1 text-center">
        <h1 className="text-2xl font-bold tracking-tight">Forgot password</h1>
        <p className="text-sm text-muted-foreground">
          Enter your email and we&apos;ll send you a reset link.
        </p>
      </div>

      {apiError ? (
        <p
          role="alert"
          className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {apiError}
        </p>
      ) : null}

      <div className="space-y-1">
        <label htmlFor="email" className="text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          aria-invalid={errors.email ? "true" : undefined}
          aria-describedby={errors.email ? "email-error" : undefined}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("email")}
        />
        {errors.email ? (
          <p id="email-error" role="alert" className="text-sm text-destructive">
            {errors.email.message}
          </p>
        ) : null}
      </div>

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
      >
        {isSubmitting ? "Sending…" : "Send reset link"}
      </button>

      <p className="text-center text-sm text-muted-foreground">
        Remember your password?{" "}
        <Link href="/login" className="font-medium underline underline-offset-4">
          Sign in
        </Link>
      </p>
    </form>
  );
}
