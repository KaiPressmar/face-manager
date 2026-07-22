"""Reusable face import pipeline components."""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Optional, Set, Tuple

import numpy as np
from PIL import Image

from ..db.schema import calculate_file_hash, get_conn, get_file_created_at
from ..error_logging import configure_error_logging
from ..models.face_model import FaceModel, get_compute_mode
from .face_thumbnails import delete_face_thumbnail
from .storage import (
    FACE_REVIEW_STATUS_ACTIVE,
    get_cluster_distance_threshold,
    invalidate_image_query_cache,
    load_all_embeddings,
)

configure_error_logging()
logger = logging.getLogger("face_manager.pipeline")

if TYPE_CHECKING:
    from ..models.clustering import FaceClustering

ProgressCallback = Callable[[dict], None]

_PROCESSING_SLOT_LOCK = threading.Lock()
_PROCESSING_SLOT_COUNT = 1
_PROCESSING_SLOT_SEMAPHORE: threading.BoundedSemaphore = threading.BoundedSemaphore(1)


class ImportCancelled(Exception):
    """Signal that an import job was cancelled by the user."""


def _control_cancelled(control) -> bool:
    """Honor an optional pause checkpoint before checking cancellation."""
    wait_if_paused = getattr(control, "wait_if_paused", None)
    if callable(wait_if_paused) and wait_if_paused():
        return True
    return bool(control and control.is_set())


def configure_processing_slots(slot_count: int) -> None:
    """Configure the shared concurrent processing slot budget.

    Args:
        slot_count: Maximum number of jobs allowed in processing stage.
    """
    global _PROCESSING_SLOT_COUNT, _PROCESSING_SLOT_SEMAPHORE
    bounded = max(1, int(slot_count))
    with _PROCESSING_SLOT_LOCK:
        if bounded == _PROCESSING_SLOT_COUNT:
            return
        _PROCESSING_SLOT_COUNT = bounded
        _PROCESSING_SLOT_SEMAPHORE = threading.BoundedSemaphore(bounded)


@dataclass
class HashedImage:
    """Hold identity metadata for one discovered image.

    Args:
        path: Original filesystem path.
        normalized_path: Platform-normalized path used for database lookups.
        content_hash: SHA-256 digest used for duplicate detection.
        created_at: Best available filesystem creation timestamp.
        file_size: Source size used to recognize unchanged paths cheaply.
        modified_at_ns: High-resolution modification time for change detection.
    """

    path: Path
    normalized_path: str
    content_hash: str
    created_at: str | None
    file_size: int
    modified_at_ns: int


class ImagePreparer:
    """Hash images concurrently and decode only content requiring inference.

    Args:
        worker_count: Maximum number of images prepared concurrently.
    """

    def __init__(self, worker_count: int):
        """Initialize the bounded image preparation pool configuration.

        Args:
            worker_count: Maximum number of concurrent preparation tasks.
        """
        self.worker_count = max(1, worker_count)

    @staticmethod
    def hash_image(path: Path) -> HashedImage:
        """Calculate identity metadata for one image.

        Args:
            path: Image path to read.

        Returns:
            Hashed image metadata used for import planning.
        """
        normalized_path = os.path.normpath(str(path))
        stat_result = os.stat(normalized_path)
        content_hash = calculate_file_hash(normalized_path)
        created_at = get_file_created_at(normalized_path, stat_result)
        return HashedImage(
            path,
            normalized_path,
            content_hash,
            created_at,
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
        )

    @staticmethod
    def decode(path: Path) -> np.ndarray:
        """Decode one image into RGB pixels.

        Args:
            path: Image path to decode.

        Returns:
            RGB image represented as a NumPy array.
        """
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"))

    def iter_hashed(
        self,
        paths: Iterable[Path],
        cancel_event: Optional[threading.Event] = None,
    ) -> Iterator[Tuple[Path, Future[HashedImage]]]:
        """Yield hashing futures in input order while keeping workers busy.

        Args:
            paths: Ordered image paths to prepare.
            cancel_event: Optional event that stops scheduling additional work.

        Yields:
            Tuples containing the source path and its hashing future.
        """
        iterator = iter(paths)
        pending = deque()

        with ThreadPoolExecutor(
            max_workers=self.worker_count,
            thread_name_prefix="image-prep",
        ) as executor:
            for _ in range(self.worker_count):
                if cancel_event and _control_cancelled(cancel_event):
                    break
                try:
                    path = next(iterator)
                except StopIteration:
                    break
                pending.append((path, executor.submit(self.hash_image, path)))

            while pending:
                path, future = pending.popleft()
                if not cancel_event or not _control_cancelled(cancel_event):
                    try:
                        next_path = next(iterator)
                    except StopIteration:
                        pass
                    else:
                        pending.append(
                            (
                                next_path,
                                executor.submit(self.hash_image, next_path),
                            )
                        )
                yield path, future

    def iter_hashed_completed(
        self,
        paths: Iterable[Path],
        cancel_event: Optional[threading.Event] = None,
    ) -> Iterator[Tuple[Path, Future[HashedImage]]]:
        """Yield hashing futures as they finish.

        Args:
            paths: Ordered image paths to prepare.
            cancel_event: Optional event that stops scheduling additional work.

        Yields:
            Tuples containing the source path and its hashing future.
        """
        iterator = iter(paths)
        pending: dict[Future[HashedImage], Path] = {}

        with ThreadPoolExecutor(
            max_workers=self.worker_count,
            thread_name_prefix="image-prep",
        ) as executor:
            for _ in range(self.worker_count):
                if cancel_event and _control_cancelled(cancel_event):
                    break
                try:
                    path = next(iterator)
                except StopIteration:
                    break
                future = executor.submit(self.hash_image, path)
                pending[future] = path

            while pending:
                completed, _ = wait(
                    tuple(pending),
                    return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    path = pending.pop(future)
                    yield path, future

                    if cancel_event and _control_cancelled(cancel_event):
                        continue
                    try:
                        next_path = next(iterator)
                    except StopIteration:
                        continue
                    next_future = executor.submit(self.hash_image, next_path)
                    pending[next_future] = next_path


class ImportResources:
    """Lazily own the model and clustering index shared by queued imports."""

    def __init__(self):
        """Create an unloaded resource container."""
        self._model: Optional[FaceModel] = None
        self._clusterer: Optional["FaceClustering"] = None
        self._clusterer_loaded = False
        self._lock = threading.Lock()

    def get_model(self) -> FaceModel:
        """Return the shared face model, loading it on first use.

        Returns:
            Initialized face detection and recognition model.
        """
        with self._lock:
            if self._model is None:
                self._model = FaceModel()
            return self._model

    def get_clusterer(self) -> "FaceClustering":
        """Return the clustering index, loading stored embeddings once.

        Returns:
            Initialized incremental face clustering index.
        """
        with self._lock:
            if self._clusterer is None:
                from ..models.clustering import FaceClustering

                self._clusterer = FaceClustering()
            if not self._clusterer_loaded:
                embeddings, cluster_ids, person_ids = load_all_embeddings()
                if embeddings.size > 0:
                    self._clusterer.load_existing(
                        embeddings,
                        cluster_ids,
                        person_ids,
                    )
                self._clusterer_loaded = True
            return self._clusterer

    def reset_clusterer(self) -> None:
        """Drop the cached clustering index so it reloads from the database."""
        with self._lock:
            self._clusterer = None
            self._clusterer_loaded = False


class ImportProcessor:
    """Process one folder import at a time.

    Args:
        resources: Optional shared model and clustering resource container.
    """

    IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

    def __init__(self, resources: Optional[ImportResources] = None):
        """Initialize an import processor.

        Args:
            resources: Shared resources to reuse across queued jobs.
        """
        self.resources = resources or ImportResources()

    def process(
        self,
        folder_path: str,
        progress_callback: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Import all supported images below a folder.

        Args:
            folder_path: Existing folder to scan recursively.
            progress_callback: Callback receiving partial progress updates.
            cancel_event: Event used to request cooperative cancellation.

        Raises:
            ImportCancelled: If cancellation is requested between images.
            FileNotFoundError: If the queued folder no longer exists.
        """
        folder = Path(folder_path)
        if not folder.is_dir():
            raise FileNotFoundError(f"Import folder no longer exists: {folder_path}")

        progress_callback(
            {
                "stage": "scanning",
                "stage_current": 0,
                "stage_total": 0,
                "current_file": str(folder),
            }
        )
        image_paths = self._find_images(folder, progress_callback, cancel_event)
        progress_callback(
            {
                "stage": "hashing",
                "stage_current": 0,
                "stage_total": len(image_paths),
                "hashed_images": 0,
                "current_file": None,
                "total_images": len(image_paths),
                "processed_images": 0,
                "total_faces": 0,
                "processed_faces": 0,
            }
        )

        conn = get_conn()
        processing_slot_acquired = False
        try:
            cursor = conn.cursor()
            worker_count = get_import_worker_count(get_compute_mode())
            preparer = ImagePreparer(worker_count)
            resources_loaded = False
            content_groups: dict[str, list[HashedImage]] = {}
            paths_to_hash, unchanged_paths = self._partition_unchanged_paths(
                cursor,
                image_paths,
                cancel_event,
            )
            completed_images = len(unchanged_paths)
            hashed_images = len(unchanged_paths)
            processing_started = False
            if unchanged_paths:
                progress_callback(
                    {
                        "processed_images": completed_images,
                        "hashed_images": hashed_images,
                        "stage_current": hashed_images,
                        "current_file": str(unchanged_paths[-1]),
                    }
                )

            for image_path, future in preparer.iter_hashed_completed(
                paths_to_hash, cancel_event
            ):
                self._raise_if_cancelled(cancel_event)
                try:
                    hashed = future.result()
                    content_groups.setdefault(hashed.content_hash, []).append(hashed)
                    matching_content = cursor.execute(
                        """
                        SELECT id, processed_at
                        FROM image
                        WHERE content_hash = ?
                        """,
                        (hashed.content_hash,),
                    ).fetchone()

                    if matching_content and matching_content["processed_at"]:
                        self._attach_location(
                            cursor,
                            conn,
                            matching_content["id"],
                            hashed,
                        )
                        completed_images += 1
                        progress_callback(
                            {
                                "processed_images": completed_images,
                                "stage_current": completed_images,
                                "current_file": str(hashed.path),
                            }
                        )
                    else:
                        primary = hashed
                        try:
                            if not processing_started:
                                self._acquire_processing_slot(cancel_event)
                                processing_slot_acquired = True
                                progress_callback(
                                    {
                                        "stage": "processing",
                                        "stage_current": completed_images,
                                        "stage_total": len(image_paths),
                                        "current_file": None,
                                    }
                                )
                                processing_started = True

                            image_id = (
                                matching_content["id"]
                                if matching_content
                                else self._create_image(cursor, primary)
                            )
                            self._attach_location(cursor, conn, image_id, primary)
                            self._raise_if_cancelled(cancel_event)
                            if not resources_loaded:
                                progress_callback(
                                    {
                                        "stage": "loading_model",
                                        "current_file": None,
                                    }
                                )
                                model = self.resources.get_model()
                                progress_callback({"stage": "loading_index"})
                                resources_loaded = True
                                progress_callback(
                                    {
                                        "stage": "processing",
                                        "stage_current": completed_images,
                                        "stage_total": len(image_paths),
                                    }
                                )
                            # Interactive assignments reset the shared resource
                            # between images. Resolve the clusterer for every
                            # image so a running import observes that reset
                            # instead of keeping a stale person/cluster map for
                            # the remainder of the job.
                            clusterer = self.resources.get_clusterer()
                            image_np = preparer.decode(primary.path)
                            progress_callback({"current_file": str(primary.path)})
                            self._process_image(
                                cursor,
                                conn,
                                image_id,
                                image_np,
                                model,
                                clusterer,
                                progress_callback,
                            )
                        except ImportCancelled:
                            raise
                        except Exception as exc:
                            conn.rollback()
                            logger.exception(
                                "Import processing failed for %s",
                                primary.path,
                            )
                            progress_callback({"last_error": f"{primary.path}: {exc}"})
                        finally:
                            completed_images += 1
                            progress_callback(
                                {
                                    "processed_images": completed_images,
                                    "stage_current": completed_images,
                                }
                            )
                except ImportCancelled:
                    raise
                except Exception as exc:
                    logger.exception("Import hashing/planning failed for %s", image_path)
                    progress_callback({"last_error": f"{image_path}: {exc}"})
                    completed_images += 1
                    progress_callback({"processed_images": completed_images})
                finally:
                    hashed_images += 1
                    progress_update: dict[str, object] = {
                        "current_file": str(image_path),
                        "hashed_images": hashed_images,
                    }
                    if not processing_started:
                        progress_update["stage_current"] = hashed_images
                    progress_callback(progress_update)

            for content_hash, locations in content_groups.items():
                matching_content = cursor.execute(
                    """
                    SELECT id, processed_at
                    FROM image
                    WHERE content_hash = ?
                    """,
                    (content_hash,),
                ).fetchone()
                if not matching_content or not matching_content["processed_at"]:
                    continue
                discovered_paths = {hashed.normalized_path for hashed in locations}
                self._prune_stale_locations(
                    cursor,
                    conn,
                    matching_content["id"],
                    content_hash,
                    discovered_paths,
                )

            self._raise_if_cancelled(cancel_event)
            progress_callback(
                {
                    "stage": "finalizing",
                    "stage_current": len(image_paths),
                    "stage_total": len(image_paths),
                    "current_file": None,
                }
            )
        finally:
            if processing_slot_acquired:
                self._release_processing_slot()
            conn.close()

    @staticmethod
    def _acquire_processing_slot(cancel_event: threading.Event) -> None:
        """Acquire the shared processing slot with cancellation checks."""
        while True:
            if _control_cancelled(cancel_event):
                raise ImportCancelled()
            acquired = _PROCESSING_SLOT_SEMAPHORE.acquire(timeout=0.2)
            if acquired:
                return

    @staticmethod
    def _release_processing_slot() -> None:
        """Release one shared processing slot."""
        _PROCESSING_SLOT_SEMAPHORE.release()

    @classmethod
    def _find_images(
        cls,
        folder: Path,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[Path]:
        """Find supported images below a folder.

        Args:
            folder: Root directory to scan recursively.

        Returns:
            Sorted image paths for deterministic queue processing.
        """
        image_paths = []
        for root, directories, filenames in os.walk(folder):
            directories.sort()
            filenames.sort()
            if cancel_event is not None:
                cls._raise_if_cancelled(cancel_event)
            for filename in filenames:
                path = Path(root) / filename
                if path.suffix.lower() in cls.IMAGE_SUFFIXES:
                    image_paths.append(path)
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage_current": len(image_paths),
                        "current_file": root,
                    }
                )
        return image_paths

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event) -> None:
        """Raise when cancellation has been requested.

        Args:
            cancel_event: Event carrying the cancellation request.

        Raises:
            ImportCancelled: If the event is set.
        """
        if _control_cancelled(cancel_event):
            raise ImportCancelled()

    @classmethod
    def _partition_unchanged_paths(
        cls,
        cursor,
        image_paths: list[Path],
        cancel_event: threading.Event,
    ) -> tuple[list[Path], list[Path]]:
        """Skip hashing when a processed path has identical size and mtime."""
        paths_by_normalized = {
            os.path.normpath(str(path)): path for path in image_paths
        }
        known_locations = {}
        normalized_paths = list(paths_by_normalized)
        for start in range(0, len(normalized_paths), 400):
            cls._raise_if_cancelled(cancel_event)
            chunk = normalized_paths[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = cursor.execute(
                f"""
                SELECT location.path, location.file_size,
                       location.modified_at_ns, image.processed_at
                FROM image_location location
                JOIN image ON image.id = location.image_id
                WHERE location.path IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            known_locations.update({row["path"]: row for row in rows})

        unchanged = []
        needs_hash = []
        for normalized_path, path in paths_by_normalized.items():
            cls._raise_if_cancelled(cancel_event)
            known = known_locations.get(normalized_path)
            if (
                known is None
                or not known["processed_at"]
                or known["file_size"] is None
                or known["modified_at_ns"] is None
            ):
                needs_hash.append(path)
                continue
            try:
                stat_result = os.stat(normalized_path)
            except OSError:
                needs_hash.append(path)
                continue
            if (
                int(known["file_size"]) == int(stat_result.st_size)
                and int(known["modified_at_ns"]) == int(stat_result.st_mtime_ns)
            ):
                unchanged.append(path)
            else:
                needs_hash.append(path)
        return needs_hash, unchanged

    def _process_image(
        self,
        cursor,
        connection,
        image_id: int,
        image_np: np.ndarray,
        model: FaceModel,
        clusterer: FaceClustering,
        progress_callback: ProgressCallback,
    ) -> None:
        """Persist and analyze one prepared image.

        Args:
            cursor: SQLite cursor used for reads and writes.
            connection: SQLite connection controlling transactions.
            image_id: Canonical image identifier.
            image_np: Decoded RGB image pixels.
            model: Face detection and recognition model.
            clusterer: Incremental face clustering index.
            progress_callback: Callback receiving face progress updates.
        """
        # Detection and in-memory clustering are the expensive part. Do them
        # before the first database write so SQLite's single WAL writer remains
        # available to interactive assignments while inference is running.
        faces = model.detect_and_embed(image_np)
        distance_threshold = get_cluster_distance_threshold()
        proposed_faces: list[tuple[dict, int]] = []
        for face in faces:
            cluster_ids, _ = clusterer.add_and_assign(
                np.expand_dims(face["embedding"], axis=0),
                distance_threshold=distance_threshold,
                # Confirmed person clusters are reference data, not an
                # authorization to silently attach newly imported faces.
                # Person matches are generated as reviewable suggestions by
                # the post-import/reclustering workflow instead.
                allow_person_matches=False,
            )
            proposed_faces.append((face, int(cluster_ids[0])))

        existing_face_ids = [
            int(row["id"])
            for row in cursor.execute(
                "SELECT id FROM face WHERE image_id = ?",
                (image_id,),
            ).fetchall()
        ]
        cursor.execute("DELETE FROM face WHERE image_id = ?", (image_id,))
        fallback_cluster_ids: dict[int, int] = {}
        for face, proposed_cluster_id in proposed_faces:
            x1, y1, width, height = face["bbox"]
            embedding = face["embedding"]
            cluster_id = self._resolve_import_cluster_id(
                cursor,
                proposed_cluster_id,
                fallback_cluster_ids,
            )
            cursor.execute(
                """
                INSERT INTO face(
                    image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                    cluster_id, review_status, embedding
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    float(x1),
                    float(y1),
                    float(width),
                    float(height),
                    cluster_id,
                    FACE_REVIEW_STATUS_ACTIVE,
                    embedding.astype("float32").tobytes(),
                ),
            )

        cursor.execute(
            "UPDATE image SET processed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (image_id,),
        )
        connection.commit()
        for face_id in existing_face_ids:
            delete_face_thumbnail(face_id)
        # Face crops are generated on demand and by the idle warmup worker.
        # Keeping that recoverable JPEG work out of the critical import path
        # lets detection immediately continue with the next image.
        invalidate_image_query_cache()
        progress_callback(
            {
                "total_faces_increment": len(faces),
                "processed_faces_increment": len(faces),
            }
        )

    @staticmethod
    def _resolve_import_cluster_id(
        cursor,
        proposed_cluster_id: int,
        fallback_cluster_ids: dict[int, int],
    ) -> int:
        """Keep stale import proposals out of user-confirmed person groups.

        A person assignment may happen after the import read its in-memory
        clustering index. The insert below starts the short write transaction;
        checking ``person_id`` while that writer slot is held makes the choice
        atomic with the following face insert. Similar faces from the same
        image share one fresh fallback cluster.
        """
        cursor.execute(
            "INSERT OR IGNORE INTO cluster(id, label) VALUES (?, ?)",
            (proposed_cluster_id, f"Cluster {proposed_cluster_id}"),
        )
        row = cursor.execute(
            "SELECT person_id FROM cluster WHERE id = ?",
            (proposed_cluster_id,),
        ).fetchone()
        if row is not None and row["person_id"] is None:
            return proposed_cluster_id

        fallback = fallback_cluster_ids.get(proposed_cluster_id)
        if fallback is not None:
            return fallback
        cursor.execute(
            "INSERT INTO cluster(label, person_id) VALUES (?, NULL)",
            ("Neue Gesichtsgruppe",),
        )
        fallback = int(cursor.lastrowid)
        fallback_cluster_ids[proposed_cluster_id] = fallback
        return fallback

    @classmethod
    def _create_image(
        cls,
        cursor,
        hashed: HashedImage,
    ) -> int:
        """Create a canonical image row for new content.

        Args:
            cursor: SQLite cursor used for image lookups and writes.
            hashed: Hashed image metadata.

        Returns:
            Newly created canonical image identifier.
        """
        cls._detach_path(cursor, hashed.normalized_path)
        directory = os.path.dirname(hashed.normalized_path)
        filename = os.path.basename(hashed.normalized_path)
        cursor.execute(
            """
            INSERT INTO image(
                path, directory, filename, content_hash, processed_at
            )
            VALUES (?, ?, ?, ?, NULL)
            """,
            (
                hashed.normalized_path,
                directory,
                filename,
                hashed.content_hash,
            ),
        )
        image_id = cursor.lastrowid
        return image_id

    @classmethod
    def _attach_location(
        cls,
        cursor,
        connection,
        image_id: int,
        hashed: HashedImage,
    ) -> None:
        """Attach a discovered path to canonical content.

        Args:
            cursor: SQLite cursor used for location writes.
            connection: SQLite connection controlling transactions.
            image_id: Canonical image identifier.
            hashed: Hashed path metadata to attach.
        """
        existing = cursor.execute(
            "SELECT image_id FROM image_location WHERE path = ?",
            (hashed.normalized_path,),
        ).fetchone()
        if existing and existing["image_id"] == image_id:
            cursor.execute(
                """
                UPDATE image_location
                SET created_at = ?, file_size = ?, modified_at_ns = ?
                WHERE path = ?
                """,
                (
                    hashed.created_at,
                    hashed.file_size,
                    hashed.modified_at_ns,
                    hashed.normalized_path,
                ),
            )
            connection.commit()
            return
        if existing:
            cls._detach_path(cursor, hashed.normalized_path)

        cursor.execute(
            """
            INSERT INTO image_location(
                image_id, path, directory, filename, created_at,
                file_size, modified_at_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                hashed.normalized_path,
                os.path.dirname(hashed.normalized_path),
                os.path.basename(hashed.normalized_path),
                hashed.created_at,
                hashed.file_size,
                hashed.modified_at_ns,
            ),
        )
        connection.commit()
        invalidate_image_query_cache()

    @staticmethod
    def _detach_path(cursor, normalized_path: str) -> None:
        """Detach a path safely from content it no longer represents.

        Args:
            cursor: SQLite cursor used for canonical path maintenance.
            normalized_path: Existing location path to detach.
        """
        existing = cursor.execute(
            """
            SELECT location.image_id, i.path AS canonical_path
            FROM image_location location
            JOIN image i ON i.id = location.image_id
            WHERE location.path = ?
            """,
            (normalized_path,),
        ).fetchone()
        if not existing:
            return

        remaining = cursor.execute(
            """
            SELECT path, directory, filename
            FROM image_location
            WHERE image_id = ? AND path != ?
            ORDER BY path
            LIMIT 1
            """,
            (existing["image_id"], normalized_path),
        ).fetchone()
        cursor.execute(
            "DELETE FROM image_location WHERE path = ?",
            (normalized_path,),
        )
        if remaining:
            if existing["canonical_path"] == normalized_path:
                cursor.execute(
                    """
                    UPDATE image
                    SET path = ?, directory = ?, filename = ?
                    WHERE id = ?
                    """,
                    (
                        remaining["path"],
                        remaining["directory"],
                        remaining["filename"],
                        existing["image_id"],
                    ),
                )
        else:
            cursor.execute(
                "DELETE FROM image WHERE id = ?",
                (existing["image_id"],),
            )
            cursor.execute(
                """
                DELETE FROM cluster
                WHERE NOT EXISTS (
                    SELECT 1 FROM face WHERE face.cluster_id = cluster.id
                )
                """
            )

    @classmethod
    def _prune_stale_locations(
        cls,
        cursor,
        connection,
        image_id: int,
        expected_hash: str,
        verified_paths: Set[str],
    ) -> None:
        """Remove missing or changed locations for known content.

        This validation runs only when the same content is discovered at a
        genuinely new path. Paths hashed during the current import are trusted;
        older paths are checked for existence and matching content.

        Args:
            cursor: SQLite cursor used for location maintenance.
            connection: SQLite connection controlling the transaction.
            image_id: Canonical image whose locations should be validated.
            expected_hash: Content hash assigned to the canonical image.
            verified_paths: Paths already hashed during the current import.
        """
        rows = cursor.execute(
            """
            SELECT path
            FROM image_location
            WHERE image_id = ?
            ORDER BY path
            """,
            (image_id,),
        ).fetchall()
        for row in rows:
            path = row["path"]
            if path in verified_paths:
                continue
            try:
                location_is_valid = (
                    os.path.isfile(path) and calculate_file_hash(path) == expected_hash
                )
            except OSError:
                location_is_valid = False
            if not location_is_valid:
                cls._detach_path(cursor, path)
        connection.commit()


def get_import_worker_count(
    compute_mode: str,
    cpu_count: Optional[int] = None,
) -> int:
    """Choose a bounded image preparation worker count.

    Args:
        compute_mode: Active inference mode, either ``gpu`` or ``cpu``.
        cpu_count: Optional CPU count override used by tests.

    Returns:
        Number of image preparation workers.
    """
    configured = os.getenv("FACE_MANAGER_IMPORT_WORKERS")
    if configured is not None:
        try:
            return max(1, int(configured))
        except ValueError:
            pass

    available_cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    if compute_mode == "gpu":
        return min(8, available_cpus, max(2, available_cpus // 2))
    return min(4, available_cpus, max(1, available_cpus // 2))


_default_processor = ImportProcessor()


def process_folder(
    folder_path: str,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Process a folder through the shared import processor.

    Args:
        folder_path: Existing folder to import recursively.
        progress_callback: Optional callback receiving progress updates.
        cancel_event: Optional cooperative cancellation event.
    """
    _default_processor.process(
        folder_path,
        progress_callback or (lambda update: None),
        cancel_event or threading.Event(),
    )
