import { useEffect, useMemo, useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { fetchMapPoints, fetchSelectedMapPoints, type MapPoint } from "../../utils/api";
import { countWithLabel } from "../../utils/germanFormatting";
import type { Journey, JourneyDetail } from "./mapData";
import { describeJourneyPlaces, findNearbyPlace, loadJourneyPlaces, type JourneyPlace } from "./journeyPlaces";
import { journeyStopLabel } from "./MapJourney";
import { suggestJourneyTransport } from "./journeyTransport";
import {
  albumAccents, albumDensities, albumOutputSize, albumPaletteColors, albumPhotoQueryBounds, albumTransports, buildMapAlbumArtwork,
  defaultAlbumFraming, mapAlbumPageToPng, normalizeAlbumColor, type AlbumAccent, type AlbumCustomColors, type AlbumDpi,
  type AlbumDimensions, type AlbumFormat, type AlbumFormatting, type AlbumFraming, type AlbumMarkerStyle,
  type AlbumTransport, type MapAlbumPage,
} from "./MapAlbumArtwork";
import type { MapBounds, MapStyle } from "./WorldMap";
import { archiveMapAlbumPages, MAX_ALBUM_ARCHIVE_BYTES, MAX_ALBUM_ARCHIVE_PAGES, MAX_ALBUM_PAGE_BYTES } from "./mapAlbumArchive";
import satelliteSource from "./world-satellite.json";

export interface MapAlbumOpening {
  bounds: MapBounds;
  viewport: MapBounds;
  hasSelection: boolean;
  style: MapStyle;
  showCountryNames: boolean;
  fromDate: string;
  toDate: string;
  scopeTitle?: string;
  locationPhotoIds?: number[];
  journey?: Journey;
  journeyDetail?: JourneyDetail;
  albumId?: string;
  draft?: unknown;
}

type AlbumPalette = "sand" | "coast";
type AlbumLayout = "map" | "list" | "bleed";

export interface AlbumDraft {
  version: 1;
  title: string;
  subtitle: string;
  format: AlbumFormat;
  dimensions: AlbumDimensions;
  dpi: AlbumDpi;
  palette: AlbumPalette;
  accent: AlbumAccent;
  colors: AlbumCustomColors;
  formatting: AlbumFormatting;
  contextPercent: number;
  markerScale: number;
  useViewport: boolean;
  viewportSnapshot?: MapBounds;
  transports: Partial<Record<string, AlbumTransport>>;
  style: MapStyle;
  showNames: boolean;
  showPeriod: boolean;
  showStopsList: boolean;
  fullBleed: boolean;
  framing: AlbumFraming;
  markerStyle: AlbumMarkerStyle;
  showPlaceNames: boolean;
  showRoute: boolean;
  placeNames: Record<string, string>;
}

type AlbumData = { points?: MapPoint[]; places?: JourneyPlace[] };
type NativeSaveResult = { status: "saved" | "cancelled" | "error"; message?: string };
type NativeAlbumSave = { save_album_png?: (name: string, data: string) => Promise<NativeSaveResult>;
  save_album_zip?: (name: string, pages: string[]) => Promise<NativeSaveResult> };
const dateFormatter = new Intl.DateTimeFormat("de-DE", { dateStyle: "long", timeZone: "UTC" });
const millimeters = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const MM_PER_INCH = 25.4;
const formatOptions = [
  ["a4", "A4 quer (29,7 × 21 cm)"],
  ["portrait", "A4 hoch (21 × 29,7 cm)"],
  ["square-210", "Quadratisch (21 × 21 cm)"],
  ["square-300", "Quadratisch (30 × 30 cm)"],
  ["custom", "Eigene Größe"],
] as const;
const colorFields = [["paper", "Papierfarbe"], ["accent", "Akzentfarbe"], ["land", "Landfarbe"], ["water", "Wasserfarbe"]] as const;
const day = (value: string) => dateFormatter.format(new Date(value.slice(0, 10) + "T12:00:00Z"));
const legKey = (leg: { from_stop_id: string; to_stop_id: string }) => `${leg.from_stop_id}:${leg.to_stop_id}`;
const isChoice = <Choice extends string,>(choices: readonly Choice[], value: unknown): value is Choice =>
  choices.some((choice) => choice === value);
const frameValue = (value: unknown, minimum: number, maximum: number, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? Math.min(maximum, Math.max(minimum, Math.round(value))) : fallback;

function readViewportSnapshot(value?: MapBounds) {
  if (!value) return undefined;
  const { west, east, south, north } = value;
  if (![west, east, south, north].every(Number.isFinite) || west === east || south >= north ||
    Math.abs(west) > 180 || Math.abs(east) > 180 || south < -90 || north > 90) return undefined;
  return { west, east, south, north };
}

function readAlbumDraft(value: unknown) {
  if (!value || typeof value !== "object" || !("version" in value) || value.version !== 1) return undefined;
  const draft = value as Partial<AlbumDraft>;
  const colors = Object.fromEntries(colorFields.flatMap(([field]) => {
    const value = draft.colors?.[field];
    return typeof value === "string" && normalizeAlbumColor(value) ? [[field, value]] : [];
  }));
  const transports = Object.fromEntries(Object.entries(draft.transports ?? {}).filter(([, value]) =>
    typeof value === "string" && Object.prototype.hasOwnProperty.call(albumTransports, value)));
  const placeNames = Object.fromEntries(Object.entries(draft.placeNames ?? {}).flatMap(([id, name]) =>
    /^[0-9]{1,19}$/.test(id) && typeof name === "string" && name.trim() ? [[id, name.trim().slice(0, 60)]] : []));
  const titleScale = draft.formatting?.titleScale;
  const formatting: AlbumFormatting = {
    typeface: draft.formatting?.typeface === "sans" ? "sans" : "serif",
    alignment: draft.formatting?.alignment === "center" ? "center" : "left",
    titleScale: typeof titleScale === "number" && Number.isFinite(titleScale) ? Math.min(130, Math.max(75, titleScale)) : 100,
    margins: isChoice(["narrow", "wide"], draft.formatting?.margins) ? draft.formatting.margins : "normal",
    listColumns: draft.formatting?.listColumns === 1 ? 1 : 2,
  };
  const dpi = draft.dpi && albumDensities.includes(draft.dpi) ? draft.dpi : 300;
  return {
    ...draft, colors, transports, formatting, dpi, placeNames,
    title: typeof draft.title === "string" ? draft.title.slice(0, 72) : undefined,
    subtitle: typeof draft.subtitle === "string" ? draft.subtitle.slice(0, 100) : undefined,
    format: draft.format === "square" || isChoice(formatOptions.map(([value]) => value), draft.format)
      ? draft.format : "a4" as const,
    dimensions: albumOutputSize("custom", dpi, draft.dimensions) ?? { width: 3000, height: 3000 },
    style: isChoice(["standard", "landscape", "satellite"], draft.style) ? draft.style : undefined,
    palette: draft.palette === "coast" ? "coast" as const : "sand" as const,
    accent: isChoice(Object.keys(albumAccents) as AlbumAccent[], draft.accent) ? draft.accent : "automatic" as const,
    contextPercent: typeof draft.contextPercent === "number" && Number.isFinite(draft.contextPercent)
      ? Math.max(0, Math.min(100, draft.contextPercent)) : 0,
    markerScale: typeof draft.markerScale === "number" && Number.isFinite(draft.markerScale)
      ? Math.max(70, Math.min(140, draft.markerScale)) : 100,
    useViewport: draft.useViewport === true,
    viewportSnapshot: readViewportSnapshot(draft.viewportSnapshot),
    showNames: typeof draft.showNames === "boolean" ? draft.showNames : undefined,
    showPeriod: draft.showPeriod !== false,
    showStopsList: draft.showStopsList !== false,
    fullBleed: draft.fullBleed === true,
    framing: {
      zoom: frameValue(draft.framing?.zoom, 50, 400, 100),
      horizontal: frameValue(draft.framing?.horizontal, -200, 200, 0),
      vertical: frameValue(draft.framing?.vertical, -200, 200, 0),
    },
    markerStyle: isChoice(["dots", "none"], draft.markerStyle) ? draft.markerStyle : "standard" as const,
    showPlaceNames: typeof draft.showPlaceNames === "boolean" ? draft.showPlaceNames : undefined,
    showRoute: draft.showRoute !== false,
  };
}

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

function downloadAlbumFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function albumPeriod(opening: MapAlbumOpening) {
  const { fromDate, toDate, journey } = opening;
  if (journey?.stops.length) {
    const observations = journey.stops.flatMap((stop) => [stop.start.slice(0, 10), stop.end.slice(0, 10)]).sort();
    const start = day(observations[0]);
    const end = day(observations[observations.length - 1]);
    return start === end ? start : `${start} – ${end}`;
  }
  if (fromDate && toDate) return fromDate === toDate ? day(fromDate) : `${day(fromDate)} – ${day(toDate)}`;
  if (fromDate) return `Ab ${day(fromDate)}`;
  if (toDate) return `Bis ${day(toDate)}`;
  return undefined;
}

export default function MapAlbumDialog({ opening, onClose, onSaveDraft }: {
  opening: MapAlbumOpening; onClose: () => void; onSaveDraft?: (draft: AlbumDraft) => Promise<void>;
}) {
  const dialog = useRef<HTMLElement>(null);
  const defaultTitle = opening.scopeTitle?.trim().slice(0, 72) || (opening.journey ? "Meine Reise" : "Meine Fotokarte");
  const draft = useMemo(() => readAlbumDraft(opening.draft), [opening.draft]);
  const [title, setTitle] = useState(draft?.title ?? defaultTitle);
  const [subtitle, setSubtitle] = useState(draft?.subtitle ?? "");
  const [format, setFormat] = useState<AlbumFormat>(draft?.format ?? "a4");
  const [width, setWidth] = useState(String(draft?.dimensions.width ?? 3000));
  const [height, setHeight] = useState(String(draft?.dimensions.height ?? 3000));
  const [sizeUnit, setSizeUnit] = useState<"px" | "mm">("px");
  const [dpi, setDpi] = useState<AlbumDpi>(draft?.dpi ?? 300);
  const [palette, setPalette] = useState<AlbumPalette>(draft?.palette ?? "sand");
  const [accent, setAccent] = useState<AlbumAccent>(draft?.accent ?? "automatic");
  const [colors, setColors] = useState<AlbumCustomColors>(draft?.colors ?? {});
  const [formatting, setFormatting] = useState<AlbumFormatting>(draft?.formatting ?? {});
  const [contextPercent, setContextPercent] = useState(draft?.contextPercent ?? 0);
  const [framing, setFraming] = useState(draft?.framing ?? defaultAlbumFraming);
  const [markerScale, setMarkerScale] = useState(draft?.markerScale ?? 100);
  const canChangeArea = Boolean(opening.hasSelection || opening.journey);
  const [useViewport, setUseViewport] = useState(Boolean(canChangeArea && draft?.useViewport));
  const [viewportSnapshot, setViewportSnapshot] = useState(draft?.viewportSnapshot ?? opening.viewport);
  const [transports, setTransports] = useState<Partial<Record<string, AlbumTransport>>>(draft?.transports ?? {});
  const [style, setStyle] = useState(draft?.style ?? opening.style);
  const [showNames, setShowNames] = useState(draft?.showNames ?? opening.showCountryNames);
  const [showPeriod, setShowPeriod] = useState(draft?.showPeriod ?? true);
  const [layout, setLayout] = useState<AlbumLayout>(draft?.fullBleed ? "bleed" : draft?.showStopsList && opening.journey ? "list" : "map");
  const [markerStyle, setMarkerStyle] = useState<AlbumMarkerStyle>(draft?.markerStyle ?? "standard");
  const [showPlaceNames, setShowPlaceNames] = useState(draft?.showPlaceNames ?? Boolean(opening.journey));
  const [showRoute, setShowRoute] = useState(draft?.showRoute ?? true);
  const [placeNames, setPlaceNames] = useState(draft?.placeNames ?? {});
  const [nameStopId, setNameStopId] = useState("");
  const showStopsList = layout === "list";
  const fullBleed = layout === "bleed";
  const [data, setData] = useState<AlbumData | null>(null);
  const [retry, setRetry] = useState(0);
  const [dataError, setDataError] = useState("");
  const [rendered, setRendered] = useState<{
    key: string; opening: MapAlbumOpening; data: AlbumData; pages: MapAlbumPage[];
  } | null>(null);
  const [renderError, setRenderError] = useState<{ key: string; text: string }>();
  const [pageIndex, setPageIndex] = useState(0);
  const [preview, setPreview] = useState<{ page: MapAlbumPage; url: string }>();
  const [saving, setSaving] = useState<"page" | "archive">();
  const [archivePage, setArchivePage] = useState(0);
  const [savingDraft, setSavingDraft] = useState(false);
  const busy = Boolean(saving || savingDraft);
  const [saveError, setSaveError] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [leaveDesign, setLeaveDesign] = useState<"close" | "reset">();
  const confirmationDialog = useRef<HTMLElement>(null);
  const active = useRef(true);
  const fileTitle = title.trim();
  const exportName = fileTitle.replace(/[^\p{L}\p{N} ._-]+/gu, "-").replace(/^\.+|[ .-]+$/g, "").trim() || "Fotokarte";
  const desktop = (window as Window & { pywebview?: { api?: NativeAlbumSave } }).pywebview?.api;
  const bounds = useViewport ? viewportSnapshot : opening.bounds;
  const framingChanged = framing.zoom !== 100 || framing.horizontal !== 0 || framing.vertical !== 0;
  const queryingCrop = !opening.journey && framingChanged;
  const queryBounds = useMemo(() => queryingCrop ? albumPhotoQueryBounds(bounds, framing) : bounds,
    [bounds, framing, queryingCrop]);
  const dimensions = useMemo(() => sizeUnit === "mm"
    ? { width: Math.round(Number(width) * dpi / MM_PER_INCH), height: Math.round(Number(height) * dpi / MM_PER_INCH) }
    : { width: Number(width), height: Number(height) }, [dpi, height, sizeUnit, width]);
  const size = albumOutputSize(format, dpi, dimensions);
  const availableColorFields = colorFields.filter(([field]) => style !== "satellite" || field === "paper" || field === "accent");
  const invalidColor = availableColorFields.some(([field]) => Boolean(colors[field]?.trim() && !normalizeAlbumColor(colors[field])));
  const canRender = Boolean(size && !invalidColor);
  const currentDesign: AlbumDraft = { version: 1, title, subtitle, format, dimensions, dpi, palette, accent, colors, formatting,
    contextPercent, markerScale, useViewport, transports, style, showNames, showPeriod, showStopsList, fullBleed, framing,
    markerStyle, showPlaceNames, showRoute, placeNames, viewportSnapshot: useViewport ? viewportSnapshot : undefined };
  const designKey = JSON.stringify(currentDesign);
  const savedDesign = useRef({ design: currentDesign, key: designKey });
  const dirty = savedDesign.current.key !== designKey;
  const markDesignSaved = () => { savedDesign.current = { design: currentDesign, key: designKey }; };
  const defaultColors = albumPaletteColors(palette, accent, Boolean(opening.journey), style);
  const renderKey = `${designKey}:${retry}`;
  const pages = canRender && rendered?.key === renderKey && rendered.opening === opening && rendered.data === data
    ? rendered.pages : [];
  const page = pages[pageIndex];
  const previewCount = rendered?.pages.length ?? 0;
  const previewIndex = Math.min(pageIndex, Math.max(0, previewCount - 1));
  const previewPage = rendered?.pages[previewIndex];
  const error = dataError || (renderError?.key === renderKey ? renderError.text : "");
  const updatingPreview = canRender && !page && !error;
  const stops = useMemo(() => {
    if (!opening.journey) return undefined;
    const places = describeJourneyPlaces(opening.journey.stops, data?.places);
    return opening.journey.stops.map((stop) => {
      const location = places.get(stop.id);
      return { ...stop, title: location?.nearby ? `Bei ${location.nearby.name}` : location?.coordinates ?? `Stopp ${stop.number}`,
        placeName: location?.nearby?.name,
        subtitle: `${location?.sharedName ? `${location.coordinates} · ` : ""}${journeyStopLabel(stop)}${stop.uncertain ? " · Zeitfolge unsicher" : ""}` };
    });
  }, [opening, data?.places]);
  const stopsById = useMemo(() => new Map(stops?.map((stop) => [stop.id, stop])), [stops]);
  const stopToName = stopsById.get(nameStopId) ?? stops?.[0];
  const placeIndex = data?.places;
  const points = useMemo(() => data?.points?.map((point) => ({
    ...point, placeName: showPlaceNames && placeIndex ? findNearbyPlace(placeIndex, point.latitude, point.longitude)?.name : undefined,
  })), [data?.points, placeIndex, showPlaceNames]);
  const legs = useMemo(() => opening.journey?.connections.flatMap((leg) => {
    const start = stopsById.get(leg.from_stop_id);
    const end = stopsById.get(leg.to_stop_id);
    return start && end ? [{ key: legKey(leg), start, end }] : [];
  }), [opening, stopsById]);
  const requestClose = () => {
    if (busy) return;
    if (dirty) setLeaveDesign("close");
    else onClose();
  };
  useModalFocus(dialog, requestClose, !busy);
  useModalFocus(confirmationDialog, () => setLeaveDesign(undefined), true, Boolean(leaveDesign));

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  useEffect(() => {
    const controller = new AbortController();
    setDataError("");
    setData(null);
    const timer = window.setTimeout(() => {
      const places = loadJourneyPlaces().catch(() => undefined);
      const dates = { from_date: opening.fromDate || undefined, to_date: opening.toDate || undefined, album_id: opening.albumId };
      const viewport = { ...queryBounds, grid_columns: 18, grid_rows: 12 };
      const mapPoints = opening.journey ? Promise.resolve(undefined) : opening.locationPhotoIds || opening.hasSelection || queryingCrop
        ? fetchSelectedMapPoints(viewport, { ...dates, image_ids: opening.locationPhotoIds,
            bounds: opening.hasSelection ? opening.bounds : queryingCrop ? bounds : undefined }, controller.signal)
        : fetchMapPoints({ ...viewport, ...dates }, controller.signal);
      void Promise.all([mapPoints, places]).then(([map, indexedPlaces]) => {
        if (!controller.signal.aborted) setData({ points: map?.points, places: indexedPlaces });
      }).catch((failure) => {
        if (!controller.signal.aborted) setDataError(failure instanceof Error ? failure.message : "Die Karte konnte nicht geladen werden.");
      });
    }, queryingCrop ? 180 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [opening, bounds, queryBounds, queryingCrop, retry]);

  useEffect(() => {
    if (!data || !canRender) return;
    let current = true;
    const timer = window.setTimeout(() => {
      const arrivals = new Map(legs?.map((leg) => [leg.end.id, transports[leg.key]]));
      void buildMapAlbumArtwork({
        format, dimensions, dpi, title: fileTitle, subtitle: subtitle.trim(), period: showPeriod ? albumPeriod(opening) : undefined,
        palette, accent, colors, formatting, style, showCountryNames: showNames, showStopsList, bounds, contextPercent, markerScale,
        fullBleed, framing, markerStyle, showPlaceNames, showRoute, points,
        stops: stops?.map((stop) => {
          const name = placeNames[stop.id]?.trim();
          return { ...stop, ...(name && { title: name, placeName: name, customPlaceName: true }), arrivalMode: arrivals.get(stop.id) };
        }),
        connections: opening.journey?.connections.map((leg) => ({ ...leg, transport: transports[legKey(leg)] })),
        fitJourney: Boolean(opening.journey && !useViewport), journeyPadding: opening.journeyDetail === "city" ? .014 : .09,
      }).then((pages) => {
        if (current) {
          setRenderError(undefined);
          setPageIndex((index) => Math.min(index, pages.length - 1));
          setRendered({ key: renderKey, opening, data, pages });
        }
      }).catch((failure) => {
        if (current) setRenderError({ key: renderKey, text: failure instanceof Error ? failure.message : "Die Vorschau konnte nicht erstellt werden." });
      });
    }, 120);
    return () => { current = false; window.clearTimeout(timer); };
  }, [data, canRender, fileTitle, format, dimensions, dpi, opening, palette, accent, colors, formatting, style,
    showNames, showPeriod, showStopsList, subtitle, renderKey, bounds, contextPercent, markerScale, stops, legs, transports, useViewport,
    fullBleed, framing, markerStyle, showPlaceNames, showRoute, points, placeNames]);

  useEffect(() => {
    if (!previewPage) return;
    const url = URL.createObjectURL(new Blob([previewPage.svg], { type: "image/svg+xml;charset=utf-8" }));
    setPreview({ page: previewPage, url });
    return () => URL.revokeObjectURL(url);
  }, [previewPage]);

  const save = async () => {
    if (!page || busy) return;
    setSaving("page");
    setSaveError("");
    setConfirmation("");
    try {
      const png = await mapAlbumPageToPng(page);
      if (!active.current) return;
      const filename = `${exportName}${page.total > 1 ? `-${String(page.number).padStart(2, "0")}` : ""}.png`;
      if (desktop?.save_album_png) {
        const data = await pngBase64(png);
        if (!active.current) return;
        const result = await desktop.save_album_png(filename, data);
        if (result.status === "error") throw new Error(result.message || "Das PNG konnte nicht gespeichert werden.");
        if (active.current && result.status === "saved") {
          if (!onSaveDraft && pages.length === 1) markDesignSaved();
          setConfirmation("PNG gespeichert.");
        }
      } else {
        downloadAlbumFile(png, filename);
        if (!onSaveDraft && pages.length === 1) markDesignSaved();
        setConfirmation("PNG erstellt.");
      }
    } catch (failure) {
      if (active.current) setSaveError(failure instanceof Error ? failure.message : "Das PNG konnte nicht gespeichert werden.");
    } finally {
      if (active.current) setSaving(undefined);
    }
  };

  const saveArchive = async () => {
    if (busy || pages.length < 2 || pages.length > MAX_ALBUM_ARCHIVE_PAGES) return;
    setSaving("archive");
    setSaveError("");
    setConfirmation("");
    try {
      const pngFiles: Blob[] = [];
      const nativePages: string[] = [];
      let bytes = 0;
      for (const [index, currentPage] of pages.entries()) {
        setArchivePage(index + 1);
        const png = await mapAlbumPageToPng(currentPage);
        if (!active.current) return;
        bytes += png.size;
        if (png.size > MAX_ALBUM_PAGE_BYTES || bytes > MAX_ALBUM_ARCHIVE_BYTES) {
          throw new Error("Das ZIP ist zu groß. Speichere die Seiten einzeln.");
        }
        if (desktop?.save_album_zip) nativePages.push(await pngBase64(png));
        else pngFiles.push(png);
      }
      if (!active.current) return;
      const filename = `${exportName}.zip`;
      if (desktop?.save_album_zip) {
        const result = await desktop.save_album_zip(filename, nativePages);
        if (result.status === "error") throw new Error(result.message || "Das ZIP konnte nicht gespeichert werden.");
        if (active.current && result.status === "saved") {
          if (!onSaveDraft) markDesignSaved();
          setConfirmation("ZIP gespeichert.");
        }
      } else {
        const archive = await archiveMapAlbumPages(pngFiles);
        if (!active.current) return;
        downloadAlbumFile(archive, filename);
        if (!onSaveDraft) markDesignSaved();
        setConfirmation("ZIP erstellt.");
      }
    } catch (failure) {
      if (active.current) setSaveError(failure instanceof Error ? failure.message : "Das ZIP konnte nicht gespeichert werden.");
    } finally {
      if (active.current) setSaving(undefined);
    }
  };

  const changeSizeUnit = (unit: "px" | "mm") => {
    if (unit === sizeUnit) return;
    const convert = (value: string, pixels: number) => value ? String(unit === "px" ? pixels : Number((pixels * MM_PER_INCH / dpi).toFixed(2))) : "";
    setWidth(convert(width, dimensions.width));
    setHeight(convert(height, dimensions.height));
    setSizeUnit(unit);
    setConfirmation("");
  };

  const saveDraft = async () => {
    if (!onSaveDraft || !canRender || busy) return;
    setSavingDraft(true);
    setSaveError("");
    setConfirmation("");
    try {
      await onSaveDraft({ ...currentDesign, title: fileTitle, subtitle: subtitle.trim() });
      if (active.current) {
        markDesignSaved();
        setConfirmation("Entwurf im Album gespeichert.");
      }
    } catch (failure) {
      if (active.current) setSaveError(failure instanceof Error ? failure.message : "Der Entwurf konnte nicht gespeichert werden.");
    } finally {
      if (active.current) setSavingDraft(false);
    }
  };

  const changeColor = (field: keyof AlbumCustomColors, value: string) => {
    setColors((previous) => ({ ...previous, [field]: value }));
    setConfirmation("");
  };

  const changeFormatting = <Key extends keyof AlbumFormatting,>(field: Key, value: AlbumFormatting[Key]) => {
    setFormatting((previous) => ({ ...previous, [field]: value }));
    setConfirmation("");
  };

  const updateFraming = (next: AlbumFraming) => {
    setFraming(next);
    setConfirmation("");
    setPageIndex(0);
  };
  const panFraming = (axis: "horizontal" | "vertical", direction: number) => {
    const step = Math.round(1800 / framing.zoom) * direction;
    updateFraming({ ...framing, [axis]: frameValue(framing[axis] + step, -200, 200, 0) });
  };
  const discardChanges = () => {
    if (leaveDesign === "close") { onClose(); return; }
    const initial = savedDesign.current.design;
    setTitle(initial.title);
    setSubtitle(initial.subtitle);
    setFormat(initial.format);
    setSizeUnit("px");
    setWidth(String(initial.dimensions.width));
    setHeight(String(initial.dimensions.height));
    setDpi(initial.dpi);
    setPalette(initial.palette);
    setAccent(initial.accent);
    setColors(initial.colors);
    setFormatting(initial.formatting);
    setContextPercent(initial.contextPercent);
    setMarkerScale(initial.markerScale);
    setUseViewport(initial.useViewport);
    setViewportSnapshot(initial.viewportSnapshot ?? opening.viewport);
    setTransports(initial.transports);
    setStyle(initial.style);
    setShowNames(initial.showNames);
    setShowPeriod(initial.showPeriod);
    setLayout(initial.fullBleed ? "bleed" : initial.showStopsList ? "list" : "map");
    setFraming(initial.framing);
    setMarkerStyle(initial.markerStyle);
    setShowPlaceNames(initial.showPlaceNames);
    setShowRoute(initial.showRoute);
    setPlaceNames(initial.placeNames);
    setLeaveDesign(undefined);
    setSaveError("");
    setConfirmation("Gestaltung zurückgesetzt.");
  };

  return <div className="gps-album-overlay">
    <section ref={dialog} className="gps-album-dialog" role="dialog" aria-modal="true" aria-labelledby="gps-album-title" aria-hidden={leaveDesign ? true : undefined}>
      <header className="gps-album-dialog__header">
        <h2 id="gps-album-title">Fotobuchbild erstellen</h2>
        <button type="button" data-initial-focus onClick={requestClose} disabled={busy}>Schließen</button>
      </header>
      <div className="gps-album-dialog__preview-area">
        {opening.scopeTitle && <p className="gps-album-dialog__feedback gps-album-dialog__scope">{opening.scopeTitle}</p>}
        <div className="gps-album-dialog__preview" aria-busy={updatingPreview}>
          {preview && preview.page === previewPage && <img src={preview.url} alt="Vorschau des Fotobuchbildes" />}
          {!preview && !updatingPreview && <p>Die Druckvorschau erscheint hier.</p>}
        </div>
        <div className="gps-album-dialog__preview-context">
          {previewCount > 1 && <div role="group" aria-label="Fotobuchseiten" className="gps-album-dialog__pages">
            <button type="button" aria-label="Vorherige Seite" disabled={busy || previewIndex === 0} onClick={() => {
              setPageIndex(previewIndex - 1); setConfirmation("");
            }}>‹</button>
            <span>Seite {previewIndex + 1} von {previewCount}</span>
            <button type="button" aria-label="Nächste Seite" disabled={busy || previewIndex >= previewCount - 1} onClick={() => {
              setPageIndex(previewIndex + 1); setConfirmation("");
            }}>›</button>
          </div>}
          {size && <span className="gps-album-dialog__dimensions">{millimeters.format(size.width / dpi * MM_PER_INCH)} × {millimeters.format(size.height / dpi * MM_PER_INCH)} mm · {dpi} dpi · {size.width} × {size.height} px</span>}
        </div>
        {updatingPreview && <p className="gps-album-dialog__feedback" role="status">{preview ? "Vorschau wird aktualisiert …" : "Vorschau wird erstellt …"}</p>}
        {!size && <p className="gps-album-dialog__feedback">Prüfe die Bildgröße.</p>}
        {invalidColor && <p className="gps-album-dialog__feedback">Prüfe die Hexcodes unter „Eigene Farben“.</p>}
        {page && <p className="gps-album-dialog__feedback" role="status">{page.visibleStopCount !== undefined
          ? `${countWithLabel(page.visibleStopCount, "Stopp", "Stopps")} im Kartenausschnitt`
          : `${countWithLabel(page.visiblePhotoCount ?? 0, "Foto", "Fotos")} im Kartenausschnitt`}</p>}
        {error && <p className="gps-album-dialog__feedback" role="alert">{error}{" "}<button type="button" onClick={() => {
          setData(null); setRenderError(undefined); setRetry((value) => value + 1);
        }}>Erneut versuchen</button></p>}
        {page?.satelliteFallback && <p className="gps-album-dialog__feedback" role="status">Satellitenbild nicht verfügbar. Die Standardkarte wird verwendet.</p>}
        {style === "satellite" && page?.number === 1 && !page.satelliteFallback &&
          <a className="gps-map__credit" href={satelliteSource.url} target="_blank" rel="noreferrer">{satelliteSource.name}</a>}
      </div>
      <div className="gps-album-dialog__editor">
        <fieldset className="gps-album-dialog__options" disabled={busy}>
          {!fullBleed && <label className="gps-album-dialog__title-field">Titel<input value={title} placeholder="Ohne Titel" maxLength={72} onChange={(event) => {
            setTitle(event.target.value); setConfirmation("");
          }} /></label>}
          <label><span id="gps-album-layout">Layout</span><select aria-labelledby="gps-album-layout" value={layout} onChange={(event) => {
            setLayout(event.target.value as AlbumLayout); setConfirmation(""); setPageIndex(0);
          }}><option value="map">Karte auf einer Seite</option><option value="bleed">Randlose Karte</option>
            {opening.journey && <option value="list">Karte mit Stoppliste</option>}</select></label>
          <label><span id="gps-album-format">Format</span><select aria-labelledby="gps-album-format" value={format} onChange={(event) => {
            const next = event.target.value as AlbumFormat;
            setFormat(next);
            if (next !== "custom" && !albumOutputSize(next, dpi)) setDpi(300);
            setConfirmation("");
          }}>
            {formatOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            {draft?.format === "square" && <option value="square">Quadratisch (25,4 × 25,4 cm)</option>}
          </select></label>
          {format === "custom" && <>
            <label>Einheit der Bildgröße<select value={sizeUnit} onChange={(event) => changeSizeUnit(event.target.value === "mm" ? "mm" : "px")}>
              <option value="px">Pixel (px)</option><option value="mm">Millimeter (mm)</option>
            </select></label>
            <label>Breite ({sizeUnit})<input type="number" inputMode={sizeUnit === "mm" ? "decimal" : "numeric"}
              min={sizeUnit === "px" ? 1200 : 0} max={sizeUnit === "px" ? 4800 : undefined} step={sizeUnit === "px" ? 1 : .01} value={width} onChange={(event) => {
              setWidth(event.target.value); setConfirmation("");
            }} aria-invalid={!size} aria-describedby={!size ? "gps-album-dimensions-error" : undefined} /></label>
            <label>Höhe ({sizeUnit})<input type="number" inputMode={sizeUnit === "mm" ? "decimal" : "numeric"}
              min={sizeUnit === "px" ? 1200 : 0} max={sizeUnit === "px" ? 4800 : undefined} step={sizeUnit === "px" ? 1 : .01} value={height} onChange={(event) => {
              setHeight(event.target.value); setConfirmation("");
            }} aria-invalid={!size} aria-describedby={!size ? "gps-album-dimensions-error" : undefined} /></label>
            {!size && <p role="alert" id="gps-album-dimensions-error" className="gps-album-dialog__field-error">
              {sizeUnit === "mm" ? `Wähle je Kante ${millimeters.format(1200 * MM_PER_INCH / dpi)} bis ${millimeters.format(4800 * MM_PER_INCH / dpi)} mm bei ${dpi} dpi.`
                : "Wähle je Kante 1200 bis 4800 px."} Verwende höchstens 16 Megapixel und ein Seitenverhältnis zwischen 1 zu 2 und 2 zu 1.
            </p>}
          </>}
          {canChangeArea && <label><span id="gps-album-area">Kartenbereich</span><select aria-labelledby="gps-album-area" value={useViewport ? "viewport" : "selection"} onChange={(event) => {
            const useVisibleArea = event.target.value === "viewport";
            if (useVisibleArea) setViewportSnapshot(opening.viewport);
            setUseViewport(useVisibleArea); updateFraming(defaultAlbumFraming);
          }}><option value="selection">{opening.journey ? "Ganze Auswahl einrahmen" : "Gewählter Bereich"}</option>
            <option value="viewport">Sichtbarer Kartenausschnitt</option></select></label>}
          <label><span id="gps-album-palette">Farbwelt</span><select aria-labelledby="gps-album-palette" value={palette} onChange={(event) => {
            setPalette(event.target.value === "coast" ? "coast" : "sand"); setConfirmation("");
          }}><option value="sand">Sand &amp; Salbei</option><option value="coast">Küste &amp; Schiefer</option></select></label>
        </fieldset>
        <details className="gps-album-dialog__details">
          <summary>Ausschnitt anpassen</summary>
          <fieldset disabled={busy} className="gps-album-dialog__framing">
            <p className="gps-album-dialog__feedback">Zoom und Pfeile ändern, welche {opening.journey ? "Stopps" : "Fotos"} im Bild sind.</p>
            <label className="gps-album-dialog__slider"><span>Zoom</span><input type="range" min={50} max={400} step={5} value={framing.zoom}
              aria-label="Zoom" aria-valuetext={`${framing.zoom} Prozent`}
              onChange={(event) => updateFraming({ ...framing, zoom: Number(event.target.value) })} />
              <span aria-hidden="true">{framing.zoom} %</span></label>
            <div className="gps-album-dialog__pan" role="group" aria-label="Ausschnitt verschieben">
              <button type="button" title="Nach links" aria-label="Ausschnitt nach links" onClick={() => panFraming("horizontal", -1)}>←</button>
              <button type="button" title="Nach rechts" aria-label="Ausschnitt nach rechts" onClick={() => panFraming("horizontal", 1)}>→</button>
              <button type="button" title="Nach oben" aria-label="Ausschnitt nach oben" onClick={() => panFraming("vertical", 1)}>↑</button>
              <button type="button" title="Nach unten" aria-label="Ausschnitt nach unten" onClick={() => panFraming("vertical", -1)}>↓</button>
            </div>
            <button type="button" disabled={!framingChanged} onClick={() => updateFraming(defaultAlbumFraming)}>Ausschnitt zurücksetzen</button>
          </fieldset>
        </details>
        <details className="gps-album-dialog__details">
          <summary tabIndex={0}>Bildgröße und Auflösung</summary>
          <fieldset disabled={busy}>
            <label><span id="gps-album-dpi">Druckauflösung</span><select aria-labelledby="gps-album-dpi" value={dpi} onChange={(event) => {
              setDpi(Number(event.target.value) as AlbumDpi); setConfirmation("");
            }}>
              {albumDensities.map((density) => <option key={density} value={density}
                disabled={format !== "custom" && !albumOutputSize(format, density)}>{density} dpi</option>)}
            </select></label>
            {format === "square-300" && <p className="gps-album-dialog__feedback">Für 30 × 30 cm stehen bis zu 300 dpi zur Wahl.</p>}
          </fieldset>
        </details>
        <details className="gps-album-dialog__details">
          <summary tabIndex={0}>Darstellung</summary>
          <fieldset disabled={busy}>
            <label><span id="gps-album-style">Kartenstil</span><select aria-labelledby="gps-album-style" value={style} onChange={(event) => {
              setStyle(event.target.value as MapStyle); setConfirmation("");
            }}><option value="standard">Standard</option><option value="landscape">Landschaft</option><option value="satellite">Satellit</option></select></label>
            <label><span id="gps-album-frame">Bildausschnitt</span><select aria-labelledby="gps-album-frame" value={contextPercent === 0 ? "close" : contextPercent === 52 ? "wide" : "custom"} onChange={(event) => {
              setContextPercent(event.target.value === "wide" ? 52 : event.target.value === "custom" ? 25 : 0); setConfirmation("");
            }}><option value="close">Eng einrahmen</option>
              <option value="wide">Mehr Umgebung</option><option value="custom">Individuell</option></select></label>
            <label className="gps-album-dialog__slider"><span>Umgebung</span><input type="range" min={0} max={100} value={contextPercent} onChange={(event) => {
              setContextPercent(Number(event.target.value)); setConfirmation("");
            }} aria-label="Umgebung" aria-valuetext={`${contextPercent} Prozent`} /><span aria-hidden="true">{contextPercent} %</span></label>
            {!showStopsList && <label>Markierungen<select value={markerStyle} onChange={(event) => {
              setMarkerStyle(event.target.value as AlbumMarkerStyle); setConfirmation("");
            }}><option value="standard">{opening.journey ? "Stopps und Anzahl bei Wiederbesuchen" : "Fotoanzahl"}</option>
              <option value="dots">Punkte</option><option value="none">Ausblenden</option></select></label>}
            <label className="gps-album-dialog__slider"><span>Markergröße</span><input type="range" min={70} max={140} value={markerScale} onChange={(event) => {
              setMarkerScale(Number(event.target.value)); setConfirmation("");
            }} disabled={!showStopsList && markerStyle === "none"} aria-label="Markergröße"
              aria-valuetext={`${markerScale} Prozent`} /><span aria-hidden="true">{markerScale} %</span></label>
            <label><span id="gps-album-accent">{opening.journey ? "Routenfarbe" : "Markerfarbe"}</span>
              <span className="gps-album-dialog__swatch" aria-hidden="true" style={{ backgroundColor: normalizeAlbumColor(colors.accent) ?? defaultColors.accent }} />
              <select aria-labelledby="gps-album-accent" value={accent} onChange={(event) => {
                setAccent(event.target.value as AlbumAccent); changeColor("accent", "");
              }}>{Object.entries(albumAccents).map(([value, { name }]) => <option key={value} value={value}>{name}</option>)}</select></label>
            <label><input type="checkbox" checked={showNames} onChange={(event) => {
              setShowNames(event.target.checked); setConfirmation("");
            }} /> Ländernamen</label>
            {!showStopsList && <label><input type="checkbox" checked={showPlaceNames} disabled={markerStyle === "none"} onChange={(event) => {
              setShowPlaceNames(event.target.checked); setConfirmation("");
            }} /> Ortsnamen an Markierungen</label>}
            {opening.journey && <label><input type="checkbox" checked={showRoute} onChange={(event) => {
              setShowRoute(event.target.checked); setConfirmation("");
            }} /> Verbindungslinien</label>}
            {!fullBleed && <label><input type="checkbox" checked={showPeriod} onChange={(event) => {
              setShowPeriod(event.target.checked); setConfirmation("");
            }} /> Zeitraum im Bild</label>}
          </fieldset>
        </details>
        <details className="gps-album-dialog__details gps-album-dialog__colors">
          <summary tabIndex={0}>Eigene Farben</summary>
          <fieldset disabled={busy}>
            {availableColorFields.map(([field, label]) => {
              const draft = colors[field] ?? "";
              const selected = normalizeAlbumColor(draft);
              return <div className="gps-album-dialog__color" key={field}>
                <span>{label}</span>
                <input type="color" aria-label={`${label} auswählen`} value={selected ?? defaultColors[field]}
                  onChange={(event) => changeColor(field, event.target.value)} />
                <input type="text" aria-label={`${label} als Hexcode`} value={draft} maxLength={7}
                  placeholder={defaultColors[field]} autoComplete="off" spellCheck={false}
                  aria-invalid={Boolean(draft.trim() && !selected)}
                  aria-describedby={invalidColor ? "gps-album-colors-error" : undefined}
                  onChange={(event) => changeColor(field, event.target.value)} />
              </div>;
            })}
            {invalidColor && <p role="alert" id="gps-album-colors-error" className="gps-album-dialog__field-error">
              Gib einen Hexcode mit drei oder sechs Stellen ein, etwa #3B91A8.
            </p>}
            <button type="button" disabled={!Object.values(colors).some(Boolean)} onClick={() => {
              setColors({}); setConfirmation("");
            }}>Eigene Farben zurücksetzen</button>
          </fieldset>
        </details>
        {!fullBleed && <details className="gps-album-dialog__details">
          <summary tabIndex={0}>Schrift und Layout</summary>
          <fieldset disabled={busy}>
            <label>Unterzeile (optional)<input value={subtitle} maxLength={100} onChange={(event) => {
              setSubtitle(event.target.value); setConfirmation("");
            }} /></label>
            <label>Schrift<select aria-label="Schrift" value={formatting.typeface ?? "serif"} onChange={(event) => {
              changeFormatting("typeface", event.target.value === "sans" ? "sans" : "serif");
            }}><option value="serif">Klassisch (Serifen)</option><option value="sans">Modern (serifenlos)</option></select></label>
            <label>Titelausrichtung<select aria-label="Titelausrichtung" value={formatting.alignment ?? "left"} onChange={(event) => {
              changeFormatting("alignment", event.target.value === "center" ? "center" : "left");
            }}><option value="left">Links</option><option value="center">Zentriert</option></select></label>
            <label className="gps-album-dialog__slider"><span>Titelgröße</span><input type="range" min={75} max={130}
              value={formatting.titleScale ?? 100} aria-label="Titelgröße" aria-valuetext={`${formatting.titleScale ?? 100} Prozent`}
              onChange={(event) => changeFormatting("titleScale", Number(event.target.value))} />
              <span aria-hidden="true">{formatting.titleScale ?? 100} %</span></label>
            <label>Seitenränder<select aria-label="Seitenränder" value={formatting.margins ?? "normal"} onChange={(event) => {
              changeFormatting("margins", event.target.value as AlbumFormatting["margins"]);
            }}><option value="narrow">Schmal</option><option value="normal">Normal</option><option value="wide">Breit</option></select></label>
            {opening.journey && showStopsList && <label>Spalten der Stoppliste<select aria-label="Spalten der Stoppliste" value={formatting.listColumns ?? 2} onChange={(event) => {
              changeFormatting("listColumns", event.target.value === "1" ? 1 : 2);
            }}><option value="1">Eine Spalte</option><option value="2">Zwei Spalten</option></select></label>}
          </fieldset>
        </details>}
        {stopToName && <details className="gps-album-dialog__details">
          <summary>Stopps beschriften</summary>
          <fieldset disabled={busy} className="gps-album-dialog__stop-names">
            <label>Stopp<select value={stopToName.id} onChange={(event) => setNameStopId(event.target.value)}>
              {stops?.map((stop) => <option key={stop.id} value={stop.id}>{stop.number}. {stop.title}</option>)}
            </select></label>
            <label>Eigener Ortsname<input value={placeNames[stopToName.id] ?? ""} maxLength={60}
              placeholder={stopToName.placeName ?? stopToName.title} onChange={(event) => {
                const value = event.target.value;
                setPlaceNames((names) => {
                  const next = { ...names };
                  if (value) next[stopToName.id] = value;
                  else delete next[stopToName.id];
                  return next;
                });
                setConfirmation("");
              }} /></label>
          </fieldset>
        </details>}
        {Boolean(legs?.length) && <details className="gps-album-dialog__details gps-album-dialog__route">
          <summary tabIndex={0}>Route gestalten</summary>
          <p>Verkehrsmittel pro Etappe wählen. Vorschläge beruhen auf Luftlinie und Aufnahmezeiten.</p>
          <fieldset disabled={busy} aria-label="Verkehrsmittel pro Etappe">
            {legs?.map(({ key, start, end }) => {
              const controlId = `gps-album-transport-${key}`;
              const hint = !transports[key] ? suggestJourneyTransport(start, end) : undefined;
              const changeTransport = (mode?: AlbumTransport) => {
                setTransports((before) => ({ ...before, [key]: mode }));
                setConfirmation("");
              };
              return <div key={key}><label>
              <span id={`gps-album-leg-${key}`}>{start.number}. {start.title} → {end.number}. {end.title}</span>
              <select id={controlId} aria-labelledby={`gps-album-leg-${key}`} value={transports[key] ?? ""} onChange={(event) => {
                changeTransport(event.target.value ? event.target.value as AlbumTransport : undefined);
              }}><option value="">Nicht angegeben</option>
                {Object.entries(albumTransports).map(([value, { label }]) => <option key={value} value={value}>{label}</option>)}
              </select>
              </label>
              {hint && <p className="gps-album-dialog__transport-hint">
                {hint === "road-or-rail" ? "Auto oder Zug möglich" : `${albumTransports[hint].label} möglich`}
                {hint !== "road-or-rail" && <button type="button" onClick={() => {
                  changeTransport(hint);
                  document.getElementById(controlId)?.focus();
                }}
                  aria-label={`${albumTransports[hint].label} für Etappe ${start.number} bis ${end.number} übernehmen`}>Übernehmen</button>}
              </p>}
              </div>;
            })}
          </fieldset>
        </details>}
      </div>
      <footer className="gps-album-dialog__footer">
        <p className="gps-album-dialog__usage">Füge das PNG in deinem Fotobuchprogramm als Foto ein.</p>
        {saveError && <span role="alert">{saveError}</span>}
        {confirmation && <span role="status">{confirmation}</span>}
        {saving === "archive" && <span role="status">Seite {archivePage} von {pages.length} wird für das ZIP vorbereitet.</span>}
        {previewCount > MAX_ALBUM_ARCHIVE_PAGES && <span>Mehr als {MAX_ALBUM_ARCHIVE_PAGES} Seiten lassen sich einzeln als PNG speichern.</span>}
        <button type="button" disabled={!dirty || busy} onClick={() => setLeaveDesign("reset")}>Gestaltung zurücksetzen</button>
        {onSaveDraft && <button type="button" disabled={!canRender || busy} onClick={() => void saveDraft()}>
          {savingDraft ? "Entwurf wird gespeichert …" : "Entwurf im Album speichern"}
        </button>}
        <button type="button" className={previewCount < 2 ? "gps-album-dialog__save" : undefined} disabled={!page || busy} onClick={() => void save()}>
          {saving === "page" ? "PNG wird erstellt …" : previewCount > 1 ? "Diese Seite als PNG speichern" : "PNG speichern"}
        </button>
        {previewCount > 1 && previewCount <= MAX_ALBUM_ARCHIVE_PAGES && <button type="button" className="gps-album-dialog__save"
          disabled={!page || busy} onClick={() => void saveArchive()}>{saving === "archive" ? "ZIP wird erstellt …" : "Alle Seiten als ZIP speichern"}</button>}
      </footer>
    </section>
    {leaveDesign && <div className="gps-album-overlay gps-album-confirm-overlay">
      <section className="gps-album-confirm" ref={confirmationDialog} role="dialog" aria-modal="true" aria-labelledby="gps-album-confirm-title">
        <h2 id="gps-album-confirm-title">Änderungen am Fotobuch verwerfen?</h2>
        <p>{leaveDesign === "close" ? "Deine Änderungen an der Gestaltung gehen verloren." : "Die Gestaltung kehrt zu ihrem letzten gespeicherten Stand zurück."}</p>
        <div>
          <button type="button" className="secondary-button" data-initial-focus onClick={() => setLeaveDesign(undefined)}>Weiter bearbeiten</button>
          <button type="button" className="secondary-button" onClick={discardChanges}>Entwurf verwerfen</button>
        </div>
      </section>
    </div>}
  </div>;
}
