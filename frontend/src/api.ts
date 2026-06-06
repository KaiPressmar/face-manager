import axios from "axios";

const API_BASE = "http://localhost:8000/api";

export async function processFolder(folderPath: string) {
  return axios.post(`${API_BASE}/process-folder`, { folderPath });
}

export async function getProcessStatus() {
  const res = await axios.get(`${API_BASE}/process-status`);
  return res.data;
}

export async function getFaces(folderPath: string) {
  const res = await axios.get(`${API_BASE}/faces`, { params: { folderPath } });
  return res.data;
}

export async function getClusters() {
  const res = await axios.get(`${API_BASE}/clusters`);
  return res.data;
}

export async function getClusterFaces(clusterId: number) {
  const res = await axios.get(`${API_BASE}/clusters/${clusterId}/faces`);
  return res.data;
}

export async function assignClusterToPerson(clusterId: number, personName: string) {
  return axios.post(`${API_BASE}/clusters/${clusterId}/assign-person`, { personName });
}

export async function removeFaceFromCluster(faceId: number) {
  return axios.post(`${API_BASE}/faces/${faceId}/remove-from-cluster`);
}

export async function getPersons() {
  const res = await axios.get(`${API_BASE}/persons`);
  return res.data;
}

export async function getPersonFaces(personId: number) {
  const res = await axios.get(`${API_BASE}/persons/${personId}/faces`);
  return res.data;
}
