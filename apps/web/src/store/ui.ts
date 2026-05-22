import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

// Client-only UI state (preferences, draft forms). Server state lives in
// TanStack Query, not here (doc 06 §2). B5 uses ``activeWorkspaceId`` as the
// *UI selection* of the current workspace — the source of truth for which
// workspace id the app sends as the ``X-Workspace-Id`` header on scoped calls.
// The workspace records themselves are server state (see hooks/use-workspaces).
// Persisted so a page refresh keeps the active workspace selected.
interface UiState {
  activeWorkspaceId: string | null;
  setActiveWorkspaceId: (id: string | null) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      activeWorkspaceId: null,
      setActiveWorkspaceId: (id) => set({ activeWorkspaceId: id }),
    }),
    {
      name: "cs.ui",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
