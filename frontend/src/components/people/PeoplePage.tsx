import React, { useEffect, useRef, useState } from "react";
import { FaceImage, fetchImages, fetchImportQueue, ImagePage, type ImportQueueState } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import LibraryFilterBar from "../shared/LibraryFilterBar";
import ImageGrid, {
  type FaceOverlayMode,
  type ImageGridSize,
} from "./ImageGrid";
import FolderFilterModal from "../shared/FolderFilterModal";
import FolderPickerModal from "../shared/FolderPickerModal";
import { subscribeToTopic } from "../../utils/events";

export type ImageGroupingMode = "date" | "folder";
export type SortDirection = "desc" | "asc";

const PAGE_SIZE = 40;
const LIVE_REFRESH_BATCH_MS = 2500;
const FINAL_REFRESH_DELAY_MS = 120;
const USER_IDLE_DELAY_MS = 900;
const IMAGE_GRID_SIZE_STORAGE_KEY = "face-manager:image-grid-size";
const IMAGE_GRID_SIZE_OPTIONS: Array<{
  value: ImageGridSize;
  label: string;
}> = [
  { value: "xsmall", label: "Sehr klein" },
  { value: "small", label: "Klein" },
  { value: "medium", label: "Mittel" },
  { value: "large", label: "Groß" },
];

function readImageGridSize(): ImageGridSize {
  try {
    const stored = window.localStorage.getItem(IMAGE_GRID_SIZE_STORAGE_KEY);
    if (
      stored === "xsmall" ||
      stored === "small" ||
      stored === "medium" ||
      stored === "large"
    ) {
      return stored;
    }
  } catch {
    // Storage can be unavailable in restricted browser contexts.
  }
  return "medium";
}

function persistImageGridSize(size: ImageGridSize) {
  try {
    window.localStorage.setItem(IMAGE_GRID_SIZE_STORAGE_KEY, size);
  } catch {
    // The in-memory choice still works for the current session.
  }
}

interface PeoplePageProps {
  active: boolean;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
  onOpenPeople: () => void;
}

const PeoplePage: React.FC<PeoplePageProps> = ({ active, onNavigateToCluster, onOpenPeople }) => {
  const [images, setImages] = useState<FaceImage[]>([]);
  const [availablePersons, setAvailablePersons] = useState<string[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importPending, setImportPending] = useState(false);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  // Archived faces are hidden unless explicitly filtered for.
  const [faceStatuses, setFaceStatuses] = useState<string[]>([]);
  const [faceOverlayMode, setFaceOverlayMode] = useState<FaceOverlayMode>("all");
  const [imageGridSize, setImageGridSize] = useState<ImageGridSize>(readImageGridSize);
  const [galleryRevision, setGalleryRevision] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState(false);
  const [refreshError, setRefreshError] = useState(false);
  const [retryLoad, setRetryLoad] = useState(0);
  const [retryRefresh, setRetryRefresh] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [totalImages, setTotalImages] = useState(0);
  // Unfiltered library size, so the bar can say "162 von 3.000".
  const [libraryTotal, setLibraryTotal] = useState<number | null>(null);
  const [groupingMode, setGroupingMode] = useState<ImageGroupingMode>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const latestQueryRef = useRef(0);
  const loadMoreRequestRef = useRef(0);
  const isFetchingFirstPageRef = useRef(true);
  const isMountedRef = useRef(true);
  const loadedCountRef = useRef(PAGE_SIZE);
  const prefetchedPageRef = useRef<ImagePage | null>(null);
  const prefetchedOffsetRef = useRef<number | null>(null);
  const prefetchPromiseRef = useRef<Promise<void> | null>(null);
  const queryKeyRef = useRef("");
  const pageHeaderRef = useRef<HTMLElement | null>(null);
  const liveRefreshTimerRef = useRef<number | null>(null);
  const hasActivatedLiveRefreshRef = useRef(false);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      latestQueryRef.current += 1;
      loadMoreRequestRef.current += 1;
    };
  }, []);

  useEffect(() => {
    loadedCountRef.current = Math.max(PAGE_SIZE, images.length || PAGE_SIZE);
  }, [images.length]);

  useEffect(() => {
    queryKeyRef.current = JSON.stringify({
      folders: selectedFolders,
      persons: selectedPersons,
      faceStatuses,
      sortBy: groupingMode,
      sortDirection,
    });
  }, [faceStatuses, groupingMode, selectedFolders, selectedPersons, sortDirection]);

  const scheduleNextPagePrefetch = async (
    requestId: number,
    offset: number,
    expectedQueryKey: string,
  ) => {
    if (prefetchPromiseRef.current || prefetchedOffsetRef.current === offset) return;

    const prefetchPromise = (async () => {
      const page = await fetchImages({
        folders: selectedFolders,
        persons: selectedPersons,
        faceStatuses,
        sortBy: groupingMode,
        sortDirection,
        limit: PAGE_SIZE,
        offset,
      });
      if (
        !isMountedRef.current ||
        latestQueryRef.current !== requestId ||
        queryKeyRef.current !== expectedQueryKey ||
        page.offset !== offset
      ) {
        return;
      }

      prefetchedPageRef.current = page;
      prefetchedOffsetRef.current = offset;
    })();

    prefetchPromiseRef.current = prefetchPromise;
    try {
      await prefetchPromise;
    } catch {
      // Load more can make a normal request when the speculative fetch fails.
    } finally {
      if (prefetchPromiseRef.current === prefetchPromise) {
        prefetchPromiseRef.current = null;
      }
    }
  };

  useEffect(() => {
    let isMounted = true;
    const requestId = latestQueryRef.current + 1;
    const queryKey = JSON.stringify({
      folders: selectedFolders,
      persons: selectedPersons,
      faceStatuses,
      sortBy: groupingMode,
      sortDirection,
    });
    latestQueryRef.current = requestId;
    queryKeyRef.current = queryKey;
    prefetchedPageRef.current = null;
    prefetchedOffsetRef.current = null;
    prefetchPromiseRef.current = null;
    loadMoreRequestRef.current += 1;
    isFetchingFirstPageRef.current = true;
    setIsLoading(images.length === 0);
    setIsLoadingMore(false);
    setLoadError(false);
    setLoadMoreError(false);
    setRefreshError(false);

    const loadImages = async (limit: number) => {
      try {
        const page = await fetchImages({
          folders: selectedFolders,
          persons: selectedPersons,
          faceStatuses,
          sortBy: groupingMode,
          sortDirection,
          limit,
          offset: 0,
        });
        if (!isMounted || latestQueryRef.current !== requestId) return;

        setImages(page.items);
        setAvailablePersons(page.available_persons);
        setTotalImages(page.total);
        setHasMore(page.has_more);
        setLoadError(false);
        setSelectedPersons((current) => {
          const next = current.filter((person) =>
            page.available_persons.includes(person),
          );
          return next.length === current.length ? current : next;
        });
        loadedCountRef.current = Math.max(PAGE_SIZE, page.items.length || PAGE_SIZE);
        if (page.has_more) {
          void scheduleNextPagePrefetch(requestId, loadedCountRef.current, queryKey);
        }
      } catch (error) {
        if (!isMounted || latestQueryRef.current !== requestId) return;
        console.error("Bilder konnten nicht geladen werden:", error);
        setLoadError(true);
      } finally {
        if (isMounted && latestQueryRef.current === requestId) {
          isFetchingFirstPageRef.current = false;
          setIsLoading(false);
        }
      }
    };

    void loadImages(PAGE_SIZE);

    return () => {
      isMounted = false;
    };
  }, [faceStatuses, groupingMode, retryLoad, selectedFolders, selectedPersons, sortDirection]);

  useEffect(() => {
    const closeGalleryAfterRestore = (update: { reason?: string }) => {
      if (update?.reason === "database_import") setGalleryRevision((value) => value + 1);
    };
    if (!active) {
      // Keep the gallery's image IDs scoped to the current library even while
      // its page is hidden. Other background events still pause with the page.
      return subscribeToTopic<{ reason?: string }>("clusters", closeGalleryAfterRestore);
    }
    let isMounted = true;
    let refreshInFlight = false;
    let refreshPending = false;
    let userBusyUntil = 0;
    const captureViewportAnchor = () => {
      const scroller = pageHeaderRef.current?.closest(".page-content");
      if (!(scroller instanceof HTMLElement) || scroller.scrollTop < 80) {
        return null;
      }
      const scrollerTop = scroller.getBoundingClientRect().top;
      const cards = Array.from(
        scroller.querySelectorAll<HTMLElement>("[data-image-id]"),
      );
      const anchor = cards.find((card) => card.getBoundingClientRect().bottom > scrollerTop);
      if (!anchor) return null;
      return {
        scroller,
        imageId: anchor.dataset.imageId ?? "",
        offset: anchor.getBoundingClientRect().top - scrollerTop,
      };
    };

    const restoreViewportAnchor = (
      anchor: ReturnType<typeof captureViewportAnchor>,
    ) => {
      if (!anchor) return;
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          const node = anchor.scroller.querySelector<HTMLElement>(
            `[data-image-id="${anchor.imageId}"]`,
          );
          if (!node) return;
          const nextOffset =
            node.getBoundingClientRect().top -
            anchor.scroller.getBoundingClientRect().top;
          anchor.scroller.scrollTop += nextOffset - anchor.offset;
        });
      });
    };

    const refreshVisibleImages = async (preserveViewport = true) => {
      const requestId = latestQueryRef.current;
      const anchor = preserveViewport ? captureViewportAnchor() : null;
      try {
        const page = await fetchImages({
          folders: selectedFolders,
          persons: selectedPersons,
          faceStatuses,
          sortBy: groupingMode,
          sortDirection,
          limit: loadedCountRef.current,
          offset: 0,
        });
        if (!isMounted || latestQueryRef.current !== requestId) return false;
        // Keep existing cards at their current array index while the user is
        // below the top. New imports are appended to this live snapshot instead
        // of redistributing the masonry columns underneath the user's pointer.
        if (anchor) {
          setImages((current) => {
            const freshById = new Map(page.items.map((image) => [image.id, image]));
            const stable = current
              .map((image) => freshById.get(image.id))
              .filter((image): image is FaceImage => image !== undefined);
            const knownIds = new Set(stable.map((image) => image.id));
            return [
              ...stable,
              ...page.items.filter((image) => !knownIds.has(image.id)),
            ];
          });
        } else {
          setImages(page.items);
        }
        setAvailablePersons(page.available_persons);
        setTotalImages(page.total);
        setHasMore(page.has_more);
        setLoadError(false);
        restoreViewportAnchor(anchor);
        prefetchedPageRef.current = null;
        prefetchedOffsetRef.current = null;
        prefetchPromiseRef.current = null;
        loadedCountRef.current = Math.max(PAGE_SIZE, page.items.length || PAGE_SIZE);
        if (page.has_more) {
          void scheduleNextPagePrefetch(
            requestId,
            loadedCountRef.current,
            queryKeyRef.current,
          );
        }
        return true;
      } catch (error) {
        if (isMounted && latestQueryRef.current === requestId) {
          console.error("Live-Aktualisierung der Bilder fehlgeschlagen:", error);
        }
        return false;
      }
    };

    const refreshLibraryTotal = async () => {
      try {
        const page = await fetchImages({ limit: 1, offset: 0 });
        if (isMounted) setLibraryTotal(page.total);
        return true;
      } catch (error) {
        if (isMounted) console.error("Bibliotheksgröße konnte nicht geladen werden:", error);
        return false;
      }
    };

    const runRefresh = async (preserveViewport = true) => {
      if (document.visibilityState !== "visible") return;
      if (isLoadingMore || isFetchingFirstPageRef.current) {
        scheduleRefresh(preserveViewport, USER_IDLE_DELAY_MS);
        return;
      }
      const idleIn = userBusyUntil - performance.now();
      if (idleIn > 0) {
        scheduleRefresh(preserveViewport, idleIn + 50);
        return;
      }
      if (refreshInFlight) {
        refreshPending = true;
        return;
      }
      refreshInFlight = true;
      try {
        const [imagesUpdated, totalUpdated] = await Promise.all([
          refreshVisibleImages(preserveViewport),
          refreshLibraryTotal(),
        ]);
        if (isMounted) setRefreshError(!imagesUpdated || !totalUpdated);
      } finally {
        refreshInFlight = false;
        if (isMounted && refreshPending) {
          refreshPending = false;
          scheduleRefresh(true, LIVE_REFRESH_BATCH_MS);
        }
      }
    };

    const scheduleRefresh = (
      preserveViewport = true,
      delay = LIVE_REFRESH_BATCH_MS,
      replacePending = false,
    ) => {
      if (liveRefreshTimerRef.current !== null) {
        if (!replacePending) return;
        window.clearTimeout(liveRefreshTimerRef.current);
      }
      // Coalesce rapid import/reclustering checkpoints. Existing cards stay
      // visually anchored while new results are folded into the live list.
      liveRefreshTimerRef.current = window.setTimeout(() => {
        liveRefreshTimerRef.current = null;
        void runRefresh(preserveViewport);
      }, delay);
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        scheduleRefresh(false, FINAL_REFRESH_DELAY_MS, true);
      }
    };
    const handleWindowFocus = () =>
      scheduleRefresh(false, FINAL_REFRESH_DELAY_MS, true);
    const noteUserActivity = () => {
      userBusyUntil = performance.now() + USER_IDLE_DELAY_MS;
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    document.addEventListener("scroll", noteUserActivity, true);
    document.addEventListener("pointerdown", noteUserActivity, true);
    window.addEventListener("focus", handleWindowFocus);
    const unsubscribeClusters = subscribeToTopic<{ reason?: string }>(
      "clusters",
      (update) => {
        closeGalleryAfterRestore(update);
        const isBackgroundCheckpoint = update?.reason === "background_progress";
        scheduleRefresh(
          true,
          isBackgroundCheckpoint ? LIVE_REFRESH_BATCH_MS : FINAL_REFRESH_DELAY_MS,
          !isBackgroundCheckpoint,
        );
      },
    );

    if (hasActivatedLiveRefreshRef.current) {
      scheduleRefresh(false, FINAL_REFRESH_DELAY_MS, true);
    } else {
      hasActivatedLiveRefreshRef.current = true;
      void refreshLibraryTotal().then((updated) => {
        if (isMounted && !updated) setRefreshError(true);
      });
    }

    return () => {
      isMounted = false;
      if (liveRefreshTimerRef.current !== null) {
        window.clearTimeout(liveRefreshTimerRef.current);
        liveRefreshTimerRef.current = null;
      }
      unsubscribeClusters();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      document.removeEventListener("scroll", noteUserActivity, true);
      document.removeEventListener("pointerdown", noteUserActivity, true);
      window.removeEventListener("focus", handleWindowFocus);
    };
  }, [active, faceStatuses, groupingMode, isLoadingMore, retryRefresh, selectedFolders, selectedPersons, sortDirection]);

  const loadMoreImages = async () => {
    if (!isMountedRef.current || isLoading || isLoadingMore || isFetchingFirstPageRef.current || loadError || !hasMore) return;
    const loadMoreRequest = ++loadMoreRequestRef.current;
    const requestId = latestQueryRef.current;
    const offset = loadedCountRef.current;
    const cachedPage =
      prefetchedOffsetRef.current === offset ? prefetchedPageRef.current : null;

    const appendPage = (page: ImagePage) => {
      setImages((current) => {
        const merged = new Map(current.map((image) => [image.id, image]));
        page.items.forEach((image) => merged.set(image.id, image));
        const next = Array.from(merged.values());
        loadedCountRef.current = next.length;
        return next;
      });
      setAvailablePersons(page.available_persons);
      setTotalImages(page.total);
      setHasMore(page.has_more);
      setLoadMoreError(false);
      if (page.has_more) {
        void scheduleNextPagePrefetch(
          requestId,
          offset + page.items.length,
          queryKeyRef.current,
        );
      }
    };

    if (cachedPage) {
      prefetchedPageRef.current = null;
      prefetchedOffsetRef.current = null;
      appendPage(cachedPage);
      return;
    }

    setIsLoadingMore(true);
    setLoadMoreError(false);
    try {
      const page = await fetchImages({
        folders: selectedFolders,
        persons: selectedPersons,
        faceStatuses,
        sortBy: groupingMode,
        sortDirection,
        limit: PAGE_SIZE,
        offset,
      });
      if (!isMountedRef.current || latestQueryRef.current !== requestId || loadMoreRequestRef.current !== loadMoreRequest) return;
      appendPage(page);
    } catch (error) {
      if (!isMountedRef.current || latestQueryRef.current !== requestId || loadMoreRequestRef.current !== loadMoreRequest) return;
      console.error("Weitere Bilder konnten nicht geladen werden:", error);
      setLoadMoreError(true);
    } finally {
      if (isMountedRef.current && loadMoreRequestRef.current === loadMoreRequest) setIsLoadingMore(false);
    }
  };

  const hasActiveFilters =
    selectedPersons.length > 0 ||
    selectedFolders.length > 0 ||
    faceStatuses.length > 0;
  // Only the unfiltered total can decide whether setup is still required.
  // The current result may legitimately be empty for a status/person/folder.
  const knownLibraryTotal =
    libraryTotal ?? (!hasActiveFilters ? totalImages : null);
  const libraryIsEmpty =
    !isLoading &&
    !loadError &&
    knownLibraryTotal === 0;

  useEffect(() => {
    if (!active || !libraryIsEmpty) return;
    let live = true;
    let receivedEvent = false;
    const applyQueue = (queue: ImportQueueState) => {
      setImportPending(queue.jobs.some((job) =>
        ["queued", "running", "paused", "cancelling"].includes(job.status),
      ));
    };
    const unsubscribe = subscribeToTopic<ImportQueueState>("imports", (queue) => {
      receivedEvent = true;
      applyQueue(queue);
    });
    void fetchImportQueue().then((queue) => {
      if (live && !receivedEvent) applyQueue(queue);
    }).catch(() => {
      // The activity panel exposes connection and import errors.
    });
    return () => {
      live = false;
      unsubscribe();
    };
  }, [active, libraryIsEmpty]);

  const imageGridSizeIndex = Math.max(
    0,
    IMAGE_GRID_SIZE_OPTIONS.findIndex((option) => option.value === imageGridSize),
  );

  const pageHeader = (
    <header ref={pageHeaderRef} className="people-page-heading">
      <h1>Bilder</h1>
    </header>
  );

  const importDialog = showImport && (
    <FolderPickerModal onClose={(started) => {
      if (started) setImportPending(true);
      setShowImport(false);
    }} />
  );
  const resetFilters = () => {
    setSelectedPersons([]);
    setSelectedFolders([]);
    setFaceStatuses([]);
  };

  if (libraryIsEmpty) {
    return (
      <>
        {pageHeader}
        <section className="collection-start" aria-labelledby="collection-start-title">
          <div className="collection-start__intro">
            <h2 id="collection-start-title">Deine Fotos an einem Ort</h2>
            <p>Wähle Bilderordner von deinem Gerät oder einem verbundenen Laufwerk. Face Manager erfasst auch ihre Unterordner.</p>
            {importPending && (
              <p className="collection-start__notice" role="status">
                Deine Quellen stehen unter „Aktivitäten“. Sobald Fotos eingelesen sind, erscheinen sie hier.
              </p>
            )}
            <button className="neon-button" onClick={() => setShowImport(true)} type="button">
              {importPending ? "Weitere Ordner hinzufügen" : "Bilderordner auswählen"}
            </button>
          </div>
          <ul className="collection-start__steps">
            <li><strong>Doppelte Fotos zusammenfassen</strong><span>Identische Dateien erscheinen einmal. Alle Speicherorte bleiben erhalten.</span></li>
            <li><strong>Ähnliche Gesichter gruppieren</strong><span>Face Manager sucht passende Gruppen und schlägt bekannte Personen vor.</span></li>
            <li><strong>Personen Namen geben</strong><span>Ein Name pro Gruppe genügt. Vorschläge prüfst du unter „Personen“.</span></li>
          </ul>
        </section>
        {importDialog}
      </>
    );
  }

  return (
    <>
      {pageHeader}
      {!isLoading && !loadError && !hasActiveFilters &&
        availablePersons.length === 1 && availablePersons[0] === "Unbekannt" && (
          <div className="collection-next">
            <p>Deine ersten Gesichter sind bereit. Gib einer Gruppe einen Namen, um ihre Fotos zusammen zu finden.</p>
            <button className="secondary-button" type="button" onClick={onOpenPeople}>Personen zuordnen</button>
          </div>
        )}
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
        resultCount={isLoading || (loadError && !images.length) ? null : totalImages}
        totalCount={libraryTotal}
        resultNoun="Bilder"
        faceStatuses={faceStatuses}
        onFaceStatusesChange={setFaceStatuses}
        onResetFilters={hasActiveFilters ? resetFilters : undefined}
      >
        <label
          className="filter-bar__grid-size"
          title={`Bildgröße im Raster: ${IMAGE_GRID_SIZE_OPTIONS[imageGridSizeIndex].label}`}
        >
          <span className="filter-bar__grid-size-label">Bildgröße</span>
          <span
            className="filter-bar__grid-size-icon filter-bar__grid-size-icon--small"
            aria-hidden="true"
          >
            ▦
          </span>
          <input
            type="range"
            min="0"
            max={IMAGE_GRID_SIZE_OPTIONS.length - 1}
            step="1"
            value={imageGridSizeIndex}
            aria-label="Bildgröße im Raster"
            aria-valuetext={IMAGE_GRID_SIZE_OPTIONS[imageGridSizeIndex].label}
            style={
              {
                "--grid-size-position": `${
                  (imageGridSizeIndex / (IMAGE_GRID_SIZE_OPTIONS.length - 1)) * 100
                }%`,
              } as React.CSSProperties
            }
            onChange={(event) => {
              const option = IMAGE_GRID_SIZE_OPTIONS[Number(event.target.value)];
              if (!option) return;
              setImageGridSize(option.value);
              persistImageGridSize(option.value);
            }}
          />
          <span
            className="filter-bar__grid-size-icon filter-bar__grid-size-icon--large"
            aria-hidden="true"
          >
            ▦
          </span>
          <output>{IMAGE_GRID_SIZE_OPTIONS[imageGridSizeIndex].label}</output>
        </label>
        <select
          className="filter-bar__control filter-bar__select"
          aria-label="Gesichtsmarkierungen"
          value={faceOverlayMode}
          onChange={(event) =>
            setFaceOverlayMode(event.target.value as FaceOverlayMode)
          }
          title="Welche Gesichter im Bild markiert werden"
        >
          <option value="all">Alle Gesichter markieren</option>
          <option value="assigned">Nur zugewiesene markieren</option>
          <option value="none">Keine Markierungen</option>
        </select>
      </LibraryFilterBar>

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
        <div className="image-grid-status" role="alert">
          Bilder konnten nicht geladen werden. {images.length > 0 && "Bereits angezeigte Bilder bleiben erhalten. "}
          <button type="button" onClick={() => setRetryLoad((value) => value + 1)}>
            Erneut versuchen
          </button>
        </div>
      )}
      {refreshError && !loadError && (
        <div className="image-grid-status" role="alert">
          Die Bibliothek konnte nicht aktualisiert werden.{" "}
          <button type="button" onClick={() => setRetryRefresh((value) => value + 1)}>
            Erneut versuchen
          </button>
        </div>
      )}
      {loadMoreError && !loadError && (
        <div className="image-grid-status" role="alert">
          Weitere Bilder konnten nicht geladen werden.{" "}
          <button type="button" onClick={() => void loadMoreImages()}>
            Erneut versuchen
          </button>
        </div>
      )}

      {(!loadError || images.length > 0) && (
        <ImageGrid
        images={images}
        isLoading={isLoading}
        hasMore={hasMore && !loadMoreError && !loadError}
        isLoadingMore={isLoadingMore}
        faceOverlayMode={faceOverlayMode}
        gridSize={imageGridSize}
        galleryRevision={galleryRevision}
        onNavigateToCluster={onNavigateToCluster}
        onLoadMore={loadMoreImages}
        hasActiveFilters={hasActiveFilters}
        onResetFilters={resetFilters}
        onImageDeleted={(imageId) => {
          setImages((current) => current.filter((image) => image.id !== imageId));
          setTotalImages((current) => Math.max(0, current - 1));
          setLibraryTotal((current) =>
            current === null ? current : Math.max(0, current - 1),
          );
        }}
        />
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
      {importDialog}
    </>
  );
};

export default PeoplePage;
