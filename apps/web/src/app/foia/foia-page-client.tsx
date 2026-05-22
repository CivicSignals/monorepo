// FoiaPageClient — client island for the /foia list page (M4).
// Houses the "New request" button + modal alongside the FoiaList.

"use client";

import { useState } from "react";
import { FoiaList } from "@/components/foia/foia-list";
import { FoiaCreateModal } from "@/components/foia/foia-create-form";

export function FoiaPageClient() {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">FOIA requests</h1>
          <p className="mt-1 text-muted-foreground">
            Browse, filter, and track your public-records requests.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModalOpen(true)}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          + New request
        </button>
      </div>

      <FoiaList />

      <FoiaCreateModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  );
}
