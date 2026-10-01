import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchImageDetail, fetchMapImages, fetchMapPoints, imageThumbnailUrl,
  type FaceImage, type MapImage, type MapImagesResponse, type MapPoint, type MapPointsResponse,
} from "../../utils/api";
import FullscreenImageGallery from "../people/FullscreenImageGallery";
import WorldMap, { boundsForView, type MapBounds, type MapView } from "./WorldMap";
import { useModalFocus } from "../../hooks/useModalFocus";
import "./map.css";

const PAGE_SIZE = 36;
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
  const [region, setRegion] = useState<MapBounds | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [datePreset, setDatePreset] = useState("all");
  const [points, setPoints] = useState<MapPointsResponse | null>(null);
  const [images, setImages] = useState<MapImage[]>([]);
  const [imageTotal, setImageTotal] = useState(0);
  const [pointsLoading, setPointsLoading] = useState(active);
  const [imagesLoading, setImagesLoading] = useState(active);
  const [moreLoading, setMoreLoading] = useState(false);
  const [pointsError, setPointsError] = useState("");
  const [imagesError, setImagesError] = useState("");
  const imageRequestGeneration = useRef(0);
  const resultsRef = useRef<HTMLElement>(null);
  const moreController = useRef<AbortController | null>(null);
  const galleryOpenerRef = useRef<HTMLElement | null>(null);
  const [galleryIndex, setGalleryIndex] = useState<number | null>(null);
  const [galleryImage, setGalleryImage] = useState<FaceImage | null>(null);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [galleryError, setGalleryError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  const invalidDate = !!(fromDate && toDate && fromDate > toDate);
  const viewport = useMemo(() => boundsForView(view), [view]);
  const filterBounds = region || viewport;
  const filterKey = JSON.stringify({ ...filterBounds, from_date: fromDate, to_date: toDate });
  const pointKey = JSON.stringify({ ...viewport, from_date: fromDate, to_date: toDate, zoom: Math.round(Math.log2(view.zoom) * 2) });

  useEffect(() => {
    if (!active || invalidDate) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setPointsLoading(true);
      setPointsError("");
      void fetchMapPoints({ ...viewport, from_date: fromDate || undefined, to_date: toDate || undefined, zoom: Math.min(20, Math.round(Math.log2(view.zoom) * 2)) }, controller.signal)
        .then((result) => { if (!controller.signal.aborted) setPoints(result); })
        .catch((reason) => { if (!controller.signal.aborted) setPointsError(reason instanceof Error ? reason.message : "Standorte konnten nicht geladen werden."); })
        .finally(() => { if (!controller.signal.aborted) setPointsLoading(false); });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
    // The serialized key captures the rounded map bounds for deliberate debouncing.
  }, [active, invalidDate, pointKey, refresh]);

  useEffect(() => {
    resultsRef.current?.scrollTo({ top: 0 });
  }, [filterKey]);

  useEffect(() => {
    imageRequestGeneration.current += 1;
    moreController.current?.abort();
    setGalleryIndex(null);
    setGalleryImage(null);
    setImagesError("");
    if (!active || invalidDate) { setImages([]); setImageTotal(0); setImagesLoading(false); return; }
    setImagesLoading(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setImagesLoading(true);
      setImagesError("");
      void fetchMapImages({ ...filterBounds, from_date: fromDate || undefined, to_date: toDate || undefined, limit: PAGE_SIZE, offset: 0 }, controller.signal)
        .then((page: MapImagesResponse) => { if (!controller.signal.aborted) { setImages(page.items); setImageTotal(page.total); } })
        .catch((reason) => { if (!controller.signal.aborted) { setImages([]); setImageTotal(0); setImagesError(reason instanceof Error ? reason.message : "Fotos konnten nicht geladen werden."); } })
        .finally(() => { if (!controller.signal.aborted) setImagesLoading(false); });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, invalidDate, filterKey, refresh]);

  useEffect(() => {
    if (!active || !points?.indexing || galleryIndex !== null) return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 5000);
    return () => window.clearTimeout(timer);
  }, [active, points?.indexing, refresh, galleryIndex]);

  useEffect(() => {
    if (galleryIndex === null) { setGalleryImage(null); return; }
    const image = images[galleryIndex];
    if (!image) { setGalleryIndex(null); return; }
    let current = true;
    setGalleryLoading(true);
    setGalleryError(null);
    void fetchImageDetail(image.id)
      .then((detail) => { if (current) setGalleryImage(detail); })
      .catch((reason) => { if (current) setGalleryError(reason instanceof Error ? reason.message : "Foto konnte nicht geöffnet werden."); })
      .finally(() => { if (current) setGalleryLoading(false); });
    return () => { current = false; };
  }, [galleryIndex, images]);

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
    setGalleryIndex(null);
    const opener = galleryOpenerRef.current;
    window.requestAnimationFrame(() => { if (opener?.isConnected) opener.focus(); });
  };
  const loadMore = async () => {
    if (moreLoading || imagesLoading || invalidDate || images.length >= imageTotal) return;
    const generation = imageRequestGeneration.current;
    const controller = new AbortController();
    moreController.current = controller;
    setMoreLoading(true);
    try {
      const page = await fetchMapImages({ ...filterBounds, from_date: fromDate || undefined, to_date: toDate || undefined, limit: PAGE_SIZE, offset: images.length }, controller.signal);
      if (controller.signal.aborted || generation !== imageRequestGeneration.current) return;
      setImages((current) => current.concat(page.items));
      setImageTotal(page.total);
    } catch (reason) { if (!controller.signal.aborted && generation === imageRequestGeneration.current) setImagesError(reason instanceof Error ? reason.message : "Weitere Fotos konnten nicht geladen werden."); }
    finally { if (moreController.current === controller) { moreController.current = null; setMoreLoading(false); } }
  };

  return <main className="gps-page">
    <header className="gps-page__header">
      <div><span className="gps-page__eyebrow">FOTOBIBLIOTHEK · STANDORTE</span><h1>Fotokarte</h1><p>Entdecke deine Fotos dort, wo sie aufgenommen wurden.</p></div>
      <div className="gps-page__stat"><strong>{points ? formatCount(points.located_total) : "…"}</strong><span>Fotos mit Standort</span></div>
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
        <div className="gps-page__map-head"><div><strong>Standorte</strong><span>{pointsLoading ? "Karte wird aktualisiert …" : `${formatCount(points?.total ?? 0)} Fotos im sichtbaren Ausschnitt`}</span></div><div className="gps-page__map-actions"><button type="button" className={selectMode ? "gps-page__tool--active" : ""} aria-pressed={selectMode} onClick={() => setSelectMode((value) => !value)}>{selectMode ? "Auswahl beenden" : "Bereich wählen"}</button>{region && <button type="button" onClick={() => setRegion(null)}>Auswahl löschen</button>}</div></div>
        <WorldMap view={view} onViewChange={setView} points={points?.points || []} selection={region} onSelectionChange={(bounds) => { setRegion(bounds); setSelectMode(false); }} onMarkerClick={handleMarkerClick} selectionMode={selectMode} />
        <div className="gps-page__map-foot"><span>{selectMode ? "Einen Bereich mit Maus oder Finger aufziehen" : "Karte ziehen · +/− zum Zoomen · Ort anklicken für Fotos"}</span><span>{region ? "Bereich ausgewählt" : "Sichtbarer Kartenausschnitt"}</span></div>
      </section>
    <section ref={resultsRef} className={`gps-page__results${imagesLoading ? " gps-page__results--loading" : ""}`} aria-label="Fotos im gewählten Kartenausschnitt" aria-busy={imagesLoading}><div className="gps-page__results-head"><div><span className="gps-page__eyebrow">AUSWAHL</span><h2>{region ? "Fotos im gewählten Bereich" : "Fotos im Kartenausschnitt"}</h2></div><span role="status" aria-live="polite">{imagesLoading ? "Lädt …" : `${formatCount(imageTotal)} Fotos`}</span></div>
      <p className="gps-page__results-hint">{region ? "Die Fotos bleiben auf den gewählten Bereich begrenzt." : "Die Fotos passen sich deinem Kartenausschnitt an."}</p>
      {(imagesError || pointsError) && <div className="gps-page__error" role="alert">{imagesError || pointsError} <button type="button" onClick={() => setRefresh((value) => value + 1)}>Erneut versuchen</button></div>}
      {!imagesLoading && !invalidDate && !imagesError && imageTotal === 0 && <div className="gps-page__empty"><strong>{points?.indexing ? "Deine Fotokarte entsteht gerade." : points?.located_total === 0 ? "Noch keine Fotos mit Standort." : "Hier sind noch keine Fotos."}</strong><p>{points?.indexing ? "Fotos mit Standort erscheinen automatisch, sobald sie gefunden werden." : points?.located_total === 0 ? "Sobald Fotos mit gespeicherten Standortdaten hinzugefügt werden, findest du sie hier." : "Wähle einen anderen Kartenausschnitt oder entferne den Zeitfilter."}</p>{hasFilters && <button type="button" onClick={reset}>Alle Standorte anzeigen</button>}</div>}
      {imagesLoading && images.length === 0 && <div className="gps-page__skeletons" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <div key={index} />)}</div>}
      <div className="gps-page__grid">{images.map((image, index) => <button type="button" className="gps-page__photo" key={image.id} disabled={imagesLoading} onClick={(event) => { galleryOpenerRef.current = event.currentTarget; setGalleryIndex(index); }} aria-label={`${image.filename || "Foto"} öffnen`}><img src={imageThumbnailUrl(image.id)} alt="" loading="lazy" /><span>{image.filename || "Foto"}</span></button>)}</div>
      {!invalidDate && images.length < imageTotal && <button type="button" className="gps-page__more" disabled={moreLoading || imagesLoading} onClick={() => void loadMore()}>{moreLoading ? "Weitere Fotos werden geladen …" : "Weitere Fotos laden"}</button>}
    </section>
    </div>
    <aside className="gps-page__collection" aria-label="Standortübersicht">
      <span><strong>{points ? formatCount(points.located_total) : "…"}</strong> Fotos mit Standort</span>
      <span><strong>{points ? formatCount(Math.max(0, points.library_total - points.located_total - points.pending_total)) : "…"}</strong> ohne Standort</span>
      {!!points?.pending_total && <span className="gps-page__pending">{formatCount(points.pending_total)} werden noch geprüft</span>}
      <span className="gps-page__privacy">Deine Fotostandorte bleiben privat. Die Karte funktioniert auch offline.</span>
    </aside>
    {galleryIndex !== null && galleryImage && <FullscreenImageGallery images={[galleryImage]} activeIndex={0} onNavigateToCluster={onNavigateToCluster} onChange={() => {}} onClose={closeGallery} sequence={{ activeIndex: galleryIndex, length: images.length, onChange: setGalleryIndex, itemLabel: "Foto", loading: galleryLoading, error: galleryError }} />}
    {galleryIndex !== null && !galleryImage && (galleryLoading || galleryError) && <GalleryStatus error={galleryError} onClose={closeGallery} />}
  </main>;
};

export default MapPage;
