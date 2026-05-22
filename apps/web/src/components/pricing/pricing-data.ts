// N6 — Shared pricing data. Extracted so tests can import without DOM/React.
// Prices are illustrative — final confirmed at launch (tasks N1/N2/LC-13).

export interface PlanFeature {
  text: string;
  included: boolean;
}

export interface PlanTier {
  id: string;
  name: string;
  tagline: string;
  monthlyPrice: string | null;
  annualNote: string;
  ctaLabel: string;
  ctaHref: string;
  selfServe: boolean;
  highlighted: boolean;
  features: PlanFeature[];
}

export const PLANS: PlanTier[] = [
  {
    id: "self-host",
    name: "Self-Host",
    tagline: "For dev teams and privacy-first orgs",
    monthlyPrice: "$0",
    annualNote: "Free forever · AGPL-3.0",
    ctaLabel: "View self-host docs",
    ctaHref: "https://docs.civicsignals.io/self-host",
    selfServe: true,
    highlighted: false,
    features: [
      { text: "Full signal extraction pipeline", included: true },
      { text: "All 8 signal types", included: true },
      { text: "BYO LLM key (OpenAI, Anthropic, Ollama)", included: true },
      { text: "Open scraper recipes (YAML)", included: true },
      { text: "REST API on every plan", included: true },
      { text: "Basic CRM pipeline view", included: true },
      { text: "FOIA tracker", included: true },
      { text: "Managed scraper fleet", included: false },
      { text: "Verified contact enrichment", included: false },
      { text: "Salesforce / HubSpot sync", included: false },
    ],
  },
  {
    id: "solo",
    name: "Solo",
    tagline: "Single rep or boutique consultant",
    monthlyPrice: "$19",
    annualNote: "$228 / yr · billed annually",
    ctaLabel: "Start free trial",
    ctaHref: "/signup?plan=solo",
    selfServe: true,
    highlighted: false,
    features: [
      { text: "1 seat", included: true },
      { text: "All 8 signal types", included: true },
      { text: "Up to 500 tracked entities", included: true },
      { text: "Managed scraper fleet", included: true },
      { text: "Email digest (daily / weekly)", included: true },
      { text: "Slack notifications", included: true },
      { text: "REST API + webhooks", included: true },
      { text: "FOIA tracker", included: true },
      { text: "Verified contact enrichment", included: false },
      { text: "Salesforce / HubSpot sync", included: false },
    ],
  },
  {
    id: "starter",
    name: "Starter",
    tagline: "Small SLED sales team (3–10 reps)",
    monthlyPrice: "$49",
    annualNote: "$588 / seat / yr · billed annually",
    ctaLabel: "Start free trial",
    ctaHref: "/signup?plan=starter",
    selfServe: true,
    highlighted: true,
    features: [
      { text: "Up to 10 seats", included: true },
      { text: "All 8 signal types", included: true },
      { text: "Up to 5,000 tracked entities", included: true },
      { text: "Managed scraper fleet", included: true },
      { text: "Verified contact enrichment (500/mo)", included: true },
      { text: "Salesforce + HubSpot sync", included: true },
      { text: "Pipedrive / Attio / Folk connectors", included: true },
      { text: "REST API + webhooks", included: true },
      { text: "FOIA tracker + templates", included: true },
      { text: "Advanced AI analytics", included: false },
    ],
  },
  {
    id: "pro",
    name: "Pro",
    tagline: "Mid-market team (10–30 reps)",
    monthlyPrice: "$149",
    annualNote: "$1,788 / seat / yr · billed annually",
    ctaLabel: "Start free trial",
    ctaHref: "/signup?plan=pro",
    selfServe: true,
    highlighted: false,
    features: [
      { text: "Up to 30 seats", included: true },
      { text: "Unlimited tracked entities", included: true },
      { text: "Verified contact enrichment (unlimited)", included: true },
      { text: "Advanced AI analytics", included: true },
      { text: "Account scoring + competitor displacement maps", included: true },
      { text: "Priority signal queue", included: true },
      { text: "All Starter integrations", included: true },
      { text: "SSO (SAML / OIDC)", included: true },
      { text: "Dedicated Slack channel with team", included: true },
      { text: "Custom data SLA", included: false },
    ],
  },
  {
    id: "enterprise",
    name: "Enterprise",
    tagline: "Large teams + VPC / on-prem requirements",
    monthlyPrice: null,
    annualNote: "Annual contract · custom pricing",
    ctaLabel: "Talk to us",
    ctaHref: "mailto:sales@civicsignals.io",
    selfServe: false,
    highlighted: false,
    features: [
      { text: "Unlimited seats", included: true },
      { text: "VPC deploy or private cloud", included: true },
      { text: "Custom SLA + uptime guarantee", included: true },
      { text: "SOC 2 Type I report (roadmap)", included: true },
      { text: "Custom recipe commissioning", included: true },
      { text: "Dedicated CSM", included: true },
      { text: "Custom model routing", included: true },
      { text: "Annual invoice / PO billing", included: true },
      { text: "All Pro features", included: true },
      { text: "Pricing posted publicly", included: false },
    ],
  },
];

export const PLAN_NAMES = PLANS.map((p) => p.name);

export const COMPARISON_HEADING =
  "How CivicSignals compares to incumbent SLED tools";
