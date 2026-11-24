"""Manage disk use of the local JPEG preview caches without probing originals."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
import errno
import json
import logging
import os
import re
import stat
import tempfile
from threading import Lock, Timer
import time
from uuid import uuid4

from ..config import get_data_root
from .filesystem_paths import filesystem_path

logger = logging.getLogger("face_manager.preview_cache")
DEFAULT_PREVIEW_CACHE_LIMIT_MIB = 768
_MIN_CACHE_LIMIT_MIB = 128
_MAX_CACHE_LIMIT_MIB = 1048576
_MIB = 1024**2
MAX_PREVIEW_BYTES = DEFAULT_PREVIEW_CACHE_LIMIT_MIB * _MIB
MAX_PREVIEW_AGE_SECONDS = 90 * 24 * 60 * 60
_TOUCH_INTERVAL_SECONDS = 15 * 60
_SWEEP_INTERVAL_SECONDS = 60
_MANAGED_FILENAMES = {
    "images": re.compile(r"([1-9][0-9]*)-[0-9a-f]{20}\.jpg"),
    "faces": re.compile(r"([1-9][0-9]*)(?:-[0-9a-f]{24})?\.jpg"),
}
_SHARD = re.compile(r"[0-9]{4,}")
_POLICY_FILENAME = "preview-cache-policy.json"
_POLICY_LOCK_FILENAME = ".preview-cache-policy.lock"
_lock = Lock()
_Policy = tuple[int | None, str | None, bool]
_policies: dict[Path, _Policy] = {}
_policy_stamps: dict[Path, tuple[int, ...] | None] = {}
_scheduled: dict[Path, tuple[object, Timer]] = {}
_last_completed: dict[Path, float] = {}
_under_pressure: dict[Path, bool] = {}
_writes_during_sweep: set[Path] = set()
_running: set[Path] = set()


def validate_preview_cache_limit_mib(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not _MIN_CACHE_LIMIT_MIB <= value <= _MAX_CACHE_LIMIT_MIB:
        raise ValueError(
            f"Das Vorschau-Limit muss zwischen {_MIN_CACHE_LIMIT_MIB} und "
            f"{_MAX_CACHE_LIMIT_MIB} MiB liegen oder unbegrenzt sein."
        )
    return value


def _remember_policy_locked(root: Path, policy: _Policy, stamp: tuple[int, ...] | None) -> _Policy:
    if root in _policies and _policies[root] != policy:
        _under_pressure.pop(root, None)
        _last_completed.pop(root, None)
    _policies[root] = policy
    _policy_stamps[root] = stamp
    return policy


def _unreadable_policy_locked(root: Path, filename: Path, stamp: tuple[int, ...] | None) -> _Policy:
    policy = (None, None, True)
    if _policies.get(root) != policy:
        logger.warning("Could not read the preview cache policy from %s", filename, exc_info=True)
    return _remember_policy_locked(root, policy, stamp)


def _policy_locked(root: Path) -> _Policy:
    filename = root.parent / _POLICY_FILENAME
    try:
        info = filename.stat()
        stamp = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
    except FileNotFoundError:
        return _remember_policy_locked(root, (DEFAULT_PREVIEW_CACHE_LIMIT_MIB, None, False), None)
    except OSError:
        # An inaccessible preference cannot authorize eviction.
        return _unreadable_policy_locked(root, filename, None)

    if root in _policies and _policy_stamps.get(root) == stamp:
        return _policies[root]
    try:
        data = json.loads(filename.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            limit = validate_preview_cache_limit_mib(data["limit_mib"])
            revision = data["revision"]
            if not isinstance(revision, str) or not revision:
                raise ValueError("Missing preview cache policy revision")
        else:
            limit = validate_preview_cache_limit_mib(data)
            revision = None
    except (OSError, ValueError, KeyError):
        return _unreadable_policy_locked(root, filename, stamp)
    return _remember_policy_locked(root, (limit, revision, False), stamp)


def get_preview_cache_policy() -> tuple[int | None, bool]:
    root = get_data_root() / "thumbnails"
    with _lock:
        limit, _, unreadable = _policy_locked(root)
        return limit, unreadable


@contextmanager
def _device_policy_lock(root: Path, *, timeout: float):
    """Take the platform lock on a stable sidecar, with a bounded wait."""
    lock_file = root.parent / _POLICY_LOCK_FILENAME
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not _is_preview_file(os.fstat(fd)):
            raise OSError("Preview cache policy lock is unavailable or redirected")
        if os.name == "nt":
            import msvcrt

            def acquire():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
            yield
        finally:
            try:
                release()
            except OSError:
                logger.warning("Could not release the preview cache policy lock explicitly", exc_info=True)
    finally:
        os.close(fd)


def _persist_limit(root: Path, value: int | None, revision: str) -> None:
    filename = root.parent / _POLICY_FILENAME
    filename.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".preview-cache-policy-", dir=filename.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"limit_mib": value, "revision": revision}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, filename)
    finally:
        temporary.unlink(missing_ok=True)


def set_preview_cache_limit_mib(value: object) -> int | None:
    limit = validate_preview_cache_limit_mib(value)
    root = get_data_root() / "thumbnails"
    with _lock:
        root.parent.mkdir(parents=True, exist_ok=True)
        with _device_policy_lock(root, timeout=0.5):
            previous, revision, unreadable = _policy_locked(root)
            if limit == previous and not unreadable and revision is not None:
                return limit
            _persist_limit(root, limit, uuid4().hex)
            _policy_locked(root)
        if limit is None:
            pending = _scheduled.pop(root, None)
            if pending is not None:
                pending[1].cancel()
            _writes_during_sweep.discard(root)
        else:
            _schedule_locked(root, immediate=True)
    return limit


def _is_directory(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISDIR(info.st_mode) and not (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    except OSError:
        return False


def _is_preview_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _preview_directories(path: Path) -> tuple[Path, Path, Path]:
    if len(path.parents) < 3:
        raise OSError("Invalid preview cache path")
    shard, kind, root = path.parents[:3]
    if root.name != "thumbnails" or kind.name not in _MANAGED_FILENAMES or not _SHARD.fullmatch(shard.name):
        raise OSError("Invalid preview cache path")
    return root, kind, shard


@contextmanager
def preview_directory(path: Path, *, create: bool = False):
    """Validate the managed directory chain and anchor operations on POSIX."""
    root, kind, shard = _preview_directories(path)
    if create:
        root.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" or os.open not in os.supports_dir_fd:
        for directory in (root, kind, shard):
            if create:
                directory.mkdir(exist_ok=True)
            if not _is_directory(directory):
                raise OSError("Preview cache directory is unavailable or redirected")
        yield None
        return

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        if create:
            root.mkdir(exist_ok=True)
        fd = os.open(root, flags)
        stack.callback(os.close, fd)
        for name in (kind.name, shard.name):
            if create:
                try:
                    os.mkdir(name, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(name, flags, dir_fd=fd)
            stack.callback(os.close, fd)
        yield fd


def preview_exists(path: Path, *, touch: bool = True) -> bool:
    """Check a local generated preview and refresh its recency at coarse intervals."""
    try:
        with preview_directory(path) as fd:
            name = path if fd is None else path.name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not _is_preview_file(info):
                return False
            if touch and info.st_mtime < time.time() - _TOUCH_INTERVAL_SECONDS:
                try:
                    _touch_unchanged(path, fd, info)
                except (OSError, NotImplementedError, ValueError):
                    pass
    except OSError:
        return False
    return True


def read_preview_bytes(path: Path, limit: int) -> bytes:
    """Open a local JPEG without following redirected cache entries on POSIX."""
    with preview_directory(path) as directory_fd:
        name = path if directory_fd is None else path.name
        observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _is_preview_file(observed):
            raise OSError("Preview file is unavailable or redirected")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(name, flags, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as image:
            if not _matches_observed(os.fstat(image.fileno()), observed):
                raise OSError("Preview file changed while opening")
            return image.read(limit)


def _managed_previews(root: Path):
    if not _is_directory(root):
        return
    for kind, pattern in _MANAGED_FILENAMES.items():
        directory = root / kind
        if not _is_directory(directory):
            continue
        try:
            shards = list(directory.iterdir())
        except OSError:
            continue
        for shard in shards:
            if not _SHARD.fullmatch(shard.name) or not _is_directory(shard):
                continue
            try:
                with os.scandir(shard) as entries:
                    for entry in entries:
                        match = pattern.fullmatch(entry.name)
                        if not match or len(match[1]) > 19 or f"{int(match[1]) // 1000:04d}" != shard.name:
                            continue
                        try:
                            if os.name == "nt":
                                # DirEntry.stat does not populate st_nlink on Windows.
                                info = os.lstat(entry.path)
                            else:
                                info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if _is_preview_file(info):
                            yield Path(entry.path), info
            except OSError:
                continue


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size


def _matches_observed(current: os.stat_result, observed: os.stat_result) -> bool:
    return _is_preview_file(current) and _identity(current) == _identity(observed)


@contextmanager
def _windows_preview_handle(path: Path, observed: os.stat_result, access: int):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    open_file.restype = wintypes.HANDLE
    share_all = 0x01 | 0x02 | 0x04
    handle = open_file(filesystem_path(path), access | 0x80, share_all, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(wintypes.HANDLE(handle))
        raise
    try:
        if not _matches_observed(os.fstat(descriptor), observed):
            raise OSError("Preview file changed while opening")
        yield kernel, handle
    finally:
        os.close(descriptor)


def _touch_unchanged(path: Path, directory_fd: int | None, observed: os.stat_result) -> None:
    if directory_fd is not None:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            if _matches_observed(os.fstat(fd), observed):
                os.utime(fd, None)
        finally:
            os.close(fd)
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes

        with _windows_preview_handle(path, observed, 0x100) as (kernel, handle):
            ticks = time.time_ns() // 100 + 116444736000000000
            timestamp = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
            set_file_time = kernel.SetFileTime
            set_file_time.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 3
            set_file_time.restype = wintypes.BOOL
            set_file_time(handle, None, None, ctypes.byref(timestamp))


def _unlink_windows(path: Path, observed: os.stat_result) -> bool:
    import ctypes
    from ctypes import wintypes

    # Delete through the same handle used for the identity check, including
    # when a parent path has been redirected by the time deletion begins.
    with _windows_preview_handle(path, observed, 0x10000) as (kernel, handle):
        set_file_info = kernel.SetFileInformationByHandle
        set_file_info.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        set_file_info.restype = wintypes.BOOL
        delete_file = wintypes.BOOLEAN(True)
        return bool(set_file_info(handle, 4, ctypes.byref(delete_file), ctypes.sizeof(delete_file)))


def _unlink_unchanged(path: Path, observed: os.stat_result) -> bool:
    # Anchor the removal to the checked cache directories. A directory swap
    # between validation and unlink cannot redirect it to an original photo.
    with preview_directory(path) as fd:
        if fd is None:
            return _unlink_windows(path, observed) if os.name == "nt" else False
        current = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if not _matches_observed(current, observed):
            return False
        os.unlink(path.name, dir_fd=fd)
        return True


def delete_preview_file(path: Path) -> None:
    """Remove an individual generated preview after its database entry disappears."""
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return
    if _is_preview_file(observed):
        _unlink_unchanged(path, observed)


def prune_preview_cache(
    root: Path,
    *,
    max_bytes: int | None = None,
    now: float | None = None,
    _expected_policy: _Policy | None = None,
) -> tuple[int, int]:
    """Remove expired and least recently used previews across both cache trees."""
    with _lock:
        policy = _policy_locked(root)
    limit, _, _ = policy
    if limit is None or (_expected_policy is not None and policy != _expected_policy):
        return 0, 0
    if max_bytes is None:
        max_bytes = limit * _MIB
    cutoff = (time.time() if now is None else now) - MAX_PREVIEW_AGE_SECONDS
    entries = sorted(_managed_previews(root), key=lambda item: (item[1].st_mtime_ns, item[0]))
    total = sum(info.st_size for _, info in entries)
    removed = 0
    for path, info in entries:
        if total <= max_bytes and info.st_mtime >= cutoff:
            break
        try:
            with _lock:
                with _device_policy_lock(root, timeout=0):
                    if _policy_locked(root) != policy:
                        break
                    try:
                        if _unlink_unchanged(path, info):
                            total -= info.st_size
                            removed += 1
                    except FileNotFoundError:
                        total -= info.st_size
                    except OSError:
                        continue
        except (OSError, ImportError):
            break
    return removed, total


def _sweep(root: Path, token: object | None = None) -> None:
    with _lock:
        if token is not None:
            pending = _scheduled.get(root)
            if pending is None or pending[0] is not token:
                return
            _scheduled.pop(root)
        policy = _policy_locked(root)
        limit, _, _ = policy
        if limit is None or root in _running:
            return
        _running.add(root)
    needs_followup = False
    try:
        _, total = prune_preview_cache(root, _expected_policy=policy)
        needs_followup = total > limit * _MIB
        with _lock:
            if _policy_locked(root) == policy:
                _under_pressure[root] = total >= limit * _MIB * 0.9
    except Exception:
        logger.exception("Could not prune the local preview cache")
    finally:
        with _lock:
            _running.discard(root)
            _last_completed[root] = time.monotonic()
            changed = root in _writes_during_sweep
            _writes_during_sweep.discard(root)
            current = _policy_locked(root)
            if current != policy:
                _schedule_locked(root, immediate=True)
            elif changed or needs_followup:
                _schedule_locked(root)


def _schedule_locked(root: Path, *, immediate: bool = False) -> None:
    if _policy_locked(root)[0] is None:
        return
    if root in _running:
        _writes_during_sweep.add(root)
        return
    pending = _scheduled.get(root)
    if pending is not None:
        if not immediate:
            return
        pending[1].cancel()
    previous = _last_completed.get(root, float("-inf"))
    delay = 0.0 if immediate else max(0.0, previous + _SWEEP_INTERVAL_SECONDS - time.monotonic())
    token = object()
    timer = Timer(delay, _sweep, args=(root, token))
    timer.daemon = True
    _scheduled[root] = (token, timer)
    try:
        timer.start()
    except RuntimeError:
        _scheduled.pop(root, None)
        logger.exception("Could not start preview cache cleanup")


def schedule_preview_cache_cleanup(root: Path | None = None) -> None:
    """Coalesce cleanup after preview writes without blocking a request or import."""
    root = root if root is not None else get_data_root() / "thumbnails"
    with _lock:
        _schedule_locked(root)


def note_preview_written(destination: Path) -> None:
    """Schedule accounting for the cache containing an atomically written JPEG."""
    if len(destination.parents) < 3:
        return
    shard, kind, root = destination.parents[:3]
    if root.name == "thumbnails" and kind.name in _MANAGED_FILENAMES and _SHARD.fullmatch(shard.name):
        schedule_preview_cache_cleanup(root)


def cache_warmup_has_room() -> bool:
    """Leave space for visible and newly imported photos before warming old faces."""
    root = get_data_root() / "thumbnails"
    with _lock:
        limit, _, _ = _policy_locked(root)
        return limit is None or not _under_pressure.get(root, False)
