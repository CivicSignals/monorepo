// Contacts API client for the web app (C4).
//
// Talks to the FastAPI contacts endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1). TanStack Query owns the *server* state
// (see src/hooks/use-contacts.ts); this module is the thin transport layer.
//
// Contacts are **global / not workspace-scoped** (doc 07 §3, C2 req 3):
// no X-Workspace-Id header is needed, same as entities.
//
// Endpoints mirrored:
//   GET /contacts?entity_id=…&cursor=…&limit=…  — list contacts for entity
//   GET /contacts/{contact_id}                   — get one contact

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Shapes (mirrors apps/api/src/civicsignals_api/modules/contacts/schemas.py) ----

export interface ContactEmailRead {
  id: string;
  contact_id: string;
  email: string;
  is_primary: boolean;
  email_status: string;
  source: string | null;
  source_url: string | null;
  source_recipe_id: string | null;
  confidence: number | null;
  observed_at: string | null;
  verified: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContactPhoneRead {
  id: string;
  contact_id: string;
  phone: string;
  phone_type: string | null;
  is_primary: boolean;
  source: string | null;
  source_url: string | null;
  source_recipe_id: string | null;
  confidence: number | null;
  observed_at: string | null;
  verified: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContactTitleRead {
  id: string;
  contact_id: string;
  title: string;
  department: string | null;
  is_current: boolean;
  first_observed_at: string | null;
  last_observed_at: string | null;
  source: string | null;
  source_url: string | null;
  source_recipe_id: string | null;
  confidence: number | null;
  observed_at: string | null;
  verified: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContactRead {
  id: string;
  entity_id: string;
  name: string;
  department: string | null;
  title: string | null;
  status: string;
  canonical_email: string | null;
  attributes: Record<string, unknown>;
  // Per-record source provenance (doc 16 §18)
  source: string | null;
  source_url: string | null;
  source_recipe_id: string | null;
  confidence: number | null;
  observed_at: string | null;
  verified: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ContactPage {
  items: ContactRead[];
  next_cursor: string | null;
}

export interface ContactFilters {
  entity_id?: string;
  cursor?: string;
  limit?: number;
}

// ---- Transport ----

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const merged = new Headers(init.headers);
  merged.set("Accept", "application/json");

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: merged,
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({
      type: "about:blank",
      title: res.statusText,
      status: res.status,
    }))) as Problem;
    throw new ProblemError(problem);
  }
  return (await res.json()) as T;
}

// ---- API functions ----

export const CONTACTS_PAGE_LIMIT = 25;

/**
 * List contacts for an entity (or globally) with cursor pagination.
 * Production callers should always supply entity_id.
 */
export function listContacts(filters: ContactFilters = {}): Promise<ContactPage> {
  const params = new URLSearchParams();
  if (filters.entity_id) params.set("entity_id", filters.entity_id);
  if (filters.cursor) params.set("cursor", filters.cursor);
  params.set("limit", String(filters.limit ?? CONTACTS_PAGE_LIMIT));

  const qs = params.toString();
  return request<ContactPage>(`/contacts${qs ? `?${qs}` : ""}`);
}

/** Get a single contact by id. Returns null if 404. */
export async function getContact(id: string): Promise<ContactRead | null> {
  try {
    return await request<ContactRead>(`/contacts/${id}`);
  } catch (err) {
    if (err instanceof ProblemError && err.problem.status === 404) return null;
    throw err;
  }
}

// ---- Verification status helpers ----

/** Number of days after which a verified contact is considered stale. */
export const STALE_DAYS = 180;

/**
 * Derive the verification status label for a contact.
 *
 * - "Verified"  — verified=true and last_verified_at within STALE_DAYS days.
 * - "Stale"     — verified=false, or last_verified_at is older than STALE_DAYS days.
 */
export type VerificationStatus = "Verified" | "Stale";

export function getVerificationStatus(contact: Pick<ContactRead, "verified" | "last_verified_at">): VerificationStatus {
  if (!contact.verified) return "Stale";
  if (!contact.last_verified_at) return "Stale";
  const verifiedAt = new Date(contact.last_verified_at).getTime();
  const ageMs = Date.now() - verifiedAt;
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays > STALE_DAYS) return "Stale";
  return "Verified";
}
