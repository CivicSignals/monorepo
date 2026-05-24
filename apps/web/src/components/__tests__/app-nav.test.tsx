// @vitest-environment jsdom
// Render test for the authed-app nav island: it shows the Feed (G1) + other
// authed destinations only when a session exists, and nothing when signed out.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AppNav } from "../app-nav";
import { useSessionStore } from "@/store/session";
import type { User } from "@/lib/auth-api";

const USER: User = {
  id: "00000000-0000-7000-8000-000000000001",
  email: "ops@example.gov",
  name: "Ops User",
  email_verified: true,
  created_at: "2026-05-22T00:00:00Z",
};

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ accessToken: null, user: null });
});

afterEach(() => {
  cleanup();
  useSessionStore.setState({ accessToken: null, user: null });
});

describe("AppNav", () => {
  it("renders nothing when signed out", () => {
    const { container } = render(
      <ul>
        <AppNav />
      </ul>,
    );
    expect(container.querySelector("[data-testid='nav-feed']")).toBeNull();
  });

  it("renders the Feed link (and other authed destinations) when signed in", () => {
    useSessionStore.setState({ accessToken: "jwt-access", user: USER });

    render(
      <ul>
        <AppNav />
      </ul>,
    );

    const feed = screen.getByTestId("nav-feed");
    expect(feed).toBeTruthy();
    expect(feed.getAttribute("href")).toBe("/feed");

    expect(screen.getByTestId("nav-pipeline").getAttribute("href")).toBe(
      "/pipeline",
    );
    expect(screen.getByTestId("nav-directory").getAttribute("href")).toBe(
      "/directory",
    );
    expect(screen.getByTestId("nav-icp").getAttribute("href")).toBe("/icp/new");
    expect(screen.getByTestId("nav-settings").getAttribute("href")).toBe(
      "/settings/integrations",
    );
  });
});
