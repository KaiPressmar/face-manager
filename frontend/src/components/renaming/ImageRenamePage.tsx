import React, { useEffect, useMemo, useState } from "react";
import {
  AppSettings,
  ImageRenameCandidate,
  applyImageRenames,
  fetchImageRenameCandidates,
  fetchSettings,
} from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import PersonFilter from "../people/PersonFilter";
import FolderFilterModal from "../shared/FolderFilterModal";

const DEFAULT_PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];
const SKELETON_ROW_COUNT = 6;
type ImageGroupingMode = "date" | "folder";
type SortDirection = "desc" | "asc";

const ImageRenamePage: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [items, setItems] = useState<ImageRenameCandidate[]>([]);
  const [availablePersons, setAvailablePersons] = useState<string[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [groupingMode, setGroupingMode] = useState<ImageGroupingMode>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectionMode, setSelectionMode] = useState<"manual" | "all">("manual");
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [excludedPaths, setExcludedPaths] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageJumpInput, setPageJumpInput] = useState("1");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then((data) => {
        if (!cancelled) {
          setSettings(data);
        }
      })
      .catch(() => {
        // Keep the page usable even if settings metadata is temporarily unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadData = async (nextOffset = 0) => {
    setIsLoading(true);
    setError(null);
    try {
      const renameData = await fetchImageRenameCandidates({
        folders: selectedFolders,
        persons: selectedPersons,
        sortBy: groupingMode,
        sortDirection,
        limit: pageSize,
        offset: nextOffset,
      });
      setItems(renameData.items);
      setAvailablePersons(renameData.available_persons);
      setSelectedPersons((current) => {
        const next = current.filter((person) =>
          renameData.available_persons.includes(person),
        );
        return next.length === current.length ? current : next;
      });
      setTotal(renameData.total);
      setOffset(renameData.offset);
      setHasLoadedOnce(true);
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Die Umbenennungsvorschlaege konnten nicht geladen werden.",
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadData(0);
  }, [groupingMode, pageSize, sortDirection, selectedFolders, selectedPersons]);

  useEffect(() => {
    handleClearSelection();
  }, [groupingMode, pageSize, sortDirection, selectedFolders, selectedPersons]);

  const selectedCount = useMemo(() => {
    if (selectionMode === "all") {
      return Math.max(total - excludedPaths.size, 0);
    }
    return selectedPaths.size;
  }, [excludedPaths.size, selectedPaths.size, selectionMode, total]);

  const isSelected = (path: string) =>
    selectionMode === "all"
      ? !excludedPaths.has(path)
      : selectedPaths.has(path);

  const toggleItem = (path: string) => {
    setMessage(null);
    setError(null);
    if (selectionMode === "all") {
      setExcludedPaths((current) => {
        const next = new Set(current);
        if (next.has(path)) {
          next.delete(path);
        } else {
          next.add(path);
        }
        return next;
      });
      return;
    }

    setSelectedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const handleSelectPage = () => {
    setSelectionMode("manual");
    setExcludedPaths(new Set());
    setSelectedPaths(new Set(items.map((item) => item.path)));
  };

  const handleSelectAll = () => {
    setSelectionMode("all");
    setSelectedPaths(new Set());
    setExcludedPaths(new Set());
  };

  const handleClearSelection = () => {
    setSelectionMode("manual");
    setSelectedPaths(new Set());
    setExcludedPaths(new Set());
  };

  const handleSubmit = async () => {
    if (selectedCount === 0) {
      setError("Bitte waehlen Sie mindestens einen Dateipfad aus.");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      const result = await applyImageRenames(
        selectionMode === "all"
          ? {
              rename_all: true,
              excluded_paths: Array.from(excludedPaths),
              folders: selectedFolders,
              persons: selectedPersons,
              sort_by: groupingMode,
              sort_direction: sortDirection,
            }
          : {
              selected_paths: Array.from(selectedPaths),
              folders: selectedFolders,
              persons: selectedPersons,
              sort_by: groupingMode,
              sort_direction: sortDirection,
            },
      );
      setMessage(
        `${result.renamed_count} Dateipfade aktualisiert, ${result.skipped_count} uebersprungen, ${result.error_count} Fehler.`,
      );
      handleClearSelection();
      await loadData(offset);
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Die Dateinamen konnten nicht aktualisiert werden.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handlePageChange = (nextOffset: number) => {
    void loadData(nextOffset);
  };

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + items.length, total);
  const currentPage = total === 0 ? 1 : Math.floor(offset / pageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const showSkeletonRows = isLoading && !hasLoadedOnce;
  const showLoadingOverlay = isLoading && hasLoadedOnce;

  useEffect(() => {
    setPageJumpInput(String(currentPage));
  }, [currentPage]);

  const handleJumpToPage = () => {
    const parsed = Number.parseInt(pageJumpInput, 10);
    if (Number.isNaN(parsed)) {
      setPageJumpInput(String(currentPage));
      return;
    }
    const nextPage = Math.min(Math.max(parsed, 1), pageCount);
    void loadData((nextPage - 1) * pageSize);
  };

  const renderPaginationControls = (position: "top" | "bottom") => (
    <div
      className={`rename-pagination rename-pagination--${position}`}
      aria-label={position === "top" ? "Seitennavigation oben" : "Seitennavigation unten"}
    >
      <div className="rename-pagination__summary">
        <span>
          Zeige {pageStart}-{pageEnd} von {total}
        </span>
        <span>
          Seite {currentPage} von {pageCount}
        </span>
      </div>

      <div className="rename-pagination__controls">
        <label className="rename-pagination__field">
          <span>Pro Seite</span>
          <select
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
            disabled={isLoading}
          >
            {PAGE_SIZE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>

        <div className="rename-pagination__buttons">
          <button
            className="neon-card"
            onClick={() => handlePageChange(0)}
            disabled={isLoading || currentPage <= 1}
          >
            Erste
          </button>
          <button
            className="neon-card"
            onClick={() => handlePageChange(Math.max(offset - pageSize, 0))}
            disabled={isLoading || currentPage <= 1}
          >
            Zurück
          </button>
          <button
            className="neon-card"
            onClick={() => handlePageChange(offset + pageSize)}
            disabled={isLoading || currentPage >= pageCount}
          >
            Weiter
          </button>
          <button
            className="neon-card"
            onClick={() => handlePageChange((pageCount - 1) * pageSize)}
            disabled={isLoading || currentPage >= pageCount}
          >
            Letzte
          </button>
        </div>

        <label className="rename-pagination__field rename-pagination__field--jump">
          <span>Gehe zu Seite</span>
          <div className="rename-pagination__jump">
            <input
              type="number"
              min="1"
              max={pageCount}
              value={pageJumpInput}
              onChange={(event) => setPageJumpInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  handleJumpToPage();
                }
              }}
              disabled={isLoading}
            />
            <button className="neon-card" onClick={handleJumpToPage} disabled={isLoading}>
              Springen
            </button>
          </div>
        </label>
      </div>
    </div>
  );

  const renderSkeletonRows = () => (
    <div className="rename-list rename-list--loading" aria-hidden="true">
      {Array.from({ length: SKELETON_ROW_COUNT }).map((_, index) => (
        <div className="rename-row rename-row--skeleton" key={index}>
          <div className="rename-row__checkbox-skeleton shimmer-block" />
          <div className="rename-row__content">
            <div className="rename-row__path-skeleton shimmer-block" />
            <div className="rename-row__meta-skeleton">
              <div className="shimmer-block" />
              <div className="shimmer-block" />
            </div>
            <div className="rename-row__people-skeleton shimmer-block" />
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div className="settings-page">
      <div className="settings-page__header">
        <div>
          <div className="settings-page__eyebrow">Dateinamen</div>
          <h1 className="settings-page__title">Personennamen an Bilddateien haengen</h1>
          <p className="settings-page__copy">
            Diese Liste zeigt alle Bildpfade, deren Dateiname die erkannten
            Personen noch nicht vollstaendig oder nicht in der Reihenfolge von
            links nach rechts enthaelt.
          </p>
        </div>
      </div>

      <div className="people-toolbar">
        <PersonFilter
          persons={availablePersons}
          selected={selectedPersons}
          onChange={setSelectedPersons}
        />

        <div className="people-toolbar-actions">
          <div className="people-sort-panel">
            <label className="people-sort-panel__field">
              <span>Sortieren nach</span>
              <select
                value={groupingMode}
                onChange={(event) =>
                  setGroupingMode(event.target.value as ImageGroupingMode)
                }
              >
                <option value="date">Erstellungsdatum</option>
                <option value="folder">Ordnerpfad</option>
              </select>
            </label>

            <label className="people-sort-panel__field">
              <span>Reihenfolge</span>
              <select
                value={sortDirection}
                onChange={(event) =>
                  setSortDirection(event.target.value as SortDirection)
                }
              >
                <option value="desc">Absteigend</option>
                <option value="asc">Aufsteigend</option>
              </select>
            </label>
          </div>

          <div className="people-toolbar-count">
            {total.toLocaleString()} Dateipfade
          </div>

          <button
            className={`folder-filter-trigger${selectedFolders.length ? " folder-filter-trigger--active" : ""}`}
            onClick={() => setShowFolderFilter(true)}
          >
            <span className="folder-icon" aria-hidden="true" />
            <span>
              <strong>Ordnerfilter</strong>
              <small>
                {selectedFolders.length
                  ? `${selectedFolders.length} ausgewählt`
                  : "Alle Ordner"}
              </small>
            </span>
            {selectedFolders.length > 0 && <b>{selectedFolders.length}</b>}
          </button>
        </div>
      </div>

      {selectedFolders.length > 0 && (
        <div className="active-folder-filters">
          <span>Aktive Ordner</span>
          {selectedFolders.map((folder) => (
            <button
              key={folder}
              title={folder}
              onClick={() =>
                setSelectedFolders((current) =>
                  current.filter((path) => path !== folder),
                )
              }
            >
              {pathBasename(folder)}
              <b>×</b>
            </button>
          ))}
          <button className="clear-folder-filters" onClick={() => setSelectedFolders([])}>
            Alle löschen
          </button>
        </div>
      )}

      <section className="settings-card rename-toolbar">
        <div className="settings-actions">
          <button className="neon-card" onClick={handleSelectPage} disabled={items.length === 0}>
            Seite waehlen ({items.length})
          </button>
          <button className="neon-card" onClick={handleSelectAll} disabled={total === 0}>
            Alle waehlen ({total})
          </button>
          <button className="neon-card" onClick={handleClearSelection} disabled={selectedCount === 0}>
            Auswahl loeschen
          </button>
          <button className="neon-card" onClick={handleSubmit} disabled={isSubmitting || selectedCount === 0}>
            {isSubmitting ? "Aktualisieren…" : `${selectedCount} Dateipfade aktualisieren`}
          </button>
        </div>
        <div className="settings-meta">
          <span>Aktuelle Schreibweise</span>
          <code>{settings?.filename_person_suffix_format || "DATEI Kai, Regina.jpg"}</code>
        </div>
      </section>

      <section className="settings-card rename-list-card">
        <div className="rename-list-status">
          <div className="rename-list-status__pill">
            Ausgewählt: <strong>{selectedCount}</strong>
          </div>
          {showLoadingOverlay && (
            <div className="rename-list-status__loading">
              <span className="rename-spinner" aria-hidden="true" />
              Liste wird aktualisiert…
            </div>
          )}
        </div>

        {renderPaginationControls("top")}

        {showSkeletonRows ? (
          renderSkeletonRows()
        ) : items.length === 0 ? (
          <div className="rename-empty-state">
            Alle bekannten Dateinamen entsprechen bereits dem aktuellen Format.
          </div>
        ) : (
          <div className="rename-list-shell">
            {showLoadingOverlay && <div className="rename-list-overlay" aria-hidden="true" />}
            <div className="rename-list">
              {items.map((item) => (
                <label className="rename-row" key={item.path}>
                  <input
                    type="checkbox"
                    checked={isSelected(item.path)}
                    onChange={() => toggleItem(item.path)}
                  />
                  <div className="rename-row__content">
                    <div className="rename-row__path">{item.path}</div>
                    <div className="rename-row__meta">
                      <span>Aktuell: {item.current_filename}</span>
                      <span>Neu: {item.proposed_filename}</span>
                    </div>
                    <div className="rename-row__people">
                      Personen: {item.detected_person_names.join(", ")}
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>
        )}

        {renderPaginationControls("bottom")}
      </section>

      {(message || error) && (
        <div
          className={
            error
              ? "settings-feedback settings-feedback--error"
              : "settings-feedback"
          }
        >
          {error || message}
        </div>
      )}

      {showFolderFilter && (
        <FolderFilterModal
          selected={selectedFolders}
          onClose={() => setShowFolderFilter(false)}
          onApply={(folders) => {
            setSelectedFolders(folders);
            setShowFolderFilter(false);
          }}
        />
      )}
    </div>
  );
};

export default ImageRenamePage;
