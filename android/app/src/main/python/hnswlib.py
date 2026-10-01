"""The desktop clustering API backed by the same HNSW implementation on Android."""

import numpy as np
from java import jarray, jclass, jfloat, jlong

_NativeIndex = jclass("de.face_manager.app.HnswIndex")
_BATCH = 128


class Index:
    def __init__(self, space, dim):
        self._native = None
        if space != "cosine" or int(dim) <= 0:
            raise ValueError("Android HNSW requires cosine space and a positive dimension")
        self.space = space
        self.dim = int(dim)
        self._next_id = 0

    def init_index(self, max_elements, M=16, ef_construction=200, random_seed=100):
        if self._native is not None:
            raise RuntimeError("The index is already initialized")
        self._native = _NativeIndex(
            self.dim, int(max_elements), int(ef_construction), int(M), int(random_seed)
        )

    def _require_index(self):
        if self._native is None:
            raise RuntimeError("The index is not initialized or has been closed")
        return self._native

    def set_ef(self, ef):
        self._require_index().setEf(int(ef))

    def _values(self, data):
        values = np.asarray(data, dtype=np.float32)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        if values.ndim != 2 or values.shape[1] != self.dim:
            raise ValueError("Embedding dimensions do not match index")
        if not np.isfinite(values).all():
            raise ValueError("Embedding must contain finite values")
        return np.ascontiguousarray(values)

    def add_items(self, data, ids=None, num_threads=-1):
        native = self._require_index()
        values = self._values(data)
        count = len(values)
        labels = (
            np.arange(self._next_id, self._next_id + count, dtype=np.int64)
            if ids is None else np.asarray(ids, dtype=np.int64).reshape(-1)
        )
        if labels.shape != (count,) or np.any(labels < 0):
            raise ValueError("Nonnegative labels must match embedding rows")
        for start in range(0, count, _BATCH):
            stop = start + _BATCH
            native.add(
                jarray(jfloat)(values[start:stop].ravel()),
                jarray(jlong)(labels[start:stop]),
            )
        if count:
            self._next_id = max(self._next_id, int(labels.max()) + 1)

    def knn_query(self, data, k=1, num_threads=-1):
        native = self._require_index()
        values = self._values(data)
        k = int(k)
        if k <= 0 or k > native.size():
            raise RuntimeError("k must be positive and cannot exceed the number of indexed items")
        labels = np.empty((len(values), k), dtype=np.int64)
        distances = np.empty((len(values), k), dtype=np.float32)
        for start in range(0, len(values), _BATCH):
            stop = min(start + _BATCH, len(values))
            result = native.query(jarray(jfloat)(values[start:stop].ravel()), k)
            labels[start:stop] = np.asarray(result.labels).reshape(stop - start, k)
            distances[start:stop] = np.asarray(result.distances).reshape(stop - start, k)
        return labels, distances

    def get_current_count(self):
        return self._require_index().size()

    def close(self):
        if self._native is not None:
            self._native.close()
            self._native = None

    def __del__(self):
        self.close()
