"""Page through occupied map cells in the local photo library."""
from contextlib import closing
from ..db.schema import get_conn
from .geo_query import GeoPhotoSelection, photo_selection_sql


def list_observed_places(selection: GeoPhotoSelection, limit: int, offset: int) -> dict:
    where, values = photo_selection_sql(selection, require_coordinates=True)
    with closing(get_conn()) as connection:
        cells = connection.execute(
            f"""SELECT ROUND(latitude, 2) AS latitude, ROUND(longitude, 2) AS longitude,
                       COUNT(*) AS photo_count, MIN(substr(captured_at, 1, 10)) AS first_day,
                       MAX(substr(captured_at, 1, 10)) AS last_day
                FROM image i WHERE {where}
                GROUP BY ROUND(latitude, 2), ROUND(longitude, 2)
                ORDER BY photo_count DESC, latitude, longitude LIMIT ? OFFSET ?""",
            (*values, limit + 1, offset),
        ).fetchall()
    return {"places": [dict(cell) for cell in cells[:limit]], "next_offset": offset + limit if len(cells) > limit else None}
