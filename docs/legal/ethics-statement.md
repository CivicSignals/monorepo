# Ethics Statement — Responsible Use of Civic and Government Data

> **DRAFT — Pending review. Not final until reviewed by leadership and published.**  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

This statement explains how CivicSignals collects, processes, and uses civic and government data, and the ethical commitments we make to the public servants, institutions, and communities this data represents.

---

## What We Are Building and Why

CivicSignals monitors publicly available government and education websites — procurement notices, board meeting agendas, budget documents, grant announcements, leadership directories — and extracts structured signals from them using AI, making that intelligence accessible and affordable to companies that sell to the public sector.

The underlying data is public. By law, governments publish their procurement activity, their budgets, and their decision-making processes precisely so that the public and the market can see them. CivicSignals does not create this transparency — it surfaces it. Our contribution is making information that is technically public but practically inaccessible legible to the thousands of small and mid-sized companies that cannot afford a research staff or a $30,000-per-seat enterprise intelligence tool.

We are also building an open-source platform. The code, the data collection methodology, and the decisions about what we collect and how we collect it are all visible to scrutiny. We believe this transparency is an ethical obligation, not just a business strategy.

---

## What Data We Collect and From Where

CivicSignals collects:

1. **Public-sector entity data:** Names, addresses, identifiers, and organizational structure of government agencies, school districts, universities, and special-purpose authorities. Sourced from official government data sources (US Census of Governments, NCES, IPEDS) and official agency websites.

2. **Public procurement signals:** RFP notices, contract awards, budget approvals, grant announcements, board agenda items, and similar procurement-related events. Sourced exclusively from official government portals, agency websites, and public record repositories. We do not purchase data from data brokers.

3. **Public-sector contact records:** Names, titles, work email addresses, and office phone numbers of public servants in their official roles. Sourced exclusively from official .gov and .edu websites — the public directories that agencies maintain and publish themselves. We do not collect:
   - Personal (non-work) email addresses or phone numbers
   - Home addresses
   - Social media profiles
   - Salary or financial records of individuals (we process publicly disclosed budget data, not individual salaries)
   - Medical information
   - Family relationships

We do not purchase contact data from data brokers, scrape LinkedIn or other social networks, or use any data source that the individual has not placed in an official public-sector capacity.

---

## How We Use This Data

The primary purpose of collecting public-sector contact data is to connect procurement officers and decision-makers with companies that sell legitimate products and services to the public sector. A school IT director reviewing a network infrastructure bid deserves to hear from vendors who can serve them; a city purchasing manager with an open RFP benefits from a competitive market of qualified responders.

We do not:
- Enable unsolicited bulk outreach to public servants (see our [Acceptable Use Policy](acceptable-use-policy.md))
- Sell contact data to data brokers or third parties
- Use contact data for purposes unrelated to professional procurement outreach
- Enable tracking, surveillance, or monitoring of individuals outside their public roles
- Train AI models on individual contact data

---

## Opt-Out and Objection

Public servants who prefer not to have their public-role contact information included in CivicSignals may object at privacy@civicsignals.io. Submissions should include:

- Full name and title as they appear in CivicSignals
- URL of the official agency page where the information was published
- The agency affiliation

We will:
- Remove the record from search and extraction results within 30 business days
- Suppress re-discovery for 24 months
- Retain an opaque hash of the objection to prevent inadvertent re-indexing

We acknowledge that this places a burden on the individual rather than on the system, and that we are asking public servants to opt out of something they did not opt into. We take that seriously. We are committed to making the opt-out process as simple and frictionless as possible, and to honoring objections promptly and permanently.

---

## Our Stance on Public-Sector AI Procurement

CivicSignals operates during a moment of rapid expansion of AI procurement in the public sector. Every state, city, and school district is evaluating AI tools. This creates more buying signals for CivicSignals' customers — and also raises questions about what CivicSignals' role is in this ecosystem.

Our commitments:

1. **We surface information; we do not make decisions.** CivicSignals provides intelligence to sales professionals. We do not recommend that a specific vendor win a specific contract, and we do not have a stake in the outcome of any procurement.

2. **We do not discriminate in coverage.** Our scraper recipes aim to cover public entities across geographies, sizes, and political affiliations. We do not suppress or de-prioritize signals from any jurisdiction.

3. **We support competition, not concentration.** Our mission is to lower the cost of accessing public procurement intelligence so that small and mid-sized vendors — not just large incumbents — can compete for public contracts. We believe competitive public markets serve taxpayers.

4. **We support investigative journalism and civic research.** The AGPL-3.0 open-source core is available to journalists, watchdog organizations, and researchers who want to investigate public procurement patterns. We will not use terms of service or legal threats to suppress legitimate journalism or research using CivicSignals data.

---

## Responsibility for How the Platform Is Used

CivicSignals is a tool. We are responsible for building it responsibly; our customers are responsible for using it responsibly. The [Acceptable Use Policy](acceptable-use-policy.md) defines the limits. We enforce it.

We will not passively benefit from harmful uses of the platform. If we discover that a customer is using CivicSignals to harass public servants, to conduct discriminatory outreach, to stalk individuals, or to engage in fraud, we will terminate their access. We encourage users to report misuse to trust@civicsignals.io.

---

## Self-Hosted Use

CivicSignals Core is open-source under AGPL-3.0. Operators who self-host the platform control their own deployments and are responsible for their data collection and use practices under applicable law. CivicSignals, Inc. cannot govern self-hosted installations, but we make the same ethical commitments to our community:

- We will not knowingly provide technical assistance to a self-hosted operator whose use of the platform we know to be harmful or illegal.
- We will publish security advisories and patches regardless of the operator's use case.
- We ask the community to hold self-hosted deployments to the spirit of this ethics statement.

---

## Open Questions We Are Working Through

We are an early-stage company and we do not have all the answers. Questions we are actively thinking about:

- **Scraped content and copyright:** Government documents are generally in the public domain in the US, but some documents (e.g., privately-produced documents submitted to government agencies) may have copyright claims. We err on the side of extracting only structured signal metadata, not reproducing full document text.
- **Scraping ethics beyond legality:** Some websites request in their terms of service or robots.txt that crawlers not access them. We honor robots.txt and crawl-delay headers. For sites that are technically public but appear to discourage automated access, we apply a human-in-the-loop review before adding them to our recipe set.
- **AI accuracy and accountability:** Our signals are produced by automated processes. They can be wrong. We show confidence indicators, link to source documents, and encourage users to verify signals before acting on them. We are thinking about how to make our error rates transparent.
- **Concentration of competitive intelligence:** If CivicSignals becomes the dominant way that vendors learn about public procurement, does that create unfair advantages for early adopters? We don't have a complete answer. Our current position is that the information itself is public, and making it more accessible is net-positive.

---

## Contact

Questions or concerns about our data collection or ethics practices:

- **Ethics and data concerns:** privacy@civicsignals.io
- **Opt-out / objection requests:** privacy@civicsignals.io (subject line: "Contact Record Objection")
- **Misuse reports:** trust@civicsignals.io

**CivicSignals, Inc.**  
civicsignals.io
