import { API_BASE, apiFetch, readApiError, type ImagePage } from "./api";

export interface PersonDirectoryEntry {
  id: number;
  name: string;
  representative_face_id: number | null;
  photo_count: number;
  assignment_library_id?: string;
}

export interface PersonDirectoryPage {
  items: PersonDirectoryEntry[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

interface PageOptions {
  offset?: number;
  limit?: number;
  signal?: AbortSignal;
}

export async function fetchPersonDirectory({
  search = "", offset = 0, limit = 24, signal,
}: PageOptions & { search?: string } = {}): Promise<PersonDirectoryPage> {
  const query = new URLSearchParams({ search, offset: String(offset), limit: String(limit) });
  const response = await apiFetch(`${API_BASE}/person-directory?${query}`, { signal });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Das Personenverzeichnis konnte nicht geladen werden."));
  }
  return await response.json();
}

export async function fetchPersonProfile(id: number, signal?: AbortSignal): Promise<PersonDirectoryEntry | null> {
  const response = await apiFetch(`${API_BASE}/person-directory/${id}`, { signal });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(await readApiError(response, "Das Personenprofil konnte nicht geladen werden."));
  }
  return await response.json();
}

export async function fetchPersonPhotos(id: number, {
  offset = 0, limit = 24, signal,
}: PageOptions = {}): Promise<ImagePage> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  const response = await apiFetch(`${API_BASE}/person-directory/${id}/photos?${query}`, { signal });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die Fotos konnten nicht geladen werden."));
  }
  return await response.json();
}
