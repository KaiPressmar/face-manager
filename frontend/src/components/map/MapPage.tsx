import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  fetchMapPoints, fetchSelectedMapPoints,
  type MapImage, type MapImageCursor, type MapPoint, type MapPointsResponse,
} from "../../utils/api";
import WorldMap, { boundsForView, countryLabels, normalizedView, type MapBounds, type MapGrid, type MapView } from "./WorldMap";
import MapGallery, { type MapGalleryOpening } from "./MapGallery";
import MapPhotoTile from "./MapPhotoTile";
import MapAlbumDialog, { type MapAlbumOpening } from "./MapAlbumDialog";
import MapJourney, { journeyStopLabel } from "./MapJourney";
import MapTrips from "./MapTrips";
import MapObservedPlaces from "./MapObservedPlaces";
import AlbumPickerDialog from "../shared/AlbumPickerDialog";
import { updatePhotoAlbum, usePhotoAlbums } from "../../utils/photoAlbums";
import { applyTripSelection, photoIdsForTrip, selectedTripDays, type TravelHome, type TripSelection } from "./journeyTrips";
import { fitAlbumJourney } from "./MapAlbumArtwork";
import { fetchJourney, fetchMapSelection, fetchMapYears, omitInferredPhotos, type Journey, type JourneyDetail } from "./mapData";
import { describeMapDates, formatMapDate, parseMapDate } from "./mapDates";
import { countWithLabel } from "../../utils/germanFormatting";
import type { GeoPhotoSelection } from "../../utils/geoPhotoSelection";
import { subscribeToTopic } from "../../utils/events";
import { useWorkspaceNavigation } from "../../hooks/useWorkspaceNavigation";
import { useUiPreferences } from "../../hooks/useUiPreferences";
import "./map.css";
import "./cartography.css";
import "./map-workspace.css";

const PAGE_SIZE = 36;
const MAX_IMAGE_REQUEST_SIZE = 200;
const COMPACT_MAP_QUERY = "(max-width: 960px)";
const MAP_LIBRARY_EVENTS = new Set([
  "database_import", "delete_image", "image_path_cleanup", "image_rename", "import_completed", "albums_changed",
]);
const EMPTY_POINTS: MapPoint[] = [];
const EMPTY_IMAGES: MapImage[] = [];
type MapResult<T> = { key: string; data: T | null; error: string };
type DateFields = { from: string; to: string };
type MapPane = "map" | "results";
type DateError = { field: keyof DateFields | "both"; text: string };
const reversedDates: DateError = { field: "both", text: "Das Enddatum muss am oder nach dem Startdatum liegen." };
type ImagePage = {
  items: MapImage[];
  total: number;
  nextCursor: MapImageCursor | null;
  extended: boolean;
};
type MapImageResult = MapResult<ImagePage> & { revision: number };
const INITIAL_VIEW: MapView = { longitude: 0, latitude: 0, zoom: 1 };
const REGIONS: { label: string; view: MapView }[] = [
  { label: "Welt", view: INITIAL_VIEW },
  { label: "Europa", view: { longitude: 12, latitude: 51, zoom: 5 } },
  { label: "Nordamerika", view: { longitude: -100, latitude: 43, zoom: 3 } },
  { label: "Südamerika", view: { longitude: -62, latitude: -18, zoom: 3 } },
  { label: "Asien", view: { longitude: 105, latitude: 34, zoom: 3 } },
  { label: "Afrika", view: { longitude: 20, latitude: 1, zoom: 3 } },
  { label: "Ozeanien", view: { longitude: 138, latitude: -27, zoom: 3 } },
];

interface Props {
  active: boolean;
  openAlbumId?: string;
  onAlbumChange?: (albumId?: string, options?: { replace?: boolean }) => void;
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
  onOpenImages?: (selection: GeoPhotoSelection) => void;
}

interface MapWorkspace {
  view: MapView;
  mode: "locations" | "journey";
  region: MapBounds | null;
  regionName: string;
  fromDate: string;
  toDate: string;
  datePreset: string;
  dateDraft?: DateFields;
  dateEntryError?: DateError;
  albumId: string;
  journeyDetail: JourneyDetail;
  journeyStopId?: string;
  includeInferred: boolean;
  home?: TravelHome;
  trip?: TripSelection;
  resultScroll: number;
  compactPane: MapPane;
  filtersOpen: boolean;
  loadedResults?: MapImageResult;
}

const formatter = new Intl.NumberFormat("de-DE");
const formatCount = (count: number) => formatter.format(count);
const shortDate = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short", timeZone: "UTC" });
const year = new Date().getFullYear();

const countries = [...countryLabels].sort((a, b) => a.name.localeCompare(b.name, "de"));

function viewForBounds(bounds: MapBounds): MapView {
  // The interactive map has fixed world edges. Show both sides of a date-line crossing.
  if (bounds.west > bounds.east || bounds.west < -180 || bounds.east > 180) return INITIAL_VIEW;
  return normalizedView({
    longitude: (bounds.west + bounds.east) / 2,
    latitude: (bounds.south + bounds.north) / 2,
    zoom: Math.min(360 / (bounds.east - bounds.west), 180 / (bounds.north - bounds.south)) * .9,
  });
}

function journeyView(journey: Journey, detail: JourneyDetail) {
  return viewForBounds(fitAlbumJourney(journey.stops, journey.connections, detail === "city" ? .008 : .08));
}

const MapPage: React.FC<Props> = ({ active, openAlbumId, onAlbumChange, onNavigateToCluster, onOpenImages }) => {
  const [view, setView] = useState<MapView>(INITIAL_VIEW);
  const [mode, setMode] = useState<"locations" | "journey">("locations");
  const [onlineDetails, setOnlineDetails] = useState(false);
  const { values: { map_style: mapStyle, map_country_names: showNames }, setPreference } = useUiPreferences();
  const [journeyResult, setJourneyResult] = useState<MapResult<Journey> | null>(null);
  const [journeyStopId, setJourneyStopId] = useState<string | undefined>();
  const [stopGroup, setStopGroup] = useState<string[]>();
  const framedJourney = useRef<string>();
  const preserveRestoredView = useRef(false);
  const [journeyRetry, setJourneyRetry] = useState(0);
  const [journeyDetail, setJourneyDetail] = useState<JourneyDetail>("trip");
  const [includeInferred, setIncludeInferred] = useState(true);
  const isJourney = mode === "journey";
  const [pointGrid, setPointGrid] = useState<MapGrid | null>(null);
  const [region, setRegion] = useState<MapBounds | null>(null);
  const [regionName, setRegionName] = useState("");
  const [selectMode, setSelectMode] = useState(false);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [dateDraft, setDateDraft] = useState<DateFields>();
  const [dateEntryError, setDateEntryError] = useState<DateError>();
  const [datePreset, setDatePreset] = useState("all");
  const [mapYears, setMapYears] = useState<{ albumId: string; years: string[] }>();
  const [compactLayout, setCompactLayout] = useState(() => window.matchMedia(COMPACT_MAP_QUERY).matches);
  const [compactPane, setCompactPane] = useState<MapPane>("map");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filtersButton = useRef<HTMLButtonElement>(null);
  const periodFilter = useRef<HTMLSelectElement>(null);
  const mapPaneButton = useRef<HTMLButtonElement>(null);
  const resultsPaneButton = useRef<HTMLButtonElement>(null);
  const mapPanelRef = useRef<HTMLElement>(null);
  const focusResults = useRef(false);
  const savedResultScroll = useRef(0);
  const [pointsResult, setPointsResult] = useState<MapResult<MapPointsResponse> | null>(null);
  const [imagesResult, setImagesResult] = useState<MapImageResult | null>(null);
  const [moreLoading, setMoreLoading] = useState(false);
  const [moreError, setMoreError] = useState("");
  const resultsRef = useRef<HTMLElement>(null);
  const stopPhotosRef = useRef<HTMLHeadingElement>(null);
  const stopOpener = useRef<HTMLButtonElement>();
  const focusStopPhotos = useRef(false);
  const restoreResultScroll = useRef<number>();
  const moreController = useRef<AbortController | null>(null);
  const galleryOpenerRef = useRef<HTMLElement | null>(null);
  const albumOpenerRef = useRef<HTMLButtonElement | null>(null);
  const [gallery, setGallery] = useState<MapGalleryOpening | null>(null);
  const [album, setAlbum] = useState<MapAlbumOpening | null>(null);
  const [pointRefresh, setPointRefresh] = useState(0);
  const [imageRefresh, setImageRefresh] = useState(0);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [placeLibraryEpoch, setPlaceLibraryEpoch] = useState(0);
  const [albumId, setAlbumId] = useState("");
  const { albums, loading: albumsLoading, error: albumsError, reload: reloadAlbums } = usePhotoAlbums(active);
  const selectedAlbum = albums.find((item) => item.id === albumId);
  const [home, setHome] = useState<TravelHome>();
  const [trip, setTrip] = useState<TripSelection>();
  const [saveSelection, setSaveSelection] = useState<{ selection: GeoPhotoSelection; count: number; travel: boolean }>();
  const albumPickerOpener = useRef<HTMLElement | null>(null);
  const openedAlbumRoute = useRef<string>();
  const [albumRouteRevision, setAlbumRouteRevision] = useState(0);
  const [routeNotice, setRouteNotice] = useState("");
  const showResultsPane = !compactLayout || compactPane === "results";

  useEffect(() => {
    const media = window.matchMedia(COMPACT_MAP_QUERY);
    const updateLayout = () => {
      if (media.matches) {
        if (resultsRef.current?.contains(document.activeElement)) setCompactPane("results");
        else if (mapPanelRef.current?.contains(document.activeElement)) setCompactPane("map");
      }
      setCompactLayout(media.matches);
    };
    updateLayout();
    media.addEventListener("change", updateLayout);
    return () => media.removeEventListener("change", updateLayout);
  }, []);

  const { checkpoint } = useWorkspaceNavigation<MapWorkspace>("map", {
    capture: () => ({ view, mode, region, regionName, fromDate, toDate, datePreset, dateDraft, dateEntryError,
      albumId, journeyDetail, journeyStopId, includeInferred, home, trip, compactPane, filtersOpen,
      resultScroll: showResultsPane ? resultsRef.current?.scrollTop ?? 0 : savedResultScroll.current,
      loadedResults: imageResult?.data?.extended && imageResult.revision === libraryRevision ? imageResult : undefined }),
    restore: (saved) => {
      setView(saved?.view ?? INITIAL_VIEW);
      setMode(saved?.mode ?? "locations");
      setRegion(saved?.region ?? null);
      setRegionName(saved?.regionName ?? "");
      setFromDate(saved?.fromDate ?? "");
      setToDate(saved?.toDate ?? "");
      setDatePreset(saved?.datePreset ?? "all");
      setDateDraft(saved?.dateDraft);
      setDateEntryError(saved?.dateEntryError);
      setAlbumId(saved?.albumId ?? "");
      setJourneyDetail(saved?.journeyDetail ?? "trip");
      setJourneyStopId(saved?.journeyStopId);
      setStopGroup(undefined);
      framedJourney.current = undefined;
      preserveRestoredView.current = saved?.mode === "journey";
      if (saved?.loadedResults) setImagesResult(saved.loadedResults);
      setIncludeInferred(saved?.includeInferred ?? true);
      setHome(saved?.home);
      setTrip(saved?.trip);
      setSelectMode(false);
      setRouteNotice("");
      setCompactPane(saved?.compactPane ?? "map");
      setFiltersOpen(saved?.filtersOpen ?? Boolean(saved?.dateEntryError));
      openedAlbumRoute.current = saved?.albumId || undefined;
      restoreResultScroll.current = saved?.resultScroll ?? 0;
      setAlbumRouteRevision((revision) => revision + 1);
    },
  });

  const dateError = dateEntryError ?? (fromDate && toDate && fromDate > toDate ? reversedDates : undefined);
  const waitingForRouteAlbum = Boolean(openAlbumId && albumId !== openAlbumId);
  const canShowResults = !dateError && !waitingForRouteAlbum;
  const canLoad = active && canShowResults;
  const showPoints = canShowResults && !isJourney && pointGrid !== null;
  const canLoadPoints = active && showPoints;
  const handleGridChange = useCallback((grid: MapGrid) => {
    setPointGrid((current) => current?.columns === grid.columns && current.rows === grid.rows ? current : grid);
  }, []);
  const viewport = useMemo(() => boundsForView(view), [view]);
  const filterBounds = region || viewport;
  const areaInView = !region || (viewport.west <= (region.west > region.east ? -180 : region.west) + .0001
    && viewport.east >= (region.west > region.east ? 180 : region.east) - .0001
    && viewport.south <= region.south + .0001 && viewport.north >= region.north - .0001);
  const regionChoice = isJourney ? region ? REGIONS.some((item) => item.label === regionName) ? regionName : "custom" : "Welt"
    : REGIONS.find((item) => JSON.stringify(item.view) === JSON.stringify(view))?.label || "custom";
  const queryFrom = trip?.day || fromDate;
  const queryTo = trip?.day || toDate;
  const shownDates = dateDraft ?? { from: formatMapDate(fromDate), to: formatMapDate(toDate) };
  const dates = { from_date: queryFrom || undefined, to_date: queryTo || undefined, album_id: albumId || undefined };
  // A background refresh can keep displaying the last route for this exact selection.
  const journeyKey = JSON.stringify([queryFrom, queryTo, region, journeyDetail, albumId]);
  const journeyState = isJourney && canShowResults && journeyResult?.key === journeyKey ? journeyResult : null;
  const baseJourney = journeyState?.data;
  const tripsSource = baseJourney ?? (trip ? journeyResult?.data : null);
  const journey = useMemo(() => {
    if (!baseJourney) return baseJourney;
    const scope = includeInferred ? baseJourney : omitInferredPhotos(baseJourney);
    return trip ? applyTripSelection(scope, photoIdsForTrip(trip)) : scope;
  }, [baseJourney, includeInferred, trip]);
  const journeyFrameKey = JSON.stringify([journeyKey, trip?.trip.id, trip?.includeArrival, trip?.includeDeparture]);
  const journeyStop = journey?.stops.find((stop) => stop.id === journeyStopId);
  const stopDay = journeyStop?.start.slice(0, 10);
  const showImages = canShowResults && (!isJourney || Boolean(journeyStop));
  const canLoadImages = active && showImages;
  const locationPhotoIds = useMemo(() => !isJourney && trip ? [...photoIdsForTrip(trip)] : undefined, [isJourney, trip]);
  const selection: GeoPhotoSelection = isJourney && journeyStop
    ? { ...dates, image_ids: journeyStop.image_ids }
    : { ...dates, bounds: filterBounds, image_ids: locationPhotoIds };
  const stopMembership = useMemo(() => journeyStop?.image_ids.join(","), [journeyStop]);
  const inferredIds = useMemo(() => journeyStop?.inferred_image_ids?.length
    ? new Set(journeyStop.inferred_image_ids) : undefined, [journeyStop]);
  const journeySelection: GeoPhotoSelection | undefined = journey?.photo_count
    ? { ...dates, image_ids: journey.stops.flatMap((stop) => stop.image_ids) }
    : undefined;
  const pointQuery = {
    ...viewport, ...dates,
    grid_columns: pointGrid?.columns,
    grid_rows: pointGrid?.rows,
  };
  const pointSelection = region || locationPhotoIds
    ? { ...dates, bounds: region ?? undefined, image_ids: locationPhotoIds }
    : undefined;
  const filterKey = JSON.stringify(isJourney ? ["journey", journeyKey, journeyStopId, stopMembership] : selection);
  // A library update refreshes the current query without hiding its last results.
  const pointKey = JSON.stringify([pointQuery, pointSelection]);
  const pointResult = showPoints && pointsResult?.key === pointKey ? pointsResult : null;
  const imageResult = showImages && imagesResult?.key === filterKey ? imagesResult : null;
  const points = pointResult?.data;
  const collection = pointsResult?.data;
  const images = imageResult?.data?.items ?? EMPTY_IMAGES;
  const imageTotal = imageResult?.data?.total ?? 0;
  const nextCursor = imageResult?.data?.nextCursor;
  const canLoadMore = Boolean(nextCursor) && imageResult?.revision === libraryRevision;
  const hasExtendedResults = imageResult?.data?.extended ?? false;
  const pointsLoading = showPoints && !pointResult;
  const imagesLoading = showImages && !imageResult;
  const openedGallery = active ? gallery : null;
  const availableYears = mapYears?.albumId === albumId ? mapYears.years : [];

  const clearGallery = useCallback(() => setGallery(null), []);
  useEffect(() => {
    if (!active) { clearGallery(); setAlbum(null); setSaveSelection(undefined); }
  }, [active, clearGallery]);

  useLayoutEffect(() => {
    if (!active || albumsLoading || albumsError) return;
    const requestedId = openAlbumId || albumId;
    if (!requestedId) { openedAlbumRoute.current = undefined; return; }
    const target = albums.find((item) => item.id === requestedId);
    if (!target) {
      openedAlbumRoute.current = undefined;
      setAlbumId("");
      setTrip(undefined);
      setJourneyStopId(undefined);
      setRouteNotice("Dieses Album ist nicht mehr verfügbar. Der Albumfilter wurde entfernt.");
      if (openAlbumId) onAlbumChange?.(undefined, { replace: true });
      return;
    }
    if (openedAlbumRoute.current === requestedId) return;
    openedAlbumRoute.current = requestedId;
    setAlbumId(requestedId);
    setRegion(null);
    setRegionName("");
    setTrip(undefined);
    setJourneyStopId(undefined);
    setStopGroup(undefined);
    preserveRestoredView.current = false;
    setView(INITIAL_VIEW);
    setMode(target.journey ? "journey" : "locations");
    setJourneyDetail(target.journey?.detail ?? "trip");
    setFromDate(target.journey?.from_date ?? "");
    setToDate(target.journey?.to_date ?? "");
    setDateDraft(undefined);
    setDateEntryError(undefined);
    setDatePreset(target.journey?.from_date || target.journey?.to_date ? "custom" : "all");
  }, [active, albumId, albumRouteRevision, albums, albumsError, albumsLoading, onAlbumChange, openAlbumId]);

  useEffect(() => {
    if (!active || waitingForRouteAlbum) return;
    const controller = new AbortController();
    void fetchMapYears(albumId, controller.signal).then((years) => {
      if (!controller.signal.aborted) setMapYears({ albumId, years });
    }).catch(() => {
      // Manual date filters remain available when the year list cannot load.
    });
    return () => controller.abort();
  }, [active, albumId, libraryRevision, pointRefresh, waitingForRouteAlbum]);

  useEffect(() => {
    if (!active || !isJourney || !journey?.stops.length || framedJourney.current === journeyFrameKey) return;
    framedJourney.current = journeyFrameKey;
    if (preserveRestoredView.current) preserveRestoredView.current = false;
    else setView(journeyView(journey, journeyDetail));
  }, [active, isJourney, journey, journeyDetail, journeyFrameKey]);

  useEffect(() => { setStopGroup(undefined); }, [journeyFrameKey]);

  useLayoutEffect(() => {
    if (!active || !showResultsPane || !journeyStop || !focusStopPhotos.current) return;
    focusStopPhotos.current = false;
    stopPhotosRef.current?.focus({ preventScroll: true });
    stopPhotosRef.current?.scrollIntoView({ block: "nearest" });
  }, [active, journeyStop, showResultsPane]);

  useEffect(() => {
    if (!canLoad || !isJourney) return;
    const controller = new AbortController();
    void fetchJourney(queryFrom, queryTo, region, journeyDetail, controller.signal, albumId).then((data) => {
      if (!controller.signal.aborted) setJourneyResult({ key: journeyKey, data, error: "" });
    }).catch((error) => {
      if (!controller.signal.aborted) setJourneyResult((current) => ({
        key: journeyKey, data: current?.key === journeyKey ? current.data : null,
        error: error instanceof Error ? error.message : "Der Standortverlauf konnte nicht geladen werden.",
      }));
    });
    return () => controller.abort();
  }, [canLoad, isJourney, queryFrom, queryTo, region, journeyDetail, journeyKey, journeyRetry, albumId, libraryRevision]);

  useEffect(() => {
    if (!active || !journey?.indexing || openedGallery) return;
    const timer = window.setTimeout(() => setJourneyRetry((value) => value + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [active, journey, openedGallery]);

  useEffect(() => {
    let timer: number | undefined;
    const unsubscribe = subscribeToTopic<{ reason?: string }>("clusters", (update) => {
      if (!update?.reason || !MAP_LIBRARY_EVENTS.has(update.reason)) return;
      if (update.reason === "database_import") {
        clearGallery();
        setJourneyResult(null);
        setPointsResult(null);
        setImagesResult(null);
        setAlbum(null);
        setSaveSelection(undefined);
        setAlbumId("");
        setTrip(undefined);
        setHome(undefined);
        setJourneyStopId(undefined);
        setPlaceLibraryEpoch((value) => value + 1);
        window.clearTimeout(timer);
        timer = undefined;
        setLibraryRevision((value) => value + 1);
        return;
      }
      if (timer !== undefined) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        setLibraryRevision((value) => value + 1);
      }, 150);
    });
    return () => {
      unsubscribe();
      window.clearTimeout(timer);
    };
  }, [clearGallery]);

  useEffect(() => {
    if (!canLoadPoints) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const request = pointSelection ? fetchSelectedMapPoints(pointQuery, pointSelection, controller.signal)
        : fetchMapPoints(pointQuery, controller.signal);
      void request
        .then((data) => { if (!controller.signal.aborted) setPointsResult({ key: pointKey, data, error: "" }); })
        .catch((reason) => {
          if (!controller.signal.aborted) setPointsResult((current) => ({
            key: pointKey, data: current?.key === pointKey ? current.data : null,
            error: reason instanceof Error ? reason.message : "Standorte konnten nicht geladen werden.",
          }));
        });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
    // The key encodes every query value.
  }, [canLoadPoints, pointKey, pointRefresh, libraryRevision]);

  useLayoutEffect(() => {
    if (restoreResultScroll.current !== undefined) return;
    savedResultScroll.current = 0;
    resultsRef.current?.scrollTo({ top: 0 });
  }, [filterKey]);

  useLayoutEffect(() => {
    if (!active || !showResultsPane || !resultsRef.current) return;
    if (focusResults.current) {
      focusResults.current = false;
      resultsRef.current.focus();
    }
    if (restoreResultScroll.current === undefined) return;
    if (canLoad && (canLoadImages ? !imageResult : isJourney && !journeyState)) return;
    resultsRef.current.scrollTop = restoreResultScroll.current;
    restoreResultScroll.current = undefined;
  }, [active, albumRouteRevision, canLoad, canLoadImages, imageResult, isJourney, journeyState, showResultsPane]);

  useEffect(() => {
    setMoreError("");
    return () => { moreController.current?.abort(); };
  }, [canLoadImages, filterKey, libraryRevision]);

  useEffect(() => {
    if (!canLoadImages) return;
    const current = imagesResult?.key === filterKey ? imagesResult : null;
    if (current?.revision === libraryRevision && current.data?.extended && !current.error) return;
    const controller = new AbortController();
    const publish = (result: MapImageResult) => {
      if (controller.signal.aborted || moreController.current?.signal.aborted === false) return;
      setImagesResult((latest) => {
        // Pagination may already have delivered a newer, more complete result.
        if (latest !== current && latest?.key === filterKey && latest.revision === libraryRevision &&
            latest.data?.extended) return latest;
        return result;
      });
    };
    const refresh = async () => {
      try {
        // Refill the visible extent and publish the items and new cursor together.
        const target = Math.max(PAGE_SIZE, current?.data?.items.length ?? 0);
        const items: MapImage[] = [];
        let cursor: MapImageCursor | null = null;
        let page;
        do {
          page = await fetchMapSelection(selection, Math.min(MAX_IMAGE_REQUEST_SIZE, target - items.length), cursor, controller.signal);
          if (controller.signal.aborted) return;
          items.push(...page.items);
          cursor = page.next_cursor;
        } while (items.length < target && cursor && page.items.length > 0);
        publish({
          key: filterKey, revision: libraryRevision,
          data: { items, total: page.total, nextCursor: cursor, extended: current?.data?.extended ?? false },
          error: "",
        });
      } catch (reason) {
        publish({
          key: filterKey, revision: current?.revision ?? libraryRevision,
          data: current?.data ?? null,
          error: reason instanceof Error ? reason.message : "Fotos konnten nicht geladen werden.",
        });
      }
    };
    const timer = window.setTimeout(() => { void refresh(); }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [canLoadImages, filterKey, imageRefresh, libraryRevision]);

  useEffect(() => {
    if (!canLoad || !points?.indexing || openedGallery || moreLoading) return;
    const timer = window.setTimeout(() => {
      setPointRefresh((value) => value + 1);
      if (!hasExtendedResults) setImageRefresh((value) => value + 1);
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [canLoad, pointKey, filterKey, pointRefresh, points?.indexing, openedGallery, moreLoading, hasExtendedResults]);

  const commitDates = useCallback((draft: DateFields) => {
    const from = parseMapDate(draft.from);
    const to = parseMapDate(draft.to);
    if (from === null || to === null) {
      setDateEntryError(from === null && to === null
        ? { field: "both", text: "Gib zwei gültige Kalenderdaten ein." }
        : from === null ? { field: "from", text: "Gib bei „Von“ ein gültiges Kalenderdatum ein." }
          : { field: "to", text: "Gib bei „Bis“ ein gültiges Kalenderdatum ein." });
      return;
    }
    if (from && to && from > to) { setDateEntryError(reversedDates); return; }
    setDateEntryError(undefined);
    setDateDraft(undefined);
    setFromDate(from);
    setToDate(to);
    setTrip(undefined);
    setDatePreset(from || to ? "custom" : "all");
    setJourneyStopId(undefined);
  }, []);

  useEffect(() => {
    if (!dateDraft || !active) return;
    const timeout = window.setTimeout(() => commitDates(dateDraft), 500);
    return () => window.clearTimeout(timeout);
  }, [active, commitDates, dateDraft]);

  const editDate = (field: keyof DateFields, value: string) => {
    setDateEntryError(undefined);
    setDateDraft({ ...shownDates, [field]: value });
    setDatePreset("custom");
  };

  const clearDateDraft = () => { setDateDraft(undefined); setDateEntryError(undefined); };

  const useDateRange = (from: string, to: string, preset = "custom") => {
    clearDateDraft();
    setTrip(undefined);
    setFromDate(from);
    setToDate(to);
    setDatePreset(preset);
    setJourneyStopId(undefined);
  };

  const setPreset = (value: string) => {
    clearDateDraft();
    setTrip(undefined);
    setDatePreset(value);
    setJourneyStopId(undefined);
    if (value === "all") { setFromDate(""); setToDate(""); }
    else if (value === "last-30-days") {
      const today = new Date();
      const start = new Date(today);
      start.setDate(start.getDate() - 29);
      const localDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      setFromDate(localDate(start)); setToDate(localDate(today));
    }
    else if (value === "this-year") { setFromDate(`${year}-01-01`); setToDate(`${year}-12-31`); }
    else if (value === "last-year") { setFromDate(`${year - 1}-01-01`); setToDate(`${year - 1}-12-31`); }
    else if (value.startsWith("year-")) {
      const selected = value.slice(5);
      setFromDate(`${selected}-01-01`);
      setToDate(`${selected}-12-31`);
    }
  };
  const dateLabel = describeMapDates(queryFrom, queryTo);
  const photoScope = journeyStop ? `Stopp ${journeyStop.number}` : regionName || (region ? "Gewählter Bereich" : "Sichtbarer Kartenausschnitt");
  const scopeLabels = [photoScope, dateLabel, selectedAlbum?.name, !isJourney && trip?.title].filter(Boolean);
  const resultsContext = journeyStop
    ? [journeyStopLabel(journeyStop), regionName, selectedAlbum?.name].filter(Boolean).join(" · ")
    : regionName || dateLabel || selectedAlbum ? scopeLabels.join(" · ") : "";
  const hasFilters = !!(region || queryFrom || queryTo || dateDraft || albumId || trip);
  const onlyDates = Boolean(dateLabel && !region && view.zoom === INITIAL_VIEW.zoom && !albumId);
  const selectRegion = (bounds: MapBounds | null, name = "") => {
    setRegion(bounds);
    setRegionName(name);
    setJourneyStopId(undefined);
    setStopGroup(undefined);
    setSelectMode(false);
  };
  const reset = () => {
    if (albumId) onAlbumChange?.(undefined);
    else checkpoint();
    openedAlbumRoute.current = undefined;
    setRouteNotice("");
    selectRegion(null);
    setPreset("all");
    setAlbumId("");
  };
  const clearArea = () => {
    selectRegion(null);
  };
  const showArea = () => { if (region) setView(viewForBounds(region)); };
  const changeTrip = (next?: TripSelection) => {
    if (next?.day && !selectedTripDays(next).includes(next.day)) next = { ...next, day: "" };
    if (next?.trip.id !== trip?.trip.id || next?.day !== trip?.day) checkpoint();
    clearDateDraft();
    setTrip(next);
    setJourneyStopId(undefined);
  };
  const openAlbumPicker = (opener: HTMLElement, chosen: GeoPhotoSelection, count: number, travel = false) => {
    albumPickerOpener.current = opener;
    setSaveSelection({ selection: chosen, count, travel });
  };
  const tripDays = journey?.stops.flatMap((stop) => [stop.start.slice(0, 10), stop.end.slice(0, 10)]).sort();
  const tripDates = trip ? describeMapDates(trip.trip.days[0], trip.trip.days[trip.trip.days.length - 1]) : "";
  const tripLabel = [trip?.title, tripDates].filter(Boolean).join(" · ");
  const changeMode = (next: typeof mode, currentArea = false) => {
    if (mode === next) return;
    checkpoint();
    if (currentArea) {
      if (!region) selectRegion(viewport);
      setIncludeInferred(false);
    }
    framedJourney.current = undefined;
    preserveRestoredView.current = false;
    setMode(next);
    setStopGroup(undefined);
    setSelectMode(false);
  };
  const showStopDay = (day: string) => {
    if (trip && selectedTripDays(trip).includes(day)) changeTrip({ ...trip, day });
    else {
      checkpoint();
      useDateRange(day, day);
    }
  };
  const chooseAlbum = (id: string) => {
    if (albumId === id) return;
    onAlbumChange?.(id || undefined);
    openedAlbumRoute.current = id || undefined;
    setRouteNotice("");
    setAlbumId(id);
    setTrip(undefined);
    setJourneyStopId(undefined);
  };
  const closeAlbumPicker = () => {
    setSaveSelection(undefined);
    requestAnimationFrame(() => albumPickerOpener.current?.focus());
  };
  const switchCompactPane = (pane: MapPane, fromMapSelection = false) => {
    if (pane === compactPane) return;
    if (pane === "map") {
      savedResultScroll.current = resultsRef.current?.scrollTop ?? 0;
    } else if (fromMapSelection) {
      restoreResultScroll.current = 0;
      focusResults.current = true;
    } else {
      restoreResultScroll.current ??= savedResultScroll.current;
    }
    setCompactPane(pane);
  };
  const selectJourneyStop = (id: string, opener?: HTMLButtonElement) => {
    const stop = journey?.stops.find((item) => item.id === id);
    if (!stop) return;
    stopOpener.current = opener;
    focusStopPhotos.current = true;
    setJourneyStopId(id);
    if (stop.longitude < viewport.west || stop.longitude > viewport.east ||
        stop.latitude < viewport.south || stop.latitude > viewport.north) {
      setView(normalizedView({ latitude: stop.latitude, longitude: stop.longitude, zoom: view.zoom }));
    }
    if (compactLayout) {
      switchCompactPane("results", true);
      focusResults.current = false;
    }
  };

  const showStopGroup = (ids: string[]) => {
    setJourneyStopId(undefined);
    setStopGroup(ids);
    if (compactLayout) switchCompactPane("results", true);
    else resultsRef.current?.focus();
  };

  const openPhotobook = (opener: HTMLButtonElement) => {
    albumOpenerRef.current = opener;
    const selectedDays = trip && !trip.day ? tripDays ?? selectedTripDays(trip) : undefined;
    const from = selectedDays?.[0] ?? queryFrom;
    const to = selectedDays?.[selectedDays.length - 1] ?? queryTo;
    setAlbum({ bounds: filterBounds, viewport, hasSelection: Boolean(region), fromDate: from, toDate: to, style: mapStyle,
      albumId: albumId || undefined, draft: selectedAlbum?.layout_draft, showCountryNames: showNames,
      scopeTitle: trip?.title || selectedAlbum?.name || regionName || undefined, journeyDetail, locationPhotoIds,
      journey: isJourney ? journey ?? undefined : undefined });
  };
  const openGallery = (opener: HTMLElement, index: number | null) => {
    galleryOpenerRef.current = opener;
    setGallery({ selection, index, items: images, total: imageTotal, cursor: nextCursor ?? null,
      inferredImageIds: journeyStop?.inferred_image_ids,
      label: scopeLabels.join(" · ") });
  };
  const openInImages = (chosen: GeoPhotoSelection) => {
    clearGallery();
    onOpenImages?.(chosen);
  };
  const handleMarkerClick = (marker: MapPoint) => {
    selectRegion({ west: marker.west, east: marker.east, south: marker.south, north: marker.north });
    if (view.zoom < 8) setView({ longitude: marker.longitude, latitude: marker.latitude, zoom: Math.min(8, view.zoom * 2) });
    if (compactLayout) switchCompactPane("results", true);
  };
  const closeGallery = () => {
    clearGallery();
    const opener = galleryOpenerRef.current;
    window.requestAnimationFrame(() => {
      if (opener?.isConnected && !opener.closest("[hidden]")) opener.focus();
      else if (showResultsPane) resultsRef.current?.focus();
      else resultsPaneButton.current?.focus();
    });
  };
  const closeAlbum = () => {
    setAlbum(null);
    window.requestAnimationFrame(() => {
      const opener = albumOpenerRef.current;
      if (opener?.isConnected && !opener.disabled && !opener.closest("[hidden]")) opener.focus({ preventScroll: true });
      else if (compactLayout) mapPaneButton.current?.focus({ preventScroll: true });
      else resultsRef.current?.focus({ preventScroll: true });
    });
  };
  const loadMore = async () => {
    if (moreController.current || !canLoadImages || !canLoadMore || !nextCursor) return;
    const controller = new AbortController();
    const loadedImages = images;
    moreController.current = controller;
    setMoreLoading(true);
    setMoreError("");
    try {
      const page = await fetchMapSelection(selection, PAGE_SIZE, nextCursor, controller.signal);
      if (controller.signal.aborted) return;
      const seen = new Set(loadedImages.map((image) => image.id));
      setImagesResult({
        key: filterKey, revision: libraryRevision,
        data: {
          items: loadedImages.concat(page.items.filter((image) => !seen.has(image.id))),
          total: page.total,
          nextCursor: page.next_cursor,
          extended: true,
        },
        error: "",
      });
    } catch (reason) {
      if (!controller.signal.aborted) setMoreError(reason instanceof Error ? reason.message : "Weitere Fotos konnten nicht geladen werden.");
    } finally {
      if (moreController.current === controller) {
        moreController.current = null;
        setMoreLoading(false);
      }
    }
  };

  return <div className="gps-page">
    <header className="gps-page__header">
      <h1>Fotokarte</h1>
      <div className="gps-map-options__modes" role="group" aria-label="Kartenmodus">
        <button type="button" aria-pressed={!isJourney} onClick={() => changeMode("locations")}>Standorte</button>
        <button type="button" aria-pressed={isJourney} onClick={() => changeMode("journey")}>Verlauf</button>
      </div>
      <button type="button" className="gps-page__photobook" disabled={!canLoad || Boolean(dateDraft) || (isJourney ? !journey?.stops.length : !imageTotal)}
        onClick={(event) => openPhotobook(event.currentTarget)}>Fotobuchbild gestalten</button>
    </header>

    {compactLayout && <div className="gps-page__compact-controls">
      <div className="gps-map-options__modes gps-page__pane-switch" role="group" aria-label="Kartenansicht">
        <button type="button" ref={mapPaneButton} aria-controls="gps-map-panel" aria-pressed={compactPane === "map"}
          onClick={() => switchCompactPane("map")}>Karte</button>
        <button type="button" ref={resultsPaneButton} aria-controls="gps-map-results" aria-pressed={compactPane === "results"}
          onClick={() => switchCompactPane("results")}>{isJourney ? "Stopps" : "Fotos"}</button>
      </div>
      <button type="button" ref={filtersButton} className="gps-page__filter-button" aria-expanded={filtersOpen}
        aria-controls="gps-map-filters" onClick={() => setFiltersOpen((open) => !open)}>Kartenfilter</button>
    </div>}

    <section className="gps-page__toolbar" id="gps-map-filters" aria-label="Kartenfilter" hidden={compactLayout && !filtersOpen}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !compactLayout) return;
        setFiltersOpen(false);
        filtersButton.current?.focus();
      }}>
      <label className="gps-page__field gps-page__field--region"><span>{isJourney ? "Verlaufsbereich" : "Kartenausschnitt"}</span><select aria-label={isJourney ? "Verlaufsbereich" : "Kartenausschnitt"} value={regionChoice} onChange={(event) => {
        const item = REGIONS.find((entry) => entry.label === event.target.value);
        if (!item) return;
        setView(item.view);
        if (isJourney) selectRegion(item.view === INITIAL_VIEW ? null : boundsForView(item.view), item.label);
      }}><option value="custom" disabled>Eigener Ausschnitt</option>{REGIONS.map((item) => <option key={item.label}>{item.label}</option>)}</select></label>
      <label className="gps-page__field gps-page__field--period"><span>Zeitraum</span><select ref={periodFilter} aria-label="Zeitraum" value={datePreset} onChange={(event) => setPreset(event.target.value)}>
        <option value="all">Alle Zeiten</option><option value="last-30-days">Letzte 30 Tage</option><option value="this-year">Dieses Jahr</option><option value="last-year">Letztes Jahr</option>
        {(availableYears.length > 0 || datePreset.startsWith("year-")) && <optgroup label="Aufnahmejahre">
          {datePreset.startsWith("year-") && !availableYears.includes(datePreset.slice(5)) && <option value={datePreset}>{datePreset.slice(5)}</option>}
          {availableYears.map((item) => <option key={item} value={`year-${item}`}>{item}</option>)}
        </optgroup>}
        <option value="custom">Eigener Zeitraum</option>
      </select></label>
      {datePreset === "custom" && ([["from", "Von"], ["to", "Bis"]] as const).map(([field, label]) => {
        const invalid = dateError?.field === field || dateError?.field === "both";
        return <label key={field} className="gps-page__field"><span>{label}</span>
          <input type="text" inputMode="numeric" autoComplete="off" maxLength={10} placeholder="TT.MM.JJJJ"
            value={shownDates[field]} aria-invalid={invalid} aria-describedby={`gps-date-help${invalid ? " gps-date-error" : ""}`}
            onChange={(event) => editDate(field, event.target.value)}
            onBlur={() => { if (dateDraft) commitDates(dateDraft); }}
            onKeyDown={(event) => { if (event.key === "Enter" && dateDraft) commitDates(dateDraft); }} />
        </label>;
      })}
      {(albums.length > 0 || albumId) && <label className="gps-page__field"><span>Album</span>
        <select aria-label="Album" value={albumId} onChange={(event) => chooseAlbum(event.target.value)}>
          <option value="">Alle Fotos</option>
          {albums.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>}
      {isJourney && <label className="gps-page__field gps-page__field--detail"><span>Stoppabstand</span><select aria-label="Stoppabstand" value={journeyDetail} onChange={(event) => {
        const detail = event.target.value === "city" ? "city" : "trip";
        checkpoint();
        preserveRestoredView.current = detail === "city" && Boolean(journeyStop);
        if (detail === "city" && journeyStop) {
          setView(normalizedView({ latitude: journeyStop.latitude, longitude: journeyStop.longitude, zoom: Math.max(1600, view.zoom) }));
        }
        setJourneyDetail(detail);
        setJourneyStopId(undefined);
      }}><option value="trip">Reiseüberblick</option><option value="city">Stadt und Spaziergang</option></select></label>}
      {hasFilters && <button type="button" className="gps-page__reset" onClick={() => {
        reset();
        periodFilter.current?.focus({ preventScroll: true });
      }}>Filter zurücksetzen</button>}
      {datePreset === "custom" && <p id="gps-date-help" className="gps-page__date-help">TT.MM.JJJJ, z. B. 05.07.2025. Beide Tage zählen.</p>}
    </section>
    {dateError && <p id="gps-date-error" className="gps-page__error" role="alert">{dateError.text}</p>}
    {routeNotice && <p className="gps-page__route-note" role="status">{routeNotice}</p>}
    {waitingForRouteAlbum && (albumsError
      ? <p className="gps-page__error" role="alert">{albumsError} <button type="button" onClick={reloadAlbums}>Erneut versuchen</button></p>
      : <p className="gps-page__route-note" role="status">Album wird geladen …</p>)}

    {(region || queryFrom || queryTo || selectedAlbum || trip) && <div className="gps-page__filters" role="group" aria-label="Aktive Filter">
      <span>Gefiltert nach</span>
      {region && <button type="button" onClick={clearArea} aria-label="Bereichsfilter entfernen">{regionName || "Gewählter Bereich"} <span aria-hidden="true">×</span></button>}
      {(queryFrom || queryTo) && <button type="button" onClick={() => trip?.day ? changeTrip({ ...trip, day: "" }) : setPreset("all")} aria-label="Zeitfilter entfernen">{dateLabel} <span aria-hidden="true">×</span></button>}
      {trip && <button type="button" onClick={() => changeTrip(undefined)} aria-label="Reisefilter entfernen">Reise {tripLabel} <span aria-hidden="true">×</span></button>}
      {selectedAlbum && <button type="button" onClick={() => chooseAlbum("")} aria-label="Albumfilter entfernen">{selectedAlbum.name} <span aria-hidden="true">×</span></button>}
    </div>}
    <div className="gps-page__discovery">
      <MapObservedPlaces key={placeLibraryEpoch} active={active} revision={libraryRevision}
        from={fromDate} to={toDate} albumId={albumId}
        onSelect={(place, period) => {
          checkpoint();
          selectRegion(place.bounds, place.name);
          setTrip(undefined);
          setView(viewForBounds(place.bounds));
          if (period) useDateRange(period.from, period.to);
          if (compactLayout && isJourney) switchCompactPane("map");
        }}
        onSetHome={isJourney ? setHome : undefined} />
      {isJourney && canShowResults && !journeyState?.error && <MapTrips journey={tripsSource} home={home} onClearHome={() => setHome(undefined)}
        selection={trip} canSave={Boolean(journeySelection)} onChange={changeTrip} onSave={(opener) => {
          if (journeySelection && journey) openAlbumPicker(opener, journeySelection, journey.photo_count, true);
        }} />}
    </div>
    <div className="gps-page__layout">
      <section ref={mapPanelRef} className="gps-page__map-card" id="gps-map-panel" aria-label="Fotostandorte auf der Weltkarte" hidden={compactLayout && showResultsPane}>
        <div className="gps-page__map-head">
          <div>
            <strong>{isJourney ? "Standortverlauf" : "Fotostandorte"}</strong>
            {(!compactLayout || !showResultsPane) && <span aria-live="polite">{isJourney
              ? journey ? `${countWithLabel(journey.stops.length, "Stopp", "Stopps")} · ${countWithLabel(journey.photo_count, "Foto", "Fotos")}` : "–"
              : pointsLoading ? "Karte wird aktualisiert …" : points ? `${countWithLabel(points.total, "Foto", "Fotos")} ${pointSelection ? "aus der Auswahl im Ausschnitt" : "im sichtbaren Ausschnitt"}` : "–"}</span>}
          </div>
          <div className="gps-page__map-actions">
            <button type="button" className={selectMode ? "gps-page__tool--active" : ""} aria-pressed={selectMode} onClick={() => setSelectMode((value) => !value)}>{selectMode ? "Auswahl beenden" : "Bereich wählen"}</button>
            {selectMode && <button type="button" onClick={() => selectRegion(viewport)}>Sichtbaren Bereich wählen</button>}
            {region && !isJourney && <button type="button" onClick={() => selectRegion(null)}>Auswahl löschen</button>}
            {!areaInView && !selectMode && <button type="button" onClick={showArea}>Filterbereich zeigen</button>}
            {isJourney && Boolean(journey?.stops.length) && <button type="button" onClick={() => {
              if (journey) setView(journeyView(journey, journeyDetail));
            }}>Route einpassen</button>}
            {!isJourney && !compactLayout && view.zoom > 1 && <button type="button" onClick={() => setView(INITIAL_VIEW)}>Ganze Karte zeigen</button>}
            {!compactLayout && <button type="button" className="gps-page__skip" onClick={() => resultsRef.current?.focus()}>{isJourney ? "Zu den Stopps" : "Zu den Fotos"}</button>}
          </div>
        </div>
        {pointResult?.error && <div className="gps-page__error" role="alert">{pointResult.error} <button type="button" onClick={() => { setPointRefresh((value) => value + 1); }}>Erneut versuchen</button></div>}
        <WorldMap
          view={view}
          onViewChange={setView}
          points={points?.points ?? EMPTY_POINTS}
          grid={points ? { columns: points.grid_columns, rows: points.grid_rows } : null}
          onGridChange={handleGridChange}
          selection={region}
          style={mapStyle}
          showCountryNames={showNames}
          compactControls={compactLayout}
          onlineDetails={onlineDetails && active && (!compactLayout || compactPane === "map")}
          journeyStops={journey?.stops}
          journeyConnections={journey?.connections}
          activeJourneyStop={journeyStopId}
          onJourneyStopClick={selectJourneyStop}
          onJourneyGroupClick={showStopGroup}
          onSelectionChange={selectRegion}
          onCancelSelection={() => setSelectMode(false)}
          onMarkerClick={handleMarkerClick}
          selectionMode={selectMode}
        />
        <div className="gps-page__map-foot">
          <span>{selectMode ? "Bereich mit Maus oder einem Finger aufziehen" : isJourney ? "Stopp oder Gruppe auf der Karte wählen" : "Ort auf der Karte wählen, um Fotos zu sehen"}</span>
          <span className="gps-page__touch-hint">Mit zwei Fingern zoomen</span>
          {mapStyle === "satellite" ? <a className="gps-map__credit" href="https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/" target="_blank" rel="noreferrer">NASA Earth Observatory · Natural Earth</a>
            : <a className="gps-map__credit" href="https://www.naturalearthdata.com/" target="_blank" rel="noreferrer">Kartendaten: Natural Earth</a>}
          {region && <span>Fester Bereichsfilter</span>}
        </div>
        <div className="gps-page__online-details">
          <label><input type="checkbox" checked={onlineDetails} aria-describedby="gps-online-details-note"
            onChange={(event) => setOnlineDetails(event.target.checked)} />Kartendetails online</label>
          <span id="gps-online-details-note">Fragt beim Zoomen den sichtbaren Bereich bei Kartenanbietern ab.</span>
        </div>
        <div className="gps-page__map-settings">
          <details className="gps-map-discovery">
            <summary>Kartendarstellung</summary>
            <div className="gps-map-discovery__content gps-map-options">
              <label>Kartenstil<select aria-label="Kartenstil" value={mapStyle} onChange={(event) => {
                const style = event.target.value === "landscape" || event.target.value === "satellite" ? event.target.value : "standard";
                setPreference("map_style", style);
              }}><option value="standard">Standard</option><option value="landscape">Landschaft</option><option value="satellite">Satellit</option></select></label>
              <label><input type="checkbox" checked={showNames} onChange={(event) => {
                setPreference("map_country_names", event.target.checked);
              }} />Ländernamen anzeigen</label>
              <label>Land finden<select aria-label="Land finden" value="" onChange={(event) => {
                const country = countries.find((item) => item.code === event.target.value);
                if (country) setView(normalizedView(country.focus));
              }}><option value="">Land auswählen</option>{countries.map((country) =>
                <option key={country.code} value={country.code}>{country.name}</option>)}</select></label>
            </div>
          </details>
        </div>
      </section>
      <section ref={resultsRef} id="gps-map-results" tabIndex={-1} className="gps-page__results" aria-label={isJourney ? "Standortverlauf und Fotos" : "Fotos im gewählten Kartenausschnitt"} aria-busy={imagesLoading} hidden={!showResultsPane}>
        {isJourney && <>
          {!journeyStop && <h2>Chronologische Stopps</h2>}
          {compactLayout && showResultsPane && journey && <p className="gps-page__results-hint" role="status">
            {countWithLabel(journey.stops.length, "Stopp", "Stopps")} · {countWithLabel(journey.photo_count, "Foto", "Fotos")}
          </p>}
          {selectedAlbum?.journey && <p className="gps-page__results-hint">Reisealbum
            {selectedAlbum.journey.arrival_included && " · Anreise enthalten"}
            {selectedAlbum.journey.departure_included && " · Rückreise enthalten"}</p>}
          {canLoad && !journeyState && <p role="status">Standortverlauf wird geladen …</p>}
          {journeyState?.error && <p className="gps-page__error" role="alert">{journeyState.error}{" "}
            <button type="button" onClick={() => { setJourneyResult(null); setJourneyRetry((value) => value + 1); }}>Erneut versuchen</button></p>}
          {Boolean(baseJourney?.inferred_location_count) && <div className="gps-journey-inference">
            <label><input type="checkbox" checked={includeInferred} onChange={(event) => setIncludeInferred(event.target.checked)}
              aria-describedby="gps-journey-inference-note" />Passende Fotos ohne Standort einbeziehen</label>
            <p id="gps-journey-inference-note">Ein Foto ohne Standort kann zu einem Stopp passen, wenn es kurz zwischen zwei Fotos von dort aufgenommen wurde.</p>
          </div>}
          {journeySelection && <div className="gps-results-actions">
            {onOpenImages && <button type="button" onClick={() => openInImages(journeySelection)}>Verlauf in Bilder öffnen</button>}
            {!trip && journey && <button type="button" onClick={(event) => openAlbumPicker(event.currentTarget, journeySelection,
              journey.photo_count, true)}>Als Reisealbum speichern</button>}
          </div>}
        </>}
        {(!isJourney || journeyStop) && <>
        <div className="gps-page__results-head"><div><h2 ref={journeyStop ? stopPhotosRef : undefined} tabIndex={journeyStop ? -1 : undefined}>{journeyStop ? `Fotos von Stopp ${journeyStop.number}` : region ? "Fotos im gewählten Bereich" : "Fotos im Kartenausschnitt"}</h2></div><span role="status">{imagesLoading ? "Fotos werden geladen …" : imageResult?.data ? countWithLabel(imageTotal, "Foto", "Fotos") : "–"}</span></div>
        {resultsContext && <p className="gps-page__results-hint">{resultsContext}</p>}
        {journeyStop && <div className="gps-results-actions">
          <button type="button" onClick={() => {
            setJourneyStopId(undefined);
            requestAnimationFrame(() => {
              const opener = stopOpener.current;
              if (opener?.isConnected && !opener.closest("[hidden]")) opener.focus();
              else resultsRef.current?.focus();
            });
          }}>Zur Stoppliste</button>
          <button type="button" onClick={() => {
            setView(normalizedView({ latitude: journeyStop.latitude, longitude: journeyStop.longitude,
              zoom: Math.max(view.zoom, journeyDetail === "city" ? 6400 : 3200) }));
            if (compactLayout) switchCompactPane("map");
          }}>Stopp auf Karte zeigen</button>
        </div>}
        {images.length > 0 && <div className="gps-results-actions">
          {!isJourney && region && <button type="button" onClick={() => {
            const frame = viewForBounds(region);
            setView(normalizedView({ ...frame, zoom: Math.min(frame.zoom, 12000) }));
            if (compactLayout) switchCompactPane("map");
          }}>Fotoorte auf Karte zeigen</button>}
          {!isJourney && <button type="button" onClick={() => changeMode("journey", true)}>Verlauf dieser Auswahl</button>}
          {stopDay && (queryFrom !== stopDay || queryTo !== stopDay) &&
            <button type="button" onClick={() => showStopDay(stopDay)}>{shortDate.format(new Date(stopDay))} zeigen</button>}
          <button type="button" onClick={(event) => openGallery(event.currentTarget, null)}>Galerie öffnen</button>
          {onOpenImages && <button type="button" onClick={() => openInImages(selection)}>In Bilder öffnen</button>}
          <button type="button" onClick={(event) => openAlbumPicker(event.currentTarget, selection, imageTotal)}>Zum Album hinzufügen</button>
        </div>}
        {imageResult?.error && <div className="gps-page__error" role="alert">{imageResult.error} <button type="button" onClick={() => { setImageRefresh((value) => value + 1); }}>Erneut versuchen</button></div>}
        {imageResult?.data && !imageResult.error && imageTotal === 0 && <div className="gps-page__empty">
          <strong>{points?.indexing ? "Deine Fotokarte entsteht gerade." : collection?.located_total === 0 ? "Noch keine Fotos mit Standort."
            : onlyDates ? "Keine Fotos mit Standort in diesem Zeitraum." : "Hier sind noch keine Fotos."}</strong>
          <p>{points?.indexing ? "Fotos mit Standort erscheinen automatisch, sobald sie gefunden werden."
            : collection?.located_total === 0 ? "Sobald Fotos mit gespeicherten Standortdaten hinzugefügt werden, findest du sie hier."
              : onlyDates ? "Wähle andere Aufnahmetage oder zeige alle Zeiten." : "Wähle einen anderen Kartenausschnitt oder entferne den Zeitfilter."}</p>
          {onlyDates ? <button type="button" onClick={() => setPreset("all")}>Alle Zeiten zeigen</button>
            : hasFilters && <button type="button" onClick={reset}>Alle Standorte anzeigen</button>}
        </div>}
        {imagesLoading && <div className="gps-page__skeletons" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <div key={index} />)}</div>}
        <div className="gps-page__grid">{images.map((image, index) =>
          <MapPhotoTile key={image.id} image={image} inferred={inferredIds?.has(image.id)}
            onOpen={(event) => openGallery(event.currentTarget, index)} />)}</div>
        {moreError && <p className="gps-page__error" role="alert">{moreError}</p>}
        {nextCursor && <button type="button" className="gps-page__more" disabled={moreLoading || !canLoadMore} onClick={() => void loadMore()}>{moreLoading ? "Weitere Fotos werden geladen …" : moreError ? "Weitere Fotos erneut laden" : "Weitere Fotos laden"}</button>}
        </>}
        {isJourney && journey && <div hidden={Boolean(journeyStop)}>
          <MapJourney key={journeyFrameKey} journey={journey} onSelect={selectJourneyStop}
            stopGroup={stopGroup} onClearGroup={() => setStopGroup(undefined)}
            areaFiltered={Boolean(region)} onClearArea={clearArea}
            onClearDates={fromDate || toDate ? () => setPreset("all") : undefined} />
        </div>}
      </section>
    </div>
    <aside className="gps-page__collection" aria-label="Standortübersicht">
      {!isJourney && <>
      <span>{collection ? countWithLabel(collection.located_total, "Foto mit Standort", "Fotos mit Standort") : "… Fotos mit Standort"}</span>
      <span>{collection ? countWithLabel(Math.max(0, collection.library_total - collection.located_total - collection.pending_total), "Foto ohne Standort", "Fotos ohne Standort") : "… Fotos ohne Standort"}</span>
      {!!collection?.pending_total && <span className="gps-page__pending">{formatCount(collection.pending_total)} werden noch geprüft</span>}
      </>}
      <span className="gps-page__privacy">Kartenübersicht und Fotobuch-Export funktionieren offline.</span>
    </aside>
    {openedGallery && <MapGallery opening={openedGallery} onClose={closeGallery}
      onOpenImages={onOpenImages ? openInImages : undefined} onNavigateToCluster={onNavigateToCluster}
      onAddToAlbum={openAlbumPicker} />}
    {active && album && <MapAlbumDialog opening={album} onClose={closeAlbum}
      onSaveDraft={album.albumId ? async (draft) => { await updatePhotoAlbum(album.albumId!, { layout_draft: { ...draft } }); } : undefined} />}
    {active && saveSelection && <AlbumPickerDialog
      selection={saveSelection.selection.bounds || !saveSelection.selection.image_ids ? saveSelection.selection : undefined}
      imageIds={saveSelection.selection.bounds ? undefined : saveSelection.selection.image_ids} photoCount={saveSelection.count}
      initialName={saveSelection.travel ? trip?.title || `Reise ${tripDays?.[0] || queryFrom}`.trim() : undefined}
      journey={saveSelection.travel ? { detail: journeyDetail, from_date: tripDays?.[0],
        to_date: tripDays?.[tripDays.length - 1], arrival_included: trip?.includeArrival,
        departure_included: trip?.includeDeparture } : undefined}
      onClose={closeAlbumPicker} />}
  </div>;
};

export default MapPage;
