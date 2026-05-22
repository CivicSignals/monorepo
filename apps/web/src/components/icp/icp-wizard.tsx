// ICP onboarding wizard orchestrator (F2).
//
// Renders the current wizard step, manages the Zustand draft store, and
// dispatches create / update + activate mutations via TanStack Query hooks.
//
// Edit mode: pass `existingIcpId` to pre-populate the wizard with an existing
// ICP's data (loads from TanStack Query cache or fetches on mount).

"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useIcpWizardStore } from "@/store/icp-wizard";
import { useIcp, useCreateIcp, useUpdateIcp, useActivateIcp } from "@/hooks/use-icp";
import type { IcpOut } from "@/lib/icp-api";
import { WizardStepIndicator } from "./wizard-step-indicator";
import { WizardGeographyStep } from "./wizard-geography-step";
import { WizardSegmentsStep } from "./wizard-segments-step";
import { WizardSizeStep } from "./wizard-size-step";
import { WizardSignalsStep } from "./wizard-signals-step";
import { WizardKeywordsStep } from "./wizard-keywords-step";
import { WizardReviewStep } from "./wizard-review-step";

interface IcpWizardProps {
  /** When set, the wizard runs in edit mode for this ICP id. */
  existingIcpId?: string;
}

/**
 * Populate the wizard store from an ICP fetched from the API.
 * Runs only once per `icp` identity change.
 */
function useLoadFromExisting(icp: IcpOut | null | undefined) {
  const loadFromIcp = useIcpWizardStore((s) => s.loadFromIcp);
  useEffect(() => {
    if (!icp) return;
    loadFromIcp({
      id: icp.id,
      name: icp.name,
      countries: icp.countries,
      states: icp.states,
      entity_kinds: icp.entity_kinds as IcpOut["entity_kinds"],
      signal_types: icp.signal_types as IcpOut["signal_types"],
      signal_weights: icp.signal_weights,
      min_size: icp.min_size,
      max_size: icp.max_size,
      keywords_required: icp.keywords_required,
      keywords_excluded: icp.keywords_excluded,
      deal_band_min_cents: icp.deal_band_min_cents,
      deal_band_max_cents: icp.deal_band_max_cents,
      threshold: icp.threshold,
    });
  }, [icp, loadFromIcp]);
}

export function IcpWizard({ existingIcpId }: IcpWizardProps) {
  const router = useRouter();

  // Wizard store
  const currentStep = useIcpWizardStore((s) => s.currentStep);
  const draft = useIcpWizardStore((s) => s.draft);
  const editingId = useIcpWizardStore((s) => s.editingId);
  const nextStep = useIcpWizardStore((s) => s.nextStep);
  const prevStep = useIcpWizardStore((s) => s.prevStep);
  const patchDraft = useIcpWizardStore((s) => s.patchDraft);
  const reset = useIcpWizardStore((s) => s.reset);

  // Existing ICP (edit mode)
  const { data: existingIcp } = useIcp(existingIcpId);
  useLoadFromExisting(existingIcp);

  // Mutations
  const createIcp = useCreateIcp();
  const updateIcp = useUpdateIcp(editingId ?? "");
  const activateIcp = useActivateIcp();

  const isSubmitting =
    createIcp.isPending || updateIcp.isPending || activateIcp.isPending;
  const submitError =
    createIcp.error ?? updateIcp.error ?? activateIcp.error ?? null;

  /** Called from the review step: POST (create) or PATCH (edit) then activate. */
  const handleSubmit = async () => {
    const payload = {
      name: draft.name,
      countries: draft.countries,
      states: draft.states,
      entity_kinds: draft.entity_kinds,
      signal_types: draft.signal_types,
      signal_weights: draft.signal_weights,
      min_size: draft.min_size,
      max_size: draft.max_size,
      keywords_required: draft.keywords_required,
      keywords_excluded: draft.keywords_excluded,
      deal_band_min_cents: draft.deal_band_min_cents,
      deal_band_max_cents: draft.deal_band_max_cents,
      threshold: draft.threshold,
    };

    let savedId: string;
    if (editingId) {
      const updated = await updateIcp.mutateAsync(payload);
      savedId = updated.id;
    } else {
      // Create with is_active=false so we explicitly activate after.
      const created = await createIcp.mutateAsync({ ...payload, is_active: false });
      savedId = created.id;
    }
    await activateIcp.mutateAsync(savedId);

    reset();
    // TODO G1: redirect to /signals (feed) once that route exists.
    router.push("/");
  };

  // Loading state (edit mode, fetching existing ICP)
  if (existingIcpId && existingIcp === undefined) {
    return (
      <div className="flex items-center justify-center py-12" role="status">
        <span className="text-muted-foreground text-sm">Loading ICP…</span>
      </div>
    );
  }

  // Not-found state (edit mode, ICP returned null)
  if (existingIcpId && existingIcp === null) {
    return (
      <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
        ICP not found. It may have been deleted or belong to another workspace.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <WizardStepIndicator currentStep={currentStep} />

      {currentStep === "geography" && (
        <WizardGeographyStep
          defaultValues={{
            name: draft.name,
            countries: draft.countries,
            states: draft.states,
          }}
          onNext={(values) => {
            patchDraft(values);
            nextStep();
          }}
        />
      )}

      {currentStep === "segments" && (
        <WizardSegmentsStep
          defaultValues={{ entity_kinds: draft.entity_kinds }}
          onNext={(values) => {
            patchDraft(values);
            nextStep();
          }}
          onBack={prevStep}
        />
      )}

      {currentStep === "size" && (
        <WizardSizeStep
          defaultValues={{
            min_size: draft.min_size,
            max_size: draft.max_size,
            deal_band_min_cents: draft.deal_band_min_cents,
            deal_band_max_cents: draft.deal_band_max_cents,
          }}
          onNext={(values) => {
            patchDraft(values);
            nextStep();
          }}
          onBack={prevStep}
        />
      )}

      {currentStep === "signals" && (
        <WizardSignalsStep
          defaultValues={{
            signal_types: draft.signal_types,
            signal_weights: draft.signal_weights,
          }}
          onNext={(values) => {
            patchDraft(values);
            nextStep();
          }}
          onBack={prevStep}
        />
      )}

      {currentStep === "keywords" && (
        <WizardKeywordsStep
          defaultValues={{
            keywords_required: draft.keywords_required,
            keywords_excluded: draft.keywords_excluded,
            threshold: draft.threshold,
          }}
          onNext={(values) => {
            patchDraft(values);
            nextStep();
          }}
          onBack={prevStep}
        />
      )}

      {currentStep === "review" && (
        <WizardReviewStep
          draft={draft}
          editingId={editingId}
          onSubmit={handleSubmit}
          onBack={prevStep}
          isSubmitting={isSubmitting}
          submitError={submitError}
        />
      )}
    </div>
  );
}
