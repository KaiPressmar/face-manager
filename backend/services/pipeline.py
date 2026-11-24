"""Reusable face import pipeline components."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Optional, Set, Tuple

import numpy as np
from PIL import Image, ImageFile

from ..db.schema import HASH_CHUNK_SIZE, calculate_file_hash, get_conn, get_file_created_at
from ..error_logging import configure_error_logging
from ..models.face_model import FaceModel, get_compute_mode
from .face_thumbnails import delete_face_thumbnail, write_face_thumbnails
from .image_thumbnails import write_image_thumbnail
from .preview_cache import get_image_geometry
from .geo import extract_image_metadata, record_image_metadata
from .filesystem_paths import (
    filesystem_path as _filesystem_path,
    is_confirmed_missing,
    stored_path as _stored_filesystem_path,
)
from .storage import (
    FACE_REVIEW_STATUS_ACTIVE,
    get_cluster_distance_threshold,
    invalidate_image_query_cache,
    load_all_embeddings,
)

configure_error_logging()
logger = logging.getLogger("face_manager.pipeline")

# Cameras and synchronization tools sometimes leave JPEG files without their
# final marker even though all pixel data is available. Recover those files.
ImageFile.LOAD_TRUNCATED_IMAGES = True

if TYPE_CHECKING:
    from ..models.clustering import FaceClustering

ProgressCallback = Callable[[dict], None]

_PROCESSING_SLOT_LOCK = threading.Lock()
_IMPORT_INDEX_LOCK = threading.Lock()
_PROCESSING_SLOT_COUNT = 1
_PROCESSING_SLOT_SEMAPHORE: threading.BoundedSemaphore = threading.BoundedSemaphore(1)


class ImportCancelled(Exception):
    """Signal that an import job was cancelled by the user."""


class ImportFailed(RuntimeError):
    """Signal that a whole import could not complete."""


class SourceChangedError(OSError):
    """Signal that a file no longer matches the version selected for import."""


def _file_identity(source_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        source_stat.st_dev,
        source_stat.st_ino,
        source_stat.st_size,
        source_stat.st_mtime_ns,
        source_stat.st_ctime_ns,
    )


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
    identity: tuple[int, int, int, int, int]
    metadata: tuple[float | None, float | None, str | None]
    geometry: tuple[int, int, int] | None = None

    def verify_current(self) -> None:
        source = _filesystem_path(self.normalized_path)
        if _file_identity(os.stat(source)) != self.identity:
            raise SourceChangedError("Die Datei wurde während des Imports geändert.")


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
        filesystem_path = _filesystem_path(normalized_path)
        digest = hashlib.sha256()
        with open(filesystem_path, "rb") as source:
            stat_result = os.fstat(source.fileno())
            identity = _file_identity(stat_result)
            while chunk := source.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
            source.seek(0)
            metadata = extract_image_metadata(source)
            if _file_identity(os.fstat(source.fileno())) != identity:
                raise SourceChangedError("Die Datei wurde während des Imports geändert.")
        result = HashedImage(
            path,
            normalized_path,
            digest.hexdigest(),
            get_file_created_at(filesystem_path, stat_result),
            stat_result.st_size,
            stat_result.st_mtime_ns,
            identity,
            metadata,
        )
        result.verify_current()
        return result

    @staticmethod
    def decode(
        path: Path, expected: HashedImage | None = None
    ) -> np.ndarray:
        """Decode one image into RGB pixels.

        Args:
            path: Image path to decode.

        Returns:
            RGB image represented as a NumPy array.
        """
        with open(_filesystem_path(path), "rb") as source:
            if expected is not None and _file_identity(os.fstat(source.fileno())) != expected.identity:
                raise SourceChangedError("Die Datei wurde während des Imports geändert.")
            with Image.open(source) as image:
                if expected is not None:
                    expected.geometry = get_image_geometry(image)
                pixels = np.asarray(image.convert("RGB"))
            if expected is not None and _file_identity(os.fstat(source.fileno())) != expected.identity:
                raise SourceChangedError("Die Datei wurde während des Imports geändert.")
        if expected is not None:
            expected.verify_current()
        return pixels

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

    def reset_clusterer(self, expected: FaceClustering | None = None) -> None:
        """Reload from the database on next use, optionally only for one index."""
        with self._lock:
            if expected is None or self._clusterer is expected:
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
        if not os.path.isdir(_filesystem_path(folder)):
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
            content_groups: dict[str, set[str]] = {}
            paths_to_hash, unchanged_paths = self._partition_unchanged_paths(
                cursor,
                image_paths,
                cancel_event,
            )
            completed_images = len(unchanged_paths)
            hashed_images = len(unchanged_paths)
            processing_started = False
            successful_images = len(unchanged_paths)
            last_error = None
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
                    if not self._attach_processed_content(cursor, conn, hashed):
                        image_np = preparer.decode(hashed.path, hashed)
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

                        if not resources_loaded:
                            progress_callback({"stage": "loading_model", "current_file": None})
                            try:
                                model = self.resources.get_model()
                            except Exception as exc:
                                raise ImportFailed(f"Die Gesichtserkennung konnte nicht vorbereitet werden. {exc}") from exc

                        def report_index_ready():
                            nonlocal resources_loaded
                            if not resources_loaded:
                                resources_loaded = True
                                progress_callback(
                                    {
                                        "stage": "processing",
                                        "stage_current": completed_images,
                                        "stage_total": len(image_paths),
                                    }
                                )

                        if not resources_loaded:
                            progress_callback({"stage": "loading_index"})
                        # Keep filesystem reads and model inference ahead of SQLite writes.
                        self._raise_if_cancelled(cancel_event)
                        progress_callback({"current_file": str(hashed.path)})
                        self._process_image(
                            cursor,
                            conn,
                            None,
                            image_np,
                            model,
                            None,
                            progress_callback,
                            pending_source=hashed,
                            on_index_ready=report_index_ready,
                        )
                    content_groups.setdefault(hashed.content_hash, set()).add(hashed.normalized_path)
                    successful_images += 1
                except (ImportCancelled, ImportFailed):
                    raise
                except OSError as exc:
                    conn.rollback()
                    logger.warning("Skipping unavailable or unreadable image %s: %s", image_path, exc)
                    last_error = f"{image_path}: {exc}"
                    progress_callback({"last_error": last_error})
                except Exception as exc:
                    conn.rollback()
                    logger.exception("Import processing failed for %s", image_path)
                    last_error = f"{image_path}: {exc}"
                    progress_callback({"last_error": last_error})
                finally:
                    completed_images += 1
                    hashed_images += 1
                    progress_callback(
                        {
                            "current_file": str(image_path),
                            "hashed_images": hashed_images,
                            "processed_images": completed_images,
                            "stage_current": completed_images if processing_started else hashed_images,
                        }
                    )

            self._raise_if_cancelled(cancel_event)
            if image_paths and successful_images == 0:
                raise ImportFailed(f"Kein Bild konnte hinzugefügt werden. {last_error or ''}".strip())

            for content_hash, discovered_paths in content_groups.items():
                self._raise_if_cancelled(cancel_event)
                matching_content = self._find_content(cursor, content_hash)
                if matching_content and matching_content["processed_at"]:
                    self._prune_stale_locations(
                        cursor, conn, matching_content["id"], content_hash, discovered_paths
                    )

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
        for root, directories, filenames in os.walk(_filesystem_path(folder)):
            directories.sort()
            filenames.sort()
            if cancel_event is not None:
                cls._raise_if_cancelled(cancel_event)
            for filename in filenames:
                path = Path(_stored_filesystem_path(root)) / filename
                if path.suffix.lower() in cls.IMAGE_SUFFIXES:
                    image_paths.append(path)
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage_current": len(image_paths),
                        "current_file": _stored_filesystem_path(root),
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
        """Skip hashing when a processed location still has its imported identity."""
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
                       location.modified_at_ns, location.file_device, location.file_inode,
                       location.file_changed_at_ns, image.processed_at, image.content_hash
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
                or not known["content_hash"]
                or known["file_size"] is None
                or known["modified_at_ns"] is None
                or known["file_device"] is None
                or known["file_inode"] is None
                or known["file_changed_at_ns"] is None
            ):
                needs_hash.append(path)
                continue
            try:
                stat_result = os.stat(_filesystem_path(normalized_path))
            except OSError:
                needs_hash.append(path)
                continue
            identity = (
                int(known["file_device"]), int(known["file_inode"]),
                int(known["file_size"]), int(known["modified_at_ns"]),
                int(known["file_changed_at_ns"]),
            )
            if identity == _file_identity(stat_result):
                unchanged.append(path)
            else:
                needs_hash.append(path)
        return needs_hash, unchanged

    def _process_image(
        self,
        cursor,
        connection,
        image_id: int | None,
        image_np: np.ndarray,
        model: FaceModel,
        clusterer: FaceClustering | None,
        progress_callback: ProgressCallback,
        *,
        pending_source: HashedImage | None = None,
        on_index_ready: Callable[[], None] | None = None,
    ) -> None:
        """Run inference, then serialize index and database changes together."""
        faces = model.detect_and_embed(image_np)
        distance_threshold = get_cluster_distance_threshold()
        with _IMPORT_INDEX_LOCK:
            # Look up the index here, after inference. A preceding importer
            # may have rolled back and discarded its in-memory proposals.
            if clusterer is None:
                try:
                    clusterer = self.resources.get_clusterer()
                except Exception as exc:
                    raise ImportFailed(f"Die Gesichtserkennung konnte nicht vorbereitet werden. {exc}") from exc
            if on_index_ready is not None:
                on_index_ready()
            committed = self._commit_faces(
                cursor, connection, image_id, faces, clusterer, distance_threshold, pending_source
            )

        invalidate_image_query_cache()
        if committed is None:
            return
        committed_image_id, existing_face_ids, thumbnail_faces = committed
        for face_id in existing_face_ids:
            delete_face_thumbnail(face_id)
        if pending_source is not None and pending_source.geometry is not None:
            try:
                with Image.fromarray(image_np) as raw:
                    orientation = pending_source.geometry[0]
                    write_image_thumbnail(
                        committed_image_id, pending_source.content_hash, raw, orientation
                    )
                    if thumbnail_faces:
                        write_face_thumbnails(
                            pending_source.content_hash, thumbnail_faces, raw, orientation
                        )
            except Exception:
                logger.warning("Could not prepare previews for image %s", committed_image_id, exc_info=True)
        progress_callback(
            {
                "total_faces_increment": len(faces),
                "processed_faces_increment": len(faces),
            }
        )

    def _commit_faces(
        self,
        cursor,
        connection,
        image_id: int | None,
        faces,
        clusterer: FaceClustering,
        distance_threshold: float,
        pending_source: HashedImage | None,
    ) -> tuple[int, list[int], list[tuple[int, tuple[int, int, int, int]]]] | None:
        """Persist prepared faces, or only attach the path for a raced duplicate."""
        index_dirty = False
        try:
            if pending_source is not None:
                pending_source.verify_current()
                cursor.execute("BEGIN IMMEDIATE")
                matching = self._find_content(cursor, pending_source.content_hash)
                image_id = matching["id"] if matching else self._create_image(cursor, pending_source)
                self._attach_location(cursor, image_id, pending_source)
                if matching and matching["processed_at"]:
                    pending_source.verify_current()
                    connection.commit()
                    return None
            assert image_id is not None

            existing_face_ids = [
                int(row["id"])
                for row in cursor.execute(
                    "SELECT id FROM face WHERE image_id = ?", (image_id,)
                ).fetchall()
            ]
            cursor.execute("DELETE FROM face WHERE image_id = ?", (image_id,))
            fallback_cluster_ids: dict[int, int] = {}
            thumbnail_faces = []
            for face in faces:
                embedding = face["embedding"]
                index_dirty = True
                cluster_ids, _ = clusterer.add_and_assign(
                    np.expand_dims(embedding, axis=0),
                    distance_threshold=distance_threshold,
                    # Confirmed person matches are surfaced as suggestions.
                    allow_person_matches=False,
                )
                cluster_id = self._resolve_import_cluster_id(
                    cursor, int(cluster_ids[0]), fallback_cluster_ids
                )
                x1, y1, width, height = face["bbox"]
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
                thumbnail_faces.append((int(cursor.lastrowid), tuple(map(int, face["bbox"]))))
            if pending_source is not None and pending_source.geometry is not None:
                orientation, width, height = pending_source.geometry
                cursor.execute(
                    "UPDATE image SET raw_width = ?, raw_height = ?, exif_orientation = ? WHERE id = ?",
                    (width, height, orientation, image_id),
                )
            cursor.execute(
                "UPDATE image SET processed_at = CURRENT_TIMESTAMP WHERE id = ?", (image_id,)
            )
            if pending_source is not None:
                pending_source.verify_current()
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            finally:
                if index_dirty:
                    self.resources.reset_clusterer(clusterer)
            raise
        return int(image_id), existing_face_ids, thumbnail_faces

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
        if cls._legacy_processed_location(cursor, hashed.normalized_path) is not None:
            raise SourceChangedError("Der Inhalt des älteren Bibliothekseintrags ist nicht sicher überprüfbar.")
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

    @staticmethod
    def _find_content(cursor, content_hash: str):
        return cursor.execute(
            "SELECT id, processed_at FROM image WHERE content_hash = ?", (content_hash,)
        ).fetchone()

    @classmethod
    def _attach_processed_content(cls, cursor, connection, hashed: HashedImage) -> bool:
        """Attach a duplicate only when its canonical image still exists."""
        matching = cls._find_content(cursor, hashed.content_hash)
        legacy_location = cls._legacy_processed_location(cursor, hashed.normalized_path)
        if (not matching or not matching["processed_at"]) and legacy_location is None:
            return False
        cursor.execute("BEGIN IMMEDIATE")
        matching = cls._find_content(cursor, hashed.content_hash)
        legacy_location = cls._legacy_processed_location(cursor, hashed.normalized_path)
        if legacy_location is not None:
            saved_identity = tuple(legacy_location)[1:]
            if matching is not None or saved_identity != hashed.identity:
                raise SourceChangedError("Der Inhalt des älteren Bibliothekseintrags ist nicht sicher überprüfbar.")
            hashed.verify_current()
            cursor.execute(
                "UPDATE image SET content_hash = ? WHERE id = ?",
                (hashed.content_hash, legacy_location["image_id"]),
            )
            matching = cls._find_content(cursor, hashed.content_hash)
        if not matching or not matching["processed_at"]:
            connection.rollback()
            return False
        hashed.verify_current()
        cls._attach_location(cursor, matching["id"], hashed)
        hashed.verify_current()
        connection.commit()
        invalidate_image_query_cache()
        return True

    @staticmethod
    def _legacy_processed_location(cursor, path: str):
        return cursor.execute(
            """
            SELECT location.image_id, location.file_device, location.file_inode,
                   location.file_size, location.modified_at_ns, location.file_changed_at_ns
            FROM image_location location
            JOIN image ON image.id = location.image_id
            WHERE location.path = ? AND image.content_hash IS NULL AND image.processed_at IS NOT NULL
            """,
            (path,),
        ).fetchone()

    @classmethod
    def _attach_location(
        cls,
        cursor,
        image_id: int,
        hashed: HashedImage,
    ) -> None:
        """Attach a discovered path inside the caller's transaction.

        Args:
            cursor: SQLite cursor used for location writes.
            image_id: Canonical image identifier.
            hashed: Hashed path metadata to attach.
        """
        record_image_metadata(cursor, image_id, hashed.normalized_path, metadata=hashed.metadata)
        device, inode, _, _, changed_at = hashed.identity
        # A platform can expose unsigned or 128-bit file IDs. SQLite INTEGER
        # parameters are signed 64-bit, so unsupported IDs are rehashed on import.
        device = device if -(2**63) <= device < 2**63 else None
        inode = inode if -(2**63) <= inode < 2**63 else None
        identity_values = (hashed.file_size, hashed.modified_at_ns, device, inode, changed_at)
        existing = cursor.execute(
            "SELECT image_id FROM image_location WHERE path = ?",
            (hashed.normalized_path,),
        ).fetchone()
        if existing and existing["image_id"] == image_id:
            cursor.execute(
                """
                UPDATE image_location
                SET created_at = ?, file_size = ?, modified_at_ns = ?, file_device = ?,
                    file_inode = ?, file_changed_at_ns = ?
                WHERE path = ?
                """,
                (
                    hashed.created_at,
                    *identity_values,
                    hashed.normalized_path,
                ),
            )
            return
        if existing:
            if cls._legacy_processed_location(cursor, hashed.normalized_path) is not None:
                raise SourceChangedError("Der Inhalt des älteren Bibliothekseintrags ist nicht sicher überprüfbar.")
            cls._detach_path(cursor, hashed.normalized_path)

        cursor.execute(
            """
            INSERT INTO image_location(
                image_id, path, directory, filename, created_at,
                file_size, modified_at_ns, file_device, file_inode, file_changed_at_ns
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                image_id,
                hashed.normalized_path,
                os.path.dirname(hashed.normalized_path),
                os.path.basename(hashed.normalized_path),
                hashed.created_at,
                *identity_values,
            ),
        )

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
            SELECT id, path, file_size, modified_at_ns, file_device, file_inode, file_changed_at_ns
            FROM image_location
            WHERE image_id = ?
            ORDER BY path
            """,
            (image_id,),
        ).fetchall()
        stale_locations = []
        for row in rows:
            path = row["path"]
            if path in verified_paths:
                continue
            try:
                source = _filesystem_path(path)
                before = os.stat(source)
                if not stat.S_ISREG(before.st_mode):
                    continue
                changed = calculate_file_hash(source) != expected_hash
                if changed and _file_identity(os.stat(source)) == _file_identity(before):
                    stale_locations.append(row)
            except FileNotFoundError:
                if is_confirmed_missing(path, row["file_device"]):
                    stale_locations.append(row)
            except OSError:
                continue
        if not stale_locations:
            return
        cursor.execute("BEGIN IMMEDIATE")
        removed = False
        try:
            for row in stale_locations:
                current = cursor.execute(
                    """
                    SELECT location.id, location.path, location.file_size, location.modified_at_ns,
                           location.file_device, location.file_inode, location.file_changed_at_ns
                    FROM image_location location
                    JOIN image ON image.id = location.image_id
                    WHERE location.id = ? AND image.id = ? AND image.content_hash = ?
                      AND EXISTS (
                          SELECT 1 FROM image_location alternate
                          WHERE alternate.image_id = image.id AND alternate.id != location.id
                      )
                    """,
                    (row["id"], image_id, expected_hash),
                ).fetchone()
                if current is not None and tuple(current) == tuple(row):
                    cls._detach_path(cursor, row["path"])
                    removed = True
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        if removed:
            invalidate_image_query_cache()


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
