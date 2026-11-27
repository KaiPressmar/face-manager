import type { MapBounds } from "./WorldMap";

const MAX_MERCATOR_LATITUDE = 85.0511287798066;
const MAX_TILE_ZOOM = 14;
const MAX_VISIBLE_TILES = 24;
const MAX_TILE_BYTES = 2_000_000;
const MAX_TILE_CACHE = 48;
const VIEWPORT_TIMEOUT_MS = 6000;
const CONCURRENT_REQUESTS = 4;
const decoder = new TextDecoder();

export type StreetTile = { z: number; x: number; y: number };
export type StreetMapSize = { width: number; height: number };
export type StreetDetailStatus = "loading" | "ready" | "unavailable";
type Point = { x: number; y: number };
type AtlasRect = Point & { width: number; height: number };
export type StreetPath = "water" | "building" | "green" | "waterway" | "rail" | "trail" | "street" | "mainRoad";
export type StreetLabel = Point & { name: string; priority: number };
export type StreetTileData = {
  key: string;
  rectangle: AtlasRect;
  paths: Partial<Record<StreetPath, string>>;
  labels: StreetLabel[];
};

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const mercatorY = (latitude: number) => {
  const radians = clamp(latitude, -MAX_MERCATOR_LATITUDE, MAX_MERCATOR_LATITUDE) * Math.PI / 180;
  return (1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2;
};

export function streetTileKey({ z, x, y }: StreetTile) { return `${z}/${x}/${y}`; }

export function visibleStreetTiles(bounds: MapBounds, size: StreetMapSize): StreetTile[] {
  const west = clamp(bounds.west, -180, 180);
  const east = clamp(bounds.east, -180, 180);
  const left = (west + 180) / 360;
  const right = (east + 180) / 360;
  const top = mercatorY(bounds.north);
  const bottom = mercatorY(bounds.south);
  if (!(right > left && bottom > top && size.width > 0 && size.height > 0)) return [];
  const pixels = Math.min(size.width / (right - left), size.height / (bottom - top));
  let z = clamp(Math.floor(Math.log2(pixels / 256)), 0, MAX_TILE_ZOOM);

  for (; z >= 0; z -= 1) {
    const count = 2 ** z;
    const xMin = clamp(Math.floor(left * count), 0, count - 1);
    const xMax = clamp(Math.ceil(right * count) - 1, 0, count - 1);
    const yMin = clamp(Math.floor(top * count), 0, count - 1);
    const yMax = clamp(Math.ceil(bottom * count) - 1, 0, count - 1);
    if ((xMax - xMin + 1) * (yMax - yMin + 1) > MAX_VISIBLE_TILES) continue;
    const tiles: StreetTile[] = [];
    for (let y = yMin; y <= yMax; y += 1) {
      for (let x = xMin; x <= xMax; x += 1) tiles.push({ z, x, y });
    }
    return tiles;
  }
  return [];
}

function atlasPoint(tile: StreetTile, extent: number, x: number, y: number): Point {
  const world = 2 ** tile.z;
  const latitude = Math.atan(Math.sinh(Math.PI * (1 - 2 * (tile.y + y / extent) / world))) * 180 / Math.PI;
  return { x: 1000 * (tile.x + x / extent) / world, y: 500 * (90 - latitude) / 180 };
}

// The OpenMapTiles data uses the Mapbox Vector Tile 2.1 protobuf format.
// Only the scalar values and geometry needed by the map layer are decoded.
class Reader {
  private offset = 0;

  constructor(private readonly data: Uint8Array) {}

  get done() { return this.offset === this.data.length; }

  varint() {
    let number = 0;
    for (let shift = 0; shift <= 63 && this.offset < this.data.length; shift += 7) {
      const byte = this.data[this.offset++];
      number += (byte & 127) * 2 ** shift;
      if (byte < 128) return number;
    }
    throw new Error("Invalid vector tile integer");
  }

  private take(length: number) {
    const end = this.offset + length;
    if (!Number.isSafeInteger(end) || length < 0 || end > this.data.length) throw new Error("Truncated vector tile");
    const result = this.data.subarray(this.offset, end);
    this.offset = end;
    return result;
  }

  bytes() { return this.take(this.varint()); }
  string() { return decoder.decode(this.bytes()); }

  skip(wire: number) {
    if (wire === 0) this.varint();
    else if (wire === 1) this.take(8);
    else if (wire === 2) this.bytes();
    else if (wire === 5) this.take(4);
    else throw new Error("Unsupported vector tile field");
  }
}

const LAYER_LIMITS = new Map([
  ["building", { features: 1500, points: 8000 }], ["landcover", { features: 240, points: 4000 }],
  ["landuse", { features: 240, points: 4000 }], ["place", { features: 72, points: 300 }],
  ["transportation", { features: 1800, points: 12000 }], ["transportation_name", { features: 180, points: 5000 }],
  ["water", { features: 260, points: 6000 }], ["waterway", { features: 260, points: 3000 }],
]);
const EMPTY_BYTES = new Uint8Array();

function readStringValue(bytes: Uint8Array) {
  const reader = new Reader(bytes);
  while (!reader.done) {
    const field = reader.varint();
    if (field === 10) return reader.string();
    reader.skip(field & 7);
  }
  return "";
}

function readFeature(bytes: Uint8Array, keys: string[], values: string[]) {
  const reader = new Reader(bytes);
  const tags: number[] = [];
  let type = 0;
  let geometry: Uint8Array = EMPTY_BYTES;
  while (!reader.done) {
    const field = reader.varint();
    if (field === 18) {
      const packed = new Reader(reader.bytes());
      while (!packed.done) tags.push(packed.varint());
    } else if (field === 16) tags.push(reader.varint());
    else if (field === 24) type = reader.varint();
    else if (field === 34) geometry = reader.bytes();
    else reader.skip(field & 7);
  }
  const properties: Partial<Record<string, string>> = {};
  for (let index = 0; index + 1 < tags.length; index += 2) {
    const key = keys[tags[index]];
    if (key === "class" || key === "name" || key === "name:de" || key === "name_de" || key === "ref") {
      properties[key] = values[tags[index + 1]];
    }
  }
  return { properties, type, geometry };
}

function readLayer(bytes: Uint8Array) {
  const reader = new Reader(bytes);
  let name = "";
  let extent = 4096;
  const features: Uint8Array[] = [];
  const keys: string[] = [];
  const values: Uint8Array[] = [];
  while (!reader.done) {
    const field = reader.varint();
    if (field === 10) name = reader.string();
    else if (field === 18) features.push(reader.bytes());
    else if (field === 26) keys.push(reader.string());
    else if (field === 34) values.push(reader.bytes());
    else if (field === 40) extent = reader.varint();
    else reader.skip(field & 7);
  }
  return { name, extent, features, keys, values };
}

function pathType(layer: string, type: number, classification: string | undefined): StreetPath | undefined {
  if (type === 3) {
    if (layer === "water" || layer === "building") return layer;
    if ((layer === "landcover" || layer === "landuse") && /^(wood|forest|park|grass|recreation_ground)$/.test(classification ?? "")) return "green";
  }
  if (type !== 2) return;
  if (layer === "waterway") return "waterway";
  if (layer !== "transportation") return;
  if (classification === "rail" || classification === "transit") return "rail";
  if (classification === "path" || classification === "track") return "trail";
  if (/^(motorway|trunk|primary|secondary)$/.test(classification ?? "")) return "mainRoad";
  if (classification !== "ferry" && classification !== "aerialway") return "street";
}

type GeometryPart = { points: Point[]; closed: boolean };

function geometryParts(bytes: Uint8Array, limit: number) {
  const reader = new Reader(bytes);
  const parts: GeometryPart[] = [];
  let part: GeometryPart | undefined;
  let pointCount = 0;
  let x = 0;
  let y = 0;
  const delta = () => {
    const encoded = reader.varint();
    if (encoded > 0xffffffff) throw new Error("Invalid vector tile coordinate");
    return encoded % 2 ? -(encoded + 1) / 2 : encoded / 2;
  };
  while (!reader.done) {
    const header = reader.varint();
    const kind = header % 8;
    const count = Math.floor(header / 8);
    if (count < 1 || count > 200_000) throw new Error("Invalid vector tile geometry length");
    if (kind === 7 && part && count === 1) { part.closed = true; continue; }
    if (kind !== 1 && (kind !== 2 || !part)) throw new Error("Invalid vector tile geometry command");
    pointCount += count;
    if (pointCount > limit) return;
    for (let index = 0; index < count; index += 1) {
      x += delta();
      y += delta();
      if (kind === 1) { part = { points: [], closed: false }; parts.push(part); }
      part?.points.push({ x, y });
    }
  }
  return { parts, pointCount };
}

function labelPosition(parts: GeometryPart[]) {
  let best: Point | undefined;
  let lengthSquared = -1;
  for (const { points } of parts) {
    if (points.length === 1 && lengthSquared < 0) {
      best = points[0];
      lengthSquared = 0;
    }
    for (let index = 1; index < points.length; index += 1) {
      const a = points[index - 1];
      const b = points[index];
      const length = (a.x - b.x) ** 2 + (a.y - b.y) ** 2;
      if (length > lengthSquared) {
        best = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        lengthSquared = length;
      }
    }
  }
  return best;
}

export function decodeStreetTile(tile: StreetTile, bytes: Uint8Array): StreetTileData {
  if (bytes.length > MAX_TILE_BYTES) throw new Error("Vector tile exceeds size limit");
  const reader = new Reader(bytes);
  const paths: Partial<Record<StreetPath, string[]>> = {};
  const labels: StreetLabel[] = [];
  while (!reader.done) {
    const field = reader.varint();
    if (field !== 26) { reader.skip(field & 7); continue; }
    const { name, extent, features, keys, values } = readLayer(reader.bytes());
    const limit = LAYER_LIMITS.get(name);
    if (!limit || !extent || extent > 65536) continue;
    const strings = values.map(readStringValue);
    let remainingPoints = limit.points;
    for (const encoded of features.slice(0, limit.features)) {
      if (remainingPoints <= 0) break;
      const { type, properties, geometry } = readFeature(encoded, keys, strings);
      const kind = pathType(name, type, properties.class);
      const label = (name === "transportation_name" || name === "place") && (type === 1 || type === 2)
        ? properties["name:de"] || properties.name_de || properties.name || properties.ref : undefined;
      if (!kind && !label) continue;
      const decoded = geometryParts(geometry, remainingPoints);
      if (!decoded) continue;
      const { parts, pointCount } = decoded;
      remainingPoints -= pointCount;
      if (kind) {
        const path = parts.map(({ points, closed }) => {
          const coordinates = points.map(({ x, y }) => {
            const point = atlasPoint(tile, extent, x, y);
            return `${point.x.toFixed(5)} ${point.y.toFixed(5)}`;
          });
          return `M${coordinates.join(" ")}${closed ? "Z" : ""}`;
        }).join("");
        if (path) (paths[kind] ??= []).push(path);
      }
      if (label && label.length <= 110) {
        const position = labelPosition(parts);
        if (position && position.x >= 0 && position.x < extent && position.y >= 0 && position.y < extent) {
          labels.push({ ...atlasPoint(tile, extent, position.x, position.y), name: label,
            priority: name === "place" ? 2 : pathType("transportation", 2, properties.class) === "mainRoad" ? 1 : 0 });
        }
      }
    }
  }
  const topLeft = atlasPoint(tile, 1, 0, 0);
  const bottomRight = atlasPoint(tile, 1, 1, 1);
  const rectangle = { ...topLeft, width: bottomRight.x - topLeft.x, height: bottomRight.y - topLeft.y };
  const compact = Object.fromEntries(Object.entries(paths).map(([key, segments]) => [key, segments.join("")]));
  return { key: streetTileKey(tile), rectangle, paths: compact, labels };
}

type TileCache = Map<string, StreetTileData>;

async function readTileBody(response: Response) {
  if (!response.body) return new Uint8Array(await response.arrayBuffer());
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > MAX_TILE_BYTES) {
        await reader.cancel();
        throw new Error("Vector tile exceeds size limit");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

async function requestTile(tile: StreetTile, cache: TileCache, signal: AbortSignal, apiBase: string) {
  const key = streetTileKey(tile);
  const cached = cache.get(key);
  if (cached) {
    cache.delete(key);
    cache.set(key, cached);
    return cached;
  }
  try {
    const response = await fetch(`${apiBase}/map/detail-tiles/street/${key}`, {
      signal, credentials: "same-origin", referrerPolicy: "no-referrer", cache: "no-store",
    });
    if (!(response.ok || response.status === 404)) return;
    if (Number(response.headers.get("Content-Length")) > MAX_TILE_BYTES) return;
    const data = response.status === 404 ? EMPTY_BYTES : await readTileBody(response);
    if (signal.aborted) return;
    const decoded = decodeStreetTile(tile, data);
    // A transient missing tile should be attempted again on explicit retry.
    if (response.ok) {
      cache.set(key, decoded);
      const oldest = cache.keys().next().value;
      if (cache.size > MAX_TILE_CACHE && oldest !== undefined) cache.delete(oldest);
    }
    return decoded;
  } catch {
    return undefined;
  }
}

export async function loadStreetTiles(tiles: StreetTile[], cache: TileCache, signal: AbortSignal, apiBase: string) {
  if (signal.aborted) return [];
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(cancel, VIEWPORT_TIMEOUT_MS);
  const loaded: StreetTileData[] = [];
  try {
    let next = 0;
    const workers = Array.from({ length: Math.min(tiles.length, CONCURRENT_REQUESTS) }, async () => {
      while (!controller.signal.aborted && next < tiles.length) {
        const tile = await requestTile(tiles[next++], cache, controller.signal, apiBase);
        if (tile) loaded.push(tile);
      }
    });
    await Promise.all(workers);
    return loaded;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", cancel);
  }
}
