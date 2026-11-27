from contextlib import closing
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.db import schema
from backend.services.geo_query import GeoPhotoSelection
from backend.services.observed_places import list_observed_places


class ObservedPlacesTest(unittest.TestCase):
    def test_repeated_visits_keep_year_counts_and_respect_photo_scope(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(schema, "DB_PATH", str(Path(folder) / "places.sqlite")):
            schema.init_db(scan_sources=False)
            album_id = "a" * 32
            with closing(schema.get_conn()) as connection:
                connection.executemany(
                    """INSERT INTO image (id, path, directory, filename, latitude, longitude, captured_at)
                       VALUES (?, ?, '', 'image.jpg', ?, ?, ?)""",
                    [
                        (1, "first", 52.521, 13.401, "2021-06-20T10:30:00"),
                        (2, "second", 52.522, 13.402, "2024-07-22T20:24:00"),
                        (3, "third", 52.523, 13.403, "2024-07-29T15:30:00"),
                        (4, "undated", 52.524, 13.404, None),
                        (5, "elsewhere", 48.85, 2.35, "2024-10-11T15:16:00"),
                        (6, "unlocated", None, None, "2021-10-11T15:16:00"),
                    ],
                )
                connection.execute(
                    "INSERT INTO album (id, name, created_at, updated_at) VALUES (?, 'Sommer', '', '')", (album_id,),
                )
                connection.executemany(
                    "INSERT INTO album_photo (album_id, image_id, position) VALUES (?, ?, ?)",
                    [(album_id, 1, 0), (album_id, 2, 1), (album_id, 6, 2)],
                )
                connection.commit()

            first_page = list_observed_places(GeoPhotoSelection(), 1, 0)
            self.assertEqual(first_page, {"places": [{
                "latitude": 52.52, "longitude": 13.4, "photo_count": 4, "year_counts": {"2021": 1, "2024": 2},
                "first_day": "2021-06-20", "last_day": "2024-07-29",
            }], "next_offset": 1})
            next_page = list_observed_places(GeoPhotoSelection(), 1, first_page["next_offset"])
            self.assertIsNone(next_page["next_offset"])
            self.assertEqual(next_page["places"][0]["year_counts"], {"2024": 1})

            scoped = list_observed_places(GeoPhotoSelection(from_date=date(2024, 1, 1), album_id=album_id), 150, 0)
            self.assertEqual(len(scoped["places"]), 1)
            self.assertEqual(scoped["places"][0]["photo_count"], 1)
            self.assertEqual(scoped["places"][0]["year_counts"], {"2024": 1})
