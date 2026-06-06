export async function fetchImages() {
  const res = await fetch("http://localhost:8000/api/images");
  if (!res.ok) return [];
  return await res.json();
}

export async function fetchClusters() {
  const res = await fetch("http://localhost:8000/api/clusters");
  if (!res.ok) return [];
  return await res.json();
}

// legacy, no longer used by ClusterPage, but kept if needed elsewhere
export async function fetchClusterFaces(id: number) {
  const res = await fetch(`http://localhost:8000/api/clusters/${id}/faces`);
  if (!res.ok) return [];
  return await res.json();
}

export async function removeFaceFromCluster(
  clusterId: number,
  faceId: number
) {
  await fetch(
    `http://localhost:8000/api/clusters/${clusterId}/remove-face/${faceId}`,
    {
      method: "POST",
    }
  );
}

export async function dissolveCluster(clusterId: number) {
  await fetch(`http://localhost:8000/api/clusters/${clusterId}/dissolve`, {
    method: "POST",
  });
}

export async function assignClusterToPerson(
  clusterId: number,
  personName: string
) {
  await fetch(
    `http://localhost:8000/api/clusters/${clusterId}/assign-person`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_name: personName }),
    }
  );
}

export async function listPersons() {
  const res = await fetch("http://localhost:8000/api/persons");
  if (!res.ok) return [];
  return await res.json();
}

export async function processFolder(wslPath: string) {
  await fetch("http://localhost:8000/api/process-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_path: wslPath }),
  });
}

export async function fetchProcessStatus() {
  const res = await fetch("http://localhost:8000/api/process-status");
  if (!res.ok) return null;
  return await res.json();
}
