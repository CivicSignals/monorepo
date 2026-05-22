// Zod schemas for the ICP onboarding wizard steps (F2).
// Each step has its own schema for per-step validation; the full schema
// assembles the final IcpCreate payload for the review step.

import { z } from "zod";
import { ENTITY_KINDS, SIGNAL_TYPES } from "@/lib/icp-api";
import type { EntityKind, SignalType } from "@/lib/icp-api";

// ---- Helpers ----

const countryCode = z
  .string()
  .trim()
  .length(2, "Must be a 2-letter country code.")
  .regex(/^[A-Za-z]+$/, "Must be letters only.")
  .transform((v) => v.toUpperCase());

const stateCode = z
  .string()
  .trim()
  .length(2, "Must be a 2-letter state code.")
  .regex(/^[A-Za-z]+$/, "Must be letters only.")
  .transform((v) => v.toUpperCase());

const entityKindEnum = z.enum(ENTITY_KINDS as [EntityKind, ...EntityKind[]]);
const signalTypeEnum = z.enum(SIGNAL_TYPES as [SignalType, ...SignalType[]]);

// ---- Step schemas ----

/** Step 1: Geographies — countries + optional US state filter. */
export const geographyStepSchema = z.object({
  name: z.string().trim().min(1, "Give your ICP a name.").max(200),
  countries: z
    .array(countryCode)
    .min(1, "Select at least one country.")
    .default(["US"]),
  states: z.array(stateCode).default([]),
});
export type GeographyStepValues = z.infer<typeof geographyStepSchema>;

/** Step 2: Segments — entity kinds. Empty = all kinds. */
export const segmentsStepSchema = z.object({
  entity_kinds: z.array(entityKindEnum).default([]),
});
export type SegmentsStepValues = z.infer<typeof segmentsStepSchema>;

/** Step 3: Size band — min/max employee/enrollment counts. */
export const sizeStepSchema = z
  .object({
    min_size: z.number().int().min(0).nullable().default(null),
    max_size: z.number().int().min(0).nullable().default(null),
    deal_band_min_cents: z.number().int().min(0).nullable().default(null),
    deal_band_max_cents: z.number().int().min(0).nullable().default(null),
  })
  .superRefine((data, ctx) => {
    if (
      data.min_size !== null &&
      data.max_size !== null &&
      data.min_size > data.max_size
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["max_size"],
        message: "Max size must be greater than or equal to min size.",
      });
    }
    if (
      data.deal_band_min_cents !== null &&
      data.deal_band_max_cents !== null &&
      data.deal_band_min_cents > data.deal_band_max_cents
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["deal_band_max_cents"],
        message: "Max deal value must be greater than or equal to min.",
      });
    }
  });
export type SizeStepValues = z.infer<typeof sizeStepSchema>;

/** Step 4: Signal types + weights. */
export const signalsStepSchema = z.object({
  signal_types: z
    .array(signalTypeEnum)
    .min(1, "Select at least one signal type."),
  signal_weights: z
    .record(signalTypeEnum, z.number().min(0).max(1))
    .default({}),
});
export type SignalsStepValues = z.infer<typeof signalsStepSchema>;

/** Step 5: Keywords + threshold. */
export const keywordsStepSchema = z.object({
  keywords_required: z.array(z.string().trim().min(1)).default([]),
  keywords_excluded: z.array(z.string().trim().min(1)).default([]),
  threshold: z
    .number()
    .int()
    .min(0, "Threshold must be 0–100.")
    .max(100, "Threshold must be 0–100.")
    .default(50),
});
export type KeywordsStepValues = z.infer<typeof keywordsStepSchema>;

// ---- Full ICP schema (assembled from steps for final validation on review) ----
// Note: sizeStepSchema uses superRefine so cannot be used with .merge().
// We inline the fields here and apply the cross-field range check once.
export const icpWizardSchema = z
  .object({
    // geography
    name: z.string().trim().min(1, "Give your ICP a name.").max(200),
    countries: z.array(countryCode).min(1, "Select at least one country.").default(["US"]),
    states: z.array(stateCode).default([]),
    // segments
    entity_kinds: z.array(entityKindEnum).default([]),
    // size
    min_size: z.number().int().min(0).nullable().default(null),
    max_size: z.number().int().min(0).nullable().default(null),
    deal_band_min_cents: z.number().int().min(0).nullable().default(null),
    deal_band_max_cents: z.number().int().min(0).nullable().default(null),
    // signals
    signal_types: z.array(signalTypeEnum).min(1, "Select at least one signal type."),
    signal_weights: z.record(signalTypeEnum, z.number().min(0).max(1)).default({}),
    // keywords
    keywords_required: z.array(z.string().trim().min(1)).default([]),
    keywords_excluded: z.array(z.string().trim().min(1)).default([]),
    threshold: z.number().int().min(0, "Threshold must be 0–100.").max(100, "Threshold must be 0–100.").default(50),
  })
  .superRefine((data, ctx) => {
    if (data.min_size !== null && data.max_size !== null && data.min_size > data.max_size) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["max_size"], message: "Max size must be >= min size." });
    }
    if (
      data.deal_band_min_cents !== null &&
      data.deal_band_max_cents !== null &&
      data.deal_band_min_cents > data.deal_band_max_cents
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["deal_band_max_cents"],
        message: "Max deal value must be >= min.",
      });
    }
  });

export type IcpWizardValues = z.infer<typeof icpWizardSchema>;
