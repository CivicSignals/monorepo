// @vitest-environment jsdom
// G5 — reusable empty / error / auth-required state primitives.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import {
  AuthRequiredState,
  EmptyState,
  ErrorState,
} from "@/components/ui/states";

afterEach(cleanup);

describe("EmptyState", () => {
  it("renders the title, description, and an action", () => {
    render(
      <EmptyState
        testId="empty"
        title="No signals yet"
        description="They'll show up once scoring runs."
        action={<button type="button">Do thing</button>}
      />,
    );
    const el = screen.getByTestId("empty");
    expect(el.textContent).toContain("No signals yet");
    expect(el.textContent).toContain("They'll show up once scoring runs.");
    expect(screen.getByRole("button", { name: "Do thing" })).toBeTruthy();
  });
});

describe("ErrorState", () => {
  it("renders as an alert with the message", () => {
    render(<ErrorState testId="err" message="Boom" />);
    const el = screen.getByTestId("err");
    expect(el.getAttribute("role")).toBe("alert");
    expect(el.textContent).toContain("Boom");
  });

  it("renders a retry button only when onRetry is given, and calls it", () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorState testId="err" message="Boom" />);
    expect(screen.queryByTestId("error-retry")).toBeNull();

    rerender(<ErrorState testId="err" message="Boom" onRetry={onRetry} />);
    fireEvent.click(screen.getByTestId("error-retry"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("disables the retry button while retrying", () => {
    render(
      <ErrorState testId="err" message="Boom" onRetry={vi.fn()} retrying />,
    );
    const btn = screen.getByTestId("error-retry") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain("Retrying");
  });
});

describe("AuthRequiredState", () => {
  it("renders a sign-in link to the given href", () => {
    render(<AuthRequiredState testId="auth" href="/login" />);
    const el = screen.getByTestId("auth");
    expect(el.getAttribute("role")).toBe("status");
    const link = el.querySelector("a");
    expect(link?.getAttribute("href")).toBe("/login");
  });
});
