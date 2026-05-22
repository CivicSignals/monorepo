// @vitest-environment jsdom
// B3 — Render + validation + submit tests for the reset-password form.
//
// useSearchParams() requires next/navigation which we mock out below.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ResetPasswordForm } from "../reset-password-form";

// Mock next/navigation so useSearchParams works in jsdom.
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("token=test-reset-token-abc"),
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
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

describe("ResetPasswordForm", () => {
  it("renders the new-password and confirm-password fields", () => {
    render(<ResetPasswordForm />, { wrapper });
    expect(screen.getByLabelText(/new password/i)).toBeTruthy();
    expect(screen.getByLabelText(/confirm password/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /set new password/i }),
    ).toBeTruthy();
  });

  it("shows a validation error when passwords do not match", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<ResetPasswordForm />, { wrapper });

    await user.type(
      screen.getByLabelText(/new password/i),
      "s3cure-pa55word",
    );
    await user.type(screen.getByLabelText(/confirm password/i), "different99!");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByText(/do not match/i)).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows a validation error for a short password", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<ResetPasswordForm />, { wrapper });

    await user.type(screen.getByLabelText(/new password/i), "short");
    await user.type(screen.getByLabelText(/confirm password/i), "short");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByText(/at least 8 characters/i)).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("submits valid passwords and shows the success state", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: "password reset" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const user = userEvent.setup();
    render(<ResetPasswordForm />, { wrapper });

    await user.type(
      screen.getByLabelText(/new password/i),
      "new-p@ssword99!",
    );
    await user.type(
      screen.getByLabelText(/confirm password/i),
      "new-p@ssword99!",
    );
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    await waitFor(() =>
      expect(screen.getByTestId("reset-password-success")).toBeTruthy(),
    );
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain("/auth/password-reset/confirm");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(init?.body as string);
    expect(body.token).toBe("test-reset-token-abc");
    expect(body.new_password).toBe("new-p@ssword99!");
  });

  it("shows an API error when the token is invalid", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "https://civicsignals.io/errors/invalid_reset_token",
          title: "Invalid password-reset token",
          status: 400,
          detail: "This reset link is invalid, expired, or already used.",
        }),
        {
          status: 400,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    );

    const user = userEvent.setup();
    render(<ResetPasswordForm />, { wrapper });

    await user.type(
      screen.getByLabelText(/new password/i),
      "new-p@ssword99!",
    );
    await user.type(
      screen.getByLabelText(/confirm password/i),
      "new-p@ssword99!",
    );
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/invalid, expired, or already used/i),
      ).toBeTruthy(),
    );
  });
});
