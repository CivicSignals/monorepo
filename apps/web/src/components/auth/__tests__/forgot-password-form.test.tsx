// @vitest-environment jsdom
// B3 — Render + validation + submit tests for the forgot-password form.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ForgotPasswordForm } from "../forgot-password-form";

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

describe("ForgotPasswordForm", () => {
  it("renders the email field", () => {
    render(<ForgotPasswordForm />, { wrapper });
    expect(screen.getByLabelText(/email/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /send reset link/i }),
    ).toBeTruthy();
  });

  it("shows a validation error for an invalid email and does not call the API", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<ForgotPasswordForm />, { wrapper });

    await user.type(screen.getByLabelText(/email/i), "not-an-email");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByText(/valid email/i)).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("submits a valid email and shows the check-your-email message", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    const user = userEvent.setup();
    render(<ForgotPasswordForm />, { wrapper });

    await user.type(
      screen.getByLabelText(/email/i),
      "someone@example.com",
    );
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    await waitFor(() =>
      expect(screen.getByTestId("forgot-password-success")).toBeTruthy(),
    );
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain("/auth/password-reset/request");
    expect(init?.method).toBe("POST");
  });
});
