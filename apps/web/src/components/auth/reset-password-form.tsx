"use client";

// B3: Reset-password form — POST /auth/password-reset/confirm.
// Reads the token from the URL query string (?token=...) and lets the user set
// a new password. Rejects expired / already-used tokens with a clear error.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type ResetPasswordValues, resetPasswordSchema } from "@/lib/auth-schemas";
import { ProblemError } from "@/lib/auth-api";
import { usePasswordResetConfirm } from "@/hooks/use-auth";

export function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const resetConfirm = usePasswordResetConfirm();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ResetPasswordValues>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: { token },
  });

  const onSubmit = handleSubmit(async (values) => {
    try {
      await resetConfirm.mutateAsync({
        token: values.token,
        new_password: values.new_password,
      });
    } catch {
      // Error is captured in resetConfirm.error; isSubmitting resets cleanly.
    }
  });

  if (resetConfirm.isSuccess) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="reset-password-success"
      >
        <h1 className="text-2xl font-bold tracking-tight">Password updated</h1>
        <p className="text-sm text-muted-foreground">
          Your password has been reset. You can now sign in with your new
          password.
        </p>
        <p className="text-sm text-muted-foreground">
          <Link href="/login" className="font-medium underline underline-offset-4">
            Sign in
          </Link>
        </p>
      </div>
    );
  }

  const apiError =
    resetConfirm.error instanceof ProblemError
      ? resetConfirm.error.problem.detail
      : resetConfirm.error?.message;

  if (!token) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="reset-password-no-token"
      >
        <h1 className="text-2xl font-bold tracking-tight">Invalid link</h1>
        <p className="text-sm text-muted-foreground">
          This password-reset link is invalid or has expired.{" "}
          <Link
            href="/forgot-password"
            className="font-medium underline underline-offset-4"
          >
            Request a new one
          </Link>
          .
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="w-full max-w-sm space-y-4">
      <div className="space-y-1 text-center">
        <h1 className="text-2xl font-bold tracking-tight">Set new password</h1>
        <p className="text-sm text-muted-foreground">
          Choose a new password for your CivicSignals account.
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

      {/* Hidden token field — value comes from URL, submitted with the form */}
      <input type="hidden" {...register("token")} />

      <div className="space-y-1">
        <label htmlFor="new_password" className="text-sm font-medium">
          New password
        </label>
        <input
          id="new_password"
          type="password"
          autoComplete="new-password"
          aria-invalid={errors.new_password ? "true" : undefined}
          aria-describedby={errors.new_password ? "new-password-error" : undefined}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("new_password")}
        />
        {errors.new_password ? (
          <p
            id="new-password-error"
            role="alert"
            className="text-sm text-destructive"
          >
            {errors.new_password.message}
          </p>
        ) : null}
      </div>

      <div className="space-y-1">
        <label htmlFor="confirm_password" className="text-sm font-medium">
          Confirm password
        </label>
        <input
          id="confirm_password"
          type="password"
          autoComplete="new-password"
          aria-invalid={errors.confirm_password ? "true" : undefined}
          aria-describedby={
            errors.confirm_password ? "confirm-password-error" : undefined
          }
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("confirm_password")}
        />
        {errors.confirm_password ? (
          <p
            id="confirm-password-error"
            role="alert"
            className="text-sm text-destructive"
          >
            {errors.confirm_password.message}
          </p>
        ) : null}
      </div>

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
      >
        {isSubmitting ? "Saving…" : "Set new password"}
      </button>
    </form>
  );
}
