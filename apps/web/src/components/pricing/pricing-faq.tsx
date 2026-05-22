// N6 — Pricing FAQ section

interface FaqItem {
  question: string;
  answer: string;
}

const FAQ_ITEMS: FaqItem[] = [
  {
    question: "Is self-hosting really free?",
    answer:
      "Yes. The CivicSignals Core is released under AGPL-3.0 — you can run it on your own infrastructure at no cost. You bring your own LLM API key (OpenAI, Anthropic, or a local Ollama instance). The only costs are your own hosting and LLM usage.",
  },
  {
    question: "What's the difference between self-host and the cloud plans?",
    answer:
      "Self-host gives you the full signal extraction pipeline with BYO LLM key. Cloud plans add a managed scraper fleet (no infra to run), verified contact enrichment, CRM sync (Salesforce, HubSpot, Pipedrive, Attio, Folk), and advanced AI analytics. Cloud also handles upgrades, backups, and monitoring for you.",
  },
  {
    question: "Do SMB plans require a sales call to sign up?",
    answer:
      "No. Solo, Starter, and Pro are fully self-serve. Start a 14-day trial from any CTA on this page — no credit card required. Enterprise plans involve a conversation because they include custom SLAs, VPC deploy, and purchase-order billing.",
  },
  {
    question: "Why are prices marked 'illustrative'?",
    answer:
      "We're building in the open and the pricing structure above reflects our target positioning. Final prices will be confirmed when Stripe is wired up (task LC-13). Pricing will be in the same range — we'll never hide it behind a contact-sales wall for SMB tiers.",
  },
  {
    question: "Can I self-host and also use the managed cloud for some teams?",
    answer:
      "Yes. The same REST API and recipe format works on both. Some customers run self-hosted for privacy-sensitive data and use the cloud tier for teams that want a managed experience. The open scraper recipes are compatible across both.",
  },
  {
    question: "How does CivicSignals compare in price to GovWin or Starbridge?",
    answer:
      "Third-party comparisons put GovWin at $200+/seat/month and estimate Starbridge in the same enterprise range ($15k–$50k+/seat/year). Our Pro plan is $149/seat/month — roughly 5–10× cheaper for the same core job. And self-host is $0.",
  },
  {
    question: "What signal types are included on every plan?",
    answer:
      "All eight: RFP posted, contract expiring, budget approved, grant awarded, leadership change, RFI/RFQ, board agenda item, and strategic plan published. Signal coverage scales with the managed scraper fleet — self-host coverage is limited to recipes you run yourself.",
  },
];

export function PricingFaq() {
  return (
    <section
      aria-labelledby="faq-heading"
      className="border-t py-20"
    >
      <div className="container">
        <h2
          id="faq-heading"
          className="text-3xl font-bold tracking-tight"
        >
          Frequently asked questions
        </h2>

        <dl className="mt-10 space-y-8 divide-y divide-border">
          {FAQ_ITEMS.map((item) => (
            <div key={item.question} className="pt-8 first:pt-0">
              <dt className="text-base font-semibold">{item.question}</dt>
              <dd className="mt-2 text-sm text-muted-foreground">
                {item.answer}
              </dd>
            </div>
          ))}
        </dl>

        <div className="mt-16 rounded-xl border bg-muted/30 p-8 text-center">
          <h3 className="text-xl font-bold">Still have questions?</h3>
          <p className="mt-2 text-muted-foreground">
            We&apos;re a small team and we respond to every message.
          </p>
          <div className="mt-6 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
            <a
              href="mailto:hello@civicsignals.io"
              className="rounded-md border border-border bg-background px-5 py-2.5 text-sm font-semibold hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              Email us
            </a>
            <a
              href="https://github.com/CivicSignals/monorepo/discussions"
              className="rounded-md border border-border bg-background px-5 py-2.5 text-sm font-semibold hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub Discussions
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
