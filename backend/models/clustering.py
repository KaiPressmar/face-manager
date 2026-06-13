from typing import Tuple
import numpy as np
import hnswlib


class FaceClustering:
    """Incrementally cluster face embeddings with an HNSW cosine index.

    Args:
        dim: Embedding vector dimension.
        space: HNSW distance metric.

    Embeddings are normalized before insertion. Lower distance thresholds
    produce stricter clusters; higher thresholds reuse clusters more readily.
    """

    def __init__(self, dim: int = 512, space: str = "cosine"):
        """Initialize an empty HNSW clustering index.

        Args:
            dim: Embedding vector dimension.
            space: HNSW distance metric.
        """
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
        """L2-normalize embedding rows.

        Args:
            embeddings: Two-dimensional embedding array.

        Returns:
            Float32 normalized embeddings.
        """
        embeddings = embeddings.astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-12
        return embeddings / norms

    def load_existing(self, embeddings: np.ndarray, cluster_ids: np.ndarray):
        """Load persisted embeddings into the index.

        Args:
            embeddings: Existing normalized or unnormalized face embeddings.
            cluster_ids: Cluster identifier aligned with each embedding.

        Raises:
            ValueError: If the embedding dimension does not match the index.
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
        """Mark an empty index as ready for incremental additions."""
        if not self._initialized:
            self._initialized = True

    def add_and_assign(
        self,
        embeddings: np.ndarray,
        distance_threshold: float = 0.5,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Assign embeddings to nearest clusters and add them to the index.

        Args:
            embeddings: Two-dimensional face embedding array.
            distance_threshold: Maximum cosine distance for cluster reuse.

        Returns:
            Assigned cluster IDs and generated internal HNSW IDs.

        Raises:
            ValueError: If embeddings are not two-dimensional or use the
                wrong vector dimension.
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
