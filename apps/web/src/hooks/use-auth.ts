// Auth hooks (B1) — TanStack Query owns the server interactions (doc 06 §2).
//
// Mutations: signup, login, logout, verify-email. Query: the current user.
// On a successful login/signup the issued bearer token + user are written to the
// client session store (src/store/session.ts).

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type AuthResponse,
  type LoginInput,
  type PasswordResetConfirmInput,
  type PasswordResetRequestInput,
  type SignupInput,
  type SignupResponse,
  type User,
  getMe,
  login as loginApi,
  logout as logoutApi,
  passwordResetConfirm as passwordResetConfirmApi,
  passwordResetRequest as passwordResetRequestApi,
  signup as signupApi,
  verifyEmail as verifyEmailApi,
} from "@/lib/auth-api";
import { useSessionStore } from "@/store/session";

export const ME_QUERY_KEY = ["auth", "me"] as const;

export function useSignup() {
  const setSession = useSessionStore((s) => s.setSession);
  const queryClient = useQueryClient();

  return useMutation<SignupResponse, Error, SignupInput>({
    mutationFn: signupApi,
    onSuccess: (data) => {
      setSession(data.tokens.access_token, data.user);
      queryClient.setQueryData(ME_QUERY_KEY, data.user);
    },
  });
}

export function useLogin() {
  const setSession = useSessionStore((s) => s.setSession);
  const queryClient = useQueryClient();

  return useMutation<AuthResponse, Error, LoginInput>({
    mutationFn: loginApi,
    onSuccess: (data) => {
      setSession(data.tokens.access_token, data.user);
      queryClient.setQueryData(ME_QUERY_KEY, data.user);
    },
  });
}

export function useLogout() {
  const clear = useSessionStore((s) => s.clear);
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();

  return useMutation<{ message: string }, Error, void>({
    mutationFn: () => logoutApi(token ?? undefined),
    onSettled: () => {
      // Bearer JWTs are stateless: clearing client state is the logout, whether
      // or not the server call succeeds.
      clear();
      queryClient.setQueryData(ME_QUERY_KEY, null);
    },
  });
}

export function useVerifyEmail() {
  return useMutation<{ message: string }, Error, string>({
    mutationFn: verifyEmailApi,
  });
}

export function useCurrentUser() {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<User | null>({
    queryKey: ME_QUERY_KEY,
    queryFn: () => (token ? getMe(token) : Promise.resolve(null)),
    enabled: token !== null,
  });
}

// --- B3: Password reset -------------------------------------------------------

/** Always 204 — resolves void on success (no user enumeration). */
export function usePasswordResetRequest() {
  return useMutation<void, Error, PasswordResetRequestInput>({
    mutationFn: passwordResetRequestApi,
  });
}

/** Returns { message: "password reset" } on success, throws ProblemError on bad token. */
export function usePasswordResetConfirm() {
  return useMutation<{ message: string }, Error, PasswordResetConfirmInput>({
    mutationFn: passwordResetConfirmApi,
  });
}
