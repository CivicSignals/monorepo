// @vitest-environment jsdom
// F4 — WhyThisSignal: renders human-readable bullets + component contributions from
// the score_breakdown JSONB, derives bullets from the matched flags when none are
// pre-formatted, and degrades gracefully for no-score / empty breakdowns.

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { WhyThisSignal } from "@/app/signals/[id]/why-this-signal";
import type { ScoreBreakdown } from "@/lib/signals-api";

afterEach(cleanup);

// A representative breakdown matching workspace_scoring.score_signal_against_icp.
const fullBreakdown: ScoreBreakdown = {
  components: {
    signal_type_weight: { value: 0.8, weight: 0.3, points: 24 },
    dimensions: { value: 1, weight: 0.25, points: 25 },
    recency: { value: 0.6, weight: 0.1, points: 6 },
    confidence: { value: 0.9, weight: 0.1, points: 9 },
    keywords: { value: 0.5, weight: 0.2, points: 10 },
  },
  matched: {
    signal_type: true,
    country: true,
    state: true,
    entity_kind: false,
    size_band: false,
    states: ["WA"],
    keywords: ["budget"],
  },
  bullets: [
    "signal type rfp_posted is of interest",
    "matched state WA",
    "keyword 'budget' in signal text",
  ],
};

describe("WhyThisSignal — populated breakdown", () => {
  it("renders the pre-formatted bullets, sentence-cased", () => {
    render(<WhyThisSignal breakdown={fullBreakdown} score={74} />);
    const bullets = screen.getAllByTestId("why-bullet").map((b) => b.textContent);
    expect(bullets).toContain("Signal type rfp_posted is of interest");
    expect(bullets).toContain("Matched state WA");
    expect(bullets).toContain("Keyword 'budget' in signal text");
  });

  it("shows the workspace score in the panel", () => {
    render(<WhyThisSignal breakdown={fullBreakdown} score={74} />);
    expect(screen.getByTestId("why-this-signal").textContent).toContain("74/100");
  });

  it("renders component contributions sorted by points desc, dropping zero ones", () => {
    render(<WhyThisSignal breakdown={fullBreakdown} score={74} />);
    const rows = screen.getAllByTestId("why-component").map((r) => r.textContent ?? "");
    // Dimensions (25) leads; signal type (24) next.
    expect(rows[0]).toContain("ICP dimensions");
    expect(rows[0]).toContain("+25");
    expect(rows[1]).toContain("Signal type");
    // Component labels are humanised, not raw snake_case.
    expect(rows.join(" ")).not.toContain("signal_type_weight");
  });
});

describe("WhyThisSignal — fallback bullets", () => {
  it("derives bullets from matched flags when no pre-formatted bullets exist", () => {
    const noBullets: ScoreBreakdown = {
      components: { dimensions: { points: 25 } },
      matched: { signal_type: true, states: ["OR"], keywords: ["grant"] },
    };
    render(<WhyThisSignal breakdown={noBullets} score={60} />);
    const bullets = screen.getAllByTestId("why-bullet").map((b) => b.textContent);
    expect(bullets).toContain("Signal type is of interest");
    expect(bullets).toContain("Matched state OR");
    expect(bullets).toContain("Matched keyword 'grant'");
  });
});

describe("WhyThisSignal — degraded states", () => {
  it("shows a no-score message when the signal has no workspace score", () => {
    render(<WhyThisSignal breakdown={null} score={null} />);
    expect(screen.getByTestId("why-no-score")).toBeTruthy();
    expect(screen.queryByTestId("why-bullet")).toBeNull();
  });

  it("shows an empty-but-scored message for a breakdown with no content", () => {
    render(<WhyThisSignal breakdown={{}} score={55} />);
    expect(screen.getByTestId("why-empty").textContent).toContain("55/100");
    expect(screen.queryByTestId("why-bullet")).toBeNull();
  });

  it("still renders the heading in every state", () => {
    render(<WhyThisSignal breakdown={null} score={null} />);
    expect(screen.getByText("Why this signal?")).toBeTruthy();
  });
});
