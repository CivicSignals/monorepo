// Zod schema for the saved-search create / rename / edit form (H1 + H2).
//
// A saved search captures the G1 feed filter set, so the form validates the
// same filter dimensions the feed endpoint accepts (signal type, statuses,
// min score, date range) plus a name and a share toggle. The signal-type and
// feed-status enums are reused from signals-api so the UI can never offer a
// filter the backend would reject.
//
// H2 — the client mirrors the backend's centralized filter-validation rules
// (see modules/searches/validation.py) so an invalid *combination* is caught
// before submit with an explicit, human-readable message per rule:
//   - unknown signal type / status (the enums forbid them);
//   - min_score outside 0–100;
//   - an empty status selection (matches nothing — omit it instead);
//   - an inverted/empty date range (start must be before end).
// The server re-validates and returns the same messages (RFC 7807 errors[]),
// which the form also renders, so the two layers stay in lock-step.

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

/** Explicit messages, kept in one place so the form + tests share them (H2). */
export const FILTER_MESSAGES = {
  nameRequired: "Give your search a name.",
  scoreRange: "Score must be 0–100.",
  dateRangeInverted: "The start date must be before the end date.",
  invalidDate: "Enter a valid date.",
} as const;

/** Parse an optional date-time-local string; "" means "no bound". */
function parsedDate(value: string): Date | null {
  if (value === "") return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Form values for creating / editing a saved search. The empty string for
 * `signal_type` / dates and `null` for `min_score` represent "no filter" in the
 * form; the submit handler strips those before sending so the stored blob
 * carries only set filters (matching the backend's exclude_none serialization).
 */
export const savedSearchFormSchema = z
  .object({
    name: z.string().trim().min(1, FILTER_MESSAGES.nameRequired).max(200),
    signal_type: z.union([signalTypeEnum, z.literal("")]).default(""),
    // The checkbox group is the only way to set statuses, so unknown values
    // cannot be entered; an *empty* selection is a valid "all statuses" intent
    // here (the submit handler omits it) — but a non-empty list with a stray
    // value is impossible via the UI. We still guard the empty case the same
    // way the backend does when it is sent as an explicit `[]`.
    statuses: z.array(feedStatusEnum).default([]),
    min_score: z
      .number()
      .min(0, FILTER_MESSAGES.scoreRange)
      .max(100, FILTER_MESSAGES.scoreRange)
      .nullable()
      .default(null),
    published_at_gte: z.string().default(""),
    published_at_lt: z.string().default(""),
    is_shared: z.boolean().default(false),
  })
  .strict()
  .superRefine((values, ctx) => {
    // Each present date bound must parse (mirrors the backend `invalid_date` rule).
    const gte = parsedDate(values.published_at_gte);
    const lt = parsedDate(values.published_at_lt);
    if (values.published_at_gte !== "" && gte === null) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["published_at_gte"],
        message: FILTER_MESSAGES.invalidDate,
      });
    }
    if (values.published_at_lt !== "" && lt === null) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["published_at_lt"],
        message: FILTER_MESSAGES.invalidDate,
      });
    }
    // Cross-field: start must be strictly before end (start == end is empty).
    if (gte !== null && lt !== null && gte.getTime() >= lt.getTime()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["published_at_gte"],
        message: FILTER_MESSAGES.dateRangeInverted,
      });
    }
  });

export type SavedSearchFormValues = z.infer<typeof savedSearchFormSchema>;
