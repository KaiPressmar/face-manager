"""Hand a verified Ubuntu package to the desktop terminal and APT."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

_TERMINALS = (
    ("/usr/bin/ptyxis", "--new-window", "--"),
    ("/usr/bin/gnome-terminal", "--"),
)
_REQUIRED_TOOLS = ("/bin/sh", "/usr/bin/sudo", "/usr/bin/apt", "/usr/bin/sha256sum", "/usr/bin/sleep")

# Arguments are passed positionally, including arbitrary characters in the data path.
_INSTALL_SCRIPT = """
pid=$1
package=$2
expected=$3
ready=$4
fail() {
    printf '\n%s\n' "$1"
    printf 'Drücke die Eingabetaste, um dieses Fenster zu schließen. '
    read -r unused
    exit 1
}
: > "$ready" || exit 1
remaining=60
while kill -0 "$pid" 2>/dev/null; do
    [ "$remaining" -gt 0 ] || fail 'Face Manager wurde nicht beendet. Schließe die App und versuche das Update erneut.'
    /usr/bin/sleep 1
    remaining=$((remaining - 1))
done
if [ -L "$package" ] || ! actual=$(/usr/bin/sha256sum < "$package") ||
    [ "${actual%% *}" != "$expected" ]; then
    fail 'Das Update-Paket konnte nicht verifiziert werden.'
fi
printf '\nFace Manager wird aktualisiert. Bestätige die Installation hier im Terminal.\n\n'
if /usr/bin/sudo -- /usr/bin/apt install -- "$package"; then
    printf '\nDu kannst Face Manager jetzt wieder öffnen.\n'
else
    fail 'Das Update wurde abgebrochen oder konnte nicht installiert werden. Öffne Face Manager für einen neuen Versuch.'
fi
printf 'Drücke die Eingabetaste, um dieses Fenster zu schließen. '
read -r unused
"""


def terminal_command() -> tuple[str, ...] | None:
    if not all(os.access(tool, os.X_OK) for tool in _REQUIRED_TOOLS):
        return None
    return next((command for command in _TERMINALS if os.access(command[0], os.X_OK)), None)


def launch_ubuntu_installer(command: tuple[str, ...], package: Path, checksum: str) -> None:
    """Wait until the terminal accepts the handoff before allowing app shutdown."""
    environment = os.environ.copy()
    environment.pop("LD_LIBRARY_PATH", None)
    with tempfile.TemporaryDirectory(prefix="face-manager-install-") as directory:
        ready = Path(directory) / "ready"
        terminal = subprocess.Popen(
            [
                *command, "/bin/sh", "-c", _INSTALL_SCRIPT, "face-manager-update",
                str(os.getpid()), str(package.absolute()), checksum, str(ready),
            ],
            env=environment,
            start_new_session=True,
        )
        deadline = time.monotonic() + 5.0
        while not ready.is_file():
            if terminal.poll() not in (None, 0) or time.monotonic() >= deadline:
                raise OSError("Das Terminal für die Ubuntu-Installation konnte nicht geöffnet werden.")
            time.sleep(0.05)
