from typing import Any, Tuple
import numpy as np
import hnswlib


def order_embeddings_by_similarity(
    embeddings: np.ndarray,
    stable_ids: np.ndarray | None = None,
) -> np.ndarray:
    """Return a stable nearest-neighbor path through face embeddings.

    The path starts at a peripheral sample and repeatedly follows the nearest
    unvisited neighbor. An HNSW neighbor graph keeps this fast for large
    clusters; exact fallback searches bridge disconnected local regions.
    """
    values = np.asarray(embeddings, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError("Expected a two-dimensional embedding matrix")
    count = values.shape[0]
    if stable_ids is None:
        ids = np.arange(count, dtype=np.int64)
    else:
        ids = np.asarray(stable_ids, dtype=np.int64)
        if ids.shape != (count,):
            raise ValueError("Stable IDs must align with embedding rows")
    if count <= 1:
        return np.arange(count, dtype=int)

    norms = np.linalg.norm(values, axis=1, keepdims=True)
    values = values / (norms + 1e-12)
    centroid = np.mean(values, axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-12
    # Start at the periphery so the path travels through each appearance mode
    # instead of beginning in the middle and immediately choosing a branch.
    start = int(np.lexsort((ids, values @ centroid))[0])

    index = hnswlib.Index(space="cosine", dim=values.shape[1])
    index.init_index(
        max_elements=count,
        ef_construction=min(200, max(50, count)),
        M=16,
        random_seed=17,
    )
    index.set_ef(min(100, count))
    row_ids = np.arange(count)
    index.add_items(values, row_ids)
    neighbor_count = min(32, count)
    neighbor_rows, neighbor_distances = index.knn_query(
        values,
        k=neighbor_count,
    )

    visited = np.zeros(count, dtype=bool)
    order = []
    current = start
    for _ in range(count):
        order.append(current)
        visited[current] = True
        if len(order) == count:
            break

        candidates = [
            (float(distance), int(ids[int(row)]), int(row))
            for row, distance in zip(
                neighbor_rows[current],
                neighbor_distances[current],
            )
            if not visited[int(row)]
        ]
        if candidates:
            current = min(candidates)[2]
            continue

        unvisited = np.flatnonzero(~visited)
        distances = 1.0 - values[unvisited] @ values[current]
        fallback_order = np.lexsort((ids[unvisited], distances))
        current = int(unvisited[int(fallback_order[0])])

    return np.asarray(order, dtype=int)


def consolidate_small_clusters(
    embeddings: np.ndarray,
    cluster_ids: np.ndarray,
    distance_threshold: float,
    *,
    movable_mask: np.ndarray | None = None,
    max_source_size: int = 2,
    min_target_size: int = 3,
) -> np.ndarray:
    """Conservatively merge small clusters into well-supported larger ones.

    Incremental clustering is order-dependent: a face processed before its
    natural neighbors may remain a singleton. This second pass examines local
    neighborhoods after the complete batch is available. A small cluster is
    merged only when every movable member selects the same larger cluster,
    multiple neighbors support it, and no similarly good runner-up exists.
    """
    values = np.asarray(embeddings, dtype=np.float32)
    labels = np.asarray(cluster_ids, dtype=int)
    if values.ndim != 2 or labels.shape != (values.shape[0],):
        raise ValueError("Embeddings and cluster IDs must have matching rows")
    if values.shape[0] < min_target_size + 1:
        return labels.copy()
    if movable_mask is None:
        movable = np.ones(values.shape[0], dtype=bool)
    else:
        movable = np.asarray(movable_mask, dtype=bool)
        if movable.shape != (values.shape[0],):
            raise ValueError("Movable mask must align with embedding rows")

    norms = np.linalg.norm(values, axis=1, keepdims=True)
    valid = norms[:, 0] > 1e-12
    values = values / (norms + 1e-12)
    sizes = {
        int(cluster_id): int(np.count_nonzero(labels == cluster_id))
        for cluster_id in np.unique(labels)
    }
    eligible_targets = {
        cluster_id for cluster_id, size in sizes.items() if size >= min_target_size
    }
    if not eligible_targets:
        return labels.copy()

    index = hnswlib.Index(space="cosine", dim=values.shape[1])
    index.init_index(max_elements=values.shape[0], ef_construction=100, M=16)
    index.set_ef(min(100, values.shape[0]))
    row_ids = np.arange(values.shape[0])
    index.add_items(values, row_ids)
    neighbor_count = min(16, values.shape[0])
    neighbor_ids, neighbor_distances = index.knn_query(values, k=neighbor_count)

    relaxed_threshold = min(
        1.0,
        distance_threshold + min(0.06, max(0.02, distance_threshold * 0.1)),
    )
    required_margin = max(0.02, distance_threshold * 0.06)
    source_choices: dict[int, int] = {}

    for source_cluster_id, source_size in sizes.items():
        source_rows = np.flatnonzero((labels == source_cluster_id) & movable & valid)
        if source_size > max_source_size or source_rows.size != source_size:
            continue

        member_choices = []
        for row_index in source_rows:
            candidate_distances: dict[int, list[float]] = {}
            close_neighbors: list[int] = []
            for neighbor_id, distance in zip(
                neighbor_ids[row_index],
                neighbor_distances[row_index],
            ):
                neighbor_row = int(neighbor_id)
                candidate_cluster_id = int(labels[neighbor_row])
                if (
                    candidate_cluster_id == source_cluster_id
                    or candidate_cluster_id not in eligible_targets
                    or distance > relaxed_threshold
                ):
                    continue
                candidate_distances.setdefault(candidate_cluster_id, []).append(
                    float(distance)
                )
                if len(close_neighbors) < 5:
                    close_neighbors.append(candidate_cluster_id)

            scored_candidates = []
            for candidate_cluster_id, distances in candidate_distances.items():
                distances.sort()
                required_support = 2 if distances[0] <= distance_threshold else 3
                if len(distances) < required_support:
                    continue
                score = float(np.mean(distances[: min(3, len(distances))]))
                scored_candidates.append((score, candidate_cluster_id))

            if not scored_candidates or not close_neighbors:
                member_choices = []
                break
            scored_candidates.sort()
            best_score, best_cluster_id = scored_candidates[0]
            support = close_neighbors.count(best_cluster_id)
            if support < 2 or support / len(close_neighbors) < 0.6:
                member_choices = []
                break
            if (
                len(scored_candidates) > 1
                and scored_candidates[1][0] - best_score < required_margin
            ):
                member_choices = []
                break
            member_choices.append(best_cluster_id)

        if member_choices and len(set(member_choices)) == 1:
            source_choices[source_cluster_id] = member_choices[0]

    consolidated = labels.copy()
    for source_cluster_id, target_cluster_id in source_choices.items():
        consolidated[(labels == source_cluster_id) & movable] = target_cluster_id
    return consolidated


def split_heterogeneous_clusters(
    embeddings: np.ndarray,
    cluster_ids: np.ndarray,
    distance_threshold: float,
    *,
    movable_mask: np.ndarray | None = None,
    outlier_threshold: float | None = None,
) -> np.ndarray:
    """Recursively split rebuilt clusters with a broad, heterogeneous core.

    A maximum radius is much too sensitive to difficult crops, while accepting
    a cluster from its nearest-neighbour chain can join several identities via
    a handful of bridge faces.  The 95th percentile radius is deliberately
    used as the gate: it tolerates isolated pose/quality outliers but detects a
    sizeable second population.  A correct diverse cluster is retained when
    its robust radius fits the configured threshold.
    """
    values = np.asarray(embeddings, dtype=np.float32)
    labels = np.asarray(cluster_ids, dtype=int).copy()
    if values.ndim != 2 or labels.shape != (values.shape[0],):
        raise ValueError("Embeddings and cluster IDs must have matching rows")
    if movable_mask is None:
        movable = np.ones(values.shape[0], dtype=bool)
    else:
        movable = np.asarray(movable_mask, dtype=bool)
        if movable.shape != (values.shape[0],):
            raise ValueError("Movable mask must align with embedding rows")
    if values.shape[0] < 3:
        return labels

    values /= np.linalg.norm(values, axis=1, keepdims=True) + 1e-12
    next_cluster_id = int(np.max(labels)) + 1
    pending = [int(cluster_id) for cluster_id in np.unique(labels)]
    while pending:
        cluster_id = pending.pop(0)
        rows = np.flatnonzero(labels == cluster_id)
        if rows.size < 3 or not np.all(movable[rows]):
            continue

        members = values[rows]
        centroid = np.mean(members, axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-12
        parent_distances = 1.0 - members @ centroid
        parent_radius = float(np.quantile(parent_distances, 0.95))
        outlier_limit = (
            float(outlier_threshold)
            if outlier_threshold is not None
            else min(1.0, distance_threshold * 1.12)
        )
        broad_outlier_group = int(np.sum(parent_distances > outlier_limit)) >= max(
            3,
            int(np.ceil(rows.size * 0.05)),
        )
        if parent_radius <= distance_threshold and not broad_outlier_group:
            continue

        first_seed = int(np.argmin(members @ centroid))
        second_seed = int(np.argmin(members @ members[first_seed]))
        if first_seed == second_seed:
            continue
        centers = np.vstack([members[first_seed], members[second_seed]])
        assignments = np.zeros(rows.size, dtype=int)
        for _ in range(12):
            next_assignments = np.argmax(members @ centers.T, axis=1)
            if np.array_equal(assignments, next_assignments):
                assignments = next_assignments
                break
            assignments = next_assignments
            if not np.any(assignments == 0) or not np.any(assignments == 1):
                break
            centers = np.vstack(
                [np.mean(members[assignments == index], axis=0) for index in (0, 1)]
            )
            centers /= np.linalg.norm(centers, axis=1, keepdims=True) + 1e-12

        if not np.any(assignments == 0) or not np.any(assignments == 1):
            continue
        child_sizes = [int(np.sum(assignments == index)) for index in (0, 1)]
        if min(child_sizes) < 3:
            continue
        center_separation = float(1.0 - centers[0] @ centers[1])
        if center_separation < max(0.04, distance_threshold * 0.12):
            continue
        # Keep the old logical ID for the subgroup containing the earliest row
        # and allocate one fresh ID for the other subgroup.
        retained_group = int(assignments[np.argmin(rows)])
        split_group = 1 - retained_group
        labels[rows[assignments == split_group]] = next_cluster_id
        pending.extend([cluster_id, next_cluster_id])
        next_cluster_id += 1

    return labels


def tune_distance_threshold(
    embeddings: np.ndarray,
    person_ids: np.ndarray,
    *,
    cluster_ids: np.ndarray | None = None,
    step: float = 0.01,
) -> dict[str, Any]:
    """Calibrate a cosine-distance threshold from person-assigned faces.

    This mirrors the clusterer's cohesion checks with leave-one-out examples.
    A positive match must fit both local neighbors and the reference group's
    centroid. Different people provide negative candidates using the same
    effective distance. Existing subclusters define positive appearance modes
    when available, while person IDs remain the negative supervision signal.

    Each person contributes equally to the score, regardless of how many
    assigned photos they have.  Ties prefer the lower (safer) threshold to
    reduce accidental merges between different people.
    """
    values = np.asarray(embeddings, dtype=np.float32)
    labels = np.asarray(person_ids)
    if values.ndim != 2 or values.shape[0] != labels.shape[0]:
        raise ValueError("Embeddings and person labels must have matching rows.")
    if values.shape[0] < 3:
        raise ValueError("At least three assigned faces are required for auto-tuning.")

    unique_people, counts = np.unique(labels, return_counts=True)
    if unique_people.size < 2:
        raise ValueError("Assign faces to at least two people before auto-tuning.")
    if not np.any(counts >= 2):
        raise ValueError(
            "At least one person needs two assigned faces before auto-tuning."
        )
    if cluster_ids is None:
        appearance_groups = labels.copy()
    else:
        appearance_groups = np.asarray(cluster_ids)
        if appearance_groups.shape != labels.shape:
            raise ValueError("Cluster IDs must align with embedding rows.")

    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("Assigned faces contain an invalid zero-length embedding.")
    values = values / norms

    positive_effective = np.full(values.shape[0], np.nan, dtype=np.float32)
    negative_effective = np.full(values.shape[0], np.nan, dtype=np.float32)

    def effective_distance(anchor: np.ndarray, references: np.ndarray) -> float:
        distances = np.sort(1.0 - references @ anchor)
        centroid = np.mean(references, axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-12
        required = [float(distances[0]), float(1.0 - centroid @ anchor)]
        # This mirrors the multi-neighbor requirement once a candidate cluster
        # already contains at least three reference faces.
        if references.shape[0] >= 3:
            required.append(float(distances[1]))
        return float(np.clip(max(required), 0.0, 2.0))

    for row_index in range(values.shape[0]):
        positive_mask = appearance_groups == appearance_groups[row_index]
        positive_mask[row_index] = False
        if not np.any(positive_mask):
            positive_mask = labels == labels[row_index]
            positive_mask[row_index] = False
        if np.any(positive_mask):
            positive_effective[row_index] = effective_distance(
                values[row_index],
                values[positive_mask],
            )

        negative_candidates = []
        for other_person_id in unique_people:
            if other_person_id == labels[row_index]:
                continue
            other_references = values[labels == other_person_id]
            negative_candidates.append(
                effective_distance(values[row_index], other_references)
            )
        negative_effective[row_index] = min(negative_candidates)

    candidates = np.arange(0.0, 1.0 + step / 2.0, step, dtype=np.float64)
    best: tuple[float, float, float, float] | None = None
    for candidate in candidates:
        same_rates = []
        other_rates = []
        for person_id in unique_people:
            person_mask = labels == person_id
            valid_same = person_mask & np.isfinite(positive_effective)
            if np.any(valid_same):
                same_rates.append(
                    float(np.mean(positive_effective[valid_same] <= candidate))
                )
            other_rates.append(
                float(np.mean(negative_effective[person_mask] > candidate))
            )

        same_accuracy = float(np.mean(same_rates))
        other_accuracy = float(np.mean(other_rates))
        balanced_accuracy = (same_accuracy + other_accuracy) / 2.0
        result = (balanced_accuracy, other_accuracy, same_accuracy, float(candidate))
        if best is None or result[:3] > best[:3]:
            best = result

    assert best is not None
    balanced_accuracy, other_accuracy, same_accuracy, threshold = best
    return {
        "threshold": round(threshold, 2),
        "sample_size": int(values.shape[0]),
        "person_count": int(unique_people.size),
        "same_person_accuracy": round(same_accuracy, 4),
        "different_person_accuracy": round(other_accuracy, 4),
        "balanced_accuracy": round(balanced_accuracy, 4),
        "cohesion_aware": True,
    }


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
        self.index.set_ef(100)
        self._next_internal_id = 0
        self._internal_to_cluster: dict[int, int] = {}
        self._internal_to_person: dict[int, int | None] = {}
        self._person_prototypes: dict[int, np.ndarray] = {}
        self._cluster_counts: dict[int, int] = {}
        self._cluster_sums: dict[int, np.ndarray] = {}
        # Cluster ids start at 1. Zero is avoided because it collides with the
        # "no cluster" sentinel in falsy checks across the stack and cannot be
        # distinguished from an unset id in some UI paths.
        self._next_cluster_id = 1
        self._initialized = False

    @staticmethod
    def _select_prototypes(
        embeddings: np.ndarray,
        max_prototypes: int,
    ) -> np.ndarray:
        """Select deterministic spherical prototypes covering one face group."""
        count = embeddings.shape[0]
        prototype_count = min(
            max_prototypes,
            max(1, int(np.ceil(count / 12.0))),
        )
        if prototype_count == 1:
            center = np.mean(embeddings, axis=0, keepdims=True)
            center /= np.linalg.norm(center, axis=1, keepdims=True) + 1e-12
            return center.astype(np.float32)

        overall_center = np.mean(embeddings, axis=0)
        overall_center /= np.linalg.norm(overall_center) + 1e-12
        first_index = int(np.argmax(embeddings @ overall_center))
        centers = [embeddings[first_index].copy()]
        for _ in range(1, prototype_count):
            similarities = embeddings @ np.vstack(centers).T
            next_index = int(np.argmin(np.max(similarities, axis=1)))
            centers.append(embeddings[next_index].copy())

        centers_array = np.vstack(centers)
        for _ in range(8):
            assignments = np.argmax(embeddings @ centers_array.T, axis=1)
            next_centers = centers_array.copy()
            for center_index in range(prototype_count):
                members = embeddings[assignments == center_index]
                if members.size == 0:
                    continue
                next_centers[center_index] = np.mean(members, axis=0)
            next_centers /= (
                np.linalg.norm(next_centers, axis=1, keepdims=True) + 1e-12
            )
            centers_array = next_centers
        return centers_array.astype(np.float32)

    def _build_person_prototypes(
        self,
        embeddings: np.ndarray,
        cluster_ids: np.ndarray,
        person_ids: np.ndarray,
    ) -> None:
        """Build several appearance prototypes per person while retaining clusters."""
        self._person_prototypes = {}
        valid_people = sorted({int(value) for value in person_ids if int(value) >= 0})
        for person_id in valid_people:
            person_mask = person_ids == person_id
            person_embeddings = embeddings[person_mask]
            person_clusters = cluster_ids[person_mask]
            prototypes = []
            for cluster_id in sorted({int(value) for value in person_clusters}):
                cluster_embeddings = person_embeddings[person_clusters == cluster_id]
                prototypes.append(self._select_prototypes(cluster_embeddings, 3))
            combined = np.vstack(prototypes)
            self._person_prototypes[person_id] = combined

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

    def _register_assignment(
        self,
        internal_id: int,
        cluster_id: int,
        person_id: int | None,
        embedding: np.ndarray,
    ) -> None:
        """Register index metadata and update the cluster-wide centroid."""
        self._internal_to_cluster[int(internal_id)] = int(cluster_id)
        self._internal_to_person[int(internal_id)] = person_id
        self._cluster_counts[cluster_id] = self._cluster_counts.get(cluster_id, 0) + 1
        if cluster_id in self._cluster_sums:
            self._cluster_sums[cluster_id] += embedding
        else:
            self._cluster_sums[cluster_id] = embedding.astype(np.float32).copy()

    def _cluster_centroid(self, cluster_id: int) -> np.ndarray:
        centroid = self._cluster_sums[cluster_id]
        return centroid / (np.linalg.norm(centroid) + 1e-12)

    def load_existing(
        self,
        embeddings: np.ndarray,
        cluster_ids: np.ndarray,
        person_ids: np.ndarray | None = None,
    ):
        """Load persisted embeddings into the index.

        Args:
            embeddings: Existing normalized or unnormalized face embeddings.
            cluster_ids: Cluster identifier aligned with each embedding.
            person_ids: Person identifier for each embedding, or ``-1`` for
                unassigned clusters. When omitted, all clusters are unassigned.

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
        cluster_ids = np.asarray(cluster_ids, dtype=int)
        if cluster_ids.shape != (n,):
            raise ValueError("Cluster IDs must align with embedding rows")
        if person_ids is None:
            normalized_person_ids = np.full(n, -1, dtype=int)
        else:
            normalized_person_ids = np.asarray(person_ids, dtype=int)
            if normalized_person_ids.shape != (n,):
                raise ValueError("Person IDs must align with embedding rows")

        ids = np.arange(self._next_internal_id, self._next_internal_id + n)
        self._next_internal_id += n
        self.index.add_items(embeddings, ids)

        for row_index, (internal_id, cid, person_id) in enumerate(
            zip(ids, cluster_ids, normalized_person_ids)
        ):
            cid = int(cid)
            self._register_assignment(
                int(internal_id),
                cid,
                int(person_id) if int(person_id) >= 0 else None,
                embeddings[row_index],
            )
            self._next_cluster_id = max(self._next_cluster_id, cid + 1)

        self._build_person_prototypes(
            embeddings,
            cluster_ids,
            normalized_person_ids,
        )

        self._initialized = True

    def _ensure_initialized(self):
        """Mark an empty index as ready for incremental additions."""
        if not self._initialized:
            self._initialized = True

    def _person_cluster_match(
        self,
        embedding: np.ndarray,
        neighbor_ids: np.ndarray,
        neighbor_distances: np.ndarray,
        distance_threshold: float,
    ) -> tuple[int, int] | None:
        """Return a confident ``(cluster, person)`` match from labeled neighbors."""
        assigned_neighbors: dict[int, list[tuple[float, int]]] = {}
        close_assigned_people: list[int] = []
        for neighbor_id, distance in zip(neighbor_ids, neighbor_distances):
            internal_id = int(neighbor_id)
            person_id = self._internal_to_person.get(internal_id)
            if person_id is None:
                continue
            assigned_neighbors.setdefault(person_id, []).append(
                (float(distance), internal_id)
            )
            if distance <= distance_threshold and len(close_assigned_people) < 5:
                close_assigned_people.append(person_id)

        if not assigned_neighbors or not close_assigned_people:
            return None

        vote_counts = {
            person_id: close_assigned_people.count(person_id)
            for person_id in set(close_assigned_people)
        }
        scores: list[tuple[float, int]] = []
        for person_id, neighbors in assigned_neighbors.items():
            prototypes = self._person_prototypes.get(person_id)
            if prototypes is None or prototypes.size == 0:
                continue
            prototype_distance = float(np.min(1.0 - prototypes @ embedding))
            nearest_distances = [distance for distance, _ in neighbors[:3]]
            neighbor_distance = float(np.mean(nearest_distances))
            scores.append(((prototype_distance + neighbor_distance) / 2.0, person_id))

        if not scores:
            return None
        scores.sort()
        best_score, best_person_id = scores[0]
        best_votes = vote_counts.get(best_person_id, 0)
        agreement = best_votes / len(close_assigned_people)
        if best_votes < 2 or agreement < 0.6 or best_score > distance_threshold:
            return None

        if len(scores) > 1:
            required_margin = max(0.02, distance_threshold * 0.08)
            if scores[1][0] - best_score < required_margin:
                return None

        _, closest_internal_id = assigned_neighbors[best_person_id][0]
        return self._internal_to_cluster[closest_internal_id], best_person_id

    def _unassigned_cluster_match(
        self,
        embedding: np.ndarray,
        neighbor_ids: np.ndarray,
        neighbor_distances: np.ndarray,
        distance_threshold: float,
    ) -> int | None:
        """Match an inbox cluster only when its local and global shape agree."""
        by_cluster: dict[int, list[float]] = {}
        close_clusters: list[int] = []
        for neighbor_id, distance in zip(neighbor_ids, neighbor_distances):
            internal_id = int(neighbor_id)
            if self._internal_to_person.get(internal_id) is not None:
                continue
            cluster_id = self._internal_to_cluster[internal_id]
            by_cluster.setdefault(cluster_id, []).append(float(distance))
            if distance <= distance_threshold and len(close_clusters) < 5:
                close_clusters.append(cluster_id)

        candidates: list[tuple[float, int]] = []
        for cluster_id, distances in by_cluster.items():
            distances.sort()
            if distances[0] > distance_threshold:
                continue
            centroid_distance = float(
                1.0 - self._cluster_centroid(cluster_id) @ embedding
            )
            if centroid_distance > distance_threshold:
                continue

            cluster_size = self._cluster_counts[cluster_id]
            support = sum(distance <= distance_threshold for distance in distances)
            if cluster_size >= 3:
                if support < 2 or not close_clusters:
                    continue
                local_votes = close_clusters.count(cluster_id)
                if local_votes < 2 or local_votes / len(close_clusters) < 0.6:
                    continue
            score = (distances[0] + centroid_distance) / 2.0
            candidates.append((score, cluster_id))

        if not candidates:
            return None
        candidates.sort()
        if len(candidates) > 1:
            required_margin = max(0.01, distance_threshold * 0.03)
            if candidates[1][0] - candidates[0][0] < required_margin:
                return None
        return candidates[0][1]

    def add_and_assign(
        self,
        embeddings: np.ndarray,
        distance_threshold: float = 0.5,
        *,
        allow_person_matches: bool = True,
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
                self._register_assignment(
                    int(internal_id),
                    cid,
                    None,
                    embeddings[i],
                )
                cluster_ids[i] = cid
            self.index.add_items(embeddings, internal_ids)
            return cluster_ids, internal_ids

        # Inspect enough neighbors for person consensus and for a separate
        # unassigned-cluster fallback. hnswlib cosine distance is 1 - cosine.
        neighbor_count = min(64, len(self._internal_to_cluster))
        nn_ids, nn_dists = self.index.knn_query(embeddings, k=neighbor_count)

        for i, internal_id in enumerate(internal_ids):
            person_match = (
                self._person_cluster_match(
                    embeddings[i],
                    nn_ids[i],
                    nn_dists[i],
                    distance_threshold,
                )
                if allow_person_matches
                else None
            )
            person_id: int | None = None
            if person_match is not None:
                cid, person_id = person_match
            else:
                unassigned_match = self._unassigned_cluster_match(
                    embeddings[i],
                    nn_ids[i],
                    nn_dists[i],
                    distance_threshold,
                )
                cid = unassigned_match if unassigned_match is not None else -1

            if cid < 0:
                cid = self._next_cluster_id
                self._next_cluster_id += 1

            self._register_assignment(
                int(internal_id),
                cid,
                person_id,
                embeddings[i],
            )
            cluster_ids[i] = cid

        self.index.add_items(embeddings, internal_ids)
        return cluster_ids, internal_ids
