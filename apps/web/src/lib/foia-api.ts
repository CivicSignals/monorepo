// FOIA API client for the web app (M4).
//
// Talks to the FastAPI FOIA endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1/foia). TanStack Query owns the *server*
// state (see src/hooks/use-foia.ts); this module is the thin transport layer.
//
// FOIA requests are workspace-scoped — every call sends the X-Workspace-Id
// header (doc 08 §1.4, B5). Template endpoints are global reference data and
// do not require the header.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Shapes (mirrors apps/api/src/civicsignals_api/modules/foia/schemas.py) ----

export type FoiaStatus = "draft" | "sent" | "ack" | "response";

export const FOIA_STATUSES: FoiaStatus[] = ["draft", "sent", "ack", "response"];

/** All transitions allowed by the state machine (draft→sent→ack→response). */
export const ALLOWED_NEXT: Record<FoiaStatus, FoiaStatus[]> = {
  draft: ["sent"],
  sent: ["ack"],
  ack: ["response"],
  response: [],
};

export interface FoiaRequestRead {
  id: string;
  workspace_id: string;
  created_by: string;
  entity_id: string;
  jurisdiction: string | null;
  subject: string;
  body: string;
  submission_method: string;
  submission_target: string | null;
  status: FoiaStatus;
  sent_at: string | null;
  ack_at: string | null;
  response_at: string | null;
  response_notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface FoiaRequestPage {
  items: FoiaRequestRead[];
  next_cursor: string | null;
}

export interface FoiaRequestEventRead {
  id: string;
  request_id: string;
  actor_id: string;
  from_status: string;
  to_status: string;
  occurred_at: string;
}

export interface FoiaTemplateRead {
  jurisdiction: string;
  jurisdiction_name: string;
  state: string | null;
  statute: string;
  deadline_days: number;
  deadline_note: string;
  fee_waiver_language: string;
  submission_method_hint: string;
  status: string;
  placeholders: string[];
  body: string;
}

export interface FoiaTemplateList {
  items: FoiaTemplateRead[];
  total: number;
}

export interface FoiaRequestFilters {
  status?: FoiaStatus;
  entity_id?: string;
  cursor?: string;
  limit?: number;
}

export interface FoiaRequestCreate {
  entity_id: string;
  subject: string;
  jurisdiction?: string;
  template_context?: Record<string, string>;
  body?: string;
  submission_method?: string;
  submission_target?: string;
}

export interface FoiaRequestUpdate {
  subject?: string;
  body?: string;
  submission_method?: string;
  submission_target?: string;
}

export interface FoiaRequestTransition {
  status: FoiaStatus;
  response_notes?: string;
}

// ---- Transport ----

async function request<T>(
  path: string,
  init: RequestInit & { token?: string; workspaceId?: string } = {},
): Promise<T> {
  const { token, workspaceId, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (rest.body !== undefined) {
    merged.set("Content-Type", "application/json");
  }
  if (token) merged.set("Authorization", `Bearer ${token}`);
  // FOIA requests are workspace-scoped (doc 08 §1.4, M2).
  if (workspaceId) merged.set("X-Workspace-Id", workspaceId);

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Page size cap (doc 06 §5) ----

export const FOIA_PAGE_LIMIT = 25;

// ---- Template API functions (global reference data, no workspace header) ----

export function listFoiaTemplates(): Promise<FoiaTemplateList> {
  return request<FoiaTemplateList>("/foia/templates");
}

export function getFoiaTemplate(jurisdiction: string): Promise<FoiaTemplateRead> {
  return request<FoiaTemplateRead>(`/foia/templates/${encodeURIComponent(jurisdiction)}`);
}

export function renderFoiaTemplate(
  jurisdiction: string,
  context: Record<string, string>,
): Promise<{ jurisdiction: string; rendered_body: string }> {
  return request<{ jurisdiction: string; rendered_body: string }>(
    `/foia/templates/${encodeURIComponent(jurisdiction)}/render`,
    { method: "POST", body: JSON.stringify({ context }) },
  );
}

// ---- FOIA request API functions (workspace-scoped) ----

/** List/search FOIA requests for the current workspace with cursor pagination. */
export function listFoiaRequests(
  token: string,
  workspaceId: string,
  filters: FoiaRequestFilters = {},
): Promise<FoiaRequestPage> {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? FOIA_PAGE_LIMIT));
  if (filters.status) params.set("status", filters.status);
  if (filters.entity_id) params.set("entity_id", filters.entity_id);
  if (filters.cursor) params.set("cursor", filters.cursor);
  return request<FoiaRequestPage>(`/foia/requests?${params.toString()}`, {
    token,
    workspaceId,
  });
}

/** Get a single FOIA request by id. Returns null if 404. */
export async function getFoiaRequest(
  token: string,
  workspaceId: string,
  id: string,
): Promise<FoiaRequestRead | null> {
  try {
    return await request<FoiaRequestRead>(`/foia/requests/${encodeURIComponent(id)}`, {
      token,
      workspaceId,
    });
  } catch (err) {
    if (err instanceof ProblemError && err.problem.status === 404) return null;
    throw err;
  }
}

/** Create a new FOIA request (starts as draft). */
export function createFoiaRequest(
  token: string,
  workspaceId: string,
  input: FoiaRequestCreate,
): Promise<FoiaRequestRead> {
  return request<FoiaRequestRead>("/foia/requests", {
    method: "POST",
    body: JSON.stringify(input),
    token,
    workspaceId,
  });
}

/** Update a draft FOIA request (patch; only draft status allowed). */
export function updateFoiaRequest(
  token: string,
  workspaceId: string,
  id: string,
  update: FoiaRequestUpdate,
): Promise<FoiaRequestRead> {
  return request<FoiaRequestRead>(`/foia/requests/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(update),
    token,
    workspaceId,
  });
}

/** Advance the state machine: draft→sent→ack→response. */
export function transitionFoiaRequest(
  token: string,
  workspaceId: string,
  id: string,
  transition: FoiaRequestTransition,
): Promise<FoiaRequestRead> {
  return request<FoiaRequestRead>(`/foia/requests/${encodeURIComponent(id)}/transition`, {
    method: "POST",
    body: JSON.stringify(transition),
    token,
    workspaceId,
  });
}

/** Fetch the status-transition event history for a FOIA request. */
export function listFoiaRequestEvents(
  token: string,
  workspaceId: string,
  requestId: string,
): Promise<FoiaRequestEventRead[]> {
  return request<FoiaRequestEventRead[]>(
    `/foia/requests/${encodeURIComponent(requestId)}/events`,
    { token, workspaceId },
  );
}

// ---- M3: Attachment shapes (mirrors foia/schemas.py FoiaAttachmentRead) ----

export type FoiaAttachmentExtractionStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "skipped";

export interface FoiaAttachmentRead {
  id: string;
  foia_request_id: string;
  raw_document_id: string;
  extraction_job_id: string | null;
  filename: string;
  content_type: string;
  uploaded_by: string;
  uploaded_at: string;
  extraction_status: FoiaAttachmentExtractionStatus;
}

export interface FoiaAttachmentPage {
  items: FoiaAttachmentRead[];
  next_cursor: string | null;
}

export interface FoiaAttachmentSignalRef {
  id: string;
  signal_type: string;
  title: string;
  summary: string;
  confidence: number | null;
  status: string;
  observed_at: string;
}

// ---- M3: Attachment API functions (workspace-scoped) ----

/** Upload a response document to a FOIA request (multipart/form-data).
 *
 * Uses a raw fetch rather than `request()` so that Content-Type is NOT
 * forced to application/json — the browser sets the correct
 * multipart/form-data boundary automatically when FormData is the body.
 */
export async function uploadFoiaAttachment(
  token: string,
  workspaceId: string,
  requestId: string,
  file: File,
): Promise<FoiaAttachmentRead> {
  const form = new FormData();
  form.append("file", file, file.name);

  const headers = new Headers();
  headers.set("Accept", "application/json");
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("X-Workspace-Id", workspaceId);
  // No Content-Type: browser sets multipart/form-data with boundary.

  const res = await fetch(
    `${API_BASE_URL}/foia/requests/${encodeURIComponent(requestId)}/attachments`,
    { method: "POST", body: form, headers },
  );
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({
      type: "about:blank",
      title: res.statusText,
      status: res.status,
    }))) as Problem;
    throw new ProblemError(problem);
  }
  return (await res.json()) as FoiaAttachmentRead;
}

/** List attachments for a FOIA request (cursor-paginated). */
export function listFoiaAttachments(
  token: string,
  workspaceId: string,
  requestId: string,
  cursor?: string,
): Promise<FoiaAttachmentPage> {
  const params = new URLSearchParams({ limit: String(FOIA_PAGE_LIMIT) });
  if (cursor) params.set("cursor", cursor);
  return request<FoiaAttachmentPage>(
    `/foia/requests/${encodeURIComponent(requestId)}/attachments?${params.toString()}`,
    { token, workspaceId },
  );
}

/** List signals linked to a FOIA attachment. */
export function listAttachmentSignals(
  token: string,
  workspaceId: string,
  requestId: string,
  attachmentId: string,
): Promise<FoiaAttachmentSignalRef[]> {
  return request<FoiaAttachmentSignalRef[]>(
    `/foia/requests/${encodeURIComponent(requestId)}/attachments/${encodeURIComponent(attachmentId)}/signals`,
    { token, workspaceId },
  );
}
