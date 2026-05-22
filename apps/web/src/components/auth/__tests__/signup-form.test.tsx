// @vitest-environment jsdom
// B1 — Render + validation + submit test for the signup form.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { SignupForm } from "../signup-form";

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

describe("SignupForm", () => {
  it("renders the email and password fields", () => {
    render(<SignupForm />, { wrapper });
    expect(screen.getByLabelText(/email/i)).toBeTruthy();
    expect(screen.getByLabelText(/^password$/i)).toBeTruthy();
  });

  it("shows a validation error for a short password and does not call the API", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<SignupForm />, { wrapper });

    await user.type(screen.getByLabelText(/email/i), "maya@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "short");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText(/at least 8 characters/i)).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("submits a valid form and shows the verification message", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          user: {
            id: "00000000-0000-7000-8000-000000000000",
            email: "maya@example.com",
            name: "Maya",
            email_verified: false,
            created_at: "2026-05-22T00:00:00Z",
          },
          tokens: {
            access_token: "jwt-access",
            refresh_token: "jwt-refresh",
            token_type: "bearer",
            expires_in: 3600,
          },
          email_verification_required: false,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );

    const user = userEvent.setup();
    render(<SignupForm />, { wrapper });

    await user.type(screen.getByLabelText(/email/i), "maya@example.com");
    await user.type(screen.getByLabelText(/^password$/i), "s3cure-pa55word");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() =>
      expect(screen.getByTestId("signup-success")).toBeTruthy(),
    );
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain("/auth/signup");
    expect(init?.method).toBe("POST");
  });
});
