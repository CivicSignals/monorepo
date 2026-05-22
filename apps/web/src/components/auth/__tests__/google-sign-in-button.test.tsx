// @vitest-environment jsdom
// B2 — Render + href test for the GoogleSignInButton component.

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { GoogleSignInButton } from "../google-sign-in-button";
import { googleOAuthStartUrl } from "@/lib/auth-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("GoogleSignInButton", () => {
  it("renders with the default 'Sign in' label", () => {
    render(<GoogleSignInButton />, { wrapper });
    const btn = screen.getByTestId("google-sign-in-button");
    expect(btn).toBeTruthy();
    expect(btn.textContent).toContain("Sign in with Google");
  });

  it("renders with a custom label", () => {
    render(<GoogleSignInButton label="Sign up" />, { wrapper });
    const btn = screen.getByTestId("google-sign-in-button");
    expect(btn.textContent).toContain("Sign up with Google");
  });

  it("has href pointing at the API OAuth start URL", () => {
    render(<GoogleSignInButton />, { wrapper });
    const btn = screen.getByTestId("google-sign-in-button") as HTMLAnchorElement;
    expect(btn.href).toBe(googleOAuthStartUrl());
  });

  it("is rendered as an anchor (full navigation, not fetch)", () => {
    render(<GoogleSignInButton />, { wrapper });
    const btn = screen.getByTestId("google-sign-in-button");
    expect(btn.tagName.toLowerCase()).toBe("a");
  });
});

describe("LoginForm includes Google button", () => {
  it("renders the Google sign-in button on the login form", async () => {
    const { LoginForm } = await import("../login-form");
    render(<LoginForm />, { wrapper });
    expect(screen.getByTestId("google-sign-in-button")).toBeTruthy();
  });
});

describe("SignupForm includes Google button", () => {
  it("renders the Google sign-up button on the signup form", async () => {
    const { SignupForm } = await import("../signup-form");
    render(<SignupForm />, { wrapper });
    const btn = screen.getByTestId("google-sign-in-button");
    expect(btn).toBeTruthy();
    expect(btn.textContent).toContain("Sign up with Google");
  });
});
