// Contacts hooks (C4, C6) — TanStack Query owns server state (doc 06 §2).
//
// Contacts are global/not workspace-scoped; no auth or workspace header needed
// for reads. The report-invalid mutation (C6) does require auth + workspace.
//
// useEntityContacts         — infinite-scroll list of contacts for one entity
// useContact                — single contact by id
// useReportContactInvalid   — mutation: POST /contacts/{id}/report-invalid (C6)

"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  type ContactCorrectionRequest,
  type ContactCorrectionResponse,
  type ContactFilters,
  type ContactPage,
  type ContactRead,
  CONTACTS_PAGE_LIMIT,
  getContact,
  listContacts,
  reportContactInvalid,
} from "@/lib/contacts-api";

// ---- Query keys ----

export const contactsKeys = {
  all: ["contacts"] as const,
  lists: () => [...contactsKeys.all, "list"] as const,
  listForEntity: (entityId: string) =>
    [...contactsKeys.lists(), { entity_id: entityId }] as const,
  details: () => [...contactsKeys.all, "detail"] as const,
  detail: (id: string) => [...contactsKeys.details(), id] as const,
};

// ---- useEntityContacts: infinite scroll list for one entity ----

/**
 * Infinite-scroll hook for the contacts section of an entity profile.
 * Each page is fetched using the previous page's next_cursor.
 */
export function useEntityContacts(
  entityId: string | undefined,
  limit = CONTACTS_PAGE_LIMIT,
) {
  return useInfiniteQuery<
    ContactPage,
    Error,
    InfiniteData<ContactPage>,
    ReturnType<typeof contactsKeys.listForEntity>,
    string | undefined
  >({
    queryKey: contactsKeys.listForEntity(entityId ?? ""),
    queryFn: ({ pageParam }) =>
      listContacts({ entity_id: entityId, cursor: pageParam, limit }),
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: entityId !== undefined && entityId !== "",
  });
}

// ---- useContact: single contact by id ----

/** Fetch a single contact. Returns null when the server returns 404. */
export function useContact(id: string | undefined) {
  return useQuery<ContactRead | null>({
    queryKey: contactsKeys.detail(id ?? ""),
    queryFn: () => (id ? getContact(id) : Promise.resolve(null)),
    enabled: id !== undefined && id !== "",
  });
}

// Re-export the filters type for convenience
export type { ContactFilters };

// ---- useReportContactInvalid: mutation for C6 correction endpoint ----

/**
 * TanStack Query mutation to report a contact as invalid/bounced (C6).
 *
 * On success:
 * - Invalidates the contacts list query for the contact's entity so the badge
 *   updates without a manual refresh.
 * - Invalidates the single-contact detail query (if fetched).
 *
 * The mutation accepts a ``ContactCorrectionRequest`` + auth context and returns
 * a ``ContactCorrectionResponse`` with the updated contact + the new audit row.
 */
export function useReportContactInvalid(options?: {
  /** Entity id to invalidate after a successful report (for list invalidation). */
  entityId?: string;
}) {
  const queryClient = useQueryClient();

  return useMutation<
    ContactCorrectionResponse,
    Error,
    {
      contactId: string;
      body: ContactCorrectionRequest;
      accessToken: string;
      workspaceId: string;
    }
  >({
    mutationFn: ({ contactId, body, accessToken, workspaceId }) =>
      reportContactInvalid(contactId, body, { accessToken, workspaceId }),

    onSuccess: (data) => {
      // Invalidate the detail query for the affected contact.
      void queryClient.invalidateQueries({
        queryKey: contactsKeys.detail(data.contact.id),
      });
      // Invalidate the list query so the updated badge propagates immediately.
      if (options?.entityId) {
        void queryClient.invalidateQueries({
          queryKey: contactsKeys.listForEntity(options.entityId),
        });
      } else {
        // Broad invalidation when entity id is not known.
        void queryClient.invalidateQueries({
          queryKey: contactsKeys.lists(),
        });
      }
    },
  });
}
