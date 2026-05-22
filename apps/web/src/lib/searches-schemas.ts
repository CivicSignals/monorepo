// Zod schema for the saved-search create / rename / edit form (H1).
//
// A saved search captures the G1 feed filter set, so the form validates the
// same filter dimensions the feed endpoint accepts (signal type, statuses,
// min score, date range) plus a name and a share toggle. The signal-type and
// feed-status enums are reused from signals-api so the UI can never offer a
// filter the backend would reject.

import { z } from "zod";
import {
  FEED_STATUS_LABELS,
  SIGNAL_TYPE_LABELS,
  type FeedStatus,
  type SignalType,
} from "@/lib/signals-api";

const SIGNAL_TYPES = Object.keys(SIGNAL_TYPE_LABELS) as [
  SignalType,
  ...SignalType[],
];
const FEED_STATUSES = Object.keys(FEED_STATUS_LABELS) as [
  FeedStatus,
  ...FeedStatus[],
];

const signalTypeEnum = z.enum(SIGNAL_TYPES);
const feedStatusEnum = z.enum(FEED_STATUSES);

/**
 * Form values for creating / editing a saved search. The empty string for
 * `signal_type` / `min_score` represents "no filter" in the form; the submit
 * handler strips those before sending so the stored blob carries only set
 * filters (matching the backend's exclude_none serialization).
 */
export const savedSearchFormSchema = z
  .object({
    name: z.string().trim().min(1, "Give your search a name.").max(200),
    signal_type: z.union([signalTypeEnum, z.literal("")]).default(""),
    statuses: z.array(feedStatusEnum).default([]),
    min_score: z
      .number()
      .min(0, "Score must be 0–100.")
      .max(100, "Score must be 0–100.")
      .nullable()
      .default(null),
    is_shared: z.boolean().default(false),
  })
  .strict();

export type SavedSearchFormValues = z.infer<typeof savedSearchFormSchema>;
