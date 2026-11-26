import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MapPoint } from "../../utils/api";
import borders from "./world-borders.json";
import countries from "./world-countries.json";
import land from "./world-land.json";
import overviewWater from "./world-water-overview.json";

type DetailedCartography = typeof import("./world-detail.json");

export interface MapBounds {
  west: number;
  east: number;
  south: number;
  north: number;
}

export interface MapView {
  longitude: number;
  latitude: number;
  zoom: number;
}

export interface MapGrid {
  columns: number;
  rows: number;
}

export type MapStyle = "standard" | "landscape";

interface GeographicPosition {
  longitude: number;
  latitude: number;
}

export interface JourneyStop extends GeographicPosition {
  id: string;
  number: number;
}

export interface JourneyConnection {
  from: GeographicPosition;
  to: GeographicPosition;
}

export const countryLabels = countries;

interface MapSize {
  width: number;
  height: number;
}

const WIDTH = 1000;
const HEIGHT = 500;
const MIN_SELECTION_PIXELS = 8;
const MIN_PAN_PIXELS = 6;
const MARKER_HIT_RADIUS = 22;
const MARKER_CELL_PADDING = MARKER_HIT_RADIUS + 1;
const MIN_CELL_SIZE = MARKER_CELL_PADDING * 2;
const MAX_GRID_SIZE = 32;
const DETAIL_ZOOM = 2.25;
const JOURNEY_STOP_RADIUS = 14;
const projectX = (longitude: number) => ((longitude + 180) / 360) * WIDTH;
const projectY = (latitude: number) => ((90 - latitude) / 180) * HEIGHT;
const unprojectX = (x: number) => (x / WIDTH) * 360 - 180;
const unprojectY = (y: number) => 90 - (y / HEIGHT) * 180;
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function boundsForView(view: MapView): MapBounds {
  const width = WIDTH / view.zoom;
  const height = HEIGHT / view.zoom;
  const x = clamp(projectX(view.longitude), width / 2, WIDTH - width / 2);
  const y = clamp(projectY(view.latitude), height / 2, HEIGHT - height / 2);
  return {
    west: unprojectX(x - width / 2),
    east: unprojectX(x + width / 2),
    north: unprojectY(y - height / 2),
    south: unprojectY(y + height / 2),
  };
}

export function normalizedView(view: MapView): MapView {
  const zoom = clamp(view.zoom, 1, 128);
  const width = WIDTH / zoom;
  const height = HEIGHT / zoom;
  return {
    zoom,
    longitude: unprojectX(clamp(projectX(view.longitude), width / 2, WIDTH - width / 2)),
    latitude: unprojectY(clamp(projectY(view.latitude), height / 2, HEIGHT - height / 2)),
  };
}

interface Point {
  x: number;
  y: number;
}

interface LabelCandidate extends GeographicPosition {
  id: string;
  name: string;
  kind: "country" | "city";
  minZoom: number;
  priority: number;
}

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

function viewportPoint(position: GeographicPosition, bounds: MapBounds, size: MapSize): Point {
  return {
    x: ((position.longitude - bounds.west) / (bounds.east - bounds.west)) * size.width,
    y: ((bounds.north - position.latitude) / (bounds.north - bounds.south)) * size.height,
  };
}

export function layoutMapLabels(
  candidates: readonly LabelCandidate[],
  zoom: number,
  bounds: MapBounds,
  size: MapSize,
  markers: readonly Point[],
) {
  const occupied: Box[] = markers.map(({ x, y }) => ({
    left: x - MARKER_HIT_RADIUS, right: x + MARKER_HIT_RADIUS,
    top: y - MARKER_HIT_RADIUS, bottom: y + MARKER_HIT_RADIUS,
  }));
  const labels = candidates.filter((label) => label.minZoom <= zoom &&
    label.longitude >= bounds.west && label.longitude <= bounds.east &&
    label.latitude >= bounds.south && label.latitude <= bounds.north).sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === "country" ? -1 : 1;
      return right.priority - left.priority;
    });

  const visible: Array<LabelCandidate & Point> = [];
  for (const label of labels) {
    const { x, y } = viewportPoint(label, bounds, size);
    const textWidth = label.name.length * (label.kind === "country" ? 7.4 : 6.5);
    const [left, right] = label.kind === "country"
      ? [x - textWidth / 2 - 4, x + textWidth / 2 + 4]
      : [x - 3, x + textWidth + 11];
    const box = { left, right, top: y - 9, bottom: y + 9 };
    if (box.left < 3 || box.right > size.width - 3 || box.top < 3 || box.bottom > size.height - 3) continue;
    if (occupied.some((other) => box.left < other.right && box.right > other.left &&
      box.top < other.bottom && box.bottom > other.top)) continue;
    occupied.push(box);
    visible.push({ ...label, x, y });
  }
  return visible;
}

export function journeyConnectionSegments({ from, to }: JourneyConnection): JourneyConnection[] {
  const difference = to.longitude - from.longitude;
  if (Math.abs(difference) <= 180) return [{ from, to }];
  const edge = difference < 0 ? 180 : -180;
  const destination = to.longitude + 2 * edge;
  if (destination === from.longitude) return [
    { from, to: { ...to, longitude: from.longitude } },
    { from: { ...from, longitude: to.longitude }, to },
  ];
  const fraction = (edge - from.longitude) / (destination - from.longitude);
  const latitude = from.latitude + fraction * (to.latitude - from.latitude);
  return [
    { from, to: { longitude: edge, latitude } },
    { from: { longitude: -edge, latitude }, to },
  ];
}

export function gridForSize({ width, height }: MapSize): MapGrid {
  return {
    columns: clamp(Math.floor(width / MIN_CELL_SIZE), 1, MAX_GRID_SIZE),
    rows: clamp(Math.floor(height / MIN_CELL_SIZE), 1, MAX_GRID_SIZE),
  };
}

export function markerPixelPosition(
  marker: Pick<MapPoint, "longitude" | "latitude" | "x" | "y">,
  bounds: MapBounds,
  size: MapSize,
  grid: MapGrid,
): Point {
  const cellWidth = size.width / grid.columns;
  const cellHeight = size.height / grid.rows;
  const withinCell = (value: number, origin: number, length: number) => {
    const padding = Math.min(MARKER_CELL_PADDING, length / 2);
    return clamp(value, origin + padding, origin + length - padding);
  };
  const { x, y } = viewportPoint(marker, bounds, size);
  // SQLite counts rows from the south, whereas the SVG starts at the top.
  const row = grid.rows - 1 - marker.y;
  return {
    x: withinCell(x, marker.x * cellWidth, cellWidth),
    y: withinCell(y, row * cellHeight, cellHeight),
  };
}

/** Convert two positions in the map canvas (fractions of its size) into geographic bounds. */
export function boundsForDrag(bounds: MapBounds, start: Point, end: Point): MapBounds {
  const longitude = (x: number) => bounds.west + clamp(x, 0, 1) * (bounds.east - bounds.west);
  const latitude = (y: number) => bounds.north - clamp(y, 0, 1) * (bounds.north - bounds.south);
  return {
    west: longitude(Math.min(start.x, end.x)),
    east: longitude(Math.max(start.x, end.x)),
    north: latitude(Math.min(start.y, end.y)),
    south: latitude(Math.max(start.y, end.y)),
  };
}

function projectBounds(bounds: MapBounds) {
  return {
    x: projectX(bounds.west), y: projectY(bounds.north),
    width: projectX(bounds.east) - projectX(bounds.west),
    height: projectY(bounds.south) - projectY(bounds.north),
  };
}

interface Gesture {
  pointerId: number;
  captureTarget: Element;
  start: Point;
  bounds: MapBounds;
  view: MapView;
  selecting: boolean;
  dragging: boolean;
}

function pointerPosition(event: React.PointerEvent<SVGSVGElement>) {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    position: { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height },
    rect,
  };
}

function viewAfterPan(gesture: Gesture, position: Point) {
  return normalizedView({
    ...gesture.view,
    longitude: gesture.view.longitude - (position.x - gesture.start.x) * (360 / gesture.view.zoom),
    latitude: gesture.view.latitude + (position.y - gesture.start.y) * (180 / gesture.view.zoom),
  });
}

interface Props {
  view: MapView;
  onViewChange: (view: MapView) => void;
  points: MapPoint[];
  grid: MapGrid | null;
  onGridChange: (grid: MapGrid) => void;
  selection: MapBounds | null;
  onSelectionChange: (bounds: MapBounds | null) => void;
  onMarkerClick: (marker: MapPoint) => void;
  selectionMode: boolean;
  onCancelSelection: () => void;
  style?: MapStyle;
  showCountryNames?: boolean;
  journeyStops?: readonly JourneyStop[];
  journeyConnections?: readonly JourneyConnection[];
  activeJourneyStop?: string;
  onJourneyStopClick?: (id: string) => void;
}

function projectLine(positions: number[][]) {
  return positions.map(([longitude, latitude], index) =>
    `${index ? "L" : "M"}${projectX(longitude).toFixed(2)},${projectY(latitude).toFixed(2)}`,
  ).join(" ");
}

const landPaths = land.map((ring) => projectLine(ring) + "Z").join(" ");
const countryBorders = borders.international.map(projectLine).join(" ");
const otherBorders = borders.other.map(projectLine).join(" ");
const coordinates = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 4 });
const countryCandidates: LabelCandidate[] = countryLabels.map((label) => ({ ...label, name: label.label, id: label.code, kind: "country" }));
const noJourneyStops: readonly JourneyStop[] = [];
const noJourneyConnections: readonly JourneyConnection[] = [];

const WorldMap: React.FC<Props> = ({
  view, onViewChange, points, grid, onGridChange, selection, onSelectionChange, onMarkerClick,
  selectionMode, onCancelSelection, style = "standard", showCountryNames = false,
  journeyStops = noJourneyStops, journeyConnections = noJourneyConnections, activeJourneyStop, onJourneyStopClick,
}) => {
  const landClipId = `gps-map-land-${useId()}`;
  const landscape = style === "landscape";
  const detailed = view.zoom >= DETAIL_ZOOM;
  const requiresDetailedMap = landscape || detailed;
  const [detail, setDetail] = useState<DetailedCartography | null>(null);
  useEffect(() => {
    if (!requiresDetailedMap || detail) return;
    let cancelled = false;
    void import("./world-detail.json").then(({ default: data }) => {
      if (!cancelled) setDetail(data);
    }).catch(() => {
      // The overview remains usable if the detailed cartography cannot load.
    });
    return () => { cancelled = true; };
  }, [detail, requiresDetailedMap]);
  const cityCandidates = useMemo<LabelCandidate[]>(() => detail?.cities.map((label) => ({
    ...label, id: `city-${label.id}`, kind: "city",
  })) ?? [], [detail]);
  const svgRef = useRef<SVGSVGElement>(null);
  const focusedMarkerRef = useRef<Element | null>(null);
  const [viewportSize, setViewportSize] = useState({ width: WIDTH, height: HEIGHT });
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const measure = (width: number, height: number) => {
      // Hidden tabs report zero; retain the last useful size until shown again.
      if (width <= 0 || height <= 0) return;
      setViewportSize((current) =>
        current.width === width && current.height === height ? current : { width, height });
      onGridChange(gridForSize({ width, height }));
    };
    const size = svg.getBoundingClientRect();
    measure(size.width, size.height);
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width, entry.contentRect.height));
    observer.observe(svg);
    return () => observer.disconnect();
  }, [onGridChange]);
  const gestureRef = useRef<Gesture | null>(null);
  const suppressMarkerClick = useRef(false);
  const [dragBounds, setDragBounds] = useState<MapBounds | null>(null);
  const [previewView, setPreviewView] = useState<MapView | null>(null);
  const bounds = useMemo(() => boundsForView(view), [view]);
  const displayBounds = previewView ? boundsForView(previewView) : bounds;
  const markerViewBox = projectBounds(bounds);
  const vb = previewView ? projectBounds(displayBounds) : markerViewBox;
  // Marker geometry is in CSS pixels, independent of zoom and screen size.
  const scaleX = markerViewBox.width / viewportSize.width;
  const scaleY = markerViewBox.height / viewportSize.height;
  const markers = grid ? points.map((marker) => {
    const position = markerPixelPosition(marker, bounds, viewportSize, grid);
    return { marker, x: markerViewBox.x + position.x * scaleX, y: markerViewBox.y + position.y * scaleY };
  }) : [];
  const zoom = view.zoom;
  const coastPath = requiresDetailedMap && detail ? detail.land : landPaths;
  const rivers = [...overviewWater.rivers, ...(detail?.rivers ?? [])];
  const lakes = [...overviewWater.lakes, ...(detail?.lakes ?? [])];
  // A later visit can occupy the same pixels as an earlier one. Give the
  // selected marker visual and pointer priority at an overlapping location.
  const chosenStop = journeyStops.find((stop) => stop.id === activeJourneyStop);
  const orderedStops = chosenStop
    ? [...journeyStops.filter((stop) => stop.id !== chosenStop.id), chosenStop]
    : journeyStops;
  const stopPositions = orderedStops.map((stop) => ({ stop, x: projectX(stop.longitude), y: projectY(stop.latitude) }));
  const journeySegments = useMemo(() => journeyConnections.flatMap(journeyConnectionSegments), [journeyConnections]);
  const labels = showCountryNames || zoom >= 4 ? layoutMapLabels(
    showCountryNames ? [...countryCandidates, ...cityCandidates] : cityCandidates, zoom, displayBounds, viewportSize,
    [...markers, ...stopPositions].map(({ x, y }) => ({ x: (x - vb.x) / scaleX, y: (y - vb.y) / scaleY })),
  ) : [];

  useLayoutEffect(() => {
    const marker = focusedMarkerRef.current;
    if (!marker || marker.isConnected) return;
    focusedMarkerRef.current = null;
    const svg = svgRef.current;
    if (svg?.getClientRects().length && document.activeElement === document.body && document.hasFocus()) {
      svg.focus({ preventScroll: true });
    }
  }, [journeyStops, points, view]);

  useLayoutEffect(() => {
    const gesture = gestureRef.current;
    if (!gesture || (gesture.selecting === selectionMode && gesture.view === view && gesture.captureTarget.isConnected)) return;
    gestureRef.current = null;
    if (gesture.captureTarget.hasPointerCapture(gesture.pointerId)) {
      gesture.captureTarget.releasePointerCapture(gesture.pointerId);
    }
    setDragBounds(null);
    setPreviewView(null);
  }, [selectionMode, view, points, journeyStops]);

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 || gestureRef.current) return;
    const target = event.target as Element;
    // Keep marker taps on the element the pointer originally hit.
    const captureTarget = !selectionMode && target.closest(".gps-map__marker") ? target : event.currentTarget;
    const { position } = pointerPosition(event);
    suppressMarkerClick.current = false;
    gestureRef.current = {
      pointerId: event.pointerId, captureTarget, start: position, bounds, view,
      selecting: selectionMode, dragging: false,
    };
    captureTarget.setPointerCapture(event.pointerId);
    if (selectionMode) setDragBounds(boundsForDrag(bounds, position, position));
  };

  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (gesture?.pointerId !== event.pointerId) return;
    const { position, rect } = pointerPosition(event);
    if (gesture.selecting) {
      setDragBounds(boundsForDrag(gesture.bounds, gesture.start, position));
      return;
    }
    const distance = Math.hypot((position.x - gesture.start.x) * rect.width, (position.y - gesture.start.y) * rect.height);
    if (!gesture.dragging && distance < MIN_PAN_PIXELS) return;
    gesture.dragging = true;
    suppressMarkerClick.current = true;
    setPreviewView(viewAfterPan(gesture, position));
  };

  const finishGesture = (pointerId: number) => {
    const gesture = gestureRef.current;
    if (gesture?.pointerId !== pointerId) return;
    gestureRef.current = null;
    if (gesture.captureTarget.hasPointerCapture(pointerId)) gesture.captureTarget.releasePointerCapture(pointerId);
    setDragBounds(null);
    setPreviewView(null);
    return gesture;
  };

  const handlePointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    const gesture = finishGesture(event.pointerId);
    if (!gesture || gesture.selecting !== selectionMode) return;
    const { position, rect } = pointerPosition(event);
    if (!gesture.selecting) {
      if (gesture.dragging) onViewChange(viewAfterPan(gesture, position));
      return;
    }
    const width = Math.abs(clamp(position.x, 0, 1) - gesture.start.x) * rect.width;
    const height = Math.abs(clamp(position.y, 0, 1) - gesture.start.y) * rect.height;
    if (width < MIN_SELECTION_PIXELS || height < MIN_SELECTION_PIXELS) return;
    onSelectionChange(boundsForDrag(gesture.bounds, gesture.start, position));
  };

  const cancelGesture = (event: React.PointerEvent<SVGSVGElement>) => {
    finishGesture(event.pointerId);
  };

  const pan = (dx: number, dy: number) => onViewChange(normalizedView({
    ...view, longitude: view.longitude + dx * (360 / view.zoom), latitude: view.latitude + dy * (180 / view.zoom),
  }));

  const selectionRect = selection && projectBounds(selection);
  const activeRect = selectionMode && dragBounds && projectBounds(dragBounds);
  const markerInteractions = (activate: () => void) => ({
    onPointerUp: (event: React.PointerEvent<SVGGElement>) => {
      const gesture = gestureRef.current;
      if (event.pointerType !== "touch" || selectionMode || gesture?.pointerId !== event.pointerId ||
        gesture.dragging || !event.currentTarget.contains(gesture.captureTarget)) return;
      // A browser can skip its synthetic click after a touch pan.
      suppressMarkerClick.current = true;
      activate();
    },
    onClick: (event: React.MouseEvent<SVGGElement>) => {
      if (!selectionMode && (event.detail === 0 || !suppressMarkerClick.current)) activate();
    },
    onKeyDown: (event: React.KeyboardEvent<SVGGElement>) => {
      if (selectionMode || event.repeat) return;
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); }
    },
  });

  return (
    <div className="gps-map__viewport">
      <div className="gps-map__controls" role="group" aria-label="Kartensteuerung">
        <button type="button" aria-label="Vergrößern" title="Vergrößern" disabled={view.zoom >= 128} onClick={() => onViewChange(normalizedView({ ...view, zoom: view.zoom * 1.5 }))}>+</button>
        <button type="button" aria-label="Verkleinern" title="Verkleinern" disabled={view.zoom <= 1} onClick={() => onViewChange(normalizedView({ ...view, zoom: view.zoom / 1.5 }))}>−</button>
        <button type="button" aria-label="Nach Norden verschieben" title="Nach Norden" onClick={() => pan(0, 0.2)}>↑</button>
        <button type="button" aria-label="Nach Westen verschieben" title="Nach Westen" onClick={() => pan(-0.2, 0)}>←</button>
        <button type="button" aria-label="Nach Osten verschieben" title="Nach Osten" onClick={() => pan(0.2, 0)}>→</button>
        <button type="button" aria-label="Nach Süden verschieben" title="Nach Süden" onClick={() => pan(0, -0.2)}>↓</button>
        <button type="button" aria-label="Weltkarte anzeigen" title="Weltkarte anzeigen" onClick={() => onViewChange({ longitude: 0, latitude: 0, zoom: 1 })}>⌖</button>
      </div>
      <div className="gps-map__canvas">
      <svg ref={svgRef} className={`gps-map__svg${selectionMode ? " gps-map__svg--select" : ""}${landscape ? " gps-map__svg--landscape" : ""}`}
        viewBox={`${vb.x} ${vb.y} ${vb.width} ${vb.height}`} preserveAspectRatio="none"
        role="group" aria-label="Weltkarte mit Fotostandorten" tabIndex={0}
        aria-describedby="gps-map-keyboard-hint"
        onFocus={(event) => { focusedMarkerRef.current = (event.target as Element).closest(".gps-map__marker"); }}
        onBlur={(event) => { if (event.relatedTarget) focusedMarkerRef.current = null; }}
        onKeyDown={(event) => {
          if (selectionMode && event.key === "Escape") {
            event.preventDefault();
            onCancelSelection();
            return;
          }
          if (event.target !== event.currentTarget) return;
          if (selectionMode && event.key === "Enter") {
            event.preventDefault();
            onSelectionChange(bounds);
            return;
          }
          const directions: Record<string, [number, number]> = { ArrowUp: [0, 0.2], ArrowDown: [0, -0.2], ArrowLeft: [-0.2, 0], ArrowRight: [0.2, 0] };
          const direction = directions[event.key];
          if (direction) { event.preventDefault(); pan(...direction); }
          if (event.key === "+" || event.key === "-") {
            event.preventDefault();
            onViewChange(normalizedView({ ...view, zoom: view.zoom * (event.key === "+" ? 1.5 : 1 / 1.5) }));
          }
        }}
        onPointerDown={handlePointerDown} onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp} onPointerCancel={cancelGesture}
        onLostPointerCapture={(event) => {
          if (event.target === gestureRef.current?.captureTarget) cancelGesture(event);
        }}>
        <desc id="gps-map-keyboard-hint">{selectionMode
          ? "Mit den Pfeiltasten verschieben, mit Plus und Minus zoomen. Enter übernimmt den sichtbaren Bereich, Escape beendet die Auswahl."
          : "Mit den Pfeiltasten verschieben, mit Plus und Minus zoomen. Mit Tab einen Fotostandort wählen und mit Enter öffnen."}</desc>
        <rect width={WIDTH} height={HEIGHT} className="gps-map__sea" />
        {[0, 1, 2, 3, 4, 5].map((line) => <line key={`lat-${line}`} x1={0} x2={WIDTH} y1={line * 100} y2={line * 100} className="gps-map__graticule" />)}
        {[0, 1, 2, 3, 4, 5, 6].map((line) => <line key={`lon-${line}`} y1={0} y2={HEIGHT} x1={line * (WIDTH / 6)} x2={line * (WIDTH / 6)} className="gps-map__graticule" />)}
        <path d={coastPath} className="gps-map__land" fillRule="evenodd" />
        {landscape && <g aria-hidden="true" className="gps-map__relief">
          <defs><clipPath id={landClipId}><path d={coastPath} clipRule="evenodd" /></clipPath></defs>
          <image href="./map/world-relief.webp" width={WIDTH} height={HEIGHT} preserveAspectRatio="none" clipPath={`url(#${landClipId})`} />
        </g>}
        <g aria-hidden="true" className="gps-map__water">
          {rivers.filter((layer) => layer.minZoom <= zoom).map((layer) =>
            <path key={layer.minZoom} d={layer.path} className="gps-map__river" />)}
          {lakes.filter((layer) => layer.minZoom <= zoom).map((layer) =>
            <path key={layer.minZoom} d={layer.path} className="gps-map__lake" fillRule="evenodd" />)}
        </g>
        <g aria-hidden="true" className="gps-map__borders">
          <path d={detailed && detail ? detail.borders.international : countryBorders} className="gps-map__border" />
          <path d={detailed && detail ? detail.borders.other : otherBorders} className="gps-map__border gps-map__border--other" />
        </g>
        <g aria-hidden="true" className="gps-map__labels">
          {labels.map(({ id, name, kind, x, y }) => <g key={id}
            className={`gps-map__place-label gps-map__place-label--${kind}`}
            transform={`translate(${vb.x + x * scaleX} ${vb.y + y * scaleY}) scale(${scaleX} ${scaleY})`}>
            {kind === "city" && <circle r={2} />}
            <text x={kind === "city" ? 6 : 0} textAnchor={kind === "city" ? "start" : "middle"} dominantBaseline="central">{name}</text>
          </g>)}
        </g>
        {journeySegments.length > 0 && <g aria-hidden="true" className="gps-map__journey-route">
          {journeySegments.map(({ from, to }, index) => {
            const x1 = projectX(from.longitude), x2 = projectX(to.longitude);
            const y1 = projectY(from.latitude), y2 = projectY(to.latitude);
            const dx = (x2 - x1) / scaleX, dy = (y2 - y1) / scaleY;
            const angle = Math.atan2(dy, dx) * 180 / Math.PI;
            return <g key={index}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} className="gps-map__journey-line-outline" />
              <line x1={x1} y1={y1} x2={x2} y2={y2} className="gps-map__journey-line" />
              {Math.hypot(dx, dy) >= 42 && <path d="M-4,-4 L3,0 L-4,4" className="gps-map__journey-direction"
                transform={`translate(${x1 + (x2 - x1) * .62} ${y1 + (y2 - y1) * .62}) scale(${scaleX} ${scaleY}) rotate(${angle})`} />}
            </g>;
          })}
        </g>}
        {selectionRect && <rect {...selectionRect} className="gps-map__selection" />}
        {activeRect && <rect {...activeRect} className="gps-map__selection gps-map__selection--drag" />}
        {grid && markers.map(({ marker, x, y }) => {
          const radius = clamp(11 + Math.log10(marker.count + 1) * 3, 12, 18);
          const photoCount = `${marker.count} Foto${marker.count === 1 ? "" : "s"}`;
          return <g key={`${grid.columns}:${grid.rows}:${marker.x}:${marker.y}`} className="gps-map__marker"
            transform={`translate(${x} ${y}) scale(${scaleX} ${scaleY})`} role="button" tabIndex={selectionMode ? -1 : 0}
            aria-hidden={selectionMode || undefined}
            aria-pressed={!!selection && marker.west === selection.west && marker.east === selection.east && marker.south === selection.south && marker.north === selection.north}
            aria-label={`${photoCount} anzeigen, Breitengrad ${coordinates.format(marker.latitude)}, Längengrad ${coordinates.format(marker.longitude)}`}
            {...markerInteractions(() => onMarkerClick(marker))}>
            <title>{photoCount} an diesem Ort anzeigen</title>
            <circle r={MARKER_HIT_RADIUS} className="gps-map__marker-hit" fill="transparent" />
            <circle r={radius + 2} className="gps-map__marker-halo" />
            <circle r={radius} className="gps-map__marker-core" />
            <text textAnchor="middle" dominantBaseline="central" fontSize={11}>{marker.count}</text>
          </g>;
        })}
        {stopPositions.filter(({ stop }) => stop.longitude >= bounds.west && stop.longitude <= bounds.east &&
          stop.latitude >= bounds.south && stop.latitude <= bounds.north).map(({ stop, x, y }) => <g key={stop.id}
          className="gps-map__marker gps-map__journey-stop"
          transform={`translate(${x} ${y}) scale(${scaleX} ${scaleY})`}
          role={onJourneyStopClick ? "button" : undefined} tabIndex={selectionMode || !onJourneyStopClick ? -1 : 0}
          aria-hidden={selectionMode || !onJourneyStopClick || undefined} aria-pressed={onJourneyStopClick ? stop.id === activeJourneyStop : undefined}
          aria-label={`Reisestopp ${stop.number}`}
          {...(onJourneyStopClick && markerInteractions(() => onJourneyStopClick(stop.id)))}>
          <title>Reisestopp {stop.number}</title>
          <circle r={MARKER_HIT_RADIUS} className="gps-map__marker-hit" fill="transparent" />
          <circle r={JOURNEY_STOP_RADIUS + 3} className="gps-map__journey-stop-halo" />
          <circle r={JOURNEY_STOP_RADIUS} className="gps-map__journey-stop-core" />
          <text textAnchor="middle" dominantBaseline="central" fontSize={11}>{stop.number}</text>
        </g>)}
      </svg>
      </div>
    </div>
  );
};

export default WorldMap;
