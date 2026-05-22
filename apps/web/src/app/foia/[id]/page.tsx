// FOIA request detail page — /foia/[id] (M4).
// Server component shell; rendering delegated to FoiaDetail client island.
import type { Metadata } from "next";
import { FoiaDetail } from "@/components/foia/foia-detail";

interface PageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `FOIA Request ${id} | CivicSignals`,
    description: "View FOIA request details, body, status timeline, and transition controls.",
  };
}

export default async function FoiaRequestPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <main className="container max-w-3xl py-8">
      <FoiaDetail id={id} />
    </main>
  );
}
