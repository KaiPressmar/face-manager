#!/usr/bin/env python3
"""Seed an isolated GPS photo library for the browser smoke test."""

import os
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw


def seed_journeys(conn, root: Path, now: str) -> int:
    paris = ("Paris", 48.8566, 2.3522)
    lyon = ("Lyon", 45.7640, 4.8357)
    marseille = ("Marseille", 43.2965, 5.3698)
    nice = ("Nice", 43.7102, 7.2620)
    marrakech = ("Marrakech", 31.6295, -7.9811)
    geneva = ("Genève", 46.2044, 6.1432)
    berlin = ("Berlin", 52.5200, 13.4050)
    lisbon = ("Lisboa", 38.7223, -9.1393)
    tokyo = ("Tokio", 35.6762, 139.6503)
    east = ("Fidschi", -16.7, 179.35)
    west = ("Tonga", -19.6, -175.2)
    trips = (
        ("2024-07-13", (paris, lyon, marrakech, marseille, nice, geneva, paris)),
        ("2024-08-16", (berlin, paris, lyon, marseille, geneva, nice, paris)),
        ("2024-12-10", (paris, geneva, lyon, marseille, nice, lyon, paris)),
        ("2025-07-13", (paris, lisbon, marrakech, marseille, nice, geneva, paris)),
        ("2025-10-03", (berlin, paris, geneva, nice, marseille, lyon, berlin)),
        ("2026-03-10", (tokyo, east, west, east, tokyo, east, west)),
        ("2026-04-05", (paris, lisbon, paris, berlin, paris, geneva, paris)),
        ("2026-08-01", (nice, lyon, paris, marseille, lyon, geneva, paris)),
        ("2026-09-22", (berlin, ("Stephansplatz", 48.2085, 16.3720), ("Graben", 48.2087, 16.3687),
                        ("Hofburg", 48.2070, 16.3659), ("Heldenplatz", 48.2057, 16.3623),
                        ("Museumsquartier", 48.2037, 16.3593), berlin)),
    )
    count = 0
    for trip, (day, stops) in enumerate(trips, 1):
        start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        offsets = (480, 600, 628, 656, 684, 712, 1080) if day == "2026-09-22" else (480, 660, 900, 1260, 1920, 2220, 2520)
        for stop, ((city, latitude, longitude), minute) in enumerate(zip(stops, offsets), 1):
            for picture in range(5):
                captured = start + timedelta(minutes=minute + picture * 5)
                missing_location = day == "2026-09-22" and stop == 2 and picture == 2
                coordinates = (None, None) if missing_location else (latitude + picture * .0002, longitude)
                time_source = "exif_original_offset" if missing_location else "gps_utc"
                path = root / f"Reise-{trip:02d}-{stop:02d}-{picture + 1}-{city}.jpg"
                with Image.new("RGB", (320, 240), (42 + 21 * trip, 75 + 13 * stop, 115 + 18 * picture)) as image:
                    ImageDraw.Draw(image).text((18, 34), f"{city} · {captured.date()}", fill="#ffffff")
                    image.save(path, "JPEG")
                cursor = conn.execute(
                    """INSERT INTO image(path, directory, filename, latitude, longitude, captured_at, captured_at_utc,
                       capture_time_source, metadata_scanned_at, processed_at, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(path), str(root), path.name, *coordinates, captured.replace(tzinfo=None).isoformat(),
                     captured.isoformat(), time_source, now, now, sha256(path.read_bytes()).hexdigest()),
                )
                info = path.stat()
                conn.execute(
                    """INSERT INTO image_location(image_id, path, directory, filename, created_at, file_size, modified_at_ns)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (cursor.lastrowid, str(path), str(root), path.name, now, info.st_size, info.st_mtime_ns),
                )
                count += 1
    return count


def main() -> None:
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[2] != "--journey"):
        raise SystemExit("Usage: ui-fixture.py TEMP_DATA_DIRECTORY [--journey]")
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
               captured_at, metadata_scanned_at, processed_at, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(path), str(root), filename, latitude, longitude, captured_at, now, now,
             sha256(path.read_bytes()).hexdigest()),
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
    journey_count = seed_journeys(conn, root, now) if "--journey" in sys.argv[2:] else 0
    conn.commit()
    conn.close()
    print(f"Seeded {85 + journey_count} photos ({journey_count} journey photos), 2 people, 3 clusters, 12 faces in {root}")


if __name__ == "__main__":
    main()
