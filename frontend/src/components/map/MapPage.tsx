import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchImageDetail, fetchMapImages, fetchMapPoints, imageThumbnailUrl,
  type FaceImage, type MapImage, type MapImageCursor, type MapPoint, type MapPointsResponse,
} from "../../utils/api";
import FullscreenImageGallery from "../people/FullscreenImageGallery";
import WorldMap, { boundsForView, type MapBounds, type MapGrid, type MapView } from "./WorldMap";
import { useModalFocus } from "../../hooks/useModalFocus";
import { subscribeToTopic } from "../../utils/events";
import "./map.css";

const PAGE_SIZE = 36;
const MAP_LIBRARY_EVENTS = new Set([
  "database_import", "delete_image", "image_path_cleanup", "image_rename", "import_completed",
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
  onNavigateToCluster: (clusterId: number, personName?: string | null) => void;
}

const GalleryStatus: React.FC<{ error: string | null; onClose: () => void }> = ({ error, onClose }) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalFocus(dialogRef, onClose);
  return <div ref={dialogRef} className="gps-page__gallery-wait" role="dialog" aria-modal="true" aria-label="Foto öffnen">
    <p role={error ? "alert" : "status"}>{error || "Foto wird geöffnet …"}</p>
    <button type="button" onClick={onClose}>{error ? "Schließen" : "Abbrechen"}</button>
  </div>;
};

const formatter = new Intl.NumberFormat("de-DE");
const formatCount = (count: number) => formatter.format(count);
const year = new Date().getFullYear();

const MapPage: React.FC<Props> = ({ active, onNavigateToCluster }) => {
  const [view, setView] = useState<MapView>(INITIAL_VIEW);
  const [pointGrid, setPointGrid] = useState<MapGrid | null>(null);
  const [region, setRegion] = useState<MapBounds | null>(null);
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
  const [gallery, setGallery] = useState<{ key: string; items: MapImage[] } | null>(null);
  const [galleryIndex, setGalleryIndex] = useState(0);
  const [lastGalleryImage, setLastGalleryImage] = useState<FaceImage | null>(null);
  const [galleryResult, setGalleryResult] = useState<{ id: number; error: string } | null>(null);
  const [pointRefresh, setPointRefresh] = useState(0);
  const [imageRefresh, setImageRefresh] = useState(0);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [detailRetry, setDetailRetry] = useState(0);

  const invalidDate = !!(fromDate && toDate && fromDate > toDate);
  const canLoad = active && !invalidDate;
  const canLoadPoints = canLoad && pointGrid !== null;
  const handleGridChange = useCallback((grid: MapGrid) => {
    setPointGrid((current) => current?.columns === grid.columns && current.rows === grid.rows ? current : grid);
  }, []);
  const viewport = useMemo(() => boundsForView(view), [view]);
  const filterBounds = region || viewport;
  const dates = { from_date: fromDate || undefined, to_date: toDate || undefined };
  const imageQuery = { ...filterBounds, ...dates };
  const pointQuery = {
    ...viewport, ...dates,
    grid_columns: pointGrid?.columns,
    grid_rows: pointGrid?.rows,
  };
  const filterKey = JSON.stringify(imageQuery);
  const pointKey = JSON.stringify([pointQuery, libraryRevision]);
  const imageKey = JSON.stringify([imageQuery, libraryRevision]);
  const pointResult = canLoad && pointsResult?.key === pointKey ? pointsResult : null;
  const imageResult = canLoad && imagesResult?.key === imageKey ? imagesResult : null;
  const points = pointResult?.error ? null : pointResult?.data;
  const collection = pointsResult?.data;
  const images = imageResult?.data?.items ?? EMPTY_IMAGES;
  const imageTotal = imageResult?.data?.total ?? 0;
  const nextCursor = imageResult?.data?.nextCursor;
  const hasExtendedResults = imageResult?.data?.extended ?? false;
  const pointsLoading = canLoad && !pointResult;
  const imagesLoading = canLoad && !imageResult;
  const openedGallery = canLoad && gallery?.key === filterKey ? gallery : null;
  const galleryImageId = openedGallery?.items[galleryIndex]?.id;
  const displayedGalleryIndex = openedGallery?.items.findIndex((image) => image.id === lastGalleryImage?.id) ?? -1;
  const galleryError = galleryResult?.id === galleryImageId ? galleryResult?.error : undefined;

  const clearGallery = useCallback(() => {
    setGallery(null);
    setLastGalleryImage(null);
    setGalleryResult(null);
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    const unsubscribe = subscribeToTopic<{ reason?: string }>("clusters", (update) => {
      if (!update?.reason || !MAP_LIBRARY_EVENTS.has(update.reason)) return;
      if (update.reason === "database_import") {
        clearGallery();
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
    clearGallery();
  }, [canLoad, filterKey, clearGallery]);

  useEffect(() => {
    setMoreError("");
    return () => { moreController.current?.abort(); };
  }, [canLoad, imageKey]);

  useEffect(() => {
    if (!canLoad) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void fetchMapImages({ ...imageQuery, limit: PAGE_SIZE }, controller.signal)
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
  }, [canLoad, imageKey, imageRefresh]);

  useEffect(() => {
    if (!canLoad || !points?.indexing || openedGallery || moreLoading) return;
    const timer = window.setTimeout(() => {
      setPointRefresh((value) => value + 1);
      if (!hasExtendedResults) setImageRefresh((value) => value + 1);
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [canLoad, pointKey, filterKey, pointRefresh, points?.indexing, openedGallery, moreLoading, hasExtendedResults]);

  useEffect(() => {
    if (galleryImageId === undefined) return;
    const controller = new AbortController();
    void fetchImageDetail(galleryImageId, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) {
          setLastGalleryImage(detail);
          setGalleryResult(null);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setGalleryResult({
          id: galleryImageId,
          error: reason instanceof Error ? reason.message : "Foto konnte nicht geöffnet werden.",
        });
      });
    return () => controller.abort();
  }, [galleryImageId, detailRetry]);

  const setPreset = (value: string) => {
    setDatePreset(value);
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
  const dateLabel = [fromDate, toDate].map((date) => date ? new Date(`${date}T12:00:00`).toLocaleDateString("de-DE") : "offen").join(" – ");
  const hasFilters = !!(region || fromDate || toDate || view.zoom > 1);
  const reset = () => { setRegion(null); setView(INITIAL_VIEW); setPreset("all"); setSelectMode(false); };
  const handleMarkerClick = (marker: MapPoint) => {
    setRegion({ west: marker.west, east: marker.east, south: marker.south, north: marker.north });
    setSelectMode(false);
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
  const loadMore = async () => {
    if (moreController.current || !canLoad || !nextCursor) return;
    const controller = new AbortController();
    const loadedImages = images;
    moreController.current = controller;
    setMoreLoading(true);
    setMoreError("");
    try {
      const page = await fetchMapImages({ ...imageQuery, ...nextCursor, limit: PAGE_SIZE }, controller.signal);
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
      <div className="gps-page__stat"><strong>{collection ? formatCount(collection.located_total) : "…"}</strong><span>Fotos mit Standort</span></div>
    </header>

    <section className="gps-page__toolbar" aria-label="Kartenfilter">
      <label className="gps-page__field gps-page__field--region"><span>Kartenausschnitt</span><select aria-label="Kartenausschnitt" value={REGIONS.find((item) => JSON.stringify(item.view) === JSON.stringify(view))?.label || "custom"} onChange={(event) => { const item = REGIONS.find((entry) => entry.label === event.target.value); if (item) { setView(item.view); setRegion(null); } }}><option value="custom" disabled>Eigener Ausschnitt</option>{REGIONS.map((item) => <option key={item.label}>{item.label}</option>)}</select></label>
      <label className="gps-page__field"><span>Zeitraum</span><select aria-label="Zeitraum" value={datePreset} onChange={(event) => setPreset(event.target.value)}><option value="all">Alle Zeiten</option><option value="last-30-days">Letzte 30 Tage</option><option value="this-year">Dieses Jahr</option><option value="last-year">Letztes Jahr</option><option value="custom">Eigener Zeitraum</option></select></label>
      <label className="gps-page__field"><span>Von</span><input type="date" aria-invalid={invalidDate} aria-describedby={invalidDate ? "gps-date-error" : undefined} value={fromDate} onChange={(event) => { setFromDate(event.target.value); setDatePreset("custom"); }} /></label>
      <label className="gps-page__field"><span>Bis</span><input type="date" aria-invalid={invalidDate} aria-describedby={invalidDate ? "gps-date-error" : undefined} value={toDate} onChange={(event) => { setToDate(event.target.value); setDatePreset("custom"); }} /></label>
      <button type="button" className="gps-page__reset" onClick={reset}>Zurücksetzen</button>
    </section>
    <p className="gps-page__hint">Der Zeitraum bezieht sich auf das Aufnahmedatum. Fotos ohne Aufnahmedatum erscheinen nur ohne Zeitfilter.</p>
    {invalidDate && <p id="gps-date-error" className="gps-page__error" role="alert">Das Enddatum muss am oder nach dem Startdatum liegen.</p>}

    {(region || fromDate || toDate) && <div className="gps-page__filters" aria-label="Aktive Filter">
      <span>Gefiltert nach</span>
      {region && <button type="button" onClick={() => setRegion(null)} aria-label="Bereichsfilter entfernen">Gewählter Bereich <span aria-hidden="true">×</span></button>}
      {(fromDate || toDate) && <button type="button" onClick={() => setPreset("all")} aria-label="Zeitfilter entfernen">{dateLabel} <span aria-hidden="true">×</span></button>}
    </div>}
    <div className="gps-page__layout">
      <section className="gps-page__map-card" aria-label="Fotostandorte auf der Weltkarte">
        <div className="gps-page__map-head">
          <div>
            <strong>Standorte</strong>
            <span aria-live="polite">{pointsLoading ? "Karte wird aktualisiert …" : points ? `${formatCount(points.total)} Fotos im sichtbaren Ausschnitt` : "–"}</span>
          </div>
          <div className="gps-page__map-actions">
            <button type="button" className={selectMode ? "gps-page__tool--active" : ""} aria-pressed={selectMode} onClick={() => setSelectMode((value) => !value)}>{selectMode ? "Auswahl beenden" : "Bereich wählen"}</button>
            {selectMode && <button type="button" onClick={() => { setRegion(viewport); setSelectMode(false); }}>Sichtbaren Bereich wählen</button>}
            {region && <button type="button" onClick={() => setRegion(null)}>Auswahl löschen</button>}
            <button type="button" className="gps-page__skip" onClick={() => resultsRef.current?.focus()}>Zu den Fotos</button>
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
          onSelectionChange={(bounds) => { setRegion(bounds); setSelectMode(false); }}
          onCancelSelection={() => setSelectMode(false)}
          onMarkerClick={handleMarkerClick}
          selectionMode={selectMode}
        />
        <div className="gps-page__map-foot">
          <span>{selectMode ? "Einen Bereich mit Maus oder Finger aufziehen" : "Karte ziehen · +/− zum Zoomen · Ort anklicken für Fotos"}</span>
          <a className="gps-map__credit" href="https://www.naturalearthdata.com/" target="_blank" rel="noreferrer">Kartendaten: Natural Earth</a>
          <span>{region ? "Bereich ausgewählt" : "Sichtbarer Kartenausschnitt"}</span>
        </div>
      </section>
      <section ref={resultsRef} id="gps-map-results" tabIndex={-1} className="gps-page__results" aria-label="Fotos im gewählten Kartenausschnitt" aria-busy={imagesLoading}>
        <div className="gps-page__results-head"><div><span className="gps-page__eyebrow">AUSWAHL</span><h2>{region ? "Fotos im gewählten Bereich" : "Fotos im Kartenausschnitt"}</h2></div><span role="status">{imagesLoading ? "Lädt …" : imageResult?.data ? `${formatCount(imageTotal)} Fotos` : "–"}</span></div>
        <p className="gps-page__results-hint">{region ? "Die Fotos bleiben auf den gewählten Bereich begrenzt." : "Die Fotos passen sich deinem Kartenausschnitt an."}</p>
        {imageResult?.error && <div className="gps-page__error" role="alert">{imageResult.error} <button type="button" onClick={() => { setImageRefresh((value) => value + 1); }}>Erneut versuchen</button></div>}
        {imageResult?.data && !imageResult.error && imageTotal === 0 && <div className="gps-page__empty"><strong>{points?.indexing ? "Deine Fotokarte entsteht gerade." : collection?.located_total === 0 ? "Noch keine Fotos mit Standort." : "Hier sind noch keine Fotos."}</strong><p>{points?.indexing ? "Fotos mit Standort erscheinen automatisch, sobald sie gefunden werden." : collection?.located_total === 0 ? "Sobald Fotos mit gespeicherten Standortdaten hinzugefügt werden, findest du sie hier." : "Wähle einen anderen Kartenausschnitt oder entferne den Zeitfilter."}</p>{hasFilters && <button type="button" onClick={reset}>Alle Standorte anzeigen</button>}</div>}
        {imagesLoading && <div className="gps-page__skeletons" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <div key={index} />)}</div>}
        <div className="gps-page__grid">{images.map((image, index) => <button type="button" className="gps-page__photo" key={image.id} onClick={(event) => {
          galleryOpenerRef.current = event.currentTarget;
          setGalleryIndex(index);
          setGallery({ key: filterKey, items: images });
        }} aria-label={`${image.filename || "Foto"} öffnen`}><img src={imageThumbnailUrl(image.id)} alt="" loading="lazy" /><span>{image.filename || "Foto"}</span></button>)}</div>
        {moreError && <p className="gps-page__error" role="alert">{moreError}</p>}
        {nextCursor && <button type="button" className="gps-page__more" disabled={moreLoading} onClick={() => void loadMore()}>{moreLoading ? "Weitere Fotos werden geladen …" : moreError ? "Weitere Fotos erneut laden" : "Weitere Fotos laden"}</button>}
      </section>
    </div>
    <aside className="gps-page__collection" aria-label="Standortübersicht">
      <span><strong>{collection ? formatCount(collection.located_total) : "…"}</strong> Fotos mit Standort</span>
      <span><strong>{collection ? formatCount(Math.max(0, collection.library_total - collection.located_total - collection.pending_total)) : "…"}</strong> ohne Standort</span>
      {!!collection?.pending_total && <span className="gps-page__pending">{formatCount(collection.pending_total)} werden noch geprüft</span>}
      <span className="gps-page__privacy">Deine Fotostandorte bleiben privat. Die Karte funktioniert auch offline.</span>
    </aside>
    {openedGallery && lastGalleryImage && <FullscreenImageGallery images={[lastGalleryImage]} activeIndex={0} onNavigateToCluster={onNavigateToCluster} onChange={() => {}} onClose={closeGallery} sequence={{
      activeIndex: displayedGalleryIndex,
      length: openedGallery.items.length,
      onChange: (index) => {
        setGalleryResult(null);
        if (index === galleryIndex) setDetailRetry((value) => value + 1);
        else setGalleryIndex(index);
      },
      itemLabel: "Foto",
      loading: displayedGalleryIndex !== galleryIndex && !galleryError,
      error: galleryError,
    }} />}
    {openedGallery && !lastGalleryImage && <GalleryStatus error={galleryError ?? null} onClose={closeGallery} />}
  </main>;
};

export default MapPage;
