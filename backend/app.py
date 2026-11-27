import asyncio
import hashlib
import hmac
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from collections import OrderedDict
from concurrent.futures import Future
from functools import partial, wraps
from contextlib import asynccontextmanager, closing, nullcontext
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from queue import Full, Queue
from typing import Annotated, List, Literal

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .changelog import ChangelogError, find_release, load_changelog, released_versions
from .config import (
    APP_VERSION,
    DB_PATH,
    get_build_variant,
    get_changelog_path,
    get_data_root,
    get_error_log_path,
    get_frontend_dist_dir,
)
from .db.schema import get_conn, init_db
from .error_logging import (
    DEFAULT_FILE_LOG_LEVEL,
    apply_persisted_file_log_level,
    configure_error_logging,
    install_global_exception_hooks,
)
from .models.face_model import get_runtime_provider_details
from .services import albums
from .services.archive_repair import (
    apply_archive_repair,
    preview_archive_repair,
    register_archive_repair_snapshot,
    undo_archive_repair,
)
from .services.desktop import (
    is_windows_host,
    is_wsl_host,
    normalize_import_folder_path,
    open_file_location,
    pick_folder,
    to_display_path,
)
from .services.face_box_suggestions import (
    FaceBoxSuggestionBusy,
    FaceBoxSuggestionPhotoUnavailable,
    FaceBoxSuggestionUnavailable,
    suggest_face_boxes,
)
from .services.face_thumbnails import ensure_face_thumbnail, get_cached_face_thumbnail, write_face_thumbnail
from .services.image_thumbnails import (
    ensure_image_thumbnail, get_cached_image_thumbnail, read_legacy_preview_geometry, write_image_thumbnail,
)
from .services.manual_face_preview import (
    PREVIEW_ORIGINAL_HEADER,
    PREVIEW_PROOF_HEADER,
    FacePreview,
    find_preview_source,
    issue_preview_proof,
    read_preview_proof,
    render_face_preview,
)
from .services.preview_cache import correct_bbox_for_orientation, decode_verified_preview, read_verified_preview_geometry
from .services.preview_cache_budget import (
    DEFAULT_PREVIEW_CACHE_LIMIT_MIB,
    get_preview_cache_policy,
    read_preview_bytes,
    schedule_preview_cache_cleanup,
    set_preview_cache_limit_mib,
    validate_preview_cache_limit_mib,
)
from .services.filesystem_paths import filesystem_path
from .services.face_thumbnail_warmup import FaceThumbnailWarmupQueue
from .services.face_action_history import FaceActionConflict, face_action_history, replay_face_action
from .services.cache import app_cache
from .services.autocluster_queue import (
    AutoClusterQueue,
    PRIORITY_IDLE_RECLUSTER,
    PRIORITY_MANUAL_RECLUSTER,
    PRIORITY_STARTUP_REPAIR,
    PRIORITY_VERSION_UPGRADE,
)
from .services.events import TrailingThrottle, event_hub, publish_cluster_change
from .services.import_queue import ImportQueue
from .services.idle_recluster import IdleReclusterScheduler
from .services.shared_libraries import shared_library_preview
from .services.shared_sync_import import SharedImportAnalysis
from .services.pipeline import ImportCancelled, ImportProcessor
from .services.image_path_cleanup import ImagePathCleanup
from .services.person_suggestion_worker import PersonSuggestionWorker
from .services.background_activities import background_activities
from .services.geo import MAP_GRID_SIZE, PHOTO_METADATA_ACTIVITY, geo_backfill, list_map_images, list_map_points, list_selected_map_images
from .services.geo_query import GeoBounds, GeoPhotoSelection, validate_photo_selection
from .services.journey import JourneyDetail, get_journey
from .services.observed_places import list_observed_places
from .services.ui_preferences import load_ui_preferences, update_ui_preferences, validate_ui_preferences
from .services.update_manager import (
    UpdateError,
    parse_semver,
    schedule_process_exit,
    update_manager,
)
from .services.storage import (
    DEFAULT_AUTOMATIC_UPDATE_CHECKS,
    DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
    DEFAULT_CLUSTER_STRICTNESS,
    DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR,
    DEFAULT_FILENAME_PERSON_JOINER,
    DEFAULT_UI_THEME,
    ManualFaceConflictError,
    StaleFaceAssignmentError,
    StalePersonSuggestionError,
    _safe_float,
    assign_cluster_to_person,
    accept_person_suggestion_assignments,
    build_filename_person_format_summary,
    build_folder_tree,
    count_filename_rename_candidates,
    delete_image,
    create_cluster_from_faces,
    create_manual_face,
    assign_faces_to_person,
    auto_tune_cluster_distance_threshold,
    get_faces_for_review_group,
    get_face_preview_source,
    get_image_detail_rows,
    get_image_preview_source,
    get_recorded_face_boxes,
    get_available_image_path,
    invalidate_image_query_cache,
    invalidate_person_suggestion_epoch,
    get_person_suggestion_epoch,
    is_person_suggestion_epoch_current,
    get_cluster_faces,
    get_cluster_overview,
    get_cluster_summary,
    get_cluster_distance_threshold,
    get_clustering_profile,
    get_applied_clustering_version,
    get_automatic_update_checks,
    get_last_seen_changelog_version,
    get_skipped_update_version,
    list_face_review_groups,
    get_file_log_level,
    get_ui_theme,
    get_filename_person_block_separator,
    get_filename_person_joiner,
    get_filename_person_suffix_format,
    list_image_locations,
    list_available_image_persons,
    list_filename_rename_candidates,
    MAX_PERSON_DIRECTORY_PAGE_SIZE,
    StalePersonRenameTargetError,
    get_person_directory_entry,
    get_person_faces,
    list_person_directory,
    list_images_page,
    list_cluster_summaries,
    list_persons,
    get_person_suggestion_snapshot,
    list_review_suggestions,
    mark_faces_with_review_status,
    dismiss_person_suggestion,
    dismiss_review_suggestion,
    accept_review_suggestions,
    normalize_face_review_group,
    normalize_face_status_filters,
    preview_image_location_renames,
    rename_cluster,
    rename_person,
    delete_person,
    rename_image_locations_to_match_people,
    restore_faces_to_manual_review,
    save_missing_image_geometry,
    remove_faces_from_cluster,
    count_reclusterable_faces,
    count_scoped_reclusterable_faces,
    recluster_all_active_faces,
    refresh_pending_person_suggestions,
    set_cluster_distance_threshold,
    set_clustering_strictness,
    set_applied_clustering_version,
    set_automatic_update_checks,
    set_last_seen_changelog_version,
    set_skipped_update_version,
    set_file_log_level,
    set_ui_theme,
    set_filename_person_block_separator,
    set_filename_person_joiner,
    set_filename_person_suffix_format,
)

configure_error_logging()
install_global_exception_hooks()
logger = logging.getLogger("face_manager.api")

init_db(scan_sources=False)

_import_activity_lock = threading.Lock()
_import_was_busy = False
_last_import_progress: dict[str, tuple[int, int]] = {}
_version_clustering_lock = threading.Lock()
_version_clustering_pending = False
_cluster_operation_lock = threading.RLock()
_imports_finalizing = 0
_database_restore_in_progress = False
_media_library_lock = threading.Lock()
_media_library_generation = 0
_pending_image_geometry: OrderedDict[tuple[int, str], tuple[int, int, int]] = OrderedDict()
_image_geometry_timer: threading.Timer | None = None
_IMAGE_GEOMETRY_RETRY_SECONDS = 1.0
_IMAGE_GEOMETRY_RETRY_BATCH = 32
_MAX_PENDING_IMAGE_GEOMETRY = 1024
PERSON_SUGGESTIONS_PENDING_HEADER = "X-Person-Suggestions-Pending"
ASSIGNMENT_LIBRARY_ID_HEADER = "X-Assignment-Library-Id"


def _describe_background_activity() -> str | None:
    """Describe background work for the UI. No longer gates any write."""
    if _imports_finalizing > 0:
        return (
            "Der Bildimport wird gerade sicher abgeschlossen und die Ansichten "
            "werden aktualisiert. Bitte warten Sie einen kurzen Moment."
        )
    import_snapshot = import_queue.snapshot()
    if isinstance(import_snapshot, dict) and (
        import_snapshot.get("running_count")
        or import_snapshot.get("queued_count")
        or import_snapshot.get("paused_count")
    ):
        return (
            "Ein Bildimport läuft oder wartet. Diese Änderung ist vorübergehend "
            "gesperrt, damit keine Zuordnungen verloren gehen. Bitte warten Sie, "
            "bis der Import abgeschlossen ist."
        )
    auto_cluster_snapshot = auto_cluster_queue.snapshot()
    task = (
        auto_cluster_snapshot.get("task")
        if isinstance(auto_cluster_snapshot, dict)
        else None
    )
    if task and task.get("status") in {"queued", "running", "paused", "cancelling"}:
        return (
            "Die Gesichtscluster werden gerade im Hintergrund aktualisiert. "
            "Diese Änderung ist vorübergehend gesperrt. Die Ansicht wird nach "
            "Abschluss automatisch neu geladen."
        )
    return None


# How long an interactive write waits for a background pass to step aside.
CLUSTER_YIELD_TIMEOUT_SECONDS = 2.0


def safe_cluster_mutation(function):
    """Give interactive writes priority over background clustering.

    Reclustering is a low-priority optimisation, so it must never block the
    user. SQLite (WAL) allows a single writer, so instead of rejecting the
    write we ask a running pass to stop at its next group boundary and hand
    over the writer slot. Nothing is lost: unfinished groups keep their dirty
    markers and are rebuilt on the next idle run.
    """
    @wraps(function)
    def guarded(*args, **kwargs):
        auto_cluster_queue.request_cancel(timeout=CLUSTER_YIELD_TIMEOUT_SECONDS)
        with _cluster_operation_lock:
            return function(*args, **kwargs)

    return guarded


def safe_import_enqueue(function):
    """Queue an import without waiting for background clustering to finish."""
    @wraps(function)
    def guarded(*args, **kwargs):
        auto_cluster_queue.request_cancel(timeout=CLUSTER_YIELD_TIMEOUT_SECONDS)
        with _cluster_operation_lock:
            return function(*args, **kwargs)

    return guarded


def _publish_imports() -> None:
    """Push the current import-queue snapshot to subscribed clients."""
    global _import_was_busy, _last_import_progress
    snapshot = import_queue.snapshot()
    event_hub.publish("imports", snapshot)
    busy = bool(
        snapshot.get("running_count")
        or snapshot.get("queued_count")
        or snapshot.get("paused_count")
    )
    current_progress = {
        str(job.get("id")): (
            int(job.get("processed_images") or 0),
            int(job.get("processed_faces") or 0),
        )
        for job in snapshot.get("jobs", [])
        if isinstance(job, dict) and job.get("id") is not None
    }
    with _import_activity_lock:
        became_idle = _import_was_busy and not busy
        library_grew = any(
            progress > _last_import_progress.get(job_id, (0, 0))
            for job_id, progress in current_progress.items()
        )
        _import_was_busy = busy
        _last_import_progress = current_progress
    if library_grew:
        _publish_background_cluster_progress_throttled()
    if became_idle:
        app_cache.clear()
        notify_clusters_changed("import_completed")
        if not schedule_version_clustering_upgrade():
            mark_cluster_assignments_dirty("import_completed")
        # Release any clustering task that was queued while the import ran and
        # let the thumbnail warmer start immediately instead of on its next poll.
        auto_cluster_queue.notify_ready()
        face_thumbnail_warmup_queue.wake()
        image_path_cleanup.schedule("import_completed", delay_seconds=15.0)


def _publish_autocluster() -> None:
    """Push the current autocluster snapshot to subscribed clients."""
    event_hub.publish("autocluster", auto_cluster_queue.snapshot())


def _publish_thumbnail_warmup() -> None:
    """Push the current thumbnail-warmup snapshot to subscribed clients."""
    event_hub.publish("thumbnail-warmup", face_thumbnail_warmup_queue.snapshot())


def _begin_import_finalization() -> None:
    """Block interactive writes before an import stops reporting as active."""
    global _imports_finalizing
    with _cluster_operation_lock:
        _imports_finalizing += 1


def _end_import_finalization() -> None:
    """Release writes only after import and cluster events were published."""
    global _imports_finalizing
    with _cluster_operation_lock:
        _imports_finalizing = max(0, _imports_finalizing - 1)
    # Finalization no longer blocks the writer; start any deferred clustering.
    auto_cluster_queue.notify_ready()
    person_suggestion_worker.wake()


def notify_clusters_changed(reason: str) -> None:
    """Broadcast that cluster/person/image data changed so clients refetch.

    Args:
        reason: Short label describing what triggered the change.
    """
    if reason != "background_progress":
        shared_library_preview.library_changed()
    _publish_active_cluster_change(reason)


def _publish_active_cluster_change(reason: str) -> None:
    with _media_library_lock:
        if not _database_restore_in_progress:
            publish_cluster_change(reason)


def _publish_background_cluster_progress() -> None:
    """Invalidate and broadcast a committed background-work checkpoint."""
    app_cache.clear()
    notify_clusters_changed("background_progress")


# Background workers emit progress far faster than the UI needs it, and each
# emission rebuilds a full snapshot. Coalesce those bursts to a few per second
# (transitions and the final state are always delivered by the trailing run).
_publish_imports_throttled = TrailingThrottle(_publish_imports, interval=0.2)
_publish_autocluster_throttled = TrailingThrottle(_publish_autocluster, interval=0.2)
_publish_background_cluster_progress_throttled = TrailingThrottle(
    _publish_background_cluster_progress,
    # A checkpoint invalidates several potentially large query caches. Keep
    # imports live, but batch adjacent images so the UI cannot turn a fast
    # import into a continuous stream of expensive overview rebuilds.
    interval=2.0,
)


def _notify_recluster_commit(_processed_faces: int, _total_faces: int) -> None:
    """Publish reclustering only after a group transaction is visible."""
    _publish_background_cluster_progress_throttled()


_shared_analysis = SharedImportAnalysis(
    shared_library_preview.analysis_share, available_shares=shared_library_preview.analysis_shares,
    on_reused=shared_library_preview.note_analysis_peer, dispatch=shared_library_preview.dispatch_analysis,
)
_import_processor = ImportProcessor(shared_analysis=_shared_analysis)

import_queue = ImportQueue(
    auto_start=False,
    processor=_import_processor,
    on_change=_publish_imports_throttled,
    on_before_terminal=_begin_import_finalization,
    on_after_terminal=_end_import_finalization,
)
def _handle_autocluster_success(_repaired_faces: int) -> None:
    """Refresh resources and continue a version migration waiting for idleness."""
    reset_import_resources()
    app_cache.clear()
    notify_clusters_changed("autocluster")
    # Newly rebuilt clusters mean new faces to preview; warm them right away.
    face_thumbnail_warmup_queue.wake()
    if _version_clustering_pending:
        schedule_version_clustering_upgrade()


def _reclustering_writer_available() -> bool:
    """Return whether the single SQLite writer is free for a clustering pass.

    Reclustering shares the writer with imports, so a queued clustering task
    must wait while an import is running, queued, or finalizing. It does not
    consult the auto-cluster queue itself, which serialises its own tasks.
    """
    if _imports_finalizing > 0 or _database_restore_in_progress:
        return False
    import_snapshot = import_queue.snapshot()
    return not (
        import_snapshot.get("running_count")
        or import_snapshot.get("queued_count")
        or import_snapshot.get("paused_count")
    )


auto_cluster_queue = AutoClusterQueue(
    on_success=_handle_autocluster_success,
    on_change=_publish_autocluster_throttled,
    ready_gate=_reclustering_writer_available,
)


def is_backend_idle_for_thumbnail_warmup() -> bool:
    """Return whether low-priority thumbnail warming may run."""
    if _database_restore_in_progress:
        return False
    import_snapshot = import_queue.snapshot()
    if import_snapshot.get("running_count", 0) > 0:
        return False
    if import_snapshot.get("queued_count", 0) > 0:
        return False
    if import_snapshot.get("paused_count", 0) > 0:
        return False

    auto_cluster_snapshot = auto_cluster_queue.snapshot()
    task = auto_cluster_snapshot.get("task")
    if task and task.get("status") in {"queued", "running", "paused", "cancelling"}:
        return False
    return True


face_thumbnail_warmup_queue = FaceThumbnailWarmupQueue(
    is_idle=is_backend_idle_for_thumbnail_warmup,
    on_change=_publish_thumbnail_warmup,
)


def is_backend_idle_for_path_cleanup() -> bool:
    """Keep filesystem validation behind all user-facing background work."""
    if not is_backend_idle_for_thumbnail_warmup():
        return False
    thumbnail_task = face_thumbnail_warmup_queue.snapshot().get("task") or {}
    return (
        thumbnail_task.get("status") not in {"queued", "running"}
        and not face_thumbnail_warmup_queue.has_active_batch()
    )


def _publish_image_path_cleanup(snapshot: dict) -> None:
    """Publish maintenance progress and refresh views after actual removals."""
    event_hub.publish("image-path-cleanup", snapshot)
    if snapshot.get("status") == "completed" and snapshot.get("removed_paths"):
        reset_import_resources()
        notify_clusters_changed("image_path_cleanup")


image_path_cleanup = ImagePathCleanup(
    is_idle=is_backend_idle_for_path_cleanup,
    on_change=_publish_image_path_cleanup,
)


def run_startup_repairs(reason: str = "startup") -> dict | None:
    """Schedule legacy inbox repair work without blocking app startup.

    The request is accepted even when an import is still resuming; the queue's
    readiness gate holds it until the writer is free.
    """
    task = auto_cluster_queue.start(reason, priority=PRIORITY_STARTUP_REPAIR)
    if task is not None:
        logger.info(
            "Scheduled auto-clustering repair task %s for %s stale inbox faces",
            task["id"],
            task["total_faces"],
        )
    return task


def schedule_full_recluster(
    reason: str = "cluster_assignment_change",
    scoped: bool = False,
) -> dict | None:
    """Schedule a rebuild of unassigned and per-person subclusters.

    The request is always accepted. When an import currently holds the writer
    the task is queued and starts automatically once the import finishes, so a
    user-triggered reclustering is never silently dropped.

    Args:
        scoped: Rebuild only the groups recorded as dirty instead of the whole
            library. Used for idle-triggered runs after assignment changes.
    """
    priority = PRIORITY_IDLE_RECLUSTER if scoped else PRIORITY_MANUAL_RECLUSTER
    task = auto_cluster_queue.start(
        reason,
        kind="full_recluster",
        count_callable=(
            count_scoped_reclusterable_faces if scoped else count_reclusterable_faces
        ),
        repair_callable=(
            partial(
                recluster_all_active_faces,
                scoped=True,
                commit_callback=_notify_recluster_commit,
            )
            if scoped
            else partial(
                recluster_all_active_faces,
                commit_callback=_notify_recluster_commit,
            )
        ),
        priority=priority,
    )
    if task is not None:
        logger.info(
            "Scheduled full reclustering task %s (%s) for %s faces",
            task["id"],
            task["status"],
            task["total_faces"],
        )
    return task


def _apply_version_clustering_upgrade(progress_callback=None) -> int:
    """Tune, rebuild, then atomically mark this software version as applied."""
    try:
        auto_tune_cluster_distance_threshold()
    except ValueError as exc:
        # A fresh or sparsely labelled installation cannot be calibrated yet.
        # Re-clustering with the safe current/default profile is still required.
        logger.info("Skipped version-start clustering auto-tune: %s", exc)
    except Exception:
        # Tuning is an optimization. A tuning defect must not prevent faces
        # from being rebuilt with the newest clustering implementation.
        logger.exception(
            "Version-start clustering auto-tune failed; continuing with current profile"
        )
    rebuilt_faces = recluster_all_active_faces(
        progress_callback=progress_callback,
        commit_callback=_notify_recluster_commit,
    )
    set_applied_clustering_version(APP_VERSION)
    logger.info(
        "Applied clustering algorithm for software version %s to %s faces",
        APP_VERSION,
        rebuilt_faces,
    )
    return rebuilt_faces


def schedule_version_clustering_upgrade() -> bool:
    """Ensure tuning and full clustering run once for each software version.

    Returns ``True`` when a migration is needed (scheduled, deferred, or
    completed for an empty installation), and ``False`` when this exact version
    was already applied.
    """
    global _version_clustering_pending
    with _cluster_operation_lock, _version_clustering_lock:
        if get_applied_clustering_version() == APP_VERSION:
            _version_clustering_pending = False
            return False

        import_snapshot = import_queue.snapshot()
        if (
            import_snapshot.get("running_count")
            or import_snapshot.get("queued_count")
            or import_snapshot.get("paused_count")
        ):
            _version_clustering_pending = True
            logger.info(
                "Deferred clustering upgrade for version %s until imports are idle",
                APP_VERSION,
            )
            return True

        total_faces = count_reclusterable_faces()
        if total_faces <= 0:
            set_applied_clustering_version(APP_VERSION)
            _version_clustering_pending = False
            logger.info(
                "Marked clustering version %s on empty installation",
                APP_VERSION,
            )
            return True

        reason = f"software_version:{APP_VERSION}"
        task = auto_cluster_queue.start(
            reason,
            kind="full_recluster",
            count_callable=count_reclusterable_faces,
            repair_callable=_apply_version_clustering_upgrade,
            priority=PRIORITY_VERSION_UPGRADE,
        )
        if task and task.get("reason") == reason:
            _version_clustering_pending = False
            logger.info(
                "Scheduled one-time clustering upgrade for software version %s",
                APP_VERSION,
            )
        else:
            _version_clustering_pending = True
        return True


def is_backend_idle_for_reclustering() -> bool:
    """Return whether low-priority assignment-triggered clustering may start."""
    import_snapshot = import_queue.snapshot()
    if (
        import_snapshot.get("running_count")
        or import_snapshot.get("queued_count")
        or import_snapshot.get("paused_count")
    ):
        return False
    task = auto_cluster_queue.snapshot().get("task")
    return not (
        task
        and task.get("status") in {"queued", "running", "paused", "cancelling"}
    )


# A review session is full of short pauses, so the quiet period must be long
# enough that thinking time never triggers a rebuild.
IDLE_RECLUSTER_DEBOUNCE_SECONDS = 90.0

idle_recluster_scheduler = IdleReclusterScheduler(
    is_backend_idle_for_reclustering,
    lambda reason: schedule_full_recluster(reason, scoped=True),
    debounce_seconds=IDLE_RECLUSTER_DEBOUNCE_SECONDS,
)


def _person_suggestion_work_ready() -> bool:
    return (
        not _database_restore_in_progress
        and not _imports_finalizing
        and is_backend_idle_for_reclustering()
    )


person_suggestion_worker = PersonSuggestionWorker(
    _person_suggestion_work_ready,
    lambda: refresh_pending_person_suggestions(can_publish=_person_suggestion_work_ready),
    lambda: event_hub.publish("person-suggestions", {}),
)


def mark_cluster_assignments_dirty(reason: str) -> None:
    """Request one debounced reclustering run after assignment mutations."""
    idle_recluster_scheduler.mark_dirty(reason)
    person_suggestion_worker.wake()


def _assist_shared_imports(stop: threading.Event) -> None:
    next_folder = 0
    delay = 2
    while not stop.wait(delay):
        delay = 2
        bindings = tuple(binding for binding in shared_library_preview.analysis_shares() if not binding.assist_paused)
        if not bindings or not _reclustering_writer_available():
            continue
        binding = bindings[next_folder % len(bindings)]
        next_folder += 1
        try:
            _import_processor._acquire_processing_slot(stop)
            try:
                if _reclustering_writer_available():
                    progress = _shared_analysis.assist(binding, _import_processor.resources.get_model, limit=1)
                    if progress["more"]:
                        delay = 0.25
            finally:
                _import_processor._release_processing_slot()
        except ImportCancelled:
            return
        except Exception:
            logger.warning("Could not assist a shared import", exc_info=True)
            stop.wait(5)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the durable import worker for the application lifetime.

    Args:
        _app: FastAPI application managed by this lifespan.

    Yields:
        Control while the application is accepting requests.
    """
    assist_stop = threading.Event()
    assist_thread = None
    try:
        try:
            event_hub.bind_loop(asyncio.get_running_loop())
            schedule_preview_cache_cleanup()
            update_manager.cleanup_cache(get_data_root() / "updates", APP_VERSION)
            auto_cluster_queue.resume_after_shutdown()
            idle_recluster_scheduler.start()
            import_queue.start()
            if not schedule_version_clustering_upgrade():
                run_startup_repairs()
            face_thumbnail_warmup_queue.start()
            person_suggestion_worker.start()
            shared_library_preview.start()
            if shared_library_preview.analysis_available():
                assist_thread = threading.Thread(
                    target=_assist_shared_imports, args=(assist_stop,), name="shared-photo-analysis", daemon=True,
                )
                assist_thread.start()
            geo_backfill.start()
            image_path_cleanup.resume()
            image_path_cleanup.schedule("startup", delay_seconds=60.0)
        except Exception:
            logger.exception("Could not start import queue during application startup")
            raise
        yield
    finally:
        assist_stop.set()
        shared_library_preview.stop()
        if assist_thread is not None:
            assist_thread.join(timeout=1)
        person_suggestion_worker.stop()
        idle_recluster_scheduler.stop()
        clustering_shutdown_error = None
        try:
            auto_cluster_queue.stop()
        except Exception as exc:
            logger.exception("Could not stop clustering cleanly during shutdown")
            clustering_shutdown_error = exc
        geo_backfill.stop()
        image_path_cleanup.stop()
        _publish_imports_throttled.flush()
        _publish_autocluster_throttled.flush()
        try:
            face_thumbnail_warmup_queue.stop()
        except Exception:
            logger.exception("Could not stop thumbnail warmup cleanly during shutdown")
        try:
            import_queue.stop()
        except Exception:
            logger.exception("Could not stop import queue cleanly during shutdown")
        if clustering_shutdown_error is not None:
            raise clustering_shutdown_error


def configure_cors(application: FastAPI) -> None:
    """Allow browser access from the app's origin and explicitly configured origins."""
    allowed_origins = []
    if os.environ.get("FACE_MANAGER_PLATFORM") != "android":
        configured = os.environ.get("FACE_MANAGER_ALLOWED_ORIGINS", "")
        allowed_origins = [
            origin for origin in (value.strip() for value in configured.split(","))
            if origin.startswith(("http://", "https://")) and "*" not in origin
        ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            PREVIEW_PROOF_HEADER, PREVIEW_ORIGINAL_HEADER,
            PERSON_SUGGESTIONS_PENDING_HEADER, ASSIGNMENT_LIBRARY_ID_HEADER,
        ],
    )


app = FastAPI(title="Face Manager API", version=APP_VERSION, lifespan=lifespan)
configure_cors(app)


@app.middleware("http")
async def require_android_session(request: Request, call_next):
    """Keep the embedded loopback server private to its WebView session."""
    if os.environ.get("FACE_MANAGER_PLATFORM") == "android":
        expected = os.environ.get("FACE_MANAGER_ANDROID_SESSION_TOKEN", "")
        supplied = request.cookies.get("fm_session", "")
        if not expected or not hmac.compare_digest(supplied, expected):
            return JSONResponse({"detail": "Unauthorized local session"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def log_unhandled_request_errors(request: Request, call_next):
    """Log unexpected request failures and return a stable JSON response."""
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Unhandled API error for %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            {
                "detail": (
                    "Internal server error. Face Manager attempted to recover "
                    "automatically. See the error log for details."
                )
            },
            status_code=500,
        )

# -----------------------------
# PROCESSING
# -----------------------------


@app.get("/api/version")
def api_version():
    """Return the application version.

    Returns:
        Version metadata for frontend diagnostics.
    """
    return {"version": APP_VERSION}


@app.get("/api/changelog/current")
def api_current_changelog():
    """Return unseen release notes up to the running version.

    After an update that skipped several releases the user must see every
    intermediate version's notes, not only the newest. On a fresh install
    (no recorded last-seen version) only the running version is offered so the
    first launch does not dump the entire history. An empty first library is
    marked separately so the UI can give the import and help entry priority.
    """
    last_seen = get_last_seen_changelog_version()
    fresh_install = False
    if last_seen is None:
        with closing(get_conn()) as connection:
            fresh_install = connection.execute("SELECT 1 FROM image LIMIT 1").fetchone() is None
    try:
        document = load_changelog(get_changelog_path())
    except (ChangelogError, OSError):
        logger.exception("Could not load release notes for version %s", APP_VERSION)
        document = None

    versions: list[dict] = []
    if document is not None:
        try:
            current_version = parse_semver(APP_VERSION)
        except ValueError:
            current_version = None
        try:
            seen_version = parse_semver(last_seen) if last_seen else None
        except ValueError:
            seen_version = None

        for release in released_versions(document):
            try:
                release_version = parse_semver(release["version"])
            except ValueError:
                continue
            if current_version is not None and release_version > current_version:
                continue
            if seen_version is None:
                # Without a recorded history only surface the running version.
                if current_version is not None and release_version != current_version:
                    continue
            elif release_version <= seen_version:
                continue
            versions.append(release)

    if not versions:
        # Preserve a stable shape so the UI can distinguish "nothing new" from
        # a changelog that failed to load.
        current = find_release(document, APP_VERSION) if document is not None else None
        versions = [current] if current else []

    return {
        "versions": versions,
        "seen": last_seen == APP_VERSION,
        "fresh_install": fresh_install,
    }


@app.get("/api/changelog")
def api_full_changelog():
    """Return the complete released changelog history, newest first."""
    try:
        document = load_changelog(get_changelog_path())
    except (ChangelogError, OSError) as exc:
        logger.exception("Could not load full changelog")
        raise HTTPException(status_code=503, detail="Das Änderungsprotokoll konnte nicht geladen werden.") from exc
    return {"versions": released_versions(document)}


@app.post("/api/changelog/current/acknowledge")
def api_acknowledge_current_changelog():
    """Remember that the running version's notes were dismissed."""
    with _cluster_operation_lock:
        set_last_seen_changelog_version(APP_VERSION)
    return {"version": APP_VERSION, "seen": True}


@app.get("/api/updates/check")
def api_check_updates(force: bool = Query(False)):
    """Check the latest public GitHub release, cached for one hour."""
    enabled = get_automatic_update_checks()
    if not enabled and not force:
        return {
            "enabled": False,
            "current_version": APP_VERSION,
            "update_available": False,
            "check_interval_seconds": 60 * 60,
        }
    try:
        result = update_manager.check(
            APP_VERSION,
            get_build_variant(),
            force=force,
        )
    except UpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    result["enabled"] = enabled
    result["skipped"] = get_skipped_update_version() == result["latest_version"]
    result["can_install"] = update_manager.can_install()
    result["check_interval_seconds"] = 60 * 60
    return result


@app.post("/api/updates/skip")
def api_skip_update(data: dict = Body(...)):
    """Suppress one offered release while allowing later releases through."""
    version = str(data.get("version") or "").strip()
    try:
        if parse_semver(version) <= parse_semver(APP_VERSION):
            raise UpdateError("Es kann nur eine neuere Version übersprungen werden.")
        update_manager.get_cached_release(version)
    except UpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with _cluster_operation_lock:
        set_skipped_update_version(version)
    return {"version": version, "skipped": True}


@app.post("/api/updates/download")
def api_download_update(data: dict = Body(...)):
    """Start a verified installer download without blocking the UI."""
    version = str(data.get("version") or "").strip()
    try:
        return update_manager.start_download(version, get_data_root() / "updates")
    except (OSError, UpdateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/updates/download")
def api_update_download_state():
    """Return progress for the current installer download."""
    return update_manager.download_state()


@app.post("/api/updates/open-release")
def api_open_update_release(data: dict = Body(...)):
    """Open the validated GitHub page for the offered release."""
    try:
        update_manager.open_release_page(str(data.get("version") or ""))
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"opened": True}


@app.post("/api/updates/install")
def api_install_update(data: dict = Body(...)):
    """Hand off a verified installer and close the desktop app when required."""
    activity = _describe_background_activity()
    if activity is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Das Update kann erst gestartet werden, wenn die laufende "
                f"Hintergrundarbeit abgeschlossen ist: {activity}"
            ),
        )
    version = str(data.get("version") or "").strip()
    try:
        update_manager.launch_downloaded_installer(version)
    except (OSError, UpdateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if os.environ.get("FACE_MANAGER_PLATFORM") != "android":
        schedule_process_exit()
    return {"installing": True, "version": version}


@app.get("/api/runtime")
def api_runtime():
    """Return the active inference provider and compute mode.

    Returns:
        Runtime provider details used by the UI badge.
    """
    return {
        **get_runtime_provider_details(),
        "host_platform": "android" if os.environ.get("FACE_MANAGER_PLATFORM") == "android" else "windows" if is_windows_host() else "linux",
        "display_platform": "android" if os.environ.get("FACE_MANAGER_PLATFORM") == "android" else "windows" if is_windows_host() else "linux",
    }


@app.post("/api/runtime/gpu-diagnostic")
def api_gpu_diagnostic():
    from .services.gpu_diagnostics import run_gpu_diagnostic

    if import_queue.snapshot()["running_count"]:
        raise HTTPException(status_code=409, detail="Führe die GPU-Diagnose nach dem laufenden Import aus.")
    return run_gpu_diagnostic()


def get_display_platform(request: Request) -> str:
    """Resolve the preferred UI path format for one request."""
    preferred = request.headers.get("x-face-manager-display-platform", "").strip()
    if preferred == "windows":
        if is_windows_host() or is_wsl_host():
            return "windows"
    if is_windows_host():
        return "windows"
    return "linux"


def serialize_folder_tree(node, display_platform: str):
    """Convert folder tree paths into display paths for the UI."""
    display_path = (
        to_display_path(node["path"])
        if display_platform == "windows"
        else node["path"]
    )
    display_name = (
        display_path.replace("\\", "/").rstrip("/").split("/").pop() or display_path
    )
    return {
        **node,
        "path": display_path,
        "name": display_name,
        "children": [
            serialize_folder_tree(child, display_platform)
            for child in node["children"]
        ],
    }


def serialize_image_locations(locations, display_platform: str):
    """Convert canonical image locations into UI display paths."""
    payload = []
    for location in locations:
        display_path = (
            to_display_path(location["path"])
            if display_platform == "windows"
            else location["path"]
        )
        payload.append(
            {
                **location,
                "path": display_path,
                "directory": (
                    to_display_path(location["directory"])
                    if display_platform == "windows"
                    else location["directory"]
                ),
            }
        )
    return payload


def serialize_import_snapshot(snapshot, display_platform: str):
    """Convert queued job paths into UI display paths."""
    for job in snapshot["jobs"]:
        if display_platform == "windows":
            job["folder_path"] = to_display_path(job["folder_path"])
        if job.get("current_file"):
            if display_platform == "windows":
                job["current_file"] = to_display_path(job["current_file"])
        for station in job.get("stations", []):
            if station.get("current_file"):
                if display_platform == "windows":
                    station["current_file"] = to_display_path(station["current_file"])
    return snapshot


def ensure_database_is_idle() -> None:
    """Reject database mutation while imports are queued or running."""
    snapshot = import_queue.snapshot()
    if snapshot["running_count"] or snapshot["queued_count"] or snapshot.get("paused_count"):
        raise HTTPException(
            status_code=409,
            detail="Eine Datensicherung ist erst möglich, wenn alle Bilder hinzugefügt wurden.",
        )


def reset_import_resources() -> None:
    """Refresh cached import resources that depend on database contents."""
    processor = getattr(import_queue, "_processor", None)
    resources = getattr(processor, "resources", None)
    if resources is not None and hasattr(resources, "reset_clusterer"):
        resources.reset_clusterer()


def validate_cluster_distance_threshold(value: float) -> float:
    """Validate and normalize a clustering threshold supplied by the client."""
    try:
        threshold = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Die Empfindlichkeit muss eine Zahl sein.") from exc
    if not 0 <= threshold <= 1:
        raise HTTPException(
            status_code=400,
            detail="Die Empfindlichkeit muss zwischen 0,0 und 1,0 liegen.",
        )
    return threshold


def validate_database_file(path: Path) -> dict[str, int]:
    """Verify a SQLite snapshot without modifying it and return its summary."""
    required = {
        "image": {"id", "path", "directory", "filename"},
        "face": {"id", "image_id", "cluster_id", "bbox_x", "bbox_y", "bbox_w", "bbox_h"},
        "cluster": {"id", "label", "person_id"},
        "person": {"id", "name"},
    }
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.execute("PRAGMA trusted_schema = OFF")
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        if conn.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("Database integrity check failed")
        table_names = {row[0] for row in rows}
        if any(table not in table_names or not columns.issubset({
            row[1] for row in conn.execute(f"PRAGMA table_info({table})")
        }) for table, columns in required.items()):
            raise HTTPException(
                status_code=400,
                detail="In der ausgewählten Datei fehlen benötigte Face-Manager-Daten.",
            )
        return {
            "photos": conn.execute("SELECT COUNT(*) FROM image").fetchone()[0],
            "people": conn.execute("SELECT COUNT(*) FROM person").fetchone()[0],
            "albums": conn.execute("SELECT COUNT(*) FROM album").fetchone()[0] if "album" in table_names else 0,
        }
    except sqlite3.DatabaseError as exc:
        raise HTTPException(
            status_code=400,
            detail="Die ausgewählte Datei ist keine gültige Face-Manager-Sicherung.",
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def snapshot_database_backup(target_path: Path) -> None:
    """Save the live SQLite state, including WAL, without copying OS metadata."""
    source = get_conn()
    target = None
    try:
        target = sqlite3.connect(target_path)
        source.backup(target)
        target.execute("PRAGMA journal_mode = DELETE")
        target.commit()
    finally:
        if target is not None:
            target.close()
        source.close()


def remove_database_sidecars(database_path: Path) -> None:
    """Discard WAL state belonging to the database file being replaced."""
    for suffix in ("-wal", "-shm"):
        database_path.with_name(database_path.name + suffix).unlink(missing_ok=True)


@app.post("/api/system/select-folder")
def api_select_folder(request: Request):
    """Open a native folder picker on the backend host."""
    display_platform = get_display_platform(request)
    try:
        folder_path = pick_folder(prefer_windows_dialog=display_platform == "windows")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Die Ordnerauswahl konnte auf diesem Gerät nicht geöffnet werden.",
        ) from exc
    if not folder_path:
        return {"folder_path": None}
    return {
        "folder_path": (
            to_display_path(normalize_import_folder_path(folder_path))
            if display_platform == "windows"
            else normalize_import_folder_path(folder_path)
        )
    }


def _preview_cache_settings() -> dict:
    limit, unreadable = get_preview_cache_policy()
    return _preview_cache_fields(limit, unreadable)


def _preview_cache_fields(limit: int | None, unreadable: bool) -> dict:
    return {
        "preview_cache_limit_mib": limit,
        "preview_cache_limit_mib_default": DEFAULT_PREVIEW_CACHE_LIMIT_MIB,
        "preview_cache_limit_error": unreadable,
    }


@app.get("/api/ui-preferences")
def api_get_ui_preferences(response: Response) -> dict:
    """Return the UI preferences saved for this installation."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return load_ui_preferences()
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("Could not read UI preferences", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Die Anzeigeeinstellungen konnten nicht geladen werden.",
        ) from exc


@app.patch("/api/ui-preferences")
def api_update_ui_preferences(payload: object = Body(...)) -> dict:
    """Merge validated UI preferences without changing the photo library."""
    if not isinstance(payload, dict) or "values" not in payload or payload.keys() - {"values", "only_if_absent"}:
        raise HTTPException(status_code=422, detail="Die Anfrage für die Anzeigeeinstellungen ist ungültig.")
    only_if_absent = payload.get("only_if_absent", False)
    if not isinstance(only_if_absent, bool):
        raise HTTPException(status_code=422, detail="Die Anfrage für die Anzeigeeinstellungen ist ungültig.")
    try:
        values = validate_ui_preferences(payload["values"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return update_ui_preferences(values, only_if_absent=only_if_absent)
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("Could not save UI preferences", exc_info=True)
        raise HTTPException(
            status_code=503, detail="Die Anzeigeeinstellungen konnten nicht gespeichert werden.",
        ) from exc


@app.get("/api/settings")
def api_get_settings():
    """Return persisted application settings used by the frontend."""
    def load_settings():
        block_separator = get_filename_person_block_separator()
        joiner = get_filename_person_joiner()
        clustering_profile = get_clustering_profile()
        return {
            "cluster_distance_threshold": get_cluster_distance_threshold(),
            "cluster_distance_threshold_default": DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
            "clustering_strictness": clustering_profile["strictness"],
            "clustering_strictness_default": DEFAULT_CLUSTER_STRICTNESS,
            "clustering_profile": clustering_profile,
            "filename_person_suffix_format": build_filename_person_format_summary(
                block_separator=block_separator,
                joiner=joiner,
            ),
            "filename_person_suffix_format_default": build_filename_person_format_summary(
                block_separator=DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR,
                joiner=DEFAULT_FILENAME_PERSON_JOINER,
            ),
            "filename_person_block_separator": block_separator,
            "filename_person_block_separator_default": DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR,
            "filename_person_joiner": joiner,
            "filename_person_joiner_default": DEFAULT_FILENAME_PERSON_JOINER,
            "file_log_level": get_file_log_level(),
            "file_log_level_default": DEFAULT_FILE_LOG_LEVEL,
            "ui_theme": get_ui_theme(),
            "ui_theme_default": DEFAULT_UI_THEME,
            "automatic_update_checks": get_automatic_update_checks(),
            "automatic_update_checks_default": DEFAULT_AUTOMATIC_UPDATE_CHECKS,
            "database_path": DB_PATH,
            "error_log_path": str(get_error_log_path()),
        }

    settings = app_cache.get_or_set(
        ("api_settings",),
        load_settings,
        ttl_seconds=5.0,
        tags={"settings"},
    )
    return {**settings, **_preview_cache_settings()}


@app.put("/api/settings")
def api_update_settings(data: dict = Body(...)):
    """Persist mutable application settings."""
    if "preview_cache_limit_mib" in data:
        if len(data) != 1:
            raise HTTPException(
                status_code=400, detail="Das Vorschau-Limit muss separat gespeichert werden."
            )
        try:
            limit = validate_preview_cache_limit_mib(data["preview_cache_limit_mib"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings = api_get_settings()
        limit = _save_preview_cache_limit(limit)
        return {**settings, **_preview_cache_fields(limit, False)}
    return _update_library_settings(data)


def _save_preview_cache_limit(value: object) -> int | None:
    try:
        limit = set_preview_cache_limit_mib(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, ImportError) as exc:
        logger.warning("Could not save the preview cache policy", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Das Vorschau-Limit konnte nicht gespeichert werden."
        ) from exc
    face_thumbnail_warmup_queue.wake()
    return limit


@safe_cluster_mutation
def _update_library_settings(data: dict):
    threshold = get_cluster_distance_threshold()
    clustering_profile = get_clustering_profile()
    block_separator = get_filename_person_block_separator()
    joiner = get_filename_person_joiner()
    file_log_level = get_file_log_level()
    ui_theme = get_ui_theme()
    automatic_update_checks = get_automatic_update_checks()

    if "cluster_distance_threshold" in data:
        threshold = validate_cluster_distance_threshold(data["cluster_distance_threshold"])
        threshold = set_cluster_distance_threshold(threshold)
        clustering_profile = get_clustering_profile()
    if "clustering_strictness" in data:
        strictness = validate_cluster_distance_threshold(data["clustering_strictness"])
        clustering_profile = set_clustering_strictness(strictness)
        threshold = clustering_profile["neighbor_threshold"]
    if "filename_person_block_separator" in data:
        block_separator = set_filename_person_block_separator(
            str(data["filename_person_block_separator"])
        )
    if "filename_person_joiner" in data:
        joiner = set_filename_person_joiner(str(data["filename_person_joiner"]))
    if "filename_person_suffix_format" in data:
        try:
            set_filename_person_suffix_format(
                str(data["filename_person_suffix_format"])
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "file_log_level" in data:
        try:
            file_log_level = set_file_log_level(str(data["file_log_level"]))
            apply_persisted_file_log_level()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "ui_theme" in data:
        try:
            ui_theme = set_ui_theme(str(data["ui_theme"]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "automatic_update_checks" in data:
        try:
            automatic_update_checks = set_automatic_update_checks(
                data["automatic_update_checks"]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if (
        "cluster_distance_threshold" not in data
        and "clustering_strictness" not in data
        and "filename_person_block_separator" not in data
        and "filename_person_joiner" not in data
        and "filename_person_suffix_format" not in data
        and "file_log_level" not in data
        and "ui_theme" not in data
        and "automatic_update_checks" not in data
    ):
        raise HTTPException(
            status_code=400,
            detail="Es wurde keine Einstellung zum Speichern übermittelt.",
        )

    return {
        "cluster_distance_threshold": threshold,
        "cluster_distance_threshold_default": DEFAULT_CLUSTER_DISTANCE_THRESHOLD,
        "clustering_strictness": clustering_profile["strictness"],
        "clustering_strictness_default": DEFAULT_CLUSTER_STRICTNESS,
        "clustering_profile": clustering_profile,
        "filename_person_suffix_format": build_filename_person_format_summary(
            block_separator=block_separator,
            joiner=joiner,
        ),
        "filename_person_suffix_format_default": build_filename_person_format_summary(
            block_separator=DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR,
            joiner=DEFAULT_FILENAME_PERSON_JOINER,
        ),
        "filename_person_block_separator": block_separator,
        "filename_person_block_separator_default": DEFAULT_FILENAME_PERSON_BLOCK_SEPARATOR,
        "filename_person_joiner": joiner,
        "filename_person_joiner_default": DEFAULT_FILENAME_PERSON_JOINER,
        "file_log_level": file_log_level,
        "file_log_level_default": DEFAULT_FILE_LOG_LEVEL,
        "ui_theme": ui_theme,
        "ui_theme_default": DEFAULT_UI_THEME,
        "automatic_update_checks": automatic_update_checks,
        "automatic_update_checks_default": DEFAULT_AUTOMATIC_UPDATE_CHECKS,
        **_preview_cache_settings(),
        "database_path": DB_PATH,
        "error_log_path": str(get_error_log_path()),
    }


@app.post("/api/settings/cluster-threshold/auto-tune")
@safe_cluster_mutation
def api_auto_tune_cluster_threshold():
    """Calibrate and persist the threshold without starting reclustering."""
    try:
        result = auto_tune_cluster_distance_threshold()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    validate_cluster_distance_threshold(result["threshold"])
    return result


@app.get("/api/database/export")
def api_export_database():
    """Export a consistent SQLite snapshot of the current database."""
    ensure_database_is_idle()
    fd, temp_path = tempfile.mkstemp(suffix=".sqlite", prefix="face-manager-export-")
    os.close(fd)

    source = get_conn()
    target = sqlite3.connect(temp_path)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode = DELETE")
        target.commit()
        with open(temp_path, "rb") as exported:
            payload = exported.read()
    except Exception:
        logger.exception("Database export failed")
        raise
    finally:
        target.close()
        source.close()
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    return Response(
        payload,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": 'attachment; filename="face-manager-database.sqlite"'
        },
    )


def _restore_work_file(db_path: Path, purpose: str) -> Path:
    fd, name = tempfile.mkstemp(
        suffix=".sqlite", prefix=f"{db_path.stem}.{purpose}-", dir=db_path.parent
    )
    os.close(fd)
    return Path(name)


_RESTORE_BACKUP_NAME = re.compile(r"database-before-restore-\d{8}-\d{6}-\d{6}-[a-z0-9_]+\.sqlite")


def _latest_restore_backup() -> Path | None:
    folder = Path(DB_PATH).parent / "restore-backups"
    if not folder.is_dir() or folder.is_symlink():
        return None
    return max((
        path for path in folder.iterdir()
        if _RESTORE_BACKUP_NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file()
    ), default=None)


@app.post("/api/database/restore-preview")
def api_preview_database_restore(payload: bytes = Body(..., media_type="application/octet-stream")):
    if not payload:
        raise HTTPException(status_code=400, detail="Es wurde keine Sicherungsdatei ausgewählt.")
    path = _restore_work_file(Path(DB_PATH), "restore-preview")
    try:
        path.write_bytes(payload)
        return {**validate_database_file(path), "checksum": hashlib.sha256(payload).hexdigest()}
    finally:
        path.unlink(missing_ok=True)


@app.get("/api/database/restore-backup")
def api_get_restore_backup():
    return {"available": _latest_restore_backup() is not None}


@app.get("/api/database/restore-backup/file")
def api_download_restore_backup():
    backup = _latest_restore_backup()
    if backup is None:
        raise HTTPException(status_code=404, detail="Es ist keine Sicherung des vorherigen Stands vorhanden.")
    return FileResponse(backup, filename=backup.name, media_type="application/octet-stream")


class _ArchiveRepairSelection(BaseModel):
    tokens: list[str] = Field(min_length=1, max_length=500)


class _ArchiveRepairUndo(BaseModel):
    batch_id: str = Field(min_length=1, max_length=64)


@app.get("/api/database/archive-repair")
def api_preview_archive_repair():
    generation = _get_media_library_generation()
    result = preview_archive_repair()
    _get_media_library_generation(generation)
    return result


@app.post("/api/database/archive-repair/snapshots")
@safe_cluster_mutation
def api_register_archive_repair_snapshot(payload: bytes = Body(..., media_type="application/octet-stream")):
    _get_media_library_generation()
    try:
        return register_archive_repair_snapshot(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Could not store archive repair comparison snapshot")
        raise HTTPException(
            status_code=503, detail="Die Sicherung konnte nicht zum Vergleich gespeichert werden."
        ) from exc


def _finish_archive_repair(operation, argument):
    _get_media_library_generation()
    try:
        result = operation(argument)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception("Could not complete archive repair")
        raise HTTPException(
            status_code=503,
            detail="Die Zuordnungen konnten nicht geändert werden. Bitte versuche es erneut.",
        ) from exc
    reset_import_resources()
    invalidate_image_query_cache()
    mark_cluster_assignments_dirty("archive_repair")
    notify_clusters_changed("archive_repair")
    return result


@app.post("/api/database/archive-repair/apply")
@safe_cluster_mutation
def api_apply_archive_repair(selection: _ArchiveRepairSelection):
    return _finish_archive_repair(apply_archive_repair, selection.tokens)


@app.post("/api/database/archive-repair/undo")
@safe_cluster_mutation
def api_undo_archive_repair(selection: _ArchiveRepairUndo):
    return _finish_archive_repair(undo_archive_repair, selection.batch_id)


def _initialize_restored_library() -> None:
    init_db(recover=False, scan_sources=False)
    reset_import_resources()
    invalidate_image_query_cache()
    app_cache.clear()


@app.post("/api/database/import")
@safe_cluster_mutation
def api_import_database(
    payload: bytes = Body(..., media_type="application/octet-stream"),
    preview_checksum: Annotated[str | None, Header(alias="X-Restore-SHA256")] = None,
):
    """Replace the active library, keeping its snapshot until recovery is verified."""
    global _database_restore_in_progress, _media_library_generation
    ensure_database_is_idle()
    if not payload:
        raise HTTPException(status_code=400, detail="Es wurde keine Sicherungsdatei ausgewählt.")
    if preview_checksum is not None and (
        not re.fullmatch(r"[a-f0-9]{64}", preview_checksum)
        or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), preview_checksum)
    ):
        raise HTTPException(status_code=409, detail="Die Sicherungsdatei wurde geändert. Wähle sie erneut aus.")

    current = Path(DB_PATH)
    upload = backup = rollback_copy = None
    backup_ready = replaced = library_ready = False
    restore_gate = drain_started = geo_stopped = suggestion_worker_stopped = False

    try:
        current.parent.mkdir(parents=True, exist_ok=True)
        upload = _restore_work_file(current, "import")
        upload.write_bytes(payload)
        validate_database_file(upload)

        with _media_library_lock:
            _database_restore_in_progress = restore_gate = True
            _media_library_generation += 1
            _pending_image_geometry.clear()
        if not shared_library_preview.begin_restore():
            raise HTTPException(
                status_code=409, detail="Eine Freigabeänderung wird noch bestätigt. Bitte versuche es erneut.",
            )
        face_thumbnail_warmup_queue.invalidate_library()
        clustering_task = auto_cluster_queue.snapshot().get("task")
        if _imports_finalizing or (
            clustering_task and clustering_task["status"] in {"running", "paused", "cancelling"}
        ):
            raise HTTPException(
                status_code=409, detail="Hintergrundarbeiten werden noch beendet. Bitte versuche es erneut."
            )
        suggestion_worker_stopped = person_suggestion_worker.stop()
        drain_started = True
        if not image_path_cleanup.stop():
            raise HTTPException(status_code=409, detail="Die Pfadprüfung läuft noch. Bitte versuche es erneut.")
        geo_backfill.stop()
        geo_stopped = True

        if current.exists():
            backup = _restore_work_file(current, "pre-import-backup")
            snapshot_database_backup(backup)
            validate_database_file(backup)
            backup_ready = True

        shared_library_preview.prepare_replacement()
        invalidate_person_suggestion_epoch()
        os.replace(upload, current)
        replaced = True
        remove_database_sidecars(current)
        _initialize_restored_library()
        if backup_ready:
            recovery_folder = current.parent / "restore-backups"
            recovery_folder.mkdir(exist_ok=True)
            if recovery_folder.is_symlink():
                raise OSError("The restore backup directory must be local")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            suffix = backup.stem.rsplit("-", 1)[-1]
            os.replace(backup, recovery_folder / f"database-before-restore-{stamp}-{suffix}.sqlite")
            backup = None
        library_ready = True
    except Exception as exc:
        if not replaced and isinstance(exc, HTTPException):
            raise
        logger.exception("Database import failed")
        if replaced:
            try:
                if backup_ready:
                    # Keep the independent snapshot even if reinitializing its copy fails.
                    rollback_copy = _restore_work_file(current, "rollback")
                    shutil.copyfile(backup, rollback_copy)
                    os.replace(rollback_copy, current)
                else:
                    current.unlink(missing_ok=True)
                remove_database_sidecars(current)
                _initialize_restored_library()
                library_ready = True
            except Exception:
                logger.exception("Database rollback failed; previous snapshot retained at %s", backup)
        detail = "Die Sicherung konnte nicht wiederhergestellt werden."
        if not replaced or (library_ready and backup_ready):
            detail += " Der vorherige Stand wurde beibehalten."
        elif backup_ready:
            detail += f" Die vorherige Bibliothek ist gesichert unter {backup}."
        raise HTTPException(status_code=500, detail=detail) from exc
    finally:
        if restore_gate:
            if replaced:
                invalidate_person_suggestion_epoch()
                background_activities.discard(PHOTO_METADATA_ACTIVITY)
            face_thumbnail_warmup_queue.invalidate_library()
            with _media_library_lock:
                _database_restore_in_progress = False
                _media_library_generation += 1
            shared_library_preview.library_changed(restoring=False)
            face_thumbnail_warmup_queue.wake()
        disposable_backup = backup if not (replaced and backup_ready and not library_ready) else None
        for path in (upload, rollback_copy, disposable_backup):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove database restore work file %s", path, exc_info=True)
        if not replaced or library_ready:
            if suggestion_worker_stopped:
                person_suggestion_worker.start()
            if drain_started:
                image_path_cleanup.resume()
            if geo_stopped:
                try:
                    geo_backfill.start()
                except Exception:
                    logger.exception("Could not restart photo metadata indexing after database import")
            if restore_gate:
                auto_cluster_queue.notify_ready()
        if library_ready:
            notify_clusters_changed("database_import")
            person_suggestion_worker.wake()
            try:
                if not schedule_version_clustering_upgrade():
                    run_startup_repairs("database_import")
            except Exception:
                logger.exception("Could not schedule post-restore clustering")

    return {"status": "imported"}


@app.post("/api/process-folder")
def api_process_folder(request: Request, data: dict = Body(...)):
    """Queue a folder import through the legacy endpoint.

    Args:
        data: Request body containing ``folder_path``.

    Returns:
        Newly queued import job.
    """
    return api_create_import(request, data)


@app.get("/api/process-status")
def api_process_status(request: Request):
    """Return queue state through the legacy status endpoint.

    Returns:
        Current import queue snapshot.
    """
    return serialize_import_snapshot(
        import_queue.snapshot(),
        get_display_platform(request),
    )


@app.post("/api/imports", status_code=202)
def api_create_import(request: Request, data: dict = Body(...)):
    """Queue a folder for serialized background import.

    Args:
        data: Request body containing ``folder_path``.

    Returns:
        Newly queued import job.

    Raises:
        HTTPException: If the folder path is missing or invalid.
    """
    if not isinstance(data.get("folder_path"), str) or not data["folder_path"].strip() or "\0" in data["folder_path"]:
        raise HTTPException(status_code=400, detail="Wähle einen Bilderordner aus.")

    folder_path = normalize_import_folder_path(data["folder_path"])

    defer_check = folder_path.startswith(("\\\\", "//")) or shared_library_preview.contains_source(folder_path)
    if not defer_check and not os.path.isdir(folder_path):
        raise HTTPException(
            status_code=400,
            detail=f"Der ausgewählte Ordner ist nicht verfügbar: {folder_path}",
        )

    payload = _enqueue_import(folder_path)
    if get_display_platform(request) == "windows":
        payload["folder_path"] = to_display_path(payload["folder_path"])
    return payload


@safe_import_enqueue
def _enqueue_import(folder_path: str):
    return import_queue.enqueue(folder_path)


@app.get("/api/imports")
def api_imports(request: Request):
    """Return all visible import jobs.

    Returns:
        Queue summary with active, queued, and recent terminal jobs.
    """
    return serialize_import_snapshot(
        import_queue.snapshot(),
        get_display_platform(request),
    )


@app.get("/api/shared-library")
def api_shared_library():
    return shared_library_preview.snapshot()


@app.put("/api/shared-library")
def api_configure_shared_library(data: dict = Body(...)):
    if not shared_library_preview.available():
        raise HTTPException(status_code=404, detail="Die Vorschau für gemeinsame Ordner ist deaktiviert.")
    try:
        return shared_library_preview.configure(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/shared-library/resolve", status_code=202)
def api_resolve_shared_library(data: dict = Body(...)):
    if not shared_library_preview.write_available():
        raise HTTPException(status_code=404, detail="Der schreibende Abgleich ist nicht verfügbar.")
    try:
        return shared_library_preview.select_conflict(data.get("id"), data.get("revision"), data.get("choice"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/shared-library/conflicts/{case_id}/evidence")
def api_shared_conflict_evidence(case_id: str, revision: str):
    evidence = shared_library_preview.conflict_evidence(case_id, revision)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Das geprüfte Foto ist gerade nicht verfügbar.")
    return JSONResponse(evidence, headers={"Cache-Control": "no-store"})


@app.get("/api/autocluster-tasks")
def api_autocluster_tasks():
    """Return the current background auto-clustering repair task, if any."""
    return auto_cluster_queue.snapshot()


@app.get("/api/thumbnail-warmup")
def api_thumbnail_warmup():
    """Return the current low-priority thumbnail warmup state."""
    return face_thumbnail_warmup_queue.snapshot()


@app.post("/api/thumbnail-warmup/pause")
def api_pause_thumbnail_warmup():
    return {"task": face_thumbnail_warmup_queue.pause()}


@app.post("/api/thumbnail-warmup/resume")
def api_resume_thumbnail_warmup():
    return {"task": face_thumbnail_warmup_queue.resume()}


@app.post("/api/thumbnail-warmup/cancel")
def api_cancel_thumbnail_warmup():
    return {"task": face_thumbnail_warmup_queue.cancel()}


@app.delete("/api/thumbnail-warmup/history")
def api_delete_thumbnail_warmup_history():
    if not face_thumbnail_warmup_queue.dismiss_history():
        raise HTTPException(status_code=409, detail="Die Aufgabe ist noch nicht beendet.")
    return {"status": "removed"}


@app.post("/api/autocluster-tasks/{task_id}/pause")
def api_pause_autocluster_task(task_id: str):
    task = auto_cluster_queue.pause(task_id)
    if task is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe kann nicht pausiert werden.")
    return {"task": task}


@app.post("/api/autocluster-tasks/{task_id}/resume")
def api_resume_autocluster_task(task_id: str):
    task = auto_cluster_queue.resume(task_id)
    if task is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe kann nicht fortgesetzt werden.")
    return {"task": task}


@app.post("/api/autocluster-tasks/{task_id}/cancel")
def api_cancel_autocluster_task(task_id: str):
    task = auto_cluster_queue.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe kann nicht abgebrochen werden.")
    return {"task": task}


@app.delete("/api/autocluster-tasks/{task_id}")
def api_delete_autocluster_task(task_id: str):
    if not auto_cluster_queue.dismiss(task_id):
        raise HTTPException(status_code=409, detail="Die Aufgabe ist noch nicht beendet.")
    return {"status": "removed"}


@app.get("/api/events")
async def api_events():
    """Stream live backend state to subscribed clients via Server-Sent Events.

    On connect the client receives the latest snapshot for every topic
    (``imports``, ``autocluster``, ``thumbnail-warmup``, ``clusters``), then
    incremental updates as they happen. This replaces the previous polling.
    """
    return StreamingResponse(
        event_hub.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering so events are flushed immediately.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/library-identity")
def api_library_identity(response: Response):
    """Return the current library epoch even when the latest event was coalesced."""
    generation = _get_media_library_generation()
    identity = get_person_suggestion_epoch()
    _get_media_library_generation(generation)
    response.headers["Cache-Control"] = "no-store"
    return {"library_id": identity}


@app.delete("/api/imports/history")
def api_delete_import_history():
    return {"removed_count": import_queue.clear_history()}


@app.post("/api/imports/{job_id}/pause")
def api_pause_import(job_id: str):
    result = import_queue.pause(job_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe kann nicht pausiert werden.")
    return result


@app.post("/api/imports/{job_id}/resume")
def api_resume_import(job_id: str):
    result = import_queue.resume(job_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe kann nicht fortgesetzt werden.")
    return result


@app.post("/api/imports/{job_id}/cancel")
def api_cancel_import(job_id: str):
    result = import_queue.cancel(job_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe kann nicht abgebrochen werden.")
    return result


@app.delete("/api/imports/{job_id}/history")
def api_delete_import_history_entry(job_id: str):
    result = import_queue.delete_terminal(job_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Die Aufgabe ist noch nicht beendet.")
    return result


@app.delete("/api/imports/{job_id}")
def api_cancel_or_remove_import(job_id: str):
    """Cancel a running import or remove another queue entry.

    Args:
        job_id: Import job identifier.

    Returns:
        Cancellation or removal result.

    Raises:
        HTTPException: If the job does not exist.
    """
    result = import_queue.cancel_or_remove(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Die Aufgabe wurde nicht gefunden.")
    return result


# -----------------------------
# CLUSTERS
# -----------------------------


@app.get("/api/clusters")
def api_clusters():
    """List compact cluster summaries for the sidebar."""
    return list_cluster_summaries()


@app.get("/api/clusters-overview")
def api_clusters_overview():
    """Return the cluster-page bootstrap payload in a single round trip."""
    return _assignment_snapshot(get_cluster_overview)


def _assignment_snapshot(read):
    generation = _get_media_library_generation()
    library_id = get_person_suggestion_epoch()
    result = read()
    _get_media_library_generation(generation)
    return {**result, "assignment_library_id": library_id}


@app.get("/api/person-suggestions")
def api_person_suggestions(response: Response):
    """List conservative, reviewable matches for existing people."""
    generation = _get_media_library_generation()
    with _cluster_operation_lock:
        suggestions, pending, library_id = get_person_suggestion_snapshot()
    _get_media_library_generation(generation)
    response.headers[PERSON_SUGGESTIONS_PENDING_HEADER] = "1" if pending else "0"
    response.headers[ASSIGNMENT_LIBRARY_ID_HEADER] = library_id
    response.headers["Cache-Control"] = "no-store"
    return suggestions


@app.post("/api/person-suggestions/accept")
@safe_cluster_mutation
def api_accept_person_suggestions(data: dict = Body(...)):
    """Confirm one or several proposals in a single user action."""
    assignments = data.get("assignments")
    if not isinstance(assignments, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("proposal_token"), str)
        for item in assignments
    ):
        raise HTTPException(
            status_code=409,
            detail="Lade die Vorschläge neu und wähle die Gesichter erneut aus.",
        )
    try:
        updated = accept_person_suggestion_assignments(assignments)
    except StalePersonSuggestionError as exc:
        raise HTTPException(
            status_code=409,
            detail="Der Vorschlag hat sich geändert. Lade ihn neu und prüfe die Auswahl erneut.",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty("accept_person_suggestions")
    notify_clusters_changed("accept_person_suggestions")
    return {"status": "ok", "accepted_count": updated}


@app.post("/api/person-suggestions/{cluster_id}/dismiss")
@safe_cluster_mutation
def api_dismiss_person_suggestion(cluster_id: int, data: dict | None = Body(default=None)):
    """Dismiss a proposal while leaving its cluster unassigned."""
    token = (data or {}).get("proposal_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=409, detail="Lade die Vorschläge neu und versuche es erneut.")
    try:
        dismiss_person_suggestion(cluster_id, token)
    except StalePersonSuggestionError as exc:
        raise HTTPException(status_code=409, detail="Der Vorschlag hat sich geändert. Lade ihn neu.") from exc
    notify_clusters_changed("dismiss_person_suggestion")
    return {"status": "ok"}


@app.get("/api/review-suggestions")
def api_review_suggestions():
    """List conservative proposals learned from explicit review decisions."""
    with _cluster_operation_lock:
        library_id = get_person_suggestion_epoch()
        return [dict(item, library_id=library_id) for item in list_review_suggestions()]


@app.post("/api/review-suggestions/accept")
@safe_cluster_mutation
def api_accept_review_suggestions(data: dict = Body(...)):
    cluster_ids = data.get("cluster_ids") or data.get("clusterIds") or []
    expected_statuses = data.get("expected_review_statuses")
    expected_face_ids = data.get("expected_face_ids")
    if (
        data.get("library_id") != get_person_suggestion_epoch()
        or not isinstance(expected_statuses, dict)
        or not isinstance(expected_face_ids, dict)
    ):
        raise HTTPException(status_code=409, detail="Lade die Vorschläge neu und prüfe die Gesichter erneut.")
    try:
        updated = accept_review_suggestions(cluster_ids, expected_statuses, expected_face_ids)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty("accept_review_suggestions")
    notify_clusters_changed("accept_review_suggestions")
    return {"status": "ok", "accepted_count": updated}


@app.post("/api/review-suggestions/{cluster_id}/dismiss")
@safe_cluster_mutation
def api_dismiss_review_suggestion(cluster_id: int, data: dict | None = Body(default=None)):
    payload = data or {}
    category = payload.get("review_status")
    if payload.get("library_id") != get_person_suggestion_epoch() or category not in ("unknown_person", "not_face"):
        raise HTTPException(status_code=409, detail="Lade die Aussortiervorschläge neu und versuche es erneut.")
    try:
        dismiss_review_suggestion(cluster_id, category)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    notify_clusters_changed("dismiss_review_suggestion")
    return {"status": "ok"}


@app.post("/api/clusters/recluster")
@safe_cluster_mutation
def api_recluster_clusters():
    """Schedule a full rebuild including each person's internal subclusters.

    The request is always accepted: if an import currently holds the writer the
    task is queued and starts on its own once the import finishes.
    """
    idle_recluster_scheduler.clear()
    task = schedule_full_recluster("manual_recluster")
    status = task.get("status") if task else "noop"
    return {"scheduled": task is not None, "status": status, "task": task}


@app.get("/api/face-review-groups")
def api_face_review_groups():
    """List non-cluster review queues and their counts."""
    return list_face_review_groups()


@app.get("/api/clusters/{cluster_id}")
def api_cluster_detail(cluster_id: int):
    """Return compact metadata for one cluster."""
    cluster = get_cluster_summary(cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Die Gesichtsgruppe wurde nicht gefunden.")
    return cluster


@app.get("/api/clusters/{cluster_id}/faces")
def api_cluster_faces(cluster_id: int):
    """Return faces for one cluster only."""
    def read():
        cluster = get_cluster_summary(cluster_id)
        if cluster is None:
            raise HTTPException(status_code=404, detail="Die Gesichtsgruppe wurde nicht gefunden.")
        return {**cluster, "faces": get_cluster_faces(cluster_id)}
    return _assignment_snapshot(read)


@app.get("/api/face-review-groups/{group_key}/faces")
def api_face_review_group_faces(group_key: str):
    """Return faces for one non-cluster review queue."""
    try:
        normalize_face_review_group(group_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _assignment_snapshot(partial(get_faces_for_review_group, group_key))


@app.post("/api/clusters/{cluster_id}/assign-person")
@safe_cluster_mutation
def api_assign_person_to_cluster(cluster_id: int, data: dict = Body(...)):
    """Assign a cluster to a person.

    Args:
        cluster_id: Cluster identifier to update.
        data: Request body containing the person name.

    Returns:
        Success status.

    Raises:
        HTTPException: If no person name is supplied.
    """
    person_name = data.get("person_name") or data.get("personName")
    if not person_name or not str(person_name).strip():
        raise HTTPException(status_code=400, detail="Gib einen Namen ein.")
    try:
        assign_cluster_to_person(cluster_id, str(person_name))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty("assign_person")
    notify_clusters_changed("assign_person")
    return {"status": "ok"}


@app.patch("/api/clusters/{cluster_id}")
@safe_cluster_mutation
def api_rename_cluster(cluster_id: int, data: dict = Body(...)):
    """Rename one cluster."""
    label = data.get("label") or data.get("cluster_label") or data.get("clusterLabel")
    if not label or not str(label).strip():
        raise HTTPException(status_code=400, detail="Gib einen Namen für die Gesichtsgruppe ein.")
    try:
        rename_cluster(cluster_id, str(label))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    notify_clusters_changed("rename_cluster")
    return {"status": "ok"}


@app.post("/api/clusters/{cluster_id}/remove-face/{face_id}")
@safe_cluster_mutation
def api_remove_face_from_cluster(cluster_id: int, face_id: int):
    """Remove a face from a specific cluster.

    Args:
        cluster_id: Expected cluster identifier.
        face_id: Face identifier to remove.

    Returns:
        Success status.

    Raises:
        HTTPException: If the face is not assigned to the cluster.
    """
    try:
        updated = remove_faces_from_cluster(cluster_id, [face_id])
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail="Das Gesicht gehört nicht mehr zu dieser Gesichtsgruppe.",
        )
    reset_import_resources()
    mark_cluster_assignments_dirty("remove_face")
    notify_clusters_changed("remove_face")
    return {"status": "ok", "updated_count": updated}


def _expected_face_assignments(rows):
    if rows is None:
        return None
    detail = "Die Angaben zur Gesichtsauswahl sind ungültig."
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail=detail)
    expected = {}
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=detail)
        face_id, cluster_id, status = row.get("id"), row.get("cluster_id"), row.get("review_status")
        person_name = row.get("person_name")
        if (
            type(face_id) is not int or face_id <= 0 or face_id in expected
            or (cluster_id is not None and (type(cluster_id) is not int or cluster_id <= 0))
            or status not in ("active", "unknown_person", "not_face")
            or "person_name" not in row or (person_name is not None and not isinstance(person_name, str))
        ):
            raise HTTPException(status_code=400, detail=detail)
        expected[face_id] = (cluster_id, status, person_name)
    return expected


_SHARED_FACE_HISTORY_BLOCK = "Rückgängig und Wiederholen sind bei schreibend abgeglichenen Fotoordnern deaktiviert."


@app.get("/api/faces/action-history")
def api_face_action_history():
    generation = _get_media_library_generation()
    library_id = get_person_suggestion_epoch()
    with shared_library_preview.local_face_history_allowed() as allowed:
        result = face_action_history(library_id, blocked_reason=None if allowed else _SHARED_FACE_HISTORY_BLOCK)
    _get_media_library_generation(generation)
    return result


class _FaceActionReplay(BaseModel):
    action_id: str = Field(min_length=1, max_length=64)
    library_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=0)


def _replay_face_action(selection: _FaceActionReplay, *, redo: bool):
    _get_media_library_generation()
    if selection.library_id != get_person_suggestion_epoch():
        raise HTTPException(status_code=409, detail="Die Bibliothek wurde gewechselt. Lade den Änderungsverlauf neu.")
    with shared_library_preview.local_face_history_allowed() as allowed:
        if not allowed:
            raise HTTPException(status_code=409, detail=_SHARED_FACE_HISTORY_BLOCK)
        try:
            result = replay_face_action(selection.action_id, selection.revision, selection.library_id, redo=redo)
        except FaceActionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    reset_import_resources()
    reason = "redo_face_action" if redo else "undo_face_action"
    mark_cluster_assignments_dirty(reason)
    notify_clusters_changed(reason)
    return result


@app.post("/api/faces/action-history/undo")
@safe_cluster_mutation
def api_undo_face_action(selection: _FaceActionReplay):
    return _replay_face_action(selection, redo=False)


@app.post("/api/faces/action-history/redo")
@safe_cluster_mutation
def api_redo_face_action(selection: _FaceActionReplay):
    return _replay_face_action(selection, redo=True)


@app.post("/api/faces/batch")
@safe_cluster_mutation
def api_batch_update_faces(data: dict = Body(...)):
    """Apply one batch review action to selected faces."""
    action = str(data.get("action") or "").strip()
    face_ids = data.get("face_ids") or data.get("faceIds") or []
    try:
        if action == "remove_from_cluster":
            cluster_id = int(data.get("cluster_id") or data.get("clusterId"))
            updated = remove_faces_from_cluster(cluster_id, face_ids)
            response = {"status": "ok", "updated_count": updated}
        elif action == "create_cluster":
            cluster_id = create_cluster_from_faces(face_ids)
            response = {"status": "ok", "cluster_id": cluster_id}
        elif action in {"assign_person", "mark_unknown_person", "mark_not_face"}:
            _get_media_library_generation()
            expected_library_id = data.get("expected_library_id")
            if expected_library_id is not None and expected_library_id != get_person_suggestion_epoch():
                raise HTTPException(status_code=409, detail="Die Bibliothek wurde gewechselt. Lade die Gesichtsauswahl neu.")
            expected_faces = _expected_face_assignments(data.get("expected_faces"))
            with shared_library_preview.local_face_history_allowed() as record_history:
                if action == "assign_person":
                    person_name = data.get("person_name") or data.get("personName")
                    if not person_name or not str(person_name).strip():
                        raise HTTPException(status_code=400, detail="Gib einen Namen ein.")
                    result = assign_faces_to_person(
                        face_ids, str(person_name), expected_faces=expected_faces, record_history=record_history,
                    )
                    response_field = "cluster_id"
                else:
                    status = "unknown_person" if action == "mark_unknown_person" else "not_face"
                    result = mark_faces_with_review_status(
                        face_ids, status, expected_faces=expected_faces, record_history=record_history,
                    )
                    response_field = "updated_count"
            if record_history:
                value, history_action = result
                response = {"status": "ok", response_field: value, "history_action": history_action}
            else:
                response = {"status": "ok", response_field: result}
        elif action == "restore_to_manual_review":
            updated = restore_faces_to_manual_review(face_ids)
            response = {"status": "ok", "updated_count": updated}
        else:
            raise HTTPException(status_code=400, detail="Diese Aktion ist nicht verfügbar.")
    except StaleFaceAssignmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty(f"faces_batch:{action}")
    notify_clusters_changed(f"faces_batch:{action}")
    return response


# -----------------------------
# PERSONS
# -----------------------------


@app.get("/api/persons")
def api_persons():
    """List known people.

    Returns:
        Person identifier and name dictionaries.
    """
    return list_persons()


@app.get("/api/person-directory")
def api_person_directory(
    search: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_PERSON_DIRECTORY_PAGE_SIZE)] = 24,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
):
    generation = _get_media_library_generation()
    result = list_person_directory(search, limit, offset)
    _get_media_library_generation(generation)
    return result


@app.get("/api/person-directory/{person_id}")
def api_person_profile(person_id: Annotated[int, ApiPath(ge=1, le=2**63 - 1)]):
    generation = _get_media_library_generation()
    library_id = get_person_suggestion_epoch()
    profile = get_person_directory_entry(person_id)
    _get_media_library_generation(generation)
    if profile is None:
        raise HTTPException(status_code=404, detail="Diese Person wurde nicht gefunden.")
    return {**profile, "assignment_library_id": library_id}


@app.get("/api/person-directory/{person_id}/photos")
def api_person_photos(
    request: Request,
    person_id: Annotated[int, ApiPath(ge=1, le=2**63 - 1)],
    limit: Annotated[int, Query(ge=1, le=MAX_PERSON_DIRECTORY_PAGE_SIZE)] = 24,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
):
    generation = _get_media_library_generation()
    if get_person_directory_entry(person_id) is None:
        _get_media_library_generation(generation)
        raise HTTPException(status_code=404, detail="Diese Person wurde nicht gefunden.")
    rows, total = list_images_page(limit=limit, offset=offset, person_id=person_id)
    items = _group_image_rows(rows, get_display_platform(request))
    _get_media_library_generation(generation)
    return {
        "items": items, "total": total, "offset": offset, "limit": limit,
        "has_more": offset + len(items) < total, "available_persons": [],
    }


@app.get("/api/persons/{person_id}/faces")
def api_person_faces(person_id: int):
    """List faces assigned to a person.

    Args:
        person_id: Person identifier.

    Returns:
        Face dictionaries assigned through clusters.
    """
    return get_person_faces(person_id)


@app.patch("/api/persons/{person_id}")
@safe_cluster_mutation
def api_rename_person(person_id: int, data: dict = Body(...)):
    """Rename one person."""
    if "expected_library_id" in data:
        _require_person_management_library(data["expected_library_id"])
    name = data.get("name") or data.get("person_name") or data.get("personName")
    if not name or not str(name).strip():
        raise HTTPException(status_code=400, detail="Gib einen Namen ein.")
    options = {}
    if "expected_target_person_id" in data:
        target_id = data["expected_target_person_id"]
        if target_id is not None and (type(target_id) is not int or not 0 < target_id <= 2**63 - 1):
            raise HTTPException(status_code=400, detail="Wähle eine gültige Zielperson aus.")
        options["expected_target_person_id"] = target_id
    try:
        next_person_id = rename_person(person_id, str(name), **options)
    except StalePersonRenameTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    reset_import_resources()
    person_suggestion_worker.wake()
    notify_clusters_changed("rename_person")
    return {"status": "ok", "person_id": next_person_id}


@app.delete("/api/persons/{person_id}")
@safe_cluster_mutation
def api_delete_person(
    person_id: int,
    reassignment_group: str = Query(...),
    expected_library_id: Annotated[str | None, Query(max_length=128)] = None,
):
    """Delete one person and reclassify all assigned faces."""
    if expected_library_id is not None:
        _require_person_management_library(expected_library_id)
    try:
        normalize_face_review_group(reassignment_group)
        delete_person(person_id, reassignment_group)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty("delete_person")
    notify_clusters_changed("delete_person")
    return {"status": "ok"}


def _require_person_management_library(expected_library_id: str) -> None:
    if expected_library_id != get_person_suggestion_epoch():
        raise HTTPException(
            status_code=409,
            detail="Die Bibliothek hat sich geändert. Öffne das Personenprofil erneut.",
        )


# -----------------------------
# IMAGES
# -----------------------------


_SOURCE_WORKER_COUNT = 3
_SOURCE_TIMEOUT_SECONDS = 8
_source_work_lock = threading.Lock()
_source_work_queue: Queue = Queue(maxsize=8)
_source_workers_started = False


def _source_worker() -> None:
    """Keep potentially blocked source I/O out of the shared HTTP worker pool."""
    while True:
        future, operation = _source_work_queue.get()
        if not future.set_running_or_notify_cancel():
            continue
        try:
            future.set_result(operation())
        except Exception as exc:
            future.set_exception(exc)


async def _run_source_work(operation):
    global _source_workers_started
    with _source_work_lock:
        if not _source_workers_started:
            for worker in range(_SOURCE_WORKER_COUNT):
                threading.Thread(
                    target=_source_worker, name=f"image-source-{worker}", daemon=True,
                ).start()
            _source_workers_started = True
        future: Future = Future()
        try:
            _source_work_queue.put_nowait((future, operation))
        except Full as exc:
            raise HTTPException(
                status_code=503,
                detail="Die Fotoquellen sind gerade ausgelastet. Bitte versuche es erneut.",
                headers={"Retry-After": "2"},
            ) from exc
    pending = asyncio.wrap_future(future)
    try:
        return await asyncio.wait_for(asyncio.shield(pending), timeout=_SOURCE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError) as exc:
        # Python 3.10 uses a distinct asyncio timeout exception.
        # A cancelled await cannot interrupt an OS file operation. Running reads
        # retain their worker; queued requests are skipped when dequeued.
        future.cancel()
        pending.add_done_callback(lambda result: None if result.cancelled() else result.exception())
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(
            status_code=503,
            detail="Die Fotoquelle antwortet nicht. Bitte versuche es erneut.",
            headers={"Retry-After": "2"},
        ) from exc


def _get_media_library_generation(expected: int | None = None) -> int:
    with _media_library_lock:
        if _database_restore_in_progress or (
            expected is not None and expected != _media_library_generation
        ):
            raise HTTPException(
                status_code=409,
                detail="Die Bibliothek wird gewechselt. Bitte versuche es erneut.",
            )
        return _media_library_generation


@app.get("/api/images/{image_id}/file")
async def get_image(image_id: int):
    """Serve an image from an available filesystem location.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Streaming file response.

    Raises:
        HTTPException: If no image location exists.
    """
    generation = _get_media_library_generation()
    path = await _run_source_work(partial(get_available_image_path, image_id))
    _get_media_library_generation(generation)
    if not path:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    return FileResponse(filesystem_path(path))


def _thumbnail_response(thumbnail_path: str | Path, request: Request) -> Response:
    # The cached filename includes the source version and, for crops, the box.
    digest = hashlib.sha256(Path(thumbnail_path).name.encode("utf-8")).hexdigest()
    etag = f'"{digest}"'
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    validators = request.headers.get("if-none-match", "").split(",")
    if any(value.strip().removeprefix("W/") in {etag, "*"} for value in validators):
        return Response(status_code=304, headers=headers)
    # Read the small JPEG before ASGI starts sending headers. Cache eviction
    # cannot remove bytes already loaded for this response.
    body = read_preview_bytes(Path(thumbnail_path), 2 * 1024 * 1024 + 1)
    if not body or len(body) > 2 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Das Vorschaubild konnte nicht gelesen werden.")
    return Response(body, media_type="image/jpeg", headers=headers)


def _cached_registered_preview(preview_id: int, content_hash: str, bbox):
    if bbox is not None:
        return get_cached_face_thumbnail(preview_id, content_hash, bbox)
    return get_cached_image_thumbnail(preview_id, content_hash)


def _registered_preview(preview_id: int, face: bool):
    row = get_face_preview_source(preview_id) if face else get_image_preview_source(preview_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    bbox = (
        tuple(int(_safe_float(row[key])) for key in ("bbox_x", "bbox_y", "bbox_w", "bbox_h"))
        if face else None
    )
    content_hash = row["content_hash"]
    cached = _cached_registered_preview(preview_id, content_hash, bbox) if content_hash else None
    return int(row["image_id"]), content_hash, bbox, cached, _stored_image_geometry(row) is None


def _try_save_image_geometry(key: tuple[int, str], geometry: tuple[int, int, int]) -> bool:
    try:
        save_missing_image_geometry(*key, geometry)
    except sqlite3.Error as exc:
        message = str(exc).lower()
        if isinstance(exc, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            return False
        logger.warning("Could not store photo dimensions for image %s", key[0], exc_info=True)
    return True


def _schedule_image_geometry_retry() -> None:
    global _image_geometry_timer
    if _image_geometry_timer is None:
        _image_geometry_timer = threading.Timer(_IMAGE_GEOMETRY_RETRY_SECONDS, _retry_image_geometry)
        _image_geometry_timer.daemon = True
        _image_geometry_timer.start()


def _retry_image_geometry() -> None:
    global _image_geometry_timer
    with _media_library_lock:
        _image_geometry_timer = None
        if _database_restore_in_progress:
            return
        for key in list(_pending_image_geometry)[:_IMAGE_GEOMETRY_RETRY_BATCH]:
            if not _try_save_image_geometry(key, _pending_image_geometry[key]):
                break
            del _pending_image_geometry[key]
        if _pending_image_geometry:
            _schedule_image_geometry_retry()


def _remember_image_geometry(
    image_id: int, content_hash: str, geometry: tuple[int, int, int], generation: int,
) -> None:
    key = (image_id, content_hash)
    with _media_library_lock:
        if _database_restore_in_progress or generation != _media_library_generation:
            return
        if not _pending_image_geometry and _try_save_image_geometry(key, geometry):
            return
        _pending_image_geometry[key] = geometry
        if len(_pending_image_geometry) > _MAX_PENDING_IMAGE_GEOMETRY:
            _pending_image_geometry.popitem(last=False)
        _schedule_image_geometry_retry()


def _render_registered_preview(
    preview_id: int, image_id: int, content_hash: str | None,
    bbox: tuple[int, int, int, int] | None, generation: int, needs_geometry: bool,
) -> Path:
    _get_media_library_generation(generation)
    if content_hash:
        cached = _cached_registered_preview(preview_id, content_hash, bbox)
        if cached is not None:
            return cached
    locations = list_image_locations([image_id]).get(image_id, ())
    failed = False
    for location in locations:
        _get_media_library_generation(generation)
        source = location["path"]
        try:
            if not content_hash:
                # Older backups still work through the original file-identity cache.
                return (
                    ensure_face_thumbnail(preview_id, source, bbox)
                    if bbox is not None else Path(ensure_image_thumbnail(preview_id, source))
                )
            image, geometry = decode_verified_preview(source, content_hash)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, Image.DecompressionBombError):
            failed = True
            continue

        with image:
            _get_media_library_generation(generation)
            try:
                thumbnail = (
                    write_face_thumbnail(preview_id, content_hash, bbox, image, geometry[0])
                    if bbox is not None else write_image_thumbnail(preview_id, content_hash, image, geometry[0])
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    status_code=422, detail="Das Vorschaubild konnte nicht erstellt werden.",
                ) from exc
        if needs_geometry:
            _remember_image_geometry(image_id, content_hash, geometry, generation)
        return thumbnail

    if failed:
        raise HTTPException(status_code=422, detail="Das Vorschaubild konnte nicht erstellt werden.")
    raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")


async def _serve_thumbnail(preview_id: int, request: Request, *, face: bool = False) -> Response:
    generation = _get_media_library_generation()
    image_id, content_hash, bbox, thumbnail, needs_geometry = await run_in_threadpool(
        _registered_preview, preview_id, face,
    )
    _get_media_library_generation(generation)
    if thumbnail is not None:
        try:
            response = await run_in_threadpool(_thumbnail_response, thumbnail, request)
        except OSError:
            # It may have been evicted between the cached lookup and read.
            pass
        else:
            _get_media_library_generation(generation)
            return response
    thumbnail = await _run_source_work(
        partial(_render_registered_preview, preview_id, image_id, content_hash, bbox, generation, needs_geometry),
    )
    _get_media_library_generation(generation)
    try:
        response = await run_in_threadpool(_thumbnail_response, thumbnail, request)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="Das Vorschaubild ist nicht verfügbar. Bitte versuche es erneut.",
            headers={"Retry-After": "2"},
        ) from exc
    else:
        _get_media_library_generation(generation)
        return response


@app.get("/api/images/{image_id}/thumbnail")
async def get_image_thumbnail(image_id: int, request: Request):
    """Serve a cached, upright, size-limited image thumbnail."""
    return await _serve_thumbnail(image_id, request)


@app.delete("/api/images/{image_id}")
@safe_cluster_mutation
def remove_image(image_id: int):
    """Delete an image and its dependent records.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Deletion status.

    Raises:
        HTTPException: If the image does not exist.
    """
    if not delete_image(image_id):
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    reset_import_resources()
    mark_cluster_assignments_dirty("delete_image")
    notify_clusters_changed("delete_image")
    return {"status": "deleted"}


@app.post("/api/images/{image_id}/open-location")
def open_image_location(image_id: int, data: dict = Body(default=None)):
    """Reveal an image in the system file manager.

    Args:
        image_id: Canonical image identifier.
        data: Optional body containing a preferred image path.

    Returns:
        Open status.

    Raises:
        HTTPException: If the image is missing or the file manager fails.
    """
    preferred_path = data.get("image_path") if data else None
    if preferred_path:
        preferred_path = normalize_import_folder_path(preferred_path)
    path = get_available_image_path(
        image_id,
        preferred_path,
        require_preferred=bool(preferred_path),
    )
    if not path:
        raise HTTPException(
            status_code=404,
            detail="Der ausgewählte Dateispeicherort ist nicht mehr verfügbar.",
        )
    try:
        open_file_location(path)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.exception("Could not open image location for %s", path)
        raise HTTPException(
            status_code=500,
            detail="Der Explorer oder Dateimanager konnte nicht geöffnet werden.",
        ) from exc
    return {"status": "opened"}


@app.get("/api/folders")
def get_folders(request: Request):
    """Return the imported folder hierarchy.

    Returns:
        Folder tree and aggregate counts.
    """
    display_platform = get_display_platform(request)
    tree = build_folder_tree()
    tree["roots"] = [
        serialize_folder_tree(root, display_platform) for root in tree["roots"]
    ]
    return tree


@app.get("/api/maintenance/image-paths")
def api_get_image_path_cleanup():
    """Return the current or most recent image path validation state."""
    return image_path_cleanup.snapshot()


@app.post("/api/maintenance/image-paths")
def api_start_image_path_cleanup():
    """Queue a low-priority validation of every stored image location."""
    with _cluster_operation_lock:
        return image_path_cleanup.start("manual")


def _stored_image_geometry(row) -> tuple[int, int, int] | None:
    if {"exif_orientation", "raw_width", "raw_height"}.issubset(row.keys()):
        geometry = row["exif_orientation"], row["raw_width"], row["raw_height"]
        if all(type(value) is int for value in geometry) and geometry[0] in range(1, 9) and min(geometry[1:]) > 0:
            return geometry
    return None


def _group_image_rows(
    rows, display_platform: str, *, geometry_override: tuple[int, int, int] | None = None,
) -> list[dict]:
    """Turn image/face rows into the payload the picture views expect.

    Shared by the paged library listing and the single-image lookup so both
    always describe an image the same way.
    """
    images = {}
    geometry_by_id = {}

    for r in rows:
        image_id = r["image_id"]
        path = r["image_path"]

        if image_id not in images:
            geometry = geometry_override or _stored_image_geometry(r) or (1, 0, 0)
            geometry_by_id[image_id] = geometry
            orientation, width, height = geometry
            if orientation in {5, 6, 7, 8}:
                width, height = height, width
            images[image_id] = {
                "image_width": width or None,
                "image_height": height or None,
                "id": image_id,
                "image_path": (
                    to_display_path(path) if display_platform == "windows" else path
                ),
                "directory": (
                    to_display_path(r["directory"])
                    if display_platform == "windows"
                    else r["directory"]
                ),
                "filename": r["filename"],
                "created_at": r["created_at"],
                "content_hash": r["content_hash"],
                "location_count": r["location_count"],
                "faces": [],
            }

        # A photo without visible faces still belongs to the library.
        if r["face_id"] is None:
            continue
        raw_bbox = (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"])
        if None in raw_bbox:
            continue

        bbox_x, bbox_y, bbox_w, bbox_h = correct_bbox_for_orientation(
            geometry_by_id[image_id],
            *(_safe_float(value) for value in raw_bbox),
        )

        review_status = (
            r["review_status"] if "review_status" in r.keys() else "active"
        )
        face = {
            "id": r["face_id"],
            "bbox_x": bbox_x,
            "bbox_y": bbox_y,
            "bbox_w": bbox_w,
            "bbox_h": bbox_h,
            "cluster_id": r["cluster_id"],
            "person_name": r["person_name"],
            "review_status": review_status,
        }
        if "is_manual" in r.keys() and r["is_manual"]:
            face["is_manual"] = True
        images[image_id]["faces"].append(face)

    locations_by_image = list_image_locations(list(images.keys()))
    for image_id, image in images.items():
        locations = locations_by_image.get(image_id, [])
        image["locations"] = serialize_image_locations(locations, display_platform)
        image["location_count"] = len(locations)
    return list(images.values())


def _image_detail_rows(image_id: int, *, include_archived: bool = False):
    rows = get_image_detail_rows(image_id, include_archived=include_archived)
    if not rows:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    return rows


def api_image_detail(request: Request, image_id: int):
    """Return one image with its active faces without consulting the original."""
    generation = _get_media_library_generation()
    image = _group_image_rows(_image_detail_rows(image_id), get_display_platform(request))[0]
    _get_media_library_generation(generation)
    return image


def _resolve_image_geometry(image_id: int, content_hash: str | None, generation: int):
    _get_media_library_generation(generation)
    for location in list_image_locations([image_id]).get(image_id, ()):
        _get_media_library_generation(generation)
        path = location["path"]
        try:
            if not content_hash:
                # With no hash, use the same source and version checks as the
                # thumbnail. Keep the result ephemeral for this response.
                return read_legacy_preview_geometry(image_id, path), path
            geometry = read_verified_preview_geometry(path, content_hash)
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
        _remember_image_geometry(image_id, content_hash, geometry, generation)
        return geometry, path
    return None, None


@app.get("/api/images/{image_id}/detail")
async def get_image_detail(request: Request, image_id: int, resolve_geometry: bool = False):
    if not resolve_geometry:
        return await run_in_threadpool(api_image_detail, request, image_id)

    generation = _get_media_library_generation()
    load_rows = partial(_image_detail_rows, image_id, include_archived=True)
    rows = await run_in_threadpool(load_rows)
    geometry = _stored_image_geometry(rows[0])
    content_hash = rows[0]["content_hash"]
    if geometry is None:
        with _media_library_lock:
            geometry = _pending_image_geometry.get((image_id, content_hash))
    if geometry is None and any(row["face_id"] is not None for row in rows):
        geometry, path = await _run_source_work(
            partial(_resolve_image_geometry, image_id, content_hash, generation),
        )
        rows = await run_in_threadpool(load_rows)
        if rows[0]["content_hash"] != content_hash:
            raise HTTPException(status_code=409, detail="Das Bild wurde geändert. Bitte lade es erneut.")
        if geometry and not content_hash:
            locations = await run_in_threadpool(list_image_locations, [image_id])
            if not any(location["path"] == path for location in locations.get(image_id, ())):
                geometry = None

    images = await run_in_threadpool(
        partial(_group_image_rows, rows, get_display_platform(request), geometry_override=geometry),
    )
    _get_media_library_generation(generation)
    return images[0]


def _registered_face_preview(image_id: int, source) -> FacePreview | None:
    content_hash = source["content_hash"]
    if not content_hash:
        return None
    geometry = _stored_image_geometry(source)
    if geometry is None:
        with _media_library_lock:
            geometry = _pending_image_geometry.get((image_id, content_hash))
    if geometry is None:
        return None
    cached = get_cached_image_thumbnail(image_id, content_hash)
    if cached is None:
        return None
    try:
        body = read_preview_bytes(cached, 2 * 1024 * 1024 + 1)
        if not body or len(body) > 2 * 1024 * 1024:
            return None
        with Image.open(BytesIO(body), formats=("JPEG",)) as thumbnail:
            width, height = thumbnail.size
    except (OSError, Image.DecompressionBombError):
        return None
    orientation, raw_width, raw_height = geometry
    expected_width, expected_height = (raw_height, raw_width) if orientation >= 5 else (raw_width, raw_height)
    if abs(width * expected_height - height * expected_width) > max(expected_width, expected_height):
        return None
    return FacePreview(body, geometry, content_hash)


async def _face_preview_paths(image_id: int) -> list[str]:
    locations = await run_in_threadpool(list_image_locations, [image_id])
    return [location["path"] for location in locations.get(image_id, ())]


@app.get("/api/images/{image_id}/manual-face-preview")
async def api_manual_face_preview(image_id: int):
    generation = _get_media_library_generation()
    source = await run_in_threadpool(get_image_preview_source, image_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    content_hash = source["content_hash"]
    paths = await _face_preview_paths(image_id)

    try:
        preview = await _run_source_work(partial(render_face_preview, paths, content_hash))
    except HTTPException as error:
        if error.status_code != 503:
            raise
        preview = await run_in_threadpool(_registered_face_preview, image_id, source)
        if preview is None:
            raise
        original = False
    else:
        original = preview is not None
        if preview is None:
            preview = await run_in_threadpool(_registered_face_preview, image_id, source)
    if preview is None:
        raise HTTPException(status_code=422, detail="Für dieses Foto ist keine passende Vorschau verfügbar. Prüfe das verknüpfte Original.")

    current = await run_in_threadpool(get_image_preview_source, image_id)
    _get_media_library_generation(generation)
    if current is None or current["content_hash"] != content_hash:
        raise HTTPException(status_code=409, detail="Das Bild wurde geändert. Bitte lade es erneut.")
    registered_geometry = _stored_image_geometry(current)
    if registered_geometry is not None and registered_geometry != preview.geometry:
        raise HTTPException(status_code=409, detail="Die Bildausrichtung stimmt nicht mit dem registrierten Foto überein.")
    return Response(preview.body, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store",
        PREVIEW_PROOF_HEADER: issue_preview_proof(image_id, generation, content_hash, preview),
        PREVIEW_ORIGINAL_HEADER: "yes" if original else "no",
    })


def _require_face_preview_proof(token, image_id: int, generation: int, content_hash: str | None):
    proof = read_preview_proof(token, image_id, generation, content_hash)
    if proof is None:
        raise HTTPException(status_code=409, detail="Die Vorschau ist nicht mehr gültig. Öffne die Markierung erneut.")
    return proof


def _raw_manual_face_box(geometry, normalized_box):
    orientation, width, height = geometry
    upright_width, upright_height = (height, width) if orientation in {5, 6, 7, 8} else (width, height)
    x, y, w, h = normalized_box
    left, top = round(x * upright_width), round(y * upright_height)
    right = round(min(1, x + w) * upright_width)
    bottom = round(min(1, y + h) * upright_height)
    if right <= left or bottom <= top:
        raise HTTPException(status_code=422, detail="Die Gesichtsmarkierung ist zu klein.")
    inverse_orientation = {6: 8, 8: 6}.get(orientation, orientation)
    return correct_bbox_for_orientation(
        (inverse_orientation, upright_width, upright_height), left, top, right - left, bottom - top,
    )


@safe_cluster_mutation
def _commit_manual_face(image_id, content_hash, geometry, box, person_name, generation, source_path):
    _get_media_library_generation(generation)
    try:
        face_id, cluster_id = create_manual_face(
            image_id, content_hash, geometry, box, person_name, source_path=source_path,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ManualFaceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise
        raise HTTPException(
            status_code=503, detail="Die Bibliothek wird gerade aktualisiert. Bitte versuche es erneut.",
            headers={"Retry-After": "2"},
        ) from exc
    notify_clusters_changed("manual_face_created")
    return {"face_id": face_id, "cluster_id": cluster_id}


@app.post("/api/images/{image_id}/manual-faces", status_code=201)
async def api_create_manual_face(image_id: int, data: dict = Body(...)):
    normalized_box = tuple(data.get(key) for key in ("x", "y", "width", "height"))
    if not all(type(value) in (int, float) and 0 <= value <= 1 for value in normalized_box):
        raise HTTPException(status_code=422, detail="Die Gesichtsmarkierung muss innerhalb des Bildes liegen.")
    x, y, w, h = normalized_box
    if w <= 0 or h <= 0 or x + w > 1 + 1e-9 or y + h > 1 + 1e-9:
        raise HTTPException(status_code=422, detail="Die Gesichtsmarkierung muss innerhalb des Bildes liegen und eine Fläche haben.")
    person_name = data.get("person_name")
    person_name = person_name.strip() if isinstance(person_name, str) else ""
    if not person_name or len(person_name) > 255 or any(
        character == "\x00" or "\ud800" <= character <= "\udfff" for character in person_name
    ):
        raise HTTPException(status_code=422, detail="Gib einen gültigen Personennamen mit höchstens 255 Zeichen ein.")

    generation = _get_media_library_generation()
    source = await run_in_threadpool(get_image_preview_source, image_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    content_hash = source["content_hash"]
    if "expected_content_hash" in data and content_hash != data["expected_content_hash"]:
        raise HTTPException(status_code=409, detail="Das Bild wurde geändert. Bitte lade es erneut.")

    geometry, source_hash = _require_face_preview_proof(data.get("preview_token"), image_id, generation, content_hash)
    source_path = None
    if not content_hash:
        paths = await _face_preview_paths(image_id)
        source_path = await _run_source_work(
            partial(find_preview_source, paths, source_hash),
        )
        if source_path is None:
            raise HTTPException(status_code=409, detail="Das Originalfoto ist nicht mehr verfügbar oder wurde geändert. Öffne die Markierung erneut.")
    box = _raw_manual_face_box(geometry, normalized_box)
    return await run_in_threadpool(
        _commit_manual_face, image_id, content_hash, geometry, box, person_name, generation, source_path,
    )


@app.get("/api/images/{image_id}/face-box-suggestions")
async def api_face_box_suggestions(
    image_id: int, preview_token: Annotated[str | None, Header(alias=PREVIEW_PROOF_HEADER)] = None,
):
    generation = _get_media_library_generation()
    source = await run_in_threadpool(get_image_preview_source, image_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    paths = await _face_preview_paths(image_id)
    content_hash = source["content_hash"]
    source_hash = content_hash
    if preview_token is not None:
        _, source_hash = _require_face_preview_proof(preview_token, image_id, generation, content_hash)
    boxes = await run_in_threadpool(get_recorded_face_boxes, image_id)
    try:
        items = await _run_source_work(
            partial(suggest_face_boxes, paths, source_hash, boxes, _import_processor.resources.get_model),
        )
    except FaceBoxSuggestionBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"}) from exc
    except FaceBoxSuggestionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FaceBoxSuggestionPhotoUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    current_source = await run_in_threadpool(get_image_preview_source, image_id)
    current_boxes = await run_in_threadpool(get_recorded_face_boxes, image_id)
    if not content_hash:
        current_paths = await _face_preview_paths(image_id)
    else:
        current_paths = paths
    _get_media_library_generation(generation)
    if current_source is None or current_source["content_hash"] != content_hash or boxes != current_boxes or paths != current_paths:
        raise HTTPException(status_code=409, detail="Das Bild oder seine Markierungen wurden geändert. Bitte lade es erneut.")
    return {"items": items}


@app.get("/api/faces/{face_id}/image")
def api_face_image(request: Request, face_id: int, proposal_token: str | None = None):
    """Resolve a face used in a proposal to its original photo context."""
    with _cluster_operation_lock:
        if proposal_token is not None and not is_person_suggestion_epoch_current(proposal_token):
            raise HTTPException(status_code=409, detail="Die Bibliothek hat sich geändert. Lade den Vorschlag neu.")
        conn = get_conn()
        try:
            row = conn.execute("SELECT image_id FROM face WHERE id = ?", (face_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="Das Gesicht wurde nicht gefunden.")
        image = api_image_detail(request, int(row["image_id"]))
        if not any(face["id"] == face_id for face in image["faces"]):
            raise HTTPException(status_code=404, detail="Das Gesicht wurde nicht gefunden.")
        return image


def _validated_photo_selection(selection: GeoPhotoSelection) -> GeoPhotoSelection:
    try:
        validate_photo_selection(selection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return selection


class _GeoBoundsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    west: float
    east: float
    south: float
    north: float


class _GeoSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bounds: _GeoBoundsBody | None = None
    from_date: date | None = None
    to_date: date | None = None
    image_ids: list[Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]] | None = None
    album_id: str | None = None

    def to_selection(self) -> GeoPhotoSelection:
        return _validated_photo_selection(GeoPhotoSelection(
            bounds=GeoBounds(**self.bounds.model_dump()) if self.bounds else None,
            from_date=self.from_date,
            to_date=self.to_date,
            image_ids=tuple(dict.fromkeys(self.image_ids)) if self.image_ids is not None else None,
            album_id=self.album_id,
        ))


class _AlbumJourneyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_date: date | None = None
    to_date: date | None = None
    detail: JourneyDetail = "trip"
    title: Annotated[str, Field(max_length=albums.MAX_ALBUM_NAME)] | None = None
    arrival_included: bool | None = None
    departure_included: bool | None = None

    def metadata(self) -> dict:
        _validated_photo_selection(GeoPhotoSelection(from_date=self.from_date, to_date=self.to_date))
        result = self.model_dump(mode="json", exclude_none=True)
        if self.title:
            result["title"] = self.title.strip()
        return result


class _AlbumDraftOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _AlbumDraftDimensions(_AlbumDraftOptions):
    width: Annotated[int, Field(ge=0, le=99_999)]
    height: Annotated[int, Field(ge=0, le=99_999)]


_AlbumColor = Annotated[str, Field(max_length=7, pattern=r"^(?:#?[0-9a-fA-F]{3}|#?[0-9a-fA-F]{6})?$")]


class _AlbumDraftColors(_AlbumDraftOptions):
    paper: _AlbumColor | None = None
    accent: _AlbumColor | None = None
    land: _AlbumColor | None = None
    water: _AlbumColor | None = None


class _AlbumDraftFormatting(_AlbumDraftOptions):
    typeface: Literal["serif", "sans"] | None = None
    alignment: Literal["left", "center"] | None = None
    titleScale: Annotated[int, Field(ge=75, le=130)] | None = None
    margins: Literal["narrow", "normal", "wide"] | None = None
    listColumns: Literal[1, 2] | None = None


class _AlbumLayoutDraftBody(_AlbumDraftOptions):
    version: Literal[1]
    title: Annotated[str, Field(max_length=72)] | None = None
    subtitle: Annotated[str, Field(max_length=100)] | None = None
    format: Literal["a4", "portrait", "square", "custom"] | None = None
    dimensions: _AlbumDraftDimensions | None = None
    dpi: Literal[150, 240, 300, 400] | None = None
    palette: Literal["sand", "coast"] | None = None
    accent: Literal["automatic", "rust", "ocean", "pine", "plum"] | None = None
    colors: _AlbumDraftColors | None = None
    formatting: _AlbumDraftFormatting | None = None
    contextPercent: Annotated[int, Field(ge=0, le=100)] | None = None
    markerScale: Annotated[int, Field(ge=70, le=140)] | None = None
    useViewport: bool | None = None
    transports: dict[
        Annotated[str, Field(pattern=r"^[0-9]{1,19}:[0-9]{1,19}$")],
        Literal["car", "train", "plane", "walk", "bike", "boat"],
    ] | None = None
    style: Literal["standard", "landscape", "satellite"] | None = None
    showNames: bool | None = None
    showPeriod: bool | None = None
    showStopsList: bool | None = None


_AlbumImageId = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]


class _AlbumPhotoSelectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_ids: list[_AlbumImageId] | None = None
    selection: _GeoSelectionBody | None = None

    def photo_kwargs(self):
        return {
            "image_ids": self.image_ids,
            "selection": self.selection.to_selection() if self.selection is not None else None,
        }


class _AlbumCreateBody(_AlbumPhotoSelectionBody):
    name: str
    journey: _AlbumJourneyBody | None = None
    layout_draft: _AlbumLayoutDraftBody | None = None


class _AlbumUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    cover_image_id: _AlbumImageId | None = None
    journey: _AlbumJourneyBody | None = None
    layout_draft: _AlbumLayoutDraftBody | None = None


class _AlbumRemovePhotosBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_ids: list[_AlbumImageId]


def _album_call(operation, *args, write=False, **kwargs):
    generation = _get_media_library_generation()
    # The database restore takes the same operation lock before it replaces
    # the library. A write cannot commit into a newly imported database.
    with (_cluster_operation_lock if write else nullcontext()):
        _get_media_library_generation(generation)
        try:
            result = operation(*args, **kwargs)
        except albums.AlbumPhotosMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _get_media_library_generation(generation)
        if write:
            _publish_active_cluster_change("albums_changed")
        return result


def _validate_album_id(album_id: str) -> None:
    _validated_photo_selection(GeoPhotoSelection(album_id=album_id))


@app.get("/api/albums")
def get_albums():
    return {"albums": _album_call(albums.list_albums)}


@app.get("/api/albums/{album_id}")
def get_album(album_id: str):
    _validate_album_id(album_id)
    return _album_call(albums.get_album, album_id)


@app.post("/api/albums", status_code=201)
def create_album(data: _AlbumCreateBody):
    return _album_call(
        albums.create_album, data.name, write=True, **data.photo_kwargs(),
        journey=data.journey.metadata() if data.journey is not None else None,
        layout_draft=data.layout_draft.model_dump(exclude_none=True) if data.layout_draft is not None else None,
    )


@app.patch("/api/albums/{album_id}")
def update_album(album_id: str, data: _AlbumUpdateBody):
    _validate_album_id(album_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes and data.name is None:
        raise HTTPException(status_code=422, detail="Gib einen Albumname an.")
    if data.journey is not None:
        changes["journey"] = data.journey.metadata()
    if data.layout_draft is not None:
        changes["layout_draft"] = data.layout_draft.model_dump(exclude_none=True)
    return _album_call(albums.update_album, album_id, changes, write=True)


@app.delete("/api/albums/{album_id}")
def delete_album(album_id: str):
    _validate_album_id(album_id)
    _album_call(albums.delete_album, album_id, write=True)
    return {"status": "deleted"}


@app.post("/api/albums/{album_id}/photos")
def add_album_photos(album_id: str, data: _AlbumPhotoSelectionBody):
    _validate_album_id(album_id)
    return _album_call(albums.add_album_photos, album_id, write=True, **data.photo_kwargs())


@app.delete("/api/albums/{album_id}/photos")
def remove_album_photos(album_id: str, data: _AlbumRemovePhotosBody):
    _validate_album_id(album_id)
    return _album_call(albums.remove_album_photos, album_id, data.image_ids, write=True)


def _validate_map_bounds(west, east, south, north, from_date, to_date, album_id=None):
    _validated_photo_selection(GeoPhotoSelection(GeoBounds(west, east, south, north), from_date, to_date, album_id=album_id))


def _optional_map_bounds(west, east, south, north) -> GeoBounds | None:
    if all(value is None for value in (west, east, south, north)):
        return None
    if west is None or east is None or south is None or north is None:
        raise HTTPException(status_code=422, detail="Der Kartenausschnitt benötigt vier Grenzen.")
    return GeoBounds(west, east, south, north)


def _validate_map_cursor(offset, before_id, before_captured_at):
    if before_id is None and before_captured_at is not None:
        raise HTTPException(status_code=422, detail="before_captured_at requires before_id")
    if before_id is not None and offset:
        raise HTTPException(status_code=422, detail="offset must be zero with before_id")


@app.get("/api/map/points")
def get_map_points(
    west: float = Query(default=-180, ge=-180, le=180),
    east: float = Query(default=180, ge=-180, le=180),
    south: float = Query(default=-90, ge=-90, le=90),
    north: float = Query(default=90, ge=-90, le=90),
    from_date: date | None = None,
    to_date: date | None = None,
    zoom: int = Query(default=2, ge=0, le=20),
    grid_columns: int = Query(default=MAP_GRID_SIZE, ge=1, le=MAP_GRID_SIZE),
    grid_rows: int = Query(default=MAP_GRID_SIZE, ge=1, le=MAP_GRID_SIZE),
    album_id: str | None = None,
):
    """Return bounded photo aggregates for a map viewport."""
    generation = _get_media_library_generation()
    _validate_map_bounds(west, east, south, north, from_date, to_date, album_id)
    result = list_map_points(west, east, south, north, from_date, to_date, zoom, grid_columns, grid_rows, album_id=album_id)
    _get_media_library_generation(generation)
    return result


@app.get("/api/map/images")
def get_map_images(
    west: float = Query(default=-180, ge=-180, le=180),
    east: float = Query(default=180, ge=-180, le=180),
    south: float = Query(default=-90, ge=-90, le=90),
    north: float = Query(default=90, ge=-90, le=90),
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    before_id: int | None = Query(default=None, ge=1, le=2**63 - 1),
    before_captured_at: str | None = Query(default=None, min_length=1, max_length=64),
    album_id: str | None = None,
):
    """Return a stable page of geotagged photos in a viewport."""
    generation = _get_media_library_generation()
    _validate_map_bounds(west, east, south, north, from_date, to_date, album_id)
    _validate_map_cursor(offset, before_id, before_captured_at)
    page = list_map_images(
        west, east, south, north, from_date, to_date, limit, offset,
        before_id=before_id, before_captured_at=before_captured_at, album_id=album_id,
    )
    _get_media_library_generation(generation)
    return page


@app.post("/api/map/images/selection")
def get_selected_map_images(
    selection: Annotated[_GeoSelectionBody, Body(embed=True)],
    limit: Annotated[int, Query(ge=1, le=200)] = 40,
    offset: Annotated[int, Query(ge=0)] = 0,
    before_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    before_captured_at: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
):
    """Return one map photo page for an explicit selection."""
    generation = _get_media_library_generation()
    _validate_map_cursor(offset, before_id, before_captured_at)
    page = list_selected_map_images(selection.to_selection(), limit, offset, before_id, before_captured_at)
    _get_media_library_generation(generation)
    return page


@app.get("/api/map/journey")
def get_map_journey(
    from_date: date | None = None,
    to_date: date | None = None,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    detail: JourneyDetail = "trip",
    album_id: str | None = None,
):
    """Return observed journey groups for the specified capture period."""
    generation = _get_media_library_generation()
    result = get_journey(from_date, to_date, _optional_map_bounds(west, east, south, north), detail, album_id=album_id)
    _get_media_library_generation(generation)
    return result


@app.get("/api/map/observed-places")
def get_observed_places(
    from_date: date | None = None,
    to_date: date | None = None,
    album_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=300)] = 150,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    generation = _get_media_library_generation()
    selection = _validated_photo_selection(GeoPhotoSelection(
        from_date=from_date, to_date=to_date, album_id=album_id,
    ))
    result = list_observed_places(selection, limit, offset)
    _get_media_library_generation(generation)
    return result


@app.get("/api/images")
def get_images(
    request: Request,
    folders: List[str] = Query(default=[]),
    persons: List[str] = Query(default=[]),
    sort_by: str = Query(default="date"),
    sort_direction: str = Query(default="desc"),
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    face_statuses: List[str] = Query(default=[]),
    all_photos: bool = False,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    from_date: date | None = None,
    to_date: date | None = None,
    album_id: str | None = None,
):
    """List images and oriented face boxes.

    Args:
        folders: Optional folder roots used to filter images.
        persons: Optional person names used to require matching faces.
        sort_by: Primary gallery sort key.
        sort_direction: Primary gallery sort direction.
        limit: Maximum number of images returned in one page.
        offset: Starting image offset for pagination.

    Returns:
        Paginated image dictionaries containing nested face data.
    """
    bounds = _optional_map_bounds(west, east, south, north)
    selection = None
    if bounds or from_date or to_date or album_id is not None:
        selection = _validated_photo_selection(GeoPhotoSelection(bounds, from_date, to_date, album_id=album_id))
    return _get_images_page(request, folders, persons, sort_by, sort_direction, limit, offset,
                            face_statuses, all_photos, selection)


@app.post("/api/images/search")
def search_selected_images(
    request: Request,
    selection: Annotated[_GeoSelectionBody, Body(embed=True)],
    folders: List[str] = Query(default=[]),
    persons: List[str] = Query(default=[]),
    sort_by: str = Query(default="date"),
    sort_direction: str = Query(default="desc"),
    limit: Annotated[int, Query(ge=1, le=200)] = 40,
    offset: Annotated[int, Query(ge=0)] = 0,
    face_statuses: List[str] = Query(default=[]),
    all_photos: bool = False,
):
    """Apply regular library filters to an explicit photo selection."""
    return _get_images_page(request, folders, persons, sort_by, sort_direction, limit, offset,
                            face_statuses, all_photos, selection.to_selection())


def _get_images_page(request, folders, persons, sort_by, sort_direction, limit, offset,
                     face_statuses, all_photos, selection):
    generation = _get_media_library_generation()
    display_platform = get_display_platform(request)
    normalized_folders = [normalize_import_folder_path(folder) for folder in folders]
    rows, total = list_images_page(
        folders=normalized_folders,
        persons=persons,
        face_statuses=normalize_face_status_filters(face_statuses),
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
        all_photos=all_photos,
        **({"selection": selection} if selection is not None else {}),
    )
    items = _group_image_rows(rows, display_platform)
    available_persons = list_available_image_persons(normalized_folders)
    _get_media_library_generation(generation)
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "available_persons": available_persons,
    }


@app.get("/api/image-renames")
def api_get_image_rename_candidates(
    request: Request,
    folders: List[str] = Query(default=[]),
    persons: List[str] = Query(default=[]),
    sort_by: str = Query(default="date"),
    sort_direction: str = Query(default="desc"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    include_total: bool = Query(default=True),
):
    """List image paths whose filenames should be updated with person names."""
    display_platform = get_display_platform(request)
    normalized_folders = [normalize_import_folder_path(folder) for folder in folders]
    candidates, total = list_filename_rename_candidates(
        folders=normalized_folders,
        persons=persons,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    if include_total:
        total = count_filename_rename_candidates(
            folders=normalized_folders,
            persons=persons,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
    else:
        total = None

    items = []
    for candidate in candidates:
        display_directory = (
            to_display_path(candidate["directory"])
            if display_platform == "windows"
            else candidate["directory"]
        )
        display_path = (
            to_display_path(candidate["path"])
            if display_platform == "windows"
            else candidate["path"]
        )
        items.append(
            {
                **candidate,
                "directory": display_directory,
                "path": display_path,
                "proposed_path": os.path.join(
                    display_directory, candidate["proposed_filename"]
                ),
            }
        )

    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": len(items) == limit if total is None else offset + len(items) < total,
        "available_persons": list_available_image_persons(normalized_folders),
    }


@app.get("/api/image-renames/count")
def api_count_image_rename_candidates(
    folders: List[str] = Query(default=[]),
    persons: List[str] = Query(default=[]),
    sort_by: str = Query(default="date"),
    sort_direction: str = Query(default="desc"),
):
    """Count image paths whose filenames should be updated with person names."""
    normalized_folders = [normalize_import_folder_path(folder) for folder in folders]
    total = count_filename_rename_candidates(
        folders=normalized_folders,
        persons=persons,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    return {"total": total}


def _image_rename_selection(data):
    def filter_values(key):
        values = data.get(key, [])
        if not isinstance(values, list):
            raise HTTPException(status_code=422, detail="Die Dateiauswahl ist ungültig.")
        return (value for value in values if isinstance(value, str) and value.strip())

    return {
        "folders": [normalize_import_folder_path(path) for path in filter_values("folders")],
        "persons": [person.strip() for person in filter_values("persons")],
        "selected_paths": [normalize_import_folder_path(path) for path in filter_values("selected_paths")],
        "excluded_paths": [normalize_import_folder_path(path) for path in filter_values("excluded_paths")],
        "rename_all": bool(data.get("rename_all")),
        "sort_by": data.get("sort_by", "date"),
        "sort_direction": data.get("sort_direction", "desc"),
    }


@app.post("/api/image-renames/preview")
def api_preview_image_rename_candidates(request: Request, data: dict = Body(...)):
    generation = _get_media_library_generation()
    preview = preview_image_location_renames(**_image_rename_selection(data))
    _get_media_library_generation(generation)
    if get_display_platform(request) == "windows":
        for item in preview["items"]:
            item["path"] = to_display_path(item["path"])
            if item["target_path"]:
                item["target_path"] = to_display_path(item["target_path"])
    return preview


@app.post("/api/image-renames/apply")
@safe_cluster_mutation
def api_apply_image_rename_candidates(data: dict = Body(...)):
    """Rename selected image paths and update the database."""
    selection = _image_rename_selection(data)
    targets = data.get("confirmed_targets")
    if targets is not None:
        if not isinstance(targets, dict) or not all(
            isinstance(path, str) and path.strip() and isinstance(confirmation, dict)
            and isinstance(confirmation.get("target_path"), str) and confirmation["target_path"].strip()
            and isinstance(confirmation.get("source_token"), str)
            and re.fullmatch(r"[a-f0-9]{64}", confirmation["source_token"])
            for path, confirmation in targets.items()
        ):
            raise HTTPException(status_code=422, detail="Die bestätigten Dateinamen sind ungültig.")
        selection["confirmed_targets"] = {
            normalize_import_folder_path(path): normalize_import_folder_path(confirmation["target_path"])
            for path, confirmation in targets.items()
        }
        selection["confirmed_source_tokens"] = {
            normalize_import_folder_path(path): confirmation["source_token"]
            for path, confirmation in targets.items()
        }
    result = rename_image_locations_to_match_people(**selection)
    notify_clusters_changed("image_rename")
    return result


# -----------------------------
# FACE CROP
# -----------------------------


@app.get("/api/faces/{face_id}/crop")
async def api_face_crop(face_id: int, request: Request):
    """Return a JPEG crop for one detected face.

    Args:
        face_id: Face identifier to crop.

    Returns:
        JPEG response containing the oriented face region.

    Raises:
        HTTPException: If the face image cannot be found.
    """
    return await _serve_thumbnail(face_id, request, face=True)


frontend_dist_dir = get_frontend_dist_dir()
if frontend_dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist_dir, html=True), name="frontend")
