import { useCallback, useEffect, useState } from "react";
import { API_BASE, apiFetch, readApiError } from "./api";
import { subscribeToTopic } from "./events";
import type { GeoPhotoSelection } from "./geoPhotoSelection";

export interface PhotoAlbum {
  id: string;
  name: string;
  photo_count: number;
  cover_image_id: number | null;
  created_at: string;
  updated_at: string;
  journey: AlbumJourney | null;
  layout_draft?: object | null;
}

export interface AlbumJourney {
  from_date?: string | null;
  to_date?: string | null;
  detail: "trip" | "city";
  title?: string | null;
  arrival_included?: boolean;
  departure_included?: boolean;
}

export type AlbumPhotos = { image_ids: readonly number[] } | { selection: GeoPhotoSelection };

const albumsEvent = "face-manager:albums-changed";

async function albumRequest<Result>(
  path: string,
  message: string,
  { signal, method, body }: { signal?: AbortSignal; method?: string; body?: object } = {},
) {
  const response = await apiFetch(`${API_BASE}/albums${path}`, {
    method, signal,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(await readApiError(response, message));
  const result: Result = await response.json();
  if (method) window.dispatchEvent(new CustomEvent(albumsEvent, { detail: result }));
  return result;
}

export const fetchPhotoAlbums = (signal?: AbortSignal) =>
  albumRequest<{ albums: PhotoAlbum[] }>("", "Die Alben konnten nicht geladen werden.", { signal });

export const createPhotoAlbum = (name: string, photos?: AlbumPhotos, journey?: AlbumJourney) =>
  albumRequest<PhotoAlbum>("", "Das Album konnte nicht erstellt werden.", {
    method: "POST", body: { name, ...photos, journey },
  });

export const updatePhotoAlbum = (id: string, changes: {
  name?: string;
  cover_image_id?: number | null;
  journey?: AlbumJourney | null;
  layout_draft?: object | null;
}) =>
  albumRequest<PhotoAlbum>(`/${encodeURIComponent(id)}`, "Das Album konnte nicht bearbeitet werden.", { method: "PATCH", body: changes });

export const deletePhotoAlbum = (id: string) =>
  albumRequest<{ status: "deleted" }>(`/${encodeURIComponent(id)}`, "Das Album konnte nicht gelöscht werden.", { method: "DELETE" });

export const addPhotoAlbumImages = (id: string, photos: AlbumPhotos) =>
  albumRequest<PhotoAlbum>(`/${encodeURIComponent(id)}/photos`, "Die Fotos konnten nicht hinzugefügt werden.", { method: "POST", body: photos });

export const removePhotoAlbumImages = (id: string, imageIds: readonly number[]) =>
  albumRequest<PhotoAlbum>(`/${encodeURIComponent(id)}/photos`, "Die Fotos konnten nicht aus dem Album entfernt werden.",
    { method: "DELETE", body: { image_ids: imageIds } });

export function usePhotoAlbums(active = true) {
  const [albums, setAlbums] = useState<PhotoAlbum[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    if (!active) return;
    const unsubscribe = subscribeToTopic<{ reason?: string }>("clusters", (update) => {
      if (update.reason === "database_import") {
        setAlbums([]);
        reload();
      } else if (update.reason === "albums_changed") reload();
    });
    window.addEventListener(albumsEvent, reload);
    return () => {
      unsubscribe();
      window.removeEventListener(albumsEvent, reload);
    };
  }, [active, reload]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void fetchPhotoAlbums(controller.signal)
      .then(({ albums: result }) => { if (!controller.signal.aborted) setAlbums(result); })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Alben konnten nicht geladen werden.");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, revision]);

  return { albums, loading, error, reload };
}
