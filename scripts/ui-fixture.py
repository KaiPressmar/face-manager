#!/usr/bin/env python3
"""Seed an isolated GPS photo library for the browser smoke test."""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: ui-fixture.py TEMP_DATA_DIRECTORY")
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["FACE_MANAGER_DATA_DIR"] = str(root)

    # Import only after the data directory is set; config resolves DB_PATH at import.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.db import schema

    schema.init_db()
    conn = schema.get_conn()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cities = [
        ("Berlin", 52.5200, 13.4050, 2024, "#378eb2"),
        ("Paris", 48.8566, 2.3522, 2025, "#aa77c0"),
        ("Tokio", 35.6762, 139.6503, 2026, "#d16e79"),
        ("New York", 40.7128, -74.0060, 2025, "#65a675"),
        ("Kapstadt", -33.9249, 18.4241, 2026, "#e29a51"),
        ("Sydney", -33.8688, 151.2093, 2024, "#6988d0"),
    ]
    conn.executemany("INSERT INTO person(id, name) VALUES (?, ?)", [(1, "Anna Beispiel"), (2, "Ben Beispiel")])
    conn.executemany(
        "INSERT INTO cluster(id, label, person_id) VALUES (?, ?, ?)",
        [(1, "Anna", 1), (2, "Ben", 2), (3, "Offene Gruppe", None)],
    )
    for index in range(85):
        city, latitude, longitude, year, color = cities[index % len(cities)]
        filename = f"{city.replace(' ', '-')}-{index:03d}.jpg"
        path = root / filename
        image = Image.new("RGB", (640, 480), color)
        painter = ImageDraw.Draw(image)
        painter.rectangle((25, 25, 615, 455), outline="#ffffff", width=4)
        painter.text((45, 45), f"{city} · {index + 1}", fill="#ffffff")
        image.save(path, format="JPEG", quality=78)
        captured_at = f"{year}-{(index % 12) + 1:02d}-{((index // 6) % 27) + 1:02d}T12:00:00"
        cursor = conn.execute(
            """INSERT INTO image(path, directory, filename, latitude, longitude,
               captured_at, metadata_scanned_at, processed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(path), str(root), filename, latitude, longitude, captured_at, now, now),
        )
        image_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO image_location(image_id, path, directory, filename,
               created_at, file_size, modified_at_ns) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (image_id, str(path), str(root), filename, now, path.stat().st_size, path.stat().st_mtime_ns),
        )
        if index < 12:
            conn.execute(
                """INSERT INTO face(image_id, bbox_x, bbox_y, bbox_w, bbox_h,
                   cluster_id, review_status) VALUES (?, 150, 120, 160, 190, ?, 'active')""",
                (image_id, (index + 1) % 3 + 1),
            )
    conn.executemany(
        "INSERT INTO app_settings(key, value) VALUES (?, ?)",
        [("last_seen_changelog_version", (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()),
         ("automatic_update_checks", "0")],
    )
    conn.commit()
    conn.close()
    print(f"Seeded 85 photos, 2 people, 3 clusters, 12 faces in {root}")


if __name__ == "__main__":
    main()
