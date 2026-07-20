"""Discover, download, verify, and launch Face Manager updates."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse


logger = logging.getLogger("face_manager.updates")

GITHUB_REPOSITORY = "KaiPressmar/face-manager"
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
UPDATE_CHECK_INTERVAL_SECONDS = 60 * 60
SEMVER_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
SHA256_PATTERN = re.compile(r"\b([a-fA-F0-9]{64})\b")
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
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
    if not release_url.startswith(
        f"https://github.com/{GITHUB_REPOSITORY}/releases/"
    ):
        raise UpdateError("GitHub lieferte keine vertrauenswürdige Release-Adresse.")

    installer_name = _asset_name(version, variant)
    checksum_name = f"{installer_name}.sha256"
    assets = {
        str(asset.get("name")): asset
        for asset in payload.get("assets", [])
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
        _validate_asset_url(candidate["installer_url"], installer_name)
        _validate_asset_url(candidate["checksum_url"], checksum_name)

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


def _validate_asset_url(url: str, expected_name: str) -> None:
    parsed = urlparse(url)
    expected_path = (
        f"/{GITHUB_REPOSITORY}/releases/download/"
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or expected_path not in parsed.path
        or not parsed.path.endswith(f"/{expected_name}")
    ):
        raise UpdateError(f"Unsichere Download-Adresse für {expected_name}.")


def _validate_final_download_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname in ALLOWED_DOWNLOAD_HOSTS
        or hostname.endswith(".githubusercontent.com")
    ):
        raise UpdateError("Der Download wurde auf einen unbekannten Server umgeleitet.")


class UpdateManager:
    """Thread-safe hourly release cache and one-at-a-time download worker."""

    def __init__(self) -> None:
        self._check_lock = threading.Lock()
        self._last_checked = 0.0
        self._cached_result: dict[str, object] | None = None
        self._candidate: dict[str, str] | None = None
        self._download_lock = threading.RLock()
        self._download_state: dict[str, object] = {"status": "idle"}

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
        return urllib.request.urlopen(request, timeout=timeout)

    def check(
        self, current_version: str, variant: str, *, force: bool = False
    ) -> dict[str, object]:
        with self._check_lock:
            now = time.monotonic()
            if (
                not force
                and self._cached_result is not None
                and now - self._last_checked < UPDATE_CHECK_INTERVAL_SECONDS
            ):
                return dict(self._cached_result)
            try:
                with self._request(GITHUB_LATEST_RELEASE_URL) as response:
                    payload = json.loads(response.read().decode("utf-8"))
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

    def start_download(self, version: str, target_directory: Path) -> dict[str, object]:
        with self._check_lock:
            candidate = dict(self._candidate) if self._candidate else None
            update_available = bool(
                self._cached_result and self._cached_result.get("update_available")
            )
        if not update_available or candidate is None or candidate["version"] != version:
            raise UpdateError("Für diese Version ist noch kein Installer verfügbar.")

        with self._download_lock:
            if self._download_state.get("status") == "downloading":
                return dict(self._download_state)
            if (
                self._download_state.get("status") == "ready"
                and self._download_state.get("version") == version
                and Path(str(self._download_state.get("path"))).is_file()
            ):
                return self._public_download_state()
            self._download_state = {
                "status": "downloading",
                "version": version,
                "installer_name": candidate["installer_name"],
                "downloaded_bytes": 0,
                "total_bytes": None,
            }

        thread = threading.Thread(
            target=self._download,
            args=(candidate, target_directory),
            daemon=True,
            name="face-manager-update-download",
        )
        thread.start()
        return self._public_download_state()

    def _download(self, candidate: dict[str, str], target_directory: Path) -> None:
        target_directory.mkdir(parents=True, exist_ok=True)
        final_path = target_directory / candidate["installer_name"]
        partial_path = final_path.with_suffix(final_path.suffix + ".part")
        try:
            expected_checksum = self._fetch_checksum(candidate)
            digest = hashlib.sha256()
            with self._request(candidate["installer_url"], timeout=60.0) as response:
                _validate_final_download_url(response.geturl())
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header else None
                with self._download_lock:
                    self._download_state["total_bytes"] = total
                downloaded = 0
                with partial_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        with self._download_lock:
                            self._download_state["downloaded_bytes"] = downloaded
            actual_checksum = digest.hexdigest()
            if actual_checksum != expected_checksum:
                raise UpdateError("Die SHA-256-Prüfung des Installers ist fehlgeschlagen.")
            partial_path.replace(final_path)
            with self._download_lock:
                self._download_state = {
                    "status": "ready",
                    "version": candidate["version"],
                    "installer_name": candidate["installer_name"],
                    "downloaded_bytes": final_path.stat().st_size,
                    "total_bytes": final_path.stat().st_size,
                    "path": str(final_path),
                    "sha256": actual_checksum,
                }
        except Exception as exc:
            partial_path.unlink(missing_ok=True)
            logger.exception("Face Manager update download failed")
            with self._download_lock:
                self._download_state = {
                    "status": "error",
                    "version": candidate["version"],
                    "installer_name": candidate["installer_name"],
                    "error": str(exc),
                }

    def _fetch_checksum(self, candidate: dict[str, str]) -> str:
        with self._request(candidate["checksum_url"]) as response:
            _validate_final_download_url(response.geturl())
            checksum_text = response.read(4096).decode("ascii", errors="strict")
        match = SHA256_PATTERN.search(checksum_text)
        if not match or candidate["installer_name"] not in checksum_text:
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
        return sys.platform == "win32" and bool(getattr(sys, "frozen", False))

    def launch_downloaded_installer(self, version: str) -> None:
        if not self.can_install():
            raise UpdateError(
                "Die automatische Installation ist nur in der Windows-App verfügbar."
            )
        with self._download_lock:
            state = dict(self._download_state)
        if state.get("status") != "ready" or state.get("version") != version:
            raise UpdateError("Der verifizierte Installer ist noch nicht bereit.")
        installer_path = Path(str(state.get("path")))
        if not installer_path.is_file():
            raise UpdateError("Der heruntergeladene Installer wurde nicht gefunden.")
        digest = hashlib.sha256()
        with installer_path.open("rb") as installer_file:
            for chunk in iter(lambda: installer_file.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_checksum = digest.hexdigest()
        if actual_checksum != state.get("sha256"):
            raise UpdateError("Der Installer wurde nach dem Download verändert.")
        subprocess.Popen([str(installer_path), "/CLOSEAPPLICATIONS"])

    def open_release_page(self, version: str) -> None:
        release = self.get_cached_release(version)
        if not webbrowser.open(str(release["release_url"]), new=2):
            raise UpdateError("Die GitHub-Release-Seite konnte nicht geöffnet werden.")


update_manager = UpdateManager()

_shutdown_callback = None


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
