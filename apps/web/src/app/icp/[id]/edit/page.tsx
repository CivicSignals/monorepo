// /icp/[id]/edit — Edit an existing ICP definition (F2).
// The wizard pre-populates all steps from the existing ICP fetched from the API.

import type { Metadata } from "next";
import { IcpWizardEdit } from "@/components/icp/icp-wizard-edit";

export const metadata: Metadata = {
  title: "Edit ICP | CivicSignals",
  description: "Edit your ideal customer profile.",
};

interface EditIcpPageProps {
  params: Promise<{ id: string }>;
}

export default async function EditIcpPage({ params }: EditIcpPageProps) {
  const { id } = await params;
  return (
    <main className="container max-w-2xl py-10">
      <div className="mb-8 space-y-1">
        <h1 className="text-3xl font-bold tracking-tight">Edit ICP</h1>
        <p className="text-muted-foreground">
          Update your ideal customer profile. Changes will be saved and
          re-activated immediately.
        </p>
      </div>
      <IcpWizardEdit icpId={id} />
    </main>
  );
}
