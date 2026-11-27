import type { JourneyConnection, MapBounds, MapStyle } from "./WorldMap";
import countries from "./world-countries.json";
import satelliteSource from "./world-satellite.json";
import overviewWater from "./world-water-overview.json";

interface AlbumPosition {
  latitude: number;
  longitude: number;
}

interface AlbumPoint extends AlbumPosition {
  count?: number;
}

export interface MapAlbumStop extends AlbumPosition {
  id: string;
  number: number;
  title: string;
  placeName?: string;
  subtitle?: string;
  arrivalMode?: AlbumTransport;
}

export const albumTransports = {
  car: { label: "Auto", path: "M3 16v-4l2-2 2-5h10l2 5 2 2v4M3 12h18M5 16v3m14-3v3M8 15h.01m8 0h.01M3 16h18" },
  train: { label: "Zug", path: "M8 19l-3 3m11-3 3 3M6 5c0-3 12-3 12 0v11c0 4-12 4-12 0ZM6 11h12M9 15h.01m6 0h.01" },
  plane: { label: "Flugzeug", path: "M12 3v18M3 14l9-5 9 5M8 21l4-2 4 2" },
  walk: { label: "Zu Fuß", path: "M13 4a1 1 0 1 0 0-2 1 1 0 0 0 0 2ZM5 12l4-3 3-2 3 4 4 1M12 7l-2 7 4 3 2 5m-6-8-2 4-4 3" },
  bike: { label: "Fahrrad", path: "M8 17a3 3 0 1 0-6 0 3 3 0 0 0 6 0Zm14 0a3 3 0 1 0-6 0 3 3 0 0 0 6 0ZM5 17l4-9 5 9H5m9 0 3-11h3M7 8h5" },
  boat: { label: "Schiff", path: "M4 15l8-3 8 3-2 4H6l-2-4Zm3-1V8h10v6m-5-6V4M3 22h18" },
} as const;
export type AlbumTransport = keyof typeof albumTransports;
type AlbumConnection = JourneyConnection & { transport?: AlbumTransport };

export const albumAccents = {
  automatic: { name: "Automatisch", color: "" },
  rust: { name: "Terrakotta", color: "#94402e" },
  ocean: { name: "Ozeanblau", color: "#205b79" },
  pine: { name: "Tannengrün", color: "#305a39" },
  plum: { name: "Pflaume", color: "#713f69" },
} as const;
export type AlbumAccent = keyof typeof albumAccents;
export type AlbumFormat = "a4" | "portrait" | "square" | "square-210" | "square-300" | "custom";
export const albumDensities = [150, 240, 300, 400] as const;
export type AlbumDpi = (typeof albumDensities)[number];

export interface AlbumDimensions {
  width: number;
  height: number;
}

export interface AlbumCustomColors {
  paper?: string;
  accent?: string;
  land?: string;
  water?: string;
}

export interface AlbumFormatting {
  typeface?: "serif" | "sans";
  alignment?: "left" | "center";
  titleScale?: number;
  margins?: "narrow" | "normal" | "wide";
  listColumns?: 1 | 2;
}

export interface MapAlbumArtworkInput {
  format: AlbumFormat;
  dimensions?: AlbumDimensions;
  dpi?: AlbumDpi;
  title: string;
  subtitle?: string;
  period?: string;
  style: MapStyle;
  palette?: "sand" | "coast";
  accent?: AlbumAccent;
  colors?: AlbumCustomColors;
  formatting?: AlbumFormatting;
  wideFrame?: boolean;
  contextPercent?: number;
  markerScale?: number;
  showCountryNames?: boolean;
  showStopsList?: boolean;
  bounds: MapBounds;
  points?: readonly AlbumPoint[];
  stops?: readonly MapAlbumStop[];
  connections?: readonly AlbumConnection[];
  fitJourney?: boolean;
  journeyPadding?: number;
}

export interface MapAlbumPage {
  svg: string;
  height: number;
  width: number;
  number: number;
  total: number;
  dpi: AlbumDpi;
  satelliteFallback?: boolean;
  visibleStopCount?: number;
}

interface Pixel {
  x: number;
  y: number;
}

interface Frame extends Pixel {
  width: number;
  height: number;
}

const palettes = {
  sand: { paper: "#f8f3e8", ink: "#253b35", muted: "#4b6259", rule: "#b7c1aa", routeInk: "#945035", mapInk: "#2b535a",
    sea: "#d8e8e6", land: "#cee0c9", reliefLand: "#d1d7b9", lake: "#adcdd0", river: "#77a5a9", shore: "#9eb29d", border: "#7d8e7d", labelHalo: "#edf1da" },
  coast: { paper: "#f0f5f6", ink: "#223b45", muted: "#405e69", rule: "#b1c4cb", routeInk: "#8d4939", mapInk: "#244f66",
    sea: "#d0e5ee", land: "#e3e9d9", reliefLand: "#d7e4cf", lake: "#a9c9d6", river: "#729fae", shore: "#96aea9", border: "#718d88", labelHalo: "#edf4ec" },
} as const;
type Palette = Record<keyof typeof palettes.sand | "labelInk", string>;
export function albumAccentColor(palette: keyof typeof palettes, accent: AlbumAccent, forJourney: boolean) {
  return albumAccents[accent].color || palettes[palette][forJourney ? "routeInk" : "mapInk"];
}

export function albumPaletteColors(palette: keyof typeof palettes, accent: AlbumAccent, forJourney: boolean, style: MapStyle) {
  const { paper, land, reliefLand, sea } = palettes[palette];
  return { paper, land: style === "landscape" ? reliefLand : land, water: sea, accent: albumAccentColor(palette, accent, forJourney) };
}

export function normalizeAlbumColor(value?: string) {
  const hex = value?.trim().replace(/^#/, "").toLowerCase() ?? "";
  if (/^[0-9a-f]{6}$/.test(hex)) return `#${hex}`;
  if (/^[0-9a-f]{3}$/.test(hex)) return `#${[...hex].map((digit) => digit.repeat(2)).join("")}`;
  return undefined;
}

function luminance(color: string) {
  return [.2126, .7152, .0722].reduce((total, factor, index) => {
    const channel = parseInt(color.slice(1 + index * 2, 3 + index * 2), 16) / 255;
    return total + factor * (channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
  }, 0);
}

function contrast(first: string, second: string) {
  const a = luminance(first), b = luminance(second);
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
}

function readableOn(background: string, preferred: string, minimum = 4.5) {
  if (contrast(background, preferred) >= minimum) return preferred;
  return contrast(background, "#000000") > contrast(background, "#ffffff") ? "#000000" : "#ffffff";
}

function tintForOutline(background: string, target: string, minimum: number) {
  if (contrast(background, target) >= minimum) return target;
  const edge = readableOn(background, target, minimum);
  return "#" + [1, 3, 5].map((position) => {
    const from = parseInt(background.slice(position, position + 2), 16);
    const to = parseInt(edge.slice(position, position + 2), 16);
    return Math.round(from + (to - from) * .28).toString(16).padStart(2, "0");
  }).join("");
}

function artworkPalette(input: MapAlbumArtworkInput): Palette {
  const basePalette = input.palette ?? "sand";
  const base = palettes[basePalette];
  const paper = normalizeAlbumColor(input.colors?.paper);
  const accent = normalizeAlbumColor(input.colors?.accent);
  const land = normalizeAlbumColor(input.colors?.land);
  const water = normalizeAlbumColor(input.colors?.water);
  const labelInk = land ? readableOn(land, base.ink) : base.ink;
  return {
    ...base,
    ...(paper && { paper, ink: readableOn(paper, base.ink), muted: readableOn(paper, base.muted), rule: readableOn(paper, base.rule, 1.4) }),
    ...(land && { land, reliefLand: land, shore: tintForOutline(land, base.shore, 1.6), border: tintForOutline(land, base.border, 2) }),
    ...(water && { sea: water, lake: water }),
    labelInk,
    labelHalo: land ? readableOn(labelInk, base.labelHalo) : base.labelHalo,
    routeInk: accent ?? albumAccentColor(basePalette, input.accent ?? "automatic", true),
    mapInk: accent ?? albumAccentColor(basePalette, input.accent ?? "automatic", false),
  };
}
const serif = "Georgia, 'Times New Roman', serif";
const sans = "Arial, Helvetica, sans-serif";
const zoomInset = 0.12;
const atlasWidth = 1000;
const atlasHeight = 500;
const layouts = {
  a4: { width: 3508, height: 2480, margin: 156, mapHeight: 1370, firstStops: 4, continuedStops: 16 },
  portrait: { width: 2480, height: 3508, margin: 145, mapHeight: 1500, firstStops: 14, continuedStops: 26 },
  square: { width: 3000, height: 3000, margin: 150, mapHeight: 1510, firstStops: 8, continuedStops: 20 },
} as const;
const squareBookSizes = { "square-210": 210, "square-300": 300 } as const;
type Layout = Record<keyof typeof layouts.a4, number>;

export function albumOutputSize(format: AlbumFormat, dpi: AlbumDpi, dimensions?: AlbumDimensions): AlbumDimensions | null {
  if (!albumDensities.includes(dpi)) return null;
  if (format === "square-210" || format === "square-300") {
    const side = Math.round(squareBookSizes[format] * dpi / 25.4);
    return side * side <= 16_000_000 ? { width: side, height: side } : null;
  }
  if (format !== "custom") {
    const { width, height } = layouts[format];
    return { width: Math.round(width * dpi / 300), height: Math.round(height * dpi / 300) };
  }
  if (!dimensions) return null;
  const { width, height } = dimensions;
  if (![width, height].every((side) => Number.isInteger(side) && side >= 1200 && side <= 4800) ||
      width * height > 16_000_000 || width > height * 2 || height > width * 2) return null;
  return dimensions;
}

function drawingLayout(format: AlbumFormat, dimensions: AlbumDimensions, marginStyle: AlbumFormatting["margins"]): Layout {
  const ratio = dimensions.width / dimensions.height;
  const base = layouts[format === "custom" ? ratio > 1.2 ? "a4" : ratio < 1 / 1.2 ? "portrait" : "square"
    : format === "square-210" || format === "square-300" ? "square" : format];
  const scale = Math.min(base.width, base.height) / Math.min(dimensions.width, dimensions.height);
  const margin = Math.round(base.margin * (marginStyle === "narrow" ? .67 : marginStyle === "wide" ? 1.45 : 1));
  return format === "custom" ? {
    ...base, width: Math.round(dimensions.width * scale), height: Math.round(dimensions.height * scale), margin,
  } : { ...base, margin };
}

const clamp = (n: number, min: number, max: number) => Math.max(min, Math.min(max, n));
const turn = (longitude: number) => ((longitude % 360) + 360) % 360;
const atlasX = (longitude: number) => (longitude + 180) / 360 * atlasWidth;
const atlasY = (latitude: number) => (90 - latitude) / 180 * atlasHeight;
const nearLongitude = (longitude: number, center: number) => longitude + 360 * Math.round((center - longitude) / 360);
const direction = (from: number, to: number) => {
  const difference = to - from;
  return Math.abs(difference) > 180 ? difference - Math.sign(difference) * 360 : difference;
};

const number = (value: number) => Number(value.toFixed(2));
const xml = (value: string) => value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uD800-\uDFFF\uFFFE\uFFFF]/gu, "")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");

/** Return the smallest longitude arc containing all stops and observed connections. */
export function fitAlbumJourney(stops: readonly AlbumPosition[], connections: readonly JourneyConnection[], minimumPadding = 1): MapBounds {
  const spans = stops.map(({ longitude }) => {
    const start = turn(longitude + 180);
    return { start, end: start };
  });
  for (const { from, to } of connections) {
    const start = turn(from.longitude + 180);
    const end = start + direction(from.longitude, to.longitude);
    const low = Math.min(start, end), high = Math.max(start, end);
    if (low < 0) spans.push({ start: low + 360, end: 360 }, { start: 0, end: high });
    else if (high > 360) spans.push({ start: low, end: 360 }, { start: 0, end: high - 360 });
    else spans.push({ start: low, end: high });
  }
  if (spans.length === 0) return { west: -180, east: 180, south: -90, north: 90 };

  const merged: typeof spans = [];
  for (const span of spans.sort((a, b) => a.start - b.start)) {
    const previous = merged[merged.length - 1];
    if (previous && previous.end >= span.start) previous.end = Math.max(previous.end, span.end);
    else merged.push(span);
  }
  let gap = -1;
  let west = 0;
  merged.forEach((span, index) => {
    const next = index === merged.length - 1 ? merged[0].start + 360 : merged[index + 1].start;
    if (next - span.end > gap) {
      west = turn(next) - 180;
      gap = next - span.end;
    }
  });
  let south = 90;
  let north = -90;
  const measure = ({ latitude }: AlbumPosition) => {
    south = Math.min(south, latitude);
    north = Math.max(north, latitude);
  };
  stops.forEach(measure);
  connections.forEach(({ from, to }) => { measure(from); measure(to); });
  const longitudeSpan = 360 - gap;
  const latitudeSpan = north - south;
  const padding = Number.isFinite(minimumPadding) ? Math.max(.0001, minimumPadding) : 1;
  const longitudePadding = Math.max(padding, longitudeSpan * zoomInset);
  const latitudePadding = Math.max(padding, latitudeSpan * zoomInset);
  return longitudeSpan + longitudePadding * 2 >= 360
    ? { west: -180, east: 180, south: Math.max(-90, south - latitudePadding), north: Math.min(90, north + latitudePadding) }
    : { west: west - longitudePadding, east: west + longitudeSpan + longitudePadding,
        south: Math.max(-90, south - latitudePadding), north: Math.min(90, north + latitudePadding) };
}

function fitFrame(bounds: MapBounds, frame: Frame): MapBounds {
  const originalWidth = Math.max(bounds.east - bounds.west, .01);
  const originalHeight = Math.max(bounds.north - bounds.south, .01);
  const aspect = frame.width / frame.height;
  const width = Math.min(360, Math.max(originalWidth, originalHeight * aspect));
  const height = Math.min(180, Math.max(originalHeight, originalWidth / aspect));
  const longitude = (bounds.east + bounds.west) / 2;
  const latitude = clamp((bounds.north + bounds.south) / 2, -90 + height / 2, 90 - height / 2);
  if (width === 360) return { west: -180, east: 180, south: latitude - height / 2, north: latitude + height / 2 };
  let west = longitude - width / 2;
  // Keep ordinary map views at their original edge. Journey windows can wrap.
  if (bounds.west >= -180 && bounds.east <= 180) west = clamp(west, -180, 180 - width);
  return { west, east: west + width, south: latitude - height / 2, north: latitude + height / 2 };
}

function mapPixel(bounds: MapBounds, frame: Frame, longitude: number, latitude: number): Pixel {
  return {
    x: frame.x + (longitude - bounds.west) / (bounds.east - bounds.west) * frame.width,
    y: frame.y + (bounds.north - latitude) / (bounds.north - bounds.south) * frame.height,
  };
}

function inside(point: Pixel, frame: Frame, margin = 0) {
  return point.x >= frame.x + margin && point.x <= frame.x + frame.width - margin &&
    point.y >= frame.y + margin && point.y <= frame.y + frame.height - margin;
}

function textWidth(value: string, size: number) {
  return Array.from(value).reduce((width, char) => width + (
    /[ilI.,' :]/u.test(char) ? .3 : /[MW@&m]/u.test(char) ? .82 : .61
  ), 0) * size;
}

function text(x: number, y: number, value: string, attrs: string) {
  return `<text x="${number(x)}" y="${number(y)}" ${attrs}>${xml(value)}</text>`;
}

function titleLines(title: string, width: number, scale: number) {
  const words = title.trim().split(/\s+/);
  const preferred = Math.round(145 * scale);
  const longTitle = textWidth(title, preferred) > width;
  if (!longTitle) return { lines: [title], size: preferred };
  const characters = Array.from(title);
  const midpoint = Math.ceil(characters.length / 2);
  const balanced = words.slice(1).map((_, index) => [
    words.slice(0, index + 1).join(" "), words.slice(index + 1).join(" "),
  ]).reduce<string[] | null>((best, lines) => {
    const widest = (parts: string[]) => Math.max(...parts.map((line) => textWidth(line, 1)));
    return !best || widest(lines) < widest(best) ? lines : best;
  }, null) ?? [characters.slice(0, midpoint).join(""), characters.slice(midpoint).join("")];
  const size = Math.floor(Math.min(117 * scale, ...balanced.map((line) => width * .98 / Math.max(1, textWidth(line, 1)))));
  return { lines: balanced, size };
}

function header(input: MapAlbumArtworkInput, layout: Layout, { paper, ink, muted, rule }: Palette) {
  const { height, width, margin } = layout;
  const title = input.title.trim();
  const formatting = input.formatting;
  const typeface = formatting?.typeface === "sans" ? sans : serif;
  const centered = formatting?.alignment === "center";
  const x = centered ? width / 2 : margin;
  const textAnchor = centered ? 'text-anchor="middle"' : '';
  const { lines, size } = title
    ? titleLines(title, width - 2 * margin, clamp(formatting?.titleScale ?? 100, 75, 130) / 100)
    : { lines: [], size: 0 };
  const background = `<rect width="${width}" height="${height}" fill="${paper}"/>`;
  if (!title && !input.subtitle?.trim() && !input.period?.trim()) return { mapTop: margin, svg: background };
  const titleTop = 124;
  let lastBaseline = titleTop + size * (1 + (lines.length - 1) * .99);
  const captions = [input.subtitle, input.period].flatMap((value) => {
    const caption = value?.trim();
    if (!caption) return [];
    const captionSize = Math.min(44, (width - margin * 2 - 8) / Math.max(1, textWidth(caption, 1)));
    lastBaseline += Math.max(57, captionSize + 17);
    return [text(x + (centered ? 0 : 3), lastBaseline, caption, `fill="${muted}" font-family="${sans}" font-size="${number(captionSize)}" ${textAnchor}`)];
  });
  const divider = number(lastBaseline + 40);
  return {
    mapTop: divider + 45,
    svg: `${background}
      ${lines.map((line, index) => text(x, titleTop + size * (1 + index * .99), line,
        `fill="${ink}" font-family="${typeface}" font-size="${size}" font-weight="600" ${textAnchor}`)).join("")}
      ${captions.join("")}
      <line x1="${margin}" x2="${width - margin}" y1="${divider}" y2="${divider}" stroke="${rule}" stroke-width="2"/>`,
  };
}

function stationList(stops: readonly MapAlbumStop[], layout: Layout, top: number, bottom: number, continued: boolean,
  transportOnly: boolean, { ink, muted, rule, routeInk, paper }: Palette, markerNumbers: ReadonlyMap<string, number>,
  formatting?: AlbumFormatting) {
  if (!stops.length) return "";
  const { margin, width } = layout;
  const heading = transportOnly ? "Verkehrsmittel nach Zielstopp" : continued ? "Weitere Stationen" : "Stationen";
  const columns = formatting?.listColumns ?? 2;
  const typeface = formatting?.typeface === "sans" ? sans : serif;
  const numberInk = readableOn(paper, routeInk);
  const columnWidth = (width - margin * 2 - (columns - 1) * 68) / columns;
  const rowHeight = Math.min(161, (bottom - top - 87) / Math.ceil(stops.length / columns));
  return text(margin, top + 47, heading, `fill="${ink}" font-family="${typeface}" font-size="58"`) +
    stops.map((stop, index) => {
      const col = index % columns;
      const x = margin + col * (columnWidth + 68);
      const y = top + 81 + Math.floor(index / columns) * rowHeight;
      const label = stop.title.trim() || `Stopp ${stop.number}`;
      const titleSize = Math.min(48, (columnWidth - 105) / Math.max(1, textWidth(label, 1)));
      const arrival = stop.arrivalMode ? `Anreise: ${albumTransports[stop.arrivalMode].label}` : "";
      const markerNumber = markerNumbers.get(stop.id);
      const subtitle = [
        transportOnly ? "" : stop.subtitle, arrival, markerNumber ? `Kartenpunkt ${markerNumber}` : "",
      ].filter(Boolean).join(" · ");
      const subSize = Math.min(33, (columnWidth - 105) / Math.max(1, textWidth(subtitle, 1)));
      return `<g data-album-stop="${xml(stop.id)}">
        <line x1="${number(x)}" x2="${number(x + columnWidth)}" y1="${number(y)}" y2="${number(y)}" stroke="${rule}" stroke-width="2"/>
        ${text(x + 1, y + 52, String(stop.number), `fill="${numberInk}" font-family="${typeface}" font-size="48" font-weight="600"`)}
        ${text(x + 90, y + 49, label, `fill="${ink}" font-family="${sans}" font-size="${number(titleSize)}" font-weight="600"`)}
        ${subtitle ? text(x + 90, y + 90, subtitle, `fill="${muted}" font-family="${sans}" font-size="${number(subSize)}"`) : ""}
      </g>`;
    }).join("");
}

function frameShape(frame: Frame, attrs: string) {
  return `<rect x="${frame.x}" y="${frame.y}" width="${frame.width}" height="${frame.height}" rx="32" ${attrs}/>`;
}

type Detail = typeof import("./world-detail.json");

function baseMap(detail: Detail, frame: Frame, bounds: MapBounds, style: MapStyle, raster: string, palette: Palette) {
  const scale = 360 / (bounds.east - bounds.west);
  const landscape = style === "landscape";
  const satellite = style === "satellite";
  const reliefOpacity = number(.57 * Math.min(1, Math.sqrt(6 / scale)));
  const waters = [
    ...overviewWater.rivers, ...detail.rivers,
  ].filter(({ minZoom }) => minZoom <= scale * 1.7).map(({ path }) =>
    `<path d="${path}" fill="none" stroke="${palette.river}" stroke-opacity=".68" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join("");
  const lakes = [
    ...overviewWater.lakes, ...detail.lakes,
  ].filter(({ minZoom }) => minZoom <= scale * 1.7).map(({ path }) =>
    `<path d="${path}" fill="${palette.lake}" fill-rule="evenodd"/>`).join("");
  const base = satellite
    ? `<image data-album-satellite="true" href="${raster}" width="${atlasWidth}" height="${atlasHeight}" preserveAspectRatio="none"/>`
    : `<use href="#album-land" fill="${landscape ? palette.reliefLand : palette.land}" stroke="${palette.shore}" stroke-width="2.4"/>
      ${raster ? `<image href="${raster}" width="${atlasWidth}" height="${atlasHeight}" preserveAspectRatio="none" clip-path="url(#album-land-clip)" opacity="${reliefOpacity}" style="mix-blend-mode:multiply"/>` : ""}`
      + waters + lakes;
  const border = satellite ? "#f2e8c8" : palette.border;
  const tile = `<g id="album-atlas">
    ${base}
    <path d="${detail.borders.international}" fill="none" stroke="${border}" stroke-opacity=".75" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <path d="${detail.borders.other}" fill="none" stroke="${border}" stroke-opacity=".58" stroke-width="1.7" stroke-dasharray="6 6" vector-effect="non-scaling-stroke"/>
  </g>`;
  const minTile = Math.floor((bounds.west + 180) / 360);
  const maxTile = Math.ceil((bounds.east + 180) / 360) - 1;
  const tiles = Array.from({ length: maxTile - minTile + 1 }, (_, i) =>
    `<use href="#album-atlas" x="${(minTile + i) * atlasWidth}"/>`).join("");
  return `${frameShape(frame, `fill="${palette.sea}"`)}
    <svg x="${frame.x}" y="${frame.y}" width="${frame.width}" height="${frame.height}"
      viewBox="${atlasX(bounds.west)} ${atlasY(bounds.north)} ${atlasX(bounds.east) - atlasX(bounds.west)} ${atlasY(bounds.south) - atlasY(bounds.north)}" preserveAspectRatio="none">
      <defs>
        ${!satellite ? `<path id="album-land" d="${detail.land}" fill-rule="evenodd" clip-rule="evenodd" vector-effect="non-scaling-stroke"/>` : ""}
        ${landscape ? `<clipPath id="album-land-clip"><use href="#album-land"/></clipPath>` : ""}
        ${tile}
      </defs>
      ${tiles}
    </svg>`;
}

function clipLine(start: Pixel, end: Pixel, frame: Frame): [Pixel, Pixel] | null {
  const dx = end.x - start.x, dy = end.y - start.y;
  let from = 0, to = 1;
  for (const [delta, min, max] of [
    [dx, frame.x - start.x, frame.x + frame.width - start.x],
    [dy, frame.y - start.y, frame.y + frame.height - start.y],
  ]) {
    if (delta === 0) {
      if (min > 0 || max < 0) return null;
      continue;
    }
    from = Math.max(from, Math.min(min / delta, max / delta));
    to = Math.min(to, Math.max(min / delta, max / delta));
    if (from >= to) return null;
  }
  const at = (position: number) => ({ x: start.x + dx * position, y: start.y + dy * position });
  return [at(from), at(to)];
}

function transportIcon(mode: AlbumTransport, x: number, y: number, color: string, size: number) {
  return `<path d="${albumTransports[mode].path}" transform="translate(${number(x - size / 2)} ${number(y - size / 2)}) scale(${size / 24})"
    fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
}

function journeyLines(connections: readonly AlbumConnection[], bounds: MapBounds, frame: Frame, { routeInk, paper }: Palette,
  clipFrame = frame) {
  const center = (bounds.west + bounds.east) / 2;
  const badges: string[] = [];
  const used: Pixel[] = [];
  const underlay = readableOn(routeInk, paper, 3);
  const badgeInk = readableOn(paper, routeInk);
  const drawn = new Set<string>();
  const lines = connections.flatMap(({ from, to, transport }) => {
    const base = nearLongitude(from.longitude, center);
    const finish = base + direction(from.longitude, to.longitude);
    const segments = [-360, 0, 360].flatMap((shift) => {
      const start = mapPixel(bounds, frame, base + shift, from.latitude);
      const end = mapPixel(bounds, frame, finish + shift, to.latitude);
      const clipped = clipLine(start, end, clipFrame);
      if (!clipped) return [];
      const [a, b] = clipped;
      const dx = b.x - a.x, dy = b.y - a.y;
      const length = Math.hypot(dx, dy);
      const angle = number(Math.atan2(dy, dx) * 180 / Math.PI);
      const x = number(a.x + dx * .62), y = number(a.y + dy * .62);
      const key = [a.x, a.y, b.x, b.y].map((coordinate) => Math.round(coordinate / 12)).join(":");
      const duplicate = drawn.has(key);
      drawn.add(key);
      const line = `<line x1="${number(a.x)}" y1="${number(a.y)}" x2="${number(b.x)}" y2="${number(b.y)}"`;
      return [{ a, dx, dy, length, svg: duplicate ? "" : `${line} stroke="${underlay}" stroke-width="16" stroke-linecap="round"/>
        ${line} data-album-connection="true"
        stroke="${routeInk}" stroke-width="10" stroke-dasharray="17 19" stroke-linecap="round"/>
        ${length > 132 ? `<path d="M-13 -12 L4 0 L-13 12" fill="none" stroke="${routeInk}" stroke-width="9"
          stroke-linecap="round" stroke-linejoin="round" transform="translate(${x} ${y}) rotate(${angle})"/>` : ""}` }];
    });
    if (transport) {
      const candidates = [...segments].sort((a, b) => b.length - a.length)
        .filter(({ length }) => length > 110)
        .flatMap(({ a, dx, dy }) => [.37, .52, .72].map((fraction) => ({
          x: number(a.x + dx * fraction), y: number(a.y + dy * fraction),
        })));
      const position = candidates.find((point) => inside(point, frame, 32) &&
        used.every((other) => Math.hypot(point.x - other.x, point.y - other.y) > 70));
      if (position) {
        used.push(position);
        badges.push(`<g data-album-transport="${transport}"><circle cx="${position.x}" cy="${position.y}" r="29"
          fill="${paper}" stroke="${badgeInk}" stroke-width="4"/>${transportIcon(transport, position.x, position.y, badgeInk, 37)}</g>`);
      }
    }
    return segments.map(({ svg }) => svg);
  });
  return lines.join("") + badges.join("");
}

const markerOffsets: Pixel[] = [{ x: 0, y: 0 }, ...[1, 1.7, 2.4, 3.2, 4.1].flatMap((radius) =>
  Array.from({ length: 8 }, (_, index) => ({
    x: radius * Math.cos(index * Math.PI / 4),
    y: radius * Math.sin(index * Math.PI / 4),
  })))];

function albumMarkers(input: MapAlbumArtworkInput, stops: readonly MapAlbumStop[], bounds: MapBounds, frame: Frame,
  { mapInk, paper, routeInk, labelInk, labelHalo }: Palette) {
  const mapOnly = stops.length > 0 && input.showStopsList === false;
  const points: (AlbumPosition & { label: string; stop?: MapAlbumStop })[] = stops.length
    ? stops.map((stop) => ({ ...stop, label: String(stop.number), stop }))
    : (input.points ?? []).map((point, index) => ({ ...point, label: String(point.count ?? index + 1) }));
  const radius = number((mapOnly ? 22 : points.length > 100 ? 30 : 41) * clamp(input.markerScale ?? 100, 70, 140) / 100);
  const placed: Pixel[] = [];
  const markerNumbers = new Map<string, number>();
  const center = (bounds.west + bounds.east) / 2;
  const color = stops.length ? routeInk : mapInk;
  const markerInk = readableOn(paper, color);
  const ring = contrast(paper, color) >= 3 ? "" : `stroke="${markerInk}" stroke-width="10"`;
  const groups: { origin: Pixel; members: typeof points }[] = [];
  for (const point of points) {
    const origin = mapPixel(bounds, frame, nearLongitude(point.longitude, center), point.latitude);
    if (!inside(origin, frame)) continue;
    const close = point.stop && groups.find(({ origin: other }) =>
      Math.hypot(origin.x - other.x, origin.y - other.y) < Math.max(38, radius * 1.5));
    if (close) close.members.push(point);
    else groups.push({ origin, members: [point] });
  }

  const labels: { x: number; y: number; title: string; visits: number }[] = [];
  const svg = groups.map(({ members, origin }) => {
    const first = members[0];
    const visits = members.length;
    if (!mapOnly && first.stop) {
      const number = first.stop.number;
      for (const { stop } of members.slice(1)) if (stop) markerNumbers.set(stop.id, number);
    }
    const label = mapOnly ? visits > 1 ? String(visits) : "" : visits > 1 ? `${first.label}+` : first.label;
    const candidate = markerOffsets.map((offset) => ({
      x: origin.x + offset.x * radius * 2.22,
      y: origin.y + offset.y * radius * 2.22,
    })).find((position) => inside(position, frame, radius + 10) &&
      placed.every((other) => Math.hypot(position.x - other.x, position.y - other.y) >= radius * 2.18)) ??
      { x: clamp(origin.x, frame.x + radius + 10, frame.x + frame.width - radius - 10),
        y: clamp(origin.y, frame.y + radius + 10, frame.y + frame.height - radius - 10) };
    placed.push(candidate);
    const { x, y } = candidate;
    const placeName = first.stop?.placeName;
    if (mapOnly && placeName && members.every(({ stop }) => stop?.placeName === placeName)) {
      labels.push({ x, y, title: placeName, visits });
    }
    const fontSize = Math.min(radius, radius * 2.55 / label.length);
    const grouped = visits > 1 ? ` data-album-visits="${members.map(({ stop }) => stop?.number).join(",")}"` : "";
    return `<g data-album-marker="${xml(first.label)}"${grouped}>
      ${x !== origin.x || y !== origin.y ? `<line x1="${number(origin.x)}" y1="${number(origin.y)}" x2="${number(x)}" y2="${number(y)}" stroke="${color}" stroke-width="4"/>` : ""}
      ${ring ? `<circle cx="${number(x)}" cy="${number(y)}" r="${radius}" fill="none" ${ring}/>` : ""}
      <circle cx="${number(x)}" cy="${number(y)}" r="${radius}" fill="${label ? paper : color}" stroke="${label ? color : paper}" stroke-width="6"/>
      ${label ? text(x, y + fontSize * .34, label, `fill="${markerInk}" text-anchor="middle" font-family="${sans}" font-weight="700" font-size="${number(fontSize)}"`) : ""}
    </g>`;
  }).join("");
  const labelSize = 39;
  const labelBoxes: Frame[] = [];
  const placeLabels = labels.sort((a, b) => b.visits - a.visits).map(({ x, y, title }) => {
    const width = textWidth(title, labelSize) + 16;
    const height = labelSize + 8;
    const offsets = [
      { x: x + radius + 12, y: y - height / 2 },
      { x: x - radius - width - 12, y: y - height / 2 },
      { x: x - width / 2, y: y + radius + 12 },
      { x: x - width / 2, y: y - radius - height - 12 },
    ];
    const position = offsets.find((point) => inside(point, frame, 6) &&
      inside({ x: point.x + width, y: point.y + height }, frame, 6) &&
      placed.every((marker) => marker.x + radius < point.x || marker.x - radius > point.x + width ||
        marker.y + radius < point.y || marker.y - radius > point.y + height) &&
      labelBoxes.every((box) => box.x + box.width < point.x || box.x > point.x + width ||
        box.y + box.height < point.y || box.y > point.y + height));
    if (!position) return "";
    labelBoxes.push({ ...position, width, height });
    return text(position.x + 8, position.y + labelSize, title, `data-album-place-label="true" fill="${labelInk}"
      paint-order="stroke" stroke="${labelHalo}" stroke-width="7" stroke-linejoin="round" font-family="${sans}"
      font-size="${labelSize}" font-weight="700"`);
  }).join("");
  return { svg: svg + placeLabels, markerNumbers, positions: placed.map((position) => ({ ...position, radius })) };
}

function countryNames(bounds: MapBounds, frame: Frame, markers: readonly (Pixel & { radius: number })[], { labelInk, labelHalo }: Palette) {
  const center = (bounds.west + bounds.east) / 2;
  const zoom = 360 / (bounds.east - bounds.west);
  const used = markers.map(({ x, y, radius }) => ({ left: x - radius - 12, right: x + radius + 12,
    top: y - radius - 12, bottom: y + radius + 12 }));
  return [...countries].sort((left, right) => right.priority - left.priority).map((country) => {
    if (country.minZoom > zoom * 1.35) return "";
    const { x, y } = mapPixel(bounds, frame, nearLongitude(country.longitude, center), country.latitude);
    const halfWidth = textWidth(country.label, 35) / 2 + 11;
    const box = { left: x - halfWidth, right: x + halfWidth, top: y - 22, bottom: y + 22 };
    if (!inside({ x: box.left, y: box.top }, frame, 11) || !inside({ x: box.right, y: box.bottom }, frame, 11)) return "";
    if (used.some((area) => box.left < area.right && box.right > area.left && box.top < area.bottom && box.bottom > area.top)) return "";
    used.push(box);
    return text(x, y + 11, country.label, `fill="${labelInk}" paint-order="stroke" stroke="${labelHalo}" stroke-width="6"
      stroke-linejoin="round" text-anchor="middle" font-family="${sans}" font-weight="600" font-size="35"`);
  }).join("");
}

const embeddedRasters = new Map<"landscape" | "satellite", Promise<string>>();

async function embedMapRaster(style: "landscape" | "satellite") {
  const asset = style === "satellite" ? satelliteSource.path : "map/world-relief.webp";
  const response = await fetch(new URL(asset, document.baseURI));
  if (!response.ok) throw new Error("Die Kartenebene konnte nicht geladen werden.");
  const data = await response.blob();
  if (style === "satellite") {
    const url = URL.createObjectURL(data);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Die Kartenebene konnte nicht gelesen werden."));
    reader.readAsDataURL(new Blob([data], { type: "image/webp" }));
  });
}

function readMapRaster(style: "landscape" | "satellite") {
  const cached = embeddedRasters.get(style);
  if (cached) return cached;
  const embedded = embedMapRaster(style).catch((error) => {
    embeddedRasters.delete(style);
    throw error;
  });
  embeddedRasters.set(style, embedded);
  return embedded;
}

export async function buildMapAlbumArtwork(input: MapAlbumArtworkInput): Promise<MapAlbumPage[]> {
  const dpi = input.dpi ?? 300;
  const size = albumOutputSize(input.format, dpi, input.dimensions);
  if (!size) throw new Error("Die Bildgröße wird nicht unterstützt.");
  const layout = drawingLayout(input.format, size, input.formatting?.margins);
  const palette = artworkPalette(input);
  const { width, height, margin, mapHeight } = layout;
  const allStops = input.stops ?? [];
  const transportOnly = input.showStopsList === false;
  const connections = input.connections ?? [];
  const rawSubject = input.fitJourney && (allStops.length || connections.length)
    ? fitAlbumJourney(allStops, connections, input.journeyPadding) : input.bounds;
  const wraps = rawSubject.west > rawSubject.east;
  const subject = wraps ? { ...rawSubject, east: rawSubject.east + 360 } : rawSubject;
  const subjectCenter = (subject.west + subject.east) / 2;
  const cropJourney = input.fitJourney === false && allStops.length > 0;
  const stops = cropJourney ? allStops.filter(({ latitude, longitude }) => {
    const x = nearLongitude(longitude, subjectCenter);
    return x >= subject.west && x <= subject.east && latitude >= subject.south && latitude <= subject.north;
  }) : allStops;
  const [{ default: detail }, raster] = await Promise.all([
    import("./world-detail.json"),
    input.style === "satellite" ? readMapRaster("satellite").catch(() => "")
      : input.style === "landscape" ? readMapRaster("landscape") : Promise.resolve(""),
  ]);
  const satelliteFallback = input.style === "satellite" && !raster;
  const style = satelliteFallback ? "standard" : input.style;
  const mapPalette = style === "satellite" ? { ...palette, labelInk: "#f4f0de", labelHalo: "#17211b" } : palette;
  const head = header(input, layout, palette);
  const columns = input.formatting?.listColumns ?? 2;
  const availableRows = Math.max(1, Math.floor((height - 305 - head.mapTop - mapHeight) / 112));
  const firstStops = Math.min(availableRows, Math.ceil(layout.firstStops / 2)) * columns;
  const continuedStops = Math.min(Math.ceil(layout.continuedStops / 2), Math.floor((height - 299 - head.mapTop) / 112)) * columns;
  const listedStops = transportOnly ? stops.filter((stop) => stop.arrivalMode).slice(0, firstStops) : stops;
  const availableHeight = height - head.mapTop - 245;
  const frameWidth = width - 2 * margin;
  const coverRows = Math.ceil(Math.min(listedStops.length, firstStops) / columns);
  const frameHeight = coverRows
    ? Math.max(mapHeight, Math.min(frameWidth * 1.05, availableHeight - 72 - coverRows * 142))
    : Math.min(availableHeight, frameWidth * (allStops.length ? 1.15 : .95));
  const frameArea = { x: margin, y: number(head.mapTop + (listedStops.length ? 0 : (availableHeight - frameHeight) * .38)),
    width: frameWidth, height: number(frameHeight) };
  const padding = clamp(input.contextPercent ?? (input.wideFrame ? 52 : 0), 0, 100) / 200;
  const longitudePadding = (subject.east - subject.west) * padding;
  const latitudePadding = (subject.north - subject.south) * padding;
  const bounds = fitFrame({
    west: input.fitJourney || wraps ? subject.west - longitudePadding : Math.max(-180, subject.west - longitudePadding),
    east: input.fitJourney || wraps ? subject.east + longitudePadding : Math.min(180, subject.east + longitudePadding),
    south: Math.max(-90, subject.south - latitudePadding),
    north: Math.min(90, subject.north + latitudePadding),
  }, frameArea);
  const scale = Math.min(frameArea.width / (bounds.east - bounds.west), frameArea.height / (bounds.north - bounds.south));
  const fittedWidth = (bounds.east - bounds.west) * scale;
  const fittedHeight = (bounds.north - bounds.south) * scale;
  const frame = { x: number(frameArea.x + (frameArea.width - fittedWidth) / 2),
    y: number(frameArea.y + (frameArea.height - fittedHeight) / 2),
    width: number(fittedWidth), height: number(fittedHeight) };
  const markers = albumMarkers(input, stops, bounds, frame, mapPalette);
  const cropStart = mapPixel(bounds, frame, subject.west, subject.north);
  const cropEnd = mapPixel(bounds, frame, subject.east, subject.south);
  const clipFrame = cropJourney
    ? { ...cropStart, width: cropEnd.x - cropStart.x, height: cropEnd.y - cropStart.y } : frame;
  const plate = `<defs><clipPath id="album-map-clip">${frameShape(frame, "")}</clipPath></defs>
    <g clip-path="url(#album-map-clip)">
      ${baseMap(detail, frame, bounds, style, raster, mapPalette)}
      ${input.showCountryNames ? countryNames(bounds, frame, markers.positions, mapPalette) : ""}
      ${journeyLines(connections, bounds, frame, palette, clipFrame)}${markers.svg}
    </g>
    ${frameShape(frame, 'fill="none" stroke="#9ead9f" stroke-width="3"')}`;

  const groups = [listedStops.slice(0, firstStops)];
  for (let start = firstStops; start < listedStops.length; start += continuedStops) groups.push(listedStops.slice(start, start + continuedStops));

  return groups.map((group, index) => {
    const continuation = index > 0;
    const listTop = continuation ? head.mapTop + 35 : frameArea.y + frameArea.height + 41;
    const grouped = markers.markerNumbers.size > 0;
    const legend = continuation ? "" : allStops.length
      ? transportOnly ? "Zahlen zeigen mehrere Stopps am selben Kartenpunkt. Linien verbinden Aufnahmeorte."
        : grouped ? "+ kennzeichnet mehrere Stopps am selben Kartenpunkt. Linien verbinden Aufnahmeorte."
          : "Gestrichelte Linien verbinden Aufnahmeorte in zeitlicher Reihenfolge."
      : input.points?.length ? "Zahlen zeigen die Anzahl der Fotos je Kartenpunkt." : "";
    const pageNumber = groups.length > 1 ? `${index + 1} / ${groups.length}` : "";
    const footer = `<line x1="${margin}" x2="${width - margin}" y1="${height - 147}" y2="${height - 147}" stroke="${palette.rule}" stroke-width="2"/>
      ${text(margin, height - 92, continuation ? "Fotokarte · Stationen" : "Kartendaten von Natural Earth", `fill="${palette.muted}" font-family="${sans}" font-size="31"`)}
      ${legend ? text(margin + 515, height - 92, legend, `fill="${palette.muted}" font-family="${sans}" font-size="31"`) : ""}
      ${!continuation && style === "satellite" ? text(margin, height - 47, `Satellitenbild von ${satelliteSource.name}`,
        `fill="${palette.muted}" font-family="${sans}" font-size="31"`) : ""}
      ${pageNumber ? text(width - margin, height - 92, pageNumber, `fill="${palette.ink}" font-family="${sans}" font-size="31" text-anchor="end"`) : ""}`;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size.width}" height="${size.height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <title>${xml(input.title.trim() || "Meine Fotokarte")}</title>
      ${head.svg}${index === 0 ? plate : ""}${stationList(group, layout, listTop, height - 177, continuation, transportOnly,
        palette, markers.markerNumbers, input.formatting)}${footer}
    </svg>`;
    return { svg, ...size, dpi, number: index + 1, total: groups.length, ...(!continuation && satelliteFallback && { satelliteFallback }),
      ...(cropJourney && { visibleStopCount: stops.length }) };
  });
}

function densityChunk(dpi: AlbumDpi) {
  const chunk = new Uint8Array(21);
  const view = new DataView(chunk.buffer);
  view.setUint32(0, 9);
  chunk.set([112, 72, 89, 115], 4); // pHYs
  const printerPixelsPerMeter = Math.round(dpi / .0254);
  view.setUint32(8, printerPixelsPerMeter);
  view.setUint32(12, printerPixelsPerMeter);
  chunk[16] = 1;
  let crc = 0xFFFFFFFF;
  for (const byte of chunk.subarray(4, 17)) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
  }
  view.setUint32(17, crc ^ 0xFFFFFFFF);
  return chunk;
}

/** Set the PNG pixel density so print applications use the intended dimensions. */
export async function setMapAlbumPngDensity(png: Blob, dpi: AlbumDpi = 300): Promise<Blob> {
  const data = await png.arrayBuffer();
  const view = new DataView(data);
  const chunks: BlobPart[] = [data.slice(0, 8)];
  for (let offset = 8; offset + 12 <= data.byteLength;) {
    const length = view.getUint32(offset);
    const type = view.getUint32(offset + 4);
    const end = offset + 12 + length;
    if (end > data.byteLength) throw new Error("Die PNG-Datei ist unvollständig.");
    if (type !== 0x70485973) chunks.push(data.slice(offset, end)); // pHYs
    if (type === 0x49484452) chunks.push(densityChunk(dpi)); // IHDR
    offset = end;
  }
  return new Blob(chunks, { type: "image/png" });
}

export async function mapAlbumPageToPng(page: MapAlbumPage): Promise<Blob> {
  const url = URL.createObjectURL(new Blob([page.svg], { type: "image/svg+xml;charset=utf-8" }));
  const canvas = document.createElement("canvas");
  canvas.width = page.width;
  canvas.height = page.height;
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Das Bild konnte nicht gerendert werden.");
    context.drawImage(image, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Die PNG-Datei konnte nicht erstellt werden."));
    }, "image/png"));
    return await setMapAlbumPngDensity(png, page.dpi);
  } finally {
    URL.revokeObjectURL(url);
    canvas.width = 0;
    canvas.height = 0;
  }
}
