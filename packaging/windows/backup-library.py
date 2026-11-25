"""Save a consistent copy of the library before installing an app update."""

import os
import re
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path


def backup_library(database: Path, installed_version: str = "") -> None:
    database = database.expanduser().resolve()
    if not database.is_file():
        return

    backup_directory = database.parent / "update-backups"
    backup_directory.mkdir(exist_ok=True)
    deadline = time.monotonic() + 120

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("The library backup took too long.")

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    version = installed_version if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", installed_version) else "unknown"
    descriptor, name = tempfile.mkstemp(prefix=f"database-{version}-{stamp}-", suffix=".partial", dir=backup_directory)
    os.close(descriptor)
    partial = Path(name)
    try:
        source_uri = database.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            with closing(sqlite3.connect(partial)) as backup:
                source.backup(backup, pages=256, progress=check_deadline)
                backup.execute("PRAGMA journal_mode = DELETE")
                if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise sqlite3.DatabaseError("The library backup failed its integrity check.")
        partial.rename(partial.with_suffix(".sqlite"))
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        backup_library(Path(sys.argv[1]), sys.argv[2])
    except Exception as error:
        if sys.stderr is not None:
            print(f"Library backup failed: {error}", file=sys.stderr)
        sys.exit(1)
