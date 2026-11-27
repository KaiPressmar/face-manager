import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  API_BASE,
  apiFetch,
  type ApplyImageRenamePayload,
  type ApplyImageRenameResult,
  ImageRenameCandidate,
  type ImageRenamePreview,
  applyImageRenames,
  fetchImageRenameCandidateCount,
  fetchImageRenameCandidates,
  openImageLocation,
  previewImageRenames,
  readApiError,
} from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import { copyTextToClipboard } from "../../utils/clipboard";
import LibraryFilterBar from "../shared/LibraryFilterBar";
import FolderFilterModal from "../shared/FolderFilterModal";
import { subscribeToTopic } from "../../utils/events";
import { useModalFocus } from "../../hooks/useModalFocus";
import { useWorkspaceNavigation } from "../../hooks/useWorkspaceNavigation";
import { countWithLabel } from "../../utils/germanFormatting";
import "./ImageRenamePage.css";

const DEFAULT_PAGE_SIZE = 25;
const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];
const SKELETON_ROW_COUNT = 6;
const LIVE_REFRESH_BATCH_MS = 2500;
const FINAL_REFRESH_DELAY_MS = 120;
const USER_IDLE_DELAY_MS = 900;
const REVIEW_PAGE_SIZE = 20;
const number = new Intl.NumberFormat("de-DE");
type ImageGroupingMode = "date" | "folder";
type SortDirection = "desc" | "asc";

interface RenameReview {
  payload: ApplyImageRenamePayload;
  count: number;
}

interface RenameReport {
  result: ApplyImageRenameResult;
  unreported: number;
}

interface RenameRecovery {
  pending_count: number;
  pending: { source_path: string; target_path: string; staging_path: string }[];
  resolved_count?: number;
}

async function requestRenameRecovery(method = "GET", signal?: AbortSignal): Promise<RenameRecovery> {
  const response = await apiFetch(`${API_BASE}/image-renames/recovery`, { method, signal });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Unterbrochene Umbenennungen konnten nicht geprüft werden."));
  }
  return response.json();
}

interface RenameWorkspace {
  folders: string[];
  persons: string[];
  sortBy: ImageGroupingMode;
  sortDirection: SortDirection;
  pageSize: number;
  offset: number;
  scrollTop: number;
}

function skipLabel(reason: string) {
  switch (reason) {
    case "already_matches": return "Bereits passend benannt";
    case "target_exists": return "Zielname bereits belegt";
    case "batch_target_conflict": return "Mehrere ausgewählte Dateien haben denselben Zielnamen";
    case "source_alias": return "Datei ist über eine Verknüpfung oder mehrere Pfade ausgewählt";
    case "source_changed": return "Datei wurde geändert oder ersetzt";
    case "exclusive_rename_unavailable": return "Sicheres Umbenennen ist an diesem Speicherort nicht verfügbar";
    case "preview_changed": return "Vorschlag hat sich seit der Auswahl geändert";
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
  const [reviewPreview, setReviewPreview] = useState<ImageRenamePreview | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewAttempt, setReviewAttempt] = useState(0);
  const [reviewPage, setReviewPage] = useState(0);
  const [renameReport, setRenameReport] = useState<RenameReport | null>(null);
  const [renameRecovery, setRenameRecovery] = useState<RenameRecovery | null>(null);
  const [isCheckingRecovery, setIsCheckingRecovery] = useState(false);
  const [recoveryRevision, setRecoveryRevision] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mutationRefresh, setMutationRefresh] = useState(0);
  const [pageJumpInput, setPageJumpInput] = useState("1");
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [navigationRevision, setNavigationRevision] = useState(0);
  const pageRef = useRef<HTMLDivElement>(null);
  const pendingResume = useRef<{ offset: number; scrollTop: number }>();
  const activeRequestRef = useRef(0);
  const pendingOffsetRef = useRef(0);
  const activeAbortRef = useRef<AbortController | null>(null);
  const totalAbortRef = useRef<AbortController | null>(null);
  const libraryTotalAbortRef = useRef<AbortController | null>(null);
  const hasActivatedLiveRefreshRef = useRef(false);
  const reviewDialogRef = useRef<HTMLElement>(null);
  const reportRef = useRef<HTMLElement>(null);
  const submittingRef = useRef(false);
  const checkingRecoveryRef = useRef(false);
  const reviewOpenRef = useRef(false);
  const hasFilters = selectedFolders.length > 0 || selectedPersons.length > 0;
  const [bulkActionsOpen, setBulkActionsOpen] = useState(false);
  const bulkActionsId = useId();

  useWorkspaceNavigation<RenameWorkspace>("renaming", {
    capture: () => ({
      folders: selectedFolders, persons: selectedPersons, sortBy: groupingMode, sortDirection,
      pageSize, offset, scrollTop: pageRef.current?.closest(".page-content")?.scrollTop ?? 0,
    }),
    restore: (snapshot) => {
      pendingResume.current = { offset: snapshot?.offset ?? 0, scrollTop: snapshot?.scrollTop ?? 0 };
      setSelectedFolders(snapshot?.folders ?? []);
      setSelectedPersons(snapshot?.persons ?? []);
      setGroupingMode(snapshot?.sortBy ?? "date");
      setSortDirection(snapshot?.sortDirection ?? "desc");
      setPageSize(snapshot?.pageSize ?? DEFAULT_PAGE_SIZE);
      setShowFolderFilter(false);
      setRenameReview(null);
      setRenameReport(null);
      setRenameRecovery(null);
      handleClearSelection();
      setNavigationRevision((value) => value + 1);
    },
  });
  reviewOpenRef.current = renameReview !== null;
  useModalFocus(reviewDialogRef, () => setRenameReview(null), !isSubmitting, active && renameReview !== null);

  useEffect(() => {
    if (!active) setRenameReview(null);
  }, [active]);

  const reviewPayload = renameReview?.payload;
  useEffect(() => {
    if (!active || !reviewPayload) return;
    const controller = new AbortController();
    void previewImageRenames(reviewPayload, controller.signal).then(
      (preview) => {
        if (!controller.signal.aborted) {
          setReviewPreview({
            ...preview,
            items: [...preview.items].sort((a, b) => Number(Boolean(b.reason)) - Number(Boolean(a.reason))),
          });
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setReviewError(error instanceof Error ? error.message : "Die Prüfung konnte nicht geladen werden.");
        }
      },
    );
    return () => controller.abort();
  }, [active, reviewPayload, reviewAttempt]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void requestRenameRecovery("GET", controller.signal).then((report) => {
      if (!controller.signal.aborted && !checkingRecoveryRef.current) setRenameRecovery(report);
    }, () => {});
    return () => controller.abort();
  }, [active, mutationRefresh, navigationRevision, recoveryRevision]);

  useEffect(() => {
    if (active && renameReport) reportRef.current?.focus();
  }, [renameReport]);

  const loadTotalCount = async (background = false) => {
    const controller = new AbortController();
    totalAbortRef.current?.abort();
    totalAbortRef.current = controller;
    if (!background) {
      setIsTotalLoading(true);
      setTotal(null);
    }
    try {
      const nextTotal = await fetchImageRenameCandidateCount({
        folders: selectedFolders,
        persons: selectedPersons,
        signal: controller.signal,
      });
      if (!controller.signal.aborted) {
        setTotal(nextTotal);
        if (!hasFilters) {
          libraryTotalAbortRef.current?.abort();
          setLibraryTotal(nextTotal);
        }
      }
    } catch {
      if (!controller.signal.aborted && !background) {
        setTotal(null);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsTotalLoading(false);
      }
    }
  };

  const loadData = async (nextOffset = 0, background = false) => {
    pendingOffsetRef.current = nextOffset;
    const requestId = activeRequestRef.current + 1;
    activeRequestRef.current = requestId;
    activeAbortRef.current?.abort();
    const controller = new AbortController();
    activeAbortRef.current = controller;

    if (!background) setIsLoading(true);
    if (!background) setLoadError(null);
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
      if (controller.signal.aborted || activeRequestRef.current !== requestId) {
        return;
      }
      setLoadError(null);
      setItems(renameData.items);
      setAvailablePersons(renameData.available_persons);
      if (renameData.total !== null) {
        setTotal(renameData.total);
      }
      setOffset(renameData.offset);
      setHasMore(renameData.has_more);
      setHasLoadedOnce(true);
      const resume = pendingResume.current;
      if (resume && nextOffset === resume.offset) {
        pendingResume.current = undefined;
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
          if (activeRequestRef.current === requestId) pageRef.current?.closest(".page-content")?.scrollTo({ top: resume.scrollTop });
        }));
      }
    } catch (error) {
      if (controller.signal.aborted || background) {
        return;
      }
      setLoadError(
        error instanceof Error
          ? error.message
          : "Die Umbenennungsvorschläge konnten nicht geladen werden.",
      );
    } finally {
      if (activeRequestRef.current === requestId) {
        activeAbortRef.current = null;
        setIsLoading(false);
      }
    }
  };

  const refreshLibraryTotal = useCallback(async () => {
    const controller = new AbortController();
    libraryTotalAbortRef.current?.abort();
    libraryTotalAbortRef.current = controller;
    try {
      const count = await fetchImageRenameCandidateCount({ signal: controller.signal });
      if (!controller.signal.aborted) setLibraryTotal(count);
    } catch {
      // Keep the previous library count until another update is available.
    }
  }, []);

  // Keep queued automatic updates attached to the workspace through filter changes.
  const liveQuery = useRef({ loadData, loadTotalCount, hasFilters });
  useLayoutEffect(() => {
    liveQuery.current = { loadData, loadTotalCount, hasFilters };
  });

  useEffect(() => {
    void loadData(pendingResume.current?.offset ?? 0);
  }, [groupingMode, pageSize, sortDirection, selectedFolders, selectedPersons, navigationRevision]);

  useEffect(() => {
    return () => {
      activeAbortRef.current?.abort();
      totalAbortRef.current?.abort();
    };
  }, []);

  // Sorting and pagination only change which rows are displayed.
  useEffect(() => {
    void loadTotalCount();
  }, [selectedFolders, selectedPersons]);

  useEffect(() => {
    if (active && hasFilters && libraryTotal === null) void refreshLibraryTotal();
  }, [active, hasFilters, libraryTotal, refreshLibraryTotal]);

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

    const refreshVisibleData = async () => {
      if (document.visibilityState !== "visible") return;
      if (reviewOpenRef.current || submittingRef.current || activeAbortRef.current) {
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
        const query = liveQuery.current;
        await Promise.all([
          query.loadData(pendingOffsetRef.current, true),
          query.loadTotalCount(true),
          query.hasFilters ? refreshLibraryTotal() : undefined,
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
        if (update?.reason === "image_rename_recovery") setRecoveryRevision((value) => value + 1);
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
    }

    return () => {
      isMounted = false;
      libraryTotalAbortRef.current?.abort();
      if (refreshTimer !== null) window.clearTimeout(refreshTimer);
      unsubscribeClusters();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      document.removeEventListener("scroll", noteUserActivity, true);
      document.removeEventListener("pointerdown", noteUserActivity, true);
      window.removeEventListener("focus", handleWindowFocus);
    };
  }, [active, refreshLibraryTotal]);

  const openRenameReview = () => {
    if (!canReview) return;
    setActionError(null);
    setReviewPreview(null);
    setReviewError(null);
    setReviewPage(0);
    setRenameReview({
      count: selectedCount,
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
    if (!renameReview || !reviewPreview?.ready_count || submittingRef.current || !active) return;
    submittingRef.current = true;
    setIsSubmitting(true);
    setActionError(null);
    setMessage(null);
    setRenameReport(null);
    try {
      const confirmedTargets = Object.fromEntries(reviewPreview.items.flatMap((item) => (
        item.target_path && item.source_token && !item.reason
          ? [[item.path, { target_path: item.target_path, source_token: item.source_token }]]
          : []
      )));
      const alreadySkipped = reviewPreview.items.flatMap((item) => (
        item.reason ? [{ path: item.path, reason: item.reason }] : []
      ));
      const result = await applyImageRenames({
        ...renameReview.payload, confirmed_targets: confirmedTargets, expected_library_id: reviewPreview.library_id,
      });
      const reported = result.renamed_count + result.skipped_count + result.error_count;
      setRenameReport({
        result: {
          ...result,
          skipped: [...alreadySkipped, ...result.skipped],
          skipped_count: alreadySkipped.length + result.skipped_count,
        },
        unreported: Math.max(0, reviewPreview.ready_count - reported),
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

  const checkRenameRecovery = async () => {
    if (checkingRecoveryRef.current) return;
    checkingRecoveryRef.current = true;
    setIsCheckingRecovery(true);
    setActionError(null);
    setMessage(null);
    try {
      const report = await requestRenameRecovery("POST");
      setRenameRecovery(report);
      if (report.resolved_count) {
        setMessage(`${countWithLabel(report.resolved_count, "Umbenennung", "Umbenennungen")} wiederhergestellt.`);
        setMutationRefresh((value) => value + 1);
      } else {
        setMessage("Die gezeigten Dateien benötigen weiterhin eine manuelle Prüfung.");
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Prüfung konnte nicht abgeschlossen werden.");
    } finally {
      checkingRecoveryRef.current = false;
      setIsCheckingRecovery(false);
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
    <nav
      className={`rename-pagination rename-pagination--${position}${pageCount === 1 ? " rename-pagination--single" : ""}`}
      aria-label={position === "top" ? "Seitennavigation oben" : "Seitennavigation unten"}
    >
      <div className="rename-pagination__summary">
        <span>
          {number.format(pageStart)}–{number.format(pageEnd)}
          {total === null ? " von …" : ` von ${number.format(total)}`}
        </span>
        <span>
          Seite {number.format(currentPage)}
          {pageCount === null ? "" : ` von ${number.format(pageCount)}`}
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

        <div className="rename-pagination__field rename-pagination__field--jump">
          <label htmlFor={`rename-page-${position}`}>Gehe zu Seite</label>
          <div className="rename-pagination__jump">
            <input
              type="number"
              id={`rename-page-${position}`}
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
        </div>
      </div>
    </nav>
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
    const recoveryErrors = errors.filter((item) => item.recovery_path);
    const ordinaryErrors = errors.filter((item) => !item.recovery_path).slice(0, Math.max(0, 12 - recoveryErrors.length));
    const details: { path: string; label: string; description?: string; destination?: string }[] = [
      ...[...recoveryErrors, ...ordinaryErrors].map((item) => ({ path: item.path, label: "Fehler", description: item.reason })),
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
        {recoveryErrors.map((item) => <div className="rename-outcome__recovery" key={item.path}>
          <strong>Pfad zur Wiederherstellung</strong>
          <code>{item.recovery_path}</code>
          <button type="button" className="secondary-button" onClick={(event) => void copyCandidatePath(event, item.recovery_path!)}>Pfad kopieren</button>
        </div>)}
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
    <div className="settings-page rename-page" ref={pageRef}>
      <div className="settings-page__header rename-page-header">
        <div>
          <h1 className="settings-page__title">Dateinamen</h1>
          <p className="settings-page__copy">
            Ergänze Personennamen in deinen Fotodateien.
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
        resultSingular="Datei"
        resultsUnavailable={Boolean(loadError)}
        onResetFilters={selectedFolders.length || selectedPersons.length ? () => {
          pendingResume.current = undefined;
          setSelectedFolders([]);
          setSelectedPersons([]);
        } : undefined}
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

      {Boolean(renameRecovery?.pending_count) && renameRecovery && (
        <section className="settings-card rename-recovery" aria-labelledby="rename-recovery-title">
          <h2 id="rename-recovery-title">Unterbrochene Umbenennungen</h2>
          <p>{countWithLabel(renameRecovery.pending_count, "Datei", "Dateien")} zur Prüfung. Verbinde bei Bedarf den Speicherort erneut.</p>
          <button type="button" className="secondary-button" disabled={isCheckingRecovery} onClick={() => void checkRenameRecovery()}>
            {isCheckingRecovery ? "Speicherorte werden geprüft …" : "Speicherorte erneut prüfen"}
          </button>
          <details className="rename-outcome__details">
            <summary>Betroffene Dateipfade anzeigen</summary>
            {renameRecovery.pending_count > renameRecovery.pending.length && (
              <p>Die Liste zeigt die ersten {number.format(renameRecovery.pending.length)} betroffenen Dateien.</p>
            )}
            <ul>
              {renameRecovery.pending.map((item) => (
                <li key={item.source_path}>
                  <span>Ursprünglicher Pfad</span><code>{item.source_path}</code>
                  <span>Zielpfad</span><code>{item.target_path}</code>
                  <span>Möglicher Pfad im Zwischenordner</span><code>{item.staging_path}</code>
                  <button type="button" className="secondary-button" onClick={(event) => void copyCandidatePath(event, item.staging_path)}>
                    Pfad im Zwischenordner kopieren
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}

      <section className="settings-card rename-toolbar" aria-label="Dateiauswahl">
        <div className="rename-toolbar__heading">
          <strong>Dateiauswahl</strong>
          <span aria-hidden="true">{countWithLabel(selectedCount, "Datei", "Dateien")} ausgewählt</span>
        </div>
        <span className="rename-sr-status" role="status">{countWithLabel(selectedCount, "Datei", "Dateien")} ausgewählt</span>
        <div className="rename-toolbar__controls">
          <button type="button" className="secondary-button rename-toolbar__toggle" aria-expanded={bulkActionsOpen}
            aria-controls={bulkActionsId} onClick={() => setBulkActionsOpen((open) => !open)}>
            Auswahl{selectedCount > 0 ? ` (${number.format(selectedCount)})` : ""}
          </button>
          <div id={bulkActionsId} className="settings-actions rename-toolbar__bulk" data-open={bulkActionsOpen}>
            <button className="neon-card" onClick={handleSelectPage} disabled={selectableItems.length === 0 || isLoading || isSubmitting}>
              Diese Seite auswählen ({number.format(selectableItems.length)})
            </button>
            <button
              className="neon-card"
              onClick={handleSelectAll}
              disabled={total === null || total === 0 || isTotalLoading || isLoading || isSubmitting}
            >
              Alle Filtertreffer auswählen ({total === null ? "…" : number.format(total)})
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
            {isSubmitting ? "Dateien werden umbenannt …" : "Umbenennung prüfen"}
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
        {showLoadingOverlay && (
            <div className="rename-list-status__loading" role="status">
              <span className="rename-spinner" aria-hidden="true" />
              Liste wird aktualisiert…
            </div>
        )}

        {total !== 0 && renderPaginationControls("top")}

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
                <div className="rename-row" key={item.path} data-selected={isSelectable(item) && isSelected(item.path)}>
                  <label className="rename-row__select" title={`${item.current_filename} auswählen`}>
                    <input
                      type="checkbox"
                      checked={isSelectable(item) && isSelected(item.path)}
                      onChange={() => toggleItem(item.path)}
                      disabled={!isSelectable(item) || isLoading || isSubmitting}
                      aria-label={`${item.current_filename} auswählen`}
                    />
                  </label>
                  <div className="rename-row__content">
                    <div className="rename-row__meta rename-file-names">
                      <div><small>Aktueller Name</small><code>{item.current_filename}</code></div>
                      <span className="rename-file-names__arrow" aria-hidden="true">→</span>
                      <div><small>Vorgeschlagener Name</small><strong>{item.proposed_filename}</strong></div>
                    </div>
                    <div className="rename-row__path" title={item.directory ?? item.path}>{item.directory ?? item.path}</div>
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

        {pageCount !== 1 && total !== 0 && renderPaginationControls("bottom")}
      </section>

      {renameReview && active && (
        <div className="modal-backdrop">
          <section
            ref={reviewDialogRef}
            className="cluster-action-modal rename-review"
            role="dialog"
            aria-modal="true"
            aria-labelledby="rename-review-title"
            aria-describedby="rename-review-scope"
            aria-busy={isSubmitting || (!reviewPreview && !reviewError)}
          >
            <header className="cluster-action-modal__header">
              <h2 id="rename-review-title">Dateinamen prüfen</h2>
            </header>
            <div className="cluster-action-modal__confirmation">
              <strong>{countWithLabel(renameReview.count, "Datei", "Dateien")} ausgewählt</strong>
              <p id="rename-review-scope">Face Manager benennt die Dateien am jeweiligen Speicherort um.</p>
              {renameReview.payload.rename_all && (
                <p>Die Auswahl umfasst alle Treffer des aktuellen Filters, auch auf anderen Seiten.</p>
              )}
              {!reviewPreview && !reviewError && <p role="status">Dateinamen werden geprüft…</p>}
              {reviewError && (
                <div className="rename-review__error">
                  <p role="alert">{reviewError}</p>
                  <button className="secondary-button" type="button" onClick={() => {
                    reviewDialogRef.current?.querySelector<HTMLElement>("[data-initial-focus]")?.focus();
                    setReviewError(null);
                    setReviewAttempt((attempt) => attempt + 1);
                  }}>Prüfung wiederholen</button>
                </div>
              )}
              {reviewPreview && (
                <>
                  <p role="status">
                    {reviewPreview.ready_count} bereit zum Umbenennen · {reviewPreview.skipped_count} werden übersprungen
                  </p>
                  <ul key={reviewPage} className="rename-review__targets" aria-label="Geprüfte Dateinamen" tabIndex={0}>
                    {reviewPreview.items.slice(reviewPage * REVIEW_PAGE_SIZE, (reviewPage + 1) * REVIEW_PAGE_SIZE).map((item) => (
                      <li key={item.path}>
                        <div className="rename-file-names">
                          <div><small>Aktueller Pfad</small><code>{item.path}</code></div>
                          <span className="rename-file-names__arrow" aria-hidden="true">→</span>
                          <div><small>Zielname</small><strong>{item.target_path ? pathBasename(item.target_path) : "Kein Vorschlag"}</strong></div>
                        </div>
                        {item.reason && <p className="rename-review__reason">{skipLabel(item.reason)}</p>}
                      </li>
                    ))}
                  </ul>
                  {reviewPreview.items.length > REVIEW_PAGE_SIZE && (
                    <nav className="rename-review__pages" aria-label="Vorschauseiten">
                      <button className="neon-card" type="button" onClick={() => setReviewPage(reviewPage - 1)} disabled={isSubmitting || reviewPage === 0}>Zurück</button>
                      <span aria-live="polite">Seite {reviewPage + 1} von {Math.ceil(reviewPreview.items.length / REVIEW_PAGE_SIZE)}</span>
                      <button className="neon-card" type="button" onClick={() => setReviewPage(reviewPage + 1)} disabled={isSubmitting || (reviewPage + 1) * REVIEW_PAGE_SIZE >= reviewPreview.items.length}>Weiter</button>
                    </nav>
                  )}
                </>
              )}
            </div>
            <footer className="cluster-action-modal__footer">
              <button className="neon-card" type="button" data-initial-focus onClick={() => setRenameReview(null)} disabled={isSubmitting}>
                Abbrechen
              </button>
              <button className="neon-button" type="button" onClick={() => void handleSubmit()} disabled={isSubmitting || !reviewPreview?.ready_count}>
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
