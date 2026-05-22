// Entity hooks (C3) — TanStack Query owns the server interactions (doc 06 §2).
//
// The entity directory is public/global; no authentication or workspace header
// is required. All server state lives here; client-only filters (q, kind, etc.)
// are passed in by the caller (React state → these hooks → API client).
//
// useEntities        — list/search with cursor-based infinite scroll
// useEntity          — single entity by id
// useEntityChildren  — paginated children for the profile page

"use client";

import {
  useInfiniteQuery,
  useQuery,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  type EntityFilters,
  type EntityPage,
  type EntityRead,
  ENTITIES_PAGE_LIMIT,
  getEntity,
  listEntities,
  listEntityChildren,
} from "@/lib/entities-api";

// ---- Query keys ----

export const entitiesKeys = {
  all: ["entities"] as const,
  lists: () => [...entitiesKeys.all, "list"] as const,
  list: (filters: EntityFilters) =>
    [...entitiesKeys.lists(), filters] as const,
  details: () => [...entitiesKeys.all, "detail"] as const,
  detail: (id: string) => [...entitiesKeys.details(), id] as const,
  children: (id: string) =>
    [...entitiesKeys.detail(id), "children"] as const,
};

// ---- useEntities: infinite scroll list ----

/**
 * Infinite-scroll hook for the entity directory.
 * Each page is fetched by passing the previous page's next_cursor.
 * Filters (q, kind, state, status, type) are applied on the server.
 */
export function useEntities(filters: Omit<EntityFilters, "cursor" | "limit">) {
  return useInfiniteQuery<
    EntityPage,
    Error,
    InfiniteData<EntityPage>,
    ReturnType<typeof entitiesKeys.list>,
    string | undefined
  >({
    queryKey: entitiesKeys.list(filters),
    queryFn: ({ pageParam }) =>
      listEntities({ ...filters, cursor: pageParam, limit: ENTITIES_PAGE_LIMIT }),
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

// ---- useEntity: single entity by id ----

/** Fetch a single entity. Returns null when the server returns 404. */
export function useEntity(id: string | undefined) {
  return useQuery<EntityRead | null>({
    queryKey: entitiesKeys.detail(id ?? ""),
    queryFn: () => (id ? getEntity(id) : Promise.resolve(null)),
    enabled: id !== undefined && id !== "",
  });
}

// ---- useEntityChildren: paginated children ----

/** Infinite-scroll hook for the children list on the entity profile page. */
export function useEntityChildren(
  entityId: string | undefined,
  limit = ENTITIES_PAGE_LIMIT,
) {
  return useInfiniteQuery<
    EntityPage,
    Error,
    InfiniteData<EntityPage>,
    ReturnType<typeof entitiesKeys.children>,
    string | undefined
  >({
    queryKey: entitiesKeys.children(entityId ?? ""),
    queryFn: ({ pageParam }) =>
      listEntityChildren(entityId!, { cursor: pageParam, limit }),
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: entityId !== undefined && entityId !== "",
  });
}
