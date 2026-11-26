import { useEffect, useState } from "react";
import { API_BASE, apiFetch, readApiError } from "../../utils/api";
import { findNearbyPlace, loadJourneyPlaces } from "./journeyPlaces";
import type { TravelHome } from "./journeyTrips";
import type { MapBounds } from "./WorldMap";

type Cell = {
  latitude: number;
  longitude: number;
  photo_count: number;
  first_day?: string;
  last_day?: string;
};

export type ObservedPlace = TravelHome & { count: number; bounds: MapBounds };
const coord = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const nameKey = (value: string) => value.toLocaleLowerCase("de").normalize("NFD").replace(/\p{Diacritic}/gu, "");

function cellBounds(cell: Cell, pad: number): MapBounds {
  return {
    west: Math.max(-180, cell.longitude - pad), east: Math.min(180, cell.longitude + pad),
    south: Math.max(-90, cell.latitude - pad), north: Math.min(90, cell.latitude + pad),
  };
}

interface Props {
  from: string;
  to: string;
  albumId: string;
  onSelect: (place: ObservedPlace) => void;
  onSetHome?: (place: TravelHome) => void;
}

export default function MapObservedPlaces({ from, to, albumId, onSelect, onSetHome }: Props) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [places, setPlaces] = useState<ObservedPlace[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const search = new URLSearchParams({ offset: String(offset) });
    if (from) search.set("from_date", from);
    if (to) search.set("to_date", to);
    if (albumId) search.set("album_id", albumId);
    setLoading(true);
    setError("");
    const request = apiFetch(`${API_BASE}/map/observed-places?${search}`, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(await readApiError(response, "Fotoorte konnten nicht geladen werden."));
      return response.json() as Promise<{ places: Cell[]; next_offset: number | null }>;
    });
    void Promise.all([request, loadJourneyPlaces().catch(() => [])]).then(([data, locations]) => {
      if (controller.signal.aborted) return;
      setPlaces((current) => {
        const grouped = [...(offset ? current : [])];
        const byName = new Map<string, number[]>();
        grouped.forEach((place, index) => byName.set(place.name, [...(byName.get(place.name) ?? []), index]));
        for (const cell of data.places) {
          const nearby = findNearbyPlace(locations, cell.latitude, cell.longitude);
          const title = nearby?.name ?? `${coord.format(cell.latitude)}°, ${coord.format(cell.longitude)}°`;
          const bounds = cellBounds(cell, nearby ? .014 : .009);
          const known = nearby ? byName.get(title)?.find((index) => Math.abs(grouped[index].latitude - cell.latitude) < .28
            && Math.abs(grouped[index].longitude - cell.longitude) < .36) : undefined;
          if (known !== undefined) {
            const previous = grouped[known];
            grouped[known] = { ...previous, count: previous.count + cell.photo_count, bounds: {
              west: Math.min(previous.bounds.west, bounds.west), east: Math.max(previous.bounds.east, bounds.east),
              south: Math.min(previous.bounds.south, bounds.south), north: Math.max(previous.bounds.north, bounds.north),
            } };
          } else {
            byName.set(title, [...(byName.get(title) ?? []), grouped.length]);
            grouped.push({ latitude: cell.latitude, longitude: cell.longitude, name: title, count: cell.photo_count, bounds });
          }
        }
        return grouped;
      });
      setNext(data.next_offset);
      setLoading(false);
    }).catch((failure) => {
      if (controller.signal.aborted) return;
      setError(failure instanceof Error ? failure.message : "Fotoorte konnten nicht geladen werden.");
      setLoading(false);
    });
    return () => controller.abort();
  }, [open, from, to, albumId, offset, retry]);

  const visible = places.filter((place) => nameKey(place.name).includes(nameKey(query.trim())));
  return <details className="gps-map-discovery" onToggle={(event) => {
    const expanded = event.currentTarget.open;
    setOpen(expanded);
    if (expanded) { setOffset(0); setPlaces([]); setNext(null); }
  }}>
    <summary>Orte aus meinen Fotos</summary>
    {open && <div className="gps-map-discovery__content">
      <label>Fotoorte suchen<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <p>Wähle den Kartenbereich um einen gefundenen Fotoort.</p>
      <div className="gps-map-discovery__places">
        {visible.map((place) => <div key={`${place.latitude}:${place.longitude}`}>
          <button type="button" onClick={() => onSelect(place)}>{place.name} <small>{place.count} {place.count === 1 ? "Foto" : "Fotos"}</small></button>
          {onSetHome && <button type="button" onClick={() => onSetHome(place)} aria-label={`${place.name} als Heimatort wählen`}>Als Heimatort</button>}
        </div>)}
      </div>
      {loading && <p role="status">Fotoorte werden geladen …</p>}
      {error && <p role="alert">{error} <button type="button" onClick={() => setRetry((value) => value + 1)}>Erneut versuchen</button></p>}
      {!loading && !error && visible.length === 0 && <p>Für diese Auswahl wurden keine passenden Orte gefunden.</p>}
      {next !== null && <button type="button" disabled={loading} onClick={() => setOffset(next)}>Weitere Orte laden</button>}
    </div>}
  </details>;
}
