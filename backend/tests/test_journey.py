import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image, TiffImagePlugin

from backend.db import schema
from backend.services.geo import GeoBackfill, extract_image_metadata, list_map_images, list_map_points
from backend.services.journey import Observation, build_journey, get_journey, list_journey


class JourneyTest(unittest.TestCase):
    def photo(self, image_id, longitude, hours, *, wall_hours=None, utc=True):
        start = datetime(2024, 1, 1, 8)
        wall = start + timedelta(hours=hours if wall_hours is None else wall_hours)
        instant = (start + timedelta(hours=hours)).replace(tzinfo=timezone.utc) if utc else None
        return Observation(image_id, 0, longitude, wall, instant)

    def test_stop_radius_uses_a_fixed_anchor_and_repeated_visits_are_distinct(self):
        result = build_journey([
            self.photo(1, 0, 0), self.photo(2, 0.007, 2),
            self.photo(3, 0.014, 3), self.photo(4, 0, 4),
        ])
        self.assertEqual([stop["image_ids"] for stop in result["stops"]], [[1, 2], [3], [4]])
        self.assertEqual(len(result["connections"]), 2)
        self.assertEqual(result["connections"][0]["from"]["longitude"], 0)

    def test_dateline_neighbours_group_and_gaps_break_only_after_twenty_four_hours(self):
        result = build_journey([
            self.photo(1, 179.998, 0), self.photo(2, -179.998, 2),
            self.photo(3, -179.5, 26), self.photo(4, -179.6, 50.001),
        ])
        self.assertEqual([stop["image_ids"] for stop in result["stops"]], [[1, 2], [3], [4]])
        self.assertEqual([stop["section"] for stop in result["stops"]], [1, 1, 2])
        self.assertEqual(len(result["connections"]), 1)

    def test_utc_precedes_wall_order_and_uncertain_observations_do_not_connect(self):
        result = build_journey([
            self.photo(1, 1, 1, wall_hours=2), self.photo(2, 2, 2, wall_hours=1),
            self.photo(3, 3, 3, utc=False), self.photo(4, 4, 4),
            self.photo(5, 5, 4), self.photo(6, 6, 5), self.photo(7, 7, 6),
        ])
        self.assertEqual([stop["image_ids"] for stop in result["stops"]], [[i] for i in range(1, 8)])
        self.assertEqual([stop["number"] for stop in result["stops"] if stop["uncertain"]], [3, 4, 5])
        self.assertEqual(result["uncertain_count"], 3)
        self.assertEqual([edge["from"]["longitude"] for edge in result["connections"]], [1, 6])

    def test_camera_date_filters_utc_order_and_keeps_exact_stop_ids_for_large_groups(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(schema, "DB_PATH", str(Path(folder) / "journey.sqlite")):
            schema.init_db(scan_sources=False)
            with closing(schema.get_conn()) as connection:
                connection.executemany(
                    """INSERT INTO image (id, path, directory, filename, latitude, longitude,
                                          captured_at, captured_at_utc)
                       VALUES (?, ?, '', '', ?, ?, ?, ?)""",
                    [
                        (i, str(i), 35, 140, "2024-01-02T01:00:00", "2024-01-01T16:00:00+00:00")
                        for i in range(1, 521)
                    ] + [
                        (521, "missing-location", 35, None, "2024-01-02T05:00:00", None),
                        (522, "missing-time", 35, 140, None, None),
                        (523, "outside-range", 35, 140, "2024-01-01T18:00:00", None),
                    ],
                )
                connection.commit()
            day = date(2024, 1, 2)
            result = list_journey(day, day)
            self.assertEqual(result["photo_count"], 520)
            self.assertEqual(result["missing_location_count"], 1)
            self.assertEqual(result["missing_time_count"], 1)
            self.assertEqual(result["stops"][0]["image_ids"], list(range(1, 521)))
            with self.assertRaises(HTTPException) as raised:
                get_journey(day, date(2024, 1, 1))
            self.assertEqual(raised.exception.status_code, 422)


class CaptureClockTest(unittest.TestCase):
    def _save_photo(self, path, *, offset=None, gps_hour=None, camera_time=True, gps_date=True):
        rational = TiffImagePlugin.IFDRational
        gps = {1: "N", 2: tuple(map(rational, (52, 30, 0))),
               3: "E", 4: tuple(map(rational, (13, 24, 0)))}
        if gps_hour is not None:
            gps[7] = tuple(map(rational, (gps_hour, 45, 0)))
            if gps_date:
                gps[29] = "2024:01:01"
        exif = Image.Exif()
        exif[34853] = gps
        if camera_time:
            exif[34665] = {36867: "2024:01:02 00:45:00", 36882: "-05:00"}
            if offset:
                exif[34665][36881] = offset
        Image.new("RGB", (8, 8)).save(path, exif=exif)

    def test_corresponding_exif_offset_and_gps_only_with_date(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "photo.jpg"
            self._save_photo(photo, offset="+02:00")
            metadata = extract_image_metadata(photo, include_timing=True)
            self.assertEqual(metadata[2:], (
                "2024-01-02T00:45:00", "2024-01-01T22:45:00.000000+00:00", "exif_offset",
            ))
            self._save_photo(photo, gps_hour=22, gps_date=False)
            self.assertEqual(extract_image_metadata(photo, include_timing=True)[2:],
                             ("2024-01-02T00:45:00", None, "local"))
            self._save_photo(photo, gps_hour=22, camera_time=False)
            self.assertEqual(extract_image_metadata(photo, include_timing=True)[2:],
                             ("2024-01-01T22:45:00", "2024-01-01T22:45:00.000000+00:00", "gps_utc"))

    def test_conflicting_offset_and_gps_clock_have_no_reliable_instant(self):
        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / "photo.jpg"
            self._save_photo(photo, offset="+02:00", gps_hour=21)
            self.assertEqual(extract_image_metadata(photo, include_timing=True)[2:],
                             ("2024-01-02T00:45:00", None, "conflict"))
            self._save_photo(photo, gps_hour=1)
            self.assertEqual(extract_image_metadata(photo, include_timing=True)[2:],
                             ("2024-01-02T00:45:00", None, "conflict"))

    def test_upgrade_from_indexed_v0150_data_preserves_map_and_backfills_clocks(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(schema, "DB_PATH", str(Path(folder) / "legacy.sqlite")):
            offline = Path(folder) / "offline.jpg"
            available = Path(folder) / "available.jpg"
            self._save_photo(available, offset="+02:00")
            self._save_photo(offline, offset="+02:00")
            photo_hashes = {photo: hashlib.sha256(photo.read_bytes()).hexdigest() for photo in (offline, available)}
            # Distinct content identities make both photos canonical rows in
            # the earlier library even when their camera metadata matches.
            with offline.open("ab") as output:
                output.write(b"fixture: offline source")
            photo_hashes[offline] = hashlib.sha256(offline.read_bytes()).hexdigest()
            disconnected = Path(folder) / "offline.ejected"
            offline.rename(disconnected)
            with closing(sqlite3.connect(schema.DB_PATH)) as connection:
                # Match the v0.15.0 image and location schema, including its
                # already-indexed map columns and no timeline columns.
                connection.executescript("""
                    CREATE TABLE image (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL UNIQUE, directory TEXT NOT NULL, filename TEXT NOT NULL,
                        content_hash TEXT, processed_at TEXT,
                        raw_width INTEGER, raw_height INTEGER, exif_orientation INTEGER,
                        latitude REAL, longitude REAL, captured_at TEXT, metadata_scanned_at TEXT
                    );
                    CREATE TABLE image_location (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        image_id INTEGER NOT NULL,
                        path TEXT NOT NULL UNIQUE, directory TEXT NOT NULL, filename TEXT NOT NULL,
                        created_at TEXT, file_size INTEGER, modified_at_ns INTEGER,
                        file_device INTEGER, file_inode INTEGER, file_changed_at_ns INTEGER,
                        FOREIGN KEY(image_id) REFERENCES image(id) ON DELETE CASCADE
                    );
                    CREATE INDEX idx_image_geo_date ON image(latitude, longitude, captured_at)
                    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;
                    CREATE INDEX idx_image_metadata_pending ON image(id) WHERE metadata_scanned_at IS NULL;
                """)
                connection.executemany(
                    """INSERT INTO image (id, path, directory, filename, content_hash,
                                          latitude, longitude, captured_at, metadata_scanned_at)
                       VALUES (?, ?, ?, ?, ?, 52.5, 13.4, '2024-01-02T00:45:00', '2024-01-02T01:00:00')""",
                    [(i, str(photo), folder, photo.name, photo_hashes[photo]) for i, photo in enumerate((offline, available), 1)],
                )
                connection.execute("INSERT INTO image_location(image_id,path,directory,filename) SELECT id,path,directory,filename FROM image")
                connection.commit()

            schema.init_db(recover=False, scan_sources=False)
            day = date(2024, 1, 2)
            bounds = (10, 15, 50, 55, day, day)
            self.assertEqual(list_map_points(*bounds)["total"], 2)
            self.assertEqual([item["id"] for item in list_map_images(*bounds)["items"]], [2, 1])
            self.assertEqual(list_journey(day, day)["uncertain_count"], 2)

            worker = GeoBackfill()
            worker._run()
            with closing(schema.get_conn()) as connection:
                rows = connection.execute(
                    """SELECT latitude, longitude, captured_at, captured_at_utc, capture_time_source, metadata_scanned_at
                       FROM image ORDER BY id"""
                ).fetchall()
                self.assertEqual(tuple(rows[0]), (52.5, 13.4, "2024-01-02T00:45:00", None, "unavailable", "2024-01-02T01:00:00"))
                self.assertEqual(tuple(rows[1][3:]), ("2024-01-01T22:45:00.000000+00:00", "exif_offset", "2024-01-02T01:00:00"))
            self.assertEqual(list_journey(day, day)["uncertain_count"], 1)
            disconnected.rename(offline)
            for _ in range(2):
                schema.init_db(recover=False, scan_sources=False)
                worker._run()
            with closing(schema.get_conn()) as connection:
                rows = connection.execute("SELECT captured_at_utc, capture_time_source FROM image ORDER BY id").fetchall()
                self.assertEqual([tuple(row) for row in rows], [("2024-01-01T22:45:00.000000+00:00", "exif_offset")] * 2)
            result = list_journey(day, day)
            self.assertEqual(result["uncertain_count"], 0)
            self.assertEqual(result["stops"][0]["image_ids"], [1, 2])

    def test_legacy_clock_verifies_file_identity_and_tries_all_registered_sources(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(schema, "DB_PATH", str(Path(folder) / "identity.sqlite")):
            schema.init_db(scan_sources=False)
            root = Path(folder)
            replaced, alternate, known, unknown = (root / f"{name}.jpg" for name in ("replaced", "alternate", "known", "unknown"))
            for path, offset in ((replaced, "-05:00"), (alternate, "+02:00"), (known, "+03:00"), (unknown, "+04:00")):
                self._save_photo(path, offset=offset)
            alternate_hash = hashlib.sha256(alternate.read_bytes()).hexdigest()
            known_stat = known.stat()
            with closing(schema.get_conn()) as connection:
                connection.executemany(
                    """INSERT INTO image (id, path, directory, filename, content_hash,
                                          latitude, longitude, captured_at, metadata_scanned_at)
                       VALUES (?, ?, ?, ?, ?, 52.5, 13.4, '2024-01-02T00:45:00', 'done')""",
                    [(i, str(path), folder, path.name, digest) for i, (path, digest) in enumerate(
                        ((replaced, alternate_hash), (known, None), (unknown, None)), 1)],
                )
                connection.executemany(
                    """INSERT INTO image_location(image_id,path,directory,filename)
                       VALUES (?,?,?,?)""",
                    [(i, str(path), folder, path.name) for i, path in
                     ((1, replaced), (1, alternate), (2, known), (3, unknown))],
                )
                connection.execute(
                    """UPDATE image_location SET file_device = ?, file_inode = ?, file_size = ?,
                           modified_at_ns = ?, file_changed_at_ns = ? WHERE image_id = 2""",
                    (known_stat.st_dev, known_stat.st_ino, known_stat.st_size, known_stat.st_mtime_ns, known_stat.st_ctime_ns),
                )
                connection.commit()
            GeoBackfill()._run()
            with closing(schema.get_conn()) as connection:
                rows = connection.execute("SELECT captured_at_utc, capture_time_source FROM image ORDER BY id").fetchall()
            self.assertEqual([tuple(row) for row in rows], [
                ("2024-01-01T22:45:00.000000+00:00", "exif_offset"),
                ("2024-01-01T21:45:00.000000+00:00", "exif_offset"),
                (None, "local"),
            ])


if __name__ == "__main__":
    unittest.main()
