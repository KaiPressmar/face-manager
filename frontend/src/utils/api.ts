import type { ThemeMode } from "./theme";
import type { GeoPhotoSelection } from "./geoPhotoSelection";

declare global {
  interface Window {
    FACE_MANAGER_API_BASE?: string;
    AndroidExport?: { requestDatabaseExport(): void };
  }
}

function resolveApiBase() {
  const configuredBase =
    window.FACE_MANAGER_API_BASE || import.meta.env.VITE_API_BASE;
  if (configuredBase) {
    return configuredBase.replace(/\/$/, "");
  }
  if (import.meta.env.DEV) {
    // Vite proxies the same-origin /api path to the backend. This keeps API and
    // event-stream connections stable in WSL, containers and remote browsers.
    return "/api";
  }
  return `${window.location.origin}/api`;
}

export const API_BASE = resolveApiBase();

function resolveDisplayPlatform() {
  const userAgentPlatform =
    (navigator as Navigator & {
      userAgentData?: { platform?: string };
    }).userAgentData?.platform || navigator.platform || "";
  return /win/i.test(userAgentPlatform) ? "windows" : "linux";
}

const DISPLAY_PLATFORM = resolveDisplayPlatform();

export const OPERATION_BLOCKED_EVENT = "face-manager:operation-blocked";

export async function apiFetch(input: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("x-face-manager-display-platform", DISPLAY_PLATFORM);
  const response = await fetch(input, {
    ...init,
    headers,
  });
  if (response.status === 409) {
    const detail = await readApiError(
      response.clone(),
      "Diese Aktion ist während der laufenden Hintergrundarbeit vorübergehend gesperrt.",
    );
    window.dispatchEvent(
      new CustomEvent(OPERATION_BLOCKED_EVENT, { detail }),
    );
  }
  return response;
}

export async function readApiError(res: Response, fallback: string) {
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

export function eventsUrl() {
  return `${API_BASE}/events`;
}

let libraryMediaRevision = 0;

export function invalidateLibraryMediaUrls() {
  libraryMediaRevision += 1;
}

function imageMediaUrl(imageId: number, endpoint: "file" | "thumbnail", retryToken?: number) {
  const url = `${API_BASE}/images/${imageId}/${endpoint}`;
  if (!libraryMediaRevision && retryToken === undefined) return url;
  const query = new URLSearchParams();
  if (libraryMediaRevision) query.set("library", String(libraryMediaRevision));
  if (retryToken !== undefined) query.set("retry", String(retryToken));
  return `${url}?${query}`;
}

export function imageFileUrl(imageId: number, retryToken?: number) {
  return imageMediaUrl(imageId, "file", retryToken);
}

export function imageThumbnailUrl(imageId: number, retryToken?: number) {
  return imageMediaUrl(imageId, "thumbnail", retryToken);
}

export function faceCropUrl(faceId: number) {
  return `${API_BASE}/faces/${faceId}/crop?v=2&library=${libraryMediaRevision}`;
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
  provider_initialized?: boolean;
  host_platform: "windows" | "linux" | "android";
  display_platform?: "windows" | "linux" | "android";
}

export interface ReleaseNotesSection {
  title: "Neu" | "Verbessert" | "Behoben";
  items: string[];
}

export interface ReleaseNotes {
  version: string;
  date: string | null;
  sections: ReleaseNotesSection[];
}

export interface UnseenReleaseNotes {
  versions: ReleaseNotes[];
  seen: boolean;
}

export interface AvailableUpdate {
  enabled: boolean;
  current_version: string;
  latest_version?: string;
  update_available: boolean;
  download_available?: boolean;
  release_url?: string;
  published_at?: string | null;
  sections?: ReleaseNotesSection[];
  build_variant?: "cpu" | "gpu" | "android" | "windows" | "linux";
  installer_name?: string | null;
  skipped?: boolean;
  can_install?: boolean;
  check_interval_seconds: number;
}

export interface UpdateDownloadState {
  status: "idle" | "downloading" | "ready" | "error";
  version?: string;
  installer_name?: string;
  downloaded_bytes?: number;
  total_bytes?: number | null;
  sha256?: string;
  error?: string;
}

export interface AppSettings {
  cluster_distance_threshold: number;
  cluster_distance_threshold_default: number;
  clustering_strictness: number;
  clustering_strictness_default: number;
  clustering_profile: ClusteringProfile;
  filename_person_suffix_format: string;
  filename_person_suffix_format_default: string;
  filename_person_block_separator: string;
  filename_person_block_separator_default: string;
  filename_person_joiner: string;
  filename_person_joiner_default: string;
  file_log_level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  file_log_level_default: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  ui_theme: ThemeMode;
  ui_theme_default: ThemeMode;
  automatic_update_checks: boolean;
  automatic_update_checks_default: boolean;
  preview_cache_limit_mib?: number | null;
  preview_cache_limit_mib_default?: number;
  preview_cache_limit_error?: boolean;
  database_path: string;
  error_log_path: string;
}

export interface ImagePathCleanupStatus {
  status: "idle" | "queued" | "running" | "completed" | "failed";
  reason: string | null;
  scanned_paths: number;
  unavailable_paths: number;
  removed_paths: number;
  removed_images: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface UpdateSettingsPayload {
  cluster_distance_threshold?: number;
  clustering_strictness?: number;
  filename_person_suffix_format?: string;
  filename_person_block_separator?: string;
  filename_person_joiner?: string;
  file_log_level?: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  ui_theme?: ThemeMode;
  automatic_update_checks?: boolean;
  preview_cache_limit_mib?: number | null;
}

export interface ClusteringProfile {
  strictness: number;
  neighbor_threshold: number;
  cohesion_threshold: number;
  person_anchor_threshold: number;
  ambiguity_margin: number;
  cluster_support_ratio: number;
  outlier_threshold: number;
}

export interface ThresholdAutoTuneResult {
  threshold: number;
  strictness: number;
  profile: ClusteringProfile;
  precision_priority: boolean;
  sample_size: number;
  person_count: number;
  same_person_accuracy: number;
  different_person_accuracy: number;
  balanced_accuracy: number;
  cohesion_aware: boolean;
}

export interface FaceImage {
  id: number;
  image_path: string;
  filename?: string;
  directory?: string;
  created_at: string | null;
  content_hash: string | null;
  image_width?: number | null;
  image_height?: number | null;
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
    review_status: FaceReviewStatus;
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
  cluster_label?: string | null;
  person_name: string | null;
  face_count: number;
}

export interface ClusterFace {
  id: number;
  image_id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  cluster_id: number | null;
  person_name: string | null;
  review_status: FaceReviewStatus;
}

export interface ClusterDetails extends ClusterSummary {
  faces: ClusterFace[];
}

export interface PersonSuggestion {
  cluster_id: number;
  person_id: number;
  person_name: string;
  confidence: number;
  best_distance: number;
  runner_up_margin: number;
  support_count: number;
  face_count: number;
  support_ratio: number;
  recommended: boolean;
  preview_face_ids: number[];
  proposal_token: string;
  face_ids: number[];
  reference_face_ids: number[];
}

export interface PersonSuggestionDecision {
  cluster_id: number;
  person_id: number;
  proposal_token: string;
  face_ids: number[];
  scope: "preview" | "detail";
}

export interface ReviewSuggestion {
  cluster_id: number;
  review_status: "unknown_person" | "not_face";
  confidence: number;
  best_distance: number;
  support_count: number;
  face_count: number;
  support_ratio: number;
  recommended: boolean;
  preview_face_ids: number[];
  library_id?: string;
}

export type FaceReviewStatus = "active" | "unknown_person" | "not_face";
export type FaceReviewGroupKey = "unassigned" | "unknown_person" | "not_face";

export interface FaceReviewGroupSummary {
  group_key: FaceReviewGroupKey;
  label: string;
  face_count: number;
  cluster_count: number;
}

export interface FaceReviewGroupDetails extends FaceReviewGroupSummary {
  faces: ClusterFace[];
}

export interface FetchImagesParams {
  folders?: string[];
  persons?: string[];
  /** Archived review statuses to include, e.g. "unknown_person"/"not_face". */
  faceStatuses?: string[];
  selection?: GeoPhotoSelection;
  sortBy?: "date" | "folder";
  sortDirection?: "desc" | "asc";
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}

export type ImportJobStatus =
  | "queued"
  | "running"
  | "paused"
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
  unavailable_images?: number;
  unreadable_images?: number;
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
  paused_count?: number;
  max_concurrent_jobs?: number;
  overall_eta_seconds: number | null;
}

export interface AutoClusterTask {
  id: string;
  kind: "auto_cluster_repair" | "unassigned_recluster" | "full_recluster";
  reason: string;
  status: "queued" | "running" | "paused" | "cancelling" | "completed" | "failed" | "cancelled";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  total_faces: number;
  processed_faces: number;
  repaired_faces: number;
  stage: "preparing" | "processing" | "finalizing" | "completed" | "failed" | "cancelled" | null;
  last_error: string | null;
  elapsed_seconds: number | null;
}

export interface AutoClusterTaskState {
  task: AutoClusterTask | null;
}

export interface ThumbnailWarmupTask {
  status: "stopped" | "idle" | "paused" | "running" | "failed" | "cancelled";
  started_at: string | null;
  last_run_at: string | null;
  next_face_id: number;
  total_faces: number;
  cycle_scanned_faces: number;
  scanned_faces: number;
  created_thumbnails: number;
  skipped_existing: number;
  skipped_missing_source: number;
  failed_faces: number;
  eta_seconds: number | null;
  last_error: string | null;
  cache_complete: boolean;
  user_paused?: boolean;
}

export interface ThumbnailWarmupState {
  task: ThumbnailWarmupTask | null;
}

export async function fetchRuntimeInfo(): Promise<RuntimeInfo> {
  const res = await apiFetch(`${API_BASE}/runtime`);
  if (!res.ok) {
    throw new Error("Informationen zur Bilderkennung sind nicht verfügbar.");
  }
  return await res.json();
}

export async function fetchUnseenReleaseNotes(): Promise<UnseenReleaseNotes> {
  const res = await apiFetch(`${API_BASE}/changelog/current`);
  if (!res.ok) {
    throw new Error("Die Versionshinweise sind nicht verfügbar.");
  }
  return await res.json();
}

export async function fetchFullChangelog(): Promise<ReleaseNotes[]> {
  const res = await apiFetch(`${API_BASE}/changelog`);
  if (!res.ok) {
    throw new Error("Das Änderungsprotokoll ist nicht verfügbar.");
  }
  const payload = await res.json();
  return Array.isArray(payload?.versions) ? payload.versions : [];
}

export async function acknowledgeCurrentReleaseNotes(): Promise<void> {
  const res = await apiFetch(`${API_BASE}/changelog/current/acknowledge`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error("Die Versionshinweise konnten nicht bestätigt werden.");
  }
}

export async function checkForUpdates(force = false): Promise<AvailableUpdate> {
  const res = await apiFetch(`${API_BASE}/updates/check?force=${force ? "true" : "false"}`);
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Update-Prüfung ist fehlgeschlagen."));
  }
  return await res.json();
}

export async function skipUpdate(version: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/updates/skip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Version konnte nicht übersprungen werden."));
  }
}

export async function startUpdateDownload(version: string): Promise<UpdateDownloadState> {
  const res = await apiFetch(`${API_BASE}/updates/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Installationsdatei konnte nicht geladen werden."));
  }
  return await res.json();
}

export async function fetchUpdateDownloadState(): Promise<UpdateDownloadState> {
  const res = await apiFetch(`${API_BASE}/updates/download`);
  if (!res.ok) {
    throw new Error("Der Downloadstatus ist nicht verfügbar.");
  }
  return await res.json();
}

export async function openUpdateRelease(version: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/updates/open-release`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Release-Seite konnte nicht geöffnet werden."));
  }
}

export async function installDownloadedUpdate(version: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/updates/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Das Update konnte nicht gestartet werden."));
  }
}

export async function fetchSettings(): Promise<AppSettings> {
  const res = await apiFetch(`${API_BASE}/settings`);
  if (!res.ok) {
    throw new Error("Die Einstellungen sind nicht verfügbar.");
  }
  return await res.json();
}

export interface SharedLibraryDecision {
  device: string;
  kind: "person" | "unknown_person" | "not_face" | "unassigned" | "detected" | "missing";
  person_name?: string | null;
}

export interface SharedLibraryConflict {
  id: string;
  revision: string;
  photo_id?: number | null;
  local_face_id?: number | null;
  shared_face_id?: number | null;
  evidence_available?: boolean;
  relative_path: string;
  folder_id?: string;
  source_path?: string | null;
  can_resolve: boolean;
  can_keep_local?: boolean;
  can_use_shared?: boolean;
  reason: string;
  local: SharedLibraryDecision;
  shared: SharedLibraryDecision;
}

export interface SharedLibraryConfig {
  source_path: string | null;
  sync_path: string | null;
  paused: boolean;
  share_embeddings: boolean;
  write_enabled?: boolean;
  initialize_share?: boolean;
  assist_paused?: boolean;
}

export interface SharedLibraryFolder extends SharedLibraryConfig {
  folder_id?: string;
  linked_paths?: string[];
  blocked_paths?: string[];
  parent_source_path?: string | null;
  available?: boolean;
  write_available?: boolean;
  analysis_available?: boolean;
  preview?: boolean;
  proposed_count?: number;
  status: "off" | "checking" | "online" | "offline" | "read_only" | "paused" | "attention";
  device_name?: string;
  active_devices?: string[];
  peer_names?: string[];
  resolution_pending?: boolean;
  concurrent_choices?: boolean;
  choice_error?: string | null;
  imported_decisions?: number;
  conflict_count?: number;
  conflicts?: SharedLibraryConflict[];
  error?: string | null;
}

export interface SharedLibraryState extends SharedLibraryFolder {
  folders?: Array<SharedLibraryFolder & { folder_id: string }>;
}

export function sharedLibraryFolders(state: SharedLibraryState | null | undefined): SharedLibraryFolder[] {
  if (!state || state.available === false) return [];
  return state.folders ?? (state.source_path ? [state] : []);
}

export function sharedLibraryConflictFolder(state: SharedLibraryState | null, conflict: SharedLibraryConflict) {
  if (!conflict.folder_id) return state;
  return state?.folders?.find(({ folder_id }) => folder_id === conflict.folder_id);
}

async function sharedLibraryRequest(path: string, options: RequestInit, fallback: string): Promise<SharedLibraryState> {
  const headers = new Headers(options.headers);
  headers.set("x-face-manager-display-platform", DISPLAY_PLATFORM);
  const response = await fetch(`${API_BASE}/shared-library${path}`, { ...options, headers });
  if (!response.ok) {
    throw Object.assign(new Error(await readApiError(response, fallback)), { status: response.status });
  }
  return response.json();
}

export function fetchSharedLibrary(signal?: AbortSignal): Promise<SharedLibraryState> {
  return sharedLibraryRequest("", { signal }, "Der gemeinsame Ordner konnte nicht geprüft werden.");
}

export type SharedLibraryUpdate = (SharedLibraryConfig & { folder_id?: string }) | { folder_id: string; remove_linked_path: string };

export function updateSharedLibrary(config: SharedLibraryUpdate): Promise<SharedLibraryState> {
  return sharedLibraryRequest("", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  }, "Der gemeinsame Ordner konnte nicht gespeichert werden.");
}

export function resolveSharedLibrary(conflict: SharedLibraryConflict, choice: "local" | "shared"): Promise<SharedLibraryState> {
  return sharedLibraryRequest("/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: conflict.id, revision: conflict.revision, choice }),
  }, "Die Zuordnung konnte nicht übernommen werden.");
}

export async function fetchSharedConflictEvidence(conflict: SharedLibraryConflict, signal: AbortSignal): Promise<{
  local?: string;
  shared?: string;
  photo?: string;
}> {
  const query = new URLSearchParams({ revision: conflict.revision });
  const response = await fetch(`${API_BASE}/shared-library/conflicts/${encodeURIComponent(conflict.id)}/evidence?${query}`, { signal });
  if (!response.ok) throw new Error("Das geprüfte Foto ist gerade nicht verfügbar.");
  return response.json();
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
    throw new Error(await readApiError(res, "Die Einstellungen konnten nicht gespeichert werden."));
  }
  return await res.json();
}

export async function fetchImagePathCleanup(): Promise<ImagePathCleanupStatus> {
  const res = await apiFetch(`${API_BASE}/maintenance/image-paths`);
  if (!res.ok) {
    throw new Error("Die Pfadprüfung ist nicht verfügbar.");
  }
  return await res.json();
}

export async function startImagePathCleanup(): Promise<ImagePathCleanupStatus> {
  const res = await apiFetch(`${API_BASE}/maintenance/image-paths`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Pfadprüfung konnte nicht gestartet werden."));
  }
  return await res.json();
}

export async function autoTuneClusterThreshold(): Promise<ThresholdAutoTuneResult> {
  const res = await apiFetch(`${API_BASE}/settings/cluster-threshold/auto-tune`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(
      await readApiError(res, "Die Gruppierung konnte nicht automatisch optimiert werden."),
    );
  }
  return await res.json();
}

export interface ReclusterResult {
  scheduled: boolean;
  status: "queued" | "running" | "noop" | string;
}

export async function reclusterAllFaces(): Promise<ReclusterResult> {
  const res = await apiFetch(`${API_BASE}/clusters/recluster`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(
      await readApiError(res, "Die Gesichtsgruppen konnten nicht neu geordnet werden."),
    );
  }
  const payload = await res.json();
  return {
    scheduled: Boolean(payload?.scheduled),
    status: typeof payload?.status === "string" ? payload.status : "noop",
  };
}

export async function exportDatabase(): Promise<Blob> {
  const res = await apiFetch(`${API_BASE}/database/export`);
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Sicherung konnte nicht erstellt werden."));
  }
  return await res.blob();
}

export async function fetchAutoClusterTaskState(): Promise<AutoClusterTaskState> {
  const res = await apiFetch(`${API_BASE}/autocluster-tasks`);
  if (!res.ok) {
    throw new Error("Der Status der automatischen Gruppierung ist nicht verfügbar.");
  }
  return await res.json();
}

export async function fetchThumbnailWarmupState(): Promise<ThumbnailWarmupState> {
  const res = await apiFetch(`${API_BASE}/thumbnail-warmup`);
  if (!res.ok) {
    throw new Error("Der Status der Gesichtsvorschauen ist nicht verfügbar.");
  }
  return await res.json();
}

export async function importDatabase(file: File): Promise<void> {
  const res = await apiFetch(`${API_BASE}/database/import`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: await file.arrayBuffer(),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Sicherung konnte nicht wiederhergestellt werden."));
  }
}

/** Load one image with its faces, to show a face crop in its full context. */
export async function fetchImageDetail(
  imageId: number,
  signal?: AbortSignal,
  { resolveGeometry = false } = {},
): Promise<FaceImage> {
  const query = resolveGeometry ? "?resolve_geometry=true" : "";
  const res = await apiFetch(`${API_BASE}/images/${imageId}/detail${query}`, { signal });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Das Bild konnte nicht geladen werden."));
  }
  return await res.json();
}

export async function fetchFaceImage(faceId: number, proposalToken: string, signal: AbortSignal): Promise<FaceImage> {
  const query = new URLSearchParams({ proposal_token: proposalToken });
  const res = await apiFetch(`${API_BASE}/faces/${faceId}/image?${query}`, { signal });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Das Foto konnte nicht geöffnet werden."));
  }
  return await res.json();
}

export async function fetchImages({
  folders = [],
  persons = [],
  faceStatuses = [],
  selection,
  sortBy = "date",
  sortDirection = "desc",
  limit = 40,
  offset = 0,
  signal,
}: FetchImagesParams = {}): Promise<ImagePage> {
  const params = new URLSearchParams();
  folders.forEach((folder) => params.append("folders", folder));
  persons.forEach((person) => params.append("persons", person));
  faceStatuses.forEach((status) => params.append("face_statuses", status));
  params.set("sort_by", sortBy);
  params.set("sort_direction", sortDirection);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  params.set("all_photos", "true");
  let res: Response;
  if (selection?.image_ids !== undefined) {
    res = await apiFetch(`${API_BASE}/images/search?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selection }),
      signal,
    });
  } else {
    if (selection?.bounds) {
      Object.entries(selection.bounds).forEach(([key, value]) => params.set(key, String(value)));
    }
    if (selection?.from_date) params.set("from_date", selection.from_date);
    if (selection?.to_date) params.set("to_date", selection.to_date);
    res = await apiFetch(`${API_BASE}/images?${params}`, { signal });
  }
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Bilder konnten nicht geladen werden."));
  }
  return await res.json();
}

export async function fetchFolders(): Promise<FolderTree> {
  const res = await apiFetch(`${API_BASE}/folders`);
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Ordner konnten nicht geladen werden."));
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
  if (!res.ok) throw new Error("Die Gesichtsgruppen konnten nicht geladen werden.");
  const payload = await res.json();
  if (!Array.isArray(payload)) {
    throw new Error("Die Liste der Gesichtsgruppen ist ungültig.");
  }
  return payload.map((item) => ({
    cluster_id: Number(item?.cluster_id ?? item?.id ?? 0),
    cluster_label:
      typeof item?.cluster_label === "string" || item?.cluster_label === null
        ? item.cluster_label
        : null,
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

function normalizeClusterSummaryItem(item: any): ClusterSummary {
  return {
    cluster_id: Number(item?.cluster_id ?? item?.id ?? 0),
    cluster_label:
      typeof item?.cluster_label === "string" || item?.cluster_label === null
        ? item.cluster_label
        : null,
    person_name:
      typeof item?.person_name === "string" || item?.person_name === null
        ? item.person_name
        : null,
    face_count: Number(
      item?.face_count ?? (Array.isArray(item?.faces) ? item.faces.length : 0),
    ),
  };
}

function normalizeReviewGroupItem(item: any): FaceReviewGroupSummary {
  return {
    group_key:
      item?.group_key === "unknown_person" || item?.group_key === "not_face"
        ? item.group_key
        : "unassigned",
    label: typeof item?.label === "string" ? item.label : "Noch zu prüfen",
    face_count: Number(item?.face_count ?? 0),
    cluster_count: Number(item?.cluster_count ?? item?.face_count ?? 0),
  };
}

function normalizeClusterFaceItems(faces: unknown): ClusterFace[] {
  return Array.isArray(faces)
    ? (faces as ClusterFace[]).map((face) => ({
        ...face,
        person_name:
          typeof face?.person_name === "string" || face?.person_name === null
            ? face.person_name
            : null,
        review_status:
          face?.review_status === "unknown_person" || face?.review_status === "not_face"
            ? face.review_status
            : "active",
      }))
    : [];
}

export interface ClusterOverview {
  clusters: ClusterSummary[];
  review_groups: FaceReviewGroupSummary[];
  first_cluster: ClusterDetails | null;
}

export async function fetchClusterOverview(): Promise<ClusterOverview> {
  const res = await apiFetch(`${API_BASE}/clusters-overview`);
  if (!res.ok) {
    throw new Error("Die Übersicht der Gesichtsgruppen ist nicht verfügbar.");
  }
  const payload = await res.json();
  if (!Array.isArray(payload?.clusters)) {
    throw new Error("Die Liste der Gesichtsgruppen ist ungültig.");
  }
  const clusters = payload.clusters
    .map(normalizeClusterSummaryItem)
    .filter((cluster: ClusterSummary) => cluster.cluster_id > 0);
  const reviewGroups = Array.isArray(payload?.review_groups)
    ? payload.review_groups.map(normalizeReviewGroupItem)
    : [];
  const rawFirst = payload?.first_cluster;
  const firstCluster: ClusterDetails | null =
    rawFirst && typeof rawFirst === "object"
      ? {
          ...normalizeClusterSummaryItem(rawFirst),
          faces: normalizeClusterFaceItems(rawFirst.faces),
        }
      : null;
  return { clusters, review_groups: reviewGroups, first_cluster: firstCluster };
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
    cluster_label:
      typeof (payload as { cluster_label?: unknown }).cluster_label === "string" ||
      (payload as { cluster_label?: unknown }).cluster_label === null
        ? ((payload as { cluster_label: string | null }).cluster_label ?? null)
        : null,
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
      ? (payload as { faces: ClusterFace[] }).faces.map((face) => ({
          ...face,
          person_name:
            typeof face?.person_name === "string" || face?.person_name === null
              ? face.person_name
              : null,
          review_status:
            face?.review_status === "unknown_person" || face?.review_status === "not_face"
              ? face.review_status
              : "active",
        }))
      : [],
  };
}

export async function fetchFaceReviewGroups(): Promise<FaceReviewGroupSummary[]> {
  const res = await apiFetch(`${API_BASE}/face-review-groups`);
  if (!res.ok) return [];
  const payload = await res.json();
  if (!Array.isArray(payload)) {
    return [];
  }
  return payload
    .map((item) => ({
      group_key:
        item?.group_key === "unknown_person" || item?.group_key === "not_face"
          ? item.group_key
          : "unassigned",
      label: typeof item?.label === "string" ? item.label : "Noch zu prüfen",
      face_count: Number(item?.face_count ?? 0),
      cluster_count: Number(item?.cluster_count ?? item?.face_count ?? 0),
    }))
    .filter((item) => item.face_count >= 0);
}

export async function fetchFaceReviewGroupFaces(
  groupKey: FaceReviewGroupKey,
): Promise<FaceReviewGroupDetails | null> {
  const res = await apiFetch(`${API_BASE}/face-review-groups/${groupKey}/faces`);
  if (!res.ok) return null;
  const payload = await res.json();
  if (!payload || typeof payload !== "object") {
    return null;
  }
  return {
    group_key:
      (payload as { group_key?: FaceReviewGroupKey }).group_key ?? groupKey,
    label: typeof (payload as { label?: unknown }).label === "string"
      ? (payload as { label: string }).label
      : "Noch zu prüfen",
    face_count: Number((payload as { face_count?: number }).face_count ?? 0),
    cluster_count: Number(
      (payload as { cluster_count?: number; face_count?: number }).cluster_count ??
        (payload as { face_count?: number }).face_count ??
        0,
    ),
    faces: Array.isArray((payload as { faces?: unknown[] }).faces)
      ? (payload as { faces: ClusterFace[] }).faces.map((face) => ({
          ...face,
          person_name:
            typeof face?.person_name === "string" || face?.person_name === null
              ? face.person_name
              : null,
          review_status:
            face?.review_status === "unknown_person" || face?.review_status === "not_face"
              ? face.review_status
              : "active",
        }))
      : [],
  };
}

export async function removeFaceFromCluster(clusterId: number, faceId: number) {
  const res = await apiFetch(`${API_BASE}/clusters/${clusterId}/remove-face/${faceId}`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error("Das Gesicht konnte nicht aus der Gesichtsgruppe entfernt werden.");
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
    throw new Error("Die Person konnte der Gesichtsgruppe nicht zugewiesen werden.");
  }
  invalidatePersonsCache();
}

export async function fetchPersonSuggestions(): Promise<PersonSuggestion[]> {
  const res = await apiFetch(`${API_BASE}/person-suggestions`);
  if (!res.ok) {
    throw new Error("Die Personenvorschläge konnten nicht geladen werden.");
  }
  const payload = await res.json();
  return Array.isArray(payload)
    ? payload.filter((item) => typeof item?.proposal_token === "string" && Array.isArray(item.face_ids))
    : [];
}

export async function acceptPersonSuggestions(
  assignments: PersonSuggestionDecision[],
): Promise<number> {
  const res = await apiFetch(`${API_BASE}/person-suggestions/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignments }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Vorschläge konnten nicht übernommen werden."));
  }
  invalidatePersonsCache();
  const payload = await res.json();
  return Number(payload?.accepted_count || 0);
}

export async function dismissPersonSuggestion(item: PersonSuggestion): Promise<void> {
  const res = await apiFetch(`${API_BASE}/person-suggestions/${item.cluster_id}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proposal_token: item.proposal_token }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Der Vorschlag konnte nicht verworfen werden."));
  }
}

export async function fetchReviewSuggestions(): Promise<ReviewSuggestion[]> {
  const res = await apiFetch(`${API_BASE}/review-suggestions`);
  if (!res.ok) {
    throw new Error("Die Vorschläge für aussortierte Gesichter konnten nicht geladen werden.");
  }
  const payload = await res.json();
  return Array.isArray(payload) ? payload : [];
}

export async function acceptReviewSuggestions(
  suggestions: ReviewSuggestion[],
): Promise<number> {
  const libraryId = suggestions[0]?.library_id;
  if (!libraryId || suggestions.some((item) => item.library_id !== libraryId)) {
    throw new Error("Lade die Aussortiervorschläge neu und prüfe die Gesichter erneut.");
  }
  const res = await apiFetch(`${API_BASE}/review-suggestions/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      library_id: libraryId,
      cluster_ids: suggestions.map((item) => item.cluster_id),
      expected_review_statuses: Object.fromEntries(
        suggestions.map((item) => [item.cluster_id, item.review_status]),
      ),
      expected_face_ids: Object.fromEntries(
        suggestions.map((item) => [item.cluster_id, item.preview_face_ids]),
      ),
    }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Vorschläge konnten nicht übernommen werden."));
  }
  const payload = await res.json();
  return Number(payload?.accepted_count || 0);
}

export async function dismissReviewSuggestion(item: ReviewSuggestion): Promise<void> {
  const res = await apiFetch(`${API_BASE}/review-suggestions/${item.cluster_id}/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ library_id: item.library_id, review_status: item.review_status }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Der Vorschlag konnte nicht verworfen werden."));
  }
}

export async function batchUpdateFaces(payload: {
  action:
    | "remove_from_cluster"
    | "create_cluster"
    | "assign_person"
    | "mark_unknown_person"
    | "mark_not_face"
    | "restore_to_manual_review";
  face_ids: number[];
  cluster_id?: number;
  person_name?: string;
}): Promise<{ cluster_id?: number; updated_count?: number }> {
  const res = await apiFetch(`${API_BASE}/faces/batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die ausgewählten Gesichter konnten nicht aktualisiert werden."));
  }
  invalidatePersonsCache();
  return await res.json();
}

export async function renameCluster(clusterId: number, label: string) {
  const res = await apiFetch(`${API_BASE}/clusters/${clusterId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Gesichtsgruppe konnte nicht umbenannt werden."));
  }
}

export async function renamePerson(personId: number, name: string) {
  const res = await apiFetch(`${API_BASE}/persons/${personId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Person konnte nicht umbenannt werden."));
  }
  invalidatePersonsCache();
  return await res.json();
}

export async function deletePerson(
  personId: number,
  reassignmentGroup: "unassigned" | "unknown_person" | "not_face",
) {
  const params = new URLSearchParams({ reassignment_group: reassignmentGroup });
  const res = await apiFetch(`${API_BASE}/persons/${personId}?${params.toString()}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Person konnte nicht gelöscht werden."));
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
  const request = apiFetch(`${API_BASE}/persons`).then(async (res) => {
    if (!res.ok) {
      throw new Error(await readApiError(res, "Die Personenliste konnte nicht geladen werden."));
    }
    const data = await res.json();
    const normalized = Array.isArray(data) ? data : [];
    if (personsRequest === request) {
      personsCache = { data: normalized, expiresAt: Date.now() + PERSONS_CACHE_TTL_MS };
    }
    return normalized;
  }).finally(() => {
    if (personsRequest === request) personsRequest = null;
  });
  personsRequest = request;
  return request;
}

export async function processFolder(folderPath: string): Promise<ImportJob> {
  const res = await apiFetch(`${API_BASE}/imports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_path: folderPath }),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Der Bilderordner konnte nicht hinzugefügt werden."));
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
    throw new Error(await readApiError(res, "Der Ordnerdialog konnte nicht geöffnet werden."));
  }
  const payload = (await res.json()) as { folder_path: string | null };
  return payload.folder_path;
}

export async function fetchImportQueue(): Promise<ImportQueueState> {
  const res = await apiFetch(`${API_BASE}/imports`);
  if (!res.ok) {
    throw new Error("Der Status der laufenden Bildimporte ist nicht erreichbar.");
  }
  return await res.json();
}

export async function removeImportJob(jobId: string) {
  const res = await apiFetch(`${API_BASE}/imports/${jobId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Aufgabe konnte nicht entfernt werden."));
  }
  return await res.json();
}

async function runBackgroundTaskAction(url: string, method: "POST" | "DELETE" = "POST") {
  const res = await apiFetch(`${API_BASE}${url}`, { method });
  if (!res.ok) {
    throw new Error(await readApiError(res, "Die Aufgabe konnte nicht geändert werden."));
  }
  return await res.json();
}

export const pauseImportJob = (jobId: string) =>
  runBackgroundTaskAction(`/imports/${jobId}/pause`);
export const resumeImportJob = (jobId: string) =>
  runBackgroundTaskAction(`/imports/${jobId}/resume`);
export const cancelImportJob = (jobId: string) =>
  runBackgroundTaskAction(`/imports/${jobId}/cancel`);
export const deleteImportHistoryEntry = (jobId: string) =>
  runBackgroundTaskAction(`/imports/${jobId}/history`, "DELETE");
export const clearImportHistory = () =>
  runBackgroundTaskAction("/imports/history", "DELETE");

export const pauseAutoClusterTask = (taskId: string) =>
  runBackgroundTaskAction(`/autocluster-tasks/${taskId}/pause`);
export const resumeAutoClusterTask = (taskId: string) =>
  runBackgroundTaskAction(`/autocluster-tasks/${taskId}/resume`);
export const cancelAutoClusterTask = (taskId: string) =>
  runBackgroundTaskAction(`/autocluster-tasks/${taskId}/cancel`);
export const deleteAutoClusterHistoryEntry = (taskId: string) =>
  runBackgroundTaskAction(`/autocluster-tasks/${taskId}`, "DELETE");

export const pauseThumbnailWarmup = () =>
  runBackgroundTaskAction("/thumbnail-warmup/pause");
export const resumeThumbnailWarmup = () =>
  runBackgroundTaskAction("/thumbnail-warmup/resume");
export const cancelThumbnailWarmup = () =>
  runBackgroundTaskAction("/thumbnail-warmup/cancel");
export const deleteThumbnailWarmupHistory = () =>
  runBackgroundTaskAction("/thumbnail-warmup/history", "DELETE");

export async function openImageLocation(imageId: number, imagePath: string) {
  const res = await apiFetch(`${API_BASE}/images/${imageId}/open-location`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_path: imagePath }),
  });
  if (!res.ok) {
    throw new Error(
      await readApiError(res, "Der Dateispeicherort konnte nicht geöffnet werden."),
    );
  }
}

export async function deleteImage(imageId: number) {
  const res = await apiFetch(`${API_BASE}/images/${imageId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error("Das Bild konnte nicht aus Face Manager entfernt werden.");
  }
}

export interface ImageRenameCandidate {
  location_id: number;
  image_id: number;
  path: string;
  directory: string;
  current_filename: string;
  proposed_filename: string;
  validation_error?: "filename_too_long" | "invalid_filename" | null;
  omitted_person_count?: number;
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
    throw new Error("Die Anzahl der Umbenennungsvorschläge konnte nicht geladen werden.");
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

/** Capture metadata stays local; only bounded map aggregates cross the API. */
export interface MapFilters {
  west?: number;
  east?: number;
  south?: number;
  north?: number;
  from_date?: string;
  to_date?: string;
}

export interface MapPoint {
  x: number;
  y: number;
  latitude: number;
  longitude: number;
  count: number;
  west: number;
  east: number;
  south: number;
  north: number;
}

export interface MapImage {
  id: number;
  image_path: string;
  filename: string;
  latitude: number;
  longitude: number;
  captured_at: string | null;
  created_at: string | null;
}

export interface MapPointsResponse {
  grid_columns: number;
  grid_rows: number;
  points: MapPoint[];
  total: number;
  library_total: number;
  located_total: number;
  pending_total: number;
  indexing: boolean;
}

export interface MapImageCursor {
  before_id: number;
  before_captured_at: string | null;
}

export interface MapImagesResponse {
  items: MapImage[];
  total: number;
  limit: number;
  offset: number;
  next_cursor: MapImageCursor | null;
}

type MapPointFilters = MapFilters & {
  zoom?: number;
  grid_columns?: number;
  grid_rows?: number;
};
type MapImageFilters = MapFilters & Partial<MapImageCursor> & { limit?: number; offset?: number };

async function fetchMapData<T>(
  endpoint: string,
  filters: MapPointFilters | MapImageFilters,
  signal?: AbortSignal,
): Promise<T> {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  const response = await apiFetch(`${API_BASE}/map/${endpoint}?${params}`, { signal });
  if (!response.ok) {
    throw new Error(await readApiError(response, "Die Kartendaten konnten nicht geladen werden."));
  }
  return response.json();
}

export function fetchMapPoints(
  filters: MapPointFilters = {},
  signal?: AbortSignal,
): Promise<MapPointsResponse> {
  return fetchMapData("points", filters, signal);
}

export function fetchMapImages(
  filters: MapImageFilters = {},
  signal?: AbortSignal,
): Promise<MapImagesResponse> {
  return fetchMapData("images", filters, signal);
}
