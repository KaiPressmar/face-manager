const API_BASE = "http://localhost:8000/api";

export function imageFileUrl(imageId: number) {
  return `${API_BASE}/images/${imageId}/file`;
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
  const res = await fetch(`${API_BASE}/runtime`);
  if (!res.ok) {
    throw new Error("Runtime information is unavailable.");
  }
  return await res.json();
}

export async function fetchImages(folders: string[] = []) {
  const params = new URLSearchParams();
  folders.forEach((folder) => params.append("folders", folder));
  const query = params.toString();
  const res = await fetch(`${API_BASE}/images${query ? `?${query}` : ""}`);
  if (!res.ok) return [];
  return await res.json();
}

export async function fetchFolders(): Promise<FolderTree> {
  const res = await fetch(`${API_BASE}/folders`);
  if (!res.ok) {
    return { roots: [], image_count: 0, folder_count: 0 };
  }
  return await res.json();
}

export async function fetchClusters() {
  const res = await fetch(`${API_BASE}/clusters`);
  if (!res.ok) return [];
  return await res.json();
}

// legacy, no longer used by ClusterPage, but kept if needed elsewhere
export async function fetchClusterFaces(id: number) {
  const res = await fetch(`${API_BASE}/clusters/${id}/faces`);
  if (!res.ok) return [];
  return await res.json();
}

export async function removeFaceFromCluster(clusterId: number, faceId: number) {
  await fetch(`${API_BASE}/clusters/${clusterId}/remove-face/${faceId}`, {
    method: "POST",
  });
}

export async function dissolveCluster(clusterId: number) {
  await fetch(`${API_BASE}/clusters/${clusterId}/dissolve`, {
    method: "POST",
  });
}

export async function assignClusterToPerson(
  clusterId: number,
  personName: string,
) {
  await fetch(`${API_BASE}/clusters/${clusterId}/assign-person`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_name: personName }),
  });
}

export async function listPersons() {
  const res = await fetch(`${API_BASE}/persons`);
  if (!res.ok) return [];
  return await res.json();
}

export async function processFolder(wslPath: string): Promise<ImportJob> {
  const res = await fetch(`${API_BASE}/imports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_path: wslPath }),
  });
  if (!res.ok) {
    throw new Error("Der Import konnte nicht eingereiht werden.");
  }
  const job = await res.json();
  window.dispatchEvent(new Event("face-manager:imports-changed"));
  return job;
}

export async function fetchImportQueue(): Promise<ImportQueueState> {
  const res = await fetch(`${API_BASE}/imports`);
  if (!res.ok) {
    throw new Error("Die Import-Warteschlange ist nicht erreichbar.");
  }
  return await res.json();
}

export async function removeImportJob(jobId: string) {
  const res = await fetch(`${API_BASE}/imports/${jobId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error("Der Importauftrag konnte nicht entfernt werden.");
  }
  return await res.json();
}

export async function openImageLocation(imageId: number, imagePath: string) {
  const res = await fetch(`${API_BASE}/images/${imageId}/open-location`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_path: imagePath }),
  });
  if (!res.ok) {
    throw new Error("Der Dateispeicherort konnte nicht geöffnet werden.");
  }
}

export async function deleteImage(imageId: number) {
  const res = await fetch(`${API_BASE}/images/${imageId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error("Das Bild konnte nicht aus der Datenbank entfernt werden.");
  }
}
