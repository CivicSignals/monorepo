// IcpWizardEdit — thin wrapper that passes existingIcpId to IcpWizard (F2).
// Isolated so the Edit page (a server component) can pass a string id while the
// wizard remains a fully client-side component.

"use client";

import { IcpWizard } from "./icp-wizard";

interface IcpWizardEditProps {
  icpId: string;
}

export function IcpWizardEdit({ icpId }: IcpWizardEditProps) {
  return <IcpWizard existingIcpId={icpId} />;
}
