"""Inspect a user-selected folder and match originals by verified file content."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import islice
from threading import Event

from ..db.schema import HASH_CHUNK_SIZE
from .filesystem_paths import filesystem_path, stored_path
from .photo_identity import file_identity, open_registered_photo

logger = logging.getLogger("face_manager.import_sources")
Identity = tuple[int, int, int, int, int]


class ScanInterrupted(Exception):
    """The application is shutting down."""


class SourceUnavailable(OSError):
    """The selected root could not be inventoried consistently."""


@dataclass(frozen=True)
class Location:
    id: int
    image_id: int
    path: str
    digest: str | None
    identity: Identity


@dataclass(frozen=True)
class ObservedFile:
    path: str
    identity: Identity
    digest: str


@dataclass
class Inventory:
    root_identity: tuple[int, int]
    directories: set[str] = field(default_factory=set)
    seen: set[str] = field(default_factory=set)
    files: dict[str, ObservedFile] = field(default_factory=dict)
    failed_paths: set[str] = field(default_factory=set)
    conflicts: int = 0


@dataclass
class Reconciliation:
    moves: list[tuple[Location, ObservedFile]] = field(default_factory=list)
    matched: set[str] = field(default_factory=set)
    unresolved: int = 0
    ambiguities: int = 0
    choices: list[dict] = field(default_factory=list)
    verified: int = 0


def path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def root_contains(root: str, path: str) -> bool:
    try:
        return os.path.commonpath((path_key(root), path_key(path))) == path_key(root)
    except ValueError:
        return False


def root_identity(root: str) -> tuple[int, int]:
    try:
        info = os.stat(filesystem_path(root))
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(root)
        return info.st_dev, info.st_ino
    except OSError as exc:
        raise SourceUnavailable(f"Der Bilderordner ist nicht erreichbar: {root}") from exc


def _raise_if_stopped(stop: Event) -> None:
    if stop.is_set():
        raise ScanInterrupted()


def _read_digest(path: str, expected: Identity, stop: Event) -> str:
    flags = getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    with open(path, "rb", opener=lambda name, mode: os.open(name, mode | flags)) as source:
        identity = file_identity(os.fstat(source.fileno()))
        if identity != expected:
            raise OSError("Die Datei wurde während der Prüfung geändert.")
        digest = hashlib.sha256()
        while chunk := source.read(HASH_CHUNK_SIZE):
            _raise_if_stopped(stop)
            digest.update(chunk)
        if file_identity(os.fstat(source.fileno())) != identity:
            raise OSError("Die Datei wurde während der Prüfung geändert.")
    return digest.hexdigest()


def _identity(path: str, info: os.stat_result) -> Identity:
    if os.name == "nt":
        # The handle is authoritative for Windows change timestamps.
        with open(path, "rb") as source:
            info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode):
        raise OSError("Der Speicherort enthält keine reguläre Fotodatei.")
    return file_identity(info)


def _has_verified_original(locations: dict[str, Location], stop: Event) -> bool:
    for location in islice(locations.values(), 8):
        _raise_if_stopped(stop)
        if not location.digest:
            continue
        try:
            stream, _ = open_registered_photo(location.path, location.digest, None)
        except OSError:
            continue
        stream.close()
        return True
    return False


def inspect_source(
    root: str,
    locations: dict[str, Location],
    cached: dict[str, ObservedFile],
    suffixes: frozenset[str],
    stop: Event,
    progress,
    *,
    expected_root: tuple[int, int] | None = None,
) -> Inventory:
    anchor = root_identity(root)
    if expected_root is not None and anchor != expected_root and not _has_verified_original(locations, stop):
        raise SourceUnavailable("Der Speicherort wurde geändert. Prüfe den Ordner oder wähle den neuen Speicherort.")
    inventory = Inventory(anchor)

    def on_error(error: OSError) -> None:
        path = stored_path(error.filename or root)
        if path_key(path) == path_key(root):
            raise SourceUnavailable(f"Der Bilderordner ist nicht erreichbar: {root}") from error
        inventory.failed_paths.add(path_key(path))
        logger.info("Source directory could not be read: %s", path)

    for directory, subdirectories, filenames in os.walk(filesystem_path(root), onerror=on_error, followlinks=False):
        _raise_if_stopped(stop)
        subdirectories[:] = [name for name in subdirectories if name.casefold() != ".fm"
                             and not name.casefold().startswith(".face-manager-rename-")]
        stored_directory = stored_path(directory)
        if path_key(stored_directory) != path_key(root):
            inventory.directories.add(os.path.relpath(stored_directory, root))
        for filename in filenames:
            if os.path.splitext(filename)[1].lower() not in suffixes:
                continue
            _raise_if_stopped(stop)
            path = os.path.join(stored_directory, filename)
            key = path_key(path)
            try:
                info = os.stat(filesystem_path(path), follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    inventory.failed_paths.add(key)
                    continue
                identity = _identity(filesystem_path(path), info)
                inventory.seen.add(key)
                known = locations.get(key)
                if len(inventory.seen) % 64 == 0:
                    progress(len(inventory.seen))
                if known and known.identity == identity:
                    continue
                previous = cached.get(key)
                digest = previous.digest if previous and previous.identity == identity else _read_digest(
                    filesystem_path(path), identity, stop,
                )
                if known and digest != known.digest:
                    inventory.conflicts += 1
                inventory.files[key] = ObservedFile(path, identity, digest)
            except OSError:
                inventory.failed_paths.add(key)
        progress(len(inventory.seen))

    if root_identity(root) != anchor:
        raise SourceUnavailable("Der Bilderordner wurde während der Prüfung getrennt oder ersetzt.")
    return inventory


def find_moves(
    source: str,
    destination: str,
    known_locations: list[Location],
    destination_locations: dict[str, Location],
    inventory: Inventory,
) -> Reconciliation:
    """Return verified moves, matched destinations, unresolved locations and ambiguities."""
    replacing_root = path_key(source) != path_key(destination)
    originals: dict[str, list[Location]] = defaultdict(list)
    candidates: dict[str, list[ObservedFile]] = defaultdict(list)
    missing = 0
    verified_existing = 0
    for location in known_locations:
        key = path_key(location.path)
        if not replacing_root:
            observed = inventory.files.get(key)
            if key in inventory.seen and (observed is None or observed.digest == location.digest):
                verified_existing += bool(location.digest)
                continue
            ancestor = key
            while ancestor not in inventory.failed_paths and os.path.dirname(ancestor) != ancestor:
                ancestor = os.path.dirname(ancestor)
            if ancestor in inventory.failed_paths:
                continue
        missing += 1
        if location.digest:
            originals[location.digest].append(location)
    for key, photo in inventory.files.items():
        if key not in destination_locations:
            candidates[photo.digest].append(photo)

    result = Reconciliation()
    verified_destinations = 0
    ambiguities = 0
    for digest, old_locations in originals.items():
        new_locations = candidates.get(digest, [])
        if replacing_root:
            # Equivalent relative paths disambiguate aliases only after the
            # bytes have matched. The digest also finds renamed subfolders.
            by_path = {path_key(photo.path): photo for photo in new_locations}
            unmatched = []
            for location in old_locations:
                if not root_contains(source, location.path):
                    unmatched.append(location)
                    continue
                relative = os.path.relpath(location.path, source)
                target = path_key(os.path.join(destination, relative))
                photo = by_path.pop(target, None)
                existing = destination_locations.get(target)
                if photo:
                    result.moves.append((location, photo))
                    result.matched.add(target)
                elif existing and existing.image_id == location.image_id and target in inventory.seen:
                    observed = inventory.files.get(target)
                    if observed is None or observed.digest == existing.digest:
                        verified_destinations += 1
                    else:
                        unmatched.append(location)
                else:
                    unmatched.append(location)
            old_locations = unmatched
            new_locations = list(by_path.values())
        if len(old_locations) == len(new_locations) == 1:
            result.moves.append((old_locations[0], new_locations[0]))
            result.matched.add(path_key(new_locations[0].path))
        elif old_locations and new_locations:
            ambiguities += len(new_locations)
            available = 20 - len(result.choices)
            if available > 0:
                paths = sorted(photo.path for photo in new_locations)[:20]
                result.choices.extend(
                    {"original_path": location.path, "candidate_paths": paths}
                    for location in old_locations[:available]
                )
    result.unresolved = missing - len(result.moves) - verified_destinations
    result.ambiguities = ambiguities
    result.verified = verified_existing + verified_destinations
    return result
