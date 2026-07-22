import asyncio
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from functools import partial, wraps
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import List

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from PIL import Image

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
from .models.face_model import get_compute_mode, get_execution_provider
from .services.desktop import (
    is_windows_host,
    is_wsl_host,
    normalize_import_folder_path,
    open_file_location,
    pick_folder,
    to_display_path,
)
from .services.face_thumbnails import ensure_face_thumbnail
from .services.face_thumbnail_warmup import FaceThumbnailWarmupQueue
from .services.cache import app_cache
from .services.autocluster_queue import (
    AutoClusterQueue,
    PRIORITY_IDLE_RECLUSTER,
    PRIORITY_MANUAL_RECLUSTER,
    PRIORITY_STARTUP_REPAIR,
    PRIORITY_VERSION_UPGRADE,
)
from .services.events import TrailingThrottle, event_hub
from .services.import_queue import ImportQueue
from .services.idle_recluster import IdleReclusterScheduler
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
    _safe_float,
    assign_cluster_to_person,
    accept_person_suggestions,
    accept_person_suggestion_assignments,
    build_filename_person_format_summary,
    build_folder_tree,
    count_filename_rename_candidates,
    delete_image,
    create_cluster_from_faces,
    assign_faces_to_person,
    auto_tune_cluster_distance_threshold,
    get_faces_for_review_group,
    get_image_detail_rows,
    get_available_image_path,
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
    get_person_faces,
    list_images_page,
    list_cluster_summaries,
    list_persons,
    list_person_suggestions,
    list_review_suggestions,
    mark_faces_with_review_status,
    dismiss_person_suggestion,
    dismiss_review_suggestion,
    accept_review_suggestions,
    normalize_face_review_group,
    normalize_face_status_filters,
    rename_cluster,
    rename_person,
    delete_person,
    rename_image_locations_to_match_people,
    restore_faces_to_manual_review,
    remove_faces_from_cluster,
    count_reclusterable_faces,
    count_scoped_reclusterable_faces,
    recluster_all_active_faces,
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

init_db()

_import_activity_lock = threading.Lock()
_import_was_busy = False
_version_clustering_lock = threading.Lock()
_version_clustering_pending = False
_cluster_operation_lock = threading.RLock()
_imports_finalizing = 0


def _describe_background_activity() -> str | None:
    """Describe background work for the UI. No longer gates any write."""
    if _imports_finalizing > 0:
        return (
            "Der Bildimport wird gerade sicher abgeschlossen und die Ansichten "
            "werden aktualisiert. Bitte warten Sie einen kurzen Moment."
        )
    import_snapshot = import_queue.snapshot()
    if isinstance(import_snapshot, dict) and (
        import_snapshot.get("running_count") or import_snapshot.get("queued_count")
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
    if task and task.get("status") in {"queued", "running"}:
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
    global _import_was_busy
    snapshot = import_queue.snapshot()
    event_hub.publish("imports", snapshot)
    busy = bool(snapshot.get("running_count") or snapshot.get("queued_count"))
    with _import_activity_lock:
        became_idle = _import_was_busy and not busy
        _import_was_busy = busy
    if became_idle:
        app_cache.clear()
        notify_clusters_changed("import_completed")
        if not schedule_version_clustering_upgrade():
            mark_cluster_assignments_dirty("import_completed")
        # Release any clustering task that was queued while the import ran and
        # let the thumbnail warmer start immediately instead of on its next poll.
        auto_cluster_queue.notify_ready()
        face_thumbnail_warmup_queue.wake()


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


def notify_clusters_changed(reason: str) -> None:
    """Broadcast that cluster/person/image data changed so clients refetch.

    Args:
        reason: Short label describing what triggered the change.
    """
    event_hub.publish("clusters", {"reason": reason})


# Background workers emit progress far faster than the UI needs it, and each
# emission rebuilds a full snapshot. Coalesce those bursts to a few per second
# (transitions and the final state are always delivered by the trailing run).
_publish_imports_throttled = TrailingThrottle(_publish_imports, interval=0.2)
_publish_autocluster_throttled = TrailingThrottle(_publish_autocluster, interval=0.2)


import_queue = ImportQueue(
    auto_start=False,
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
    if _imports_finalizing > 0:
        return False
    import_snapshot = import_queue.snapshot()
    return not (
        import_snapshot.get("running_count") or import_snapshot.get("queued_count")
    )


auto_cluster_queue = AutoClusterQueue(
    on_success=_handle_autocluster_success,
    on_change=_publish_autocluster_throttled,
    ready_gate=_reclustering_writer_available,
)


def is_backend_idle_for_thumbnail_warmup() -> bool:
    """Return whether low-priority thumbnail warming may run."""
    import_snapshot = import_queue.snapshot()
    if import_snapshot.get("running_count", 0) > 0:
        return False
    if import_snapshot.get("queued_count", 0) > 0:
        return False

    auto_cluster_snapshot = auto_cluster_queue.snapshot()
    task = auto_cluster_snapshot.get("task")
    if task and task.get("status") in {"queued", "running"}:
        return False
    return True


face_thumbnail_warmup_queue = FaceThumbnailWarmupQueue(
    is_idle=is_backend_idle_for_thumbnail_warmup,
    on_change=_publish_thumbnail_warmup,
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
            partial(recluster_all_active_faces, scoped=True)
            if scoped
            else recluster_all_active_faces
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
    rebuilt_faces = recluster_all_active_faces(progress_callback=progress_callback)
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
        if import_snapshot.get("running_count") or import_snapshot.get("queued_count"):
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
    if import_snapshot.get("running_count") or import_snapshot.get("queued_count"):
        return False
    task = auto_cluster_queue.snapshot().get("task")
    return not (task and task.get("status") in {"queued", "running"})


# A review session is full of short pauses, so the quiet period must be long
# enough that thinking time never triggers a rebuild.
IDLE_RECLUSTER_DEBOUNCE_SECONDS = 90.0

idle_recluster_scheduler = IdleReclusterScheduler(
    is_backend_idle_for_reclustering,
    lambda reason: schedule_full_recluster(reason, scoped=True),
    debounce_seconds=IDLE_RECLUSTER_DEBOUNCE_SECONDS,
)


def mark_cluster_assignments_dirty(reason: str) -> None:
    """Request one debounced reclustering run after assignment mutations."""
    idle_recluster_scheduler.mark_dirty(reason)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the durable import worker for the application lifetime.

    Args:
        _app: FastAPI application managed by this lifespan.

    Yields:
        Control while the application is accepting requests.
    """
    try:
        event_hub.bind_loop(asyncio.get_running_loop())
        idle_recluster_scheduler.start()
        import_queue.start()
        if not schedule_version_clustering_upgrade():
            run_startup_repairs()
        face_thumbnail_warmup_queue.start()
    except Exception:
        logger.exception("Could not start import queue during application startup")
        raise
    try:
        yield
    finally:
        idle_recluster_scheduler.stop()
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


app = FastAPI(title="Face Manager API", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    first launch does not dump the entire history.
    """
    last_seen = get_last_seen_changelog_version()
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
    }


@app.get("/api/changelog")
def api_full_changelog():
    """Return the complete released changelog history, newest first."""
    try:
        document = load_changelog(get_changelog_path())
    except (ChangelogError, OSError):
        logger.exception("Could not load full changelog")
        return {"versions": []}
    return {"versions": released_versions(document)}


@app.post("/api/changelog/current/acknowledge")
def api_acknowledge_current_changelog():
    """Remember that the running version's notes were dismissed."""
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
    """Launch a verified Windows installer, then close the current app."""
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
    schedule_process_exit()
    return {"installing": True, "version": version}


@app.get("/api/runtime")
def api_runtime():
    """Return the active inference provider and compute mode.

    Returns:
        Runtime provider details used by the UI badge.
    """
    execution_provider = get_execution_provider()
    return {
        "compute_mode": get_compute_mode(execution_provider),
        "execution_provider": execution_provider,
        "host_platform": "windows" if is_windows_host() else "linux",
        "display_platform": "windows" if is_windows_host() else "linux",
    }


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
    if snapshot["running_count"] or snapshot["queued_count"]:
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


def validate_database_file(path: Path) -> None:
    """Verify that a file is a readable SQLite database with core tables."""
    conn = None
    try:
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise HTTPException(
            status_code=400,
            detail="Die ausgewählte Datei ist keine gültige Face-Manager-Sicherung.",
        ) from exc
    finally:
        if conn is not None:
            conn.close()

    table_names = {row[0] for row in rows}
    required = {"image", "face", "cluster", "person"}
    if not required.issubset(table_names):
        raise HTTPException(
            status_code=400,
            detail="In der ausgewählten Datei fehlen benötigte Face-Manager-Daten.",
        )


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

    return app_cache.get_or_set(
        ("api_settings",),
        load_settings,
        ttl_seconds=5.0,
        tags={"settings"},
    )


@app.put("/api/settings")
@safe_cluster_mutation
def api_update_settings(data: dict = Body(...)):
    """Persist mutable application settings."""
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


@app.post("/api/database/import")
@safe_cluster_mutation
def api_import_database(payload: bytes = Body(..., media_type="application/octet-stream")):
    """Replace the current database with an uploaded SQLite file."""
    ensure_database_is_idle()
    if not payload:
        raise HTTPException(status_code=400, detail="Es wurde keine Sicherungsdatei ausgewählt.")

    current_db_path = Path(DB_PATH)
    current_db_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path_str = tempfile.mkstemp(
        suffix=".sqlite",
        prefix="face-manager-import-",
        dir=current_db_path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_path_str)
    temp_path.write_bytes(payload)
    backup_path = current_db_path.with_name(f"{current_db_path.stem}.pre-import-backup.sqlite")

    try:
        if current_db_path.exists():
            shutil.copy2(DB_PATH, backup_path)
        validate_database_file(temp_path)
        shutil.move(str(temp_path), DB_PATH)
        wal_path = current_db_path.with_name(f"{current_db_path.name}-wal")
        shm_path = current_db_path.with_name(f"{current_db_path.name}-shm")
        for sidecar_path in (wal_path, shm_path):
            if sidecar_path.exists():
                sidecar_path.unlink()
        init_db()
        if not schedule_version_clustering_upgrade():
            run_startup_repairs("database_import")
        reset_import_resources()
        app_cache.clear()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Database import failed; attempting recovery")
        try:
            if backup_path.exists():
                shutil.move(str(backup_path), DB_PATH)
                init_db()
                if not schedule_version_clustering_upgrade():
                    run_startup_repairs("database_import_recovery")
                reset_import_resources()
                app_cache.clear()
            elif current_db_path.exists():
                current_db_path.unlink()
                init_db()
                if not schedule_version_clustering_upgrade():
                    run_startup_repairs("database_import_recovery")
                reset_import_resources()
                app_cache.clear()
        except Exception:
            logger.exception("Automatic recovery after failed database import also failed")
        raise HTTPException(
            status_code=500,
            detail=(
                "Die Sicherung konnte nicht wiederhergestellt werden. "
                "Face Manager hat nach Möglichkeit den vorherigen Stand beibehalten."
            ),
        ) from exc
    finally:
        if backup_path.exists():
            backup_path.unlink()
        if temp_path.exists():
            temp_path.unlink()

    return {"status": "imported"}


@app.post("/api/process-folder")
@safe_import_enqueue
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
@safe_import_enqueue
def api_create_import(request: Request, data: dict = Body(...)):
    """Queue a folder for serialized background import.

    Args:
        data: Request body containing ``folder_path``.

    Returns:
        Newly queued import job.

    Raises:
        HTTPException: If the folder path is missing or invalid.
    """
    if "folder_path" not in data:
        raise HTTPException(status_code=400, detail="Wähle einen Bilderordner aus.")

    folder_path = normalize_import_folder_path(data["folder_path"])

    if not os.path.isdir(folder_path):
        raise HTTPException(
            status_code=400,
            detail=f"Der ausgewählte Ordner ist nicht verfügbar: {folder_path}",
        )

    payload = import_queue.enqueue(folder_path)
    if get_display_platform(request) == "windows":
        payload["folder_path"] = to_display_path(payload["folder_path"])
    return payload


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


@app.get("/api/autocluster-tasks")
def api_autocluster_tasks():
    """Return the current background auto-clustering repair task, if any."""
    return auto_cluster_queue.snapshot()


@app.get("/api/thumbnail-warmup")
def api_thumbnail_warmup():
    """Return the current low-priority thumbnail warmup state."""
    return face_thumbnail_warmup_queue.snapshot()


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
    return get_cluster_overview()


@app.get("/api/person-suggestions")
def api_person_suggestions():
    """List conservative, reviewable matches for existing people."""
    return list_person_suggestions()


@app.post("/api/person-suggestions/accept")
@safe_cluster_mutation
def api_accept_person_suggestions(data: dict = Body(...)):
    """Confirm one or several proposals in a single user action."""
    assignments = data.get("assignments")
    if isinstance(assignments, list):
        try:
            updated = accept_person_suggestion_assignments(assignments)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reset_import_resources()
        mark_cluster_assignments_dirty("accept_person_suggestions")
        notify_clusters_changed("accept_person_suggestions")
        return {"status": "ok", "accepted_count": updated}

    person_id = data.get("person_id") or data.get("personId")
    cluster_ids = data.get("cluster_ids") or data.get("clusterIds")
    if person_id is None or not isinstance(cluster_ids, list):
        raise HTTPException(
            status_code=400,
            detail="Wähle eine Person und mindestens eine Gesichtsgruppe aus.",
        )
    try:
        updated = accept_person_suggestions(int(person_id), cluster_ids)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty("accept_person_suggestions")
    notify_clusters_changed("accept_person_suggestions")
    return {"status": "ok", "accepted_count": updated}


@app.post("/api/person-suggestions/{cluster_id}/dismiss")
@safe_cluster_mutation
def api_dismiss_person_suggestion(cluster_id: int):
    """Dismiss a proposal while leaving its cluster unassigned."""
    try:
        dismiss_person_suggestion(cluster_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    notify_clusters_changed("dismiss_person_suggestion")
    return {"status": "ok"}


@app.get("/api/review-suggestions")
def api_review_suggestions():
    """List conservative proposals learned from explicit review decisions."""
    return list_review_suggestions()


@app.post("/api/review-suggestions/accept")
@safe_cluster_mutation
def api_accept_review_suggestions(data: dict = Body(...)):
    cluster_ids = data.get("cluster_ids") or data.get("clusterIds") or []
    try:
        updated = accept_review_suggestions(cluster_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reset_import_resources()
    mark_cluster_assignments_dirty("accept_review_suggestions")
    notify_clusters_changed("accept_review_suggestions")
    return {"status": "ok", "accepted_count": updated}


@app.post("/api/review-suggestions/{cluster_id}/dismiss")
@safe_cluster_mutation
def api_dismiss_review_suggestion(cluster_id: int):
    try:
        dismiss_review_suggestion(cluster_id)
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
    cluster = get_cluster_summary(cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Die Gesichtsgruppe wurde nicht gefunden.")
    return {
        **cluster,
        "faces": get_cluster_faces(cluster_id),
    }


@app.get("/api/face-review-groups/{group_key}/faces")
def api_face_review_group_faces(group_key: str):
    """Return faces for one non-cluster review queue."""
    try:
        normalize_face_review_group(group_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_faces_for_review_group(group_key)


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
        elif action == "assign_person":
            person_name = data.get("person_name") or data.get("personName")
            if not person_name or not str(person_name).strip():
                raise HTTPException(status_code=400, detail="Gib einen Namen ein.")
            cluster_id = assign_faces_to_person(face_ids, str(person_name))
            response = {"status": "ok", "cluster_id": cluster_id}
        elif action == "mark_unknown_person":
            updated = mark_faces_with_review_status(face_ids, "unknown_person")
            response = {"status": "ok", "updated_count": updated}
        elif action == "mark_not_face":
            updated = mark_faces_with_review_status(face_ids, "not_face")
            response = {"status": "ok", "updated_count": updated}
        elif action == "restore_to_manual_review":
            updated = restore_faces_to_manual_review(face_ids)
            response = {"status": "ok", "updated_count": updated}
        else:
            raise HTTPException(status_code=400, detail="Diese Aktion ist nicht verfügbar.")
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
    name = data.get("name") or data.get("person_name") or data.get("personName")
    if not name or not str(name).strip():
        raise HTTPException(status_code=400, detail="Gib einen Namen ein.")
    try:
        next_person_id = rename_person(person_id, str(name))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    reset_import_resources()
    return {"status": "ok", "person_id": next_person_id}


@app.delete("/api/persons/{person_id}")
@safe_cluster_mutation
def api_delete_person(person_id: int, reassignment_group: str = Query(...)):
    """Delete one person and reclassify all assigned faces."""
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


# -----------------------------
# IMAGES
# -----------------------------


@app.get("/api/images/{image_id}/file")
def get_image(image_id: int):
    """Serve an image from an available filesystem location.

    Args:
        image_id: Canonical image identifier.

    Returns:
        Streaming file response.

    Raises:
        HTTPException: If no image location exists.
    """
    path = get_available_image_path(image_id)
    if not path:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    return FileResponse(path)


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


@lru_cache(maxsize=2048)
def get_image_orientation(path):
    """Read and cache EXIF orientation and dimensions.

    Args:
        path: Image path to inspect.

    Returns:
        Orientation value, width, and height.
    """
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            orientation = exif.get(274, 1)
            return orientation, img.width, img.height
    except (OSError, ValueError):
        return 1, 0, 0


def correct_bbox_for_orientation(path, x, y, w, h):
    """Transform a face box according to image EXIF orientation.

    Args:
        path: Image path used to inspect orientation.
        x: Original horizontal coordinate.
        y: Original vertical coordinate.
        w: Original box width.
        h: Original box height.

    Returns:
        Corrected ``x``, ``y``, width, and height tuple.
    """
    orientation, width, height = get_image_orientation(path)

    # Orientation corrections
    if orientation == 3:  # 180°
        return (
            width - x - w,
            height - y - h,
            w,
            h,
        )
    if orientation == 6:  # 90° CW
        return (
            height - y - h,
            x,
            h,
            w,
        )
    if orientation == 8:  # 270° CW
        return (
            y,
            width - x - w,
            h,
            w,
        )

    return x, y, w, h


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


def _group_image_rows(rows, display_platform: str) -> list[dict]:
    """Turn image/face rows into the payload the picture views expect.

    Shared by the paged library listing and the single-image lookup so both
    always describe an image the same way.
    """
    images = {}

    for r in rows:
        image_id = r["image_id"]
        path = r["image_path"]

        if image_id not in images:
            images[image_id] = {
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

        bbox_x, bbox_y, bbox_w, bbox_h = correct_bbox_for_orientation(
            path,
            _safe_float(r["bbox_x"]),
            _safe_float(r["bbox_y"]),
            _safe_float(r["bbox_w"]),
            _safe_float(r["bbox_h"]),
        )

        review_status = (
            r["review_status"] if "review_status" in r.keys() else "active"
        )
        images[image_id]["faces"].append(
            {
                "id": r["face_id"],
                "bbox_x": bbox_x,
                "bbox_y": bbox_y,
                "bbox_w": bbox_w,
                "bbox_h": bbox_h,
                "cluster_id": r["cluster_id"],
                "person_name": r["person_name"],
                "review_status": review_status,
            }
        )

    locations_by_image = list_image_locations(list(images.keys()))
    for image_id, image in images.items():
        locations = locations_by_image.get(image_id, [])
        image["locations"] = serialize_image_locations(locations, display_platform)
        image["location_count"] = len(locations)
    return list(images.values())


@app.get("/api/images/{image_id}/detail")
def api_image_detail(request: Request, image_id: int):
    """Return one image with its faces so a face crop can be shown in context."""
    rows = get_image_detail_rows(image_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")
    display_platform = get_display_platform(request)
    items = _group_image_rows(rows, display_platform)

    locations_by_image = list_image_locations([image_id])
    for image in items:
        locations = locations_by_image.get(image["id"], [])
        image["locations"] = serialize_image_locations(locations, display_platform)
        image["location_count"] = len(locations)
    return items[0]


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
    )
    items = _group_image_rows(rows, display_platform)
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
        "available_persons": list_available_image_persons(normalized_folders),
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


@app.post("/api/image-renames/apply")
@safe_cluster_mutation
def api_apply_image_rename_candidates(data: dict = Body(...)):
    """Rename selected image paths and update the database."""
    folders = [
        normalize_import_folder_path(path)
        for path in data.get("folders", [])
        if isinstance(path, str) and path.strip()
    ]
    persons = [
        person.strip()
        for person in data.get("persons", [])
        if isinstance(person, str) and person.strip()
    ]
    selected_paths = [
        normalize_import_folder_path(path)
        for path in data.get("selected_paths", [])
        if isinstance(path, str) and path.strip()
    ]
    excluded_paths = [
        normalize_import_folder_path(path)
        for path in data.get("excluded_paths", [])
        if isinstance(path, str) and path.strip()
    ]
    result = rename_image_locations_to_match_people(
        selected_paths=selected_paths,
        rename_all=bool(data.get("rename_all")),
        excluded_paths=excluded_paths,
        folders=folders,
        persons=persons,
        sort_by=data.get("sort_by", "date"),
        sort_direction=data.get("sort_direction", "desc"),
    )
    notify_clusters_changed("image_rename")
    return result


# -----------------------------
# FACE CROP
# -----------------------------


@app.get("/api/faces/{face_id}/crop")
def api_face_crop(face_id: int):
    """Return a JPEG crop for one detected face.

    Args:
        face_id: Face identifier to crop.

    Returns:
        JPEG response containing the oriented face region.

    Raises:
        HTTPException: If the face image cannot be found.
    """
    conn = get_conn()
    row = conn.execute(
        """
        SELECT f.image_id, f.bbox_x, f.bbox_y, f.bbox_w, f.bbox_h
        FROM face f
        WHERE f.id = ?
        """,
        (face_id,),
    ).fetchone()
    conn.close()
    path = get_available_image_path(row["image_id"]) if row else None
    if not path:
        raise HTTPException(status_code=404, detail="Das Bild wurde nicht gefunden.")

    x = int(_safe_float(row["bbox_x"]))
    y = int(_safe_float(row["bbox_y"]))
    w = int(_safe_float(row["bbox_w"]))
    h = int(_safe_float(row["bbox_h"]))
    try:
        thumbnail_path = ensure_face_thumbnail(face_id, path, (x, y, w, h))
    except (OSError, ValueError) as exc:
        logger.exception("Could not create face crop thumbnail for face %s", face_id)
        raise HTTPException(status_code=404, detail="Der Gesichtsausschnitt wurde nicht gefunden.") from exc
    # A face crop is immutable: re-importing an image assigns a new face id and
    # deletes the old thumbnail, so a given id always maps to the same pixels.
    # Let the browser serve it straight from disk cache without revalidating.
    return FileResponse(
        thumbnail_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


frontend_dist_dir = get_frontend_dist_dir()
if frontend_dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist_dir, html=True), name="frontend")
