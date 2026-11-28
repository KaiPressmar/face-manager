import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { FaceImage, fetchImageDetail, fetchImages, fetchImportQueue, ImagePage, type ImportQueueState } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import type { GeoPhotoSelection } from "../../utils/geoPhotoSelection";
import LibraryFilterBar from "../shared/LibraryFilterBar";
import ImageGrid, {
  type FaceOverlayMode,
  type ImageGridSize,
} from "./ImageGrid";
import FolderFilterModal from "../shared/FolderFilterModal";
import FolderPickerModal from "../shared/FolderPickerModal";
import AlbumPickerDialog, { AlbumLibraryDialog } from "../shared/AlbumPickerDialog";
import { removePhotoAlbumImages, updatePhotoAlbum, usePhotoAlbums, type PhotoAlbum } from "../../utils/photoAlbums";
import { subscribeToTopic } from "../../utils/events";
import type { NavigateToFaceGroup } from "./FaceOverlay";
import { useWorkspaceNavigation } from "../../hooks/useWorkspaceNavigation";
import { useUiPreferences } from "../../hooks/useUiPreferences";

export type ImageGroupingMode = "date" | "folder";
export type SortDirection = "desc" | "asc";

const PAGE_SIZE = 40;
const REFRESH_PAGE_SIZE = 200;
const MAX_GEOMETRY_REQUESTS = 4;
const LIVE_REFRESH_BATCH_MS = 2500;
const FINAL_REFRESH_DELAY_MS = 120;
const USER_IDLE_DELAY_MS = 900;
const IMAGE_GRID_SIZE_OPTIONS: Array<{
  value: ImageGridSize;
  label: string;
}> = [
  { value: "xsmall", label: "Sehr klein" },
  { value: "small", label: "Klein" },
  { value: "medium", label: "Mittel" },
  { value: "large", label: "Groß" },
];

interface ResolvedImageGeometry {
  source: FaceImage;
  detail: FaceImage;
}

interface PhotoWorkspace {
  folders: string[];
  persons: string[];
  statuses: string[];
  sortBy: ImageGroupingMode;
  sortDirection: SortDirection;
  queryKey: string;
  loadedCount: number;
  scrollTop: number;
  selection?: number[];
}

function applyImageGeometry(image: FaceImage, geometry?: ResolvedImageGeometry): FaceImage {
  if (!geometry || (image.image_width && image.image_height)) return image;
  const { source, detail } = geometry;
  if (source.image_path !== image.image_path || source.content_hash !== image.content_hash ||
      detail.id !== image.id || detail.content_hash !== image.content_hash ||
      !detail.image_width || !detail.image_height) return image;

  const originalFaces = new Map(source.faces.map((face) => [face.id, face]));
  const orientedFaces = new Map(detail.faces.map((face) => [face.id, face]));
  // Preserve the page's face filters and assignments. A cached correction can
  // only be reused while the corresponding original coordinates still match.
  const faces = image.faces.flatMap((face) => {
    const original = originalFaces.get(face.id);
    const oriented = orientedFaces.get(face.id);
    if (!original || !oriented || original.bbox_x !== face.bbox_x || original.bbox_y !== face.bbox_y ||
        original.bbox_w !== face.bbox_w || original.bbox_h !== face.bbox_h) return [];
    return [{ ...face, bbox_x: oriented.bbox_x, bbox_y: oriented.bbox_y, bbox_w: oriented.bbox_w, bbox_h: oriented.bbox_h }];
  });
  return faces.length === image.faces.length ? {
    ...image, image_width: detail.image_width, image_height: detail.image_height, faces,
  } : image;
}

interface PeoplePageProps {
  active: boolean;
  onNavigateToCluster: NavigateToFaceGroup;
  onOpenPeople: () => void;
  geoSelection?: GeoPhotoSelection;
  onChangeGeoSelection: (selection?: GeoPhotoSelection) => void;
  onOpenAlbumMap?: (id: string) => void;
}

const PeoplePage: React.FC<PeoplePageProps> = ({ active, onNavigateToCluster, onOpenPeople, geoSelection, onChangeGeoSelection, onOpenAlbumMap }) => {
  const [images, setImages] = useState<FaceImage[]>([]);
  const [availablePersons, setAvailablePersons] = useState<string[]>([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [showFolderFilter, setShowFolderFilter] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importPending, setImportPending] = useState(false);
  const [selectedFolders, setSelectedFolders] = useState<string[]>([]);
  const [showAlbumLibrary, setShowAlbumLibrary] = useState(false);
  const [albumPickerIds, setAlbumPickerIds] = useState<readonly number[] | null>(null);
  const [selectedAlbumImages, setSelectedAlbumImages] = useState<ReadonlySet<number> | null>(null);
  const [albumStatus, setAlbumStatus] = useState<{ text: string; error?: boolean }>();
  const [albumPending, setAlbumPending] = useState(false);
  const albumMutation = useRef(false);
  const { albums, loading: albumsLoading, error: albumsError, reload: reloadAlbums } = usePhotoAlbums(active);
  const albumId = geoSelection?.album_id;
  const activeAlbum = albums.find((album) => album.id === albumId);
  // Archived faces are hidden unless explicitly filtered for.
  const [faceStatuses, setFaceStatuses] = useState<string[]>([]);
  const [faceOverlayMode, setFaceOverlayMode] = useState<FaceOverlayMode>("all");
  const { values: { image_grid_size: imageGridSize }, setPreference } = useUiPreferences();
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
  const [displayedQueryKey, setDisplayedQueryKey] = useState("");
  const requestedQueryKey = useMemo(() => JSON.stringify({
    folders: selectedFolders, persons: selectedPersons, faceStatuses, geoSelection,
    sortBy: groupingMode, sortDirection,
  }), [faceStatuses, geoSelection, groupingMode, selectedFolders, selectedPersons, sortDirection]);
  const staleResults = displayedQueryKey !== requestedQueryKey;
  const latestQueryRef = useRef(0);
  const queryRequestRef = useRef<AbortController | null>(null);
  const loadMoreRequestRef = useRef(0);
  const loadingPageRef = useRef<number>();
  const isFetchingFirstPageRef = useRef(true);
  const isMountedRef = useRef(true);
  const loadedCountRef = useRef(PAGE_SIZE);
  const prefetchedPageRef = useRef<ImagePage | null>(null);
  const prefetchedOffsetRef = useRef<number | null>(null);
  const prefetchPromiseRef = useRef<Promise<void> | null>(null);
  const prefetchRequestOffset = useRef<number>();
  const refreshImagesRequestRef = useRef<AbortController | null>(null);
  const requestedGeometryRef = useRef(new WeakSet<FaceImage>());
  const geometryQueueRef = useRef(new Map<number, FaceImage>());
  const geometryRequestsRef = useRef(new Map<number, AbortController>());
  const resolvedGeometryRef = useRef(new Map<number, ResolvedImageGeometry>());
  const queryKeyRef = useRef("");
  const pageHeaderRef = useRef<HTMLElement | null>(null);
  const refreshAnchorRef = useRef<{ scroller: HTMLElement; imageId: string; offset: number; requestId: number }>();
  const pendingResume = useRef<Pick<PhotoWorkspace, "queryKey" | "loadedCount" | "scrollTop" | "selection">>();
  const liveRefreshTimerRef = useRef<number | null>(null);
  const hasActivatedLiveRefreshRef = useRef(false);
  const liveRevision = useRef(0);
  const appliedLiveRevision = useRef(0);
  const handledRetry = useRef(0);

  const { checkpoint } = useWorkspaceNavigation<PhotoWorkspace>("people", {
    captureScrollTop: () => pendingResume.current?.queryKey === requestedQueryKey
      ? pendingResume.current.scrollTop : undefined,
    capture: () => {
      const resume = pendingResume.current?.queryKey === requestedQueryKey ? pendingResume.current : undefined;
      return {
        folders: selectedFolders, persons: selectedPersons, statuses: faceStatuses,
        sortBy: groupingMode, sortDirection, queryKey: requestedQueryKey,
        loadedCount: resume?.loadedCount ?? images.length,
        scrollTop: resume?.scrollTop ?? pageHeaderRef.current?.closest(".page-content")?.scrollTop ?? 0,
        selection: resume?.selection ?? (selectedAlbumImages && displayedQueryKey === requestedQueryKey
          ? [...selectedAlbumImages] : undefined),
      };
    },
    restore: (snapshot) => {
      pendingResume.current = snapshot;
      setSelectedFolders(snapshot?.folders ?? []);
      setSelectedPersons(snapshot?.persons ?? []);
      setFaceStatuses(snapshot?.statuses ?? []);
      setGroupingMode(snapshot?.sortBy ?? "date");
      setSortDirection(snapshot?.sortDirection ?? "desc");
      setSelectedAlbumImages(null);
      setAlbumPickerIds(null);
      setShowAlbumLibrary(false);
      setShowFolderFilter(false);
      setShowImport(false);
      setGalleryRevision((value) => value + 1);
    },
  });

  useLayoutEffect(() => {
    const anchor = refreshAnchorRef.current;
    refreshAnchorRef.current = undefined;
    if (!active || !anchor || latestQueryRef.current !== anchor.requestId) return;
    const card = anchor.scroller.querySelector<HTMLElement>(`[data-image-id="${anchor.imageId}"]`);
    if (!card) return;
    const offset = card.getBoundingClientRect().top - anchor.scroller.getBoundingClientRect().top;
    anchor.scroller.scrollTop += offset - anchor.offset;
  }, [active, images]);

  const clearGeometryRequests = useCallback(() => {
    geometryRequestsRef.current.forEach((request) => request.abort());
    geometryRequestsRef.current.clear();
    geometryQueueRef.current.clear();
    resolvedGeometryRef.current.clear();
    requestedGeometryRef.current = new WeakSet();
  }, []);

  const resolveImageGeometry = useCallback(async (image: FaceImage, signal: AbortSignal) => {
    for (let attempt = 0; attempt < 2 && !signal.aborted; attempt += 1) {
      try {
        const detail = await fetchImageDetail(image.id, signal, { resolveGeometry: true });
        const geometry = { source: image, detail };
        if (signal.aborted || applyImageGeometry(image, geometry) === image) return;
        if (image.content_hash) resolvedGeometryRef.current.set(image.id, geometry);
        setImages((current) => signal.aborted ? current : current.map((item) =>
          item === image || (image.content_hash && item.id === image.id) ? applyImageGeometry(item, geometry) : item));
        return;
      } catch {
        // A preview can succeed while the geometry request is temporarily busy.
        if (attempt === 0 && !signal.aborted) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, 400));
        }
      }
    }
  }, []);

  const drainGeometryQueue = useCallback(function drain() {
    for (const image of geometryQueueRef.current.values()) {
      if (geometryRequestsRef.current.size >= MAX_GEOMETRY_REQUESTS) return;
      if (geometryRequestsRef.current.has(image.id)) continue;
      geometryQueueRef.current.delete(image.id);
      if (applyImageGeometry(image, resolvedGeometryRef.current.get(image.id)) !== image) continue;
      const request = new AbortController();
      geometryRequestsRef.current.set(image.id, request);
      void resolveImageGeometry(image, request.signal).finally(() => {
        if (geometryRequestsRef.current.get(image.id) === request) geometryRequestsRef.current.delete(image.id);
        drain();
      });
    }
  }, [resolveImageGeometry]);

  const requestImageGeometry = useCallback((image: FaceImage) => {
    if (isFetchingFirstPageRef.current || requestedGeometryRef.current.has(image)) return;
    requestedGeometryRef.current.add(image);
    geometryQueueRef.current.set(image.id, image);
    drainGeometryQueue();
  }, [drainGeometryQueue]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      latestQueryRef.current += 1;
      loadMoreRequestRef.current += 1;
      clearGeometryRequests();
    };
  }, [clearGeometryRequests]);

  useEffect(() => {
    loadedCountRef.current = Math.max(PAGE_SIZE, images.length || PAGE_SIZE);
  }, [images.length]);

  const scheduleNextPagePrefetch = async (
    requestId: number,
    offset: number,
    expectedQueryKey: string,
  ) => {
    if (prefetchPromiseRef.current || prefetchedOffsetRef.current === offset) return;
    const signal = queryRequestRef.current?.signal;

    const prefetchPromise: Promise<void> = (async () => {
      const page = await fetchImages({
        folders: selectedFolders,
        persons: selectedPersons,
        faceStatuses,
        selection: geoSelection,
        sortBy: groupingMode,
        sortDirection,
        limit: PAGE_SIZE,
        offset,
        signal,
      });
      if (
        signal?.aborted || !isMountedRef.current ||
        prefetchPromiseRef.current !== prefetchPromise ||
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
    prefetchRequestOffset.current = offset;
    try {
      await prefetchPromise;
    } catch {
      // Load more can make a normal request when the speculative fetch fails.
    } finally {
      if (prefetchPromiseRef.current === prefetchPromise) {
        prefetchPromiseRef.current = null;
        prefetchRequestOffset.current = undefined;
      }
    }
  };

  useEffect(() => {
    let isMounted = true;
    const request = new AbortController();
    queryRequestRef.current = request;
    const requestId = latestQueryRef.current + 1;
    const queryKey = requestedQueryKey;
    const queryChanged = queryKeyRef.current !== queryKey;
    latestQueryRef.current = requestId;
    clearGeometryRequests();
    queryKeyRef.current = queryKey;
    prefetchedPageRef.current = null;
    prefetchedOffsetRef.current = null;
    prefetchPromiseRef.current = null;
    prefetchRequestOffset.current = undefined;
    loadMoreRequestRef.current += 1;
    loadingPageRef.current = undefined;
    isFetchingFirstPageRef.current = true;
    const resume = pendingResume.current?.queryKey === queryKey ? pendingResume.current : undefined;
    pendingResume.current = resume;
    if (queryChanged && !resume) {
      pageHeaderRef.current?.closest(".page-content")?.scrollTo({ top: 0 });
    }
    setIsLoading(true);
    setIsLoadingMore(false);
    setLoadError(false);
    setLoadMoreError(false);
    setRefreshError(false);

    const loadImages = async () => {
      try {
        const targetCount = Math.max(PAGE_SIZE, resume?.loadedCount ?? 0);
        const items: FaceImage[] = [];
        let page: ImagePage;
        do {
          page = await fetchImages({
            folders: selectedFolders, persons: selectedPersons, faceStatuses, selection: geoSelection,
            sortBy: groupingMode, sortDirection,
            limit: Math.min(REFRESH_PAGE_SIZE, targetCount - items.length), offset: items.length,
            signal: request.signal,
          });
          if (!isMounted || latestQueryRef.current !== requestId) return;
          items.push(...page.items);
        } while (items.length < targetCount && page.items.length > 0 && page.has_more);

        setImages(items);
        setDisplayedQueryKey(queryKey);
        setAvailablePersons(page.available_persons);
        setTotalImages(page.total);
        setHasMore(page.has_more);
        setLoadError(false);
        loadedCountRef.current = Math.max(PAGE_SIZE, items.length);
        if (resume && pendingResume.current === resume) {
          if (resume.selection) {
            const confirmed = new Set(items.map((image) => image.id));
            setSelectedAlbumImages(new Set(resume.selection.filter((id) => confirmed.has(id))));
          }
          window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
            if (isMounted && latestQueryRef.current === requestId && pendingResume.current === resume) {
              pendingResume.current = undefined;
              pageHeaderRef.current?.closest(".page-content")?.scrollTo({ top: resume.scrollTop });
            }
          }));
        }
        if (page.has_more) {
          void scheduleNextPagePrefetch(requestId, loadedCountRef.current, queryKey);
        }
      } catch (error) {
        if (request.signal.aborted || !isMounted || latestQueryRef.current !== requestId) return;
        console.error("Bilder konnten nicht geladen werden:", error);
        setLoadError(true);
      } finally {
        if (isMounted && latestQueryRef.current === requestId) {
          isFetchingFirstPageRef.current = false;
          setIsLoading(false);
        }
      }
    };

    void loadImages();

    return () => {
      isMounted = false;
      request.abort();
      if (queryRequestRef.current === request) queryRequestRef.current = null;
    };
  }, [clearGeometryRequests, faceStatuses, galleryRevision, geoSelection, groupingMode, retryLoad, selectedFolders, selectedPersons, sortDirection]);

  useEffect(() => {
    setSelectedAlbumImages(null);
  }, [faceStatuses, geoSelection, selectedFolders, selectedPersons]);

  useEffect(() => {
    const closeGalleryAfterRestore = (update: { reason?: string }) => {
      if (update?.reason !== "database_import") return;
      latestQueryRef.current += 1;
      loadMoreRequestRef.current += 1;
      loadingPageRef.current = undefined;
      isFetchingFirstPageRef.current = true;
      loadedCountRef.current = PAGE_SIZE;
      prefetchedPageRef.current = null;
      prefetchedOffsetRef.current = null;
      prefetchPromiseRef.current = null;
      prefetchRequestOffset.current = undefined;
      clearGeometryRequests();
      pendingResume.current = undefined;
      queryRequestRef.current?.abort();
      refreshImagesRequestRef.current?.abort();
      setImages([]);
      setDisplayedQueryKey("");
      setShowAlbumLibrary(false);
      setAlbumPickerIds(null);
      setSelectedAlbumImages(null);
      setAlbumStatus(undefined);
      setAvailablePersons([]);
      setTotalImages(0);
      setLibraryTotal(null);
      setHasMore(false);
      setIsLoading(true);
      setIsLoadingMore(false);
      setGalleryRevision((value) => value + 1);
    };
    const storeLiveChange = (update: { reason?: string }) => {
      closeGalleryAfterRestore(update);
      if (update?.reason === "library_identity") return false;
      // Large, deep views reconcile at import completion. Re-reading every
      // previously loaded page for each checkpoint competes with the import.
      if (update?.reason === "background_progress" && loadedCountRef.current > REFRESH_PAGE_SIZE) return false;
      liveRevision.current += 1;
      return true;
    };
    if (!active) return subscribeToTopic<{ reason?: string }>("clusters", storeLiveChange);
    let isMounted = true;
    const libraryTotalRequest = new AbortController();
    let refreshInFlight = false;
    let refreshPending = false;
    let userBusyUntil = 0;
    const captureViewportAnchor = (fresh: ReadonlyMap<number, FaceImage>, requestId: number) => {
      const scroller = pageHeaderRef.current?.closest(".page-content");
      if (!(scroller instanceof HTMLElement) || scroller.scrollTop < 80) {
        return null;
      }
      const viewport = scroller.getBoundingClientRect();
      for (const card of scroller.querySelectorAll<HTMLElement>("[data-image-id]")) {
        const imageId = card.dataset.imageId ?? "";
        if (!fresh.has(Number(imageId))) continue;
        const { top, bottom } = card.getBoundingClientRect();
        if (bottom > viewport.top && top < viewport.bottom) {
          return { scroller, imageId, offset: top - viewport.top, requestId };
        }
      }
      return null;
    };

    const refreshVisibleImages = async (preserveViewport = true) => {
      const requestId = latestQueryRef.current;
      const request = new AbortController();
      refreshImagesRequestRef.current = request;
      const count = loadedCountRef.current;
      try {
        const freshById = new Map<number, FaceImage>();
        let offset = 0;
        let page: ImagePage;
        do {
          page = await fetchImages({
            folders: selectedFolders,
            persons: selectedPersons,
            faceStatuses,
            selection: geoSelection,
            sortBy: groupingMode,
            sortDirection,
            limit: Math.min(REFRESH_PAGE_SIZE, count - offset),
            offset,
            signal: request.signal,
          });
          if (request.signal.aborted || !isMounted || latestQueryRef.current !== requestId) return false;
          page.items.forEach((image) => freshById.set(image.id, image));
          offset += page.items.length;
        } while (offset < count && page.items.length > 0 && page.has_more);

        freshById.forEach((image, id) => {
          freshById.set(id, applyImageGeometry(image, resolvedGeometryRef.current.get(id)));
        });
        const fresh = Array.from(freshById.values());
        const anchor = preserveViewport ? captureViewportAnchor(freshById, requestId) : null;
        refreshAnchorRef.current = anchor ?? undefined;
        // Keep existing cards at their current array index while the user is
        // below the top. New imports are appended to this live snapshot instead
        // of redistributing the masonry columns underneath the user's pointer.
        setImages((current) => {
          if (request.signal.aborted || !isMounted || latestQueryRef.current !== requestId) return current;
          if (!anchor) return fresh;
          const stable = current
            .map((image) => freshById.get(image.id))
            .filter((image): image is FaceImage => image !== undefined);
          const knownIds = new Set(stable.map((image) => image.id));
          return [
            ...stable,
            ...fresh.filter((image) => !knownIds.has(image.id)),
          ];
        });
        setAvailablePersons(page.available_persons);
        setTotalImages(page.total);
        setDisplayedQueryKey(requestedQueryKey);
        setHasMore(page.has_more);
        setLoadError(false);
        prefetchedPageRef.current = null;
        prefetchedOffsetRef.current = null;
        prefetchPromiseRef.current = null;
        prefetchRequestOffset.current = undefined;
        loadedCountRef.current = Math.max(PAGE_SIZE, fresh.length);
        if (page.has_more) {
          void scheduleNextPagePrefetch(
            requestId,
            loadedCountRef.current,
            queryKeyRef.current,
          );
        }
        return true;
      } catch (error) {
        if (!request.signal.aborted && isMounted && latestQueryRef.current === requestId) {
          console.error("Live-Aktualisierung der Bilder fehlgeschlagen:", error);
        }
        return false;
      } finally {
        if (refreshImagesRequestRef.current === request) refreshImagesRequestRef.current = null;
      }
    };

    const refreshLibraryTotal = async () => {
      const requestId = latestQueryRef.current;
      try {
        const page = await fetchImages({ limit: 1, offset: 0, signal: libraryTotalRequest.signal });
        if (isMounted && latestQueryRef.current === requestId) setLibraryTotal(page.total);
        return true;
      } catch (error) {
        if (isMounted && !libraryTotalRequest.signal.aborted) console.error("Bibliotheksgröße konnte nicht geladen werden:", error);
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
      const requestId = latestQueryRef.current;
      const revision = liveRevision.current;
      try {
        const [imagesUpdated, totalUpdated] = await Promise.all([
          refreshVisibleImages(preserveViewport),
          refreshLibraryTotal(),
        ]);
        if (isMounted && latestQueryRef.current === requestId) {
          if (imagesUpdated && totalUpdated) appliedLiveRevision.current = revision;
          setRefreshError(!imagesUpdated || !totalUpdated);
        }
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

    const refreshIfChanged = () => {
      if (document.visibilityState === "visible" && liveRevision.current !== appliedLiveRevision.current) {
        scheduleRefresh(true, FINAL_REFRESH_DELAY_MS, true);
      }
    };
    const noteUserActivity = () => {
      userBusyUntil = performance.now() + USER_IDLE_DELAY_MS;
    };

    document.addEventListener("visibilitychange", refreshIfChanged);
    document.addEventListener("scroll", noteUserActivity, true);
    document.addEventListener("pointerdown", noteUserActivity, true);
    window.addEventListener("focus", refreshIfChanged);
    const unsubscribeClusters = subscribeToTopic<{ reason?: string }>(
      "clusters",
      (update) => {
        if (!storeLiveChange(update)) return;
        const isBackgroundCheckpoint = update?.reason === "background_progress";
        scheduleRefresh(
          true,
          isBackgroundCheckpoint ? LIVE_REFRESH_BATCH_MS : FINAL_REFRESH_DELAY_MS,
          !isBackgroundCheckpoint,
        );
      },
    );

    if (retryRefresh !== handledRetry.current) {
      handledRetry.current = retryRefresh;
      scheduleRefresh(true, FINAL_REFRESH_DELAY_MS, true);
    } else if (hasActivatedLiveRefreshRef.current) {
      refreshIfChanged();
    } else {
      hasActivatedLiveRefreshRef.current = true;
      void refreshLibraryTotal().then((updated) => {
        if (isMounted && !updated) setRefreshError(true);
      });
    }

    return () => {
      isMounted = false;
      libraryTotalRequest.abort();
      refreshImagesRequestRef.current?.abort();
      refreshImagesRequestRef.current = null;
      if (liveRefreshTimerRef.current !== null) {
        window.clearTimeout(liveRefreshTimerRef.current);
        liveRefreshTimerRef.current = null;
      }
      unsubscribeClusters();
      document.removeEventListener("visibilitychange", refreshIfChanged);
      document.removeEventListener("scroll", noteUserActivity, true);
      document.removeEventListener("pointerdown", noteUserActivity, true);
      window.removeEventListener("focus", refreshIfChanged);
    };
  }, [active, clearGeometryRequests, faceStatuses, galleryRevision, geoSelection, groupingMode, isLoadingMore, retryRefresh, selectedFolders, selectedPersons, sortDirection]);

  const loadMoreImages = async () => {
    if (!isMountedRef.current || staleResults || isLoading || loadingPageRef.current !== undefined ||
        isFetchingFirstPageRef.current || refreshImagesRequestRef.current || loadError || !hasMore) return;
    const loadMoreRequest = ++loadMoreRequestRef.current;
    const requestId = latestQueryRef.current;
    const signal = queryRequestRef.current?.signal;
    const offset = loadedCountRef.current;
    const takePrefetchedPage = () => {
      if (prefetchedOffsetRef.current !== offset) return null;
      const page = prefetchedPageRef.current;
      prefetchedPageRef.current = null;
      prefetchedOffsetRef.current = null;
      return page;
    };

    const appendPage = (page: ImagePage) => {
      const pageItems = page.items.map((image) => applyImageGeometry(image, resolvedGeometryRef.current.get(image.id)));
      setImages((current) => {
        const merged = new Map(current.map((image) => [image.id, image]));
        pageItems.forEach((image) => merged.set(image.id, image));
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

    const cachedPage = takePrefetchedPage();
    if (cachedPage) {
      appendPage(cachedPage);
      return;
    }

    loadingPageRef.current = loadMoreRequest;
    setIsLoadingMore(true);
    setLoadMoreError(false);
    try {
      if (prefetchRequestOffset.current === offset) await prefetchPromiseRef.current?.catch(() => undefined);
      if (signal?.aborted || latestQueryRef.current !== requestId) return;
      const page = takePrefetchedPage() ?? await fetchImages({
        folders: selectedFolders,
        persons: selectedPersons,
        faceStatuses,
        selection: geoSelection,
        sortBy: groupingMode,
        sortDirection,
        limit: PAGE_SIZE,
        offset,
        signal,
      });
      if (signal?.aborted || !isMountedRef.current || latestQueryRef.current !== requestId || loadMoreRequestRef.current !== loadMoreRequest) return;
      appendPage(page);
    } catch (error) {
      if (signal?.aborted || !isMountedRef.current || latestQueryRef.current !== requestId || loadMoreRequestRef.current !== loadMoreRequest) return;
      console.error("Weitere Bilder konnten nicht geladen werden:", error);
      setLoadMoreError(true);
    } finally {
      if (loadingPageRef.current === loadMoreRequest) loadingPageRef.current = undefined;
      if (isMountedRef.current && loadMoreRequestRef.current === loadMoreRequest) setIsLoadingMore(false);
    }
  };

  const hasActiveFilters =
    Boolean(geoSelection) || selectedPersons.length > 0 ||
    selectedFolders.length > 0 ||
    faceStatuses.length > 0;
  // Only the unfiltered total can decide whether setup is still required.
  // The current result may legitimately be empty for a status/person/folder.
  const knownLibraryTotal =
    libraryTotal ?? (!hasActiveFilters ? totalImages : null);
  const libraryIsEmpty =
    !isLoading &&
    !loadError &&
    !albumId &&
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
    pendingResume.current = undefined;
    setSelectedPersons([]);
    setSelectedFolders([]);
    setFaceStatuses([]);
    if (geoSelection) onChangeGeoSelection();
  };
  const setAlbumFilter = (album?: PhotoAlbum, record = false) => {
    if (record && album?.id !== albumId) checkpoint();
    setShowAlbumLibrary(false);
    onChangeGeoSelection({ ...geoSelection, album_id: album?.id });
  };
  const openAlbumLibrary = () => {
    setAlbumStatus(undefined);
    setShowAlbumLibrary(true);
  };
  const selectedCount = selectedAlbumImages?.size ?? 0;
  const toggleAlbumPhoto = useCallback((id: number) => {
    setSelectedAlbumImages((current) => {
      if (!current) return null;
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const updateActiveAlbum = async (action: "cover" | "remove") => {
    if (!albumId || !selectedAlbumImages?.size || albumMutation.current || staleResults || isLoading || loadError) return;
    const ids = [...selectedAlbumImages];
    if (action === "cover" && ids.length !== 1) return;
    if (action === "remove" && !window.confirm(
      `Ausgewählte Fotos aus „${activeAlbum?.name ?? "Album"}“ entfernen? Sie bleiben in der Bibliothek.`,
    )) return;
    albumMutation.current = true;
    setAlbumPending(true);
    setAlbumStatus(undefined);
    try {
      if (action === "cover") {
        await updatePhotoAlbum(albumId, { cover_image_id: ids[0] });
        setAlbumStatus({ text: "Titelbild gespeichert." });
      } else {
        await removePhotoAlbumImages(albumId, ids);
        setRetryLoad((value) => value + 1);
        setAlbumStatus({ text: "Fotos aus dem Album entfernt." });
      }
      setSelectedAlbumImages(null);
    } catch (error) {
      setAlbumStatus({ text: error instanceof Error ? error.message : "Das Album konnte nicht bearbeitet werden.", error: true });
    } finally {
      albumMutation.current = false;
      setAlbumPending(false);
    }
  };
  const albumDialogs = <>
    {showAlbumLibrary && albumPickerIds === null && <AlbumLibraryDialog
      albums={albums} loading={albumsLoading} error={albumsError} reload={reloadAlbums} activeAlbumId={albumId}
      onClose={() => setShowAlbumLibrary(false)} onSelect={(album) => setAlbumFilter(album, true)}
      onCreate={() => setAlbumPickerIds([])}
      onDeleted={(id) => { if (albumId === id) setAlbumFilter(); }}
    />}
    {albumPickerIds !== null && <AlbumPickerDialog imageIds={albumPickerIds}
      onClose={() => setAlbumPickerIds(null)} onSaved={(album) => {
        if (albumPickerIds.length === 0) setAlbumFilter(album, true);
        setSelectedAlbumImages(null);
        setAlbumStatus({ text: `Album „${album.name}“ gespeichert.` });
      }} />}
  </>;

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
            <button className="text-button collection-start__albums" type="button" onClick={openAlbumLibrary}>Alben öffnen</button>
          </div>
          <ul className="collection-start__steps">
            <li><strong>Doppelte Fotos zusammenfassen</strong><span>Identische Dateien erscheinen einmal. Alle Speicherorte bleiben erhalten.</span></li>
            <li><strong>Ähnliche Gesichter gruppieren</strong><span>Face Manager sucht passende Gruppen und schlägt bekannte Personen vor.</span></li>
            <li><strong>Personen Namen geben</strong><span>Ein Name pro Gruppe genügt. Vorschläge prüfst du unter „Personen“.</span></li>
          </ul>
        </section>
        {importDialog}
        {albumDialogs}
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
        resultCount={isLoading || staleResults || loadError ? null : totalImages}
        totalCount={libraryTotal}
        resultNoun="Bilder"
        resultSingular="Bild"
        resultsUnavailable={loadError}
        faceStatuses={faceStatuses}
        onFaceStatusesChange={setFaceStatuses}
        onResetFilters={hasActiveFilters ? resetFilters : undefined}
        actions={<>
          <button type="button" className={`filter-bar__control${albumId ? " filter-bar__control--active" : ""}`}
            onClick={openAlbumLibrary}>Alben</button>
          {images.length > 0 && <button type="button" className="filter-bar__control"
            aria-pressed={selectedAlbumImages !== null} disabled={albumPending || staleResults || isLoading || loadError}
            onClick={() => {
              setAlbumStatus(undefined);
              setSelectedAlbumImages(selectedAlbumImages ? null : new Set());
            }}>{selectedAlbumImages ? "Auswahl beenden" : "Fotos auswählen"}</button>}
        </>}
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
              setPreference("image_grid_size", option.value);
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
          <option value="all">Alle Gesichtsrahmen anzeigen</option>
          <option value="assigned">Nur Rahmen zugeordneter Gesichter</option>
          <option value="none">Keine Gesichtsrahmen anzeigen</option>
        </select>
      </LibraryFilterBar>

      {albumId && <div className="active-folder-filters" aria-label="Albumfilter">
        <span>Album</span>
        <button type="button" aria-label="Albumfilter entfernen" onClick={() => setAlbumFilter()}>
          {activeAlbum?.name ?? "Album"} <b aria-hidden="true">×</b>
        </button>
        {activeAlbum && onOpenAlbumMap && <button type="button" onClick={() => onOpenAlbumMap(albumId)}>Auf Karte zeigen</button>}
      </div>}

      {geoSelection && (geoSelection.bounds || geoSelection.from_date || geoSelection.to_date || geoSelection.image_ids) && (
        <div className="active-folder-filters" aria-label="Auswahl aus der Fotokarte">
          <span>Auswahl aus der Fotokarte</span>
          {geoSelection.bounds && (
            <button type="button" aria-label="Kartenausschnitt entfernen"
              onClick={() => onChangeGeoSelection({ ...geoSelection, bounds: undefined })}>
              Kartenausschnitt <b aria-hidden="true">×</b>
            </button>
          )}
          {(geoSelection.from_date || geoSelection.to_date) && (
            <button type="button" aria-label="Aufnahmezeitraum entfernen"
              onClick={() => onChangeGeoSelection({ ...geoSelection, from_date: undefined, to_date: undefined })}>
              {[geoSelection.from_date, geoSelection.to_date]
                .map((date) => date ? new Date(`${date}T12:00:00`).toLocaleDateString("de-DE") : "offen")
                .join(" – ")} <b aria-hidden="true">×</b>
            </button>
          )}
          {geoSelection.image_ids && (
            <button type="button" aria-label="Reisestopp entfernen"
              onClick={() => onChangeGeoSelection({ ...geoSelection, image_ids: undefined })}>
              Reisestopp <b aria-hidden="true">×</b>
            </button>
          )}
        </div>
      )}

      {selectedAlbumImages && <div className="photo-selection-toolbar" role="group" aria-label="Fotoauswahl für Alben">
        <span role="status">{selectedCount.toLocaleString("de-DE")} ausgewählt</span>
        <button type="button" className="secondary-button" disabled={albumPending || staleResults || isLoading || loadError}
          onClick={() => setSelectedAlbumImages(new Set(images.map((image) => image.id)))}>Geladene Fotos auswählen</button>
        <button type="button" className="neon-button" disabled={albumPending || staleResults || isLoading || loadError || selectedCount === 0}
          onClick={() => setAlbumPickerIds([...selectedAlbumImages])}>Zum Album hinzufügen</button>
        {albumId && <>
          {selectedCount === 1 && <button type="button" className="secondary-button" disabled={albumPending || staleResults || isLoading || loadError}
            onClick={() => void updateActiveAlbum("cover")}>Als Titelbild festlegen</button>}
          <button type="button" className="secondary-button" disabled={albumPending || staleResults || isLoading || loadError || selectedCount === 0}
            onClick={() => void updateActiveAlbum("remove")}>Aus Album entfernen</button>
        </>}
      </div>}
      {albumStatus && <p className="image-grid-status" role={albumStatus.error ? "alert" : "status"}>{albumStatus.text}</p>}

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
          Die Auswahl konnte nicht geladen werden. {images.length > 0 && "Der letzte geladene Stand bleibt sichtbar. "}
          <button type="button" onClick={() => setRetryLoad((value) => value + 1)}>
            Erneut laden
          </button>
        </div>
      )}
      {isLoading && images.length > 0 && <p className="image-grid-status" role="status">Bilder werden aktualisiert …</p>}
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
        key={galleryRevision}
        active={active}
        images={images}
        isLoading={(isLoading || staleResults) && images.length === 0}
        interactionsDisabled={staleResults || isLoading || loadError}
        isUpdating={isLoading}
        hasMore={hasMore && !staleResults && !loadMoreError && !loadError}
        isLoadingMore={isLoadingMore}
        faceOverlayMode={faceOverlayMode}
        gridSize={imageGridSize}
        onNavigateToCluster={onNavigateToCluster}
        onLoadMore={loadMoreImages}
        onImageGeometryNeeded={requestImageGeometry}
        hasActiveFilters={hasActiveFilters}
        onResetFilters={resetFilters}
        photoSelection={selectedAlbumImages ? { ids: selectedAlbumImages, onToggle: toggleAlbumPhoto } : undefined}
        onManualFaceSaved={(detail, faceId) => {
          if (faceStatuses.length && !faceStatuses.includes("active")) return;
          const face = detail.faces.find((item) => item.id === faceId);
          if (!face || !detail.image_width || !detail.image_height) return;
          setImages((current) => current.map((image) => {
            if (image.id !== detail.id || image.content_hash !== detail.content_hash ||
                image.faces.some((item) => item.id === faceId)) return image;
            const oriented = applyImageGeometry(image, { source: image, detail });
            if (oriented.image_width !== detail.image_width || oriented.image_height !== detail.image_height) return image;
            return { ...oriented, faces: [...oriented.faces, face] };
          }));
        }}
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
      {albumDialogs}
    </>
  );
};

export default PeoplePage;
