// Auth API client for the web app (B1).
//
// Talks to the FastAPI auth endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1). TanStack Query owns the *server* state
// (see src/hooks/use-auth.ts); this module is the thin transport. Errors are
// RFC 7807 application/problem+json (doc 08 §1.7) and surfaced as ProblemError.

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export interface User {
  id: string;
  email: string;
  name: string | null;
  email_verified: boolean;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthResponse {
  user: User;
  tokens: TokenPair;
}

export interface SignupResponse extends AuthResponse {
  email_verification_required: boolean;
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  errors?: { field: string; code: string; message: string }[];
}

/** Error carrying the parsed RFC 7807 problem body. */
export class ProblemError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title);
    this.name = "ProblemError";
    this.problem = problem;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (rest.body !== undefined) {
    merged.set("Content-Type", "application/json");
  }
  if (token) merged.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: merged,
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({
      type: "about:blank",
      title: res.statusText,
      status: res.status,
    }))) as Problem;
    throw new ProblemError(problem);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SignupInput {
  email: string;
  password: string;
  name?: string;
}

export function signup(input: SignupInput): Promise<SignupResponse> {
  return request<SignupResponse>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export interface LoginInput {
  email: string;
  password: string;
}

export function login(input: LoginInput): Promise<AuthResponse> {
  return request<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function logout(token?: string): Promise<{ message: string }> {
  return request<{ message: string }>("/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
    token,
  });
}

export function verifyEmail(
  verificationToken: string,
): Promise<{ message: string }> {
  return request<{ message: string }>("/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token: verificationToken }),
  });
}

export function getMe(token: string): Promise<User> {
  return request<User>("/auth/me", { token });
}

// --- B2: Google OAuth2 -------------------------------------------------------

/**
 * Return the URL to redirect the browser to in order to start Google sign-in.
 *
 * This does NOT make a fetch call — it returns the API start endpoint URL so
 * the browser can navigate to it directly (which triggers the server-side 302
 * redirect to Google). We construct the URL here so components can use it in
 * anchor ``href``s without needing the API_BASE_URL in JSX.
 */
export function googleOAuthStartUrl(): string {
  return `${API_BASE_URL}/auth/oauth/google/start`;
}

// --- B3: Password reset -------------------------------------------------------

export interface PasswordResetRequestInput {
  email: string;
}

/** POST /auth/password-reset/request — always 204, no user enumeration. */
export function passwordResetRequest(
  input: PasswordResetRequestInput,
): Promise<void> {
  return request<void>("/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export interface PasswordResetConfirmInput {
  token: string;
  new_password: string;
}

/** POST /auth/password-reset/confirm — 200 on success, 400 on bad token. */
export function passwordResetConfirm(
  input: PasswordResetConfirmInput,
): Promise<{ message: string }> {
  return request<{ message: string }>("/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
