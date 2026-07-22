import logging
import os
import re
import sqlite3
from collections import defaultdict
from typing import List, Tuple

import numpy as np

from ..db.schema import (
    FACE_REVIEW_STATUS_ACTIVE,
    FACE_REVIEW_STATUS_NOT_FACE,
    FACE_REVIEW_STATUS_UNKNOWN_PERSON,
    VALID_FACE_REVIEW_STATUSES,
    get_conn,
)
from ..error_logging import (
    DEFAULT_FILE_LOG_LEVEL,
    configure_error_logging,
    normalize_file_log_level,
)
from ..models.clustering import (
    FaceClustering,
    consolidate_small_clusters,
    order_embeddings_by_similarity,
    split_heterogeneous_clusters,
    tune_distance_threshold,
)
from .cache import app_cache

configure_error_logging()
logger = logging.getLogger("face_manager.storage")

DEFAULT_CLUSTER_DISTANCE_THRESHOLD = 0.5
DEFAULT_CLUSTER_STRICTNESS = 1.0 - DEFAULT_CLUSTER_DISTANCE_THRESHOLD
DEFAULT_FILENAME_PERSON_SUFFIX_FORMAT = " {names}"
DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR = " "
DEFAULT_FILENAME_PERSON_JOINER = ", "
DEFAULT_PERSISTED_FILE_LOG_LEVEL = DEFAULT_FILE_LOG_LEVEL
DEFAULT_UI_THEME = "system"
VALID_UI_THEMES = ("system", "light", "dark")
DEFAULT_AUTOMATIC_UPDATE_CHECKS = True
UNKNOWN_PERSON_LABEL = "Unbekannt"
MAX_IMAGE_PAGE_SIZE = 200
QUERY_CACHE_TTL_SECONDS = 5.0
QUERY_CACHE_TAG_IMAGES = "images"
QUERY_CACHE_TAG_FOLDERS = "folders"
QUERY_CACHE_TAG_PERSONS = "persons"
QUERY_CACHE_TAG_RENAMES = "renames"
QUERY_CACHE_TAG_CLUSTERS = "clusters"
QUERY_CACHE_TAG_SETTINGS = "settings"
QUERY_CACHE_TAG_PERSON_FACES = "person_faces"
QUERY_CACHE_TAG_CLUSTER_FACES = "cluster_faces"
REVIEW_GROUP_UNASSIGNED = "unassigned"
REVIEW_GROUP_UNKNOWN_PERSON = FACE_REVIEW_STATUS_UNKNOWN_PERSON
REVIEW_GROUP_NOT_FACE = FACE_REVIEW_STATUS_NOT_FACE
VALID_FACE_REVIEW_GROUPS = {
    REVIEW_GROUP_UNASSIGNED,
    REVIEW_GROUP_UNKNOWN_PERSON,
    REVIEW_GROUP_NOT_FACE,
}


def _safe_float(v):
    """Convert SQLite numeric representations to a float.

    Args:
        v: Numeric value or little-endian float bytes.

    Returns:
        Converted floating-point value.

    Raises:
        TypeError: If the value uses an unsupported representation.
    """
    if isinstance(v, (float, int)):
        return float(v)
    if isinstance(v, bytes):
        import struct

        return struct.unpack("<d", v)[0]
    raise TypeError(f"Unexpected bbox type: {type(v)}")


def get_cluster_distance_threshold() -> float:
    """Return the persisted clustering distance threshold."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("cluster_distance_threshold",),
        ).fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return DEFAULT_CLUSTER_DISTANCE_THRESHOLD
    conn.close()
    if not row:
        return DEFAULT_CLUSTER_DISTANCE_THRESHOLD
    try:
        return float(row["value"])
    except (TypeError, ValueError):
        return DEFAULT_CLUSTER_DISTANCE_THRESHOLD


def get_applied_clustering_version() -> str | None:
    """Return the software version whose clustering migration completed."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("applied_clustering_version",),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    value = str(row["value"]).strip() if row and row["value"] is not None else ""
    return value or None


def set_applied_clustering_version(version: str) -> str:
    """Mark one version only after its full clustering migration succeeded."""
    normalized = str(version).strip()
    if not normalized:
        raise ValueError("Missing clustering version")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("applied_clustering_version", normalized),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return normalized


def get_last_seen_changelog_version() -> str | None:
    """Return the last release whose user-facing notes were dismissed."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("last_seen_changelog_version",),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    value = str(row["value"]).strip() if row and row["value"] is not None else ""
    return value or None


def set_last_seen_changelog_version(version: str) -> str:
    """Persist that the user dismissed one release's notes."""
    normalized = str(version).strip()
    if not normalized:
        raise ValueError("Missing changelog version")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("last_seen_changelog_version", normalized),
    )
    conn.commit()
    conn.close()
    return normalized


def get_automatic_update_checks() -> bool:
    """Return whether Face Manager may contact GitHub for update metadata."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("automatic_update_checks",),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if not row:
        return DEFAULT_AUTOMATIC_UPDATE_CHECKS
    return str(row["value"]).strip().lower() not in {"0", "false", "no", "off"}


def set_automatic_update_checks(enabled: bool) -> bool:
    """Persist the opt-out switch for automatic GitHub update checks."""
    if not isinstance(enabled, bool):
        raise ValueError("automatic_update_checks must be a boolean")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("automatic_update_checks", "1" if enabled else "0"),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return enabled


def get_skipped_update_version() -> str | None:
    """Return the release version the user chose not to see again."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            ("skipped_update_version",),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    value = str(row["value"]).strip() if row and row["value"] is not None else ""
    return value or None


def set_skipped_update_version(version: str | None) -> str | None:
    """Skip one release; a later version will still be offered automatically."""
    normalized = str(version or "").strip()
    conn = get_conn()
    if normalized:
        conn.execute(
            """
            INSERT INTO app_settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            ("skipped_update_version", normalized),
        )
    else:
        conn.execute(
            "DELETE FROM app_settings WHERE key = ?", ("skipped_update_version",)
        )
    conn.commit()
    conn.close()
    return normalized or None


def _derive_clustering_profile(distance_threshold: float) -> dict[str, float]:
    """Build safe, coherent defaults from the legacy global threshold."""
    neighbor = float(np.clip(distance_threshold, 0.0, 1.0))
    return {
        "neighbor_threshold": neighbor,
        "cohesion_threshold": neighbor,
        "person_anchor_threshold": float(min(0.48, neighbor * 0.70)),
        "ambiguity_margin": float(max(0.055, neighbor * 0.09)),
        "cluster_support_ratio": 0.80,
        "outlier_threshold": float(min(1.0, neighbor * 1.12)),
    }


def get_clustering_profile() -> dict[str, float]:
    """Return the persisted multi-dimensional clustering profile.

    Older databases only contain ``cluster_distance_threshold`` and therefore
    transparently receive safe derived defaults until auto-tuning stores the
    individual profile values.
    """
    threshold = get_cluster_distance_threshold()
    profile = _derive_clustering_profile(threshold)
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT key, value FROM app_settings
            WHERE key IN (
                'cluster_cohesion_threshold',
                'cluster_person_anchor_threshold',
                'cluster_ambiguity_margin',
                'cluster_support_ratio',
                'cluster_outlier_threshold'
            )
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    key_map = {
        "cluster_cohesion_threshold": "cohesion_threshold",
        "cluster_person_anchor_threshold": "person_anchor_threshold",
        "cluster_ambiguity_margin": "ambiguity_margin",
        "cluster_support_ratio": "cluster_support_ratio",
        "cluster_outlier_threshold": "outlier_threshold",
    }
    for row in rows:
        try:
            profile[key_map[row["key"]]] = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
    profile["strictness"] = float(np.clip(1.0 - threshold, 0.0, 1.0))
    return profile


def _persist_clustering_profile(profile: dict[str, float]) -> dict[str, float]:
    """Persist all internal thresholds as one atomic profile update."""
    settings = {
        "cluster_distance_threshold": profile["neighbor_threshold"],
        "cluster_cohesion_threshold": profile["cohesion_threshold"],
        "cluster_person_anchor_threshold": profile["person_anchor_threshold"],
        "cluster_ambiguity_margin": profile["ambiguity_margin"],
        "cluster_support_ratio": profile["cluster_support_ratio"],
        "cluster_outlier_threshold": profile["outlier_threshold"],
    }
    conn = get_conn()
    conn.executemany(
        """
        INSERT INTO app_settings(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [(key, str(float(value))) for key, value in settings.items()],
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return get_clustering_profile()


def get_filename_person_suffix_format() -> str:
    """Return the persisted filename suffix format used for person names."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("filename_person_suffix_format",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_FILENAME_PERSON_SUFFIX_FORMAT
    value = (row["value"] or "").strip()
    if not value or "{names}" not in value:
        return DEFAULT_FILENAME_PERSON_SUFFIX_FORMAT
    return value


def get_filename_person_joiner() -> str:
    """Return the separator used between multiple person names."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("filename_person_joiner",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_FILENAME_PERSON_JOINER
    return row["value"] if row["value"] is not None else DEFAULT_FILENAME_PERSON_JOINER


def get_filename_person_block_separator() -> str:
    """Return the separator between filename and appended person names."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("filename_person_block_separator",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
    return (
        row["value"]
        if row["value"] is not None
        else DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
    )


def get_file_log_level() -> str:
    """Return the persisted log level used for the local error file."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("file_log_level",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_PERSISTED_FILE_LOG_LEVEL
    try:
        return normalize_file_log_level(row["value"])
    except ValueError:
        return DEFAULT_PERSISTED_FILE_LOG_LEVEL


def normalize_ui_theme(value: str | None) -> str:
    """Return a valid UI theme, falling back to the system default."""
    if value is None:
        return DEFAULT_UI_THEME
    normalized = str(value).strip().lower()
    if normalized not in VALID_UI_THEMES:
        raise ValueError(
            f"Invalid UI theme: {value!r}. Expected one of {VALID_UI_THEMES}."
        )
    return normalized


def get_ui_theme() -> str:
    """Return the persisted UI theme preference (system, light or dark)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("ui_theme",),
    ).fetchone()
    conn.close()
    if not row:
        return DEFAULT_UI_THEME
    try:
        return normalize_ui_theme(row["value"])
    except ValueError:
        return DEFAULT_UI_THEME


def set_cluster_distance_threshold(value: float) -> float:
    """Persist legacy threshold and reset dependent values coherently."""
    threshold = float(value)
    _persist_clustering_profile(_derive_clustering_profile(threshold))
    return threshold


def set_clustering_strictness(value: float) -> dict[str, float]:
    """Map one user-facing strictness control to the complete safe profile."""
    strictness = float(np.clip(value, 0.0, 1.0))
    return _persist_clustering_profile(
        _derive_clustering_profile(1.0 - strictness)
    )


def auto_tune_cluster_distance_threshold() -> dict:
    """Tune the threshold from faces in person-assigned clusters.

    The sample is capped and taken round-robin across people so tuning remains
    responsive and people with large photo collections do not crowd out those
    with only a few assigned faces.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT f.embedding, c.person_id, c.id AS cluster_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.embedding IS NOT NULL
          AND c.person_id IS NOT NULL
          AND f.review_status = ?
        ORDER BY c.person_id, f.id
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    stability_rows = conn.execute(
        """
        SELECT f.embedding, c.id AS cluster_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.embedding IS NOT NULL
          AND c.person_id IS NULL
          AND f.review_status = ?
        ORDER BY c.id, f.id
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    conn.close()

    by_person_cluster: dict[int, dict[int, list[np.ndarray]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        if embedding.size != 512:
            logger.warning(
                "Skipping invalid %s-dimensional embedding during threshold tuning",
                embedding.size,
            )
            continue
        cluster_embeddings = by_person_cluster[int(row["person_id"])][
            int(row["cluster_id"])
        ]
        if len(cluster_embeddings) < 100:
            cluster_embeddings.append(embedding)

    by_person: dict[int, list[tuple[np.ndarray, int]]] = {}
    for person_id, clusters in by_person_cluster.items():
        person_sample = []
        for sample_index in range(100):
            for cluster_id in sorted(clusters):
                cluster_embeddings = clusters[cluster_id]
                if sample_index >= len(cluster_embeddings):
                    continue
                person_sample.append((cluster_embeddings[sample_index], cluster_id))
                if len(person_sample) >= 100:
                    break
            if len(person_sample) >= 100:
                break
        by_person[person_id] = person_sample

    sampled_embeddings: list[np.ndarray] = []
    sampled_person_ids: list[int] = []
    sampled_cluster_ids: list[int] = []
    for sample_index in range(100):
        for person_id, person_rows in by_person.items():
            if sample_index >= len(person_rows):
                continue
            embedding, cluster_id = person_rows[sample_index]
            sampled_embeddings.append(embedding)
            sampled_person_ids.append(person_id)
            sampled_cluster_ids.append(cluster_id)
            if len(sampled_embeddings) >= 4000:
                break
        if len(sampled_embeddings) >= 4000:
            break

    if not sampled_embeddings:
        raise ValueError(
            "No person-assigned faces with embeddings are available for auto-tuning."
        )

    result = tune_distance_threshold(
        np.vstack(sampled_embeddings),
        np.asarray(sampled_person_ids, dtype=np.int64),
        cluster_ids=np.asarray(sampled_cluster_ids, dtype=np.int64),
    )
    tuned_threshold = float(result["threshold"])
    normalized_samples = np.vstack(sampled_embeddings).astype(np.float32)
    normalized_samples /= (
        np.linalg.norm(normalized_samples, axis=1, keepdims=True) + 1e-12
    )
    sampled_people = np.asarray(sampled_person_ids, dtype=np.int64)
    sampled_clusters = np.asarray(sampled_cluster_ids, dtype=np.int64)

    robust_cluster_radii = []
    for cluster_id in np.unique(sampled_clusters):
        members = normalized_samples[sampled_clusters == cluster_id]
        if members.shape[0] < 3:
            continue
        centroid = np.mean(members, axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-12
        robust_cluster_radii.append(
            float(np.quantile(1.0 - members @ centroid, 0.95))
        )
    cohesion_threshold = tuned_threshold
    if robust_cluster_radii:
        observed_cohesion = float(np.quantile(robust_cluster_radii, 0.75))
        cohesion_threshold = float(
            np.clip(
                observed_cohesion,
                tuned_threshold * 0.85,
                tuned_threshold,
            )
        )

    # Preserve already coherent unknown groups as unsupervised stability
    # anchors. Heterogeneous legacy clusters are excluded because their robust
    # radius is already above the tuned identity boundary.
    stability_clusters: defaultdict[int, list[np.ndarray]] = defaultdict(list)
    oversized_stability_clusters: set[int] = set()
    for row in stability_rows:
        cluster_id = int(row["cluster_id"])
        cluster_embeddings = stability_clusters[cluster_id]
        if len(cluster_embeddings) >= 1000:
            oversized_stability_clusters.add(cluster_id)
            continue
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        if embedding.size == 512:
            cluster_embeddings.append(embedding)
    stable_unknown_radii = []
    for cluster_id, cluster_embeddings in stability_clusters.items():
        if cluster_id in oversized_stability_clusters:
            continue
        if len(cluster_embeddings) < 5:
            continue
        members = np.vstack(cluster_embeddings).astype(np.float32)
        members /= np.linalg.norm(members, axis=1, keepdims=True) + 1e-12
        centroid = np.mean(members, axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-12
        radius = float(np.quantile(1.0 - members @ centroid, 0.95))
        if radius <= tuned_threshold:
            stable_unknown_radii.append(radius)
    if stable_unknown_radii:
        # Every current cluster that already fits the tuned identity boundary
        # is a stability constraint. This prevents auto-tuning from destroying
        # a known-good but visually diverse group merely because most assigned
        # person subclusters happen to be tighter.
        stability_floor = float(max(stable_unknown_radii))
        cohesion_threshold = float(
            np.clip(
                max(cohesion_threshold, stability_floor),
                tuned_threshold * 0.85,
                tuned_threshold,
            )
        )

    positive_distances = []
    negative_distances = []
    observed_margins = []
    for index, embedding in enumerate(normalized_samples):
        same_cluster = sampled_clusters == sampled_clusters[index]
        same_cluster[index] = False
        same_person = sampled_people == sampled_people[index]
        same_person[index] = False
        positive_mask = same_cluster if np.any(same_cluster) else same_person
        negative_mask = sampled_people != sampled_people[index]
        if not np.any(positive_mask) or not np.any(negative_mask):
            continue
        positive = float(np.min(1.0 - normalized_samples[positive_mask] @ embedding))
        negative = float(np.min(1.0 - normalized_samples[negative_mask] @ embedding))
        positive_distances.append(positive)
        negative_distances.append(negative)
        observed_margins.append(negative - positive)

    anchor_threshold = min(0.48, tuned_threshold * 0.70)
    ambiguity_margin = max(0.055, tuned_threshold * 0.09)
    if positive_distances and negative_distances:
        positive_coverage = float(np.quantile(positive_distances, 0.75))
        negative_safety = float(np.quantile(negative_distances, 0.01))
        ambiguity_margin = float(
            np.clip(np.quantile(observed_margins, 0.10) * 0.50, 0.055, 0.18)
        )
        anchor_threshold = float(
            np.clip(
                min(negative_safety - ambiguity_margin, tuned_threshold * 0.80),
                max(0.18, positive_coverage),
                0.48,
            )
        )

    persisted_profile = _persist_clustering_profile(
        {
            "neighbor_threshold": tuned_threshold,
            "cohesion_threshold": cohesion_threshold,
            "person_anchor_threshold": anchor_threshold,
            "ambiguity_margin": ambiguity_margin,
            "cluster_support_ratio": 0.80,
            "outlier_threshold": float(
                min(1.0, max(tuned_threshold, cohesion_threshold * 1.12))
            ),
        }
    )
    result["threshold"] = persisted_profile["neighbor_threshold"]
    result["strictness"] = persisted_profile["strictness"]
    result["profile"] = persisted_profile
    result["precision_priority"] = True
    result["stability_cluster_count"] = len(stable_unknown_radii)
    return result


def set_filename_person_suffix_format(value: str) -> str:
    """Persist the filename suffix format used for detected person names."""
    suffix_format = (value or "").strip()
    if not suffix_format or "{names}" not in suffix_format:
        raise ValueError("Filename suffix format must contain the {names} placeholder.")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("filename_person_suffix_format", suffix_format),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS, QUERY_CACHE_TAG_RENAMES)
    return suffix_format


def set_filename_person_joiner(value: str) -> str:
    """Persist the separator used between person names."""
    joiner = value if value is not None else DEFAULT_FILENAME_PERSON_JOINER
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("filename_person_joiner", joiner),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS, QUERY_CACHE_TAG_RENAMES)
    return joiner


def set_filename_person_block_separator(value: str) -> str:
    """Persist the separator between filename and appended person names."""
    separator = (
        value if value is not None else DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
    )
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("filename_person_block_separator", separator),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS, QUERY_CACHE_TAG_RENAMES)
    return separator


def set_file_log_level(value: str) -> str:
    """Persist the log level used for the local rotating error log."""
    normalized = normalize_file_log_level(value)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("file_log_level", normalized),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return normalized


def set_ui_theme(value: str) -> str:
    """Persist the UI theme preference (system, light or dark)."""
    normalized = normalize_ui_theme(value)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        ("ui_theme", normalized),
    )
    conn.commit()
    conn.close()
    invalidate_query_cache_tags(QUERY_CACHE_TAG_SETTINGS)
    return normalized


def build_filename_person_format_summary(
    joiner: str | None = None,
    block_separator: str | None = None,
) -> str:
    """Return a human-readable format string for UI display and compatibility."""
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner
    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    names_placeholder = f"Name 1{joiner}Name 2"
    return f"DATEI{block_separator}{names_placeholder}.jpg"


def _dedupe_names_in_order(names):
    """Keep the first occurrence of each non-empty name."""
    seen = set()
    ordered = []
    for name in names:
        normalized = (name or "").strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


def _normalize_person_name_key(name: str) -> str:
    """Normalize a person name for case-insensitive comparisons."""
    return (name or "").strip().casefold()


def _extract_trailing_person_names(
    stem: str,
    detected_names: list[str],
    block_separator: str | None = None,
    joiner: str | None = None,
):
    """Split a filename stem into root text and a trailing person appendix."""
    if not stem or not detected_names:
        return stem, []

    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner
    if not block_separator:
        return stem, []

    normalized_names = _dedupe_names_in_order(detected_names)
    if not normalized_names:
        return stem, []

    known_names = {name.casefold(): name for name in normalized_names}
    name_patterns = sorted(normalized_names, key=len, reverse=True)
    separator_pattern = re.compile(r"[\s,;:+&/|()[\]{}._-]+")

    def _parse_suffix_names(suffix_text: str):
        stripped_suffix = suffix_text.strip()
        if not stripped_suffix:
            return []

        suffix_names = []
        position = 0
        while position < len(stripped_suffix):
            matched_name = None
            for candidate in name_patterns:
                candidate_length = len(candidate)
                if stripped_suffix[position : position + candidate_length].casefold() == (
                    candidate.casefold()
                ):
                    matched_name = known_names[candidate.casefold()]
                    suffix_names.append(matched_name)
                    position += candidate_length
                    break
            if matched_name is None:
                return []
            if position >= len(stripped_suffix):
                return suffix_names
            separator_match = separator_pattern.match(stripped_suffix, position)
            if separator_match is None:
                return []
            position = separator_match.end()

    search_from = 0
    while True:
        separator_index = stem.find(block_separator, search_from)
        if separator_index < 0:
            return stem, []

        suffix_text = stem[separator_index + len(block_separator) :]
        if suffix_text:
            suffix_names = _parse_suffix_names(suffix_text)
            root_stem = stem[:separator_index]
            if suffix_names and root_stem:
                return root_stem, suffix_names

        search_from = separator_index + len(block_separator)


def build_person_filename_preview(
    filename: str,
    detected_names: list[str],
    block_separator: str | None = None,
    joiner: str | None = None,
):
    """Build a rename preview for one filename based on detected people."""
    ordered_names = _dedupe_names_in_order(detected_names)
    if not ordered_names:
        return None
    block_separator = (
        DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR
        if block_separator is None
        else block_separator
    )
    joiner = DEFAULT_FILENAME_PERSON_JOINER if joiner is None else joiner

    stem, extension = os.path.splitext(filename)
    root_stem, current_suffix_names = _extract_trailing_person_names(
        stem,
        ordered_names,
        block_separator=block_separator,
        joiner=joiner,
    )
    if not root_stem:
        root_stem = stem

    names_text = joiner.join(ordered_names)
    next_stem = f"{root_stem}{block_separator}{names_text}".strip()
    next_filename = f"{next_stem}{extension}"

    if next_filename == filename and current_suffix_names == ordered_names:
        return None

    return {
        "current_filename": filename,
        "proposed_filename": next_filename,
        "detected_person_names": ordered_names,
        "current_suffix_person_names": current_suffix_names,
    }


def _face_row_to_dict(r):
    """Convert a face database row to an API dictionary.

    Args:
        r: Mapping containing face and image columns.

    Returns:
        JSON-compatible face representation.
    """
    payload = {
        "id": r["id"],
        "image_id": r["image_id"],
        "bbox_x": _safe_float(r["bbox_x"]),
        "bbox_y": _safe_float(r["bbox_y"]),
        "bbox_w": _safe_float(r["bbox_w"]),
        "bbox_h": _safe_float(r["bbox_h"]),
        "cluster_id": r["cluster_id"],
        "person_name": r["person_name"] if "person_name" in r.keys() else None,
        "review_status": (
            r["review_status"]
            if "review_status" in r.keys() and r["review_status"] in VALID_FACE_REVIEW_STATUSES
            else FACE_REVIEW_STATUS_ACTIVE
        ),
    }
    # image_path is only carried where a consumer needs it (e.g. person faces).
    # Cluster and review grids render by face id, so they omit it and skip the
    # image JOIN entirely.
    if "image_path" in r.keys():
        payload["image_path"] = r["image_path"]
    return payload


def normalize_face_review_status(review_status: str | None) -> str:
    """Normalize a persisted face review status."""
    normalized = (review_status or "").strip()
    if normalized not in VALID_FACE_REVIEW_STATUSES:
        raise ValueError(f"Invalid face review status: {review_status}")
    return normalized


def normalize_face_review_group(group_key: str | None) -> str:
    """Normalize a UI-facing face review group key."""
    normalized = (group_key or "").strip()
    if normalized not in VALID_FACE_REVIEW_GROUPS:
        raise ValueError(f"Invalid face review group: {group_key}")
    return normalized


def _normalize_face_ids(face_ids) -> list[int]:
    """Normalize a face selection to unique positive identifiers."""
    normalized = []
    seen = set()
    for value in face_ids or []:
        face_id = int(value)
        if face_id <= 0 or face_id in seen:
            continue
        seen.add(face_id)
        normalized.append(face_id)
    if not normalized:
        raise ValueError("No face_ids supplied")
    return normalized


def _delete_empty_clusters(cur) -> None:
    """Delete cluster rows that no longer reference any face."""
    cur.execute(
        """
        DELETE FROM cluster
        WHERE NOT EXISTS (
            SELECT 1 FROM face WHERE face.cluster_id = cluster.id
        )
        """
    )


def _resolve_person_id(cur, person_name: str) -> int:
    """Resolve or create one canonical person identifier."""
    normalized_person_name = (person_name or "").strip()
    if not normalized_person_name:
        raise ValueError("Missing person_name")
    normalized_name_key = _normalize_person_name_key(normalized_person_name)
    matching_people = cur.execute(
        """
        SELECT id, name
        FROM person
        WHERE LOWER(TRIM(name)) = LOWER(?)
        ORDER BY
            CASE WHEN TRIM(name) = name THEN 0 ELSE 1 END,
            CASE WHEN TRIM(name) = ? THEN 0 ELSE 1 END,
            CASE WHEN name = ? THEN 0 ELSE 1 END,
            id ASC
        """,
        (normalized_person_name, normalized_person_name, normalized_person_name),
    ).fetchall()

    person_id = None
    duplicate_person_ids = []
    for row in matching_people:
        row_name = (row["name"] or "").strip()
        if row_name.casefold() != normalized_name_key:
            continue
        if person_id is None:
            person_id = row["id"]
        else:
            duplicate_person_ids.append(row["id"])

    if person_id is None:
        cur.execute(
            "INSERT INTO person(name) VALUES (?)",
            (normalized_person_name,),
        )
        return cur.lastrowid

    placeholders = ",".join("?" for _ in duplicate_person_ids)
    if duplicate_person_ids:
        cur.execute(
            f"""
            UPDATE cluster
            SET person_id = ?
            WHERE person_id IN ({placeholders})
            """,
            (person_id, *duplicate_person_ids),
        )
        cur.execute(
            f"DELETE FROM person WHERE id IN ({placeholders})",
            duplicate_person_ids,
        )
        logger.warning(
            "Merged duplicate person rows %s into canonical person %s during face assignment",
            duplicate_person_ids,
            person_id,
        )
    canonical_name = (matching_people[0]["name"] or "").strip()
    if canonical_name != matching_people[0]["name"]:
        cur.execute(
            "UPDATE person SET name = ? WHERE id = ?",
            (canonical_name, person_id),
        )
    return person_id


def _create_cluster_for_faces(cur, face_ids: list[int], person_id: int | None = None) -> int:
    """Create a new dedicated cluster for a face selection."""
    cur.execute(
        "INSERT INTO cluster(label, person_id) VALUES (?, ?)",
        (None, person_id),
    )
    cluster_id = int(cur.lastrowid)
    placeholders = ",".join("?" for _ in face_ids)
    cur.execute(
        f"""
        UPDATE face
        SET cluster_id = ?, review_status = ?
        WHERE id IN ({placeholders})
        """,
        (cluster_id, FACE_REVIEW_STATUS_ACTIVE, *face_ids),
    )
    return cluster_id


def count_active_inbox_faces() -> int:
    """Count active faces that are still waiting in the inbox."""
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM face
            WHERE review_status = ?
              AND cluster_id IS NULL
            """,
            (FACE_REVIEW_STATUS_ACTIVE,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["count"]) if row else 0


def count_unassigned_cluster_faces() -> int:
    """Count active faces that belong to clusters without a person assignment."""
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM face f
            LEFT JOIN cluster c ON c.id = f.cluster_id
            WHERE f.review_status = ?
              AND (
                f.cluster_id IS NULL
                OR c.person_id IS NULL
              )
            """,
            (FACE_REVIEW_STATUS_ACTIVE,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["count"]) if row else 0


# Sentinel row in ``recluster_dirty_person`` for faces that belong to no person.
UNASSIGNED_DIRTY_SENTINEL = -1


def _ensure_recluster_dirty_table(cur) -> None:
    """Make the dirty-scope table available on any connection.

    ``init_db`` creates it normally; doing it idempotently here keeps the
    helpers self-sufficient for leaner schemas too.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recluster_dirty_person (
            person_id INTEGER PRIMARY KEY
        )
        """
    )


def mark_persons_dirty_for_recluster(
    cur,
    person_ids=None,
    include_unassigned: bool = True,
) -> None:
    """Record which persons need their subclusters rebuilt.

    Written inside the same transaction as the assignment change so the scope
    can never drift from the data. Passing ``person_ids=None`` marks every
    person, which is the safe fallback for coarse changes.
    """
    targets: set[int] = set()
    if person_ids is None:
        targets.update(
            int(row["id"]) for row in cur.execute("SELECT id FROM person").fetchall()
        )
    else:
        targets.update(int(person_id) for person_id in person_ids if person_id is not None)
    if include_unassigned:
        targets.add(UNASSIGNED_DIRTY_SENTINEL)
    if not targets:
        return
    _ensure_recluster_dirty_table(cur)
    cur.executemany(
        "INSERT OR IGNORE INTO recluster_dirty_person(person_id) VALUES (?)",
        [(person_id,) for person_id in sorted(targets)],
    )


def mark_persons_dirty_for_faces(cur, face_ids, include_unassigned: bool = True) -> None:
    """Mark the persons currently owning the given faces as needing a rebuild."""
    normalized = [int(face_id) for face_id in face_ids or []]
    if not normalized:
        if include_unassigned:
            mark_persons_dirty_for_recluster(cur, [], include_unassigned=True)
        return
    placeholders = ",".join("?" for _ in normalized)
    rows = cur.execute(
        f"""
        SELECT DISTINCT c.person_id AS person_id
        FROM face f
        LEFT JOIN cluster c ON c.id = f.cluster_id
        WHERE f.id IN ({placeholders})
        """,
        normalized,
    ).fetchall()
    person_ids = [row["person_id"] for row in rows if row["person_id"] is not None]
    mark_persons_dirty_for_recluster(cur, person_ids, include_unassigned=include_unassigned)


def _persons_of_faces(cur, face_ids) -> list[int]:
    """Persons currently owning the given faces, captured before a mutation."""
    normalized = [int(face_id) for face_id in face_ids or []]
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    rows = cur.execute(
        f"""
        SELECT DISTINCT c.person_id AS person_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.id IN ({placeholders}) AND c.person_id IS NOT NULL
        """,
        normalized,
    ).fetchall()
    return [int(row["person_id"]) for row in rows]


def _load_dirty_recluster_scope(cur) -> tuple[set[int], bool]:
    """Return the persons awaiting a rebuild and whether the pool is dirty."""
    _ensure_recluster_dirty_table(cur)
    rows = cur.execute("SELECT person_id FROM recluster_dirty_person").fetchall()
    person_ids = {int(row["person_id"]) for row in rows}
    include_unassigned = UNASSIGNED_DIRTY_SENTINEL in person_ids
    person_ids.discard(UNASSIGNED_DIRTY_SENTINEL)
    return person_ids, include_unassigned


def _clear_dirty_recluster_person(cur, person_id: int | None) -> None:
    """Drop one dirty marker after its group was rebuilt successfully."""
    _ensure_recluster_dirty_table(cur)
    cur.execute(
        "DELETE FROM recluster_dirty_person WHERE person_id = ?",
        (UNASSIGNED_DIRTY_SENTINEL if person_id is None else int(person_id),),
    )


def count_reclusterable_faces() -> int:
    """Count all active faces rebuilt by a full clustering pass."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM face WHERE review_status = ?",
            (FACE_REVIEW_STATUS_ACTIVE,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["count"]) if row else 0


def count_scoped_reclusterable_faces() -> int:
    """Count only the faces the next scoped rebuild would actually touch.

    Used as the queue's work estimate so the progress bar reflects the real
    amount of work instead of the whole library.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        groups = _resolve_recluster_groups(cur, scoped=True)
        return sum(
            len(_load_recluster_group_face_ids(cur, person_id)) for person_id in groups
        )
    finally:
        conn.close()


def _assign_faces_to_cluster(
    cur,
    face_ids: list[int],
    cluster_id: int,
    only_unclaimed: bool = False,
) -> int:
    """Attach one or more faces to an existing cluster.

    Args:
        only_unclaimed: Compare-and-set guard for background clustering passes.
            Between reading the candidate faces and writing the result, the
            expensive embedding work runs, during which the user may assign or
            archive one of those faces. With this guard the pass only claims
            faces that are *still* unassigned, so an interactive change always
            wins instead of being silently overwritten.

    Returns:
        Number of faces actually attached.
    """
    placeholders = ",".join("?" for _ in face_ids)
    guard = " AND cluster_id IS NULL AND review_status = ?" if only_unclaimed else ""
    params: list[object] = [cluster_id, FACE_REVIEW_STATUS_ACTIVE, *face_ids]
    if only_unclaimed:
        params.append(FACE_REVIEW_STATUS_ACTIVE)
    cur.execute(
        f"""
        UPDATE face
        SET cluster_id = ?, review_status = ?
        WHERE id IN ({placeholders}){guard}
        """,
        params,
    )
    return int(cur.rowcount or 0)


def _move_faces_to_hidden_review_status(
    cur,
    face_ids: list[int],
    review_status: str,
) -> None:
    """Move faces into a hidden review queue while preserving useful grouping."""
    if not face_ids:
        return

    normalized_status = normalize_face_review_status(review_status)
    if normalized_status == FACE_REVIEW_STATUS_ACTIVE:
        raise ValueError("Hidden review status required")

    placeholders = ",".join("?" for _ in face_ids)
    rows = cur.execute(
        f"""
        SELECT id, cluster_id
        FROM face
        WHERE id IN ({placeholders})
        ORDER BY id ASC
        """,
        face_ids,
    ).fetchall()

    selected_by_cluster: dict[int, list[int]] = {}
    unclustered_face_ids: list[int] = []
    for row in rows:
        cluster_id = row["cluster_id"]
        if cluster_id is None:
            unclustered_face_ids.append(int(row["id"]))
            continue
        selected_by_cluster.setdefault(int(cluster_id), []).append(int(row["id"]))

    for cluster_id, selected_cluster_face_ids in selected_by_cluster.items():
        source_cluster_face_count = int(
            cur.execute(
                "SELECT COUNT(*) AS count FROM face WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchone()["count"]
        )
        if source_cluster_face_count == len(selected_cluster_face_ids):
            cur.execute(
                "UPDATE cluster SET person_id = NULL WHERE id = ?",
                (cluster_id,),
            )
            continue
        _create_cluster_for_faces(cur, selected_cluster_face_ids)

    if unclustered_face_ids:
        _create_cluster_for_faces(cur, unclustered_face_ids)

    cur.execute(
        f"""
        UPDATE face
        SET review_status = ?
        WHERE id IN ({placeholders})
        """,
        (normalized_status, *face_ids),
    )


def _recluster_active_faces_into_inbox(
    cur,
    face_ids: list[int],
    excluded_cluster_ids: set[int] | None = None,
    progress_callback=None,
) -> int:
    """Cluster active faces only against the inbox and unassigned clusters.

    Faces already assigned to a person-owned cluster are intentionally excluded
    from the candidate pool so manual removals do not snap back into protected
    person clusters.
    """
    if not face_ids:
        return 0

    excluded_cluster_ids = excluded_cluster_ids or set()
    normalized_face_ids = sorted({int(face_id) for face_id in face_ids})
    target_placeholders = ",".join("?" for _ in normalized_face_ids)
    target_rows = cur.execute(
        f"""
        SELECT id, embedding
        FROM face
        WHERE id IN ({target_placeholders})
          AND review_status = ?
          AND cluster_id IS NULL
        ORDER BY id ASC
        """,
        (*normalized_face_ids, FACE_REVIEW_STATUS_ACTIVE),
    ).fetchall()
    if not target_rows:
        return 0

    candidate_params: list[object] = [FACE_REVIEW_STATUS_ACTIVE]
    candidate_exclusions = ""
    if excluded_cluster_ids:
        exclusion_placeholders = ",".join("?" for _ in excluded_cluster_ids)
        candidate_exclusions = f" AND f.cluster_id NOT IN ({exclusion_placeholders})"
        candidate_params.extend(sorted(excluded_cluster_ids))

    candidate_rows = cur.execute(
        f"""
        SELECT f.embedding, f.cluster_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.review_status = ?
          AND f.cluster_id IS NOT NULL
          AND c.person_id IS NULL
          {candidate_exclusions}
        """,
        candidate_params,
    ).fetchall()

    existing_embeddings = [
        np.frombuffer(row["embedding"], dtype=np.float32)
        for row in candidate_rows
        if row["embedding"] is not None
    ]
    existing_cluster_ids = [
        int(row["cluster_id"])
        for row in candidate_rows
        if row["embedding"] is not None
    ]

    clusterer = FaceClustering()
    if existing_embeddings:
        clusterer.load_existing(
            np.vstack(existing_embeddings),
            np.array(existing_cluster_ids, dtype=int),
        )

    profile = get_clustering_profile()
    threshold = profile["neighbor_threshold"]
    target_face_ids: list[int] = []
    target_embeddings: list[np.ndarray] = []
    target_logical_cluster_ids: list[int] = []
    faces_without_embeddings: list[int] = []

    total_targets = len(target_rows)
    for index, row in enumerate(target_rows, start=1):
        face_id = int(row["id"])
        embedding_blob = row["embedding"]
        if embedding_blob is None:
            faces_without_embeddings.append(face_id)
            if progress_callback is not None:
                progress_callback(index, total_targets)
            continue

        embedding = np.frombuffer(embedding_blob, dtype=np.float32)
        logical_cluster_ids, _ = clusterer.add_and_assign(
            np.expand_dims(embedding, axis=0),
            distance_threshold=threshold,
        )
        target_face_ids.append(face_id)
        target_embeddings.append(embedding)
        target_logical_cluster_ids.append(int(logical_cluster_ids[0]))
        if progress_callback is not None:
            progress_callback(index, total_targets)

    if target_embeddings:
        combined_embeddings = existing_embeddings + target_embeddings
        combined_cluster_ids = np.asarray(
            existing_cluster_ids + target_logical_cluster_ids,
            dtype=int,
        )
        movable_mask = np.zeros(len(combined_embeddings), dtype=bool)
        movable_mask[len(existing_embeddings):] = True
        consolidated_ids = consolidate_small_clusters(
            np.vstack(combined_embeddings),
            combined_cluster_ids,
            threshold,
            movable_mask=movable_mask,
        )
        consolidated_ids = split_heterogeneous_clusters(
            np.vstack(combined_embeddings),
            consolidated_ids,
            profile["cohesion_threshold"],
            movable_mask=movable_mask,
            outlier_threshold=profile["outlier_threshold"],
        )
        target_logical_cluster_ids = [
            int(value) for value in consolidated_ids[len(existing_embeddings):]
        ]

    resolved_cluster_ids: dict[int, int] = {}
    grouped_face_ids: defaultdict[int, list[int]] = defaultdict(list)
    existing_cluster_id_set = set(existing_cluster_ids)
    for face_id, logical_cluster_id in zip(
        target_face_ids,
        target_logical_cluster_ids,
    ):
        if logical_cluster_id in resolved_cluster_ids:
            cluster_id = resolved_cluster_ids[logical_cluster_id]
        elif logical_cluster_id in existing_cluster_id_set:
            cluster_id = logical_cluster_id
            resolved_cluster_ids[logical_cluster_id] = cluster_id
        else:
            cur.execute(
                "INSERT INTO cluster(label, person_id) VALUES (?, ?)",
                (None, None),
            )
            cluster_id = int(cur.lastrowid)
            resolved_cluster_ids[logical_cluster_id] = cluster_id

        grouped_face_ids[cluster_id].append(face_id)

    for face_id in faces_without_embeddings:
        cluster_id = _create_cluster_for_faces(cur, [face_id])
        grouped_face_ids[cluster_id].append(face_id)

    # Background pass: only claim faces the user has not taken meanwhile.
    for cluster_id, grouped_ids in grouped_face_ids.items():
        _assign_faces_to_cluster(cur, grouped_ids, cluster_id, only_unclaimed=True)

    return len(target_rows)


def normalize_folder_path(folder_path: str):
    """Trim and normalize a folder path.

    Args:
        folder_path: User-provided folder path.

    Returns:
        Platform-normalized folder path.
    """
    return os.path.normpath(folder_path.strip())


def _descendant_filter(folders):
    """Build SQL conditions selecting folders and descendants.

    Args:
        folders: Folder paths to include.

    Returns:
        SQL condition fragments and bound parameters.
    """
    conditions = []
    params = []
    for folder in dict.fromkeys(normalize_folder_path(path) for path in folders if path):
        escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            "(location.directory = ? OR location.directory LIKE ? ESCAPE '\\')"
        )
        params.extend((folder, f"{escaped}{os.sep}%"))
    return conditions, params


def invalidate_query_cache_tags(*tags: str) -> None:
    """Clear cached query groups by their logical data tags."""
    app_cache.invalidate_tags(*tags)


def invalidate_image_query_cache():
    """Clear cached image query results after image-related writes."""
    invalidate_query_cache_tags(
        QUERY_CACHE_TAG_IMAGES,
        QUERY_CACHE_TAG_FOLDERS,
        QUERY_CACHE_TAG_PERSONS,
        QUERY_CACHE_TAG_RENAMES,
        QUERY_CACHE_TAG_CLUSTERS,
        QUERY_CACHE_TAG_PERSON_FACES,
        QUERY_CACHE_TAG_CLUSTER_FACES,
    )


def _normalize_person_filters(persons):
    """Normalize person names for deterministic SQL filtering."""
    normalized = []
    seen = set()
    for person in persons or []:
        trimmed = (person or "").strip()
        if not trimmed:
            continue
        key = _normalize_person_name_key(trimmed)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(trimmed)
    return normalized


def get_image_detail_rows(image_id: int):
    """Load one image with its active faces, shaped like the image page rows.

    Lets the review workspace open the full picture behind a face crop without
    paging through the library. The preferred location is picked the same way
    as in the paged query, so both views agree on the shown path.
    """
    conn = get_conn()
    try:
        return conn.execute(
            """
            WITH ranked_locations AS (
                SELECT
                    location.image_id,
                    location.path,
                    location.directory,
                    location.filename,
                    location.created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY location.image_id ORDER BY location.path
                    ) AS location_rank
                FROM image_location location
                WHERE location.image_id = ?
            )
            SELECT
                i.id AS image_id,
                location.path AS image_path,
                location.directory AS directory,
                location.filename AS filename,
                location.created_at AS created_at,
                i.content_hash,
                (
                    SELECT COUNT(*)
                    FROM image_location all_locations
                    WHERE all_locations.image_id = i.id
                ) AS location_count,
                f.id AS face_id,
                f.bbox_x,
                f.bbox_y,
                f.bbox_w,
                f.bbox_h,
                f.cluster_id,
                p.name AS person_name,
                f.review_status
            FROM image i
            JOIN ranked_locations location
              ON location.image_id = i.id AND location.location_rank = 1
            JOIN face f ON f.image_id = i.id
            LEFT JOIN cluster c ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            WHERE i.id = ? AND f.review_status = ?
            ORDER BY f.id
            """,
            (int(image_id), int(image_id), FACE_REVIEW_STATUS_ACTIVE),
        ).fetchall()
    finally:
        conn.close()


def normalize_face_status_filters(face_statuses):
    """Keep only the archived review statuses a caller may filter on.

    Tolerates non-sequence input because the API functions are also called
    directly in tests, where an unfilled query default is not a list.
    """
    if not isinstance(face_statuses, (list, tuple, set, frozenset)):
        return []
    allowed = {FACE_REVIEW_STATUS_UNKNOWN_PERSON, FACE_REVIEW_STATUS_NOT_FACE}
    return sorted({str(status) for status in face_statuses} & allowed)


def _matching_images_cte(folders=None, persons=None, face_statuses=None):
    """Build the common CTE used for image pagination queries."""
    folders = folders or []
    persons = _normalize_person_filters(persons)
    face_statuses = normalize_face_status_filters(face_statuses)
    conditions, params = _descendant_filter(folders)
    location_where = f"WHERE {' OR '.join(conditions)}" if conditions else ""

    # Archived faces are hidden by default. Filtering for them has to widen the
    # relevance test as well, otherwise an image whose faces were *all* archived
    # would drop out before the filter could ever match it.
    visible_statuses = [FACE_REVIEW_STATUS_ACTIVE, *face_statuses]
    visible_placeholders = ",".join("?" for _ in visible_statuses)

    person_clause = ""
    person_params = []
    if persons:
        placeholders = ",".join("?" for _ in persons)
        person_clause = f"""
        AND i.id IN (
            SELECT f.image_id
            FROM face f
            LEFT JOIN cluster c ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            WHERE f.review_status = ?
              AND LOWER(TRIM(COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}'))) IN ({placeholders})
            GROUP BY f.image_id
            HAVING COUNT(DISTINCT LOWER(TRIM(COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}')))) = ?
        )
        """
        person_params = [
            FACE_REVIEW_STATUS_ACTIVE,
            *[_normalize_person_name_key(person) for person in persons],
            len(persons),
        ]

    status_clause = ""
    status_params = []
    if face_statuses:
        status_placeholders = ",".join("?" for _ in face_statuses)
        status_clause = f"""
        AND i.id IN (
            SELECT f.image_id
            FROM face f
            WHERE f.review_status IN ({status_placeholders})
        )
        """
        status_params = list(face_statuses)

    sql = f"""
    WITH ranked_locations AS (
        SELECT
            location.image_id,
            location.path,
            location.directory,
            location.filename,
            location.created_at,
            ROW_NUMBER() OVER (
                PARTITION BY location.image_id ORDER BY location.path
            ) AS location_rank
        FROM image_location location
        {location_where}
    ),
    matching_images AS (
        SELECT
            i.id AS image_id,
            location.path AS image_path,
            location.directory,
            location.filename,
            location.created_at
        FROM image i
        JOIN ranked_locations location
            ON location.image_id = i.id AND location.location_rank = 1
        WHERE EXISTS (
            SELECT 1
            FROM face f
            WHERE f.image_id = i.id AND f.review_status IN ({visible_placeholders})
        )
        {person_clause}
        {status_clause}
    )
    """
    return sql, [*params, *visible_statuses, *person_params, *status_params]


def _image_order_by(prefix: str, sort_by: str, sort_direction: str):
    """Return the ORDER BY clause for image pagination."""
    direction = "ASC" if sort_direction == "asc" else "DESC"
    if sort_by == "folder":
        return (
            f"{prefix}directory COLLATE NOCASE {direction}, "
            f"{prefix}filename COLLATE NOCASE ASC, "
            f"{prefix}image_path COLLATE NOCASE ASC"
        )
    return (
        f"{prefix}created_at IS NULL ASC, "
        f"{prefix}created_at {direction}, "
        f"{prefix}filename COLLATE NOCASE ASC, "
        f"{prefix}image_path COLLATE NOCASE ASC"
    )


def list_available_image_persons(folders=None):
    """List person names available within the current folder selection."""
    folders = folders or []
    cache_key = ("available_persons", tuple(folders))

    def load_people():
        cte, params = _matching_images_cte(folders=folders, persons=[])
        conn = get_conn()
        rows = conn.execute(
            f"""
            {cte}
            SELECT DISTINCT COALESCE(p.name, '{UNKNOWN_PERSON_LABEL}') AS person_name
            FROM matching_images
            JOIN face f ON f.image_id = matching_images.image_id
            LEFT JOIN cluster c ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            WHERE f.review_status = ?
            ORDER BY person_name COLLATE NOCASE
            """,
            [*params, FACE_REVIEW_STATUS_ACTIVE],
        ).fetchall()
        conn.close()
        return [row["person_name"] for row in rows]

    return app_cache.get_or_set(
        cache_key,
        load_people,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
        },
    )


def list_images_page(
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
    limit: int = 40,
    offset: int = 0,
    face_statuses=None,
):
    """List one page of images with clustered faces and preferred locations."""
    folders = folders or []
    persons = _normalize_person_filters(persons)
    face_statuses = normalize_face_status_filters(face_statuses)
    # Faces of a filtered-for status are shown too, so the user can see exactly
    # what made the image match.
    visible_statuses = [FACE_REVIEW_STATUS_ACTIVE, *face_statuses]
    sort_by = "folder" if sort_by == "folder" else "date"
    sort_direction = "asc" if sort_direction == "asc" else "desc"
    limit = max(1, min(int(limit), MAX_IMAGE_PAGE_SIZE))
    offset = max(0, int(offset))

    cache_key = (
        "image_page",
        tuple(folders),
        tuple(persons),
        tuple(face_statuses),
        sort_by,
        sort_direction,
        limit,
        offset,
    )

    def load_page():
        cte, params = _matching_images_cte(
            folders=folders, persons=persons, face_statuses=face_statuses
        )
        matching_order = _image_order_by("matching_images.", sort_by, sort_direction)
        paged_order = _image_order_by("paged_images.", sort_by, sort_direction)
        visible_placeholders = ",".join("?" for _ in visible_statuses)

        conn = get_conn()
        total = conn.execute(
            f"""
            {cte}
            SELECT COUNT(*) AS total
            FROM matching_images
            """,
            params,
        ).fetchone()["total"]
        rows = conn.execute(
            f"""
            {cte}
            , paged_images AS (
                SELECT *
                FROM matching_images
                ORDER BY {matching_order}
                LIMIT ? OFFSET ?
            )
            SELECT
                paged_images.image_id,
                paged_images.image_path,
                paged_images.directory,
                paged_images.filename,
                paged_images.created_at,
                i.content_hash,
                (
                    SELECT COUNT(*)
                    FROM image_location all_locations
                    WHERE all_locations.image_id = paged_images.image_id
                ) AS location_count,
                f.id AS face_id,
                f.bbox_x,
                f.bbox_y,
                f.bbox_w,
                f.bbox_h,
                f.cluster_id,
                p.name AS person_name,
                f.review_status
            FROM paged_images
            JOIN image i ON i.id = paged_images.image_id
            JOIN face f ON f.image_id = paged_images.image_id
            LEFT JOIN cluster c ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            WHERE f.review_status IN ({visible_placeholders})
            ORDER BY {paged_order}, f.id
            """,
            [*params, limit, offset, *visible_statuses],
        ).fetchall()
        conn.close()
        return rows, total

    return app_cache.get_or_set(
        cache_key,
        load_page,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
        },
    )


def list_images(folders=None):
    """List images with clustered faces and preferred locations.

    Args:
        folders: Optional folder roots used to filter results.

    Returns:
        SQLite rows containing image, face, cluster, and person data.
    """
    folders = folders or []
    conditions, params = _descendant_filter(folders)
    where = ["f.review_status = ?"]
    location_where = f"WHERE {' OR '.join(conditions)}" if conditions else ""

    conn = get_conn()
    rows = conn.execute(
        f"""
        WITH ranked_locations AS (
            SELECT
                location.image_id,
                location.path,
                location.directory,
                location.filename,
                ROW_NUMBER() OVER (
                    PARTITION BY location.image_id ORDER BY location.path
                ) AS location_rank
            FROM image_location location
            {location_where}
        )
        SELECT
            i.id AS image_id,
            location.path AS image_path,
            location.directory,
            location.filename,
            i.content_hash,
            (
                SELECT COUNT(*) FROM image_location all_locations
                WHERE all_locations.image_id = i.id
            ) AS location_count,
            f.id AS face_id,
            f.bbox_x,
            f.bbox_y,
            f.bbox_w,
            f.bbox_h,
            f.cluster_id,
            p.name AS person_name,
            f.review_status
        FROM image i
        JOIN ranked_locations location
            ON location.image_id = i.id AND location.location_rank = 1
        JOIN face f ON f.image_id = i.id
        LEFT JOIN cluster c ON f.cluster_id = c.id
        LEFT JOIN person p ON c.person_id = p.id
        WHERE {' AND '.join(where)}
        ORDER BY location.path, f.id
        """,
        [*params, FACE_REVIEW_STATUS_ACTIVE],
    ).fetchall()
    conn.close()
    return rows


def list_image_locations(image_ids):
    """List every assigned location for canonical images.

    Args:
        image_ids: Canonical image identifiers to load.

    Returns:
        Mapping from image ID to ordered location dictionaries.
    """
    unique_ids = list(dict.fromkeys(image_ids))
    if not unique_ids:
        return {}

    placeholders = ",".join("?" for _ in unique_ids)
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT image_id, path, directory, filename
        FROM image_location
        WHERE image_id IN ({placeholders})
        ORDER BY image_id, path
        """,
        unique_ids,
    ).fetchall()
    conn.close()

    locations = defaultdict(list)
    for row in rows:
        locations[row["image_id"]].append(
            {
                "path": row["path"],
                "directory": row["directory"],
                "filename": row["filename"],
            }
        )
    return dict(locations)


def build_folder_tree():
    """Build the imported folder hierarchy with unique image counts.

    Returns:
        Tree roots and aggregate image and folder counts.
    """
    def load_tree():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT location.image_id, location.directory
            FROM image_location location
            JOIN image i ON i.id = location.image_id
            WHERE i.processed_at IS NOT NULL
            ORDER BY location.directory
            """
        ).fetchall()
        conn.close()

        direct_images = defaultdict(set)
        all_image_ids = set()
        for row in rows:
            direct_images[row["directory"]].add(row["image_id"])
            all_image_ids.add(row["image_id"])

        nodes = {}

        def ensure_node(path):
            """Create a folder node and any missing ancestors."""
            if path in nodes:
                return nodes[path]
            parent = os.path.dirname(path) if path != os.path.dirname(path) else None
            node = {
                "path": path,
                "name": os.path.basename(path) or path,
                "direct_image_count": len(direct_images.get(path, set())),
                "image_count": 0,
                "children": [],
                "_parent": parent,
            }
            nodes[path] = node
            if parent is not None:
                ensure_node(parent)
            return node

        for directory in direct_images:
            ensure_node(directory)

        totals = defaultdict(set)
        for directory, image_ids in direct_images.items():
            current = directory
            while True:
                totals[current].update(image_ids)
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent

        roots = []
        for path, node in nodes.items():
            node["image_count"] = len(totals[path])
            parent = node.pop("_parent")
            if parent is None or parent not in nodes:
                roots.append(node)
            else:
                nodes[parent]["children"].append(node)

        def sort_nodes(items):
            """Sort folder nodes recursively by display name."""
            items.sort(key=lambda item: item["name"].casefold())
            for item in items:
                sort_nodes(item["children"])

        sort_nodes(roots)
        return {
            "roots": roots,
            "image_count": len(all_image_ids),
            "folder_count": len(nodes),
        }

    return app_cache.get_or_set(
        ("folder_tree",),
        load_tree,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_FOLDERS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def get_available_image_path(
    image_id: int,
    preferred_path=None,
    *,
    require_preferred: bool = False,
):
    """Find an existing filesystem location for an image.

    Args:
        image_id: Canonical image identifier.
        preferred_path: Optional location to try first.
        require_preferred: Do not silently fall back when a specific UI path
            was selected by the user.

    Returns:
        Existing path, or ``None`` when all locations are unavailable.
    """
    conn = get_conn()
    if require_preferred and preferred_path:
        rows = conn.execute(
            "SELECT path FROM image_location WHERE image_id = ? AND path = ?",
            (image_id, preferred_path),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT path
            FROM image_location
            WHERE image_id = ?
            ORDER BY CASE WHEN path = ? THEN 0 ELSE 1 END, path
            """,
            (image_id, preferred_path),
        ).fetchall()
    conn.close()
    return next((row["path"] for row in rows if os.path.isfile(row["path"])), None)


def delete_image(image_id: int):
    """Delete an image and clean up empty clusters.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Whether an image row was deleted.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM image WHERE id = ?", (image_id,))
    deleted = cur.rowcount > 0
    if deleted:
        cur.execute(
            """
            DELETE FROM cluster
            WHERE NOT EXISTS (
                SELECT 1 FROM face WHERE face.cluster_id = cluster.id
            )
            """
        )
        conn.commit()
    conn.close()
    if deleted:
        invalidate_image_query_cache()
    return deleted


def list_faces_by_folder(folder_path: str):
    """List clustered faces below a folder.

    Args:
        folder_path: Folder root used to filter images.

    Returns:
        Face dictionaries for matching images.
    """
    rows = list_images([folder_path])
    return [
        _face_row_to_dict(
            {
                "id": row["face_id"],
                "image_id": row["image_id"],
                "image_path": row["image_path"],
                "bbox_x": row["bbox_x"],
                "bbox_y": row["bbox_y"],
                "bbox_w": row["bbox_w"],
                "bbox_h": row["bbox_h"],
                "cluster_id": row["cluster_id"],
                "person_name": row["person_name"],
                "review_status": FACE_REVIEW_STATUS_ACTIVE,
            }
        )
        for row in rows
    ]


def list_cluster_summaries():
    """List compact cluster summaries for the cluster sidebar."""
    def load_clusters():
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT
                c.id AS cluster_id,
                c.label AS cluster_label,
                p.name AS person_name,
                COUNT(f.id) AS face_count
            FROM cluster c
            JOIN face f ON f.cluster_id = c.id
            LEFT JOIN person p ON c.person_id = p.id
            WHERE f.review_status = ?
            GROUP BY c.id, p.name
            ORDER BY
                face_count DESC,
                CASE WHEN COALESCE(TRIM(p.name), '') = '' THEN 1 ELSE 0 END ASC,
                COALESCE(p.name, ?) COLLATE NOCASE ASC,
                c.id ASC
            """,
            (FACE_REVIEW_STATUS_ACTIVE, UNKNOWN_PERSON_LABEL),
        ).fetchall()
        conn.close()
        return [
            {
                "cluster_id": r["cluster_id"],
                "cluster_label": r["cluster_label"],
                "person_name": r["person_name"],
                "face_count": int(r["face_count"]),
            }
            for r in rows
        ]

    return app_cache.get_or_set(
        ("cluster_summaries",),
        load_clusters,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def get_cluster_overview():
    """Bundle the cluster-page bootstrap data into a single response.

    Returns the sidebar summaries, review-queue counts, and the faces of the
    first (largest) cluster in one payload so the initial page load needs only
    one round trip instead of fetching the list and then the first cluster's
    faces sequentially. Each part reuses its own query cache.
    """
    clusters = list_cluster_summaries()
    review_groups = list_face_review_groups()
    first_cluster = None
    if clusters:
        first_cluster_id = clusters[0]["cluster_id"]
        summary = get_cluster_summary(first_cluster_id)
        if summary is not None:
            first_cluster = {**summary, "faces": get_cluster_faces(first_cluster_id)}
    return {
        "clusters": clusters,
        "review_groups": review_groups,
        "first_cluster": first_cluster,
    }


def get_cluster_summary(cluster_id: int):
    """Load compact metadata for one cluster."""
    return app_cache.get_or_set(
        ("cluster_summary", int(cluster_id)),
        lambda: _load_cluster_summary(cluster_id),
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def _load_cluster_summary(cluster_id: int):
    """Query compact metadata for one cluster from the database."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT
            c.id AS cluster_id,
            c.label AS cluster_label,
            p.name AS person_name,
            COUNT(f.id) AS face_count
        FROM cluster c
        JOIN face f ON f.cluster_id = c.id
        LEFT JOIN person p ON c.person_id = p.id
        WHERE c.id = ?
          AND f.review_status = ?
        GROUP BY c.id, p.name
        """,
        (cluster_id, FACE_REVIEW_STATUS_ACTIVE),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "cluster_id": row["cluster_id"],
        "cluster_label": row["cluster_label"],
        "person_name": row["person_name"],
        "face_count": int(row["face_count"]),
    }


def get_cluster_faces(cluster_id: int):
    """List faces assigned to a cluster.

    Args:
        cluster_id: Cluster identifier.

    Returns:
        Face dictionaries belonging to the cluster.
    """
    return app_cache.get_or_set(
        ("cluster_faces", int(cluster_id)),
        lambda: _load_cluster_faces(cluster_id),
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_CLUSTER_FACES,
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def _load_cluster_faces(cluster_id: int):
    """Load faces for one cluster from the database."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            f.id, f.image_id,
            f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.cluster_id,
            p.name AS person_name, f.review_status, f.embedding
        FROM face f
        LEFT JOIN cluster c ON c.id = f.cluster_id
        LEFT JOIN person p ON p.id = c.person_id
        WHERE f.cluster_id = ?
          AND f.review_status = ?
        """,
        (cluster_id, FACE_REVIEW_STATUS_ACTIVE),
    ).fetchall()
    conn.close()
    valid_rows = []
    valid_embeddings = []
    invalid_rows = []
    for row in rows:
        embedding_blob = row["embedding"]
        if embedding_blob is None:
            invalid_rows.append(row)
            continue
        embedding = np.frombuffer(embedding_blob, dtype=np.float32)
        if embedding.size != 512:
            invalid_rows.append(row)
            continue
        valid_rows.append(row)
        valid_embeddings.append(embedding)

    if len(valid_rows) > 1:
        similarity_order = order_embeddings_by_similarity(
            np.vstack(valid_embeddings),
            np.asarray([int(row["id"]) for row in valid_rows], dtype=np.int64),
        )
        ordered_rows = [valid_rows[int(index)] for index in similarity_order]
    else:
        ordered_rows = valid_rows
    ordered_rows.extend(sorted(invalid_rows, key=lambda row: int(row["id"])))
    return [_face_row_to_dict(row) for row in ordered_rows]


def load_all_embeddings() -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load persisted embeddings with cluster and optional person identifiers.

    Returns:
        Embedding matrix, aligned cluster IDs, and person IDs. Unassigned
        clusters use ``-1`` as their person identifier.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT f.embedding, f.cluster_id, c.person_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.embedding IS NOT NULL
          AND f.cluster_id IS NOT NULL
          AND f.review_status = ?
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    conn.close()

    if not rows:
        return (
            np.empty((0, 512), dtype=np.float32),
            np.empty((0,), dtype=int),
            np.empty((0,), dtype=int),
        )

    embs: List[np.ndarray] = []
    cids: List[int] = []
    pids: List[int] = []
    for r in rows:
        if r["embedding"] is None:
            continue
        embs.append(np.frombuffer(r["embedding"], dtype=np.float32))
        cids.append(int(r["cluster_id"]))
        pids.append(int(r["person_id"]) if r["person_id"] is not None else -1)

    return (
        np.vstack(embs),
        np.array(cids, dtype=int),
        np.array(pids, dtype=int),
    )


def refresh_person_suggestions(cur=None) -> int:
    """Rebuild conservative person proposals from confirmed assignments.

    Confirmed person clusters are treated as trusted reference modes. Unknown
    clusters remain unknown: this function only records a proposal when several
    faces independently agree on the same person with a clear runner-up margin.
    """
    owns_connection = cur is None
    conn = get_conn() if owns_connection else None
    cursor = conn.cursor() if conn is not None else cur

    suggestion_table = cursor.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'cluster_person_suggestion'
        """
    ).fetchone()
    if suggestion_table is None:
        if owns_connection:
            conn.close()
        return 0

    assigned_rows = cursor.execute(
        """
        SELECT f.embedding, f.cluster_id, c.person_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.review_status = ?
          AND f.embedding IS NOT NULL
          AND c.person_id IS NOT NULL
        ORDER BY c.person_id, f.cluster_id, f.id
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    unknown_rows = cursor.execute(
        """
        SELECT f.id, f.embedding, f.cluster_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.review_status = ?
          AND f.embedding IS NOT NULL
          AND c.person_id IS NULL
        ORDER BY f.cluster_id, f.id
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()

    # Pending proposals are derived data. Explicit dismissals survive ordinary
    # refreshes for as long as that exact cluster continues to exist.
    cursor.execute(
        """
        DELETE FROM cluster_person_suggestion
        WHERE status = 'pending'
           OR cluster_id IN (SELECT id FROM cluster WHERE person_id IS NOT NULL)
        """
    )
    if not assigned_rows or not unknown_rows:
        if owns_connection:
            conn.commit()
            conn.close()
        return 0

    by_person_cluster: defaultdict[tuple[int, int], list[np.ndarray]] = defaultdict(list)
    for row in assigned_rows:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        if embedding.size == 512:
            by_person_cluster[(int(row["person_id"]), int(row["cluster_id"]))].append(
                embedding
            )

    person_prototypes: defaultdict[int, list[np.ndarray]] = defaultdict(list)
    for (person_id, _cluster_id), embeddings in by_person_cluster.items():
        values = np.vstack(embeddings).astype(np.float32)
        values /= np.linalg.norm(values, axis=1, keepdims=True) + 1e-12
        person_prototypes[person_id].append(
            FaceClustering._select_prototypes(values, max_prototypes=3)
        )
    prototypes = {
        person_id: np.vstack(groups)
        for person_id, groups in person_prototypes.items()
        if groups
    }
    person_ids = sorted(prototypes)
    if not person_ids:
        if owns_connection:
            conn.commit()
            conn.close()
        return 0

    by_unknown_cluster: defaultdict[int, list[tuple[int, np.ndarray]]] = defaultdict(list)
    for row in unknown_rows:
        embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        if embedding.size == 512:
            by_unknown_cluster[int(row["cluster_id"])].append(
                (int(row["id"]), embedding)
            )

    profile = get_clustering_profile()
    anchor_limit = profile["person_anchor_threshold"]
    required_margin = profile["ambiguity_margin"]
    inserted = 0

    def store_suggestion(
        target_cluster_id: int,
        winner_index: int,
        winner_mask: np.ndarray,
        best_distances: np.ndarray,
        margins: np.ndarray,
        target_face_count: int,
    ) -> bool:
        support_count = int(np.sum(winner_mask))
        if support_count < 3:
            return False
        support_ratio = support_count / target_face_count
        supported_distances = best_distances[winner_mask]
        supported_margins = margins[winner_mask]
        best_distance = float(np.median(supported_distances))
        runner_up_margin = float(np.median(supported_margins))
        distance_quality = float(
            np.clip((anchor_limit - best_distance) / max(0.12, anchor_limit - 0.18), 0, 1)
        )
        margin_quality = float(np.clip(runner_up_margin / 0.16, 0, 1))
        confidence = float(
            np.clip(
                0.40 * support_ratio
                + 0.25 * distance_quality
                + 0.35 * margin_quality,
                0,
                1,
            )
        )
        dismissed = cursor.execute(
            """
            SELECT 1 FROM cluster_person_suggestion
            WHERE cluster_id = ? AND status = 'dismissed'
            """,
            (target_cluster_id,),
        ).fetchone()
        if dismissed:
            return False
        cursor.execute(
            """
            INSERT INTO cluster_person_suggestion(
                cluster_id, person_id, confidence, best_distance,
                runner_up_margin, support_count, face_count, support_ratio,
                status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
            """,
            (
                target_cluster_id,
                person_ids[winner_index],
                confidence,
                best_distance,
                runner_up_margin,
                support_count,
                target_face_count,
                support_ratio,
            ),
        )
        return True

    for cluster_id, face_rows in by_unknown_cluster.items():
        values = np.vstack([embedding for _, embedding in face_rows]).astype(np.float32)
        values /= np.linalg.norm(values, axis=1, keepdims=True) + 1e-12
        distance_columns = [
            np.min(1.0 - values @ prototypes[person_id].T, axis=1)
            for person_id in person_ids
        ]
        distances = np.column_stack(distance_columns)
        order = np.argsort(distances, axis=1)
        best_indexes = order[:, 0]
        best_distances = distances[np.arange(values.shape[0]), best_indexes]
        if len(person_ids) > 1:
            runner_distances = distances[np.arange(values.shape[0]), order[:, 1]]
            margins = runner_distances - best_distances
            anchor_mask = (best_distances <= anchor_limit) & (margins >= required_margin)
        else:
            # The first confirmed person is already useful training data. With
            # no runner-up identity available, compensate by demanding a much
            # closer match; the cluster-consensus check below still applies.
            single_person_limit = min(anchor_limit, 0.34)
            margins = np.full(values.shape[0], 0.12, dtype=np.float32)
            anchor_mask = best_distances <= single_person_limit
        if not np.any(anchor_mask):
            continue

        winning_indexes = best_indexes[anchor_mask]
        winner_counts = np.bincount(winning_indexes, minlength=len(person_ids))
        winner_index = int(np.argmax(winner_counts))
        winner_mask = anchor_mask & (best_indexes == winner_index)
        support_count = int(np.sum(winner_mask))
        face_count = int(values.shape[0])
        support_ratio = support_count / face_count
        # Assigning a proposal confirms the entire cluster, so whole-cluster
        # proposals require near-unanimous evidence. Otherwise only the strong
        # anchors are peeled out and uncertain faces remain in the inbox.
        minimum_ratio = profile["cluster_support_ratio"]
        qualified_winners = [
            index for index, count in enumerate(winner_counts) if int(count) >= 3
        ]

        # A mixed cluster must never become a person proposal as a whole. Peel
        # only the independently high-confidence anchors into dedicated,
        # reviewable proposal clusters. Uncertain bridge faces stay untouched.
        should_peel = (
            len(qualified_winners) > 1
            or (support_count >= 3 and support_ratio < minimum_ratio)
        )
        if should_peel:
            for qualified_index in qualified_winners:
                qualified_mask = anchor_mask & (best_indexes == qualified_index)
                qualified_face_ids = [
                    face_rows[index][0]
                    for index in np.flatnonzero(qualified_mask)
                ]
                if len(qualified_face_ids) < 3:
                    continue
                proposal_cluster_id = _create_cluster_for_faces(
                    cursor,
                    qualified_face_ids,
                )
                if store_suggestion(
                    proposal_cluster_id,
                    qualified_index,
                    qualified_mask,
                    best_distances,
                    margins,
                    len(qualified_face_ids),
                ):
                    inserted += 1
            continue

        if support_count >= 3 and support_ratio >= minimum_ratio:
            if store_suggestion(
                cluster_id,
                winner_index,
                winner_mask,
                best_distances,
                margins,
                face_count,
            ):
                inserted += 1

    _delete_empty_clusters(cursor)

    if owns_connection:
        conn.commit()
        conn.close()
        invalidate_image_query_cache()
    return inserted


def refresh_review_suggestions(cur=None) -> int:
    """Suggest recurring unknown people and false detections conservatively.

    Faces explicitly classified by the user form a local reference library.
    New, still-unassigned clusters are only proposed when most faces agree
    closely with one reference category. Nothing is classified automatically.
    """
    owns_connection = cur is None
    conn = get_conn() if owns_connection else None
    cursor = conn.cursor() if conn is not None else cur
    table = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cluster_review_suggestion'"
    ).fetchone()
    if table is None:
        if owns_connection:
            conn.close()
        return 0

    cursor.execute(
        """
        DELETE FROM cluster_review_suggestion
        WHERE status = 'pending'
           OR cluster_id NOT IN (
               SELECT id FROM cluster WHERE person_id IS NULL
           )
        """
    )
    reference_rows = cursor.execute(
        """
        SELECT id, review_status, embedding
        FROM face
        WHERE review_status IN (?, ?)
          AND embedding IS NOT NULL
        ORDER BY id DESC
        LIMIT 6000
        """,
        (FACE_REVIEW_STATUS_UNKNOWN_PERSON, FACE_REVIEW_STATUS_NOT_FACE),
    ).fetchall()
    target_rows = cursor.execute(
        """
        SELECT f.id, f.cluster_id, f.embedding
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.review_status = ?
          AND c.person_id IS NULL
          AND f.embedding IS NOT NULL
        ORDER BY f.cluster_id, f.id
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    if not reference_rows or not target_rows:
        if owns_connection:
            conn.commit()
            conn.close()
        return 0

    references: defaultdict[str, list[np.ndarray]] = defaultdict(list)
    for row in reference_rows:
        value = np.frombuffer(row["embedding"], dtype=np.float32)
        if value.size == 512:
            references[str(row["review_status"])].append(value)
    reference_matrices: dict[str, np.ndarray] = {}
    for review_status, values in references.items():
        matrix = np.vstack(values[:3000]).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
        reference_matrices[review_status] = matrix

    targets: defaultdict[int, list[np.ndarray]] = defaultdict(list)
    for row in target_rows:
        value = np.frombuffer(row["embedding"], dtype=np.float32)
        if value.size == 512:
            targets[int(row["cluster_id"])].append(value)

    profile = get_clustering_profile()
    limits = {
        FACE_REVIEW_STATUS_UNKNOWN_PERSON: float(
            np.clip(profile["person_anchor_threshold"] * 0.9, 0.28, 0.42)
        ),
        FACE_REVIEW_STATUS_NOT_FACE: 0.30,
    }
    required_ratios = {
        FACE_REVIEW_STATUS_UNKNOWN_PERSON: 0.78,
        FACE_REVIEW_STATUS_NOT_FACE: 0.88,
    }

    def nearest_distances(values: np.ndarray, refs: np.ndarray) -> np.ndarray:
        best = np.full(values.shape[0], np.inf, dtype=np.float32)
        for start in range(0, refs.shape[0], 512):
            similarities = values @ refs[start : start + 512].T
            best = np.minimum(best, 1.0 - np.max(similarities, axis=1))
        return best

    inserted = 0
    for cluster_id, raw_values in targets.items():
        dismissed = cursor.execute(
            "SELECT 1 FROM cluster_review_suggestion WHERE cluster_id = ? AND status = 'dismissed'",
            (cluster_id,),
        ).fetchone()
        if dismissed:
            continue
        values = np.vstack(raw_values).astype(np.float32)
        values /= np.linalg.norm(values, axis=1, keepdims=True) + 1e-12
        candidates = []
        for review_status, refs in reference_matrices.items():
            limit = limits[review_status]
            distances = nearest_distances(values, refs)
            supported = distances <= limit
            support_count = int(np.sum(supported))
            support_ratio = support_count / len(values)
            if support_count == 0:
                continue
            median_distance = float(np.median(distances[supported]))
            singleton_is_exceptional = (
                len(values) == 1 and median_distance <= limit - 0.09
            )
            if not singleton_is_exceptional and (
                support_count < 2 or support_ratio < required_ratios[review_status]
            ):
                continue
            distance_quality = float(
                np.clip((limit - median_distance) / max(0.12, limit - 0.12), 0, 1)
            )
            confidence = float(
                np.clip(0.62 * support_ratio + 0.38 * distance_quality, 0, 1)
            )
            candidates.append(
                (confidence, review_status, median_distance, support_count, support_ratio)
            )
        if not candidates:
            continue
        candidates.sort(reverse=True)
        winner = candidates[0]
        if len(candidates) > 1 and winner[0] - candidates[1][0] < 0.06:
            continue
        confidence, review_status, best_distance, support_count, support_ratio = winner
        cursor.execute(
            """
            INSERT INTO cluster_review_suggestion(
                cluster_id, review_status, confidence, best_distance,
                support_count, face_count, support_ratio, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
            """,
            (
                cluster_id,
                review_status,
                confidence,
                best_distance,
                support_count,
                len(values),
                support_ratio,
            ),
        )
        inserted += 1

    if owns_connection:
        conn.commit()
        conn.close()
        invalidate_image_query_cache()
    return inserted


def list_review_suggestions() -> list[dict]:
    """Return review-status proposals with compact face previews."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT cluster_id, review_status, confidence, best_distance,
               support_count, face_count, support_ratio
        FROM cluster_review_suggestion
        WHERE status = 'pending'
        ORDER BY confidence DESC, face_count DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        previews = conn.execute(
            "SELECT id FROM face WHERE cluster_id = ? AND review_status = ? ORDER BY id LIMIT 6",
            (row["cluster_id"], FACE_REVIEW_STATUS_ACTIVE),
        ).fetchall()
        result.append(
            {
                "cluster_id": int(row["cluster_id"]),
                "review_status": row["review_status"],
                "confidence": float(row["confidence"]),
                "best_distance": float(row["best_distance"]),
                "support_count": int(row["support_count"]),
                "face_count": int(row["face_count"]),
                "support_ratio": float(row["support_ratio"]),
                "recommended": float(row["confidence"]) >= 0.80,
                "preview_face_ids": [int(item["id"]) for item in previews],
            }
        )
    conn.close()
    return result


def dismiss_review_suggestion(cluster_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cluster_review_suggestion
        SET status = 'dismissed', updated_at = CURRENT_TIMESTAMP
        WHERE cluster_id = ? AND status = 'pending'
        """,
        (int(cluster_id),),
    )
    if cur.rowcount == 0:
        conn.close()
        raise LookupError("Review suggestion not found")
    conn.commit()
    conn.close()


def accept_review_suggestions(cluster_ids: list[int]) -> int:
    normalized_ids = sorted({int(value) for value in cluster_ids if int(value) > 0})
    if not normalized_ids:
        raise ValueError("No cluster_ids supplied")
    placeholders = ",".join("?" for _ in normalized_ids)
    conn = get_conn()
    cur = conn.cursor()
    try:
        rows = cur.execute(
            f"""
            SELECT cluster_id, review_status
            FROM cluster_review_suggestion
            WHERE status = 'pending' AND cluster_id IN ({placeholders})
            """,
            normalized_ids,
        ).fetchall()
        if len(rows) != len(normalized_ids):
            raise ValueError("One or more review suggestions are stale")
        for row in rows:
            cur.execute(
                "UPDATE face SET cluster_id = NULL, review_status = ? WHERE cluster_id = ? AND review_status = ?",
                (row["review_status"], row["cluster_id"], FACE_REVIEW_STATUS_ACTIVE),
            )
        cur.execute(
            f"DELETE FROM cluster_review_suggestion WHERE cluster_id IN ({placeholders})",
            normalized_ids,
        )
        _delete_empty_clusters(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    invalidate_image_query_cache()
    return len(normalized_ids)


def list_person_suggestions() -> list[dict]:
    """Return pending proposals grouped by person in the frontend."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT s.cluster_id, s.person_id, p.name AS person_name,
               s.confidence, s.best_distance, s.runner_up_margin,
               s.support_count, s.face_count, s.support_ratio
        FROM cluster_person_suggestion s
        JOIN cluster c ON c.id = s.cluster_id AND c.person_id IS NULL
        JOIN person p ON p.id = s.person_id
        WHERE s.status = 'pending'
        ORDER BY p.name COLLATE NOCASE, s.confidence DESC, s.face_count DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        preview_rows = conn.execute(
            """
            SELECT id FROM face
            WHERE cluster_id = ? AND review_status = ?
            ORDER BY id LIMIT 6
            """,
            (row["cluster_id"], FACE_REVIEW_STATUS_ACTIVE),
        ).fetchall()
        result.append(
            {
                "cluster_id": int(row["cluster_id"]),
                "person_id": int(row["person_id"]),
                "person_name": row["person_name"],
                "confidence": float(row["confidence"]),
                "best_distance": float(row["best_distance"]),
                "runner_up_margin": float(row["runner_up_margin"]),
                "support_count": int(row["support_count"]),
                "face_count": int(row["face_count"]),
                "support_ratio": float(row["support_ratio"]),
                "recommended": float(row["confidence"]) >= 0.72,
                "preview_face_ids": [int(item["id"]) for item in preview_rows],
            }
        )
    conn.close()
    return result


def dismiss_person_suggestion(cluster_id: int) -> None:
    """Hide one proposal without changing any face assignment."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE cluster_person_suggestion
        SET status = 'dismissed', updated_at = CURRENT_TIMESTAMP
        WHERE cluster_id = ? AND status = 'pending'
        """,
        (int(cluster_id),),
    )
    if cur.rowcount == 0:
        conn.close()
        raise LookupError("Person suggestion not found")
    conn.commit()
    conn.close()
    invalidate_image_query_cache()


def accept_person_suggestions(person_id: int, cluster_ids: list[int]) -> int:
    """Confirm selected proposals atomically after validating their person."""
    return accept_person_suggestion_assignments(
        [{"person_id": person_id, "cluster_ids": cluster_ids}]
    )


def accept_person_suggestion_assignments(assignments: list[dict]) -> int:
    """Confirm proposal groups for several people in one atomic operation."""
    normalized_assignments: list[tuple[int, list[int]]] = []
    seen_cluster_ids: set[int] = set()
    for assignment in assignments or []:
        person_id = int(assignment.get("person_id"))
        cluster_ids = sorted(
            {int(value) for value in assignment.get("cluster_ids", []) if int(value) > 0}
        )
        if not cluster_ids:
            continue
        if seen_cluster_ids.intersection(cluster_ids):
            raise ValueError("A cluster may only be accepted once")
        seen_cluster_ids.update(cluster_ids)
        normalized_assignments.append((person_id, cluster_ids))
    if not normalized_assignments:
        raise ValueError("No cluster_ids supplied")

    conn = get_conn()
    cur = conn.cursor()
    try:
        for person_id, cluster_ids in normalized_assignments:
            placeholders = ",".join("?" for _ in cluster_ids)
            rows = cur.execute(
                f"""
                SELECT cluster_id FROM cluster_person_suggestion
                WHERE person_id = ? AND status = 'pending'
                  AND cluster_id IN ({placeholders})
                """,
                (person_id, *cluster_ids),
            ).fetchall()
            if len(rows) != len(cluster_ids):
                raise ValueError(
                    "One or more suggestions are stale or belong to another person"
                )
        for person_id, cluster_ids in normalized_assignments:
            placeholders = ",".join("?" for _ in cluster_ids)
            cur.execute(
                f"""
                UPDATE cluster SET person_id = ?
                WHERE id IN ({placeholders}) AND person_id IS NULL
                """,
                (person_id, *cluster_ids),
            )
            if int(cur.rowcount) != len(cluster_ids):
                raise ValueError("One or more suggestion clusters are no longer unassigned")
            cur.execute(
                f"DELETE FROM cluster_person_suggestion WHERE cluster_id IN ({placeholders})",
                cluster_ids,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    invalidate_image_query_cache()
    return len(seen_cluster_ids)


def assign_cluster_to_person(cluster_id: int, person_name: str):
    """Assign a cluster to an existing or newly created person.

    Args:
        cluster_id: Cluster identifier to update.
        person_name: Unique person display name.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cluster_row = cur.execute(
            """
            SELECT
                c.id,
                c.person_id,
                p.id AS resolved_person_id
            FROM cluster c
            LEFT JOIN person p ON p.id = c.person_id
            WHERE c.id = ?
            """,
            (cluster_id,),
        ).fetchone()
        if cluster_row is None:
            referenced_face = cur.execute(
                "SELECT 1 FROM face WHERE cluster_id = ? LIMIT 1",
                (cluster_id,),
            ).fetchone()
            if referenced_face is None:
                raise LookupError(f"Cluster {cluster_id} not found")
            cur.execute(
                "INSERT INTO cluster(id, label, person_id) VALUES (?, NULL, NULL)",
                (cluster_id,),
            )
            logger.warning(
                "Recreated missing cluster row %s during person assignment",
                cluster_id,
            )
            cluster_row = cur.execute(
                """
                SELECT
                    c.id,
                    c.person_id,
                    p.id AS resolved_person_id
                FROM cluster c
                LEFT JOIN person p ON p.id = c.person_id
                WHERE c.id = ?
                """,
                (cluster_id,),
            ).fetchone()

        referenced_face = cur.execute(
            "SELECT 1 FROM face WHERE cluster_id = ? LIMIT 1",
            (cluster_id,),
        ).fetchone()
        if referenced_face is None:
            raise LookupError(f"Cluster {cluster_id} has no faces")

        if (
            cluster_row["person_id"] is not None
            and cluster_row["resolved_person_id"] is None
        ):
            cur.execute(
                "UPDATE cluster SET person_id = NULL WHERE id = ?",
                (cluster_id,),
            )
            logger.warning(
                "Cleared broken person reference %s from cluster %s during assignment",
                cluster_row["person_id"],
                cluster_id,
            )

        person_id = _resolve_person_id(cur, person_name)
        previous_person_id = cluster_row["person_id"]

        cur.execute(
            "UPDATE cluster SET person_id = ? WHERE id = ?",
            (person_id, cluster_id),
        )
        if cur.rowcount == 0:
            raise LookupError(f"Cluster {cluster_id} not found")
        mark_persons_dirty_for_recluster(cur, [previous_person_id, person_id])
        conn.commit()
    finally:
        conn.close()
    invalidate_image_query_cache()


def rename_cluster(cluster_id: int, label: str):
    """Rename one cluster without changing its assignments."""
    normalized_label = (label or "").strip()
    if not normalized_label:
        raise ValueError("Missing cluster label")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE cluster SET label = ? WHERE id = ?",
        (normalized_label, cluster_id),
    )
    if cur.rowcount == 0:
        conn.close()
        raise LookupError(f"Cluster {cluster_id} not found")
    conn.commit()
    conn.close()
    invalidate_image_query_cache()


def rename_person(person_id: int, name: str):
    """Rename one person and keep all assigned clusters attached."""
    conn = get_conn()
    cur = conn.cursor()
    person_row = cur.execute(
        "SELECT id FROM person WHERE id = ?",
        (person_id,),
    ).fetchone()
    if person_row is None:
        conn.close()
        raise LookupError(f"Person {person_id} not found")

    canonical_person_id = _resolve_person_id(cur, name)
    if canonical_person_id != person_id:
        cur.execute(
            "UPDATE cluster SET person_id = ? WHERE person_id = ?",
            (canonical_person_id, person_id),
        )
        cur.execute("DELETE FROM person WHERE id = ?", (person_id,))
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return canonical_person_id


def delete_person(person_id: int, reassignment_group: str):
    """Delete one person and reclassify all assigned faces."""
    normalized_group = normalize_face_review_group(reassignment_group)
    conn = get_conn()
    cur = conn.cursor()
    person_row = cur.execute(
        "SELECT id FROM person WHERE id = ?",
        (person_id,),
    ).fetchone()
    if person_row is None:
        conn.close()
        raise LookupError(f"Person {person_id} not found")

    cluster_ids = [
        row["id"]
        for row in cur.execute(
            "SELECT id FROM cluster WHERE person_id = ?",
            (person_id,),
        ).fetchall()
    ]

    if cluster_ids:
        placeholders = ",".join("?" for _ in cluster_ids)
        if normalized_group == REVIEW_GROUP_UNASSIGNED:
            face_ids = [
                int(row["id"])
                for row in cur.execute(
                    f"""
                    SELECT id
                    FROM face
                    WHERE cluster_id IN ({placeholders})
                    """,
                    (*cluster_ids,),
                ).fetchall()
            ]
            cur.execute(
                f"""
                UPDATE face
                SET cluster_id = NULL, review_status = ?
                WHERE cluster_id IN ({placeholders})
                """,
                (FACE_REVIEW_STATUS_ACTIVE, *cluster_ids),
            )
            _recluster_active_faces_into_inbox(cur, face_ids)
        elif normalized_group == REVIEW_GROUP_UNKNOWN_PERSON:
            face_ids = [
                int(row["id"])
                for row in cur.execute(
                    f"""
                    SELECT id
                    FROM face
                    WHERE cluster_id IN ({placeholders})
                    """,
                    (*cluster_ids,),
                ).fetchall()
            ]
            _move_faces_to_hidden_review_status(
                cur,
                face_ids,
                FACE_REVIEW_STATUS_UNKNOWN_PERSON,
            )
        else:
            face_ids = [
                int(row["id"])
                for row in cur.execute(
                    f"""
                    SELECT id
                    FROM face
                    WHERE cluster_id IN ({placeholders})
                    """,
                    (*cluster_ids,),
                ).fetchall()
            ]
            _move_faces_to_hidden_review_status(
                cur,
                face_ids,
                FACE_REVIEW_STATUS_NOT_FACE,
            )

    cur.execute("DELETE FROM person WHERE id = ?", (person_id,))
    _delete_empty_clusters(cur)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()


def remove_face_from_cluster(face_id: int):
    """Remove one face from its cluster.

    Args:
        face_id: Face identifier to update.
    """
    conn = get_conn()
    cur = conn.cursor()
    source_row = cur.execute(
        "SELECT cluster_id FROM face WHERE id = ?",
        (face_id,),
    ).fetchone()
    excluded_cluster_ids = (
        {int(source_row["cluster_id"])}
        if source_row is not None and source_row["cluster_id"] is not None
        else set()
    )
    cur.execute(
        """
        UPDATE face
        SET cluster_id = NULL, review_status = ?
        WHERE id = ?
        """,
        (FACE_REVIEW_STATUS_ACTIVE, face_id),
    )
    _recluster_active_faces_into_inbox(
        cur,
        [face_id],
        excluded_cluster_ids=excluded_cluster_ids,
    )
    _delete_empty_clusters(cur)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()


def list_face_review_groups():
    """List non-cluster review queues with their face and cluster counts."""
    return app_cache.get_or_set(
        ("face_review_groups",),
        _load_face_review_groups,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={QUERY_CACHE_TAG_CLUSTERS, QUERY_CACHE_TAG_IMAGES},
    )


def _load_face_review_groups():
    """Run the review-queue count query (uncached)."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT review_group, face_count, cluster_count
        FROM (
            SELECT
                ? AS review_group,
                COUNT(*) AS face_count,
                COUNT(DISTINCT COALESCE(CAST(cluster_id AS TEXT), 'face:' || CAST(id AS TEXT))) AS cluster_count
            FROM face
            WHERE review_status = ? AND cluster_id IS NULL

            UNION ALL

            SELECT
                ? AS review_group,
                COUNT(*) AS face_count,
                COUNT(DISTINCT COALESCE(CAST(cluster_id AS TEXT), 'face:' || CAST(id AS TEXT))) AS cluster_count
            FROM face
            WHERE review_status = ?

            UNION ALL

            SELECT
                ? AS review_group,
                COUNT(*) AS face_count,
                COUNT(DISTINCT COALESCE(CAST(cluster_id AS TEXT), 'face:' || CAST(id AS TEXT))) AS cluster_count
            FROM face
            WHERE review_status = ?
        )
        ORDER BY CASE review_group
            WHEN ? THEN 0
            WHEN ? THEN 1
            ELSE 2
        END
        """,
        (
            REVIEW_GROUP_UNASSIGNED,
            FACE_REVIEW_STATUS_ACTIVE,
            REVIEW_GROUP_UNKNOWN_PERSON,
            FACE_REVIEW_STATUS_UNKNOWN_PERSON,
            REVIEW_GROUP_NOT_FACE,
            FACE_REVIEW_STATUS_NOT_FACE,
            REVIEW_GROUP_UNASSIGNED,
            REVIEW_GROUP_UNKNOWN_PERSON,
        ),
    ).fetchall()
    conn.close()
    labels = {
        REVIEW_GROUP_UNASSIGNED: "Nicht zugewiesen",
        REVIEW_GROUP_UNKNOWN_PERSON: "Unbekannte Personen",
        REVIEW_GROUP_NOT_FACE: "Keine Gesichter",
    }
    return [
        {
            "group_key": row["review_group"],
            "label": labels[row["review_group"]],
            "face_count": int(row["face_count"]),
            "cluster_count": int(row["cluster_count"]),
        }
        for row in rows
    ]


def get_faces_for_review_group(group_key: str):
    """Load faces for one face review queue."""
    normalized_group = normalize_face_review_group(group_key)
    conn = get_conn()
    if normalized_group == REVIEW_GROUP_UNASSIGNED:
        where_clause = "f.review_status = ? AND f.cluster_id IS NULL"
        params = (FACE_REVIEW_STATUS_ACTIVE,)
    else:
        where_clause = "f.review_status = ?"
        params = (normalized_group,)
    rows = conn.execute(
        f"""
        SELECT
            f.id, f.image_id,
            f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.cluster_id,
            p.name AS person_name, f.review_status
        FROM face f
        LEFT JOIN cluster c ON c.id = f.cluster_id
        LEFT JOIN person p ON p.id = c.person_id
        WHERE {where_clause}
        ORDER BY f.image_id ASC, f.id ASC
        """,
        params,
    ).fetchall()
    conn.close()
    labels = {
        REVIEW_GROUP_UNASSIGNED: "Nicht zugewiesen",
        REVIEW_GROUP_UNKNOWN_PERSON: "Unbekannte Personen",
        REVIEW_GROUP_NOT_FACE: "Keine Gesichter",
    }
    return {
        "group_key": normalized_group,
        "label": labels[normalized_group],
        "face_count": len(rows),
        "cluster_count": len(
            {
                row["cluster_id"] if row["cluster_id"] is not None else f"face:{row['id']}"
                for row in rows
            }
        ),
        "faces": [_face_row_to_dict(row) for row in rows],
    }


def remove_faces_from_cluster(cluster_id: int, face_ids):
    """Detach selected faces from one cluster while keeping them reviewable."""
    normalized_face_ids = _normalize_face_ids(face_ids)
    placeholders = ",".join("?" for _ in normalized_face_ids)
    conn = get_conn()
    cur = conn.cursor()
    affected_person_ids = _persons_of_faces(cur, normalized_face_ids)
    cur.execute(
        f"""
        UPDATE face
        SET cluster_id = NULL, review_status = ?
        WHERE cluster_id = ?
          AND id IN ({placeholders})
        """,
        (FACE_REVIEW_STATUS_ACTIVE, cluster_id, *normalized_face_ids),
    )
    detached_count = int(cur.rowcount)
    if detached_count == 0:
        conn.close()
        raise LookupError("No selected faces belong to this cluster")
    _recluster_active_faces_into_inbox(cur, normalized_face_ids, excluded_cluster_ids={cluster_id})
    _delete_empty_clusters(cur)
    mark_persons_dirty_for_recluster(cur, affected_person_ids)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return detached_count


def create_cluster_from_faces(face_ids):
    """Create a fresh dedicated cluster from selected faces."""
    normalized_face_ids = _normalize_face_ids(face_ids)
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in normalized_face_ids)
    existing = cur.execute(
        f"SELECT COUNT(*) AS count FROM face WHERE id IN ({placeholders})",
        normalized_face_ids,
    ).fetchone()["count"]
    if int(existing) != len(normalized_face_ids):
        conn.close()
        raise LookupError("One or more faces were not found")
    affected_person_ids = _persons_of_faces(cur, normalized_face_ids)
    cluster_id = _create_cluster_for_faces(cur, normalized_face_ids)
    _delete_empty_clusters(cur)
    mark_persons_dirty_for_recluster(cur, affected_person_ids)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return cluster_id


def assign_faces_to_person(face_ids, person_name: str):
    """Assign selected faces directly to a person via a dedicated cluster."""
    normalized_face_ids = _normalize_face_ids(face_ids)
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in normalized_face_ids)
    face_rows = cur.execute(
        f"""
        SELECT id, cluster_id
        FROM face
        WHERE id IN ({placeholders})
        """,
        normalized_face_ids,
    ).fetchall()
    if len(face_rows) != len(normalized_face_ids):
        conn.close()
        raise LookupError("One or more faces were not found")

    source_cluster_ids = {row["cluster_id"] for row in face_rows if row["cluster_id"] is not None}
    previous_person_ids = _persons_of_faces(cur, normalized_face_ids)
    person_id = _resolve_person_id(cur, person_name)
    reused_cluster_id = None
    if len(source_cluster_ids) == 1:
        source_cluster_id = next(iter(source_cluster_ids))
        selected_count = len(normalized_face_ids)
        cluster_face_count = cur.execute(
            """
            SELECT COUNT(*) AS count
            FROM face
            WHERE cluster_id = ? AND review_status = ?
            """,
            (source_cluster_id, FACE_REVIEW_STATUS_ACTIVE),
        ).fetchone()["count"]
        if int(cluster_face_count) == selected_count:
            cur.execute(
                "UPDATE cluster SET person_id = ? WHERE id = ?",
                (person_id, source_cluster_id),
            )
            cur.execute(
                f"""
                UPDATE face
                SET review_status = ?
                WHERE id IN ({placeholders})
                """,
                (FACE_REVIEW_STATUS_ACTIVE, *normalized_face_ids),
            )
            reused_cluster_id = int(source_cluster_id)

    if reused_cluster_id is None:
        reused_cluster_id = _create_cluster_for_faces(
            cur,
            normalized_face_ids,
            person_id=person_id,
        )

    _delete_empty_clusters(cur)
    mark_persons_dirty_for_recluster(cur, [*previous_person_ids, person_id])
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return reused_cluster_id


def mark_faces_with_review_status(face_ids, review_status: str):
    """Move selected faces into one explicit hidden review status."""
    normalized_face_ids = _normalize_face_ids(face_ids)
    normalized_status = normalize_face_review_status(review_status)
    if normalized_status == FACE_REVIEW_STATUS_ACTIVE:
        raise ValueError("Active faces should be restored with restore_faces_to_manual_review")
    placeholders = ",".join("?" for _ in normalized_face_ids)
    conn = get_conn()
    cur = conn.cursor()
    affected_person_ids = _persons_of_faces(cur, normalized_face_ids)
    _move_faces_to_hidden_review_status(cur, normalized_face_ids, normalized_status)
    updated_count = int(
        cur.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM face
            WHERE id IN ({placeholders})
              AND review_status = ?
            """,
            (*normalized_face_ids, normalized_status),
        ).fetchone()["count"]
    )
    if updated_count == 0:
        conn.close()
        raise LookupError("No faces were updated")
    _delete_empty_clusters(cur)
    mark_persons_dirty_for_recluster(cur, affected_person_ids)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return updated_count


def restore_faces_to_manual_review(face_ids):
    """Restore selected faces to the visible manual review queue."""
    normalized_face_ids = _normalize_face_ids(face_ids)
    placeholders = ",".join("?" for _ in normalized_face_ids)
    conn = get_conn()
    cur = conn.cursor()
    affected_person_ids = _persons_of_faces(cur, normalized_face_ids)
    cur.execute(
        f"""
        UPDATE face
        SET cluster_id = NULL, review_status = ?
        WHERE id IN ({placeholders})
        """,
        (FACE_REVIEW_STATUS_ACTIVE, *normalized_face_ids),
    )
    restored_count = int(cur.rowcount)
    if restored_count == 0:
        conn.close()
        raise LookupError("No faces were updated")
    _recluster_active_faces_into_inbox(cur, normalized_face_ids)
    _delete_empty_clusters(cur)
    mark_persons_dirty_for_recluster(cur, affected_person_ids)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return restored_count


def repair_active_inbox_faces(progress_callback=None) -> int:
    """Recluster legacy active faces that still have no cluster assignment."""
    conn = get_conn()
    cur = conn.cursor()
    face_ids = [
        int(row["id"])
        for row in cur.execute(
            """
            SELECT id
            FROM face
            WHERE review_status = ?
              AND cluster_id IS NULL
            ORDER BY id ASC
            """,
            (FACE_REVIEW_STATUS_ACTIVE,),
        ).fetchall()
    ]
    if not face_ids:
        conn.close()
        return 0

    repaired_count = _recluster_active_faces_into_inbox(
        cur,
        face_ids,
        progress_callback=progress_callback,
    )
    _delete_empty_clusters(cur)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return repaired_count


def recluster_unassigned_faces(progress_callback=None) -> int:
    """Completely rebuild every active, person-unassigned face cluster.

    This is deliberately not an incremental repair: all matching faces are
    detached first, every now-empty legacy cluster is deleted, and clustering
    restarts from an empty inbox index. Person-owned and hidden-review faces
    remain protected and are never included in the rebuild.
    """
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT f.id
        FROM face f
        LEFT JOIN cluster c ON c.id = f.cluster_id
        WHERE f.review_status = ?
          AND (
            f.cluster_id IS NULL
            OR c.person_id IS NULL
          )
        ORDER BY f.id ASC
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    face_ids = [int(row["id"]) for row in rows]
    if not face_ids:
        conn.close()
        return 0

    placeholders = ",".join("?" for _ in face_ids)
    cur.execute(
        f"""
        UPDATE face
        SET cluster_id = NULL, review_status = ?
        WHERE id IN ({placeholders})
        """,
        (FACE_REVIEW_STATUS_ACTIVE, *face_ids),
    )
    _delete_empty_clusters(cur)
    reclustered_count = _recluster_active_faces_into_inbox(
        cur,
        face_ids,
        progress_callback=progress_callback,
    )
    _delete_empty_clusters(cur)
    refresh_person_suggestions(cur)
    refresh_review_suggestions(cur)
    conn.commit()
    conn.close()
    invalidate_image_query_cache()
    return reclustered_count


def _load_recluster_group_face_ids(cur, person_id: int | None) -> list[int]:
    """Read the current members of one rebuild group inside the transaction."""
    if person_id is None:
        rows = cur.execute(
            """
            SELECT f.id
            FROM face f
            LEFT JOIN cluster c ON c.id = f.cluster_id
            WHERE f.review_status = ?
              AND (f.cluster_id IS NULL OR c.person_id IS NULL)
            ORDER BY f.id ASC
            """,
            (FACE_REVIEW_STATUS_ACTIVE,),
        ).fetchall()
    else:
        rows = cur.execute(
            """
            SELECT f.id
            FROM face f
            JOIN cluster c ON c.id = f.cluster_id
            WHERE f.review_status = ?
              AND c.person_id = ?
            ORDER BY f.id ASC
            """,
            (FACE_REVIEW_STATUS_ACTIVE, int(person_id)),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def _resolve_recluster_groups(cur, scoped: bool) -> list[int | None]:
    """Decide which groups to rebuild, persons first and the pool last."""
    if scoped:
        person_ids, include_unassigned = _load_dirty_recluster_scope(cur)
        existing = {
            int(row["id"]) for row in cur.execute("SELECT id FROM person").fetchall()
        }
        groups: list[int | None] = sorted(person_ids & existing)
        if include_unassigned:
            groups.append(None)
        return groups

    rows = cur.execute(
        """
        SELECT DISTINCT c.person_id AS person_id
        FROM face f
        JOIN cluster c ON c.id = f.cluster_id
        WHERE f.review_status = ? AND c.person_id IS NOT NULL
        """,
        (FACE_REVIEW_STATUS_ACTIVE,),
    ).fetchall()
    return sorted(int(row["person_id"]) for row in rows) + [None]


def recluster_all_active_faces(
    progress_callback=None,
    cancel_token=None,
    scoped: bool = False,
    commit_callback=None,
) -> int:
    """Rebuild person subclusters and the unassigned pool, one group at a time.

    Person identity is a hard boundary: each person's faces are rebuilt in an
    isolated index, so subclusters may change freely inside a person but faces
    can never cross from one person to another.

    Each group is read *and* rewritten inside its own short ``BEGIN IMMEDIATE``
    transaction. SQLite in WAL mode allows a single writer, so holding one
    library-wide transaction would stall every interactive write; per-group
    transactions keep that slot busy only for a moment. Cancelling between
    groups is therefore safe and immediate: finished groups stay committed, and
    the groups that were not reached keep both their clusters and their dirty
    markers, so the next run resumes them.

    Args:
        cancel_token: Object exposing ``is_set()``; checked between groups so an
            interactive write can take priority.
        scoped: Rebuild only the groups recorded in ``recluster_dirty_person``
            instead of the whole library.
        commit_callback: Optional callback invoked after each completed group
            transaction, when readers can safely observe the new state.

    Returns:
        Number of faces that were rebuilt.
    """
    def _cancelled() -> bool:
        return cancel_token is not None and cancel_token.is_set()

    conn = get_conn()
    conn.isolation_level = None  # explicit, short transactions per group
    cur = conn.cursor()
    try:
        groups = _resolve_recluster_groups(cur, scoped)
        if not groups:
            return 0

        total_faces = 0
        for person_id in groups:
            total_faces += len(_load_recluster_group_face_ids(cur, person_id))
        if total_faces == 0:
            if scoped:
                cur.execute("BEGIN IMMEDIATE")
                for person_id in groups:
                    _clear_dirty_recluster_person(cur, person_id)
                cur.execute("COMMIT")
            return 0

        completed_faces = 0
        for person_id in groups:
            if _cancelled():
                break

            cur.execute("BEGIN IMMEDIATE")
            try:
                face_ids = _load_recluster_group_face_ids(cur, person_id)
                if not face_ids:
                    _clear_dirty_recluster_person(cur, person_id)
                    cur.execute("COMMIT")
                    continue

                placeholders = ",".join("?" for _ in face_ids)
                cur.execute(
                    f"UPDATE face SET cluster_id = NULL WHERE id IN ({placeholders})",
                    face_ids,
                )
                # Exclude every surviving cluster so the group is rebuilt from
                # scratch instead of merging back into stale groupings.
                protected_cluster_ids = {
                    int(row["id"])
                    for row in cur.execute("SELECT id FROM cluster").fetchall()
                }

                group_offset = completed_faces

                def group_progress(processed: int, _group_total: int) -> None:
                    if progress_callback is not None:
                        progress_callback(group_offset + processed, total_faces)

                _recluster_active_faces_into_inbox(
                    cur,
                    face_ids,
                    excluded_cluster_ids=protected_cluster_ids,
                    progress_callback=group_progress,
                )

                if person_id is not None:
                    cur.execute(
                        f"""
                        UPDATE cluster
                        SET person_id = ?
                        WHERE id IN (
                            SELECT DISTINCT cluster_id
                            FROM face
                            WHERE id IN ({placeholders})
                              AND cluster_id IS NOT NULL
                        )
                        """,
                        (person_id, *face_ids),
                    )

                _clear_dirty_recluster_person(cur, person_id)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

            completed_faces += len(face_ids)
            if commit_callback is not None:
                try:
                    commit_callback(completed_faces, total_faces)
                except Exception:  # pragma: no cover - UI notification is optional
                    logger.exception("Recluster commit notification failed")

        cur.execute("BEGIN IMMEDIATE")
        try:
            _delete_empty_clusters(cur)
            refresh_person_suggestions(cur)
            refresh_review_suggestions(cur)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    invalidate_image_query_cache()
    return completed_faces


def list_persons():
    """List known people alphabetically.

    Returns:
        Person identifier and name dictionaries.
    """
    return app_cache.get_or_set(
        ("person_list",),
        _load_persons,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={QUERY_CACHE_TAG_PERSONS},
    )


def _load_persons():
    """Load known people from the database."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name FROM person ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def list_filename_rename_candidates(
    suffix_format: str | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
    limit: int | None = 100,
    offset: int = 0,
):
    """List image locations whose filenames should be updated with person names."""
    suffix_format = suffix_format or get_filename_person_suffix_format()
    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    folders = folders or []
    persons = _normalize_person_filters(persons)
    sort_by = "folder" if sort_by == "folder" else "date"
    sort_direction = "asc" if sort_direction == "asc" else "desc"
    if limit is not None:
        limit = max(1, min(int(limit), MAX_IMAGE_PAGE_SIZE))
    offset = max(0, int(offset))

    cache_key = (
        "filename_rename_candidates",
        suffix_format,
        block_separator,
        joiner,
        tuple(folders),
        tuple(persons),
        sort_by,
        sort_direction,
        limit,
        offset,
    )

    def load_candidates():
        conn = get_conn()
        try:
            if limit is None:
                rows = _fetch_filename_rename_candidate_rows(
                    conn,
                    folders=folders,
                    persons=persons,
                    sort_by=sort_by,
                    sort_direction=sort_direction,
                    limit=None,
                    offset=0,
                )
                candidates = _build_filename_rename_candidates_from_rows(
                    rows,
                    block_separator=block_separator,
                    joiner=joiner,
                )
                return candidates, len(candidates)

            scan_size = max(MAX_IMAGE_PAGE_SIZE, limit * 4)
            raw_offset = 0
            collected_candidates = []
            target_size = offset + limit

            while len(collected_candidates) < target_size:
                rows = _fetch_filename_rename_candidate_rows(
                    conn,
                    folders=folders,
                    persons=persons,
                    sort_by=sort_by,
                    sort_direction=sort_direction,
                    limit=scan_size,
                    offset=raw_offset,
                )
                if not rows:
                    break
                collected_candidates.extend(
                    _build_filename_rename_candidates_from_rows(
                        rows,
                        block_separator=block_separator,
                        joiner=joiner,
                    )
                )
                raw_offset += scan_size

            total = len(collected_candidates)
            return collected_candidates[offset : offset + limit], total
        finally:
            conn.close()

    return app_cache.get_or_set(
        cache_key,
        load_candidates,
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_RENAMES,
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_SETTINGS,
        },
    )


def _filename_rename_location_order(prefix: str, sort_by: str, sort_direction: str):
    """Return the ORDER BY clause for filename rename candidate locations."""
    direction = "ASC" if sort_direction == "asc" else "DESC"
    if sort_by == "folder":
        return (
            f"{prefix}directory COLLATE NOCASE {direction}, "
            f"{prefix}filename COLLATE NOCASE ASC, "
            f"{prefix}path COLLATE NOCASE ASC"
        )
    return (
        f"{prefix}created_at IS NULL ASC, "
        f"{prefix}created_at {direction}, "
        f"{prefix}filename COLLATE NOCASE ASC, "
        f"{prefix}path COLLATE NOCASE ASC"
    )


def _fetch_filename_rename_candidate_rows(
    conn,
    *,
    folders,
    persons,
    sort_by: str,
    sort_direction: str,
    limit: int | None,
    offset: int,
):
    """Fetch ordered face rows for a slice of image locations."""
    cte, params = _matching_images_cte(folders=folders, persons=persons)
    location_order = _filename_rename_location_order(
        "matching_locations.", sort_by, sort_direction
    )
    if limit is None:
        paging_cte = ""
        from_table = "matching_locations"
        query_params = params
    else:
        paging_cte = f"""
        , paged_locations AS (
            SELECT *
            FROM matching_locations
            ORDER BY {location_order}
            LIMIT ? OFFSET ?
        )
        """
        from_table = "paged_locations"
        query_params = [*params, limit, offset]

    return conn.execute(
        f"""
        {cte}
        , matching_locations AS (
            SELECT
                location.id AS location_id,
                location.image_id,
                location.path,
                location.directory,
                location.filename,
                location.created_at
            FROM image_location location
            JOIN matching_images
                ON matching_images.image_id = location.image_id
        )
        {paging_cte}
        SELECT
            {from_table}.location_id,
            {from_table}.image_id,
            {from_table}.path,
            {from_table}.directory,
            {from_table}.filename,
            {from_table}.created_at,
            f.id AS face_id,
            f.bbox_x,
            p.name AS person_name
        FROM {from_table}
        JOIN face f ON f.image_id = {from_table}.image_id
        JOIN cluster c ON f.cluster_id = c.id
        JOIN person p ON c.person_id = p.id
        ORDER BY {_filename_rename_location_order(f'{from_table}.', sort_by, sort_direction)}, f.bbox_x ASC, f.id ASC
        """,
        query_params,
    ).fetchall()


def _build_filename_rename_candidates_from_rows(
    rows,
    *,
    block_separator: str,
    joiner: str,
):
    """Build rename candidates from ordered face rows."""
    candidates = []
    grouped_rows = []
    current_location_rows = []
    current_location_id = None
    for row in rows:
        location_id = row["location_id"]
        if current_location_id is None or current_location_id == location_id:
            current_location_rows.append(row)
            current_location_id = location_id
            continue
        grouped_rows.append(current_location_rows)
        current_location_rows = [row]
        current_location_id = location_id
    if current_location_rows:
        grouped_rows.append(current_location_rows)

    for location_rows in grouped_rows:
        first_row = location_rows[0]
        if not os.path.isfile(first_row["path"]):
            continue
        preview = build_person_filename_preview(
            first_row["filename"],
            [row["person_name"] for row in location_rows],
            block_separator=block_separator,
            joiner=joiner,
        )
        if not preview:
            continue
        candidates.append(
            {
                "location_id": first_row["location_id"],
                "image_id": first_row["image_id"],
                "path": first_row["path"],
                "directory": first_row["directory"],
                "created_at": first_row["created_at"],
                "current_filename": preview["current_filename"],
                "proposed_filename": preview["proposed_filename"],
                "proposed_path": os.path.join(
                    first_row["directory"], preview["proposed_filename"]
                ),
                "detected_person_names": preview["detected_person_names"],
                "current_suffix_person_names": preview["current_suffix_person_names"],
            }
        )
    return candidates


def count_filename_rename_candidates(
    suffix_format: str | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
):
    """Count rename candidates after preview filtering."""
    suffix_format = suffix_format or get_filename_person_suffix_format()
    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    folders = folders or []
    persons = _normalize_person_filters(persons)
    sort_by = "folder" if sort_by == "folder" else "date"
    sort_direction = "asc" if sort_direction == "asc" else "desc"
    cache_key = (
        "filename_rename_candidates_count",
        suffix_format,
        block_separator,
        joiner,
        tuple(folders),
        tuple(persons),
        sort_by,
        sort_direction,
    )
    return app_cache.get_or_set(
        cache_key,
        lambda: list_filename_rename_candidates(
            suffix_format=suffix_format,
            folders=folders,
            persons=persons,
            sort_by=sort_by,
            sort_direction=sort_direction,
            limit=None,
            offset=0,
        )[1],
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_RENAMES,
            QUERY_CACHE_TAG_IMAGES,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_SETTINGS,
        },
    )


def list_filename_rename_candidates_for_paths(
    paths: list[str] | None,
    *,
    suffix_format: str | None = None,
    folders=None,
    persons=None,
):
    """Build rename candidates for specific image paths only."""
    requested_paths = list(dict.fromkeys(path for path in (paths or []) if path))
    if not requested_paths:
        return []

    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    folders = folders or []
    persons = _normalize_person_filters(persons)
    placeholders = ",".join("?" for _ in requested_paths)

    conn = get_conn()
    try:
        cte, params = _matching_images_cte(folders=folders, persons=persons)
        rows = conn.execute(
            f"""
            {cte}
            SELECT
                location.id AS location_id,
                location.image_id,
                location.path,
                location.directory,
                location.filename,
                location.created_at,
                f.id AS face_id,
                f.bbox_x,
                p.name AS person_name
            FROM image_location location
            JOIN matching_images
                ON matching_images.image_id = location.image_id
            JOIN face f ON f.image_id = location.image_id
            JOIN cluster c ON f.cluster_id = c.id
            JOIN person p ON c.person_id = p.id
            WHERE location.path IN ({placeholders})
            ORDER BY location.path COLLATE NOCASE ASC, f.bbox_x ASC, f.id ASC
            """,
            [*params, *requested_paths],
        ).fetchall()
    finally:
        conn.close()

    candidates = _build_filename_rename_candidates_from_rows(
        rows,
        block_separator=block_separator,
        joiner=joiner,
    )
    candidate_by_path = {candidate["path"]: candidate for candidate in candidates}
    return [
        candidate_by_path[path]
        for path in requested_paths
        if path in candidate_by_path
    ]


def list_all_filename_rename_candidates(
    suffix_format: str | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
):
    """Return the complete rename candidate list without pagination."""
    candidates, _ = list_filename_rename_candidates(
        suffix_format=suffix_format,
        folders=folders,
        persons=persons,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=None,
        offset=0,
    )
    return candidates


def _refresh_canonical_image_location(cur, image_id: int):
    """Keep the legacy canonical image columns aligned with image locations."""
    row = cur.execute(
        """
        SELECT path, directory, filename
        FROM image_location
        WHERE image_id = ?
        ORDER BY path COLLATE NOCASE ASC
        LIMIT 1
        """,
        (image_id,),
    ).fetchone()
    if row:
        cur.execute(
            """
            UPDATE image
            SET path = ?, directory = ?, filename = ?
            WHERE id = ?
            """,
            (row["path"], row["directory"], row["filename"], image_id),
        )


def rename_image_locations_to_match_people(
    selected_paths: list[str] | None = None,
    rename_all: bool = False,
    excluded_paths: list[str] | None = None,
    folders=None,
    persons=None,
    sort_by: str = "date",
    sort_direction: str = "desc",
):
    """Rename selected candidate image locations and sync database metadata."""
    selected_paths = list(dict.fromkeys(selected_paths or []))
    excluded = {path for path in (excluded_paths or []) if path}

    if rename_all:
        candidates = list_all_filename_rename_candidates(
            suffix_format=None,
            folders=folders,
            persons=persons,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
        target_candidates = [
            candidate
            for candidate in candidates
            if candidate["path"] not in excluded
        ]
    else:
        target_candidates = list_filename_rename_candidates_for_paths(
            selected_paths,
            suffix_format=None,
            folders=folders,
            persons=persons,
        )

    renamed = []
    skipped = []
    errors = []

    for candidate in target_candidates:
        source_path = candidate["path"]
        target_path = candidate["proposed_path"]
        if source_path == target_path:
            skipped.append({"path": source_path, "reason": "already_matches"})
            continue
        if not os.path.isfile(source_path):
            skipped.append({"path": source_path, "reason": "missing_source"})
            continue
        if os.path.exists(target_path):
            skipped.append({"path": source_path, "reason": "target_exists"})
            continue

        try:
            os.rename(source_path, target_path)
        except OSError as exc:
            logger.exception(
                "Could not rename image from %s to %s",
                source_path,
                target_path,
            )
            errors.append({"path": source_path, "reason": str(exc)})
            continue

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE image_location
                SET path = ?, filename = ?
                WHERE id = ?
                """,
                (target_path, candidate["proposed_filename"], candidate["location_id"]),
            )
            _refresh_canonical_image_location(cur, candidate["image_id"])
            conn.commit()
        except sqlite3.DatabaseError as exc:
            conn.rollback()
            logger.exception(
                "Database update failed after renaming %s to %s",
                source_path,
                target_path,
            )
            try:
                os.rename(target_path, source_path)
            except OSError:
                logger.exception(
                    "Could not roll back file rename from %s to %s",
                    target_path,
                    source_path,
                )
                pass
            errors.append({"path": source_path, "reason": str(exc)})
            conn.close()
            continue
        conn.close()
        renamed.append(
            {
                "from_path": source_path,
                "to_path": target_path,
                "image_id": candidate["image_id"],
            }
        )

    if renamed:
        invalidate_image_query_cache()

    return {
        "renamed": renamed,
        "skipped": skipped,
        "errors": errors,
        "renamed_count": len(renamed),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }


def get_person_faces(person_id: int):
    """List faces assigned to a person through clusters.

    Args:
        person_id: Person identifier.

    Returns:
        Face dictionaries assigned to the person.
    """
    return app_cache.get_or_set(
        ("person_faces", int(person_id)),
        lambda: _load_person_faces(person_id),
        ttl_seconds=QUERY_CACHE_TTL_SECONDS,
        tags={
            QUERY_CACHE_TAG_PERSON_FACES,
            QUERY_CACHE_TAG_PERSONS,
            QUERY_CACHE_TAG_CLUSTERS,
            QUERY_CACHE_TAG_IMAGES,
        },
    )


def _load_person_faces(person_id: int):
    """Load faces assigned to a person through clusters."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            f.id, f.image_id, i.path AS image_path,
            f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h, f.cluster_id,
            p.name AS person_name, f.review_status
        FROM face f
        JOIN image i ON i.id = f.image_id
        JOIN cluster c ON f.cluster_id = c.id
        JOIN person p ON p.id = c.person_id
        WHERE c.person_id = ?
          AND f.review_status = ?
        """,
        (person_id, FACE_REVIEW_STATUS_ACTIVE),
    ).fetchall()
    conn.close()
    return [_face_row_to_dict(r) for r in rows]
