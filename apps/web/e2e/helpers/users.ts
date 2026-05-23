// Seeded e2e users + expected signals — the single TS mirror of the backend
// scenario manifest (`apps/api/tests/e2e_fixtures/scenarios.yaml`).
//
// The full-stack specs run against a stack seeded by
// `civicsignals_api.scripts.seed_e2e`, which reads that YAML. These constants
// are the *contract* between the seeder and the Playwright assertions: the
// emails / passwords below are the exact credentials the seeder get-or-creates,
// and the titles are the exact `title` fields the real extraction pipeline
// produces from each scenario's `expected-signal.json` golden fixture.
//
// If you change a credential, ICP, or expected title here you MUST change it in
// scenarios.yaml (and the matching fixture) too — they are kept deliberately
// duplicated rather than generated so a drift between backend seed and frontend
// assertion fails loudly instead of silently passing. See
// `docs/testing/e2e-rules.md` ("Adding a new scenario").

/** A seeded e2e user: real login credentials + the one signal it should see. */
export interface SeededUser {
  /** Stable token used in test names / storageState filenames. */
  readonly key: string;
  /** Login email — matches scenarios.yaml `users[].email`. */
  readonly email: string;
  /** Login password — matches scenarios.yaml `users[].password`. */
  readonly password: string;
  /** The workspace the seeder creates for this user (its only workspace). */
  readonly workspaceName: string;
  /** Title of the signal that scores into this user's feed. */
  readonly expectedSignalTitle: string;
  /** Signal type of that signal (also a feed type-filter value). */
  readonly expectedSignalType: string;
}

// Alice → TX / school_district / rfp_posted. Sees the TX school RFP.
export const ALICE: SeededUser = {
  key: "alice",
  email: "alice.e2e@civicsignals.test",
  password: "e2e-password-alice",
  workspaceName: "Alice TX Workspace",
  // tx_school_rfp/expected-signal.json `title`.
  expectedSignalTitle: "RFP 2026-014: District-wide ERP Modernization",
  expectedSignalType: "rfp_posted",
};

// Bob → CA / news_mention. Sees the CA county news.
export const BOB: SeededUser = {
  key: "bob",
  email: "bob.e2e@civicsignals.test",
  password: "e2e-password-bob",
  workspaceName: "Bob CA Workspace",
  // ca_county_news/expected-signal.json `title`.
  expectedSignalTitle: "Sierra County weighs IT modernization push",
  expectedSignalType: "news_mention",
};

/**
 * The weak_match scenario's signal title (weak_match/expected-signal.json).
 * It exists globally but its VT / library_system entity matches neither ICP, so
 * it must be ABSENT from both feeds (the threshold / pre-filter gate proof).
 */
export const WEAK_MATCH_SIGNAL_TITLE =
  "Green Mountain library renames its reading room";
