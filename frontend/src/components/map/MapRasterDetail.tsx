import { useEffect, useMemo, useState } from "react";
import { API_BASE } from "../../utils/api";
import type { MapBounds, MapStyle } from "./WorldMap";
import { fetchRasterTiles, planRasterTiles, type RasterTile } from "./rasterTiles";

export type MapRasterDetailStatus = "offline" | "loading" | "ready" | "partial" | "unavailable";

interface Props {
  style: MapStyle;
  bounds: MapBounds;
  size: { width: number; height: number };
  retry?: number;
  onStatusChange?: (status: MapRasterDetailStatus) => void;
}

interface RasterResult {
  key: string;
  style: MapStyle;
  retry: number;
  complete: boolean;
  tiles: (RasterTile & { href: string })[];
}

const VIEW_SETTLE_MS = 280;
const REQUEST_TIMEOUT_MS = 10000;

export default function MapRasterDetail({ style, bounds, size, retry = 0, onStatusChange }: Props) {
  const plan = useMemo(() => planRasterTiles(style, bounds, size, API_BASE), [bounds, size.height, size.width, style]);
  const planKey = plan?.key;
  const [result, setResult] = useState<RasterResult>();
  const status: MapRasterDetailStatus = !plan ? "offline" : !result || result.key !== planKey || result.retry !== retry ? "loading"
    : !result.tiles.length ? "unavailable" : result.complete ? "ready" : "partial";

  useEffect(() => {
    onStatusChange?.(status);
  }, [onStatusChange, status]);

  useEffect(() => {
    if (!plan) {
      setResult(undefined);
      return;
    }
    const controller = new AbortController();
    let active = true;
    let timeout: number | undefined;
    const settled = window.setTimeout(() => {
      timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      void fetchRasterTiles(plan.tiles, controller.signal).then((loaded) => {
        window.clearTimeout(timeout);
        if (!active) return;
        setResult({
          key: plan.key, style, retry, complete: loaded.length === plan.tiles.length,
          tiles: loaded.map(({ tile, image }) => ({ ...tile, href: URL.createObjectURL(image) })),
        });
      });
    }, VIEW_SETTLE_MS);
    return () => {
      active = false;
      window.clearTimeout(settled);
      window.clearTimeout(timeout);
      controller.abort();
    };
    // The plan key encodes the source and the complete tile coverage.
  }, [planKey, retry]);

  useEffect(() => () => {
    result?.tiles.forEach(({ href }) => URL.revokeObjectURL(href));
  }, [result]);

  if (!plan || result?.style !== style || !result.tiles.length) return null;
  return <g data-map-raster-detail={style} aria-hidden="true" pointerEvents="none">
    {result.tiles.map(({ url, href, x, y, side }) =>
      <image key={url} href={href} x={x} y={y} width={side} height={side} preserveAspectRatio="none" />)}
  </g>;
}
