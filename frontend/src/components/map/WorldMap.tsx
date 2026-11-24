import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MapPoint } from "../../utils/api";
import land from "./world-land.json";

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

interface MapSize {
  width: number;
  height: number;
}

const WIDTH = 1000;
const HEIGHT = 500;
const MIN_SELECTION_PIXELS = 8;
const MARKER_HIT_RADIUS = 22;
const MARKER_CELL_PADDING = MARKER_HIT_RADIUS + 1;
const MIN_CELL_SIZE = MARKER_CELL_PADDING * 2;
const MAX_GRID_SIZE = 32;
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
  const x = ((marker.longitude - bounds.west) / (bounds.east - bounds.west)) * size.width;
  const y = ((bounds.north - marker.latitude) / (bounds.north - bounds.south)) * size.height;
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
  start: Point;
  bounds: MapBounds;
  view: MapView;
  selecting: boolean;
}

function pointerPosition(event: React.PointerEvent<SVGSVGElement>) {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    position: { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height },
    rect,
  };
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
}

const landPaths = (land as number[][][]).map((ring) =>
  ring.map(([lon, lat], index) => `${index ? "L" : "M"}${projectX(lon).toFixed(2)},${projectY(lat).toFixed(2)}`).join(" ") + "Z",
).join(" ");
const coordinates = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 4 });

const WorldMap: React.FC<Props> = ({
  view, onViewChange, points, grid, onGridChange, selection, onSelectionChange, onMarkerClick,
  selectionMode, onCancelSelection,
}) => {
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
  const [dragBounds, setDragBounds] = useState<MapBounds | null>(null);
  const bounds = useMemo(() => boundsForView(view), [view]);
  const vb = projectBounds(bounds);
  // Marker geometry is in CSS pixels, independent of zoom and screen size.
  const scaleX = vb.width / viewportSize.width;
  const scaleY = vb.height / viewportSize.height;

  useLayoutEffect(() => {
    const marker = focusedMarkerRef.current;
    if (!marker || marker.isConnected) return;
    focusedMarkerRef.current = null;
    const svg = svgRef.current;
    if (svg?.getClientRects().length && document.activeElement === document.body && document.hasFocus()) {
      svg.focus({ preventScroll: true });
    }
  }, [points]);

  useLayoutEffect(() => {
    const gesture = gestureRef.current;
    if (!gesture || gesture.selecting === selectionMode) return;
    const svg = svgRef.current;
    gestureRef.current = null;
    if (svg?.hasPointerCapture(gesture.pointerId)) svg.releasePointerCapture(gesture.pointerId);
    setDragBounds(null);
  }, [selectionMode]);

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 || gestureRef.current) return;
    if (!selectionMode && (event.target as Element).closest(".gps-map__marker")) return;
    const { position } = pointerPosition(event);
    gestureRef.current = { pointerId: event.pointerId, start: position, bounds, view, selecting: selectionMode };
    event.currentTarget.setPointerCapture(event.pointerId);
    if (selectionMode) setDragBounds(boundsForDrag(bounds, position, position));
  };

  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (gesture?.pointerId !== event.pointerId) return;
    const { position } = pointerPosition(event);
    if (gesture.selecting) {
      setDragBounds(boundsForDrag(gesture.bounds, gesture.start, position));
      return;
    }
    onViewChange(normalizedView({
      ...gesture.view,
      longitude: gesture.view.longitude - (position.x - gesture.start.x) * (360 / gesture.view.zoom),
      latitude: gesture.view.latitude + (position.y - gesture.start.y) * (180 / gesture.view.zoom),
    }));
  };

  const finishGesture = (svg: SVGSVGElement, pointerId: number) => {
    const gesture = gestureRef.current;
    if (gesture?.pointerId !== pointerId) return;
    gestureRef.current = null;
    if (svg.hasPointerCapture(pointerId)) svg.releasePointerCapture(pointerId);
    setDragBounds(null);
    return gesture;
  };

  const handlePointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    const gesture = finishGesture(event.currentTarget, event.pointerId);
    if (!gesture?.selecting || !selectionMode) return;
    const { position, rect } = pointerPosition(event);
    const width = Math.abs(clamp(position.x, 0, 1) - gesture.start.x) * rect.width;
    const height = Math.abs(clamp(position.y, 0, 1) - gesture.start.y) * rect.height;
    if (width < MIN_SELECTION_PIXELS || height < MIN_SELECTION_PIXELS) return;
    onSelectionChange(boundsForDrag(gesture.bounds, gesture.start, position));
  };

  const cancelGesture = (event: React.PointerEvent<SVGSVGElement>) => {
    finishGesture(event.currentTarget, event.pointerId);
  };

  const pan = (dx: number, dy: number) => onViewChange(normalizedView({
    ...view, longitude: view.longitude + dx * (360 / view.zoom), latitude: view.latitude + dy * (180 / view.zoom),
  }));

  const selectionRect = selection && projectBounds(selection);
  const activeRect = selectionMode && dragBounds && projectBounds(dragBounds);

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
      <svg ref={svgRef} className={`gps-map__svg${selectionMode ? " gps-map__svg--select" : ""}`}
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
        onPointerUp={handlePointerUp} onPointerCancel={cancelGesture} onLostPointerCapture={cancelGesture}>
        <desc id="gps-map-keyboard-hint">{selectionMode
          ? "Mit den Pfeiltasten verschieben, mit Plus und Minus zoomen. Enter übernimmt den sichtbaren Bereich, Escape beendet die Auswahl."
          : "Mit den Pfeiltasten verschieben, mit Plus und Minus zoomen. Mit Tab einen Fotostandort wählen und mit Enter öffnen."}</desc>
        <rect width={WIDTH} height={HEIGHT} className="gps-map__sea" />
        {[0, 1, 2, 3, 4, 5].map((line) => <line key={`lat-${line}`} x1={0} x2={WIDTH} y1={line * 100} y2={line * 100} className="gps-map__graticule" />)}
        {[0, 1, 2, 3, 4, 5, 6].map((line) => <line key={`lon-${line}`} y1={0} y2={HEIGHT} x1={line * (WIDTH / 6)} x2={line * (WIDTH / 6)} className="gps-map__graticule" />)}
        <path d={landPaths} className="gps-map__land" />
        {selectionRect && <rect {...selectionRect} className="gps-map__selection" />}
        {activeRect && <rect {...activeRect} className="gps-map__selection gps-map__selection--drag" />}
        {grid && points.map((marker) => {
          const position = markerPixelPosition(marker, bounds, viewportSize, grid);
          const x = vb.x + position.x * scaleX;
          const y = vb.y + position.y * scaleY;
          const radius = clamp(11 + Math.log10(marker.count + 1) * 3, 12, 18);
          const photoCount = `${marker.count} Foto${marker.count === 1 ? "" : "s"}`;
          return <g key={`${grid.columns}:${grid.rows}:${marker.x}:${marker.y}`} className="gps-map__marker"
            transform={`translate(${x} ${y}) scale(${scaleX} ${scaleY})`} role="button" tabIndex={selectionMode ? -1 : 0}
            aria-hidden={selectionMode || undefined}
            aria-pressed={!!selection && marker.west === selection.west && marker.east === selection.east && marker.south === selection.south && marker.north === selection.north}
            aria-label={`${photoCount} anzeigen, Breitengrad ${coordinates.format(marker.latitude)}, Längengrad ${coordinates.format(marker.longitude)}`}
            onClick={() => { if (!selectionMode) onMarkerClick(marker); }} onKeyDown={(event) => {
              if (selectionMode || event.repeat) return;
              if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onMarkerClick(marker); }
            }}>
            <title>{photoCount} an diesem Ort anzeigen</title>
            <circle r={MARKER_HIT_RADIUS} className="gps-map__marker-hit" fill="transparent" />
            <circle r={radius + 2} className="gps-map__marker-halo" />
            <circle r={radius} className="gps-map__marker-core" />
            <text textAnchor="middle" dominantBaseline="central" fontSize={11}>{marker.count}</text>
          </g>;
        })}
      </svg>
      </div>
    </div>
  );
};

export default WorldMap;
