import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  type ApplyImageRenamePayload,
  type ApplyImageRenameResult,
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
import { useModalFocus } from "../../hooks/useModalFocus";
import "./ImageRenamePage.css";

const DEFAULT_PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];
const SKELETON_ROW_COUNT = 6;
const LIVE_REFRESH_BATCH_MS = 2500;
const FINAL_REFRESH_DELAY_MS = 120;
const USER_IDLE_DELAY_MS = 900;
type ImageGroupingMode = "date" | "folder";
type SortDirection = "desc" | "asc";

interface RenameReview {
  payload: ApplyImageRenamePayload;
  count: number;
  example?: ImageRenameCandidate;
}

interface RenameReport {
  result: ApplyImageRenameResult;
  unreported: number;
}

function skipLabel(reason: string) {
  switch (reason) {
    case "already_matches": return "Bereits passend benannt";
    case "target_exists": return "Zielname bereits belegt";
    case "missing_source": return "Datei nicht erreichbar";
    case "invalid_filename": return "Vorgeschlagener Name nicht zulässig";
    case "filename_too_long": return "Vorgeschlagener Dateiname zu lang";
    default: return "Übersprungen";
  }
}

function isSelectable(item: ImageRenameCandidate) {
  return !item.validation_error && item.current_filename !== item.proposed_filename && !/[/\\\u0000]/.test(item.proposed_filename);
}

function filenameWarning(item: ImageRenameCandidate) {
  if (item.validation_error === "filename_too_long") {
    return "Der Dateiname ist zu lang. Kürze den bestehenden Namen, damit die Personennamen ergänzt werden können.";
  }
  if (!isSelectable(item)) {
    return item.current_filename === item.proposed_filename
      ? "Bereits passend benannt"
      : "Der vorgeschlagene Name enthält ein unzulässiges Zeichen.";
  }
  if (item.omitted_person_count === 1) return "Der Dateiname hätte sonst nicht gepasst. Ein Personenname entfällt.";
  if (item.omitted_person_count) return `Der Dateiname hätte sonst nicht gepasst. ${item.omitted_person_count} Personennamen entfallen.`;
  return null;
}

const ImageRenamePage: React.FC<{
  active: boolean;
  onOpenFilenameSettings: () => void;
}> = ({ active, onOpenFilenameSettings }) => {
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
  const [renameReview, setRenameReview] = useState<RenameReview | null>(null);
  const [renameReport, setRenameReport] = useState<RenameReport | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mutationRefresh, setMutationRefresh] = useState(0);
  const [pageJumpInput, setPageJumpInput] = useState("1");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const activeRequestRef = useRef(0);
  const pendingOffsetRef = useRef(0);
  const activeAbortRef = useRef<AbortController | null>(null);
  const totalAbortRef = useRef<AbortController | null>(null);
  const hasActivatedLiveRefreshRef = useRef(false);
  const reviewDialogRef = useRef<HTMLElement>(null);
  const reportRef = useRef<HTMLElement>(null);
  const submittingRef = useRef(false);
  const reviewOpenRef = useRef(false);
  reviewOpenRef.current = renameReview !== null;
  useModalFocus(reviewDialogRef, () => setRenameReview(null), !isSubmitting, active && renameReview !== null);

  useEffect(() => {
    if (!active) setRenameReview(null);
  }, [active]);

  useEffect(() => {
    if (active && renameReport) reportRef.current?.focus();
  }, [renameReport]);

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
        if (selectedFolders.length === 0 && selectedPersons.length === 0) {
          setLibraryTotal(nextTotal);
        }
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
    pendingOffsetRef.current = nextOffset;
    const requestId = activeRequestRef.current + 1;
    activeRequestRef.current = requestId;
    activeAbortRef.current?.abort();
    const controller = new AbortController();
    activeAbortRef.current = controller;

    setIsLoading(true);
    setLoadError(null);
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
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      setLoadError(
        error instanceof Error
          ? error.message
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
    if (!mutationRefresh) return;
    // Refresh the filters visible when the write completes. The user may have
    // changed folders or people while the selected files were being renamed.
    void Promise.all([loadData(pendingOffsetRef.current), loadTotalCount()]);
  }, [mutationRefresh]);

  const pageOutOfRange = total !== null && offset > 0 && offset >= total;
  useEffect(() => {
    if (total === null || !pageOutOfRange || isLoading || isTotalLoading || loadError) return;
    const lastOffset = Math.floor(Math.max(0, total - 1) / pageSize) * pageSize;
    void loadData(lastOffset);
  }, [total, pageOutOfRange, isLoading, isTotalLoading, loadError, pageSize]);

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
  const selectableItems = items.filter(isSelectable);
  const canReview = selectedCount > 0 && !isLoading && !loadError && !isSubmitting;
  const exampleWarning = renameReview?.example && filenameWarning(renameReview.example);

  const toggleItem = (path: string) => {
    setMessage(null);
    setActionError(null);
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
    setSelectedPaths((current) => new Set([
      ...current,
      ...selectableItems.map((item) => item.path),
    ]));
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

  useEffect(() => {
    if (!active) return;
    let isMounted = true;
    let refreshInFlight = false;
    let refreshPending = false;
    let refreshTimer: number | null = null;
    let userBusyUntil = 0;

    // Unfiltered candidate count, kept independent of the active filter so the
    // bar can always say how much the filter narrowed things down.
    const libraryTotalController = new AbortController();
    const refreshLibraryTotal = async () => {
      // The visible count already supplies this value when no filter is active.
      if (selectedFolders.length === 0 && selectedPersons.length === 0) return;
      try {
        const count = await fetchImageRenameCandidateCount({ signal: libraryTotalController.signal });
        if (isMounted) setLibraryTotal(count);
      } catch {
        // Keep the existing total and retry at the next live checkpoint.
      }
    };

    const refreshVisibleData = async () => {
      if (document.visibilityState !== "visible") return;
      if (reviewOpenRef.current || submittingRef.current) {
        scheduleRefresh();
        return;
      }
      const idleIn = userBusyUntil - performance.now();
      if (idleIn > 0) {
        scheduleRefresh(idleIn + 50);
        return;
      }
      if (refreshInFlight) {
        refreshPending = true;
        return;
      }
      refreshInFlight = true;
      try {
        await Promise.all([
          loadData(offset),
          loadTotalCount(),
          refreshLibraryTotal(),
        ]);
      } finally {
        refreshInFlight = false;
        if (isMounted && refreshPending) {
          refreshPending = false;
          scheduleRefresh(LIVE_REFRESH_BATCH_MS);
        }
      }
    };

    const scheduleRefresh = (
      delay = LIVE_REFRESH_BATCH_MS,
      replacePending = false,
    ) => {
      if (refreshTimer !== null) {
        if (!replacePending) return;
        window.clearTimeout(refreshTimer);
      }
      refreshTimer = window.setTimeout(() => {
        refreshTimer = null;
        void refreshVisibleData();
      }, delay);
    };

    const noteUserActivity = () => {
      userBusyUntil = performance.now() + USER_IDLE_DELAY_MS;
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        scheduleRefresh(FINAL_REFRESH_DELAY_MS, true);
      }
    };
    const handleWindowFocus = () =>
      scheduleRefresh(FINAL_REFRESH_DELAY_MS, true);
    const unsubscribeClusters = subscribeToTopic<{ reason?: string }>(
      "clusters",
      (update) => {
        const isBackgroundCheckpoint = update?.reason === "background_progress";
        scheduleRefresh(
          isBackgroundCheckpoint ? LIVE_REFRESH_BATCH_MS : FINAL_REFRESH_DELAY_MS,
          !isBackgroundCheckpoint,
        );
      },
    );
    document.addEventListener("visibilitychange", handleVisibilityChange);
    document.addEventListener("scroll", noteUserActivity, true);
    document.addEventListener("pointerdown", noteUserActivity, true);
    window.addEventListener("focus", handleWindowFocus);
    if (hasActivatedLiveRefreshRef.current) {
      scheduleRefresh(FINAL_REFRESH_DELAY_MS, true);
    } else {
      hasActivatedLiveRefreshRef.current = true;
      void refreshLibraryTotal();
    }

    return () => {
      isMounted = false;
      libraryTotalController.abort();
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      unsubscribeClusters();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      document.removeEventListener("scroll", noteUserActivity, true);
      document.removeEventListener("pointerdown", noteUserActivity, true);
      window.removeEventListener("focus", handleWindowFocus);
    };
  }, [active, groupingMode, offset, pageSize, selectedFolders, selectedPersons, sortDirection]);

  const openRenameReview = () => {
    if (!canReview) return;
    setActionError(null);
    setRenameReview({
      count: selectedCount,
      example: selectableItems.find((item) => isSelected(item.path)),
      payload: {
        ...(selectionMode === "all"
          ? { rename_all: true, excluded_paths: Array.from(excludedPaths) }
          : { selected_paths: Array.from(selectedPaths) }),
        folders: selectedFolders,
        persons: selectedPersons,
        sort_by: groupingMode,
        sort_direction: sortDirection,
      },
    });
  };

  const handleSubmit = async () => {
    if (!renameReview || submittingRef.current || !active) return;
    submittingRef.current = true;
    setIsSubmitting(true);
    setActionError(null);
    setMessage(null);
    setRenameReport(null);
    try {
      const result = await applyImageRenames(renameReview.payload);
      const reported = result.renamed_count + result.skipped_count + result.error_count;
      setRenameReport({
        result,
        unreported: renameReview.payload.rename_all ? 0 : Math.max(0, renameReview.count - reported),
      });
      handleClearSelection();
    } catch (submitError) {
      const detail = submitError instanceof Error
        ? submitError.message
        : "Die Rückmeldung zum Umbenennen fehlt.";
      setActionError(`${detail} Prüfe die aktualisierte Vorschau vor einem erneuten Versuch.`);
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
      setRenameReview(null);
      setMutationRefresh((value) => value + 1);
    }
  };

  const copyCandidatePath = async (
    event: React.MouseEvent<HTMLButtonElement>,
    path: string,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    setActionError(null);
    try {
      await copyTextToClipboard(path);
      setMessage("Dateipfad kopiert.");
    } catch (copyError) {
      setActionError(
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
    setActionError(null);
    try {
      await openImageLocation(item.image_id, item.path);
      setMessage("Dateispeicherort geöffnet.");
    } catch (openError) {
      setActionError(
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
                  event.preventDefault();
                  handleJumpToPage();
                } else if (event.key === "Escape") {
                  setPageJumpInput(String(currentPage));
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

  const renderRenameReport = () => {
    if (!renameReport) return null;
    const { result, unreported } = renameReport;
    const skipped = result.skipped ?? [];
    const errors = result.errors ?? [];
    const renamed = result.renamed ?? [];
    const skippedByReason = new Map<string, number>();
    for (const item of skipped) {
      const label = skipLabel(item.reason);
      skippedByReason.set(label, (skippedByReason.get(label) ?? 0) + 1);
    }
    if (result.skipped_count > skipped.length) {
      const label = "Übersprungen";
      skippedByReason.set(label, (skippedByReason.get(label) ?? 0) + result.skipped_count - skipped.length);
    }
    const details: { path: string; label: string; description?: string; destination?: string }[] = [
      ...errors.slice(0, 12).map((item) => ({ path: item.path, label: "Fehler", description: item.reason })),
      ...skipped.slice(0, 12).map((item) => ({ path: item.path, label: skipLabel(item.reason) })),
      ...renamed.slice(0, 12).map((item) => ({ path: item.from_path, label: "Umbenannt", destination: item.to_path })),
    ];
    const entryCount = errors.length + skipped.length + renamed.length;

    return (
      <section ref={reportRef} className="settings-card rename-outcome" tabIndex={-1} aria-labelledby="rename-outcome-title">
        <div className="rename-outcome__header">
          <h2 id="rename-outcome-title">Ergebnis der Umbenennung</h2>
          <button className="neon-card" type="button" onClick={() => setRenameReport(null)}>Schließen</button>
        </div>
        <ul className="rename-outcome__counts" role="status">
          <li><strong>{result.renamed_count}</strong> umbenannt</li>
          {Array.from(skippedByReason, ([label, count]) => (
            <li key={label}><strong>{count}</strong> {label}</li>
          ))}
          {result.error_count > 0 && <li><strong>{result.error_count}</strong> mit Fehlern</li>}
          {unreported > 0 && <li><strong>{unreported}</strong> ohne Rückmeldung</li>}
        </ul>
        {details.length > 0 && (
          <details className="rename-outcome__details">
            <summary>Meldungen zu den Dateien</summary>
            {details.length < entryCount && <p>Die Liste zeigt {details.length} von {entryCount} Meldungen.</p>}
            <ul>
              {details.map((item, index) => (
                <li key={`${item.path}-${index}`}>
                  <strong>{item.label}</strong>
                  <code>{item.path}</code>
                  {item.destination && <code aria-label={`Neuer Pfad ${item.destination}`}>→ {item.destination}</code>}
                  {item.description && <span>{item.description}</span>}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
    );
  };

  return (
    <div className="settings-page">
      <div className="settings-page__header rename-page-header">
        <div>
          <div className="settings-page__eyebrow">Bibliothek</div>
          <h1 className="settings-page__title">Dateinamen</h1>
          <p className="settings-page__copy">
            Ergänze auf Wunsch die Namen zugeordneter Personen. Prüfe die Vorschläge
            und wähle die Fotos, deren Dateien du umbenennen möchtest.
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

      {loadError && (
        <div className="rename-empty-state" role="alert">
          {loadError}{" "}
          <button type="button" onClick={() => void loadData(pendingOffsetRef.current)}>
            Erneut versuchen
          </button>
        </div>
      )}

      <section className="settings-card rename-toolbar">
        <div className="rename-toolbar__heading">
          <strong>Dateien zur Prüfung auswählen</strong>
          <span>Markiere einzelne Dateien, diese Seite oder alle Treffer des Filters.</span>
        </div>
        <div className="rename-toolbar__controls">
          <div className="settings-actions">
            <button className="neon-card" onClick={handleSelectPage} disabled={selectableItems.length === 0 || isLoading || isSubmitting}>
              Seite wählen ({selectableItems.length})
            </button>
            <button
              className="neon-card"
              onClick={handleSelectAll}
              disabled={total === null || total === 0 || isTotalLoading || isLoading || isSubmitting}
            >
              Alle wählen ({total ?? "…"})
            </button>
            <button className="neon-card" onClick={handleClearSelection} disabled={selectedCount === 0 || isSubmitting}>
              Auswahl aufheben
            </button>
          </div>
          <button
            className="neon-button rename-toolbar__apply"
            onClick={openRenameReview}
            disabled={!canReview}
          >
            {isSubmitting ? "Dateinamen werden aktualisiert…" : `${selectedCount} Dateinamen aktualisieren`}
          </button>
        </div>
      </section>

      {renderRenameReport()}
      {(message || actionError) && (
        <div role={actionError ? "alert" : "status"} className={actionError ? "settings-feedback settings-feedback--error" : "settings-feedback"}>
          {actionError || message}
        </div>
      )}

      <section className={`settings-card rename-list-card${!isLoading && items.length === 0 ? " rename-list-card--empty" : ""}`}>
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
          !loadError && !pageOutOfRange && (
            <div className="rename-empty-state">
              {selectedFolders.length > 0 || selectedPersons.length > 0
                ? "Für diese Auswahl gibt es keine Vorschläge. Passe den Filter an, um weitere Fotos zu sehen."
                : "Es gibt gerade keine Vorschläge. Face Manager schlägt Änderungen für Fotos mit zugeordneten Personen vor."}
            </div>
          )
        ) : (
          <div className="rename-list-shell">
            {showLoadingOverlay && <div className="rename-list-overlay" aria-hidden="true" />}
            <div className="rename-list">
              {items.map((item) => (
                <div className="rename-row" key={item.path}>
                  <input
                    type="checkbox"
                    checked={isSelectable(item) && isSelected(item.path)}
                    onChange={() => toggleItem(item.path)}
                    disabled={!isSelectable(item) || isLoading || isSubmitting}
                    aria-label={`${item.current_filename} auswählen`}
                  />
                  <div className="rename-row__content">
                    <div className="rename-row__meta rename-file-names">
                      <div><small>Aktueller Name</small><code>{item.current_filename}</code></div>
                      <span className="rename-file-names__arrow" aria-hidden="true">→</span>
                      <div><small>Vorgeschlagener Name</small><strong>{item.proposed_filename}</strong></div>
                    </div>
                    <div className="rename-row__path">{item.directory ?? item.path}</div>
                    {filenameWarning(item) && <span className="rename-row__unavailable">
                      {filenameWarning(item)}
                    </span>}
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

      {renameReview && active && (
        <div className="modal-backdrop">
          <section
            ref={reviewDialogRef}
            className="cluster-action-modal cluster-action-modal--narrow"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-review-title"
            aria-describedby="rename-review-scope"
            aria-busy={isSubmitting}
          >
            <header className="cluster-action-modal__header">
              <h2 id="rename-review-title">Dateinamen prüfen</h2>
            </header>
            <div className="cluster-action-modal__confirmation" id="rename-review-scope">
              <strong>{renameReview.count} ausgewählte {renameReview.count === 1 ? "Datei" : "Dateien"}</strong>
              <p>Face Manager benennt die Dateien am jeweiligen Speicherort um.</p>
              {renameReview.payload.rename_all && (
                <p>Die Auswahl gilt für alle Treffer des aktuellen Filters, auch auf anderen Seiten. Bei langen Dateinamen können Personennamen entfallen. Face Manager überspringt unzulässige Dateinamen.</p>
              )}
              {renameReview.example && (
                <>
                  <div className="rename-file-names rename-review-example">
                    <div><small>{renameReview.count > 1 ? "Beispiel" : "Aktueller Name"}</small><code>{renameReview.example.current_filename}</code></div>
                    <span className="rename-file-names__arrow" aria-hidden="true">→</span>
                    <div><small>Vorgeschlagener Name</small><strong>{renameReview.example.proposed_filename}</strong></div>
                  </div>
                  {exampleWarning && <p>{exampleWarning}</p>}
                </>
              )}
            </div>
            <footer className="cluster-action-modal__footer">
              <button className="neon-card" type="button" data-initial-focus onClick={() => setRenameReview(null)} disabled={isSubmitting}>
                Abbrechen
              </button>
              <button className="neon-button" type="button" onClick={() => void handleSubmit()} disabled={isSubmitting}>
                {isSubmitting ? "Dateien werden umbenannt…" : "Dateien jetzt umbenennen"}
              </button>
            </footer>
          </section>
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
