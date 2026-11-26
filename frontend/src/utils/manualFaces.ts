import { API_BASE, apiFetch, invalidatePersonsCache, readApiError } from "./api";

export interface NormalizedFaceRectangle {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SavedManualFace {
  face_id: number;
  cluster_id: number;
}

export async function fetchManualFacePreview(imageId: number, signal: AbortSignal) {
  const response = await apiFetch(`${API_BASE}/images/${imageId}/manual-face-preview`, { signal });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Das Foto konnte nicht sicher zum Markieren geladen werden."));
  }
  const token = response.headers.get("X-Face-Preview");
  if (!token) throw new Error("Die Bildvorschau konnte nicht bestätigt werden. Lade sie erneut.");
  return { blob: await response.blob(), token, originalAvailable: response.headers.get("X-Face-Preview-Original") !== "no" };
}

export async function saveManualFace(
  imageId: number, rectangle: NormalizedFaceRectangle, personName: string, previewToken: string, expectedHash?: string | null,
): Promise<SavedManualFace> {
  const response = await apiFetch(`${API_BASE}/images/${imageId}/manual-faces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...rectangle, person_name: personName, preview_token: previewToken, expected_content_hash: expectedHash }),
  });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die Markierung konnte nicht gespeichert werden."));
  }
  invalidatePersonsCache();
  return response.json();
}

export async function fetchManualFaceSuggestions(
  imageId: number, previewToken: string, signal: AbortSignal,
): Promise<NormalizedFaceRectangle[]> {
  const response = await apiFetch(`${API_BASE}/images/${imageId}/face-box-suggestions`, {
    signal, headers: { "X-Face-Preview": previewToken },
  });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Es konnten keine Vorschläge geladen werden."));
  }
  const { items } = await response.json();
  return Array.isArray(items) ? items : [];
}
