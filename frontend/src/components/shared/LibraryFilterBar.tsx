import React, { useMemo, useState } from "react";
import { identityColor } from "../../utils/colors";

export type LibrarySortBy = "date" | "folder";
export type LibrarySortDirection = "desc" | "asc";

/**
 * The backend reports faces without a person under this placeholder name
 * (`UNKNOWN_PERSON_LABEL` in storage.py). It is *not* the "unknown person"
 * review status, so it gets its own pinned chip and a clearer label.
 */
const UNASSIGNED_FILTER_VALUE = "Unbekannt";
const UNASSIGNED_FILTER_LABEL = "Nicht zugewiesen";

/** Archived review statuses, offered as their own pinned filters. */
const STATUS_FILTERS: { value: string; label: string; hint: string }[] = [
  {
    value: "unknown_person",
    label: "Unbekannt",
    hint: "Gesichter, die du bewusst als unbekannte Person aussortiert hast",
  },
  {
    value: "not_face",
    label: "Kein Gesicht",
    hint: "Fehl-Erkennungen, die du aussortiert hast",
  },
];

/** Chips shown before the person list collapses behind a "show more" step. */
const COLLAPSED_LIMIT = 12;
/** From this many persons on, searching beats scanning. */
const SEARCH_THRESHOLD = 8;

/**
 * Two sort dimensions in one control. Stacking "sort by" and "direction" as
 * separate labelled selects cost two boxes and four lines for what is really a
 * single decision.
 */
const SORT_OPTIONS: {
  value: string;
  label: string;
  sortBy: LibrarySortBy;
  direction: LibrarySortDirection;
}[] = [
  { value: "date:desc", label: "Neueste zuerst", sortBy: "date", direction: "desc" },
  { value: "date:asc", label: "Älteste zuerst", sortBy: "date", direction: "asc" },
  { value: "folder:asc", label: "Ordner A–Z", sortBy: "folder", direction: "asc" },
  { value: "folder:desc", label: "Ordner Z–A", sortBy: "folder", direction: "desc" },
];

interface LibraryFilterBarProps {
  persons: string[];
  selectedPersons: string[];
  onPersonsChange: (persons: string[]) => void;

  sortBy: LibrarySortBy;
  sortDirection: LibrarySortDirection;
  onSortChange: (sortBy: LibrarySortBy, direction: LibrarySortDirection) => void;

  selectedFolderCount: number;
  onOpenFolderFilter: () => void;

  /** Matches for the active filter. `null` while still loading. */
  resultCount: number | null;
  /** Unfiltered size of the library, to show "162 von 3.000". */
  totalCount?: number | null;
  resultNoun: string;

  /** Archived statuses to include. Omit to hide those filters entirely. */
  faceStatuses?: string[];
  onFaceStatusesChange?: (statuses: string[]) => void;

  /** Optional extra compact control, e.g. the face-overlay toggle. */
  children?: React.ReactNode;
}

const LibraryFilterBar: React.FC<LibraryFilterBarProps> = ({
  persons,
  selectedPersons,
  onPersonsChange,
  sortBy,
  sortDirection,
  onSortChange,
  selectedFolderCount,
  onOpenFolderFilter,
  resultCount,
  totalCount = null,
  resultNoun,
  faceStatuses,
  onFaceStatusesChange,
  children,
}) => {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(false);

  const selectedSet = useMemo(() => new Set(selectedPersons), [selectedPersons]);

  // Selected persons first, then the rest alphabetically — so an active filter
  // stays visible no matter how long the list grows.
  const orderedPersons = useMemo(() => {
    const all = Array.from(new Set([...persons, ...selectedPersons])).filter(
      (person) => person !== UNASSIGNED_FILTER_VALUE,
    );
    return all.sort((a, b) => {
      const aSelected = selectedSet.has(a);
      const bSelected = selectedSet.has(b);
      if (aSelected !== bSelected) return aSelected ? -1 : 1;
      return a.localeCompare(b, "de", { sensitivity: "base" });
    });
  }, [persons, selectedPersons, selectedSet]);

  const query = search.trim().toLocaleLowerCase("de");
  const filteredPersons = useMemo(
    () =>
      query
        ? orderedPersons.filter((person) =>
            person.toLocaleLowerCase("de").includes(query),
          )
        : orderedPersons,
    [orderedPersons, query],
  );

  // Never hide an active selection behind the collapse.
  const visibleLimit = Math.max(COLLAPSED_LIMIT, selectedPersons.length);
  const showAll = expanded || query !== "";
  const visiblePersons = showAll
    ? filteredPersons
    : filteredPersons.slice(0, visibleLimit);
  const hiddenCount = filteredPersons.length - visiblePersons.length;

  const togglePerson = (person: string) => {
    if (selectedSet.has(person)) {
      onPersonsChange(selectedPersons.filter((entry) => entry !== person));
    } else {
      onPersonsChange([...selectedPersons, person]);
    }
  };

  const activeSortValue = `${sortBy}:${sortDirection}`;

  return (
    <section className="filter-bar" aria-label="Filter und Sortierung">
      <div className="filter-bar__controls">
        <div className="filter-bar__result">
          <strong>{resultCount === null ? "…" : resultCount.toLocaleString()}</strong>
          <span>
            {/* Only mention the whole library while a filter actually narrows it. */}
            {totalCount !== null && resultCount !== null && totalCount !== resultCount
              ? `von ${totalCount.toLocaleString()} ${resultNoun}`
              : resultNoun}
          </span>
        </div>

        <div className="filter-bar__tools">
          <div className="filter-bar__tool-group">
            <span className="filter-bar__tool-label">Filtern</span>
            <div className="filter-bar__tool-group-controls">
              {orderedPersons.length > SEARCH_THRESHOLD && (
                <input
                  className="filter-bar__search"
                  aria-label="Person suchen"
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Person suchen"
                  type="search"
                  value={search}
                />
              )}

              <button
                type="button"
                className={`filter-bar__control${
                  selectedFolderCount ? " filter-bar__control--active" : ""
                }`}
                onClick={onOpenFolderFilter}
                title="Nach Ordnern filtern"
              >
                <span className="folder-icon" aria-hidden="true" />
                Ordner
                {selectedFolderCount > 0 && <b>{selectedFolderCount}</b>}
              </button>
            </div>
          </div>

          <div className="filter-bar__tool-group">
            <span className="filter-bar__tool-label">Sortieren</span>
            <div className="filter-bar__tool-group-controls">
              <select
                className="filter-bar__control filter-bar__select"
                aria-label="Sortierung"
                value={activeSortValue}
                onChange={(event) => {
                  const option = SORT_OPTIONS.find(
                    (entry) => entry.value === event.target.value,
                  );
                  if (option) onSortChange(option.sortBy, option.direction);
                }}
              >
                {SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {children && (
            <div className="filter-bar__tool-group filter-bar__tool-group--view">
              <span className="filter-bar__tool-label">Ansicht</span>
              <div className="filter-bar__tool-group-controls">{children}</div>
            </div>
          )}
        </div>
      </div>

      <div className="filter-bar__chips">
        <span className="filter-bar__row-label">Personen</span>
        <button
          className={
            selectedPersons.length === 0
              ? "person-filter__chip person-filter__chip--active"
              : "person-filter__chip"
          }
          onClick={() => onPersonsChange([])}
          type="button"
        >
          Alle
        </button>

        {visiblePersons.map((person) => (
          <button
            key={person}
            onClick={() => togglePerson(person)}
            className={`person-filter__chip${
              selectedSet.has(person) ? " person-filter__chip--active" : ""
            }`}
            style={{ "--person-color": identityColor(person) } as React.CSSProperties}
            title={person}
            type="button"
          >
            {person}
          </button>
        ))}

        {hiddenCount > 0 && (
          <button
            className="person-filter__chip person-filter__chip--more"
            onClick={() => setExpanded(true)}
            type="button"
          >
            ＋ {hiddenCount} weitere
          </button>
        )}

        {expanded && !query && filteredPersons.length > COLLAPSED_LIMIT && (
          <button
            className="person-filter__chip person-filter__chip--more"
            onClick={() => setExpanded(false)}
            type="button"
          >
            Weniger
          </button>
        )}

        {query && filteredPersons.length === 0 && (
          <span className="filter-bar__hint">Keine Person passt zu „{search}“.</span>
        )}
        {orderedPersons.length === 0 && (
          <span className="filter-bar__hint">
            Noch keine benannten Personen. Ordne unter „Gesichter prüfen“ zuerst
            Gesichter einer Person zu.
          </span>
        )}
      </div>

      {/* Kept out of the person row: these are categories, not people. The
          divider alone sets them apart, so they can share the chip design. */}
      <div className="filter-bar__categories">
        <span className="filter-bar__row-label">Status</span>
        <button
          className={`person-filter__chip${
            selectedSet.has(UNASSIGNED_FILTER_VALUE) ? " person-filter__chip--active" : ""
          }`}
          onClick={() => togglePerson(UNASSIGNED_FILTER_VALUE)}
          title="Gesichter, die noch keiner Person zugeordnet sind"
          type="button"
        >
          {UNASSIGNED_FILTER_LABEL}
        </button>

        {faceStatuses !== undefined &&
          onFaceStatusesChange &&
          STATUS_FILTERS.map((status) => {
            const isActive = faceStatuses.includes(status.value);
            return (
              <button
                key={status.value}
                className={`person-filter__chip${
                  isActive ? " person-filter__chip--active" : ""
                }`}
                onClick={() =>
                  onFaceStatusesChange(
                    isActive
                      ? faceStatuses.filter((entry) => entry !== status.value)
                      : [...faceStatuses, status.value],
                  )
                }
                title={status.hint}
                type="button"
              >
                {status.label}
              </button>
            );
          })}
      </div>
    </section>
  );
};

export default LibraryFilterBar;
