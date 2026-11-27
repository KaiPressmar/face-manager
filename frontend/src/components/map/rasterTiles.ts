import type { MapBounds, MapStyle } from "./WorldMap";
import rasterSources from "./raster-detail-sources.json";

export { rasterSources };

export const RASTER_DETAIL_MIN_MAP_ZOOM = 4;
const TILE_SIZE = 256;
const MAX_TILES = 16;
const MAX_CONCURRENT_TILES = 4;
const ATLAS_WIDTH = 1000;
const ATLAS_HEIGHT = 500;

export interface RasterTile {
  url: string;
  x: number;
  y: number;
  side: number;
}

export interface RasterTilePlan {
  key: string;
  level: number;
  tiles: RasterTile[];
}

export function planRasterTiles(style: MapStyle, bounds: MapBounds, size: { width: number; height: number },
  apiBase = "/api"): RasterTilePlan | undefined {
  if (style === "standard") return undefined;
  const { west, east, south, north } = bounds;
  const longitudeSpan = east - west;
  const latitudeSpan = north - south;
  if (![west, east, south, north, size.width, size.height].every(Number.isFinite)
    || longitudeSpan <= 0 || latitudeSpan <= 0 || longitudeSpan > 360 / RASTER_DETAIL_MIN_MAP_ZOOM
    || size.width <= 0 || size.height <= 0 || east <= -180 || west >= 180 || north <= -90 || south >= 90) return undefined;

  const source = rasterSources[style];
  const cssPixelsPerDegree = Math.max(size.width / longitudeSpan, size.height / latitudeSpan);
  let level = Math.max(0, Math.min(source.maxLevel, Math.ceil(Math.log2(180 * cssPixelsPerDegree / TILE_SIZE))));
  const coverageAt = (tileLevel: number) => {
    const degrees = 180 / 2 ** tileLevel;
    return {
      degrees,
      firstColumn: Math.floor((Math.max(-180, west) + 180) / degrees),
      lastColumn: Math.ceil((Math.min(180, east) + 180) / degrees) - 1,
      firstRow: Math.floor((90 - Math.min(90, north)) / degrees),
      lastRow: Math.ceil((90 - Math.max(-90, south)) / degrees) - 1,
    };
  };
  let coverage = coverageAt(level);
  while ((coverage.lastColumn - coverage.firstColumn + 1) * (coverage.lastRow - coverage.firstRow + 1) > MAX_TILES) {
    coverage = coverageAt(--level);
  }

  const { degrees, firstColumn, lastColumn, firstRow, lastRow } = coverage;
  const tiles: RasterTile[] = [];
  for (let row = firstRow; row <= lastRow; row += 1) {
    for (let column = firstColumn; column <= lastColumn; column += 1) {
      const tileWest = -180 + column * degrees;
      const tileNorth = 90 - row * degrees;
      tiles.push({
        url: `${apiBase}/map/detail-tiles/${style}/${level}/${column}/${row}`,
        x: (tileWest + 180) / 360 * ATLAS_WIDTH,
        y: (90 - tileNorth) / 180 * ATLAS_HEIGHT,
        side: degrees / 360 * ATLAS_WIDTH,
      });
    }
  }
  return { key: [apiBase, style, level, firstColumn, lastColumn, firstRow, lastRow].join(":"), level, tiles };
}

export async function fetchRasterTiles(tiles: readonly RasterTile[], signal: AbortSignal, request = fetch) {
  const loaded: { tile: RasterTile; image: Blob }[] = [];
  let next = 0;
  const load = async () => {
    while (!signal.aborted && next < tiles.length) {
      const tile = tiles[next++];
      try {
        const response = await request(tile.url, {
          signal, credentials: "same-origin", referrerPolicy: "no-referrer", redirect: "error",
        });
        if (!response.ok || !/^image\/(jpeg|png)(;|$)/i.test(response.headers.get("content-type") ?? "")) continue;
        const image = await response.blob();
        if (!signal.aborted && image.size > 0) loaded.push({ tile, image });
      } catch {
        // The bundled map remains underneath when a tile fails.
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_CONCURRENT_TILES, tiles.length) }, load));
  return loaded;
}
