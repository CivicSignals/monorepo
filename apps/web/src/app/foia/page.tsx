// FOIA tracker page — /foia (M4).
// Server component shell; rendering delegated to FoiaPageClient client island.
import type { Metadata } from "next";
import { FoiaPageClient } from "./foia-page-client";

export const metadata: Metadata = {
  title: "FOIA Requests | CivicSignals",
  description:
    "Browse, filter, and track your public-records requests in your workspace.",
};

export default function FoiaPage() {
  return (
    <main className="container py-8">
      <FoiaPageClient />
    </main>
  );
}
