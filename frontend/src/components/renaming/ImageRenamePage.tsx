import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ImageRenameCandidate,
  applyImageRenames,
  fetchImageRenameCandidateCount,
  fetchImageRenameCandidates,
  openImageLocation,
} from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import { copyTextToClipboard } from "../../utils/clipboard";
import LibraryFilterBar from "../shared/LibraryFilterBar";
import FolderFilterModal from "../shared/FolderFilterModal";
import { subscribeToTopic } from "../../utils/events";

const DEFAULT_PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];
const SKELETON_ROW_COUNT = 6;
type ImageGroupingMode = "date" | "folder";
type SortDirection = "desc" | "asc";

const ImageRenamePage: React.FC<{ onOpenFilenameSettings: () => void }> = ({
  onOpenFilenameSettings,
}) => {
  const [items, setItems] = useState<ImageRenameCandidate[]>([]);
  const [availablePersons, setAvailablePersons] = useState<string[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [groupingMode, setGroupingMode] = useState<ImageGroupingMode>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState<number | null>(null);
  // Unfiltered candidate count, so the bar can say "162 von 3.000".
  const [libraryTotal, setLibraryTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isTotalLoading, setIsTotalLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectionMode, setSelectionMode] = useState<"manual" | "all">("manual");
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [excludedPaths, setExcludedPaths] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pageJumpInput, setPageJumpInput] = useState("1");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const activeRequestRef = useRef(0);
  const activeAbortRef = useRef<AbortController | null>(null);
  const totalAbortRef = useRef<AbortController | null>(null);

  const loadTotalCount = async () => {
    const controller = new AbortController();
    totalAbortRef.current?.abort();
    totalAbortRef.current = controller;
    setIsTotalLoading(true);
    setTotal(null);
    try {
      const nextTotal = await fetchImageRenameCandidateCount({
        folders: selectedFolders,
        persons: selectedPersons,
        sortBy: groupingMode,
        sortDirection,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        setTotal(nextTotal);
      }
    } catch {
      if (!controller.signal.aborted) {
        setTotal(null);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsTotalLoading(false);
      }
    }
  };

  const loadData = async (nextOffset = 0) => {
    const requestId = activeRequestRef.current + 1;
    activeRequestRef.current = requestId;
    activeAbortRef.current?.abort();
    const controller = new AbortController();
    activeAbortRef.current = controller;

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
        signal: controller.signal,
      });
      if (activeRequestRef.current !== requestId) {
        return;
      }
      setItems(renameData.items);
      setAvailablePersons(renameData.available_persons);
      setSelectedPersons((current) => {
        const next = current.filter((person) =>
          renameData.available_persons.includes(person),
        );
        return next.length === current.length ? current : next;
      });
      if (renameData.total !== null) {
        setTotal(renameData.total);
      }
      setOffset(renameData.offset);
      setHasMore(renameData.has_more);
      setHasLoadedOnce(true);
    } catch (loadError) {
      if (controller.signal.aborted) {
        return;
      }
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Die Umbenennungsvorschläge konnten nicht geladen werden.",
      );
    } finally {
      if (activeRequestRef.current === requestId) {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    void loadData(0);
  }, [groupingMode, pageSize, sortDirection, selectedFolders, selectedPersons]);

  useEffect(() => {
    return () => {
      activeAbortRef.current?.abort();
      totalAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    void loadTotalCount();
  }, [groupingMode, sortDirection, selectedFolders, selectedPersons]);

  useEffect(() => {
    handleClearSelection();
  }, [groupingMode, pageSize, sortDirection, selectedFolders, selectedPersons]);

  const selectedCount = useMemo(() => {
    if (selectionMode === "all") {
      return total === null ? 0 : Math.max(total - excludedPaths.size, 0);
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

  // Unfiltered candidate count, kept independent of the active filter so the
  // bar can always say how much the filter narrowed things down.
  useEffect(() => {
    let isMounted = true;
    const refreshLibraryTotal = () => {
      void fetchImageRenameCandidates({ limit: 1, offset: 0 })
        .then((page) => {
          if (isMounted && page.total !== null) setLibraryTotal(page.total);
        })
        .catch(() => undefined);
    };
    refreshLibraryTotal();
    const unsubscribe = subscribeToTopic("clusters", refreshLibraryTotal);
    return () => {
      isMounted = false;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    const unsubscribeClusters = subscribeToTopic("clusters", () => {
      handleClearSelection();
      void loadData(offset);
      void loadTotalCount();
    });
    return unsubscribeClusters;
  }, [groupingMode, offset, pageSize, selectedFolders, selectedPersons, sortDirection]);

  const handleSubmit = async () => {
    if (selectedCount === 0) {
      setError("Wähle mindestens eine Datei aus.");
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
        `${result.renamed_count} Dateinamen aktualisiert, ${result.skipped_count} übersprungen, ${result.error_count} nicht geändert.`,
      );
      handleClearSelection();
      await loadData(offset);
      await loadTotalCount();
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

  const copyCandidatePath = async (
    event: React.MouseEvent<HTMLButtonElement>,
    path: string,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    setError(null);
    try {
      await copyTextToClipboard(path);
      setMessage("Dateipfad kopiert.");
    } catch (copyError) {
      setError(
        copyError instanceof Error
          ? copyError.message
          : "Der Dateipfad konnte nicht kopiert werden.",
      );
    }
  };

  const revealCandidate = async (
    event: React.MouseEvent<HTMLButtonElement>,
    item: ImageRenameCandidate,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    setError(null);
    try {
      await openImageLocation(item.image_id, item.path);
      setMessage("Dateispeicherort geöffnet.");
    } catch (openError) {
      setError(
        openError instanceof Error
          ? openError.message
          : "Der Dateispeicherort konnte nicht geöffnet werden.",
      );
    }
  };

  const handlePageChange = (nextOffset: number) => {
    void loadData(nextOffset);
  };

  const pageStart = items.length === 0 ? 0 : offset + 1;
  const pageEnd =
    total === null ? offset + items.length : Math.min(offset + items.length, total);
  const currentPage = offset === 0 ? 1 : Math.floor(offset / pageSize) + 1;
  const pageCount = total === null ? null : Math.max(1, Math.ceil(total / pageSize));
  const showSkeletonRows = isLoading && !hasLoadedOnce;
  const showLoadingOverlay = isLoading && hasLoadedOnce;

  useEffect(() => {
    setPageJumpInput(String(currentPage));
  }, [currentPage]);

  const handleJumpToPage = () => {
    if (pageCount === null) {
      return;
    }
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
          Zeige {pageStart}-{pageEnd}
          {total === null ? " von …" : ` von ${total}`}
        </span>
        <span>
          Seite {currentPage}
          {pageCount === null ? "" : ` von ${pageCount}`}
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
            disabled={isLoading || (pageCount === null ? !hasMore : currentPage >= pageCount)}
          >
            Weiter
          </button>
          <button
            className="neon-card"
            onClick={() => handlePageChange(((pageCount || 1) - 1) * pageSize)}
            disabled={isLoading || pageCount === null || currentPage >= pageCount}
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
              max={pageCount ?? undefined}
              value={pageJumpInput}
              onChange={(event) => setPageJumpInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  handleJumpToPage();
                }
              }}
              disabled={isLoading || pageCount === null}
            />
            <button
              className="neon-card"
              onClick={handleJumpToPage}
              disabled={isLoading || pageCount === null}
            >
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
      <div className="settings-page__header rename-page-header">
        <div>
          <div className="settings-page__eyebrow">Bibliothek</div>
          <h1 className="settings-page__title">Dateinamen</h1>
          <p className="settings-page__copy">
            Ergänze erkannte Personen im Dateinamen. Du siehst vor jeder Änderung
            den bisherigen und den vorgeschlagenen Namen.
          </p>
        </div>
        <button
          className="rename-settings-link"
          type="button"
          onClick={onOpenFilenameSettings}
        >
          Benennungsschema ändern
          <span aria-hidden="true">→</span>
        </button>
      </div>

      <LibraryFilterBar
        persons={availablePersons}
        selectedPersons={selectedPersons}
        onPersonsChange={setSelectedPersons}
        sortBy={groupingMode}
        sortDirection={sortDirection}
        onSortChange={(nextSortBy, nextDirection) => {
          setGroupingMode(nextSortBy);
          setSortDirection(nextDirection);
        }}
        selectedFolderCount={selectedFolders.length}
        onOpenFolderFilter={() => setShowFolderFilter(true)}
        resultCount={total}
        totalCount={libraryTotal}
        resultNoun="Dateien"
      />

      {selectedFolders.length > 0 && (
        <div className="active-folder-filters">
          <span>Ausgewählte Ordner</span>
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
            Ordnerfilter entfernen
          </button>
        </div>
      )}

      <section className="settings-card rename-toolbar">
        <div className="rename-toolbar__heading">
          <strong>Dateien auswählen und aktualisieren</strong>
          <span>Wähle einzelne Dateien, die aktuelle Seite oder alle Treffer.</span>
        </div>
        <div className="rename-toolbar__controls">
          <div className="settings-actions">
            <button className="neon-card" onClick={handleSelectPage} disabled={items.length === 0}>
              Seite wählen ({items.length})
            </button>
            <button
              className="neon-card"
              onClick={handleSelectAll}
              disabled={total === null || total === 0 || isTotalLoading}
            >
              Alle wählen ({total ?? "…"})
            </button>
            <button className="neon-card" onClick={handleClearSelection} disabled={selectedCount === 0}>
              Auswahl aufheben
            </button>
          </div>
          <button
            className="neon-button rename-toolbar__apply"
            onClick={handleSubmit}
            disabled={isSubmitting || selectedCount === 0}
          >
            {isSubmitting ? "Dateinamen werden aktualisiert…" : `${selectedCount} Dateinamen aktualisieren`}
          </button>
        </div>
      </section>

      <section className="settings-card rename-list-card">
        <div className="rename-list-status">
          <div className="rename-list-status__pill">
            Ausgewählt: <strong>{selectionMode === "all" && total === null ? "…" : selectedCount}</strong>
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
            Alle Dateinamen entsprechen bereits deinem Benennungsschema.
          </div>
        ) : (
          <div className="rename-list-shell">
            {showLoadingOverlay && <div className="rename-list-overlay" aria-hidden="true" />}
            <div className="rename-list">
              {items.map((item) => (
                <div className="rename-row" key={item.path}>
                  <input
                    type="checkbox"
                    checked={isSelected(item.path)}
                    onChange={() => toggleItem(item.path)}
                    aria-label={`${item.current_filename} auswählen`}
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
                    <div className="rename-row__file-actions">
                      <button
                        type="button"
                        onClick={(event) => void copyCandidatePath(event, item.path)}
                        onKeyDown={(event) => event.stopPropagation()}
                        title="Vollständigen Dateipfad kopieren"
                      >
                        Pfad kopieren
                      </button>
                      <button
                        type="button"
                        onClick={(event) => void revealCandidate(event, item)}
                        onKeyDown={(event) => event.stopPropagation()}
                        title="Datei im Explorer oder Dateimanager anzeigen"
                      >
                        Speicherort öffnen
                      </button>
                    </div>
                  </div>
                </div>
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
