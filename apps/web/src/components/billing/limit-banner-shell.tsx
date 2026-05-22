"use client";

// Shell wrapper that reads the active workspace from Zustand and passes it to
// LimitBanner. This lets the layout (a server component) mount the banner
// without prop-drilling the workspace id through RSC. (N4)

import { useUiStore } from "@/store/ui";
import { LimitBanner } from "@/components/billing/limit-banner";

export function LimitBannerShell() {
  const activeWorkspaceId = useUiStore((s) => s.activeWorkspaceId);
  return <LimitBanner workspaceId={activeWorkspaceId} />;
}
