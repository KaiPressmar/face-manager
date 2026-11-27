"""Page through occupied map cells in the local photo library."""
import json
from contextlib import closing

from ..db.schema import get_conn
from .geo_query import GeoPhotoSelection, photo_selection_sql


def list_observed_places(selection: GeoPhotoSelection, limit: int, offset: int) -> dict:
    where, values = photo_selection_sql(selection, require_coordinates=True)
    with closing(get_conn()) as connection:
        cells = connection.execute(
            f"""SELECT latitude, longitude, SUM(year_count) AS photo_count,
                       MIN(first_day) AS first_day, MAX(last_day) AS last_day,
                       json_group_object(year, year_count) FILTER (WHERE year IS NOT NULL) AS year_counts
                FROM (
                    SELECT ROUND(latitude, 2) AS latitude, ROUND(longitude, 2) AS longitude,
                           CASE WHEN substr(captured_at, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
                                  AND substr(captured_at, 1, 4) <> '0000'
                                THEN substr(captured_at, 1, 4) END AS year,
                           COUNT(*) AS year_count, MIN(substr(captured_at, 1, 10)) AS first_day,
                           MAX(substr(captured_at, 1, 10)) AS last_day
                    FROM image i WHERE {where}
                    GROUP BY 1, 2, 3
                )
                GROUP BY latitude, longitude
                ORDER BY photo_count DESC, latitude, longitude LIMIT ? OFFSET ?""",
            (*values, limit + 1, offset),
        ).fetchall()
    places = [{**dict(cell), "year_counts": json.loads(cell["year_counts"])} for cell in cells[:limit]]
    return {"places": places, "next_offset": offset + limit if len(cells) > limit else None}
