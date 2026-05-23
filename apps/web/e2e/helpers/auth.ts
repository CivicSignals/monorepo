// Auth helper for the full-stack e2e specs.
//
// `loginAs` drives the REAL login page (no API shortcut, no token injection):
// it fills the form, submits, and waits until the app is authenticated AND the
// user's workspace is active. "Active workspace" matters because the feed is
// workspace-scoped — `useWorkspaceFeed` only fetches once BOTH the bearer token
// (Zustand `cs.session`) and `activeWorkspaceId` (Zustand `cs.ui`) are present.
// The header `WorkspaceSwitcher` auto-selects the user's first workspace via
// `useActiveWorkspace`, persisting `activeWorkspaceId` to localStorage; we wait
// for that select to render with the expected workspace before returning.
//
// storageState reuse: after a successful login `loginAs` writes the page's
// storageState (token + active-workspace localStorage) to `e2e/.auth/<key>.json`.
// A spec can hand that file to a fresh browser context to skip the form on a
// rerun — see `restoredContextState()`. The directory is gitignored and is a
// per-run cache, so a token never leaks across runs.

import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import type { SeededUser } from "./users";

const here = path.dirname(fileURLToPath(import.meta.url));
/** Per-run storageState cache (gitignored; one file per seeded user). */
export const AUTH_DIR = path.join(here, "..", ".auth");

/** Path to a seeded user's cached storageState for this run. */
export function storageStatePath(userKey: string): string {
  return path.join(AUTH_DIR, `${userKey}.json`);
}

/**
 * Path to pass as `storageState` when creating a context, IF a cached state for
 * this user exists for this run (else `undefined` → a fresh, signed-out context
 * that must call `loginAs`). Lets a spec opt into reuse without branching:
 *
 *   const ctx = await browser.newContext({ storageState: restoredContextState(ALICE) });
 */
export function restoredContextState(user: SeededUser): string | undefined {
  const p = storageStatePath(user.key);
  return existsSync(p) ? p : undefined;
}

/**
 * Log in as a seeded user via the real login page and land authenticated with
 * the user's workspace active. Returns the active workspace's display name.
 *
 * After success it persists the resolved session to `e2e/.auth/<key>.json` so a
 * later context can be created with `restoredContextState(user)` and skip the
 * form. If the page already arrives authenticated (a restored context), the
 * login form is skipped and only the workspace-active check runs.
 */
export async function loginAs(page: Page, user: SeededUser): Promise<string> {
  await page.goto("/login");

  // A restored (already-authenticated) context renders the success panel, not
  // the form — short-circuit straight to the workspace check.
  const form = page.getByTestId("login-form");
  const alreadyAuthed = await page
    .getByTestId("login-success")
    .isVisible()
    .catch(() => false);

  if (!alreadyAuthed) {
    await expect(form).toBeVisible();
    await page.getByTestId("login-email").fill(user.email);
    await page.getByTestId("login-password").fill(user.password);
    await page.getByTestId("login-submit").click();

    // The form swaps to a success panel echoing the signed-in email — proves
    // the bearer token reached the session store before we navigate on.
    const success = page.getByTestId("login-success");
    await expect(success).toBeVisible();
    await expect(success).toHaveAttribute("data-signed-in-email", user.email);
  }

  const workspaceName = await ensureWorkspaceActive(page, user.workspaceName);

  // Persist the resolved session for cheap reuse later in this run.
  if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true });
  await page.context().storageState({ path: storageStatePath(user.key) });

  return workspaceName;
}

/**
 * Wait for the header workspace switcher to render the user's workspace and for
 * `activeWorkspaceId` to be persisted, so workspace-scoped queries will fire.
 * Navigates to the feed (which mounts the authed header), confirms the switcher
 * shows the expected workspace, and returns that name.
 *
 * The seeded users each have exactly one workspace, so `useActiveWorkspace`'s
 * "default to the first workspace" path selects the right one with no extra
 * interaction. Selecting by label is explicit and also correct if a user ever
 * gains more than one workspace.
 */
export async function ensureWorkspaceActive(
  page: Page,
  expectedName: string,
): Promise<string> {
  await page.goto("/feed");

  const switcher = page.getByTestId("workspace-switcher");
  await expect(switcher).toBeVisible();

  const select = switcher.getByLabel("Active workspace");
  await expect(select).toBeVisible();
  await select.selectOption({ label: expectedName });
  // The selected value is a real workspace id (UUID), never the empty string.
  await expect(select).toHaveValue(/.+/);

  return expectedName;
}
