"""Reusable face import pipeline components."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Optional, Set, Tuple

import numpy as np
from PIL import Image

from ..db.schema import calculate_file_hash, get_conn
from ..models.face_model import FaceModel, get_compute_mode
from .storage import load_all_embeddings

if TYPE_CHECKING:
    from ..models.clustering import FaceClustering

ProgressCallback = Callable[[dict], None]


class ImportCancelled(Exception):
    """Signal that an import job was cancelled by the user."""


@dataclass
class HashedImage:
    """Hold identity metadata for one discovered image.

    Args:
        path: Original filesystem path.
        normalized_path: Platform-normalized path used for database lookups.
        content_hash: SHA-256 digest used for duplicate detection.
    """

    path: Path
    normalized_path: str
    content_hash: str


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
        content_hash = calculate_file_hash(normalized_path)
        return HashedImage(path, normalized_path, content_hash)

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
                if cancel_event and cancel_event.is_set():
                    break
                try:
                    path = next(iterator)
                except StopIteration:
                    break
                pending.append((path, executor.submit(self.hash_image, path)))

            while pending:
                path, future = pending.popleft()
                if not cancel_event or not cancel_event.is_set():
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
                embeddings, cluster_ids = load_all_embeddings()
                if embeddings.size > 0:
                    self._clusterer.load_existing(embeddings, cluster_ids)
                self._clusterer_loaded = True
            return self._clusterer


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
            raise FileNotFoundError(
                f"Import folder no longer exists: {folder_path}"
            )

        image_paths = self._find_images(folder)
        progress_callback(
            {
                "total_images": len(image_paths),
                "processed_images": 0,
                "total_faces": 0,
                "processed_faces": 0,
            }
        )

        conn = get_conn()
        try:
            cursor = conn.cursor()
            worker_count = get_import_worker_count(get_compute_mode())
            preparer = ImagePreparer(worker_count)
            content_groups: OrderedDict[str, list[HashedImage]] = OrderedDict()
            completed_images = 0

            for image_path, future in preparer.iter_hashed(
                image_paths, cancel_event
            ):
                self._raise_if_cancelled(cancel_event)
                try:
                    hashed = future.result()
                    content_groups.setdefault(hashed.content_hash, []).append(
                        hashed
                    )
                except ImportCancelled:
                    raise
                except Exception as exc:
                    progress_callback(
                        {"last_error": f"{image_path}: {exc}"}
                    )
                    completed_images += 1
                    progress_callback(
                        {"processed_images": completed_images}
                    )

            for content_hash, locations in content_groups.items():
                self._raise_if_cancelled(cancel_event)
                matching_content = cursor.execute(
                    """
                    SELECT id, processed_at
                    FROM image
                    WHERE content_hash = ?
                    """,
                    (content_hash,),
                ).fetchone()

                if matching_content and matching_content["processed_at"]:
                    known_paths = {
                        row["path"]
                        for row in cursor.execute(
                            """
                            SELECT path
                            FROM image_location
                            WHERE image_id = ?
                            """,
                            (matching_content["id"],),
                        ).fetchall()
                    }
                    discovered_paths = {
                        hashed.normalized_path for hashed in locations
                    }
                    has_new_location = bool(
                        discovered_paths - known_paths
                    )
                    for hashed in locations:
                        self._attach_location(
                            cursor,
                            conn,
                            matching_content["id"],
                            hashed,
                        )
                        completed_images += 1
                        progress_callback(
                            {"processed_images": completed_images}
                        )
                    if has_new_location:
                        self._prune_stale_locations(
                            cursor,
                            conn,
                            matching_content["id"],
                            content_hash,
                            discovered_paths,
                        )
                    continue

                primary = locations[0]
                try:
                    image_id = (
                        matching_content["id"]
                        if matching_content
                        else self._create_image(cursor, conn, primary)
                    )
                    self._attach_location(
                        cursor, conn, image_id, primary
                    )
                    self._raise_if_cancelled(cancel_event)
                    model = self.resources.get_model()
                    clusterer = self.resources.get_clusterer()
                    image_np = preparer.decode(primary.path)
                    self._process_image(
                        cursor,
                        conn,
                        image_id,
                        image_np,
                        model,
                        clusterer,
                        progress_callback,
                    )
                    for duplicate in locations[1:]:
                        self._attach_location(
                            cursor, conn, image_id, duplicate
                        )
                except ImportCancelled:
                    raise
                except Exception as exc:
                    conn.rollback()
                    progress_callback(
                        {"last_error": f"{primary.path}: {exc}"}
                    )
                finally:
                    completed_images += len(locations)
                    progress_callback(
                        {"processed_images": completed_images}
                    )

            self._raise_if_cancelled(cancel_event)
        finally:
            conn.close()

    @classmethod
    def _find_images(cls, folder: Path) -> list[Path]:
        """Find supported images below a folder.

        Args:
            folder: Root directory to scan recursively.

        Returns:
            Sorted image paths for deterministic queue processing.
        """
        return sorted(
            path
            for path in folder.rglob("*")
            if path.suffix.lower() in cls.IMAGE_SUFFIXES
        )

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event) -> None:
        """Raise when cancellation has been requested.

        Args:
            cancel_event: Event carrying the cancellation request.

        Raises:
            ImportCancelled: If the event is set.
        """
        if cancel_event.is_set():
            raise ImportCancelled()

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
        cursor.execute("DELETE FROM face WHERE image_id = ?", (image_id,))
        faces = model.detect_and_embed(image_np)
        for face in faces:
            x1, y1, width, height = face["bbox"]
            embedding = face["embedding"]
            cluster_ids, _ = clusterer.add_and_assign(
                np.expand_dims(embedding, axis=0)
            )
            cluster_id = int(cluster_ids[0])
            cursor.execute(
                "INSERT OR IGNORE INTO cluster(id, label) VALUES (?, ?)",
                (cluster_id, f"Cluster {cluster_id}"),
            )
            cursor.execute(
                """
                INSERT INTO face(
                    image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                    cluster_id, embedding
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image_id,
                    float(x1),
                    float(y1),
                    float(width),
                    float(height),
                    cluster_id,
                    embedding.astype("float32").tobytes(),
                ),
            )

        cursor.execute(
            "UPDATE image SET processed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (image_id,),
        )
        connection.commit()
        progress_callback(
            {
                "total_faces_increment": len(faces),
                "processed_faces_increment": len(faces),
            }
        )

    @classmethod
    def _create_image(
        cls,
        cursor,
        connection,
        hashed: HashedImage,
    ) -> int:
        """Create a canonical image row for new content.

        Args:
            cursor: SQLite cursor used for image lookups and writes.
            connection: SQLite connection used to release write locks.
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
        connection.commit()
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
            return
        if existing:
            cls._detach_path(cursor, hashed.normalized_path)

        cursor.execute(
            """
            INSERT INTO image_location(
                image_id, path, directory, filename
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                image_id,
                hashed.normalized_path,
                os.path.dirname(hashed.normalized_path),
                os.path.basename(hashed.normalized_path),
            ),
        )
        connection.commit()

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
                    os.path.isfile(path)
                    and calculate_file_hash(path) == expected_hash
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
        return min(4, available_cpus, max(2, available_cpus // 3))
    return min(2, max(1, available_cpus // 4))


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
