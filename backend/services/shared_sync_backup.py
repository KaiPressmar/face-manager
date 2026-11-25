"""Keep a standalone local library snapshot before the first shared change."""

from contextlib import closing
import errno
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile
import time


_CORE_TABLES = frozenset({"image", "face", "cluster", "person"})
_LIBRARY_SCOPE = re.compile(r"[0-9a-f]{64}")
_PRODUCER = re.compile(r"[0-9a-f]{32}")
_BACKUP_TIMEOUT_SECONDS = 30.0
_MOUNT_INFO = Path("/proc/self/mountinfo")
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
_REMOTE_FILESYSTEMS = frozenset({
    "afs", "ceph", "cifs", "davfs", "fuse.davfs", "fuse.glusterfs", "fuse.rclone", "fuse.sshfs",
    "glusterfs", "nfs", "nfs4", "smb3", "smbfs",
})


def _require_local_path(path: Path) -> None:
    if os.name == "nt":
        from ctypes import windll, wintypes

        # Reject both UNC shares and drive letters mapped to the network.
        drive_type = windll.kernel32.GetDriveTypeW
        drive_type.argtypes = (wintypes.LPCWSTR,)
        drive_type.restype = wintypes.UINT
        if path.drive.startswith("\\\\") or drive_type(path.anchor) == 4:
            raise ValueError("The initial share backup must stay on this device")
    elif os.fspath(path).startswith("//"):
        raise ValueError("The initial share backup must stay on this device")


def _require_local_linux_mount(path: Path) -> None:
    depth, remote = -1, False
    for line in _MOUNT_INFO.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        mount, _, filesystem = line.partition(" - ")
        fields = mount.split()
        if len(fields) < 5 or not filesystem:
            continue
        mount_path = Path(_MOUNT_ESCAPE.sub(lambda part: chr(int(part[1], 8)), fields[4]))
        mount_depth = len(mount_path.parts)
        if mount_depth >= depth and path.is_relative_to(mount_path):
            depth, remote = mount_depth, filesystem.split()[0] in _REMOTE_FILESYSTEMS
    if remote:
        raise ValueError("The initial share backup must stay on this device")


def _backup_directory(connection: sqlite3.Connection, requested: Path) -> Path:
    main_path = next((row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not main_path:
        raise ValueError("A local file-backed library is required for the initial share backup")
    database = Path(main_path)
    _require_local_path(database)
    root = database.parent.resolve(strict=True)
    _require_local_path(root)
    if sys.platform == "linux":
        _require_local_linux_mount(root)
    candidate = Path(requested).absolute()
    _require_local_path(candidate)
    target = candidate.resolve()
    if not target.is_relative_to(root):
        raise ValueError("The initial share backup must stay inside the library data directory")
    parent = target
    while not parent.exists():
        parent = parent.parent
    if parent.stat().st_dev != root.stat().st_dev:
        raise ValueError("The initial share backup must stay on the library filesystem")
    return target


def _validate_snapshot(path: Path, deadline: float) -> None:
    information = path.lstat()
    if not stat.S_ISREG(information.st_mode) or getattr(information, "st_file_attributes", 0) & 0x400:
        raise ValueError("The initial share backup is redirected or is not a regular file")
    with path.open("rb") as stream:
        # Immutable SQLite connections ignore WAL. Verify the file itself uses
        # the rollback-journal header, so an unrelated WAL is never required.
        if stream.read(20)[18:20] != b"\x01\x01":
            raise sqlite3.DatabaseError("The initial share backup must be usable without sidecars")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0.2)) as backup:
        backup.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1_000)
        tables = {row[0] for row in backup.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('image', 'face', 'cluster', 'person')"
        )}
        if not _CORE_TABLES.issubset(tables) or backup.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("The initial share backup is not a valid library")
    if time.monotonic() >= deadline:
        raise TimeoutError("The initial share backup took too long")


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def ensure_initial_share_backup(
    connection: sqlite3.Connection, backup_directory: Path, *, library_scope: str, producer_id: str,
) -> Path | None:
    """Publish one validated SQLite backup without replacing an earlier snapshot.

    Use an idle connection on a worker outside of app locks. The source stays
    open for its owner, and an empty library needs no backup.
    """
    if not isinstance(library_scope, str) or _LIBRARY_SCOPE.fullmatch(library_scope) is None:
        raise ValueError("Invalid library scope for the initial share backup")
    if not isinstance(producer_id, str) or _PRODUCER.fullmatch(producer_id) is None:
        raise ValueError("Invalid producer ID for the initial share backup")
    if connection.in_transaction:
        raise ValueError("The initial share backup requires an idle connection")
    directory = _backup_directory(connection, backup_directory)
    final = directory / f"database-pre-share-{library_scope}-{producer_id}.sqlite"
    deadline = time.monotonic() + _BACKUP_TIMEOUT_SECONDS
    try:
        _validate_snapshot(final, deadline)
    except FileNotFoundError:
        pass
    else:
        _sync_directory(directory)
        return final

    if not any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in sorted(_CORE_TABLES)):
        return None

    missing = []
    parent = directory
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise ValueError("The initial share backup directory was redirected")
    for created in reversed(missing):
        _sync_directory(created.parent)
    fd, name = tempfile.mkstemp(prefix=".pre-share-", suffix=".partial", dir=directory)
    os.close(fd)
    partial = Path(name)

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("The initial share backup took too long")

    try:
        with closing(sqlite3.connect(partial, timeout=0.2)) as copy:
            connection.backup(copy, pages=256, progress=check_deadline, sleep=0.025)
            copy.execute("PRAGMA journal_mode = DELETE")
        _validate_snapshot(partial, deadline)
        with partial.open("rb+") as snapshot:
            os.fsync(snapshot.fileno())
        try:
            if os.name == "nt":
                partial.rename(final)  # Windows rename fails if final already exists.
            else:
                os.link(partial, final, follow_symlinks=False)
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise
            _validate_snapshot(final, deadline)
        _sync_directory(directory)
        return final
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            partial.with_name(partial.name + suffix).unlink(missing_ok=True)
