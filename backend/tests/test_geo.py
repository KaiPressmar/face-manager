import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from PIL import Image, TiffImagePlugin
from fastapi import HTTPException

from backend.db import schema
from backend.services import geo


class GeoMetadataTest(unittest.TestCase):
    def test_real_jpeg_round_trip_reads_nested_ifds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gps.jpg"
            rational = TiffImagePlugin.IFDRational
            exif = Image.Exif()
            exif[34853] = {1: "N", 2: tuple(map(rational, (52, 31, 12))),
                           3: "E", 4: tuple(map(rational, (13, 24, 18)))}
            exif[34665] = {36867: "2025:08:09 23:59:59"}
            Image.new("RGB", (8, 8)).save(path, exif=exif)
            latitude, longitude, captured = geo.extract_image_metadata(path)
            self.assertAlmostEqual(latitude, 52.52)
            self.assertAlmostEqual(longitude, 13.405)
            self.assertEqual(captured, "2025-08-09T23:59:59")

    def test_broken_exif_decoder_does_not_fail_image_import(self):
        with patch.object(geo.Image, "open", side_effect=SyntaxError("broken metadata")):
            self.assertEqual(geo.extract_image_metadata("corrupt.jpg"), (None, None, None))

    def test_missing_and_unreadable_files_have_no_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.jpg"
            self.assertEqual(geo.extract_image_metadata(path), (None, None, None))
            path.write_bytes(b"not a JPEG")
            self.assertEqual(geo.extract_image_metadata(path), (None, None, None))

    def test_large_library_map_payload_is_bounded_without_losing_photos(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(schema, "DB_PATH", str(Path(folder) / "large.sqlite")):
                schema.init_db()
                conn = schema.get_conn()
                conn.executemany(
                    "INSERT INTO image(path,directory,filename,latitude,longitude,metadata_scanned_at) "
                    "VALUES (?, '', ?, ?, ?, 'done')",
                    [(str(i), str(i), -89 + (i % 100) * 1.78,
                      -179 + (i // 100) * 3.58) for i in range(10000)],
                )
                conn.commit()
                conn.close()
                result = geo.list_map_points(-180, 180, -90, 90)
                self.assertEqual(result["total"], 10000)
                self.assertEqual(sum(point["count"] for point in result["points"]), 10000)
                self.assertLessEqual(len(result["points"]), 1024)
                self.assertEqual(len(geo.list_map_images(-180, 180, -90, 90)["items"]), 40)

    def test_maximum_date_and_stable_pagination(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(schema, "DB_PATH", str(Path(folder) / "test.sqlite")):
                schema.init_db()
                conn = schema.get_conn()
                conn.executemany(
                    "INSERT INTO image(path,directory,filename,latitude,longitude,captured_at) "
                    "VALUES (?, '', ?, 0, 0, ?)",
                    [(str(i), str(i), "2024-02-29T23:59:59") for i in range(45)] +
                    [("undated", "undated", None)],
                )
                conn.commit()
                conn.close()
                first = geo.list_map_images(-180, 180, -90, 90, None, date.max, 20, 0)
                second = geo.list_map_images(-180, 180, -90, 90, None, date.max, 20, 20)
                self.assertEqual(first["total"], 45)
                self.assertEqual(len(first["items"]), 20)
                self.assertFalse({i["id"] for i in first["items"]} &
                                 {i["id"] for i in second["items"]})
                self.assertEqual(geo.list_map_images(-180, 180, -90, 90)["total"], 46)


    def test_reads_nested_exif_gps_and_capture_date(self):
        class Exif(dict):
            def get_ifd(self, tag):
                if tag == 34853:
                    return {1: "S", 2: (12, 30, 0), 3: "E", 4: (179, 45, 0)}
                return {36867: "2021:03:04 05:06:07"}

        class Photo:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def getexif(self):
                return Exif({34853: 1, 34665: 1})

        with patch.object(geo.Image, "open", return_value=Photo()):
            self.assertEqual(
                geo.extract_image_metadata("photo.jpg"),
                (-12.5, 179.75, "2021-03-04T05:06:07"),
            )

    def test_rejects_invalid_gps_without_losing_valid_date(self):
        class Exif(dict):
            def get_ifd(self, tag):
                if tag == 34853:
                    return {1: "N", 2: (10, 60, 0), 3: "E", 4: (4, 0, 0)}
                return {36867: "2020:01:02 03:04:05"}

        class Photo:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def getexif(self): return Exif({34853: 1, 34665: 1})

        with patch.object(geo.Image, "open", return_value=Photo()):
            self.assertEqual(geo.extract_image_metadata("photo.jpg"),
                             (None, None, "2020-01-02T03:04:05"))

    def test_queries_filter_dates_antimeridian_and_exact_cluster_bounds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "geo.sqlite"
            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                rows = [
                    ("west.jpg", 10, 179.5, "2024-01-01T23:59:59"),
                    ("east.jpg", 10, -179.5, "2024-01-02T00:00:00"),
                    ("old.jpg", 10, 40, "2023-12-31T00:00:00"),
                    ("undated.jpg", 10, 41, None),
                ]
                conn.executemany(
                    """INSERT INTO image(path,directory,filename,latitude,longitude,
                       captured_at,metadata_scanned_at) VALUES (?, '', ?, ?, ?, ?, 'done')""",
                    ((name, name, lat, lon, captured) for name, lat, lon, captured in rows),
                )
                conn.commit()
                conn.close()

                result = geo.list_map_points(170, -170, 0, 20,
                                             date(2024, 1, 1), date(2024, 1, 2))
                self.assertEqual(result["total"], 2)
                self.assertEqual(result["library_total"], 4)
                self.assertEqual(result["located_total"], 4)
                self.assertEqual(result["pending_total"], 0)
                self.assertLessEqual(len(result["points"]), 1024)
                for point in result["points"]:
                    page = geo.list_map_images(point["west"], point["east"],
                                               point["south"], point["north"],
                                               date(2024, 1, 1), date(2024, 1, 2))
                    self.assertEqual(page["total"], point["count"])
                page = geo.list_map_images(-180, 180, -90, 90,
                                           date(2024, 1, 2), date(2024, 1, 2), 1, 0)
                self.assertEqual(page["total"], 1)
                self.assertEqual(page["items"][0]["filename"], "east.jpg")

    def test_backfill_indexes_existing_rows_and_retries_missing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "geo.sqlite"
            photo = Path(temp_dir) / "photo.jpg"
            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                conn.execute(
                    "INSERT INTO image(path,directory,filename) VALUES (?, ?, ?)",
                    (str(photo), temp_dir, "photo.jpg"),
                )
                conn.commit()
                conn.close()
                worker = geo.GeoBackfill()
                worker.start()
                worker._thread.join(timeout=3)
                worker.stop()
                conn = schema.get_conn()
                self.assertEqual(conn.execute(
                    "SELECT metadata_scanned_at FROM image"
                ).fetchone()[0], "missing")
                conn.close()

                Image.new("RGB", (2, 2)).save(photo)
                with patch.object(geo, "extract_image_metadata",
                                  return_value=(1.5, 2.5, "2024-01-01T12:00:00")):
                    worker.start()
                    worker._thread.join(timeout=3)
                    worker.stop()
                conn = schema.get_conn()
                row = conn.execute(
                    "SELECT latitude, longitude, captured_at, metadata_scanned_at FROM image"
                ).fetchone()
                conn.close()
                self.assertEqual((row["latitude"], row["longitude"], row["captured_at"]),
                                 (1.5, 2.5, "2024-01-01T12:00:00"))
                self.assertNotEqual(row["metadata_scanned_at"], "missing")

    def test_legacy_schema_adds_metadata_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "old.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("""CREATE TABLE image (id INTEGER PRIMARY KEY, path TEXT UNIQUE,
                         directory TEXT, filename TEXT, content_hash TEXT, processed_at TEXT)""")
            conn.commit()
            conn.close()
            with patch.object(schema, "DB_PATH", str(db_path)):
                schema.init_db()
                conn = schema.get_conn()
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(image)")}
                conn.close()
            self.assertTrue({"latitude", "longitude", "captured_at", "metadata_scanned_at"} <= columns)


class GeoApiTest(unittest.TestCase):
    @patch("backend.app.ensure_image_thumbnail", return_value="/tmp/thumb.jpg")
    @patch("backend.app.get_available_image_path", return_value="/photos/a.jpg")
    def test_thumbnail_endpoint_returns_jpeg(self, _available, ensure):
        from backend import app
        response = app.get_image_thumbnail(7)
        self.assertEqual(response.media_type, "image/jpeg")
        ensure.assert_called_once_with(7, "/photos/a.jpg")

    @patch("backend.app.get_available_image_path", return_value=None)
    def test_thumbnail_endpoint_reports_missing_image(self, _available):
        from backend import app
        with self.assertRaises(HTTPException) as raised:
            app.get_image_thumbnail(7)
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
