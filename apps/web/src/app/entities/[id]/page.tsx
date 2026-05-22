// Entity profile page — /entities/[id] (C3).
// Server component shell; rendering delegated to EntityProfile client island.
import type { Metadata } from "next";
import { EntityProfile } from "./entity-profile";

// Dynamic params — id is a UUID string.
interface PageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `Entity ${id} | CivicSignals`,
    description: "View entity details, hierarchy, and source citations.",
  };
}

export default async function EntityPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <main className="container py-8">
      <EntityProfile id={id} />
    </main>
  );
}
