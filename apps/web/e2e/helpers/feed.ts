// Feed page-object for the full-stack e2e specs.
//
// A thin wrapper over the /feed list and the /signals/[id] detail page that
// speaks only in `data-testid` selectors (the rule — see
// `docs/testing/e2e-rules.md`). It encodes the few interactions the routing
// spec needs: navigate to the feed, enumerate visible rows, assert a signal is
// present / absent by title, open a row's detail page, and read the
// "Why this signal?" bullets there.
//
// Selectors used (all already on the components):
//   feed-list / feed-item / feed-item-link / feed-item-entity / feed-item-score
//   feed-empty / feed-empty-filtered / feed-auth-required
//   why-this-signal / why-bullet  (on the detail page)

import type { Locator, Page } from "@playwright/test";
import { expect } from "@playwright/test";

export class FeedPage {
  constructor(private readonly page: Page) {}

  /** Navigate to the feed and wait until it has settled into a known state. */
  async goto(): Promise<void> {
    await this.page.goto("/feed");
    await this.waitForSettled();
  }

  /**
   * Wait until the feed has resolved to one of its terminal states: a populated
   * list, an empty state, or the auth-required prompt. Guards against asserting
   * on a still-loading skeleton.
   */
  async waitForSettled(): Promise<void> {
    const list = this.page.getByTestId("feed-list");
    const empty = this.page.getByTestId("feed-empty");
    const emptyFiltered = this.page.getByTestId("feed-empty-filtered");
    const authRequired = this.page.getByTestId("feed-auth-required");
    await expect(
      list.or(empty).or(emptyFiltered).or(authRequired).first(),
    ).toBeVisible();
  }

  /** All currently-rendered feed rows. */
  items(): Locator {
    return this.page.getByTestId("feed-item");
  }

  /** The visible row titles, in feed (score-desc) order. */
  async itemTitles(): Promise<string[]> {
    await this.waitForSettled();
    const links = this.page.getByTestId("feed-item-link");
    return links.allInnerTexts();
  }

  /** Locator for a single feed row matched by its (exact) signal title. */
  itemByTitle(title: string): Locator {
    return this.items().filter({
      has: this.page.getByTestId("feed-item-link").getByText(title, { exact: true }),
    });
  }

  /** Assert a signal with the given title is present in the feed. */
  async expectSignalVisible(title: string): Promise<void> {
    await this.waitForSettled();
    await expect(
      this.page.getByTestId("feed-item-link").getByText(title, { exact: true }),
    ).toBeVisible();
  }

  /** Assert no feed row carries the given title (the threshold/routing gate). */
  async expectSignalAbsent(title: string): Promise<void> {
    await this.waitForSettled();
    await expect(
      this.page.getByTestId("feed-item-link").getByText(title, { exact: true }),
    ).toHaveCount(0);
  }

  /**
   * Open a signal's detail page by clicking its feed-row link, then wait for the
   * detail island to render. Returns a {@link SignalDetailPage} bound to it.
   */
  async openSignal(title: string): Promise<SignalDetailPage> {
    await this.waitForSettled();
    await this.page
      .getByTestId("feed-item-link")
      .getByText(title, { exact: true })
      .click();
    await expect(this.page).toHaveURL(/\/signals\//);
    const detail = new SignalDetailPage(this.page);
    await detail.waitForLoaded();
    return detail;
  }
}

/** Page-object for the /signals/[id] detail page. */
export class SignalDetailPage {
  constructor(private readonly page: Page) {}

  async waitForLoaded(): Promise<void> {
    await expect(this.page.getByTestId("signal-detail")).toBeVisible();
  }

  /** The signal title shown in the detail header. */
  title(): Locator {
    return this.page.getByTestId("signal-title");
  }

  /** The "Why this signal?" panel section. */
  whyPanel(): Locator {
    return this.page.getByTestId("why-this-signal");
  }

  /**
   * The text of every "Why this signal?" bullet (sentence-cased by the UI from
   * the scorer's structured bullets — e.g. "Matched state TX").
   */
  async whyBullets(): Promise<string[]> {
    await expect(this.whyPanel()).toBeVisible();
    return this.page.getByTestId("why-bullet").allInnerTexts();
  }
}
