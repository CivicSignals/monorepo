// N6 — Public pricing page. Static marketing page; no server data, no Zustand server-state.
import type { Metadata } from "next";
import { PricingHero } from "@/components/pricing/pricing-hero";
import { PricingTiers } from "@/components/pricing/pricing-tiers";
import { PricingComparison } from "@/components/pricing/pricing-comparison";
import { PricingFaq } from "@/components/pricing/pricing-faq";

export const metadata: Metadata = {
  title: "Pricing — CivicSignals",
  description:
    "Transparent, public-sector sales intelligence at a fraction of the cost of GovWin or Starbridge. Self-host free (AGPL) or sign up for cloud.",
};

export default function PricingPage() {
  return (
    <main>
      <PricingHero />
      <PricingTiers />
      <PricingComparison />
      <PricingFaq />
    </main>
  );
}
