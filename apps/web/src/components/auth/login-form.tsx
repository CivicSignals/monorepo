"use client";

import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { type LoginValues, loginSchema } from "@/lib/auth-schemas";
import { ProblemError } from "@/lib/auth-api";
import { useLogin } from "@/hooks/use-auth";
import { GoogleSignInButton } from "./google-sign-in-button";

export function LoginForm() {
  const login = useLogin();
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });

  const onSubmit = handleSubmit(async (values) => {
    const res = await login.mutateAsync(values);
    setSignedInEmail(res.user.email);
  });

  if (signedInEmail) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="login-success"
      >
        <h1 className="text-2xl font-bold tracking-tight">Signed in</h1>
        <p className="text-sm text-muted-foreground">
          Welcome back, <strong>{signedInEmail}</strong>.
        </p>
      </div>
    );
  }

  const apiError =
    login.error instanceof ProblemError
      ? login.error.problem.detail
      : login.error?.message;

  return (
    <form onSubmit={onSubmit} noValidate className="w-full max-w-sm space-y-4">
      <div className="space-y-1 text-center">
        <h1 className="text-2xl font-bold tracking-tight">Sign in</h1>
        <p className="text-sm text-muted-foreground">
          Welcome back to CivicSignals.
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

      <div className="space-y-1">
        <label htmlFor="password" className="text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={errors.password ? "true" : undefined}
          aria-describedby={errors.password ? "password-error" : undefined}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          {...register("password")}
        />
        {errors.password ? (
          <p
            id="password-error"
            role="alert"
            className="text-sm text-destructive"
          >
            {errors.password.message}
          </p>
        ) : null}
      </div>

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
      >
        {isSubmitting ? "Signing in…" : "Sign in"}
      </button>

      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t" />
        </div>
        <div className="relative flex justify-center text-xs uppercase">
          <span className="bg-background px-2 text-muted-foreground">or</span>
        </div>
      </div>

      <GoogleSignInButton label="Sign in" />

      <p className="text-center text-sm text-muted-foreground">
        Need an account?{" "}
        <Link
          href="/signup"
          className="font-medium underline underline-offset-4"
        >
          Sign up
        </Link>
      </p>
    </form>
  );
}
