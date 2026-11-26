import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchMapPoints, imageThumbnailUrl,
  type MapImage, type MapImageCursor, type MapPoint, type MapPointsResponse,
} from "../../utils/api";
import WorldMap, { boundsForView, countryLabels, normalizedView, type MapBounds, type MapGrid, type MapStyle, type MapView } from "./WorldMap";
import MapGallery, { type MapGalleryOpening } from "./MapGallery";
import MapAlbumDialog, { type MapAlbumOpening } from "./MapAlbumDialog";
import MapJourney from "./MapJourney";
import MapTrips from "./MapTrips";
import MapObservedPlaces from "./MapObservedPlaces";
import AlbumPickerDialog from "../shared/AlbumPickerDialog";
import { updatePhotoAlbum, usePhotoAlbums } from "../../utils/photoAlbums";
import { applyTripSelection, photoIdsForTrip, type TravelHome, type TripSelection } from "./journeyTrips";
import { fitAlbumJourney } from "./MapAlbumArtwork";
import { fetchJourney, fetchMapSelection, type Journey, type JourneyDetail } from "./mapData";
import type { GeoPhotoSelection } from "../../utils/geoPhotoSelection";
import { subscribeToTopic } from "../../utils/events";
import "./map.css";
import "./cartography.css";
import "./map-workspace.css";

const PAGE_SIZE = 36;
const MAP_LIBRARY_EVENTS = new Set([
  "database_import", "delete_image", "image_path_cleanup", "image_rename", "import_completed", "albums_changed",
]);
const EMPTY_POINTS: MapPoint[] = [];
const EMPTY_IMAGES: MapImage[] = [];
type MapResult<T> = { key: string; data: T | null; error: string };
type ImagePage = {
  items: MapImage[];
  total: number;
  nextCursor: MapImageCursor | null;
  extended: boolean;
};
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
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
  onOpenImages?: (selection: GeoPhotoSelection) => void;
}

const formatter = new Intl.NumberFormat("de-DE");
const formatCount = (count: number) => formatter.format(count);
const shortDate = new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short", timeZone: "UTC" });
const year = new Date().getFullYear();

const countries = [...countryLabels].sort((a, b) => a.name.localeCompare(b.name, "de"));
const MAP_STYLE_KEY = "face-manager:map-style";
const MAP_NAMES_KEY = "face-manager:map-country-names";

const MapPage: React.FC<Props> = ({ active, openAlbumId, onNavigateToCluster, onOpenImages }) => {
  const [view, setView] = useState<MapView>(INITIAL_VIEW);
  const [mode, setMode] = useState<"locations" | "journey">("locations");
  const [mapStyle, setMapStyle] = useState<MapStyle>(() => {
    const value = localStorage.getItem(MAP_STYLE_KEY);
    return value === "landscape" || value === "satellite" ? value : "standard";
  });
  const [showNames, setShowNames] = useState(() => localStorage.getItem(MAP_NAMES_KEY) === "true");
  const [journeyResult, setJourneyResult] = useState<MapResult<Journey> | null>(null);
  const [journeyStopId, setJourneyStopId] = useState<string | undefined>();
  const [journeyRetry, setJourneyRetry] = useState(0);
  const [journeyDetail, setJourneyDetail] = useState<JourneyDetail>("trip");
  const isJourney = mode === "journey";
  const [pointGrid, setPointGrid] = useState<MapGrid | null>(null);
  const [region, setRegion] = useState<MapBounds | null>(null);
  const [regionName, setRegionName] = useState("");
  const [selectMode, setSelectMode] = useState(false);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [datePreset, setDatePreset] = useState("all");
  const [pointsResult, setPointsResult] = useState<MapResult<MapPointsResponse> | null>(null);
  const [imagesResult, setImagesResult] = useState<MapResult<ImagePage> | null>(null);
  const [moreLoading, setMoreLoading] = useState(false);
  const [moreError, setMoreError] = useState("");
  const resultsRef = useRef<HTMLElement>(null);
  const moreController = useRef<AbortController | null>(null);
  const galleryOpenerRef = useRef<HTMLElement | null>(null);
  const albumOpenerRef = useRef<HTMLButtonElement | null>(null);
  const [gallery, setGallery] = useState<MapGalleryOpening | null>(null);
  const [album, setAlbum] = useState<MapAlbumOpening | null>(null);
  const [pointRefresh, setPointRefresh] = useState(0);
  const [imageRefresh, setImageRefresh] = useState(0);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [albumId, setAlbumId] = useState("");
  const { albums } = usePhotoAlbums(active);
  const selectedAlbum = albums.find((item) => item.id === albumId);
  const [home, setHome] = useState<TravelHome>();
  const [trip, setTrip] = useState<TripSelection>();
  const [saveSelection, setSaveSelection] = useState<{ selection: GeoPhotoSelection; count: number; travel: boolean }>();
  const albumPickerOpener = useRef<HTMLElement | null>(null);
  const openedAlbumRoute = useRef<string>();

  const invalidDate = !!(fromDate && toDate && fromDate > toDate);
  const canLoad = active && !invalidDate;
  const canLoadPoints = canLoad && !isJourney && pointGrid !== null;
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
  const queryFrom = isJourney && trip?.day ? trip.day : fromDate;
  const queryTo = isJourney && trip?.day ? trip.day : toDate;
  const dates = { from_date: queryFrom || undefined, to_date: queryTo || undefined, album_id: albumId || undefined };
  const journeyKey = JSON.stringify([queryFrom, queryTo, region, journeyDetail, albumId, libraryRevision]);
  const journeyState = isJourney && canLoad && journeyResult?.key === journeyKey ? journeyResult : null;
  const baseJourney = journeyState?.data;
  const tripsSource = baseJourney ?? (trip ? journeyResult?.data : null);
  const journey = useMemo(() => baseJourney && trip ? applyTripSelection(baseJourney, photoIdsForTrip(trip)) : baseJourney, [baseJourney, trip]);
  const journeyStop = journey?.stops.find((stop) => stop.id === journeyStopId);
  const stopDay = journeyStop?.start.slice(0, 10);
  const canLoadImages = canLoad && (!isJourney || Boolean(journeyStop));
  const selection: GeoPhotoSelection = isJourney && journeyStop
    ? { ...dates, image_ids: journeyStop.image_ids }
    : { ...dates, bounds: filterBounds };
  const stopMembership = useMemo(() => journeyStop?.image_ids.join(","), [journeyStop]);
  const journeySelection: GeoPhotoSelection | undefined = journey?.photo_count
    ? { ...dates, image_ids: journey.stops.flatMap((stop) => stop.image_ids) }
    : undefined;
  const pointQuery = {
    ...viewport, ...dates,
    grid_columns: pointGrid?.columns,
    grid_rows: pointGrid?.rows,
  };
  const filterKey = JSON.stringify(isJourney ? ["journey", journeyKey, journeyStopId, stopMembership] : selection);
  const pointKey = JSON.stringify([pointQuery, libraryRevision]);
  const imageKey = JSON.stringify([filterKey, libraryRevision]);
  const pointResult = canLoadPoints && pointsResult?.key === pointKey ? pointsResult : null;
  const imageResult = canLoadImages && imagesResult?.key === imageKey ? imagesResult : null;
  const points = pointResult?.error ? null : pointResult?.data;
  const collection = pointsResult?.data;
  const images = imageResult?.data?.items ?? EMPTY_IMAGES;
  const imageTotal = imageResult?.data?.total ?? 0;
  const nextCursor = imageResult?.data?.nextCursor;
  const hasExtendedResults = imageResult?.data?.extended ?? false;
  const pointsLoading = canLoadPoints && !pointResult;
  const imagesLoading = canLoadImages && !imageResult;
  const openedGallery = active ? gallery : null;

  const clearGallery = useCallback(() => setGallery(null), []);
  useEffect(() => {
    if (!active) { clearGallery(); setAlbum(null); setSaveSelection(undefined); }
  }, [active, clearGallery]);

  useEffect(() => {
    if (!openAlbumId) { openedAlbumRoute.current = undefined; return; }
    const target = albums.find((item) => item.id === openAlbumId);
    if (!target || openedAlbumRoute.current === openAlbumId) return;
    openedAlbumRoute.current = openAlbumId;
    setAlbumId(openAlbumId);
    setRegion(null);
    setTrip(undefined);
    setJourneyStopId(undefined);
    setView(INITIAL_VIEW);
    setMode(target.journey ? "journey" : "locations");
    setJourneyDetail(target.journey?.detail ?? "trip");
    setFromDate(target.journey?.from_date ?? "");
    setToDate(target.journey?.to_date ?? "");
    setDatePreset(target.journey?.from_date || target.journey?.to_date ? "custom" : "all");
  }, [openAlbumId, albums]);

  useEffect(() => {
    if (!canLoad || !isJourney) return;
    const controller = new AbortController();
    void fetchJourney(queryFrom, queryTo, region, journeyDetail, controller.signal, albumId).then((data) => {
      if (!controller.signal.aborted) setJourneyResult({ key: journeyKey, data, error: "" });
    }).catch((error) => {
      if (!controller.signal.aborted) setJourneyResult({
        key: journeyKey, data: null,
        error: error instanceof Error ? error.message : "Der Standortverlauf konnte nicht geladen werden.",
      });
    });
    return () => controller.abort();
  }, [canLoad, isJourney, queryFrom, queryTo, region, journeyDetail, journeyKey, journeyRetry, albumId]);

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
        setAlbum(null);
        setSaveSelection(undefined);
        setAlbumId("");
        setTrip(undefined);
        setHome(undefined);
        setJourneyStopId(undefined);
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
      void fetchMapPoints(pointQuery, controller.signal)
        .then((data) => { if (!controller.signal.aborted) setPointsResult({ key: pointKey, data, error: "" }); })
        .catch((reason) => {
          if (!controller.signal.aborted) setPointsResult((current) => ({
            key: pointKey, data: current?.data ?? null,
            error: reason instanceof Error ? reason.message : "Standorte konnten nicht geladen werden.",
          }));
        });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
    // The key encodes every query value. The debounce only starts for a changed query.
  }, [canLoadPoints, pointKey, pointRefresh]);

  useEffect(() => {
    resultsRef.current?.scrollTo({ top: 0 });
  }, [canLoadImages, filterKey]);

  useEffect(() => {
    setMoreError("");
    return () => { moreController.current?.abort(); };
  }, [canLoadImages, imageKey]);

  useEffect(() => {
    if (!canLoadImages) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetchMapSelection(selection, PAGE_SIZE, null, controller.signal)
        .then((page) => {
          if (controller.signal.aborted || moreController.current) return;
          setImagesResult((current) => {
            // Keep the cursor and already loaded pages together during indexing.
            if (current?.key === imageKey && current.data?.extended) return current;
            return {
              key: imageKey,
              data: { items: page.items, total: page.total, nextCursor: page.next_cursor, extended: false },
              error: "",
            };
          });
        })
        .catch((reason) => {
          if (!controller.signal.aborted && !moreController.current) setImagesResult((current) => ({
            key: imageKey, data: current?.key === imageKey ? current.data : null,
            error: reason instanceof Error ? reason.message : "Fotos konnten nicht geladen werden.",
          }));
        });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [canLoadImages, imageKey, imageRefresh]);

  useEffect(() => {
    if (!canLoad || !points?.indexing || openedGallery || moreLoading) return;
    const timer = window.setTimeout(() => {
      setPointRefresh((value) => value + 1);
      if (!hasExtendedResults) setImageRefresh((value) => value + 1);
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [canLoad, pointKey, filterKey, pointRefresh, points?.indexing, openedGallery, moreLoading, hasExtendedResults]);

  const setPreset = (value: string) => {
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
  };
  const dateLabel = [queryFrom, queryTo].map((date) => date ? new Date(`${date}T12:00:00`).toLocaleDateString("de-DE") : "offen").join(" – ");
  const hasFilters = !!(region || queryFrom || queryTo || view.zoom > 1 || albumId);
  const selectRegion = (bounds: MapBounds | null, name = "") => {
    setRegion(bounds);
    setRegionName(name);
    setJourneyStopId(undefined);
    setSelectMode(false);
  };
  const reset = () => { selectRegion(null); setView(INITIAL_VIEW); setPreset("all"); setJourneyDetail("trip"); setAlbumId(""); };
  const clearArea = () => {
    selectRegion(null);
    if (isJourney) setView(INITIAL_VIEW);
  };
  const frameBounds = (bounds: MapBounds) => {
    if (bounds.west < -180 || bounds.east > 180 || bounds.west > bounds.east) { setView(INITIAL_VIEW); return; }
    setView(normalizedView({
      longitude: (bounds.west + bounds.east) / 2,
      latitude: (bounds.south + bounds.north) / 2,
      zoom: Math.min(360 / (bounds.east - bounds.west), 180 / (bounds.north - bounds.south)) * .94,
    }));
  };
  const showArea = () => { if (region) frameBounds(region); };
  const changeTrip = (next?: TripSelection) => {
    setTrip(next);
    setJourneyStopId(undefined);
    if (!next) return;
    const selected = [
      ...(next.includeArrival && next.trip.arrival ? [next.trip.arrival] : []),
      ...next.trip.core,
      ...(next.includeDeparture && next.trip.departure ? [next.trip.departure] : []),
    ];
    const stops = next.day ? selected.filter((stop) => {
      const [start, end] = [stop.start.slice(0, 10), stop.end.slice(0, 10)].sort();
      return start <= next.day && end >= next.day;
    }) : selected;
    if (stops.length) frameBounds(fitAlbumJourney(stops, [], .012));
  };
  const openAlbumPicker = (opener: HTMLElement, chosen: GeoPhotoSelection, count: number, travel = false) => {
    albumPickerOpener.current = opener;
    setSaveSelection({ selection: chosen, count, travel });
  };
  const tripDays = journey?.stops.flatMap((stop) => [stop.start.slice(0, 10), stop.end.slice(0, 10)]).sort();
  const showStopDay = (day: string) => {
    if (trip) { changeTrip({ ...trip, day }); return; }
    setFromDate(day);
    setToDate(day);
    setDatePreset("custom");
    setJourneyStopId(undefined);
  };
  const chooseAlbum = (id: string) => {
    setAlbumId(id);
    setTrip(undefined);
    setJourneyStopId(undefined);
  };
  const closeAlbumPicker = () => {
    setSaveSelection(undefined);
    requestAnimationFrame(() => albumPickerOpener.current?.focus());
  };
  const selectJourneyStop = (id: string) => {
    const stop = journey?.stops.find((item) => item.id === id);
    if (!stop) return;
    setJourneyStopId(id);
    setView(normalizedView({
      latitude: stop.latitude, longitude: stop.longitude, zoom: Math.max(journeyDetail === "city" ? 1600 : 5, view.zoom),
    }));
  };
  const openGallery = (opener: HTMLElement, index: number | null) => {
    galleryOpenerRef.current = opener;
    const area = journeyStop ? `Stopp ${journeyStop.number}` : region ? "Gewählter Bereich" : "Kartenausschnitt";
    setGallery({ selection, index, items: images, total: imageTotal, cursor: nextCursor ?? null,
      label: fromDate || toDate ? `${area} · ${dateLabel}` : area });
  };
  const openInImages = (chosen: GeoPhotoSelection) => {
    clearGallery();
    onOpenImages?.(chosen);
  };
  const handleMarkerClick = (marker: MapPoint) => {
    selectRegion({ west: marker.west, east: marker.east, south: marker.south, north: marker.north });
    if (view.zoom < 8) setView({ longitude: marker.longitude, latitude: marker.latitude, zoom: Math.min(8, view.zoom * 2) });
  };
  const closeGallery = () => {
    clearGallery();
    const opener = galleryOpenerRef.current;
    window.requestAnimationFrame(() => {
      if (opener?.isConnected) opener.focus();
      else resultsRef.current?.focus();
    });
  };
  const closeAlbum = () => {
    setAlbum(null);
    window.requestAnimationFrame(() => {
      const opener = albumOpenerRef.current;
      if (opener?.isConnected && !opener.disabled) opener.focus({ preventScroll: true });
      else resultsRef.current?.focus({ preventScroll: true });
    });
  };
  const loadMore = async () => {
    if (moreController.current || !canLoadImages || !nextCursor) return;
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
        key: imageKey,
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

  return <main className="gps-page">
    <header className="gps-page__header">
      <div><span className="gps-page__eyebrow">FOTOBIBLIOTHEK · STANDORTE</span><h1>Fotokarte</h1><p>Entdecke deine Fotos dort, wo sie aufgenommen wurden.</p></div>
      {!isJourney && <div className="gps-page__stat"><strong>{collection ? formatCount(collection.located_total) : "…"}</strong><span>Fotos mit Standort</span></div>}
    </header>

    <section className="gps-page__toolbar" aria-label="Kartenfilter">
      <label className="gps-page__field gps-page__field--region"><span>{isJourney ? "Verlaufsbereich" : "Kartenausschnitt"}</span><select aria-label={isJourney ? "Verlaufsbereich" : "Kartenausschnitt"} value={regionChoice} onChange={(event) => {
        const item = REGIONS.find((entry) => entry.label === event.target.value);
        if (!item) return;
        setView(item.view);
        if (isJourney) selectRegion(item.view === INITIAL_VIEW ? null : boundsForView(item.view), item.label);
      }}><option value="custom" disabled>Eigener Ausschnitt</option>{REGIONS.map((item) => <option key={item.label}>{item.label}</option>)}</select></label>
      <label className="gps-page__field"><span>Zeitraum</span><select aria-label="Zeitraum" value={datePreset} onChange={(event) => setPreset(event.target.value)}><option value="all">Alle Zeiten</option><option value="last-30-days">Letzte 30 Tage</option><option value="this-year">Dieses Jahr</option><option value="last-year">Letztes Jahr</option><option value="custom">Eigener Zeitraum</option></select></label>
      <label className="gps-page__field"><span>Von</span><input type="date" aria-invalid={invalidDate} aria-describedby={invalidDate ? "gps-date-error" : undefined} value={queryFrom} onChange={(event) => { setTrip(undefined); setFromDate(event.target.value); setDatePreset("custom"); setJourneyStopId(undefined); }} /></label>
      <label className="gps-page__field"><span>Bis</span><input type="date" aria-invalid={invalidDate} aria-describedby={invalidDate ? "gps-date-error" : undefined} value={queryTo} onChange={(event) => { setTrip(undefined); setToDate(event.target.value); setDatePreset("custom"); setJourneyStopId(undefined); }} /></label>
      {(albums.length > 0 || albumId) && <label className="gps-page__field"><span>Album</span>
        <select aria-label="Album" value={albumId} onChange={(event) => chooseAlbum(event.target.value)}>
          <option value="">Alle Fotos</option>
          {albums.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>}
      <button type="button" className="gps-page__reset" onClick={reset}>Zurücksetzen</button>
    </section>
    <div className="gps-map-options" aria-label="Kartendarstellung">
      <div className="gps-map-options__modes" role="group" aria-label="Kartenmodus">
        <button type="button" aria-pressed={!isJourney} onClick={() => setMode("locations")}>Standorte</button>
        <button type="button" aria-pressed={isJourney} onClick={() => { setMode("journey"); setSelectMode(false); }}>Verlauf</button>
      </div>
      {isJourney && <label>Stoppabstand<select aria-label="Stoppabstand" value={journeyDetail} onChange={(event) => {
        const detail = event.target.value === "city" ? "city" : "trip";
        if (detail === "city" && journeyStop) {
          setView(normalizedView({ latitude: journeyStop.latitude, longitude: journeyStop.longitude, zoom: Math.max(1600, view.zoom) }));
        }
        setJourneyDetail(detail);
        setJourneyStopId(undefined);
      }}><option value="trip">Reiseüberblick</option><option value="city">Stadt und Spaziergang</option></select></label>}
      <label>Kartenstil<select value={mapStyle} onChange={(event) => {
        const style = event.target.value === "landscape" || event.target.value === "satellite" ? event.target.value : "standard";
        setMapStyle(style); localStorage.setItem(MAP_STYLE_KEY, style);
      }}><option value="standard">Standard</option><option value="landscape">Landschaft</option><option value="satellite">Satellit</option></select></label>
      <label><input type="checkbox" checked={showNames} onChange={(event) => {
        setShowNames(event.target.checked); localStorage.setItem(MAP_NAMES_KEY, String(event.target.checked));
      }} />Ländernamen anzeigen</label>
      <label>Land finden<select value="" onChange={(event) => {
        const country = countries.find((item) => item.code === event.target.value);
        if (country) setView(normalizedView(country.focus));
      }}><option value="">Land auswählen</option>{countries.map((country) =>
        <option key={country.code} value={country.code}>{country.name}</option>)}</select></label>
    </div>
    <p className="gps-page__hint">{isJourney
      ? "Wähle einen Zeitraum und bei Bedarf einen Kartenbereich. Den Bereich kannst du auch selbst aufziehen."
      : "Der Zeitraum bezieht sich auf das Aufnahmedatum. Fotos ohne Aufnahmedatum erscheinen nur ohne Zeitfilter."}</p>
    {invalidDate && <p id="gps-date-error" className="gps-page__error" role="alert">Das Enddatum muss am oder nach dem Startdatum liegen.</p>}

    {(region || queryFrom || queryTo || selectedAlbum) && <div className="gps-page__filters" aria-label="Aktive Filter">
      <span>Gefiltert nach</span>
      {region && <button type="button" onClick={clearArea} aria-label="Bereichsfilter entfernen">{regionName || "Gewählter Bereich"} <span aria-hidden="true">×</span></button>}
      {(queryFrom || queryTo) && <button type="button" onClick={() => trip?.day ? changeTrip({ ...trip, day: "" }) : setPreset("all")} aria-label="Zeitfilter entfernen">{dateLabel} <span aria-hidden="true">×</span></button>}
      {selectedAlbum && <button type="button" onClick={() => chooseAlbum("")} aria-label="Albumfilter entfernen">{selectedAlbum.name} <span aria-hidden="true">×</span></button>}
    </div>}
    <MapObservedPlaces key={JSON.stringify([fromDate, toDate, albumId, libraryRevision])} from={fromDate} to={toDate} albumId={albumId}
      onSelect={(place) => { selectRegion(place.bounds, place.name); frameBounds(place.bounds); }}
      onSetHome={isJourney ? setHome : undefined} />
    <div className="gps-page__layout">
      <section className="gps-page__map-card" aria-label="Fotostandorte auf der Weltkarte">
        <div className="gps-page__map-head">
          <div>
            <strong>{isJourney ? "Standortverlauf" : "Standorte"}</strong>
            <span aria-live="polite">{isJourney
              ? journey ? `${formatCount(journey.stops.length)} ${journey.stops.length === 1 ? "Stopp" : "Stopps"} · ${formatCount(journey.photo_count)} ${journey.photo_count === 1 ? "Foto" : "Fotos"}` : "–"
              : pointsLoading ? "Karte wird aktualisiert …" : points ? `${formatCount(points.total)} Fotos im sichtbaren Ausschnitt` : "–"}</span>
          </div>
          <div className="gps-page__map-actions">
            <button type="button" className={selectMode ? "gps-page__tool--active" : ""} aria-pressed={selectMode} onClick={() => setSelectMode((value) => !value)}>{selectMode ? "Auswahl beenden" : "Bereich wählen"}</button>
            {selectMode && <button type="button" onClick={() => selectRegion(viewport)}>Sichtbaren Bereich wählen</button>}
            {region && !isJourney && <button type="button" onClick={() => selectRegion(null)}>Auswahl löschen</button>}
            {isJourney && !areaInView && !selectMode && <button type="button" onClick={showArea}>Filterbereich zeigen</button>}
            <button type="button" className="gps-page__skip" onClick={() => resultsRef.current?.focus()}>{isJourney ? "Zu den Stopps" : "Zu den Fotos"}</button>
            <button type="button" disabled={!canLoad || (isJourney && !journey?.stops.length)} onClick={(event) => {
              albumOpenerRef.current = event.currentTarget;
              setAlbum({ bounds: filterBounds, viewport, hasSelection: Boolean(region), fromDate: queryFrom, toDate: queryTo, style: mapStyle,
                albumId: albumId || undefined, draft: selectedAlbum?.layout_draft,
                showCountryNames: showNames, journey: isJourney ? journey ?? undefined : undefined });
            }}>Bild exportieren</button>
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
          journeyStops={journey?.stops}
          journeyConnections={journey?.connections}
          activeJourneyStop={journeyStopId}
          onJourneyStopClick={selectJourneyStop}
          onSelectionChange={selectRegion}
          onCancelSelection={() => setSelectMode(false)}
          onMarkerClick={handleMarkerClick}
          selectionMode={selectMode}
        />
        <div className="gps-page__map-foot">
          <span>{selectMode ? "Einen Bereich mit Maus oder Finger aufziehen" : isJourney ? "Stopp wählen, um seine Fotos zu sehen" : "Karte ziehen · +/− zum Zoomen · Ort anklicken für Fotos"}</span>
          {mapStyle === "satellite" ? <a className="gps-map__credit" href="https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/" target="_blank" rel="noreferrer">NASA Earth Observatory · Natural Earth</a>
            : <a className="gps-map__credit" href="https://www.naturalearthdata.com/" target="_blank" rel="noreferrer">Kartendaten: Natural Earth</a>}
          <span>{region ? "Fester Bereichsfilter" : isJourney ? "Alle Orte im Zeitraum" : "Sichtbarer Kartenausschnitt"}</span>
        </div>
      </section>
      <section ref={resultsRef} id="gps-map-results" tabIndex={-1} className="gps-page__results" aria-label={isJourney ? "Standortverlauf und Fotos" : "Fotos im gewählten Kartenausschnitt"} aria-busy={imagesLoading}>
        {isJourney && <>
          <h2>Chronologische Stopps</h2>
          {selectedAlbum?.journey && <p className="gps-page__results-hint">Reisealbum
            {selectedAlbum.journey.arrival_included && " · Anreise enthalten"}
            {selectedAlbum.journey.departure_included && " · Rückreise enthalten"}</p>}
          {canLoad && !journeyState && <p role="status">Standortverlauf wird geladen …</p>}
          {journeyState?.error && <p className="gps-page__error" role="alert">{journeyState.error}{" "}
            <button type="button" onClick={() => { setJourneyResult(null); setJourneyRetry((value) => value + 1); }}>Erneut versuchen</button></p>}
          {tripsSource && <MapTrips journey={tripsSource} home={home} onClearHome={() => setHome(undefined)}
            selection={trip} canSave={Boolean(journeySelection)} onChange={changeTrip} onSave={(opener) => {
              if (journeySelection && journey) openAlbumPicker(opener, journeySelection, journey.photo_count, true);
            }} />}
          {journeySelection && onOpenImages && <div className="gps-results-actions">
            <button type="button" onClick={() => openInImages(journeySelection)}>Verlauf in Bilder öffnen</button>
          </div>}
          {journey && <MapJourney journey={journey} selected={journeyStopId} onSelect={selectJourneyStop}
            areaFiltered={Boolean(region)} onClearArea={clearArea}
            onClearDates={fromDate || toDate ? () => setPreset("all") : undefined} />}
        </>}
        {(!isJourney || journeyStop) && <>
        <div className="gps-page__results-head"><div><span className="gps-page__eyebrow">AUSWAHL</span><h2>{journeyStop ? `Fotos von Stopp ${journeyStop.number}` : region ? "Fotos im gewählten Bereich" : "Fotos im Kartenausschnitt"}</h2></div><span role="status">{imagesLoading ? "Lädt …" : imageResult?.data ? `${formatCount(imageTotal)} ${imageTotal === 1 ? "Foto" : "Fotos"}` : "–"}</span></div>
        {!isJourney && <p className="gps-page__results-hint">{region ? "Die Fotos bleiben auf den gewählten Bereich begrenzt." : "Die Fotos passen sich deinem Kartenausschnitt an."}</p>}
        {images.length > 0 && <div className="gps-results-actions">
          {stopDay && (fromDate !== stopDay || fromDate !== toDate) &&
            <button type="button" onClick={() => showStopDay(stopDay)}>{shortDate.format(new Date(stopDay))} zeigen</button>}
          <button type="button" onClick={(event) => openGallery(event.currentTarget, null)}>Galerie öffnen</button>
          {onOpenImages && <button type="button" onClick={() => openInImages(selection)}>In Bilder öffnen</button>}
          <button type="button" onClick={(event) => openAlbumPicker(event.currentTarget, selection, imageTotal)}>Zum Album hinzufügen</button>
        </div>}
        {imageResult?.error && <div className="gps-page__error" role="alert">{imageResult.error} <button type="button" onClick={() => { setImageRefresh((value) => value + 1); }}>Erneut versuchen</button></div>}
        {imageResult?.data && !imageResult.error && imageTotal === 0 && <div className="gps-page__empty"><strong>{points?.indexing ? "Deine Fotokarte entsteht gerade." : collection?.located_total === 0 ? "Noch keine Fotos mit Standort." : "Hier sind noch keine Fotos."}</strong><p>{points?.indexing ? "Fotos mit Standort erscheinen automatisch, sobald sie gefunden werden." : collection?.located_total === 0 ? "Sobald Fotos mit gespeicherten Standortdaten hinzugefügt werden, findest du sie hier." : "Wähle einen anderen Kartenausschnitt oder entferne den Zeitfilter."}</p>{hasFilters && <button type="button" onClick={reset}>Alle Standorte anzeigen</button>}</div>}
        {imagesLoading && <div className="gps-page__skeletons" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <div key={index} />)}</div>}
        <div className="gps-page__grid">{images.map((image, index) => <button type="button" className="gps-page__photo" key={image.id} onClick={(event) => {
          openGallery(event.currentTarget, index);
        }} aria-label={`${image.filename || "Foto"} öffnen`}><img src={imageThumbnailUrl(image.id)} alt="" loading="lazy" /><span>{image.filename || "Foto"}</span></button>)}</div>
        {moreError && <p className="gps-page__error" role="alert">{moreError}</p>}
        {nextCursor && <button type="button" className="gps-page__more" disabled={moreLoading} onClick={() => void loadMore()}>{moreLoading ? "Weitere Fotos werden geladen …" : moreError ? "Weitere Fotos erneut laden" : "Weitere Fotos laden"}</button>}
        </>}
      </section>
    </div>
    <aside className="gps-page__collection" aria-label="Standortübersicht">
      {!isJourney && <>
      <span><strong>{collection ? formatCount(collection.located_total) : "…"}</strong> Fotos mit Standort</span>
      <span><strong>{collection ? formatCount(Math.max(0, collection.library_total - collection.located_total - collection.pending_total)) : "…"}</strong> ohne Standort</span>
      {!!collection?.pending_total && <span className="gps-page__pending">{formatCount(collection.pending_total)} werden noch geprüft</span>}
      </>}
      <span className="gps-page__privacy">Deine Fotostandorte bleiben privat. Die Karte funktioniert auch offline.</span>
    </aside>
    {openedGallery && <MapGallery opening={openedGallery} onClose={closeGallery}
      onOpenImages={onOpenImages ? openInImages : undefined} onNavigateToCluster={onNavigateToCluster}
      onAddToAlbum={openAlbumPicker} />}
    {active && album && <MapAlbumDialog opening={album} onClose={closeAlbum}
      onSaveDraft={album.albumId ? async (draft) => { await updatePhotoAlbum(album.albumId!, { layout_draft: { ...draft } }); } : undefined} />}
    {active && saveSelection && <AlbumPickerDialog selection={saveSelection.selection.image_ids ? undefined : saveSelection.selection}
      imageIds={saveSelection.selection.image_ids} photoCount={saveSelection.count}
      initialName={saveSelection.travel ? `Reise ${queryFrom || trip?.trip.days[0] || ""}`.trim() : undefined}
      journey={saveSelection.travel ? { detail: journeyDetail, from_date: tripDays?.[0],
        to_date: tripDays?.[tripDays.length - 1], arrival_included: trip?.includeArrival,
        departure_included: trip?.includeDeparture } : undefined}
      onClose={closeAlbumPicker} />}
  </main>;
};

export default MapPage;
