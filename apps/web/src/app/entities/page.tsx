// Entity directory page — /entities (C3).
// Server component shell; rendering is delegated to the EntityDirectory client
// island so that useState / TanStack Query hooks work correctly.
import type { Metadata } from "next";
import { EntityDirectory } from "./entity-directory";

export const metadata: Metadata = {
  title: "Entity Directory | CivicSignals",
  description:
    "Browse and search the public directory of school districts, cities, counties, and other government entities.",
};

export default function EntitiesPage() {
  return (
    <main className="container py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">Entity Directory</h1>
        <p className="mt-1 text-muted-foreground">
          Browse and search school districts, cities, counties, and other
          public-sector entities.
        </p>
      </div>
      <EntityDirectory />
    </main>
  );
}
