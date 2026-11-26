import { useEffect, useMemo, useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { fetchMapPoints, type MapPoint } from "../../utils/api";
import type { Journey } from "./mapData";
import { findNearbyPlace, loadJourneyPlaces, type JourneyPlace } from "./journeyPlaces";
import { journeyStopLabel } from "./MapJourney";
import {
  albumAccentColor, albumAccents, albumTransports, buildMapAlbumArtwork, mapAlbumPageToPng,
  type AlbumAccent, type AlbumTransport, type MapAlbumPage,
} from "./MapAlbumArtwork";
import type { MapBounds, MapStyle } from "./WorldMap";

export interface MapAlbumOpening {
  bounds: MapBounds;
  viewport: MapBounds;
  hasSelection: boolean;
  style: MapStyle;
  showCountryNames: boolean;
  fromDate: string;
  toDate: string;
  journey?: Journey;
}

type AlbumFormat = "a4" | "portrait" | "square";
type AlbumPalette = "sand" | "coast";
type AlbumData = { points?: MapPoint[]; places?: JourneyPlace[] };
type NativeSave = (name: string, data: string) => Promise<{ status: "saved" | "cancelled" | "error"; message?: string }>;
const dateFormatter = new Intl.DateTimeFormat("de-DE", { dateStyle: "long", timeZone: "UTC" });
const coordinate = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });
const day = (value: string) => dateFormatter.format(new Date(value.slice(0, 10) + "T12:00:00Z"));
const legKey = (leg: { from_stop_id: string; to_stop_id: string }) => `${leg.from_stop_id}:${leg.to_stop_id}`;

function pngBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Das PNG konnte nicht zum Speichern vorbereitet werden."));
    reader.onload = () => {
      const data = typeof reader.result === "string" ? reader.result.split(",", 2)[1] : undefined;
      if (data) resolve(data);
      else reject(new Error("Das PNG konnte nicht zum Speichern vorbereitet werden."));
    };
    reader.readAsDataURL(blob);
  });
}

function albumPeriod(opening: MapAlbumOpening) {
  const { fromDate, toDate, journey } = opening;
  if (fromDate && toDate) return fromDate === toDate ? day(fromDate) : `${day(fromDate)} – ${day(toDate)}`;
  if (fromDate) return `Ab ${day(fromDate)}`;
  if (toDate) return `Bis ${day(toDate)}`;
  if (journey?.stops.length) {
    const observations = journey.stops.flatMap((stop) => [stop.start.slice(0, 10), stop.end.slice(0, 10)]).sort();
    const start = day(observations[0]);
    const end = day(observations[observations.length - 1]);
    return start === end ? start : `${start} – ${end}`;
  }
  return undefined;
}

export default function MapAlbumDialog({ opening, onClose }: { opening: MapAlbumOpening; onClose: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  const defaultTitle = opening.journey ? "Mein Reiseverlauf" : "Meine Fotokarte";
  const [title, setTitle] = useState(defaultTitle);
  const [subtitle, setSubtitle] = useState("");
  const [format, setFormat] = useState<AlbumFormat>("a4");
  const [palette, setPalette] = useState<AlbumPalette>("sand");
  const [accent, setAccent] = useState<AlbumAccent>("automatic");
  const [wideFrame, setWideFrame] = useState(false);
  const [useViewport, setUseViewport] = useState(false);
  const [transports, setTransports] = useState<Partial<Record<string, AlbumTransport>>>({});
  const [style, setStyle] = useState(opening.style);
  const [showNames, setShowNames] = useState(opening.showCountryNames);
  const [showPeriod, setShowPeriod] = useState(true);
  const [showStopsList, setShowStopsList] = useState(true);
  const [data, setData] = useState<AlbumData | null>(null);
  const [retry, setRetry] = useState(0);
  const [dataError, setDataError] = useState("");
  const [rendered, setRendered] = useState<{ key: string; pages: MapAlbumPage[] } | null>(null);
  const [renderError, setRenderError] = useState<{ key: string; text: string }>();
  const [pageIndex, setPageIndex] = useState(0);
  const [preview, setPreview] = useState<{ page: MapAlbumPage; url: string }>();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const active = useRef(true);
  const fileTitle = title.trim();
  const bounds = useViewport ? opening.viewport : opening.bounds;
  const renderKey = JSON.stringify([fileTitle, subtitle, format, palette, accent, style, showNames, showPeriod,
    showStopsList, wideFrame, useViewport, transports, retry]);
  const pages = rendered?.key === renderKey ? rendered.pages : [];
  const page = pages[pageIndex];
  const previewPage = rendered?.pages[pageIndex] ?? rendered?.pages[0];
  const error = dataError || (renderError?.key === renderKey ? renderError.text : "");
  const stops = useMemo(() => opening.journey?.stops.map((stop) => {
    const nearby = data?.places && findNearbyPlace(data.places, stop.latitude, stop.longitude);
    return {
      ...stop,
      title: nearby ? `Bei ${nearby.name}` : `${coordinate.format(stop.latitude)}°, ${coordinate.format(stop.longitude)}°`,
      subtitle: `${journeyStopLabel(stop)}${stop.uncertain ? " · Zeitfolge unsicher" : ""}`,
    };
  }), [opening, data?.places]);
  const stopsById = useMemo(() => new Map(stops?.map((stop) => [stop.id, stop])), [stops]);
  const legs = useMemo(() => opening.journey?.connections.flatMap((leg) => {
    const start = stopsById.get(leg.from_stop_id);
    const end = stopsById.get(leg.to_stop_id);
    return start && end ? [{ key: legKey(leg), start, end }] : [];
  }), [opening, stopsById]);
  useModalFocus(dialog, onClose);

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  useEffect(() => {
    const controller = new AbortController();
    setDataError("");
    setData(null);
    const request: Promise<AlbumData> = opening.journey
      ? loadJourneyPlaces().then((places) => ({ places })).catch(() => ({}))
      : fetchMapPoints({ ...bounds, from_date: opening.fromDate || undefined,
        to_date: opening.toDate || undefined, grid_columns: 18, grid_rows: 12 }, controller.signal)
        .then(({ points }) => ({ points }));
    void request.then((value) => {
      if (!controller.signal.aborted) setData(value);
    }).catch((failure) => {
      if (!controller.signal.aborted) setDataError(failure instanceof Error ? failure.message : "Die Karte konnte nicht geladen werden.");
    });
    return () => controller.abort();
  }, [opening, bounds, retry]);

  useEffect(() => {
    if (!data || !fileTitle) return;
    let current = true;
    const timer = window.setTimeout(() => {
      const arrivals = new Map(legs?.map((leg) => [leg.end.id, transports[leg.key]]));
      void buildMapAlbumArtwork({
        format, title: fileTitle, subtitle: subtitle.trim(), period: showPeriod ? albumPeriod(opening) : undefined,
        palette, accent, style, showCountryNames: showNames, showStopsList, bounds, wideFrame,
        points: data.points, stops: stops?.map((stop) => ({ ...stop, arrivalMode: arrivals.get(stop.id) })),
        connections: opening.journey?.connections.map((leg) => ({ ...leg, transport: transports[legKey(leg)] })),
        fitJourney: Boolean(opening.journey),
      }).then((pages) => {
        if (current) {
          setRenderError(undefined);
          setPageIndex((index) => Math.min(index, pages.length - 1));
          setRendered({ key: renderKey, pages });
        }
      }).catch((failure) => {
        if (current) setRenderError({ key: renderKey, text: failure instanceof Error ? failure.message : "Die Vorschau konnte nicht erstellt werden." });
      });
    }, 120);
    return () => { current = false; window.clearTimeout(timer); };
  }, [data, fileTitle, format, opening, palette, accent, style, showNames, showPeriod, showStopsList, subtitle,
    renderKey, bounds, wideFrame, stops, legs, transports]);

  useEffect(() => {
    if (!previewPage) return;
    const url = URL.createObjectURL(new Blob([previewPage.svg], { type: "image/svg+xml;charset=utf-8" }));
    setPreview({ page: previewPage, url });
    return () => URL.revokeObjectURL(url);
  }, [previewPage]);

  const save = async () => {
    if (!page || saving) return;
    setSaving(true);
    setSaveError("");
    setConfirmation("");
    try {
      const png = await mapAlbumPageToPng(page);
      if (!active.current) return;
      const name = fileTitle.replace(/[^\p{L}\p{N} ._-]+/gu, "-").replace(/^\.+|[ .-]+$/g, "").trim() || "Fotokarte";
      const filename = `${name}${page.total > 1 ? `-${String(page.number).padStart(2, "0")}` : ""}.png`;
      const desktop = (window as Window & { pywebview?: { api?: { save_album_png?: NativeSave } } }).pywebview?.api;
      if (desktop?.save_album_png) {
        const data = await pngBase64(png);
        if (!active.current) return;
        const result = await desktop.save_album_png(filename, data);
        if (result.status === "error") throw new Error(result.message || "Das PNG konnte nicht gespeichert werden.");
        if (active.current && result.status === "saved") setConfirmation("PNG gespeichert.");
      } else {
        const url = URL.createObjectURL(png);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 30000);
        setConfirmation("PNG erstellt.");
      }
    } catch (failure) {
      if (active.current) setSaveError(failure instanceof Error ? failure.message : "Das PNG konnte nicht gespeichert werden.");
    } finally {
      if (active.current) setSaving(false);
    }
  };

  return <div className="gps-album-overlay">
    <section ref={dialog} className="gps-album-dialog" role="dialog" aria-modal="true" aria-labelledby="gps-album-title">
      <header className="gps-album-dialog__header">
        <h2 id="gps-album-title">Fotobuchbild erstellen</h2>
        <button type="button" data-initial-focus onClick={onClose}>Schließen</button>
      </header>
      <fieldset className="gps-album-dialog__options" disabled={saving}>
        <label>Titel<input required aria-invalid={!fileTitle} value={title} maxLength={72} onChange={(event) => {
          setTitle(event.target.value); setConfirmation("");
        }} /></label>
        <label><span id="gps-album-format">Format</span><select aria-labelledby="gps-album-format" value={format} onChange={(event) => {
          const next = event.target.value;
          setFormat(next === "square" || next === "portrait" ? next : "a4"); setConfirmation(""); setPageIndex(0);
        }}>
          <option value="a4">A4 quer · 3508 × 2480 px</option>
          <option value="portrait">A4 hoch · 2480 × 3508 px</option>
          <option value="square">Quadratisch · 3000 × 3000 px</option>
        </select></label>
        <label>Unterzeile (optional)<input value={subtitle} maxLength={100} onChange={(event) => {
          setSubtitle(event.target.value); setConfirmation("");
        }} /></label>
        <label><span id="gps-album-palette">Farbwelt</span><select aria-labelledby="gps-album-palette" value={palette} onChange={(event) => {
          setPalette(event.target.value === "coast" ? "coast" : "sand"); setConfirmation("");
        }}><option value="sand">Sand &amp; Salbei</option><option value="coast">Küste &amp; Schiefer</option></select></label>
      </fieldset>
      <details className="gps-album-dialog__details">
        <summary tabIndex={0}>Darstellung</summary>
        <fieldset disabled={saving}>
          <label><span id="gps-album-style">Kartenstil</span><select aria-labelledby="gps-album-style" value={style} onChange={(event) => {
            setStyle(event.target.value === "landscape" ? "landscape" : "standard"); setConfirmation("");
          }}><option value="standard">Standard</option><option value="landscape">Landschaft</option></select></label>
          <label><span id="gps-album-frame">Bildausschnitt</span><select aria-labelledby="gps-album-frame" value={wideFrame ? "wide" : "close"} onChange={(event) => {
            setWideFrame(event.target.value === "wide"); setConfirmation("");
          }}><option value="close">{opening.journey ? "Ganze Reise einrahmen" : "Eng einrahmen"}</option>
            <option value="wide">Mehr Umgebung</option></select></label>
          {!opening.journey && opening.hasSelection && <label><span id="gps-album-area">Kartenbereich</span>
            <select aria-labelledby="gps-album-area" value={useViewport ? "viewport" : "selection"} onChange={(event) => {
              setUseViewport(event.target.value === "viewport"); setConfirmation("");
            }}><option value="selection">Gewählter Bereich</option><option value="viewport">Sichtbarer Bereich</option></select></label>}
          <label><span id="gps-album-accent">{opening.journey ? "Routenfarbe" : "Markerfarbe"}</span>
            <span className="gps-album-dialog__swatch" aria-hidden="true" style={{ backgroundColor: albumAccentColor(palette, accent, Boolean(opening.journey)) }} />
            <select aria-labelledby="gps-album-accent" value={accent} onChange={(event) => {
              setAccent(event.target.value as AlbumAccent); setConfirmation("");
            }}>{Object.entries(albumAccents).map(([value, { name }]) => <option key={value} value={value}>{name}</option>)}</select></label>
          <label><input type="checkbox" checked={showNames} onChange={(event) => {
            setShowNames(event.target.checked); setConfirmation("");
          }} /> Ländernamen</label>
          <label><input type="checkbox" checked={showPeriod} onChange={(event) => {
            setShowPeriod(event.target.checked); setConfirmation("");
          }} /> Zeitraum unter dem Titel</label>
          {opening.journey && <label><input type="checkbox" checked={showStopsList} onChange={(event) => {
            setShowStopsList(event.target.checked); setConfirmation(""); setPageIndex(0);
          }} /> Stoppliste</label>}
        </fieldset>
      </details>
      {Boolean(legs?.length) && <details className="gps-album-dialog__details gps-album-dialog__route">
        <summary tabIndex={0}>Route gestalten</summary>
        <p>Verkehrsmittel pro Etappe wählen. Die Linien verbinden Fotoorte.</p>
        <fieldset disabled={saving} aria-label="Verkehrsmittel pro Etappe">
          {legs?.map(({ key, start, end }) => <label key={key}>
            <span id={`gps-album-leg-${key}`}>{start.number}. {start.title} → {end.number}. {end.title}</span>
            <select aria-labelledby={`gps-album-leg-${key}`} value={transports[key] ?? ""} onChange={(event) => {
              const mode = event.target.value as AlbumTransport | "";
              setTransports((before) => ({ ...before, [key]: mode || undefined }));
              setConfirmation("");
            }}><option value="">Nicht angegeben</option>
              {Object.entries(albumTransports).map(([value, { label }]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>)}
        </fieldset>
      </details>}
      <div className="gps-album-dialog__preview" aria-busy={Boolean(fileTitle) && !page && !error}>
        {fileTitle && !error && preview && preview.page === previewPage && <img src={preview.url} alt="Vorschau des Fotobuchbildes" />}
        {!fileTitle && <p role="status">Gib einen Titel für das Fotobuchbild ein.</p>}
        {fileTitle && !page && !error && !preview && <p role="status">Vorschau wird erstellt …</p>}
        {error && <p role="alert">{error}{" "}<button type="button" onClick={() => {
          setData(null); setRenderError(undefined); setRetry((value) => value + 1);
        }}>Erneut versuchen</button></p>}
      </div>
      <footer className="gps-album-dialog__footer">
        {pages.length > 1 && <div role="group" aria-label="Fotobuchseiten" className="gps-album-dialog__pages">
          <button type="button" aria-label="Vorherige Seite" disabled={saving || pageIndex === 0} onClick={() => {
            setPageIndex((index) => index - 1); setConfirmation("");
          }}>‹</button>
          <span>Seite {pageIndex + 1} von {pages.length}</span>
          <button type="button" aria-label="Nächste Seite" disabled={saving || pageIndex >= pages.length - 1} onClick={() => {
            setPageIndex((index) => index + 1); setConfirmation("");
          }}>›</button>
        </div>}
        {saveError && <span role="alert">{saveError}</span>}
        {confirmation && <span role="status">{confirmation}</span>}
        <button type="button" className="gps-album-dialog__save" disabled={!page || saving} onClick={() => void save()}>
          {saving ? "PNG wird erstellt …" : pages.length > 1 ? "Diese Seite als PNG speichern" : "PNG speichern"}
        </button>
      </footer>
    </section>
  </div>;
}
