"""Discover, download, verify, and launch Face Manager updates."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..config import get_build_variant, get_project_root
from .events import event_hub
from .ubuntu_update import launch_ubuntu_installer, terminal_command


logger = logging.getLogger("face_manager.updates")

GITHUB_REPOSITORY = "KaiPressmar/face-manager"
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
UPDATE_CHECK_INTERVAL_SECONDS = 60 * 60
SEMVER_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$", re.ASCII)
SHA256_PATTERN = re.compile(r"([a-fA-F0-9]{64}) [ *](\S+)")
CACHE_PACKAGE_PATTERNS = (
    re.compile(r"FaceManager-Setup-(\d+\.\d+\.\d+)\.exe", re.ASCII),
    re.compile(r"FaceManager-Setup-GPU-(\d+\.\d+\.\d+)\.exe", re.ASCII),
    re.compile(r"FaceManager-Android-(\d+\.\d+\.\d+)\.apk", re.ASCII),
    re.compile(r"face-manager_(\d+\.\d+\.\d+)_amd64\.deb", re.ASCII),
)
PARTIAL_SUFFIX = re.compile(r"(?:\.[a-z0-9_]{8})?\.part$")
PARTIAL_MAX_AGE_SECONDS = 24 * 60 * 60
INSTALLER_GRACE_SECONDS = 60 * 60
ALLOWED_DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class UpdateError(RuntimeError):
    """Raised when update metadata or an update artifact is unsafe or invalid."""


def parse_semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise UpdateError(f"Ungültige Release-Version: {value!r}")
    return tuple(int(part) for part in match.groups())


def _release_sections(body: str) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            title = line[3:].strip()
            current = {"title": title, "items": []}
            sections.append(current)
        elif line.startswith("- ") and current is not None:
            current["items"].append(line[2:].strip())
    return [section for section in sections if section["items"]]


def _asset_name(version: str, variant: str) -> str:
    if variant == "android":
        return f"FaceManager-Android-{version}.apk"
    if variant == "linux":
        return f"face-manager_{version}_amd64.deb"
    suffix = "-GPU" if variant == "gpu" else ""
    return f"FaceManager-Setup{suffix}-{version}.exe"


def parse_latest_release(
    payload: dict[str, object], current_version: str, variant: str
) -> tuple[dict[str, object], dict[str, str] | None]:
    """Validate GitHub release metadata and select the exact installer pair."""
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("Vorab- oder Entwurfs-Releases werden nicht installiert.")
    tag = str(payload.get("tag_name") or "").strip()
    version_tuple = parse_semver(tag)
    version = ".".join(str(part) for part in version_tuple)
    current_tuple = parse_semver(current_version)
    release_url = str(payload.get("html_url") or "").strip()
    if release_url != f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/{tag}":
        raise UpdateError("GitHub lieferte keine vertrauenswürdige Release-Adresse.")

    installer_name = _asset_name(version, variant)
    checksum_name = f"{installer_name}.sha256"
    raw_assets = payload.get("assets", [])
    if not isinstance(raw_assets, list):
        raise UpdateError("GitHub lieferte eine ungültige Paketliste.")
    assets = {
        str(asset.get("name")): asset
        for asset in raw_assets
        if isinstance(asset, dict) and asset.get("name")
    }
    installer = assets.get(installer_name)
    checksum = assets.get(checksum_name)
    candidate = None
    if installer is not None and checksum is not None:
        candidate = {
            "version": version,
            "installer_name": installer_name,
            "installer_url": str(installer.get("browser_download_url") or ""),
            "installer_digest": str(installer.get("digest") or ""),
            "checksum_url": str(checksum.get("browser_download_url") or ""),
        }
        _validate_asset_url(candidate["installer_url"], tag, installer_name)
        _validate_asset_url(candidate["checksum_url"], tag, checksum_name)

    result: dict[str, object] = {
        "current_version": current_version,
        "latest_version": version,
        "update_available": version_tuple > current_tuple,
        "download_available": candidate is not None,
        "release_url": release_url,
        "published_at": payload.get("published_at"),
        "sections": _release_sections(str(payload.get("body") or "")),
        "build_variant": variant,
        "installer_name": installer_name if candidate is not None else None,
    }
    return result, candidate


def _validate_asset_url(url: str, tag: str, expected_name: str) -> None:
    expected = f"https://github.com/{GITHUB_REPOSITORY}/releases/download/{tag}/{expected_name}"
    if url != expected:
        raise UpdateError(f"Unsichere Download-Adresse für {expected_name}.")


def _validate_final_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in ALLOWED_DOWNLOAD_HOSTS:
        raise UpdateError("Der Download wurde auf einen unbekannten Server umgeleitet.")


class _UpdateRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_final_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _is_redirected(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _is_cache_file(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not _is_redirected(info)


def _check_cache_directory(directory: Path) -> None:
    info = directory.lstat()
    if _is_redirected(info) or not stat.S_ISDIR(info.st_mode):
        raise UpdateError("Der Update-Ordner ist kein reguläres Verzeichnis.")


def _same_cache_file(current: os.stat_result, observed: os.stat_result) -> bool:
    return _is_cache_file(current) and (
        current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns
    ) == (observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns)


def _inspect_cache_file(path: Path) -> tuple[str, os.stat_result]:
    _check_cache_directory(path.parent)
    observed = path.lstat()
    if not _is_cache_file(observed):
        raise UpdateError("Die Installationsdatei ist keine reguläre Datei.")
    with path.open("rb") as installer:
        if not _same_cache_file(os.fstat(installer.fileno()), observed):
            raise UpdateError("Die Installationsdatei wurde verändert.")
        digest = hashlib.sha256()
        for chunk in iter(lambda: installer.read(1024 * 1024), b""):
            digest.update(chunk)
    current = path.lstat()
    if not _same_cache_file(current, observed):
        raise UpdateError("Die Installationsdatei wurde verändert.")
    return digest.hexdigest(), current


class UpdateManager:
    """Thread-safe hourly release cache and one-at-a-time download worker."""

    def __init__(self, on_download_change: Callable[[dict[str, object]], None] | None = None) -> None:
        self._check_lock = threading.Lock()
        self._last_checked = 0.0
        self._cached_result: dict[str, object] | None = None
        self._candidate: dict[str, str] | None = None
        self._download_lock = threading.RLock()
        self._download_state: dict[str, object] = {"status": "idle"}
        self._desktop_install_started = False
        self._on_download_change = on_download_change
        self._last_download_notice = 0.0

    @staticmethod
    def _request(url: str, timeout: float = 15.0):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "FaceManager-UpdateChecker",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        return urllib.request.build_opener(_UpdateRedirectHandler()).open(request, timeout=timeout)

    def check(
        self, current_version: str, variant: str, *, force: bool = False
    ) -> dict[str, object]:
        with self._check_lock:
            now = time.monotonic()
            if (
                not force
                and self._cached_result is not None
                and self._cached_result.get("current_version") == current_version
                and self._cached_result.get("build_variant") == variant
                and now - self._last_checked < UPDATE_CHECK_INTERVAL_SECONDS
            ):
                return dict(self._cached_result)
            try:
                with self._request(GITHUB_LATEST_RELEASE_URL) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise UpdateError("GitHub lieferte ungültige Release-Informationen.")
                result, candidate = parse_latest_release(
                    payload, current_version, variant
                )
            except (OSError, ValueError, urllib.error.URLError) as exc:
                raise UpdateError(
                    "Die aktuelle Version konnte nicht von GitHub abgerufen werden."
                ) from exc
            self._cached_result = result
            self._candidate = candidate
            self._last_checked = now
            return dict(result)

    def get_cached_release(self, version: str) -> dict[str, object]:
        with self._check_lock:
            result = self._cached_result
            if result is None or result.get("latest_version") != version:
                raise UpdateError("Die Release-Information ist nicht mehr aktuell.")
            return dict(result)

    def cleanup_cache(self, target_directory: Path, current_version: str) -> None:
        """Retain each build's newest pending installer and recently used files."""
        try:
            installed_version = parse_semver(current_version)
            _check_cache_directory(target_directory)
            entries = list(target_directory.iterdir())
        except FileNotFoundError:
            return
        except (OSError, UpdateError):
            logger.warning("Could not inspect Face Manager update cache", exc_info=True)
            return

        now = time.time()
        packages = {}
        newest_pending = {}
        for path in entries:
            partial = PARTIAL_SUFFIX.search(path.name)
            name = path.name[:partial.start()] if partial else path.name
            for variant, pattern in enumerate(CACHE_PACKAGE_PATTERNS):
                match = pattern.fullmatch(name)
                if match:
                    break
            else:
                continue
            try:
                info = path.lstat()
                if not _is_cache_file(info):
                    continue
                version = parse_semver(match[1])
                if partial:
                    if now - info.st_mtime > PARTIAL_MAX_AGE_SECONDS:
                        self._remove_cache_file(path, info)
                    continue
                packages[path] = variant, version, info
                if version > installed_version:
                    previous = newest_pending.get(variant)
                    if previous is None or version > previous:
                        newest_pending[variant] = version
            except (OSError, UpdateError, ValueError):
                logger.debug("Could not inspect cached update %s", path.name, exc_info=True)

        for path, (variant, version, info) in packages.items():
            if version != newest_pending.get(variant) and now - info.st_mtime > INSTALLER_GRACE_SECONDS:
                self._remove_cache_file(path, info)

    @staticmethod
    def _remove_cache_file(path: Path, observed: os.stat_result) -> None:
        try:
            _check_cache_directory(path.parent)
            if _same_cache_file(path.lstat(), observed):
                path.unlink()
        except FileNotFoundError:
            pass
        except (OSError, UpdateError):
            logger.debug("Could not remove cached update %s", path.name, exc_info=True)

    def start_download(self, version: str, target_directory: Path) -> dict[str, object]:
        with self._check_lock:
            candidate = dict(self._candidate) if self._candidate else None
            release = self._cached_result or {}
        if not release.get("update_available") or candidate is None or candidate["version"] != version:
            raise UpdateError("Für diese Version ist noch kein Installer verfügbar.")

        with self._download_lock:
            if self._download_state.get("status") == "downloading":
                if self._download_state.get("version") != version:
                    raise UpdateError("Eine andere Update-Version wird bereits heruntergeladen.")
                return self._public_download_state()
            self._download_state = {
                "status": "downloading",
                "version": version,
                "installer_name": candidate["installer_name"],
                "downloaded_bytes": 0,
                "total_bytes": None,
            }
            self._notify_download_change()

            try:
                threading.Thread(
                    target=self._download,
                    args=(candidate, target_directory, str(release["current_version"])),
                    daemon=True,
                    name="face-manager-update-download",
                ).start()
            except Exception as exc:
                logger.exception("Could not start Face Manager update download")
                self._set_download_error(candidate, exc)
            return self._public_download_state()

    def _set_download_error(self, candidate: dict[str, str], error: Exception) -> None:
        with self._download_lock:
            self._download_state = {
                "status": "error",
                "version": candidate["version"],
                "installer_name": candidate["installer_name"],
                "error": str(error),
            }
            self._notify_download_change()

    def _notify_download_change(self, *, progress: bool = False) -> None:
        """Publish a compact activity snapshot while holding the download lock."""
        if self._on_download_change is None:
            return
        now = time.monotonic()
        if progress and now - self._last_download_notice < 0.5:
            return
        self._last_download_notice = now
        fields = ("status", "version", "downloaded_bytes", "total_bytes")
        snapshot = {key: self._download_state[key] for key in fields if key in self._download_state}
        try:
            self._on_download_change(snapshot)
        except Exception:
            logger.exception("Could not publish update download activity")

    def _download(
        self, candidate: dict[str, str], target_directory: Path, current_version: str
    ) -> None:
        final_path = target_directory / candidate["installer_name"]
        try:
            target_directory.mkdir(parents=True, exist_ok=True)
            _check_cache_directory(target_directory)
            expected_checksum = self._fetch_checksum(candidate)
            try:
                cached_checksum, info = _inspect_cache_file(final_path)
            except FileNotFoundError:
                pass
            else:
                if cached_checksum == expected_checksum:
                    os.utime(final_path, None, follow_symlinks=False)
                    self._set_download_ready(candidate, final_path, info.st_size, expected_checksum)
                    self.cleanup_cache(target_directory, current_version)
                    return

            size = self._download_file(candidate, final_path, expected_checksum)
            self._set_download_ready(candidate, final_path, size, expected_checksum)
            self.cleanup_cache(target_directory, current_version)
        except Exception as exc:
            logger.exception("Face Manager update download failed")
            self._set_download_error(candidate, exc)

    def _download_file(
        self, candidate: dict[str, str], final_path: Path, expected_checksum: str
    ) -> int:
        partial_path = None
        digest = hashlib.sha256()
        try:
            with self._request(candidate["installer_url"], timeout=60.0) as response:
                _validate_final_download_url(response.geturl())
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header else None
                with self._download_lock:
                    self._download_state["total_bytes"] = total
                    self._notify_download_change()
                downloaded = 0
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=f"{final_path.name}.", suffix=".part",
                    dir=final_path.parent, delete=False,
                ) as output:
                    partial_path = Path(output.name)
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        with self._download_lock:
                            self._download_state["downloaded_bytes"] = downloaded
                            self._notify_download_change(progress=True)
            if digest.hexdigest() != expected_checksum:
                raise UpdateError("Die SHA-256-Prüfung des Installers ist fehlgeschlagen.")
            _check_cache_directory(final_path.parent)
            if not _is_cache_file(partial_path.lstat()):
                raise UpdateError("Die temporäre Installationsdatei wurde verändert.")
            partial_path.replace(final_path)
            return downloaded
        finally:
            if partial_path is not None:
                try:
                    _check_cache_directory(partial_path.parent)
                    partial_path.unlink(missing_ok=True)
                except (OSError, UpdateError):
                    logger.warning("Could not remove partial update download", exc_info=True)

    def _set_download_ready(
        self, candidate: dict[str, str], path: Path, size: int, checksum: str
    ) -> None:
        with self._download_lock:
            self._download_state = {
                "status": "ready",
                "version": candidate["version"],
                "installer_name": candidate["installer_name"],
                "downloaded_bytes": size,
                "total_bytes": size,
                "path": str(path.absolute()),
                "sha256": checksum,
            }
            self._notify_download_change()

    def _fetch_checksum(self, candidate: dict[str, str]) -> str:
        with self._request(candidate["checksum_url"]) as response:
            _validate_final_download_url(response.geturl())
            checksum_text = response.read(4097).decode("ascii", errors="strict")
        match = SHA256_PATTERN.fullmatch(checksum_text.rstrip("\r\n"))
        if len(checksum_text) > 4096 or not match or match[2] != candidate["installer_name"]:
            raise UpdateError("Die veröffentlichte SHA-256-Prüfsumme ist ungültig.")
        checksum = match.group(1).lower()
        digest = candidate.get("installer_digest", "")
        if digest:
            if not digest.startswith("sha256:") or digest[7:].lower() != checksum:
                raise UpdateError("Die veröffentlichten Prüfsummen widersprechen sich.")
        return checksum

    def _public_download_state(self) -> dict[str, object]:
        with self._download_lock:
            return {
                key: value
                for key, value in self._download_state.items()
                if key != "path"
            }

    def download_state(self) -> dict[str, object]:
        return self._public_download_state()

    @staticmethod
    def can_install() -> bool:
        if os.environ.get("FACE_MANAGER_PLATFORM") == "android":
            return True
        if sys.platform == "win32":
            return bool(getattr(sys, "frozen", False))
        return _ubuntu_terminal() is not None

    def launch_downloaded_installer(self, version: str) -> None:
        if not self.can_install():
            raise UpdateError(
                "Öffne die Release-Seite, um das Paket für dein System zu installieren."
            )
        with self._check_lock, self._download_lock:
            if self._desktop_install_started:
                raise UpdateError("Die Installation wurde bereits gestartet.")
            candidate = self._candidate
            state = self._download_state
            if (
                not self._cached_result
                or not self._cached_result.get("update_available")
                or not candidate
                or candidate["version"] != version
                or state.get("status") != "ready"
                or state.get("version") != version
                or state.get("installer_name") != candidate["installer_name"]
            ):
                raise UpdateError("Der verifizierte Installer ist noch nicht bereit.")
            installer_path = Path(str(state["path"]))
            try:
                checksum, observed = _inspect_cache_file(installer_path)
                digest = candidate["installer_digest"]
                if checksum != state.get("sha256") or (
                    digest and digest.lower() != f"sha256:{checksum}"
                ):
                    raise UpdateError("Der Installer wurde nach dem Download verändert.")
                if not _same_cache_file(installer_path.lstat(), observed):
                    raise UpdateError("Der Installer wurde nach dem Download verändert.")
                # Other app instances respect this grace period while the
                # system installer opens and consumes the file.
                os.utime(installer_path, None, follow_symlinks=False)
            except FileNotFoundError as exc:
                error = UpdateError("Der heruntergeladene Installer wurde nicht gefunden.")
                self._set_download_error(candidate, error)
                raise error from exc
            except (OSError, UpdateError) as exc:
                self._set_download_error(candidate, exc)
                raise
            if os.environ.get("FACE_MANAGER_PLATFORM") == "android":
                from android_runtime import get_bridge
                if not get_bridge().installApk(str(installer_path)):
                    raise UpdateError("Der Android-Installer konnte nicht geöffnet werden.")
                return
            if sys.platform == "win32":
                subprocess.Popen([str(installer_path), "/CLOSEAPPLICATIONS"])
            else:
                terminal = _ubuntu_terminal()
                if terminal is None:
                    raise UpdateError("Für die Ubuntu-Installation ist kein unterstütztes Terminal verfügbar.")
                launch_ubuntu_installer(terminal, installer_path, checksum)
            self._desktop_install_started = True

    def open_release_page(self, version: str) -> None:
        release = self.get_cached_release(version)
        if os.environ.get("FACE_MANAGER_PLATFORM") == "android":
            from android_runtime import get_bridge
            opened = get_bridge().openExternalUrl(str(release["release_url"]))
        else:
            opened = webbrowser.open(str(release["release_url"]), new=2)
        if not opened:
            raise UpdateError("Die GitHub-Release-Seite konnte nicht geöffnet werden.")


update_manager = UpdateManager(lambda snapshot: event_hub.publish("update-download", snapshot))

_shutdown_callback = None


def _ubuntu_terminal() -> tuple[str, ...] | None:
    if (
        sys.platform == "linux"
        and _shutdown_callback is not None
        and get_project_root() == Path("/opt/face-manager")
        and get_build_variant() == "linux"
    ):
        return terminal_command()
    return None


def register_shutdown_callback(callback) -> None:
    """Register the desktop window's graceful close operation."""
    global _shutdown_callback
    _shutdown_callback = callback


def _close_desktop_or_exit() -> None:
    if _shutdown_callback is not None:
        try:
            _shutdown_callback()
            return
        except Exception:
            logger.exception("Graceful desktop shutdown before update failed")
    os._exit(0)


def schedule_process_exit(delay_seconds: float = 1.0) -> None:
    """Give the HTTP response time to reach the UI, then close the desktop."""
    timer = threading.Timer(delay_seconds, _close_desktop_or_exit)
    timer.daemon = True
    timer.start()
