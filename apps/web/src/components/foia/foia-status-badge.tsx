// FoiaStatusBadge — coloured pill for the FOIA request lifecycle status (M4).

import type { FoiaStatus } from "@/lib/foia-api";

interface FoiaStatusBadgeProps {
  status: FoiaStatus;
  className?: string;
}

const STATUS_LABELS: Record<FoiaStatus, string> = {
  draft: "Draft",
  sent: "Awaiting",
  ack: "Acknowledged",
  response: "Responded",
};

const STATUS_CLASSES: Record<FoiaStatus, string> = {
  draft:
    "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  sent:
    "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
  ack:
    "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  response:
    "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
};

export function FoiaStatusBadge({ status, className = "" }: FoiaStatusBadgeProps) {
  return (
    <span
      className={`inline-flex shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_CLASSES[status]} ${className}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}
