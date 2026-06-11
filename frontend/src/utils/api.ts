const API_BASE = "http://localhost:8000/api";

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

export async function removeFaceFromCluster(
  clusterId: number,
  faceId: number
) {
  await fetch(
    `${API_BASE}/clusters/${clusterId}/remove-face/${faceId}`,
    {
      method: "POST",
    }
  );
}

export async function dissolveCluster(clusterId: number) {
  await fetch(`${API_BASE}/clusters/${clusterId}/dissolve`, {
    method: "POST",
  });
}

export async function assignClusterToPerson(
  clusterId: number,
  personName: string
) {
  await fetch(
    `${API_BASE}/clusters/${clusterId}/assign-person`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_name: personName }),
    }
  );
}

export async function listPersons() {
  const res = await fetch(`${API_BASE}/persons`);
  if (!res.ok) return [];
  return await res.json();
}

export async function processFolder(wslPath: string) {
  await fetch(`${API_BASE}/process-folder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_path: wslPath }),
  });
}

export async function fetchProcessStatus() {
  const res = await fetch(`${API_BASE}/process-status`);
  if (!res.ok) return null;
  return await res.json();
}
