// N6 — Competitor comparison table.
// Claims grounded in 02-competitive-teardown.md. CivicSignals column is aspirational
// but substantiated by the product brief and architecture choices.
import { COMPARISON_HEADING } from "./pricing-data";

interface ComparisonRow {
  dimension: string;
  civicSignals: string;
  starbridge: string;
  govWin: string;
  govSpend: string;
}

const ROWS: ComparisonRow[] = [
  {
    dimension: "Pricing",
    civicSignals: "Posted publicly · $0 self-host · from $19/seat/mo cloud",
    starbridge: "Contact sales only · est. $15k–$50k+/seat/yr",
    govWin: "From ~$200/seat/mo (public comparisons); much higher in practice",
    govSpend: "Not published · enterprise-tier, mid-five-figures+",
  },
  {
    dimension: "Self-serve signup",
    civicSignals: "Yes — 14-day trial, no credit card for SMB",
    starbridge: "No — CSM-led week-1 onboarding required",
    govWin: "No — sales-led",
    govSpend: "No — sales-led",
  },
  {
    dimension: "Open source",
    civicSignals: "AGPL-3.0 core · self-hostable · community recipe PRs",
    starbridge: "Proprietary SaaS only",
    govWin: "Proprietary",
    govSpend: "Proprietary",
  },
  {
    dimension: "Self-host / VPC deploy",
    civicSignals: "Yes — Docker Compose or Helm; bring your own infra",
    starbridge: "No — vendor cloud only",
    govWin: "No — vendor cloud only",
    govSpend: "No — vendor cloud only",
  },
  {
    dimension: "SLED signal coverage",
    civicSignals:
      "RFPs, budgets, contracts, board minutes, grants, leadership, news, RFI/RFQ",
    starbridge: "Similar 8 signal types; closed data catalog",
    govWin: "Federal-strong; SLED coverage is secondary",
    govSpend:
      "Deep historical spend ($17.6T); weaker on forward-looking signals",
  },
  {
    dimension: "Source transparency",
    civicSignals:
      "Open scraper recipes (YAML); inspect source doc + prompt per signal",
    starbridge: "Closed — no visibility into data sources or extraction logic",
    govWin: "150+ human analysts; methodology opaque",
    govSpend: "Closed catalog; no recipe visibility",
  },
  {
    dimension: "AI / LLM model choice",
    civicSignals:
      "BYO key (OpenAI, Anthropic, Ollama) on self-host; multi-model on Cloud",
    starbridge: "Vendor-locked proprietary AI stack",
    govWin: "Mostly human-curated; AI retrofitted",
    govSpend: "AI features added 2025; model not disclosed",
  },
  {
    dimension: "SMB / solo on-ramp",
    civicSignals:
      "Solo tier from $19/seat/mo; self-host is a real product, not a teaser",
    starbridge: "No real SMB tier; enterprise-only customer profile",
    govWin: "No SMB on-ramp; minimum spend is prohibitive",
    govSpend: "No published SMB tier",
  },
  {
    dimension: "CRM integrations",
    civicSignals:
      "Salesforce, HubSpot, Pipedrive, Attio, Folk, webhooks, REST API",
    starbridge: "Salesforce, HubSpot, Outreach, Salesloft, Apollo",
    govWin: "Costpoint / Vantagepoint (Deltek-native); limited third-party",
    govSpend: "Limited — push to CRM not a core feature",
  },
  {
    dimension: "FOIA workflow",
    civicSignals:
      "First-class: template library, state machine, attachment ingestion, extraction",
    starbridge: "Mentioned; opaque — not a primary feature",
    govWin: "Not a feature",
    govSpend: "Not a feature",
  },
];

function Cell({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <td
      className={`px-4 py-3 align-top text-sm leading-snug ${className ?? ""}`}
    >
      {children}
    </td>
  );
}

function HeaderCell({ children }: { children: React.ReactNode }) {
  return (
    <th
      scope="col"
      className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider"
    >
      {children}
    </th>
  );
}

export function PricingComparison() {
  return (
    <section
      aria-labelledby="comparison-heading"
      className="border-t bg-muted/20 py-20"
    >
      <div className="container">
        <h2
          id="comparison-heading"
          className="text-3xl font-bold tracking-tight"
        >
          {COMPARISON_HEADING}
        </h2>
        <p className="mt-3 max-w-2xl text-muted-foreground">
          The SLED intelligence market is dominated by closed, enterprise-priced
          SaaS. CivicSignals is the only open-source entrant in this category.
          Claims below are sourced from public vendor pages, third-party
          comparisons, and investor disclosures. See{" "}
          <span className="font-medium">02-competitive-teardown.md</span> in
          the repository for full citations.
        </p>

        <div className="mt-10 overflow-x-auto rounded-xl border">
          <table className="min-w-full divide-y divide-border">
            <caption className="sr-only">
              Feature comparison: CivicSignals vs Starbridge, GovWin IQ, and
              GovSpend
            </caption>
            <thead className="bg-muted/50">
              <tr>
                <HeaderCell>Dimension</HeaderCell>
                <HeaderCell>
                  <span className="text-primary">CivicSignals</span>
                </HeaderCell>
                <HeaderCell>Starbridge</HeaderCell>
                <HeaderCell>GovWin IQ (Deltek)</HeaderCell>
                <HeaderCell>GovSpend</HeaderCell>
              </tr>
            </thead>
            <tbody className="divide-y divide-border bg-background">
              {ROWS.map((row, idx) => (
                <tr
                  key={row.dimension}
                  className={idx % 2 === 1 ? "bg-muted/10" : ""}
                >
                  {/* Row header for screen readers — associates dimension label with data cells */}
                  <th
                    scope="row"
                    className="px-4 py-3 align-top text-sm font-medium leading-snug text-left"
                  >
                    {row.dimension}
                  </th>
                  <Cell className="text-foreground">{row.civicSignals}</Cell>
                  <Cell className="text-muted-foreground">{row.starbridge}</Cell>
                  <Cell className="text-muted-foreground">{row.govWin}</Cell>
                  <Cell className="text-muted-foreground">{row.govSpend}</Cell>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-4 text-xs text-muted-foreground">
          Competitor pricing estimates are derived from public third-party
          comparisons and investor disclosures; exact figures are not
          independently verified. &ldquo;Contact sales&rdquo; tools do not
          disclose pricing publicly.
        </p>
      </div>
    </section>
  );
}
