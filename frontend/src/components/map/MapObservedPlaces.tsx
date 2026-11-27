import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, apiFetch, readApiError } from "../../utils/api";
import { countWithLabel } from "../../utils/germanFormatting";
import { findNearbyPlace, loadJourneyPlaces, type JourneyPlace } from "./journeyPlaces";
import type { TravelHome } from "./journeyTrips";
import type { MapBounds } from "./WorldMap";
import "./MapObservedPlaces.css";

type Cell = {
  latitude: number;
  longitude: number;
  photo_count: number;
  year_counts: Record<string, number>;
};

type PlaceYear = { count: number; bounds: MapBounds };
export type ObservedPlace = TravelHome & { count: number; bounds: MapBounds };
export type ObservedPlacePeriod = { from: string; to: string };
type PlaceGroup = ObservedPlace & { years: Record<string, PlaceYear> };
type PlacePage = { scope: string; groups: ReadonlyMap<string, PlaceGroup>; nextOffset: number | null };

const PAGE_SIZE = 300;
const RESULTS_PER_PAGE = 30;
const coord = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const nameKey = (value: string) => value.toLocaleLowerCase("de").normalize("NFD")
  .replace(/\p{Diacritic}/gu, "").replace(/ß/g, "ss");
const photoCount = (count: number) => countWithLabel(count, "Foto", "Fotos");
const wrapLongitude = (longitude: number) => ((longitude + 180) % 360 + 360) % 360 - 180;

function cellBounds(cell: Cell, anchor: number, pad: number): MapBounds {
  const longitude = anchor + wrapLongitude(cell.longitude - anchor);
  return {
    west: longitude - pad, east: longitude + pad,
    south: Math.max(-90, cell.latitude - pad), north: Math.min(90, cell.latitude + pad),
  };
}

function union(a: MapBounds, b: MapBounds): MapBounds {
  return {
    west: Math.min(a.west, b.west), east: Math.max(a.east, b.east),
    south: Math.min(a.south, b.south), north: Math.max(a.north, b.north),
  };
}

function groupCells(cells: Cell[], locations: JourneyPlace[], previous?: ReadonlyMap<string, PlaceGroup>) {
  const groups = new Map(previous);
  for (const cell of cells) {
    const nearby = findNearbyPlace(locations, cell.latitude, cell.longitude);
    const latitude = nearby?.latitude ?? cell.latitude;
    const longitude = wrapLongitude(nearby?.longitude ?? cell.longitude);
    const name = nearby?.name ?? `${coord.format(latitude)}°, ${coord.format(longitude)}°`;
    const key = `${latitude}:${longitude}:${name}`;
    const bounds = cellBounds(cell, longitude, nearby ? .014 : .009);
    const existing = groups.get(key);
    const years = { ...existing?.years };
    for (const [year, count] of Object.entries(cell.year_counts ?? {})) {
      const yearEntry = years[year];
      years[year] = { count: count + (yearEntry?.count ?? 0), bounds: yearEntry ? union(yearEntry.bounds, bounds) : bounds };
    }
    groups.set(key, {
      name, latitude, longitude, years,
      count: cell.photo_count + (existing?.count ?? 0),
      bounds: existing ? union(existing.bounds, bounds) : bounds,
    });
  }
  return groups;
}

interface Props {
  active: boolean;
  revision: number;
  from: string;
  to: string;
  albumId: string;
  onSelect: (place: ObservedPlace, period?: ObservedPlacePeriod) => void;
  onSetHome?: (place: TravelHome) => void;
}

export default function MapObservedPlaces({ active, revision, from, to, albumId, onSelect, onSetHome }: Props) {
  const panel = useRef<HTMLDetailsElement>(null);
  const summary = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<PlacePage>();
  const [manualRequest, setManualRequest] = useState<{ scope: string; offset: number }>();
  const [failure, setFailure] = useState<{ scope: string; offset: number; message: string }>();
  const [visibleLimit, setVisibleLimit] = useState(RESULTS_PER_PAGE);
  const scope = JSON.stringify([from, to, albumId, revision]);
  const current = page?.scope === scope ? page : undefined;
  const searchTerm = nameKey(query.trim());
  const searchAll = Boolean(searchTerm);
  const nextOffset = current?.nextOffset;
  const moreRequested = manualRequest?.scope === scope && manualRequest.offset === nextOffset;
  const wantsPage = !current || searchAll || moreRequested;
  const offset = current ? current.nextOffset : 0;
  const error = wantsPage && failure?.scope === scope && failure.offset === offset ? failure.message : "";
  const loading = active && open && wantsPage && offset !== null && !error;

  useEffect(() => {
    if (!loading || offset === null) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) });
    if (from) params.set("from_date", from);
    if (to) params.set("to_date", to);
    if (albumId) params.set("album_id", albumId);
    const request = apiFetch(`${API_BASE}/map/observed-places?${params}`, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(await readApiError(response, "Fotoorte konnten nicht geladen werden."));
      return response.json() as Promise<{ places: Cell[]; next_offset: number | null }>;
    });
    void Promise.all([request, loadJourneyPlaces().catch(() => [])]).then(([data, locations]) => {
      if (controller.signal.aborted) return;
      setPage((previous) => ({
        scope, groups: groupCells(data.places, locations, offset && previous?.scope === scope ? previous.groups : undefined),
        nextOffset: data.next_offset,
      }));
    }).catch((reason) => {
      if (!controller.signal.aborted) setFailure({ scope, offset, message: reason instanceof Error ? reason.message : "Fotoorte konnten nicht geladen werden." });
    });
    return () => controller.abort();
  }, [loading, from, to, albumId, offset, scope]);

  const visible = useMemo(() => {
    if (!open || !current || (searchAll && current.nextOffset !== null)) return [];
    return [...current.groups].filter(([, place]) => {
      const transliterated = place.name.toLocaleLowerCase("de").replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue");
      return nameKey(place.name).includes(searchTerm) || nameKey(transliterated).includes(searchTerm);
    }).sort(([, a], [, b]) => b.count - a.count || a.name.localeCompare(b.name, "de"));
  }, [current, open, searchAll, searchTerm]);

  const choose = (place: PlaceGroup, year?: string) => {
    const selectedYear = year ? place.years[year] : undefined;
    const bounds = selectedYear?.bounds ?? place.bounds;
    let period: ObservedPlacePeriod | undefined;
    if (year) {
      const start = `${year}-01-01`;
      const end = `${year}-12-31`;
      period = { from: from > start ? from : start, to: to && to < end ? to : end };
    }
    onSelect({
      name: place.name, latitude: place.latitude, longitude: place.longitude,
      bounds: { ...bounds, west: wrapLongitude(bounds.west), east: wrapLongitude(bounds.east) },
      count: selectedYear?.count ?? place.count,
    }, period);
    if (panel.current) panel.current.open = false;
    summary.current?.focus();
  };

  const visibleMore = visible.length > visibleLimit;
  const moreAvailable = !searchAll && nextOffset != null && !loading && !error;

  return <details ref={panel} className="gps-map-discovery gps-observed-places" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary ref={summary}>Orte aus meinen Fotos</summary>
    {open && <div className="gps-map-discovery__content">
      <label>Fotoorte suchen<input type="search" value={query} onChange={(event) => {
        setQuery(event.target.value);
        setVisibleLimit(RESULTS_PER_PAGE);
      }} /></label>
      <div className="gps-map-discovery__places" aria-busy={loading}>
        {visible.slice(0, visibleLimit).map(([key, place]) => {
          const years = Object.keys(place.years).sort().reverse();
          const hasYearChoice = years.length > 1 || (years.length === 1 && place.years[years[0]].count < place.count);
          return <div key={key} className="gps-observed-places__place">
            <div className="gps-observed-places__actions">
              <button type="button" className="gps-observed-places__main" onClick={() => choose(place)}>
                <strong>{place.name}</strong> <small>{photoCount(place.count)}{years.length === 1 ? ` · ${years[0]}` : ""}</small>
              </button>
              {onSetHome && <button type="button" onClick={() => onSetHome(place)} aria-label={`${place.name} als Heimatort wählen`}>Als Heimatort</button>}
            </div>
            {hasYearChoice && <div className="gps-observed-places__years" role="group" aria-label={`Aufnahmejahre in ${place.name}`}>
              <span>Aufnahmejahre</span>
              {years.map((year) => <button type="button" key={year} aria-label={`${place.name} im Jahr ${year} anzeigen`}
                onClick={() => choose(place, year)}>{year} · {photoCount(place.years[year].count)}</button>)}
            </div>}
          </div>;
        })}
      </div>
      {loading && <p role="status">{searchAll ? "Alle passenden Fotoorte werden gesucht …" : "Fotoorte werden geladen …"}</p>}
      {error && <p role="alert">{error} <button type="button" onClick={() => setFailure(undefined)}>Erneut versuchen</button></p>}
      {!loading && !error && current && visible.length === 0 && <p>Für diese Auswahl wurden keine passenden Orte gefunden.</p>}
      {(visibleMore || moreAvailable) && <button type="button" onClick={() => {
        if (visibleMore) setVisibleLimit((limit) => limit + RESULTS_PER_PAGE);
        else if (nextOffset != null) {
          setManualRequest({ scope, offset: nextOffset });
          setVisibleLimit((limit) => limit + RESULTS_PER_PAGE);
        }
      }}>Weitere Orte anzeigen</button>}
    </div>}
  </details>;
}
