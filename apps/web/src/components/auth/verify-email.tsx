"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { ProblemError } from "@/lib/auth-api";
import { useVerifyEmail } from "@/hooks/use-auth";

export function VerifyEmail() {
  const params = useSearchParams();
  const token = params.get("token");
  const verify = useVerifyEmail();
  const triggered = useRef(false);

  useEffect(() => {
    if (token && !triggered.current) {
      triggered.current = true;
      verify.mutate(token);
    }
  }, [token, verify]);

  if (!token) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="verify-missing-token"
      >
        <h1 className="text-2xl font-bold tracking-tight">Missing token</h1>
        <p className="text-sm text-muted-foreground">
          This page expects a verification link from your email.
        </p>
      </div>
    );
  }

  if (verify.isSuccess) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="verify-success"
      >
        <h1 className="text-2xl font-bold tracking-tight">Email verified</h1>
        <p className="text-sm text-muted-foreground">
          Your email address is confirmed.
        </p>
        <Link
          href="/login"
          className="text-sm font-medium underline underline-offset-4"
        >
          Continue to sign in
        </Link>
      </div>
    );
  }

  if (verify.isError) {
    const detail =
      verify.error instanceof ProblemError
        ? verify.error.problem.detail
        : verify.error.message;
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="verify-error"
      >
        <h1 className="text-2xl font-bold tracking-tight">
          Verification failed
        </h1>
        <p role="alert" className="text-sm text-destructive">
          {detail ??
            "This verification link is invalid, expired, or already used."}
        </p>
        <Link
          href="/signup"
          className="text-sm font-medium underline underline-offset-4"
        >
          Back to sign up
        </Link>
      </div>
    );
  }

  return (
    <div
      className="w-full max-w-sm space-y-3 text-center"
      data-testid="verify-pending"
    >
      <h1 className="text-2xl font-bold tracking-tight">Verifying…</h1>
      <p className="text-sm text-muted-foreground">
        Confirming your email address.
      </p>
    </div>
  );
}
