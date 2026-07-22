import React, { useEffect, useRef, useState } from "react";
import { FaceImage, fetchImages, imageFileUrl, ImagePage } from "../../utils/api";
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
const PREFETCH_IMAGE_COUNT = PAGE_SIZE;
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

function preloadPageImages(page: ImagePage) {
  page.items.slice(0, PREFETCH_IMAGE_COUNT).forEach((image) => {
    const preload = new Image();
    preload.src = imageFileUrl(image.id);
  });
}

interface PeoplePageProps {
  active: boolean;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
}

const PeoplePage: React.FC<PeoplePageProps> = ({ active, onNavigateToCluster }) => {
  const [images, setImages] = useState<FaceImage[]>([]);
  const [availablePersons, setAvailablePersons] = useState<string[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  // Archived faces are hidden unless explicitly filtered for.
  const [faceStatuses, setFaceStatuses] = useState<string[]>([]);
  const [faceOverlayMode, setFaceOverlayMode] = useState<FaceOverlayMode>("all");
  const [imageGridSize, setImageGridSize] = useState<ImageGridSize>(readImageGridSize);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [totalImages, setTotalImages] = useState(0);
  // Unfiltered library size, so the bar can say "162 von 3.000".
  const [libraryTotal, setLibraryTotal] = useState<number | null>(null);
  const [groupingMode, setGroupingMode] = useState<ImageGroupingMode>("date");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const latestQueryRef = useRef(0);
  const loadedCountRef = useRef(PAGE_SIZE);
  const prefetchedPageRef = useRef<ImagePage | null>(null);
  const prefetchedOffsetRef = useRef<number | null>(null);
  const prefetchPromiseRef = useRef<Promise<void> | null>(null);
  const queryKeyRef = useRef("");
  const pageHeaderRef = useRef<HTMLElement | null>(null);
  const liveRefreshTimerRef = useRef<number | null>(null);
  const hasActivatedLiveRefreshRef = useRef(false);

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
        latestQueryRef.current !== requestId ||
        queryKeyRef.current !== expectedQueryKey ||
        page.offset !== offset
      ) {
        return;
      }

      prefetchedPageRef.current = page;
      prefetchedOffsetRef.current = offset;
      if (page.items.length > 0) {
        preloadPageImages(page);
      }
    })();

    prefetchPromiseRef.current = prefetchPromise;
    try {
      await prefetchPromise;
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
    setIsLoading(true);
    setHasMore(false);

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
      } finally {
        if (isMounted && latestQueryRef.current === requestId) {
          setIsLoading(false);
        }
      }
    };

    void loadImages(PAGE_SIZE);

    return () => {
      isMounted = false;
    };
  }, [faceStatuses, groupingMode, selectedFolders, selectedPersons, sortDirection]);

  useEffect(() => {
    if (!active) {
      return;
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
        if (!isMounted || latestQueryRef.current !== requestId) return;
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
      } catch (error) {
        console.error("Live-Aktualisierung der Bilder fehlgeschlagen:", error);
      }
    };

    const refreshLibraryTotal = async () => {
      try {
        const page = await fetchImages({ limit: 1, offset: 0 });
        if (isMounted) setLibraryTotal(page.total);
      } catch {
        // A later checkpoint or focus event retries without disturbing the UI.
      }
    };

    const runRefresh = async (preserveViewport = true) => {
      if (document.visibilityState !== "visible" || isLoadingMore) return;
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
        await Promise.all([
          refreshVisibleImages(preserveViewport),
          refreshLibraryTotal(),
        ]);
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
      void refreshLibraryTotal();
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
  }, [active, faceStatuses, groupingMode, isLoadingMore, selectedFolders, selectedPersons, sortDirection]);

  const loadMoreImages = async () => {
    if (isLoading || isLoadingMore || !hasMore) return;
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
      if (latestQueryRef.current !== requestId) return;
      appendPage(page);
    } finally {
      setIsLoadingMore(false);
    }
  };

  // An entirely empty library is a setup problem, not an empty filter result —
  // so point at the setup area instead of suggesting a different filter.
  const libraryIsEmpty =
    !isLoading &&
    totalImages === 0 &&
    selectedPersons.length === 0 &&
    selectedFolders.length === 0;
  const imageGridSizeIndex = Math.max(
    0,
    IMAGE_GRID_SIZE_OPTIONS.findIndex((option) => option.value === imageGridSize),
  );

  const pageHeader = (
    <header ref={pageHeaderRef} className="people-page-heading">
      <div>
        <span>Bibliothek</span>
        <h1>Bilder</h1>
        <p>Durchsuche deine Fotos und filtere sie nach Personen, Ordnern oder Aufnahmedatum.</p>
      </div>
    </header>
  );

  if (libraryIsEmpty) {
    return (
      <>
        {pageHeader}
        <section className="home-empty-state">
          <div className="home-empty-state__visual" aria-hidden="true">◎</div>
          <h2>Noch keine Bilder vorhanden</h2>
          <p>
            Füge zuerst einen Bilderordner hinzu. Gesichter werden automatisch erkannt,
            danach kannst du deine Bilder hier nach Personen filtern.
          </p>
          <button className="neon-button" onClick={() => setShowImport(true)} type="button">
            Ersten Bilderordner hinzufügen
          </button>
          {showImport && (
            <FolderPickerModal onClose={() => setShowImport(false)} />
          )}
        </section>
      </>
    );
  }

  return (
    <>
      {pageHeader}
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
        resultCount={totalImages}
        totalCount={libraryTotal}
        resultNoun="Bilder"
        faceStatuses={faceStatuses}
        onFaceStatusesChange={setFaceStatuses}
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

      <ImageGrid
        images={images}
        isLoading={isLoading}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        faceOverlayMode={faceOverlayMode}
        gridSize={imageGridSize}
        onNavigateToCluster={onNavigateToCluster}
        onLoadMore={loadMoreImages}
        onImageDeleted={(imageId) => {
          setImages((current) => current.filter((image) => image.id !== imageId));
          setTotalImages((current) => Math.max(0, current - 1));
        }}
      />

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
    </>
  );
};

export default PeoplePage;
