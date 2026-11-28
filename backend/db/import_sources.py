"""Persistent roots and scan checkpoints for managed photo folders."""

import os
from uuid import uuid4


def create_import_source_tables(cur) -> None:
    existed = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'import_source'"
    ).fetchone()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS import_source (
            id TEXT PRIMARY KEY,
            folder_path TEXT NOT NULL UNIQUE,
            auto_scan INTEGER NOT NULL DEFAULT 1,
            auto_import INTEGER NOT NULL DEFAULT 0,
            auto_import_blocked INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            mode TEXT NOT NULL DEFAULT 'scan',
            revision INTEGER NOT NULL DEFAULT 0,
            due_at REAL NOT NULL DEFAULT 0,
            pending_path TEXT,
            resolution TEXT,
            previous_roots TEXT NOT NULL DEFAULT '[]',
            last_scan_at TEXT,
            last_success_at TEXT,
            error TEXT,
            summary TEXT NOT NULL DEFAULT '{}',
            directories TEXT NOT NULL DEFAULT '[]',
            device TEXT,
            inode TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS import_source_file (
            source_id TEXT NOT NULL REFERENCES import_source(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            identity TEXT NOT NULL,
            digest TEXT NOT NULL,
            PRIMARY KEY (source_id, path)
        ) WITHOUT ROWID
        """
    )
    columns = {row[1] for row in cur.execute("PRAGMA table_info(import_source)")}
    for name, definition in {
        "auto_import_blocked": "INTEGER NOT NULL DEFAULT 0",
        "resolution": "TEXT",
        "previous_roots": "TEXT NOT NULL DEFAULT '[]'",
    }.items():
        if name not in columns:
            cur.execute(f"ALTER TABLE import_source ADD COLUMN {name} {definition}")
    if not existed:
        # Paths explicitly imported in older installations are known roots.
        # Let the owner opt into scanning these when they first manage them.
        roots = cur.execute(
            "SELECT DISTINCT folder_path FROM import_job WHERE status IN ('completed', 'running', 'paused', 'queued')"
        ).fetchall()
        cur.executemany(
            "INSERT OR IGNORE INTO import_source(id, folder_path, auto_scan) VALUES (?, ?, 0)",
            ((uuid4().hex, row[0]) for row in roots),
        )
    if os.name == "nt":
        cur.execute("CREATE INDEX IF NOT EXISTS idx_image_location_windows_path ON image_location(path COLLATE NOCASE)")
