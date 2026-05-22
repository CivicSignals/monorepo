// ICP wizard store (F2) — client-only UI state for the multi-step onboarding
// wizard (doc 06 §2: client-only state lives in Zustand, not TanStack Query).
//
// The wizard collects ICP dimensions across 5 steps and assembles them into an
// IcpCreate payload that the review step POSTs. Session-persisted so an accidental
// navigation (back button, refresh) doesn't erase progress — cleared on submit or
// on explicit reset.

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { EntityKind, SignalType } from "@/lib/icp-api";

export type WizardStep =
  | "geography"  // Step 1 — countries + states
  | "segments"   // Step 2 — entity_kinds
  | "size"       // Step 3 — min_size / max_size
  | "signals"    // Step 4 — signal_types + signal_weights
  | "keywords"   // Step 5 — keywords + threshold
  | "review";    // Step 6 — confirm + submit

export const WIZARD_STEPS: WizardStep[] = [
  "geography",
  "segments",
  "size",
  "signals",
  "keywords",
  "review",
];

export const WIZARD_STEP_LABELS: Record<WizardStep, string> = {
  geography: "Geographies",
  segments: "Segments",
  size: "Size band",
  signals: "Signal types",
  keywords: "Keywords",
  review: "Review & save",
};

// The accumulated draft — mirrors IcpCreate but all fields optional for
// incremental collection.
export interface WizardDraft {
  name: string;
  countries: string[];
  states: string[];
  entity_kinds: EntityKind[];
  signal_types: SignalType[];
  signal_weights: Partial<Record<SignalType, number>>;
  min_size: number | null;
  max_size: number | null;
  keywords_required: string[];
  keywords_excluded: string[];
  deal_band_min_cents: number | null;
  deal_band_max_cents: number | null;
  threshold: number;
}

const DEFAULT_DRAFT: WizardDraft = {
  name: "My ICP",
  countries: ["US"],
  states: [],
  entity_kinds: [],
  signal_types: [],
  signal_weights: {},
  min_size: null,
  max_size: null,
  keywords_required: [],
  keywords_excluded: [],
  deal_band_min_cents: null,
  deal_band_max_cents: null,
  threshold: 50,
};

interface IcpWizardState {
  /** Which step the wizard is currently on. */
  currentStep: WizardStep;
  /** Accumulated draft ICP values. */
  draft: WizardDraft;
  /** id of the ICP being edited (null = create mode). */
  editingId: string | null;

  // Actions
  setStep: (step: WizardStep) => void;
  nextStep: () => void;
  prevStep: () => void;
  patchDraft: (patch: Partial<WizardDraft>) => void;
  setEditingId: (id: string | null) => void;
  /** Hydrate the wizard from an existing ICP (edit mode). */
  loadFromIcp: (icp: Partial<WizardDraft> & { name: string; id?: string }) => void;
  /** Reset to defaults (called on submit or explicit cancel). */
  reset: () => void;
}

export const useIcpWizardStore = create<IcpWizardState>()(
  persist(
    (set, get) => ({
      currentStep: "geography",
      draft: { ...DEFAULT_DRAFT },
      editingId: null,

      setStep: (step) => set({ currentStep: step }),

      nextStep: () => {
        const idx = WIZARD_STEPS.indexOf(get().currentStep);
        if (idx < WIZARD_STEPS.length - 1) {
          set({ currentStep: WIZARD_STEPS[idx + 1] });
        }
      },

      prevStep: () => {
        const idx = WIZARD_STEPS.indexOf(get().currentStep);
        if (idx > 0) {
          set({ currentStep: WIZARD_STEPS[idx - 1] });
        }
      },

      patchDraft: (patch) =>
        set((s) => ({ draft: { ...s.draft, ...patch } })),

      setEditingId: (id) => set({ editingId: id }),

      loadFromIcp: ({ id, ...fields }) =>
        set({
          draft: { ...DEFAULT_DRAFT, ...fields },
          editingId: id ?? null,
          currentStep: "geography",
        }),

      reset: () =>
        set({
          currentStep: "geography",
          draft: { ...DEFAULT_DRAFT },
          editingId: null,
        }),
    }),
    {
      name: "cs.icp-wizard",
      storage: createJSONStorage(() => sessionStorage),
    },
  ),
);
