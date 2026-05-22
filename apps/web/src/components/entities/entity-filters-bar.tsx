// EntityFiltersBar — search input + filter selects for the entity directory (C3).

"use client";

import { useCallback } from "react";

// US State codes for the state filter.
const US_STATES: { value: string; label: string }[] = [
  { value: "AL", label: "Alabama" },
  { value: "AK", label: "Alaska" },
  { value: "AZ", label: "Arizona" },
  { value: "AR", label: "Arkansas" },
  { value: "CA", label: "California" },
  { value: "CO", label: "Colorado" },
  { value: "CT", label: "Connecticut" },
  { value: "DE", label: "Delaware" },
  { value: "FL", label: "Florida" },
  { value: "GA", label: "Georgia" },
  { value: "HI", label: "Hawaii" },
  { value: "ID", label: "Idaho" },
  { value: "IL", label: "Illinois" },
  { value: "IN", label: "Indiana" },
  { value: "IA", label: "Iowa" },
  { value: "KS", label: "Kansas" },
  { value: "KY", label: "Kentucky" },
  { value: "LA", label: "Louisiana" },
  { value: "ME", label: "Maine" },
  { value: "MD", label: "Maryland" },
  { value: "MA", label: "Massachusetts" },
  { value: "MI", label: "Michigan" },
  { value: "MN", label: "Minnesota" },
  { value: "MS", label: "Mississippi" },
  { value: "MO", label: "Missouri" },
  { value: "MT", label: "Montana" },
  { value: "NE", label: "Nebraska" },
  { value: "NV", label: "Nevada" },
  { value: "NH", label: "New Hampshire" },
  { value: "NJ", label: "New Jersey" },
  { value: "NM", label: "New Mexico" },
  { value: "NY", label: "New York" },
  { value: "NC", label: "North Carolina" },
  { value: "ND", label: "North Dakota" },
  { value: "OH", label: "Ohio" },
  { value: "OK", label: "Oklahoma" },
  { value: "OR", label: "Oregon" },
  { value: "PA", label: "Pennsylvania" },
  { value: "RI", label: "Rhode Island" },
  { value: "SC", label: "South Carolina" },
  { value: "SD", label: "South Dakota" },
  { value: "TN", label: "Tennessee" },
  { value: "TX", label: "Texas" },
  { value: "UT", label: "Utah" },
  { value: "VT", label: "Vermont" },
  { value: "VA", label: "Virginia" },
  { value: "WA", label: "Washington" },
  { value: "WV", label: "West Virginia" },
  { value: "WI", label: "Wisconsin" },
  { value: "WY", label: "Wyoming" },
];

// Common entity type slugs (mirrors API data / doc 07).
const ENTITY_TYPES: { value: string; label: string }[] = [
  { value: "school_district", label: "School District" },
  { value: "charter_school", label: "Charter School" },
  { value: "university", label: "University" },
  { value: "community_college", label: "Community College" },
  { value: "city", label: "City" },
  { value: "county", label: "County" },
  { value: "state_agency", label: "State Agency" },
  { value: "federal_agency", label: "Federal Agency" },
  { value: "special_district", label: "Special District" },
  { value: "tribal_nation", label: "Tribal Nation" },
];

// Status options.
const ENTITY_STATUSES: { value: string; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "dissolved", label: "Dissolved" },
  { value: "merged", label: "Merged" },
];

export interface EntityFiltersState {
  q: string;
  type: string;
  state: string;
  status: string;
}

interface EntityFiltersBarProps {
  filters: EntityFiltersState;
  onChange: (updated: EntityFiltersState) => void;
}

const selectClass =
  "rounded-md border bg-background px-2 py-1.5 text-sm text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

export function EntityFiltersBar({ filters, onChange }: EntityFiltersBarProps) {
  const set = useCallback(
    (key: keyof EntityFiltersState) =>
      (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
        onChange({ ...filters, [key]: e.target.value }),
    [filters, onChange],
  );

  const clearAll = useCallback(
    () => onChange({ q: "", type: "", state: "", status: "" }),
    [onChange],
  );

  const hasFilters =
    filters.q !== "" ||
    filters.type !== "" ||
    filters.state !== "" ||
    filters.status !== "";

  return (
    <div
      className="flex flex-col gap-3 sm:flex-row sm:items-center sm:flex-wrap"
      role="search"
      aria-label="Filter entities"
    >
      {/* Name search */}
      <label className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
        <span className="text-sm font-medium text-foreground sr-only sm:not-sr-only">
          Search
        </span>
        <input
          type="search"
          aria-label="Search entities by name"
          placeholder="Search by name…"
          value={filters.q}
          onChange={set("q")}
          className={`${selectClass} w-full sm:w-64`}
        />
      </label>

      {/* Type filter */}
      <label className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
        <span className="text-sm font-medium text-foreground sr-only sm:not-sr-only">
          Type
        </span>
        <select
          aria-label="Filter by entity type"
          value={filters.type}
          onChange={set("type")}
          className={selectClass}
        >
          <option value="">All types</option>
          {ENTITY_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </label>

      {/* State filter */}
      <label className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
        <span className="text-sm font-medium text-foreground sr-only sm:not-sr-only">
          State
        </span>
        <select
          aria-label="Filter by state"
          value={filters.state}
          onChange={set("state")}
          className={selectClass}
        >
          <option value="">All states</option>
          {US_STATES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>

      {/* Status filter */}
      <label className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
        <span className="text-sm font-medium text-foreground sr-only sm:not-sr-only">
          Status
        </span>
        <select
          aria-label="Filter by status"
          value={filters.status}
          onChange={set("status")}
          className={selectClass}
        >
          <option value="">All statuses</option>
          {ENTITY_STATUSES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>

      {hasFilters && (
        <button
          type="button"
          onClick={clearAll}
          aria-label="Clear all filters"
          className="rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
