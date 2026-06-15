declare global {
  interface Window {
    FACE_MANAGER_API_BASE?: string;
  }
}

function resolveApiBase() {
  const configuredBase =
    window.FACE_MANAGER_API_BASE || import.meta.env.VITE_API_BASE;
  if (configuredBase) {
    return configuredBase.replace(/\/$/, "");
  }
  if (import.meta.env.DEV) {
    return "http://localhost:8000/api";
  }
  return `${window.location.origin}/api`;
}

const API_BASE = resolveApiBase();

function resolveDisplayPlatform() {
  const userAgentPlatform =
    (navigator as Navigator & {
      userAgentData?: { platform?: string };
    }).userAgentData?.platform || navigator.platform || "";
  return /win/i.test(userAgentPlatform) ? "windows" : "linux";
}

const DISPLAY_PLATFORM = resolveDisplayPlatform();

function apiFetch(input: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("x-face-manager-display-platform", DISPLAY_PLATFORM);
  return fetch(input, {
    ...init,
    headers,
  });
}

async function readApiError(res: Response, fallback: string) {
  try {
    const payload = await res.json();
    if (
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      typeof payload.detail === "string"
    ) {
      return payload.detail;
    }
  } catch {
    // Ignore non-JSON error bodies.
  }
  return fallback;
}

export function imageFileUrl(imageId: number) {
  return `${API_BASE}/images/${imageId}/file`;
}

export function faceCropUrl(faceId: number) {
  return `${API_BASE}/faces/${faceId}/crop`;
}

export interface FolderNode {
  path: string;
  name: string;
  direct_image_count: number;
  image_count: number;
  children: FolderNode[];
}

export interface FolderTree {
  roots: FolderNode[];
  image_count: number;
  folder_count: number;
}

export interface RuntimeInfo {
  compute_mode: "gpu" | "cpu";
  execution_provider: string;
  host_platform: "windows" | "linux";
  display_platform?: "windows" | "linux";
}

export interface AppSettings {
  cluster_distance_threshold: number;
  cluster_distance_threshold_default: number;
  filename_person_suffix_format: string;
  filename_person_suffix_format_default: string;
  filename_person_block_separator: string;
  filename_person_block_separator_default: string;
  filename_person_joiner: string;
  filename_person_joiner_default: string;
  file_log_level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  file_log_level_default: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  database_path: string;
  error_log_path: string;
}

export interface UpdateSettingsPayload {
  cluster_distance_threshold?: number;
  filename_person_suffix_format?: string;
  filename_person_block_separator?: string;
  filename_person_joiner?: string;
  file_log_level?: "DEBUG" | "INFO" | "WARNING" | "ERROR";
}

export interface FaceImage {
  id: number;
  image_path: string;
  filename?: string;
  directory?: string;
  created_at: string | null;
  content_hash: string | null;
  location_count: number;
  locations: {
    path: string;
    filename: string;
    directory: string;
  }[];
  faces: {
    id: number;
    bbox_x: number;
    bbox_y: number;
    bbox_w: number;
    bbox_h: number;
    cluster_id: number | null;
    person_name: string | null;
  }[];
}

export interface ImagePage {
  items: FaceImage[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  available_persons: string[];
}

export interface ClusterSummary {
  cluster_id: number;
  person_name: string | null;
  face_count: number;
}

export interface ClusterFace {
  id: number;
  image_id: number;
  image_path: string;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
}

export interface ClusterDetails extends ClusterSummary {
  faces: ClusterFace[];
}

export interface FetchImagesParams {
  folders?: string[];
  persons?: string[];
  sortBy?: "date" | "folder";
  sortDirection?: "desc" | "asc";
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}

export type ImportJobStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled";

export type ImportJobStage =
  | "scanning"
  | "hashing"
  | "loading_model"
  | "loading_index"
  | "processing"
  | "finalizing"
  | "completed";

export interface ImportJob {
  id: string;
  folder_path: string;
  status: ImportJobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  total_images: number;
  processed_images: number;
  total_faces: number;
  processed_faces: number;
  stage: ImportJobStage | null;
  stage_started_at: string | null;
  stage_current: number;
  stage_total: number;
  current_file: string | null;
  last_error: string | null;
  queue_position: number | null;
  elapsed_seconds: number | null;
  eta_seconds: number | null;
  stations?: ImportStation[];
}

export interface ImportStation {
  job_id: string;
  key: string;
  label: string;
  state: "queued" | "active" | "done" | "failed" | "cancelled";
  progress_current: number;
  progress_total: number;
  eta_seconds: number | null;
  current_file: string | null;
  detail: string | null;
}

export interface ImportQueueState {
  jobs: ImportJob[];
  active_job_id: string | null;
  active_job_ids?: string[];
  running_count?: number;
  queued_count: number;
  max_concurrent_jobs?: number;
  overall_eta_seconds: number | null;
}

export async function fetchRuntimeInfo(): Promise<RuntimeInfo> {
  const res = await apiFetch(`${API_BASE}/runtime`);
  if (!res.ok) {
    throw new Error("Runtime information is unavailable.");
  }
  return await res.json();
}

export async function fetchSettings(): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/settings`);
  if (!res.ok) {
    throw new Error("Settings are unavailable.");
  }
  return await res.json();
}

export async function updateSettings(
  payload: UpdateSettingsPayload,
): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "The settings could not be saved."));
  }
  return await res.json();
}

export async function exportDatabase(): Promise<Blob> {
  const res = await apiFetch(`${API_BASE}/database/export`);
  if (!res.ok) {
    throw new Error("The database could not be exported.");
  }
  return await res.blob();
}

export async function importDatabase(file: File): Promise<void> {
  const res = await apiFetch(`${API_BASE}/database/import`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: await file.arrayBuffer(),
  });
  if (!res.ok) {
    throw new Error("The database could not be imported.");
  }
}

export async function fetchImages({
  folders = [],
  persons = [],
  sortBy = "date",
  sortDirection = "desc",
  limit = 40,
  offset = 0,
}: FetchImagesParams = {}): Promise<ImagePage> {
  const params = new URLSearchParams();
  folders.forEach((folder) => params.append("folders", folder));
  persons.forEach((person) => params.append("persons", person));
  params.set("sort_by", sortBy);
  params.set("sort_direction", sortDirection);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  const query = params.toString();
  const res = await apiFetch(`${API_BASE}/images${query ? `?${query}` : ""}`);
  if (!res.ok) {
    return {
      items: [],
      total: 0,
      offset,
      limit,
      has_more: false,
      available_persons: [],
    };
  }
  return await res.json();
}

export async function fetchFolders(): Promise<FolderTree> {
  const res = await apiFetch(`${API_BASE}/folders`);
  if (!res.ok) {
    return { roots: [], image_count: 0, folder_count: 0 };
  }
  return await res.json();
}

let personsCache:
  | {
      expiresAt: number;
      data: Array<{ id: number; name: string }>;
    }
  | null = null;
let personsRequest: Promise<Array<{ id: number; name: string }>> | null = null;
const PERSONS_CACHE_TTL_MS = 30_000;

export function invalidatePersonsCache() {
  personsCache = null;
  personsRequest = null;
}

export async function fetchClusters(): Promise<ClusterSummary[]> {
  const res = await apiFetch(`${API_BASE}/clusters`);
  if (!res.ok) return [];
  const payload = await res.json();
  if (!Array.isArray(payload)) {
    return [];
  }
  return payload.map((item) => ({
    cluster_id: Number(item?.cluster_id ?? item?.id ?? 0),
    person_name:
      typeof item?.person_name === "string" || item?.person_name === null
        ? item.person_name
        : null,
    face_count: Number(
      item?.face_count ??
        (Array.isArray(item?.faces) ? item.faces.length : 0),
    ),
  })).filter((item) => item.cluster_id > 0);
}

export async function fetchClusterFaces(id: number): Promise<ClusterDetails | null> {
  const res = await apiFetch(`${API_BASE}/clusters/${id}/faces`);
  if (res.status === 404) return null;
  if (!res.ok) return null;
  const payload = await res.json();
  if (!payload || typeof payload !== "object") {
    return null;
  }
  return {
    cluster_id: Number((payload as { cluster_id?: number }).cluster_id ?? id),
    person_name:
      typeof (payload as { person_name?: unknown }).person_name === "string" ||
      (payload as { person_name?: unknown }).person_name === null
        ? ((payload as { person_name: string | null }).person_name ?? null)
        : null,
    face_count: Number(
      (payload as { face_count?: number }).face_count ??
        (Array.isArray((payload as { faces?: unknown[] }).faces)
          ? (payload as { faces: unknown[] }).faces.length
          : 0),
    ),
    faces: Array.isArray((payload as { faces?: unknown[] }).faces)
      ? (payload as { faces: ClusterFace[] }).faces
      : [],
  };
}

export async function removeFaceFromCluster(clusterId: number, faceId: number) {
  const res = await apiFetch(`${API_BASE}/clusters/${clusterId}/remove-face/${faceId}`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error("Das Gesicht konnte nicht aus dem Cluster entfernt werden.");
  }
}

export async function dissolveCluster(clusterId: number) {
  const res = await apiFetch(`${API_BASE}/clusters/${clusterId}/dissolve`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error("Das Cluster konnte nicht aufgelöst werden.");
  }
}

export async function assignClusterToPerson(
  clusterId: number,
  personName: string,
) {
  const res = await apiFetch(`${API_BASE}/clusters/${clusterId}/assign-person`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_name: personName }),
  });
  if (!res.ok) {
    throw new Error("Die Person konnte dem Cluster nicht zugewiesen werden.");
  }
  invalidatePersonsCache();
}

export async function listPersons() {
  const now = Date.now();
  if (personsCache && personsCache.expiresAt > now) {
    return personsCache.data;
  }
  if (personsRequest) {
    return personsRequest;
  }
  const res = await apiFetch(`${API_BASE}/persons`);
  if (!res.ok) return [];
  personsRequest = res.json().then((data) => {
    const normalized = Array.isArray(data) ? data : [];
    personsCache = {
      data: normalized,
      expiresAt: Date.now() + PERSONS_CACHE_TTL_MS,
    };
    personsRequest = null;
    return normalized;
  }).catch((error) => {
    personsRequest = null;
    throw error;
  });
  return personsRequest;
}

export async function processFolder(folderPath: string): Promise<ImportJob> {
  const res = await apiFetch(`${API_BASE}/imports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_path: folderPath }),
  });
  if (!res.ok) {
    throw new Error("Der Import konnte nicht eingereiht werden.");
  }
  const job = await res.json();
  window.dispatchEvent(new Event("face-manager:imports-changed"));
  return job;
}

export async function selectImportFolder(): Promise<string | null> {
  const res = await apiFetch(`${API_BASE}/system/select-folder`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error("Der Ordnerdialog konnte nicht geöffnet werden.");
  }
  const payload = (await res.json()) as { folder_path: string | null };
  return payload.folder_path;
}

export async function fetchImportQueue(): Promise<ImportQueueState> {
  const res = await apiFetch(`${API_BASE}/imports`);
  if (!res.ok) {
    throw new Error("Die Import-Warteschlange ist nicht erreichbar.");
  }
  return await res.json();
}

export async function removeImportJob(jobId: string) {
  const res = await apiFetch(`${API_BASE}/imports/${jobId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error("Der Importauftrag konnte nicht entfernt werden.");
  }
  return await res.json();
}

export async function openImageLocation(imageId: number, imagePath: string) {
  const res = await apiFetch(`${API_BASE}/images/${imageId}/open-location`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_path: imagePath }),
  });
  if (!res.ok) {
    throw new Error("Der Dateispeicherort konnte nicht geöffnet werden.");
  }
}

export async function deleteImage(imageId: number) {
  const res = await apiFetch(`${API_BASE}/images/${imageId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error("Das Bild konnte nicht aus der Datenbank entfernt werden.");
  }
}

export interface ImageRenameCandidate {
  location_id: number;
  image_id: number;
  path: string;
  directory: string;
  current_filename: string;
  proposed_filename: string;
  proposed_path: string;
  detected_person_names: string[];
  current_suffix_person_names: string[];
}

export interface ImageRenamePage {
  items: ImageRenameCandidate[];
  total: number | null;
  offset: number;
  limit: number;
  has_more: boolean;
  available_persons: string[];
}

export interface ApplyImageRenamePayload {
  selected_paths?: string[];
  rename_all?: boolean;
  excluded_paths?: string[];
  folders?: string[];
  persons?: string[];
  sort_by?: "date" | "folder";
  sort_direction?: "desc" | "asc";
}

export interface ApplyImageRenameResult {
  renamed: {
    from_path: string;
    to_path: string;
    image_id: number;
  }[];
  skipped: {
    path: string;
    reason: string;
  }[];
  errors: {
    path: string;
    reason: string;
  }[];
  renamed_count: number;
  skipped_count: number;
  error_count: number;
}

export async function fetchImageRenameCandidates(
  {
    folders = [],
    persons = [],
    sortBy = "date",
    sortDirection = "desc",
    limit = 100,
    offset = 0,
    signal,
  }: FetchImagesParams = {},
): Promise<ImageRenamePage> {
  const params = new URLSearchParams();
  folders.forEach((folder) => params.append("folders", folder));
  persons.forEach((person) => params.append("persons", person));
  params.set("sort_by", sortBy);
  params.set("sort_direction", sortDirection);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  params.set("include_total", "false");
  const res = await apiFetch(`${API_BASE}/image-renames?${params.toString()}`, {
    signal,
  });
  if (!res.ok) {
    throw new Error("Die Umbenennungsvorschau konnte nicht geladen werden.");
  }
  return await res.json();
}

export async function fetchImageRenameCandidateCount({
  folders = [],
  persons = [],
  sortBy = "date",
  sortDirection = "desc",
  signal,
}: FetchImagesParams = {}): Promise<number> {
  const params = new URLSearchParams();
  folders.forEach((folder) => params.append("folders", folder));
  persons.forEach((person) => params.append("persons", person));
  params.set("sort_by", sortBy);
  params.set("sort_direction", sortDirection);
  const res = await apiFetch(`${API_BASE}/image-renames/count?${params.toString()}`, {
    signal,
  });
  if (!res.ok) {
    throw new Error("Die Anzahl der Umbenennungsvorschlaege konnte nicht geladen werden.");
  }
  const payload = (await res.json()) as { total: number };
  return payload.total;
}

export async function applyImageRenames(
  payload: ApplyImageRenamePayload,
): Promise<ApplyImageRenameResult> {
  const res = await apiFetch(`${API_BASE}/image-renames/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(
      await readApiError(res, "Die Dateinamen konnten nicht aktualisiert werden."),
    );
  }
  return await res.json();
}
