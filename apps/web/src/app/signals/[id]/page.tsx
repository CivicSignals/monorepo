// Signal detail page — /signals/[id] (G2).
//
// Server component shell; rendering delegated to the SignalDetail client island
// (TanStack Query owns the server state, doc 06 §2). The detail view is
// workspace-scoped — the island sends X-Workspace-Id from the active workspace.
import type { Metadata } from "next";
import { SignalDetail } from "./signal-detail";

interface PageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `Signal ${id} | CivicSignals`,
    description:
      "Signal detail: source documents, extracted fields, suggested contacts, and related signals.",
  };
}

export default async function SignalDetailPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <main className="container max-w-4xl py-8">
      <SignalDetail id={id} />
    </main>
  );
}
