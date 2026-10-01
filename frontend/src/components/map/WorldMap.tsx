import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
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

export interface MapMarker {
  latitude: number;
  longitude: number;
  count: number;
  west?: number;
  east?: number;
  south?: number;
  north?: number;
}

const WIDTH = 1000;
const HEIGHT = 500;
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

interface Props {
  view: MapView;
  onViewChange: (view: MapView) => void;
  points: MapMarker[];
  selection: MapBounds | null;
  onSelectionChange: (bounds: MapBounds | null) => void;
  onMarkerClick: (marker: MapMarker) => void;
  selectionMode: boolean;
}

const landPaths = (land as number[][][]).map((ring) =>
  ring.map(([lon, lat], index) => `${index ? "L" : "M"}${projectX(lon).toFixed(2)},${projectY(lat).toFixed(2)}`).join(" ") + "Z",
);

const WorldMap: React.FC<Props> = ({
  view, onViewChange, points, selection, onSelectionChange, onMarkerClick, selectionMode,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [viewportSize, setViewportSize] = useState({ width: WIDTH, height: HEIGHT });
  useLayoutEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const measure = (width: number, height: number) => {
      // Hidden tabs report zero; retain the last useful size until shown again.
      if (width > 0 && height > 0) setViewportSize((current) =>
        current.width === width && current.height === height ? current : { width, height });
    };
    measure(svg.clientWidth, svg.clientHeight);
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width, entry.contentRect.height));
    observer.observe(svg);
    return () => observer.disconnect();
  }, []);
  const gestureRef = useRef<{ startX: number; startY: number; x: number; y: number; view: MapView } | null>(null);
  const [dragRect, setDragRect] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const bounds = boundsForView(view);
  const vb = useMemo(() => ({
    x: projectX(bounds.west), y: projectY(bounds.north),
    width: projectX(bounds.east) - projectX(bounds.west),
    height: projectY(bounds.south) - projectY(bounds.north),
  }), [bounds.east, bounds.north, bounds.south, bounds.west]);

  const eventPoint = (event: React.PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: vb.x + ((event.clientX - rect.left) / rect.width) * vb.width,
      y: vb.y + ((event.clientY - rect.top) / rect.height) * vb.height,
    };
  };

  const handlePointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 || (event.target as Element).closest(".gps-map__marker")) return;
    const point = eventPoint(event);
    gestureRef.current = { startX: event.clientX, startY: event.clientY, x: point.x, y: point.y, view };
    event.currentTarget.setPointerCapture(event.pointerId);
    if (selectionMode) setDragRect({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
  };

  const handlePointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    if (!gesture) return;
    if (selectionMode) {
      const point = eventPoint(event);
      setDragRect({ x1: gesture.x, y1: gesture.y, x2: point.x, y2: point.y });
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    onViewChange(normalizedView({
      ...gesture.view,
      longitude: gesture.view.longitude - ((event.clientX - gesture.startX) / rect.width) * (360 / view.zoom),
      latitude: gesture.view.latitude + ((event.clientY - gesture.startY) / rect.height) * (180 / view.zoom),
    }));
  };

  const handlePointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    const gesture = gestureRef.current;
    gestureRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!gesture || !selectionMode || !dragRect) return;
    if (Math.abs(event.clientX - gesture.startX) < 8 || Math.abs(event.clientY - gesture.startY) < 8) {
      setDragRect(null);
      return;
    }
    onSelectionChange({
      west: clamp(unprojectX(Math.min(dragRect.x1, dragRect.x2)), -180, 180),
      east: clamp(unprojectX(Math.max(dragRect.x1, dragRect.x2)), -180, 180),
      north: clamp(unprojectY(Math.min(dragRect.y1, dragRect.y2)), -90, 90),
      south: clamp(unprojectY(Math.max(dragRect.y1, dragRect.y2)), -90, 90),
    });
    setDragRect(null);
  };

  const pan = (dx: number, dy: number) => onViewChange(normalizedView({
    ...view, longitude: view.longitude + dx * (360 / view.zoom), latitude: view.latitude + dy * (180 / view.zoom),
  }));

  const selectionRect = selection && {
    x: projectX(selection.west), y: projectY(selection.north),
    width: projectX(selection.east) - projectX(selection.west),
    height: projectY(selection.south) - projectY(selection.north),
  };
  const activeRect = dragRect && {
    x: Math.min(dragRect.x1, dragRect.x2), y: Math.min(dragRect.y1, dragRect.y2),
    width: Math.abs(dragRect.x2 - dragRect.x1), height: Math.abs(dragRect.y2 - dragRect.y1),
  };

  return (
    <div className="gps-map__viewport">
      <div className="gps-map__canvas">
      <svg ref={svgRef} className={`gps-map__svg${selectionMode ? " gps-map__svg--select" : ""}`}
        viewBox={`${vb.x} ${vb.y} ${vb.width} ${vb.height}`} preserveAspectRatio="none"
        role="group" aria-label="Weltkarte mit Fotostandorten" tabIndex={0}
        aria-describedby="gps-map-keyboard-hint"
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          const directions: Record<string, [number, number]> = { ArrowUp: [0, 0.2], ArrowDown: [0, -0.2], ArrowLeft: [-0.2, 0], ArrowRight: [0.2, 0] };
          const direction = directions[event.key];
          if (direction) { event.preventDefault(); pan(...direction); }
          if (event.key === "+" || event.key === "-") {
            event.preventDefault();
            onViewChange(normalizedView({ ...view, zoom: view.zoom * (event.key === "+" ? 1.5 : 1 / 1.5) }));
          }
        }}
        onPointerDown={handlePointerDown} onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp} onPointerCancel={() => { gestureRef.current = null; setDragRect(null); }}>
        <desc id="gps-map-keyboard-hint">Mit den Pfeiltasten verschieben, mit Plus und Minus zoomen. Mit Tab einen Fotostandort wählen und mit Enter öffnen.</desc>
        <rect width={WIDTH} height={HEIGHT} className="gps-map__sea" />
        {[0, 1, 2, 3, 4, 5].map((line) => <line key={`lat-${line}`} x1={0} x2={WIDTH} y1={line * 100} y2={line * 100} className="gps-map__graticule" />)}
        {[0, 1, 2, 3, 4, 5, 6].map((line) => <line key={`lon-${line}`} y1={0} y2={HEIGHT} x1={line * (WIDTH / 6)} x2={line * (WIDTH / 6)} className="gps-map__graticule" />)}
        <path d={landPaths.join(" ")} className="gps-map__land" />
        {selectionRect && <rect {...selectionRect} className="gps-map__selection" />}
        {activeRect && <rect {...activeRect} className="gps-map__selection gps-map__selection--drag" />}
        {points.map((marker, index) => {
          const x = projectX(marker.longitude);
          const y = projectY(marker.latitude);
          const radius = clamp(11 + Math.log10(marker.count + 1) * 3, 12, 18);
          // Marker geometry is in CSS pixels, independent of zoom and screen size.
          const scaleX = vb.width / viewportSize.width;
          const scaleY = vb.height / viewportSize.height;
          return <g key={`${marker.latitude}-${marker.longitude}-${index}`} className="gps-map__marker"
            transform={`translate(${x} ${y}) scale(${scaleX} ${scaleY})`} role="button" tabIndex={0}
            aria-pressed={!!selection && marker.west === selection.west && marker.east === selection.east && marker.south === selection.south && marker.north === selection.north}
            aria-label={`${marker.count} Foto${marker.count === 1 ? "" : "s"} an diesem Ort anzeigen`}
            onClick={() => onMarkerClick(marker)} onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onMarkerClick(marker); }
            }}>
            <title>{marker.count} Fotos an diesem Ort anzeigen</title>
            <circle r={22} className="gps-map__marker-hit" fill="transparent" />
            <circle r={radius + 2} className="gps-map__marker-halo" />
            <circle r={radius} className="gps-map__marker-core" />
            <text textAnchor="middle" dominantBaseline="central" fontSize={11}>{marker.count}</text>
          </g>;
        })}
      </svg>
      <a className="gps-map__credit" href="https://www.naturalearthdata.com/" target="_blank" rel="noreferrer">Kartendaten: Natural Earth</a>
      </div>
      <div className="gps-map__controls" aria-label="Kartensteuerung">
        <button type="button" aria-label="Vergrößern" title="Vergrößern" disabled={view.zoom >= 128} onClick={() => onViewChange(normalizedView({ ...view, zoom: view.zoom * 1.5 }))}>+</button>
        <button type="button" aria-label="Verkleinern" title="Verkleinern" disabled={view.zoom <= 1} onClick={() => onViewChange(normalizedView({ ...view, zoom: view.zoom / 1.5 }))}>−</button>
        <button type="button" aria-label="Nach Norden verschieben" title="Nach Norden" onClick={() => pan(0, 0.2)}>↑</button>
        <button type="button" aria-label="Nach Westen verschieben" title="Nach Westen" onClick={() => pan(-0.2, 0)}>←</button>
        <button type="button" aria-label="Nach Osten verschieben" title="Nach Osten" onClick={() => pan(0.2, 0)}>→</button>
        <button type="button" aria-label="Nach Süden verschieben" title="Nach Süden" onClick={() => pan(0, -0.2)}>↓</button>
        <button type="button" aria-label="Weltkarte anzeigen" title="Weltkarte anzeigen" onClick={() => onViewChange({ longitude: 0, latitude: 0, zoom: 1 })}>⌖</button>
      </div>
    </div>
  );
};

export default WorldMap;
