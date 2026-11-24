"""Standalone Tk chooser for the Linux desktop."""

import json
import tkinter as tk
from tkinter import filedialog


def main() -> None:
    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(parent=root, mustexist=True, title="Bilderordner auswählen")
        print(json.dumps(path or None), flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
