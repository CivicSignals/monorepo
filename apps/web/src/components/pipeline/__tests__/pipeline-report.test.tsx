// @vitest-environment jsdom
// J5 — PipelineReportView: renders stage totals + summary; handles edge cases.

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

afterEach(cleanup);
import { PipelineReportView } from "../pipeline-report";
import type { PipelineReport } from "@/lib/pipeline-api";

// ---- Helpers ----------------------------------------------------------------

function makeReport(over: Partial<PipelineReport> = {}): PipelineReport {
  return {
    stages: [
      {
        stage_id: "00000000-0000-7000-8000-000000000001",
        stage_name: "Saved",
        stage_position: 0,
        item_count: 3,
        total_value: "150000.00",
      },
      {
        stage_id: "00000000-0000-7000-8000-000000000002",
        stage_name: "Researching",
        stage_position: 1,
        item_count: 1,
        total_value: "50000.50",
      },
      {
        stage_id: "00000000-0000-7000-8000-000000000003",
        stage_name: "Won",
        stage_position: 6,
        item_count: 0,
        total_value: null,
      },
    ],
    total_items: 4,
    total_value: "200000.50",
    ...over,
  };
}

// ---- Summary cards ----------------------------------------------------------

describe("PipelineReportView — summary cards", () => {
  it("shows total item count", () => {
    render(<PipelineReportView report={makeReport()} />);
    const card = screen.getByTestId("summary-total-items");
    expect(card.textContent).toContain("4");
  });

  it("shows formatted total value with currency symbol", () => {
    render(<PipelineReportView report={makeReport()} />);
    const card = screen.getByTestId("summary-total-value");
    expect(card.textContent).toMatch(/\$[\d,]+/);
  });

  it("shows em dash when total_value is null", () => {
    render(<PipelineReportView report={makeReport({ total_value: null })} />);
    const card = screen.getByTestId("summary-total-value");
    expect(card.textContent).toContain("—");
  });
});

// ---- Bar chart --------------------------------------------------------------

describe("PipelineReportView — bar chart", () => {
  it("renders a bar for each stage", () => {
    render(<PipelineReportView report={makeReport()} />);
    expect(screen.getByTestId("bar-Saved")).toBeTruthy();
    expect(screen.getByTestId("bar-Researching")).toBeTruthy();
    expect(screen.getByTestId("bar-Won")).toBeTruthy();
  });

  it("bar title shows count and value for Saved", () => {
    render(<PipelineReportView report={makeReport()} />);
    const savedBar = screen.getByTestId("bar-Saved");
    const title = savedBar.getAttribute("title") ?? "";
    expect(title).toContain("Saved");
    expect(title).toContain("3 items");
    expect(title).toMatch(/\$150,000/);
  });

  it("bar title for stage with no value omits currency", () => {
    render(<PipelineReportView report={makeReport()} />);
    const wonBar = screen.getByTestId("bar-Won");
    const title = wonBar.getAttribute("title") ?? "";
    expect(title).toContain("Won");
    expect(title).not.toMatch(/\$/);
  });

  it("renders chart aria label", () => {
    render(<PipelineReportView report={makeReport()} />);
    expect(
      screen.getByRole("img", { name: /Pipeline stage bar chart/i }),
    ).toBeTruthy();
  });

  it("shows 'No stages configured' when stages list is empty", () => {
    render(<PipelineReportView report={makeReport({ stages: [] })} />);
    expect(screen.getByText(/No stages configured/i)).toBeTruthy();
  });
});

// ---- Stage summary table ----------------------------------------------------

describe("PipelineReportView — stage summary table", () => {
  it("renders a row for each stage", () => {
    render(<PipelineReportView report={makeReport()} />);
    const table = screen.getByRole("table", { name: /Stage summary table/i });
    const rows = table.querySelectorAll("tbody tr");
    expect(rows.length).toBe(3);
  });

  it("shows stage names in the table", () => {
    render(<PipelineReportView report={makeReport()} />);
    // Stage names appear in both the chart labels and the table — findAll and
    // assert at least one occurrence.
    expect(screen.getAllByText("Saved").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Researching").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Won").length).toBeGreaterThan(0);
  });

  it("shows em dash for stages with no value", () => {
    render(<PipelineReportView report={makeReport()} />);
    // Won stage has total_value: null → em dash in the table.
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });

  it("singular 'item' for count of 1", () => {
    render(<PipelineReportView report={makeReport()} />);
    const researchBar = screen.getByTestId("bar-Researching");
    const title = researchBar.getAttribute("title") ?? "";
    // "1 item" (no trailing s)
    expect(title).toMatch(/1 item[^s]/);
  });
});

// ---- Zero-item workspace ---------------------------------------------------

describe("PipelineReportView — empty workspace", () => {
  it("shows 0 total items in the summary card", () => {
    const emptyReport = makeReport({
      stages: [
        {
          stage_id: "00000000-0000-7000-8000-000000000001",
          stage_name: "Saved",
          stage_position: 0,
          item_count: 0,
          total_value: null,
        },
      ],
      total_items: 0,
      total_value: null,
    });
    render(<PipelineReportView report={emptyReport} />);
    const card = screen.getByTestId("summary-total-items");
    expect(card.textContent).toContain("0");
  });
});
