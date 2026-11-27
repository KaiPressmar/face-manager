import { API_BASE, apiFetch, fetchMapImages, readApiError, type MapImageCursor, type MapImagesResponse } from "../../utils/api";
import type { GeoPhotoSelection } from "../../utils/geoPhotoSelection";
import type { MapBounds } from "./WorldMap";

export interface JourneyStop {
  id: string;
  number: number;
  section: number;
  latitude: number;
  longitude: number;
  start: string;
  end: string;
  start_utc?: string | null;
  end_utc?: string | null;
  image_ids: number[];
  inferred_image_ids?: number[];
  uncertain: boolean;
}

interface JourneyPoint {
  latitude: number;
  longitude: number;
}

export interface Journey {
  stops: JourneyStop[];
  connections: { from: JourneyPoint; to: JourneyPoint; from_stop_id: string; to_stop_id: string }[];
  photo_count: number;
  missing_location_count: number;
  inferred_location_count?: number;
  missing_time_count: number;
  uncertain_count: number;
  indexing: boolean;
}

export type JourneyDetail = "trip" | "city";

export async function fetchMapYears(albumId: string, signal: AbortSignal): Promise<string[]> {
  const query = albumId ? `?${new URLSearchParams({ album_id: albumId })}` : "";
  const response = await apiFetch(`${API_BASE}/map/years${query}`, { signal });
  if (!response.ok) throw new Error(await readApiError(response, "Aufnahmejahre konnten nicht geladen werden."));
  const { years } = await response.json() as { years: string[] };
  return years;
}

export function omitInferredPhotos(journey: Journey): Journey {
  const count = journey.inferred_location_count ?? 0;
  if (!count) return journey;
  return {
    ...journey,
    photo_count: journey.photo_count - count,
    missing_location_count: journey.missing_location_count + count,
    inferred_location_count: 0,
    stops: journey.stops.map((stop) => {
      if (!stop.inferred_image_ids?.length) return stop;
      const inferred = new Set(stop.inferred_image_ids);
      return { ...stop, image_ids: stop.image_ids.filter((id) => !inferred.has(id)), inferred_image_ids: [] };
    }),
  };
}

export async function fetchJourney(
  from: string, to: string, bounds: MapBounds | null, detail: JourneyDetail, signal: AbortSignal, albumId = "",
): Promise<Journey> {
  const query = new URLSearchParams();
  if (from) query.set("from_date", from);
  if (to) query.set("to_date", to);
  if (detail === "city") query.set("detail", detail);
  if (albumId) query.set("album_id", albumId);
  if (bounds) {
    for (const [edge, value] of Object.entries(bounds)) query.set(edge, String(value));
  }
  const response = await apiFetch(`${API_BASE}/map/journey?${query}`, { signal });
  if (!response.ok) throw new Error(await readApiError(response, "Der Standortverlauf konnte nicht geladen werden."));
  return response.json();
}

export async function fetchMapSelection(
  selection: GeoPhotoSelection, limit: number, cursor: MapImageCursor | null, signal: AbortSignal,
): Promise<MapImagesResponse> {
  if (!selection.image_ids) {
    return fetchMapImages({
      ...selection.bounds, from_date: selection.from_date, to_date: selection.to_date, album_id: selection.album_id, ...cursor, limit,
    }, signal);
  }
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) {
    query.set("before_id", String(cursor.before_id));
    if (cursor.before_captured_at) query.set("before_captured_at", cursor.before_captured_at);
  }
  const response = await apiFetch(`${API_BASE}/map/images/selection?${query}`, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selection }),
  });
  if (!response.ok) throw new Error(await readApiError(response, "Die Fotos der Auswahl konnten nicht geladen werden."));
  return response.json();
}
