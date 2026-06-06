from typing import Tuple
import numpy as np
import hnswlib


class FaceClustering:
    """
    Incremental face clustering using HNSW.

    - Uses cosine distance (space="cosine")
    - Embeddings are expected to be L2-normalized (but wir normalisieren zur Sicherheit nochmal)
    - distance_threshold ist ein Cosine-Distanz-Threshold:
        0.0  → identisch
        0.4  → cos-sim >= 0.6
        0.6  → cos-sim >= 0.4
        Wenn du willst, dass mehr Gesichter zusammengefasst werden:
        --> threshold erhöhen (z. B. 0.5)
        Wenn du willst, dass weniger Gesichter zusammengefasst werden:
        --> threshold senken (z. B. 0.3)
    """

    def __init__(self, dim: int = 512, space: str = "cosine"):
        self.dim = dim
        self.space = space
        self.index = hnswlib.Index(space=space, dim=dim)
        self.index.init_index(max_elements=200_000, ef_construction=200, M=16)
        self.index.set_ef(50)
        self._next_internal_id = 0
        self._internal_to_cluster: dict[int, int] = {}
        self._next_cluster_id = 0
        self._initialized = False

    def _normalize(self, embeddings: np.ndarray) -> np.ndarray:
        embeddings = embeddings.astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-12
        return embeddings / norms

    def load_existing(self, embeddings: np.ndarray, cluster_ids: np.ndarray):
        """
        Load existing embeddings + cluster_ids from DB into the index.
        """
        if embeddings.size == 0:
            self._initialized = True
            return

        embeddings = self._normalize(embeddings)
        n, d = embeddings.shape
        if d != self.dim:
            raise ValueError(f"Expected dim {self.dim}, got {d}")

        ids = np.arange(self._next_internal_id, self._next_internal_id + n)
        self._next_internal_id += n
        self.index.add_items(embeddings, ids)

        for internal_id, cid in zip(ids, cluster_ids):
            cid = int(cid)
            self._internal_to_cluster[int(internal_id)] = cid
            self._next_cluster_id = max(self._next_cluster_id, cid + 1)

        self._initialized = True

    def _ensure_initialized(self):
        if not self._initialized:
            self._initialized = True

    def add_and_assign(
        self,
        embeddings: np.ndarray,
        distance_threshold: float = 0.5,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Add new embeddings and assign them to existing or new clusters.

        distance_threshold ist eine Cosine-Distanz:
        - 0.4 ≈ cos-sim >= 0.6 (relativ streng)
        - 0.5 ≈ cos-sim >= 0.5 (lockerer)
        """
        self._ensure_initialized()

        embeddings = np.asarray(embeddings, dtype=np.float32)
        if embeddings.ndim != 2:
            raise ValueError(f"Expected 2D embeddings, got shape {embeddings.shape}")

        embeddings = self._normalize(embeddings)

        n, d = embeddings.shape
        if d != self.dim:
            raise ValueError(f"Expected dim {self.dim}, got {d}")

        cluster_ids = np.empty(n, dtype=int)
        internal_ids = np.arange(self._next_internal_id, self._next_internal_id + n)
        self._next_internal_id += n

        # First faces ever → jeder bekommt eigenen Cluster
        if len(self._internal_to_cluster) == 0:
            for i, internal_id in enumerate(internal_ids):
                cid = self._next_cluster_id
                self._next_cluster_id += 1
                self._internal_to_cluster[int(internal_id)] = cid
                cluster_ids[i] = cid
            self.index.add_items(embeddings, internal_ids)
            return cluster_ids, internal_ids

        # Query nearest neighbor for each embedding
        nn_ids, nn_dists = self.index.knn_query(embeddings, k=1)
        nn_ids = nn_ids[:, 0]
        nn_dists = nn_dists[:, 0]

        for i, (internal_id, nn_id, dist) in enumerate(
            zip(internal_ids, nn_ids, nn_dists)
        ):
            # hnswlib "cosine" space: dist = 1 - cos_sim
            if dist <= distance_threshold:
                cid = self._internal_to_cluster[int(nn_id)]
            else:
                cid = self._next_cluster_id
                self._next_cluster_id += 1

            self._internal_to_cluster[int(internal_id)] = cid
            cluster_ids[i] = cid

        self.index.add_items(embeddings, internal_ids)
        return cluster_ids, internal_ids
