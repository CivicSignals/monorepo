// Contacts hooks (C4) — TanStack Query owns server state (doc 06 §2).
//
// Contacts are global/not workspace-scoped; no auth or workspace header needed.
//
// useEntityContacts — infinite-scroll list of contacts for a given entity
// useContact        — single contact by id

"use client";

import {
  useInfiniteQuery,
  useQuery,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  type ContactFilters,
  type ContactPage,
  type ContactRead,
  CONTACTS_PAGE_LIMIT,
  getContact,
  listContacts,
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
