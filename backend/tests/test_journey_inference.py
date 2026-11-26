import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.db import schema
from backend.services.geo import list_selected_map_images
from backend.services.geo_query import GeoBounds, GeoPhotoSelection
from backend.services.journey import list_journey


class JourneyInferenceTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(schema, "DB_PATH", str(Path(folder) / "journey.sqlite")))
        schema.init_db(scan_sources=False)

    def photo(self, image_id, minute, longitude, *, latitude=None, source="gps_utc", wall=True, verified_clock=True):
        timestamp = datetime(2026, 1, 2, 12) + timedelta(minutes=minute)
        if longitude is not None and latitude is None:
            latitude = 0
        return (image_id, str(image_id), latitude, longitude, timestamp.isoformat() if wall else None,
                timestamp.replace(tzinfo=timezone.utc).isoformat() if verified_clock else None, source)

    def replace_photos(self, photos):
        with closing(schema.get_conn()) as connection, connection:
            connection.execute("DELETE FROM image")
            connection.executemany(
                """INSERT INTO image (id, path, directory, filename, latitude, longitude, captured_at,
                                      captured_at_utc, capture_time_source) VALUES (?, ?, '', '', ?, ?, ?, ?, ?)""",
                photos,
            )

    def test_isolated_gap_is_attached_without_moving_route_or_changing_metadata(self):
        before = self.photo(9, 0, 0)
        candidate = self.photo(1, 15, None, source="exif_original_offset")
        after = self.photo(4, 30, 0.0008)
        next_stop = self.photo(5, 40, 2)
        self.replace_photos([next_stop, after, before])
        day = date(2026, 1, 2)
        baseline = list_journey(day, day)

        self.replace_photos([next_stop, after, candidate, before])
        result = list_journey(day, day)
        self.assertEqual(result["stops"][0], {
            **baseline["stops"][0], "image_ids": [9, 1, 4], "inferred_image_ids": [1],
        })
        self.assertEqual(result["stops"][1], baseline["stops"][1])
        self.assertEqual(result["connections"], baseline["connections"])
        self.assertEqual(result["inferred_location_count"], 1)
        self.assertEqual(result["missing_location_count"], 0)
        self.assertEqual(result["photo_count"], 4)
        gallery = list_selected_map_images(GeoPhotoSelection(image_ids=tuple(result["stops"][0]["image_ids"])))
        self.assertEqual({image["id"] for image in gallery["items"]}, {1, 4, 9})
        with closing(schema.get_conn()) as connection:
            row = connection.execute(
                "SELECT latitude, longitude, captured_at, captured_at_utc, capture_time_source FROM image WHERE id = 1"
            ).fetchone()
        self.assertEqual(tuple(row), candidate[2:])

    def test_ambiguous_or_broken_evidence_keeps_gap_unassigned(self):
        before = self.photo(1, 0, 0)
        candidate = self.photo(2, 10, None)
        after = self.photo(3, 20, 0.0008)
        cases = {
            "long gap": [before, candidate, self.photo(3, 26, 0.0008)],
            "distant observations in same trip stop": [before, candidate, self.photo(3, 20, 0.001)],
            "consecutive unlocated photos": [before, candidate, after, self.photo(4, 15, None)],
            "UTC tie at candidate": [before, candidate, after, self.photo(4, 10, 0)],
            "UTC tie at neighbour": [before, candidate, after, self.photo(4, 0, 0)],
            "candidate has ambiguous legacy clock": [before, self.photo(2, 10, None, source="exif_offset"), after],
            "neighbour has ambiguous legacy clock": [self.photo(1, 0, 0, source="exif_offset"), candidate, after],
            "no verified timestamp": [before, self.photo(2, 10, None, verified_clock=False), after],
            "partial coordinates": [before, self.photo(2, 10, None, latitude=0), after],
            "invalid coordinates": [before, self.photo(2, 10, 0, latitude=91), after],
            "intervening local-only clock": [before, candidate, after, self.photo(4, 15, None, verified_clock=False)],
            "timed detour without a wall clock": [before, candidate, after, self.photo(4, 15, 2, wall=False)],
        }
        for case, photos in cases.items():
            with self.subTest(case=case):
                self.replace_photos(photos)
                result = list_journey(None, None)
                self.assertEqual(result["inferred_location_count"], 0)
                self.assertGreaterEqual(result["missing_location_count"], 1)
                self.assertTrue(all(not stop["inferred_image_ids"] for stop in result["stops"]))

    def test_inference_stays_in_one_existing_stop_and_selected_area(self):
        photos = [self.photo(1, 0, -0.0004), self.photo(2, 10, None), self.photo(3, 20, 0.0004)]
        self.replace_photos(photos)
        self.assertEqual(list_journey(None, None, GeoBounds(-1, 1, -1, 1))["inferred_location_count"], 1)
        self.assertEqual(list_journey(None, None, GeoBounds(0, 1, -1, 1))["inferred_location_count"], 0)

        self.replace_photos([self.photo(4, -5, -0.001), *photos])
        result = list_journey(None, None, detail="city")
        self.assertEqual([stop["image_ids"] for stop in result["stops"]], [[4, 1], [3]])
        self.assertEqual(result["inferred_location_count"], 0)

    def test_inference_requires_the_candidate_and_neighbors_in_selected_album(self):
        self.replace_photos([self.photo(1, 0, 0), self.photo(2, 10, None), self.photo(3, 20, 0.0008)])
        album_id = "a" * 32
        with closing(schema.get_conn()) as connection, connection:
            connection.execute(
                "INSERT INTO album (id, name, created_at, updated_at) VALUES (?, '', '', '')", (album_id,),
            )
            connection.executemany(
                "INSERT INTO album_photo (album_id, image_id, position) VALUES (?, ?, ?)",
                [(album_id, 1, 1), (album_id, 2, 2)],
            )
        self.assertEqual(list_journey(None, None, album_id=album_id)["inferred_location_count"], 0)
        self.assertEqual(list_journey(None, None)["inferred_location_count"], 1)
