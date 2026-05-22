import { create } from "zustand";

// Client-only UI state (preferences, draft forms). Server state lives in
// TanStack Query, not here. See doc 06 §2.
interface UiState {
  activeWorkspaceId: string | null;
  setActiveWorkspaceId: (id: string | null) => void;
}

export const useUiStore = create<UiState>((set) => ({
  activeWorkspaceId: null,
  setActiveWorkspaceId: (id) => set({ activeWorkspaceId: id }),
}));
