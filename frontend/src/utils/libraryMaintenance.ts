import { API_BASE, apiFetch, readApiError } from "./api";

export interface RestorePreview {
  photos: number;
  people: number;
  albums: number;
  checksum: string;
}

export async function previewDatabaseRestore(file: File): Promise<RestorePreview> {
  const response = await apiFetch(`${API_BASE}/database/restore-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die Sicherung konnte nicht geprüft werden."));
  }
  return await response.json();
}

export async function hasRestoreBackup(): Promise<boolean> {
  const response = await apiFetch(`${API_BASE}/database/restore-backup`);
  return response.ok && (await response.json()).available === true;
}

export async function downloadRestoreBackup(): Promise<Blob> {
  const response = await apiFetch(`${API_BASE}/database/restore-backup/file`);
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die vorherige Bibliothek konnte nicht heruntergeladen werden."));
  }
  return await response.blob();
}

export interface ArchiveRepairCandidate {
  face_id: number;
  image_id: number;
  person_name: string;
  restore_status: "unknown_person" | "not_face";
  source_name: string;
  source_date: string;
  photo_filename: string | null;
  token: string;
}

export interface ArchiveRepairBatch {
  batch_id: string;
  count: number;
}

export interface ArchiveRepairPreview {
  candidates: ArchiveRepairCandidate[];
  checked_backups: number;
  automatic_repaired: number;
  latest_batch: ArchiveRepairBatch | null;
  skipped_conflicts: number;
  truncated: boolean;
}

async function archiveRepairRequest<Result>(suffix = "", data?: object): Promise<Result> {
  const response = await apiFetch(`${API_BASE}/database/archive-repair${suffix}`, data && {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die Zuordnungen konnten nicht geprüft oder geändert werden."));
  }
  return await response.json();
}

export const inspectArchiveRepair = () => archiveRepairRequest<ArchiveRepairPreview>();

export async function addArchiveRepairSnapshot(file: File): Promise<void> {
  const response = await apiFetch(`${API_BASE}/database/archive-repair/snapshots`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die Sicherung konnte nicht zum Vergleich gespeichert werden."));
  }
}

export const applyArchiveRepair = (tokens: string[]) => archiveRepairRequest<ArchiveRepairBatch>("/apply", { tokens });
export const undoArchiveRepair = (batch_id: string) => archiveRepairRequest<ArchiveRepairBatch>("/undo", { batch_id });
