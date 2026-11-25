"""Analyze verified local staging files and exchange fenced, compatible results."""

from __future__ import annotations

import base64
import binascii
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import stat
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
import time
import unicodedata

import numpy as np
from PIL import Image

from ..config import EMBEDDING_DIM
from .preview_cache import get_image_geometry
from .shared_sync_jobs import make_job_descriptor, validate_job_descriptor
from .shared_sync_transport import MAX_ATTEMPTS, InvalidTransportData, PublishConflictError, UnknownPublishError

MAX_FACES = 256
MAX_IMAGE_PIXELS = 90_000_000
_WIRE_VERSION = 1
_EMBEDDING_FORMAT = "f32le"
_PENDING_RESULT_LIMIT = 4
_OBSERVATION_LIMIT = 32
_EMBEDDING_BYTES = EMBEDDING_DIM * 4
_MAX_LOCAL_STAGE_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEVICE_ID = re.compile(r"[0-9a-f]{32}")
_ANALYSIS_FIELDS = {"version", "embedding_format", "photo_sha256", "pipeline_signature", "geometry", "faces"}
_ATTRIBUTION_FIELDS = {"worker_name", "worker_device_id"}


@dataclass(frozen=True)
class SharedAnalysisOutcome:
    state: str
    job: dict
    faces: list[dict] | None = None
    geometry: tuple[int, int, int] | None = None
    worker_name: str | None = None
    worker_device_id: str | None = None


class _Unavailable(RuntimeError):
    """This attempt cannot advance before a new worker response."""


def _validate_geometry(geometry) -> tuple[int, int, int]:
    if (
        not isinstance(geometry, (tuple, list)) or len(geometry) != 3
        or any(type(value) is not int for value in geometry)
    ):
        raise InvalidTransportData("Invalid shared analysis image dimensions")
    orientation, width, height = geometry
    if not 1 <= orientation <= 8 or width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise InvalidTransportData("Shared analysis image dimensions exceed the limits")
    return orientation, width, height


def _validate_box(bbox, geometry) -> tuple[int, int, int, int]:
    if (
        not isinstance(bbox, (tuple, list)) or len(bbox) != 4
        or any(type(value) is not int for value in bbox)
    ):
        raise InvalidTransportData("Invalid shared face box")
    x, y, width, height = bbox
    _, image_width, image_height = geometry
    if not (
        -image_width <= x < image_width and -image_height <= y < image_height
        and 0 < width <= 2 * image_width and 0 < height <= 2 * image_height
        and x + width > 0 and y + height > 0
    ):
        raise InvalidTransportData("Shared face box lies outside the image")
    return x, y, width, height


def _validate_embedding(vector: np.ndarray) -> np.ndarray:
    if vector.shape != (EMBEDDING_DIM,) or not np.isfinite(vector).all():
        raise InvalidTransportData("Invalid shared face embedding")
    if abs(float(np.linalg.norm(vector.astype(np.float64))) - 1.0) > 0.001:
        raise InvalidTransportData("Shared face embedding is not normalized")
    return vector


def _worker_attribution(value: dict) -> tuple[str | None, str | None]:
    name, device = value.get("worker_name"), value.get("worker_device_id")
    if not isinstance(name, str) or not isinstance(device, str) or _DEVICE_ID.fullmatch(device) is None:
        return None, None
    if not 1 <= len(name) <= 80 or not name.strip() or any(
        char in "/\\" or unicodedata.category(char)[0] == "C" or unicodedata.category(char) in ("Zl", "Zp")
        for char in name
    ):
        return None, None
    return name.strip(), device


def encode_analysis(
    job: dict, geometry: tuple[int, int, int], faces: list[dict], *,
    worker_name: str | None = None, worker_device_id: str | None = None,
) -> dict:
    """Serialize a bounded result from the local face model."""
    geometry = _validate_geometry(geometry)
    if not isinstance(faces, list) or len(faces) > MAX_FACES:
        raise InvalidTransportData("Shared analysis returned too many faces")
    serialized = []
    for face in faces:
        try:
            bbox = [
                int(value) if isinstance(value, np.integer) else value
                for value in face["bbox"]
            ]
            embedding = np.asarray(face["embedding"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidTransportData("Invalid local model face result") from exc
        if embedding.dtype.kind != "f" or embedding.dtype.itemsize != 4:
            raise InvalidTransportData("Shared face embeddings must use float32")
        vector = _validate_embedding(embedding).astype("<f4", copy=False)
        serialized.append({
            "bbox": list(_validate_box(bbox, geometry)),
            "embedding": base64.b64encode(vector.tobytes()).decode("ascii"),
        })
    result = {
        "version": _WIRE_VERSION,
        "embedding_format": _EMBEDDING_FORMAT,
        "photo_sha256": job["photo_sha256"],
        "pipeline_signature": job["pipeline_signature"],
        "geometry": list(geometry),
        "faces": serialized,
    }
    name, device = _worker_attribution({"worker_name": worker_name, "worker_device_id": worker_device_id})
    if name is not None:
        result.update(worker_name=name, worker_device_id=device)
    return result


def decode_analysis(job: dict, value: dict) -> tuple[tuple[int, int, int], list[dict]]:
    """Validate a committed result before exposing native float32 data to import."""
    if (
        not isinstance(value, dict)
        or not _ANALYSIS_FIELDS <= value.keys() <= _ANALYSIS_FIELDS | _ATTRIBUTION_FIELDS
        or type(value["version"]) is not int or value["version"] != _WIRE_VERSION
        or value["embedding_format"] != _EMBEDDING_FORMAT
        or value["photo_sha256"] != job["photo_sha256"]
        or value["pipeline_signature"] != job["pipeline_signature"]
    ):
        raise InvalidTransportData("Shared analysis does not match the photo and active pipeline")
    geometry = _validate_geometry(value["geometry"])
    if not isinstance(value["faces"], list) or len(value["faces"]) > MAX_FACES:
        raise InvalidTransportData("Shared analysis exceeds the face limit")
    faces = []
    base64_length = 4 * ((_EMBEDDING_BYTES + 2) // 3)
    for face in value["faces"]:
        if not isinstance(face, dict) or face.keys() != {"bbox", "embedding"}:
            raise InvalidTransportData("Invalid shared face data")
        encoded = face["embedding"]
        if not isinstance(encoded, str) or len(encoded) != base64_length:
            raise InvalidTransportData("Invalid shared face embedding length")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise InvalidTransportData("Invalid shared face embedding encoding") from exc
        if len(data) != _EMBEDDING_BYTES or base64.b64encode(data).decode("ascii") != encoded:
            raise InvalidTransportData("Invalid shared face embedding encoding")
        vector = _validate_embedding(np.frombuffer(data, dtype="<f4")).astype(np.float32, copy=True)
        faces.append({"bbox": _validate_box(face["bbox"], geometry), "embedding": vector})
    return geometry, faces


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_staged_photo(path: Path, photo_sha256: str) -> tuple[tuple[int, int, int], np.ndarray]:
    """Decode only the exact verified bytes in the request's private local directory."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    seen = path.lstat()
    attributes = getattr(seen, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(seen.st_mode) or seen.st_nlink != 1
        or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not 0 < seen.st_size <= _MAX_LOCAL_STAGE_BYTES
    ):
        raise InvalidTransportData("Shared analysis stage is redirected or invalid")
    digest = hashlib.sha256()
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        identity = _identity(seen)
        if _identity(os.fstat(source.fileno())) != identity:
            raise InvalidTransportData("Shared analysis stage changed before reading")
        remaining = seen.st_size
        while remaining:
            block = source.read(min(remaining, 1024 * 1024))
            if not block:
                raise InvalidTransportData("Shared analysis stage ended before its recorded length")
            digest.update(block)
            remaining -= len(block)
        if digest.hexdigest() != photo_sha256:
            raise InvalidTransportData("Shared analysis stage differs from the verified photo")
        source.seek(0)
        with Image.open(source) as image:
            geometry = _validate_geometry(get_image_geometry(image))
            pixels = np.asarray(image.convert("RGB"))
        if _identity(os.fstat(source.fileno())) != identity or _identity(path.lstat()) != identity:
            raise InvalidTransportData("Shared analysis stage changed while decoding")
    return geometry, pixels


class SharedAnalysisExchange:
    """Drive isolated I/O from an import thread, outside SQLite transactions.

    The injected request function handles IPC deadlines and child cleanup. If a
    lease hook is provided it supplies a context manager that maintains that
    lease on a separate, bounded lane. Otherwise renewals happen at checkpoints.
    """

    def __init__(
        self, *, request, sync_root, source_root, manifest, device_id, session_id,
        allow_writes: bool, on_lease=None, heartbeat_request=None, heartbeat_seconds: float = 10,
        max_attempt_seconds: float = 900,
        worker_name: str | None = None,
    ):
        if on_lease is not None and heartbeat_request is not None:
            raise ValueError("Use one shared analysis lease renewal mechanism")
        if not math.isfinite(heartbeat_seconds) or heartbeat_seconds <= 0:
            raise ValueError("Invalid shared analysis heartbeat interval")
        if not math.isfinite(max_attempt_seconds) or max_attempt_seconds <= 0:
            raise ValueError("Invalid shared analysis attempt deadline")
        self.request = request
        self.sync_root = str(sync_root)
        self.source_root = str(source_root)
        self.manifest = dict(manifest)
        self.device_id = device_id
        self.session_id = session_id
        self.worker_name = worker_name
        self.allow_writes = allow_writes is True
        self._on_lease = on_lease
        self._heartbeat_request = heartbeat_request
        self._heartbeat_seconds = heartbeat_seconds
        self._max_attempt_seconds = max_attempt_seconds
        self._heartbeat_lane = Lock()
        self._lock = Lock()
        self._active = set()
        self._pending = OrderedDict()
        self._observations = OrderedDict()

    def describe(self, model, photo_sha256: str) -> dict:
        return make_job_descriptor(self.manifest, photo_sha256, model.shared_pipeline_signature())

    def _job(self, operation: str, job: dict, *, runner=None, **fields):
        response = (runner or self.request)({
            "action": "job", "operation": operation, "job": job,
            "sync_root": self.sync_root, "known_manifest": self.manifest,
            "device_id": self.device_id, "session_id": self.session_id,
            "allow_writes": self.allow_writes, **fields,
        })
        if not isinstance(response, dict):
            raise InvalidTransportData("Invalid shared analysis worker reply")
        status = response.get("status")
        if status == "published_unknown":
            raise UnknownPublishError("Shared analysis publication outcome is unknown")
        if status == "transport_error" and response.get("error") == "PublishConflictError":
            raise PublishConflictError("Another worker closed the shared analysis attempt")
        if status in ("unavailable", "timeout", "share_changed") or (
            status == "transport_error" and response.get("error") == "ReadOnlyTransportError"
        ):
            raise _Unavailable()
        if status != "job" or response.get("operation") != operation or "value" not in response:
            raise InvalidTransportData("Shared analysis worker rejected the operation")
        return response["value"]

    def _result(self, job: dict, state: str) -> SharedAnalysisOutcome | None:
        value = self._job("get_result", job)
        if value is None:
            return None
        geometry, faces = decode_analysis(job, value)
        with self._lock:
            self._pending.pop(job["job_id"], None)
        worker_name, worker_device_id = _worker_attribution(value)
        return SharedAnalysisOutcome(state, job, faces, geometry, worker_name, worker_device_id)

    def _renew(self, job: dict, attempt: int):
        if self._on_lease is None and self._heartbeat_request is None:
            self._job("heartbeat", job, attempt=attempt, counter=max(1, time.monotonic_ns()))

    @contextmanager
    def _lease(self, job: dict, attempt: int):
        deadline = time.monotonic() + self._max_attempt_seconds

        def ensure_before_deadline():
            if time.monotonic() >= deadline:
                raise _Unavailable()

        if self._on_lease is not None:
            with self._on_lease(job, attempt):
                yield ensure_before_deadline
            return
        if self._heartbeat_request is None:
            yield ensure_before_deadline
            return
        if not self._heartbeat_lane.acquire(blocking=False):
            raise _Unavailable()
        stop = Event()
        failed = Event()

        def renew():
            self._job(
                "heartbeat", job, runner=self._heartbeat_request,
                attempt=attempt, counter=max(1, time.monotonic_ns()),
            )

        def heartbeat():
            try:
                while not stop.wait(max(0, min(self._heartbeat_seconds, deadline - time.monotonic()))):
                    try:
                        ensure_before_deadline()
                        renew()
                    except Exception:
                        failed.set()
                        return
            finally:
                self._heartbeat_lane.release()

        def ensure_active():
            ensure_before_deadline()
            if failed.is_set():
                raise _Unavailable()

        try:
            renew()
            thread = Thread(target=heartbeat, name="shared-analysis-heartbeat", daemon=True)
            thread.start()
        except BaseException:
            self._heartbeat_lane.release()
            raise
        try:
            yield ensure_active
        finally:
            stop.set()
            # If IPC remains blocked, the loop retains the sole lane until
            # its call exits. A later job cannot create replacement threads.
            thread.join(timeout=0.1)

    def reset_observations(self) -> None:
        """Call when the connection, process, or monotonic observation epoch changes."""
        with self._lock:
            self._observations.clear()

    def reclaim(self, job: dict, *, server_fresh: bool = False, lease_seconds: float = 60) -> dict:
        """Observe or expire a different session's lease using a qualified mount."""
        status = self._job("inspect", job)
        if not isinstance(status, dict):
            raise InvalidTransportData("Invalid shared analysis work status")
        if not self.allow_writes or server_fresh is not True:
            self.reset_observations()
            return status
        if status.get("state") != "reserved":
            return status
        if type(lease_seconds) not in (int, float) or not 1 <= lease_seconds <= 86400:
            raise ValueError("Invalid shared analysis lease duration")
        attempt = status["attempt"]
        if type(attempt) is not int or not 1 <= attempt <= MAX_ATTEMPTS:
            raise InvalidTransportData("Invalid shared analysis attempt")
        key = (job["job_id"], attempt)
        with self._lock:
            previous = self._observations.get(key)
        if previous is not None and time.monotonic() - previous["checked_at"] > lease_seconds / 2:
            # Restart observation after a gap (suspend, an offline worker, or
            # a poller that could not confirm the server for a long interval).
            previous = None
        observed = self._job(
            "observe", job, attempt=attempt, observation=previous, server_fresh=True,
        )
        if (
            not isinstance(observed, dict) or observed.get("job_id") != job["job_id"]
            or type(observed.get("attempt")) is not int or observed["attempt"] != attempt
            or any(
                type(observed.get(field)) not in (int, float) or not 0 <= observed[field] < float("inf")
                for field in ("checked_at", "unchanged_since")
            )
            or observed["unchanged_since"] > observed["checked_at"]
        ):
            raise InvalidTransportData("Invalid shared analysis lease observation")
        with self._lock:
            self._observations[key] = observed
            self._observations.move_to_end(key)
            if len(self._observations) > _OBSERVATION_LIMIT:
                self._observations.popitem(last=False)
        if observed["checked_at"] - observed["unchanged_since"] < lease_seconds:
            return status
        try:
            self._job("expire", job, observation=observed, server_fresh=True, lease_seconds=lease_seconds)
        except PublishConflictError:
            # Another session may have closed the attempt or renewed during
            # our expiry check. Let the scheduler read the fixed next slot.
            return self._job("inspect", job)
        with self._lock:
            self._observations.pop(key, None)
        return self._job("claim", job)

    def _stage_and_analyze(self, model, job: dict, attempt: int, relative_path: str) -> dict | None:
        with TemporaryDirectory(prefix="face-manager-analysis-") as directory:
            # All SMB accesses happen in the worker. Inference only opens the
            # local, exclusively created output in the private IPC directory.
            staging_root = Path(directory).absolute()
            output_path = staging_root / "photo"
            response = self.request({
                "action": "stage_source", "source_root": self.source_root,
                "relative_path": relative_path, "expected_hash": job["photo_sha256"],
                "output_path": str(output_path),
            }, staging_root=staging_root)
            if not isinstance(response, dict):
                raise InvalidTransportData("Invalid shared analysis source reply")
            status = response.get("status")
            if status == "source_changed":
                return None
            if status in ("unavailable", "timeout"):
                raise _Unavailable()
            if (
                status != "verified" or response.get("photo_sha256") != job["photo_sha256"]
                or response.get("output_path") != str(output_path)
            ):
                raise InvalidTransportData("Shared analysis source was not verified")
            geometry, pixels = _read_staged_photo(output_path, job["photo_sha256"])
            self._renew(job, attempt)
            if model.shared_pipeline_signature() != job["pipeline_signature"]:
                raise InvalidTransportData("Shared analysis pipeline changed before inference")
            faces = model.detect_and_embed(pixels)
            if model.shared_pipeline_signature() != job["pipeline_signature"]:
                raise InvalidTransportData("Shared analysis pipeline changed during inference")
            return encode_analysis(
                job, geometry, faces, worker_name=self.worker_name, worker_device_id=self.device_id,
            )

    def _owned(self, model, relative_path: str, job: dict, attempt: int) -> SharedAnalysisOutcome:
        with self._lease(job, attempt) as ensure_active:
            self._renew(job, attempt)
            with self._lock:
                saved = self._pending.get(job["job_id"])
                wire = saved[1] if saved is not None and saved[0] == attempt else None
            if wire is None:
                wire = self._stage_and_analyze(model, job, attempt, relative_path)
                if wire is None:
                    return SharedAnalysisOutcome("source_changed", job)
                with self._lock:
                    self._pending[job["job_id"]] = (attempt, wire)
                    self._pending.move_to_end(job["job_id"])
                    if len(self._pending) > _PENDING_RESULT_LIMIT:
                        self._pending.popitem(last=False)
            self._renew(job, attempt)
            ensure_active()
            digest = self._job("stage_result", job, attempt=attempt, result=wire)
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise InvalidTransportData("Invalid shared analysis result digest")
            ensure_active()
            decision = self._job("finish_result", job, attempt=attempt, result_digest=digest)
            if (
                not isinstance(decision, dict) or decision.get("state") != "completed"
                or decision.get("job_id") != job["job_id"] or decision.get("attempt") != attempt
                or decision.get("result_digest") != digest
            ):
                raise InvalidTransportData("Shared analysis terminal decision differs from the submitted result")
            geometry, faces = decode_analysis(job, wire)
            with self._lock:
                self._pending.pop(job["job_id"], None)
            worker_name, worker_device_id = _worker_attribution(wire)
            return SharedAnalysisOutcome("analyzed", job, faces, geometry, worker_name, worker_device_id)

    def analyze_announced(
        self, model, job: dict, relative_path: str, *,
        server_fresh: bool = False, lease_seconds: float = 60,
    ) -> SharedAnalysisOutcome:
        """Take discovered work only when its exact inference pipeline is available."""
        job = validate_job_descriptor(self.manifest, job)
        if model.shared_pipeline_signature() != job["pipeline_signature"]:
            return SharedAnalysisOutcome("incompatible", job)
        return self._analyze_or_reuse(model, relative_path, job, server_fresh, lease_seconds)

    def analyze_or_reuse(
        self, model, relative_path: str, photo_sha256: str, *,
        server_fresh: bool = False, lease_seconds: float = 60,
    ) -> SharedAnalysisOutcome:
        """Reuse a committed result or analyze only an attempt owned by this session."""
        job = self.describe(model, photo_sha256)
        return self._analyze_or_reuse(model, relative_path, job, server_fresh, lease_seconds)

    def _analyze_or_reuse(self, model, relative_path, job, server_fresh, lease_seconds):
        if server_fresh is not True:
            self.reset_observations()
        job_id = job["job_id"]
        with self._lock:
            if job_id in self._active:
                return SharedAnalysisOutcome("pending", job)
            self._active.add(job_id)
        try:
            if not self.allow_writes:
                return self._result(job, "reused") or SharedAnalysisOutcome("pending", job)
            status = self._job("claim", job)
            if not isinstance(status, dict):
                raise InvalidTransportData("Invalid shared analysis claim reply")
            if status.get("state") == "reserved" and server_fresh is True:
                status = self.reclaim(job, server_fresh=True, lease_seconds=lease_seconds)
                if not isinstance(status, dict):
                    raise InvalidTransportData("Invalid shared analysis reclaim reply")
            state = status.get("state")
            if state == "completed":
                return self._result(job, "reused") or SharedAnalysisOutcome("pending", job)
            if state in ("pending", "reserved", "exhausted"):
                return SharedAnalysisOutcome("pending" if state == "reserved" else state, job)
            attempt = status.get("attempt")
            claim = status.get("claim")
            if (
                state != "owned" or type(attempt) is not int or not 1 <= attempt <= MAX_ATTEMPTS
                or not isinstance(claim, dict) or claim.get("job_id") != job_id
                or claim.get("attempt") != attempt or claim.get("device_id") != self.device_id
                or claim.get("session_id") != self.session_id
            ):
                raise InvalidTransportData("Invalid shared analysis claim reply")
            return self._owned(model, relative_path, job, attempt)
        except _Unavailable:
            self.reset_observations()
            return SharedAnalysisOutcome("pending", job)
        except (UnknownPublishError, PublishConflictError) as exc:
            try:
                if outcome := self._result(job, "reused"):
                    return outcome
            except (_Unavailable, UnknownPublishError, PublishConflictError):
                pass
            state = "published_unknown" if isinstance(exc, UnknownPublishError) else "pending"
            return SharedAnalysisOutcome(state, job)
        finally:
            with self._lock:
                self._active.discard(job_id)
