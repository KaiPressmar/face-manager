import { useEffect, useId, useMemo, useRef, useState } from "react";
import { API_BASE } from "../../utils/api";
import type { MapBounds, MapStyle } from "./WorldMap";
import {
  loadStreetTiles, streetTileKey, visibleStreetTiles,
  type StreetDetailStatus, type StreetLabel, type StreetMapSize, type StreetPath, type StreetTileData,
} from "./streetTiles";

interface Props {
  bounds: MapBounds;
  size: StreetMapSize;
  style: MapStyle;
  lightBasemap?: boolean;
  retry?: number;
  onStatus: (status: StreetDetailStatus) => void;
}

const LAYERS: { kind: StreetPath; width?: number; color: string; satellite?: string; opacity?: number; dashed?: string }[] = [
  { kind: "green", color: "#3e684d", opacity: .56 },
  { kind: "water", color: "var(--map-lake)", opacity: .95 },
  { kind: "building", color: "#d0c1a9", satellite: "#f0dac0", opacity: .48 },
  { kind: "waterway", width: 1.2, color: "var(--map-river)", opacity: .86 },
  { kind: "rail", width: 1, color: "#d7cacb", satellite: "#dbd3d0", dashed: "2 3", opacity: .76 },
  { kind: "trail", width: 1, color: "#d6dbc6", satellite: "#e6ecd5", dashed: "2 3", opacity: .73 },
  { kind: "street", width: 1.25, color: "#edf0e0", satellite: "#f2f0e4", opacity: .84 },
  { kind: "mainRoad", width: 2.1, color: "#f6d786", satellite: "#f2db8c", opacity: .93 },
];

const terrainInk: Record<StreetPath, string> = {
  green: "#7a9a68", water: "#a0c4d2", building: "#8e7c6d", waterway: "#5a899d",
  rail: "#6f5e62", trail: "#62733e", street: "#53605d", mainRoad: "#a35e30",
};

function nearbyLabels(tiles: StreetTileData[], bounds: MapBounds, size: StreetMapSize) {
  const left = 1000 * (bounds.west + 180) / 360;
  const top = 500 * (90 - bounds.north) / 180;
  const sx = 1000 * (bounds.east - bounds.west) / 360 / size.width;
  const sy = 500 * (bounds.north - bounds.south) / 180 / size.height;
  const labels: (StreetLabel & { left: number; right: number; top: number; bottom: number })[] = [];
  const names = new Set<string>();
  const candidates = tiles.flatMap((tile) => tile.labels).sort((a, b) => b.priority - a.priority);
  for (const label of candidates) {
    if (names.has(label.name)) continue;
    const x = (label.x - left) / sx;
    const y = (label.y - top) / sy;
    const halfWidth = Math.max(12, label.name.length * 3.15) + 5;
    const box = { left: x - halfWidth, right: x + halfWidth, top: y - 9, bottom: y + 9 };
    if (box.left < 3 || box.top < 3 || box.right > size.width - 3 || box.bottom > size.height - 3) continue;
    if (labels.some((other) => box.right > other.left && box.left < other.right &&
        box.bottom > other.top && box.top < other.bottom)) continue;
    labels.push({ ...label, ...box });
    names.add(label.name);
    if (labels.length === 32) break;
  }
  return { labels, sx, sy };
}

export default function MapStreetDetail({ bounds, size, style, lightBasemap = false, retry = 0, onStatus }: Props) {
  const id = useId();
  const cache = useRef(new Map<string, StreetTileData>());
  const tiles = useMemo(() => visibleStreetTiles(bounds, size), [bounds, size]);
  const plan = tiles.map(streetTileKey).join(";");
  const [result, setResult] = useState<{ plan: string; retry: number; tiles: StreetTileData[] }>();
  const waiting = Boolean(plan) && (result?.plan !== plan || result.retry !== retry);
  const visible = useMemo(() => {
    const keys = new Set(plan.split(";"));
    return result?.tiles.filter((tile) => keys.has(tile.key) && (tile.labels.length || Object.keys(tile.paths).length)) ?? [];
  }, [plan, result]);
  const status: StreetDetailStatus = waiting ? "loading" : visible.length ? "ready" : "unavailable";

  useEffect(() => {
    if (!plan) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void loadStreetTiles(tiles, cache.current, controller.signal, API_BASE).then((loaded) => {
        if (!controller.signal.aborted) setResult({ plan, retry, tiles: loaded });
      });
    }, 160);
    return () => { clearTimeout(timer); controller.abort(); };
    // The plan identifies the exact tile set regardless of sub-tile panning.
  }, [plan, retry]);

  useEffect(() => { onStatus(status); }, [onStatus, status]);
  const labelLayout = useMemo(() => nearbyLabels(visible, bounds, size), [visible, bounds, size]);
  if (!visible.length) return null;

  const satellite = style === "satellite";
  return <g className="gps-map__street-detail" aria-hidden="true" pointerEvents="none">
    {visible.map((tile, index) => {
      const clip = `gps-map-streets-${id}-${index}`;
      return <g key={tile.key}>
        <defs><clipPath id={clip}><rect {...tile.rectangle} /></clipPath></defs>
        <g clipPath={`url(#${clip})`}>
          {LAYERS.map(({ kind, width, color, satellite: satelliteColor, opacity, dashed }) => {
            const path = tile.paths[kind];
            if (!path || (satellite && !satelliteColor)) return null;
            const ink = satellite ? satelliteColor : lightBasemap ? terrainInk[kind] : color;
            const strokeWidth = satellite && kind === "building" ? .6 : width;
            return strokeWidth ? <path key={kind} d={path} fill="none" stroke={ink} strokeWidth={strokeWidth}
              strokeOpacity={opacity} strokeDasharray={dashed} strokeLinecap="round" strokeLinejoin="round"
              vectorEffect="non-scaling-stroke" /> : <path key={kind} d={path} fill={ink} fillOpacity={opacity} fillRule="evenodd" />;
          })}
        </g>
      </g>;
    })}
    {labelLayout.labels.map(({ name, x, y, priority }) => <text key={name}
      transform={`translate(${x} ${y}) scale(${labelLayout.sx} ${labelLayout.sy})`}
      textAnchor="middle" dominantBaseline="central" fill="var(--map-label)" stroke="var(--map-label-outline)"
      strokeWidth={2.5} strokeLinejoin="round" paintOrder="stroke" fontSize={priority > 1 ? 11 : 9}
      fontWeight={priority > 1 ? 650 : 500}>{name}</text>)}
  </g>;
}
