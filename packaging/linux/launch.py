"""Start the Ubuntu package with its private dependency directory."""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_ID = "face-manager"
sys.path[:0] = [str(ROOT / "vendor"), str(ROOT)]
# InsightFace imports this package transitively; its version check is unrelated
# to Face Manager's update policy and should not delay an offline startup.
os.environ["NO_ALBUMENTATIONS_UPDATE"] = "1"


def configure_desktop_identity() -> None:
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib

    # GTK uses the GLib program name as the Wayland app ID. Set it before
    # loading GTK so the shell can match the window to face-manager.desktop.
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Face Manager")

    from gi.repository import Gdk, Gtk

    Gdk.set_program_class(APP_ID)  # X11 WM_CLASS
    Gtk.Window.set_default_icon_name(APP_ID)


def check_runtime() -> None:
    """Check local GTK, native extensions and one CPU inference without a model download."""
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2

    from backend import desktop_main
    from backend.desktop_runtime import check_inference_runtime

    assert desktop_main.APP_VERSION == (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert Gtk.get_major_version() == 3 and WebKit2.get_major_version() == 2
    ort_version = check_inference_runtime(ROOT / "vendor")
    print(f"GTK 3, WebKitGTK 4.1, ONNX Runtime {ort_version} and CPU inference passed.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--version"]:
        print((ROOT / "VERSION").read_text(encoding="utf-8").strip())
    elif sys.argv[1:] == ["--check-runtime"]:
        check_runtime()
    else:
        configure_desktop_identity()
        from backend.desktop_main import main

        main()
