// ContactList — cursor-paginated list of contacts for an entity (C4).
//
// Renders loading skeleton, empty state, error state, and a "Load more" button
// when next_cursor is available. Uses the useEntityContacts hook (TanStack Query).

"use client";

import { useEntityContacts } from "@/hooks/use-contacts";
import { ContactCard } from "@/components/contacts/contact-card";
import { ProblemError } from "@/lib/auth-api";

interface ContactListProps {
  entityId: string;
}

// ---- Loading skeleton ----
function ContactListSkeleton() {
  return (
    <ul aria-busy="true" aria-label="Loading contacts" className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <li key={i} className="h-16 animate-pulse rounded-lg border bg-muted" />
      ))}
    </ul>
  );
}

// ---- Main ----
export function ContactList({ entityId }: ContactListProps) {
  const {
    data,
    isLoading,
    error,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useEntityContacts(entityId);

  const contacts = data?.pages.flatMap((p) => p.items) ?? [];

  if (isLoading) return <ContactListSkeleton />;

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive"
      >
        {error instanceof ProblemError
          ? (error.problem.detail ?? error.problem.title)
          : error.message}
      </div>
    );
  }

  if (contacts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">
        No contacts on record for this entity.
      </p>
    );
  }

  return (
    <>
      <ul className="space-y-3" aria-label="Contacts">
        {contacts.map((contact) => (
          <li key={contact.id}>
            <ContactCard contact={contact} />
          </li>
        ))}
      </ul>

      {hasNextPage && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={() => void fetchNextPage()}
            disabled={isFetchingNextPage}
            className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
          >
            {isFetchingNextPage ? "Loading…" : "Load more contacts"}
          </button>
        </div>
      )}
    </>
  );
}
