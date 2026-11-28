import { API_BASE, apiFetch, readApiError } from "./api";

export interface ImportSourceConflict {
  original_path: string;
  candidate_paths: string[];
}

export interface ImportSource {
  id: string;
  folder_path: string;
  auto_scan: boolean;
  auto_import: boolean;
  status: "idle" | "queued" | "running" | "completed" | "offline" | "failed" | "preview_ready";
  last_scan_at: string | null;
  last_success_at: string | null;
  scanned_files: number;
  new_files: number;
  new_folders: number;
  relinked_files: number;
  unavailable_files: number;
  conflicts: number;
  error: string | null;
  pending_path: string | null;
  auto_import_blocked: boolean;
  conflict_candidates?: ImportSourceConflict[];
}

export interface ImportSourcesSnapshot {
  library_id: string;
  sources: ImportSource[];
}

const base = `${API_BASE}/import-sources`;
const sourcePath = (id: string) => `${base}/${encodeURIComponent(id)}`;

export async function fetchImportSources(signal: AbortSignal): Promise<ImportSourcesSnapshot> {
  const response = await apiFetch(base, { signal });
  if (!response.ok) throw new Error(await readApiError(response, "Die Bilderordner konnten nicht geladen werden."));
  return response.json();
}

async function updateSource(url: string, method: "POST" | "PATCH", libraryId: string, data: object): Promise<ImportSource> {
  const response = await apiFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...data, library_id: libraryId }),
  });
  if (!response.ok) throw new Error(await readApiError(response, "Der Bilderordner konnte nicht geändert werden."));
  return response.json();
}

export const registerImportSource = (path: string, libraryId: string) =>
  updateSource(base, "POST", libraryId, { folder_path: path });

export const configureImportSource = (id: string, libraryId: string, settings: { auto_scan?: boolean; auto_import?: boolean }) =>
  updateSource(sourcePath(id), "PATCH", libraryId, settings);

export const scanImportSource = (id: string, libraryId: string, importNew: boolean) =>
  updateSource(`${sourcePath(id)}/scan`, "POST", libraryId, importNew ? { import_new: true } : {});

export const relinkImportSource = (id: string, libraryId: string, path: string, confirm = false) =>
  updateSource(`${sourcePath(id)}/relink`, "POST", libraryId, { folder_path: path, ...(confirm ? { confirm } : {}) });

export const resolveImportSourceConflict = (id: string, libraryId: string, originalPath: string, newPath: string) =>
  updateSource(`${sourcePath(id)}/resolve`, "POST", libraryId, { original_path: originalPath, new_path: newPath });

async function deleteSource(url: string, libraryId: string, fallback: string): Promise<Response> {
  const query = new URLSearchParams({ library_id: libraryId });
  const response = await apiFetch(`${url}?${query}`, { method: "DELETE" });
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response;
}

export async function cancelImportSourceRelink(id: string, libraryId: string): Promise<ImportSource> {
  const response = await deleteSource(`${sourcePath(id)}/relink`, libraryId, "Die Vorschau konnte nicht verworfen werden.");
  return response.json();
}

export async function removeImportSource(id: string, libraryId: string): Promise<void> {
  await deleteSource(sourcePath(id), libraryId, "Der Bilderordner konnte nicht entfernt werden.");
}
