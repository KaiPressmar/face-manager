#!/usr/bin/env python3
"""Create an isolated collection of synthetic holiday photos with realistic EXIF."""

import argparse
import json
import os
import random
import sys
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, TiffImagePlugin


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    setting: str = "city"
    zone: str = "Europe/Berlin"


PLACES = {
    "home": Place("Stuttgart · West", 48.7734, 9.1512),
    "park": Place("Stuttgart · Killesberg", 48.8051, 9.1714),
    "esslingen": Place("Esslingen · Altstadt", 48.7414, 9.3044),
    "strasbourg": Place("Strasbourg · Petite France", 48.5814, 7.7413),
    "colmar": Place("Colmar · Petite Venise", 48.0749, 7.3593),
    "basel": Place("Basel · Rhein", 47.5606, 7.5917),
    "lucerne": Place("Luzern · Kapellbrücke", 47.0518, 8.3075),
    "interlaken": Place("Interlaken · Höheweg", 46.6863, 7.8583, "mountain"),
    "grindelwald": Place("Grindelwald · Dorf", 46.6242, 8.0415, "mountain"),
    "laut": Place("Lauterbrunnen · Wasserfall", 46.5892, 7.9053, "mountain"),
    "spiez": Place("Spiez · Bucht", 46.6875, 7.6879, "mountain"),
    "dijon": Place("Dijon · Place de la Libération", 47.3211, 5.0412),
    "lyon": Place("Lyon · Vieux Lyon", 45.7640, 4.8273),
    "avignon": Place("Avignon · Palais des Papes", 43.9508, 4.8075),
    "aix": Place("Aix-en-Provence · Cours Mirabeau", 43.5268, 5.4479),
    "marseille": Place("Marseille · Vieux-Port", 43.2947, 5.3710, "coast"),
    "cassis": Place("Cassis · Port", 43.2143, 5.5365, "coast"),
    "calanque": Place("Cassis · Port-Miou", 43.2081, 5.5232, "coast"),
    "nice": Place("Nice · Promenade des Anglais", 43.6932, 7.2590, "coast"),
    "nice-hill": Place("Nice · Colline du Château", 43.6953, 7.2797, "coast"),
    "eze": Place("Èze · Jardin exotique", 43.7279, 7.3628, "coast"),
    "menton": Place("Menton · Vieille Ville", 43.7772, 7.5044, "coast"),
    "salzburg": Place("Salzburg · Mirabellgarten", 47.8057, 13.0417),
    "linz": Place("Linz · Hauptplatz", 48.3069, 14.2857),
    "vienna": Place("Wien · Stephansplatz", 48.2084, 16.3724),
    "graben": Place("Wien · Graben", 48.2086, 16.3709),
    "hofburg": Place("Wien · Hofburg", 48.2072, 16.3659),
    "helden": Place("Wien · Heldenplatz", 48.2059, 16.3630),
    "mq": Place("Wien · MuseumsQuartier", 48.2034, 16.3586),
    "ring": Place("Wien · Volksgarten", 48.2078, 16.3625),
    "prater": Place("Wien · Prater", 48.2167, 16.3959),
    "belvedere": Place("Wien · Schloss Belvedere", 48.1915, 16.3807),
    "schonbrunn": Place("Wien · Schönbrunn", 48.1848, 16.3122),
    "wachau": Place("Dürnstein · Wachau", 48.3961, 15.5200),
    "bratislava": Place("Bratislava · Altstadt", 48.1439, 17.1073),
    "tokyo": Place("Tokio · Shinjuku", 35.6908, 139.7005, zone="Asia/Tokyo"),
    "asakusa": Place("Tokio · Sensō-ji", 35.7149, 139.7967, zone="Asia/Tokyo"),
    "kyoto": Place("Kyoto · Fushimi Inari", 34.9673, 135.7726, zone="Asia/Tokyo"),
    "arashiyama": Place("Kyoto · Arashiyama", 35.0170, 135.6713, "mountain", "Asia/Tokyo"),
    "nara": Place("Nara · Park", 34.6852, 135.8430, zone="Asia/Tokyo"),
    "osaka": Place("Osaka · Dōtonbori", 34.6686, 135.5012, zone="Asia/Tokyo"),
    "fiji": Place("Nadi · Wailoaloa", -17.7667, 177.4235, "coast", "Pacific/Fiji"),
    "suva": Place("Suva · Albert Park", -18.1457, 178.4258, "coast", "Pacific/Fiji"),
    "tonga": Place("Nukuʻalofa · Vuna Road", -21.1306, -175.2049, "coast", "Pacific/Tongatapu"),
    "haatafu": Place("Haʻatafu · Strand", -21.0650, -175.3376, "coast", "Pacific/Tongatapu"),
}

ALPS = (
    ("home", "strasbourg", "colmar"), ("colmar", "basel", "lucerne"),
    ("lucerne", "interlaken", "grindelwald"), ("grindelwald", "laut", "grindelwald"),
    ("grindelwald", "spiez", "interlaken"), ("interlaken", "basel", "home"),
)
COAST = (
    ("home", "strasbourg", "dijon"), ("dijon", "lyon", "avignon"),
    ("avignon", "aix", "marseille"), ("marseille", "cassis", "calanque", "marseille"),
    ("marseille", "nice", "nice-hill"), ("nice", "eze", "menton", "nice"),
    ("nice", "lyon", "dijon"), ("dijon", "basel", "home"),
)
VIENNA = (
    ("home", "salzburg", "linz"), ("linz", "wachau", "vienna"),
    ("vienna", "graben", "hofburg", "helden", "mq", "ring"),
    ("vienna", "belvedere", "schonbrunn", "prater"), ("vienna", "bratislava", "vienna"),
    ("vienna", "salzburg", "home"),
)
JAPAN = (
    ("tokyo", "asakusa", "tokyo"), ("tokyo", "asakusa", "tokyo"),
    ("tokyo", "kyoto", "arashiyama"), ("arashiyama", "kyoto", "arashiyama"),
    ("kyoto", "nara", "osaka"), ("osaka", "kyoto", "tokyo"),
)
PACIFIC = (
    ("fiji", "suva"), ("suva", "fiji"), ("fiji", "tonga"),
    ("tonga", "haatafu", "tonga"), ("tonga", "fiji"),
)
TRIPS = (
    ("2019-08-03", "Elsass und Schweizer Alpen", ALPS),
    ("2020-09-12", "Wien und Wachau", VIENNA),
    ("2021-07-10", "Provence und Côte d’Azur", COAST),
    ("2022-02-19", "Winter in den Alpen", ALPS),
    ("2022-09-17", "Wien mit Freunden", VIENNA),
    ("2023-05-06", "Frühling in Japan", JAPAN),
    ("2023-08-12", "Sommer am Mittelmeer", COAST),
    ("2024-04-27", "Wiener Stadtspaziergänge", VIENNA),
    ("2024-09-14", "Wieder in Grindelwald", ALPS),
    ("2025-03-08", "Fidschi und Tonga", PACIFIC),
    ("2025-07-19", "Cassis und die Riviera", COAST),
    ("2025-10-04", "Herbst in Japan", JAPAN),
    ("2026-05-23", "Pfingsten in den Alpen", ALPS),
    ("2026-08-15", "Noch einmal Côte d’Azur", COAST),
    ("2026-10-10", "Wien im Herbst", VIENNA),
)


def degrees(decimal):
    scale = 10_000
    whole, remainder = divmod(round(abs(decimal) * 3600 * scale), 3600 * scale)
    minutes, seconds = divmod(remainder, 60 * scale)
    rational = TiffImagePlugin.IFDRational
    return rational(whole), rational(minutes), rational(seconds, scale)


def make_exif(captured, latitude, longitude, timing, orientation):
    exif = Image.Exif()
    exif[271] = "Face Manager test fixture"
    exif[272] = "Synthetic holiday library"
    exif[274] = orientation
    if timing != "missing":
        camera = {36867: captured.strftime("%Y:%m:%d %H:%M:%S")}
        if timing != "local":
            offset = captured.strftime("%z")
            camera[36881] = f"{offset[:3]}:{offset[3:]}"
        exif[34665] = camera
    if latitude is not None:
        gps = {1: "N" if latitude >= 0 else "S", 2: degrees(latitude),
               3: "E" if longitude >= 0 else "W", 4: degrees(longitude)}
        if timing in ("offset", "conflict"):
            utc = captured.astimezone(timezone.utc)
            if timing == "conflict":
                utc += timedelta(hours=3)
            gps[7] = tuple(map(TiffImagePlugin.IFDRational, (utc.hour, utc.minute, utc.second)))
            gps[29] = utc.strftime("%Y:%m:%d")
        exif[34853] = gps
    return exif


def draw_scene(image, setting, rng):
    draw = ImageDraw.Draw(image)
    width, height = image.size
    skyline = round(height * rng.uniform(.44, .59))
    tint = rng.randrange(28)
    sun_x = rng.randrange(width)
    sun_y = rng.randrange(round(height * .08), round(height * .33))
    radius = round(height * rng.uniform(.03, .07))
    for y in range(skyline):
        fraction = y / skyline
        draw.line((0, y, width, y), fill=(int(78 + tint + fraction * 120), int(135 + fraction * 72), 190 + tint))
    draw.ellipse((sun_x-radius, sun_y-radius, sun_x+radius, sun_y+radius), fill=(251, 225, 168))
    if setting == "coast":
        draw.rectangle((0, skyline, width, height), fill=(36 + tint, 104 + tint, 134 + tint))
        for _ in range(38):
            y = rng.randrange(skyline, height)
            x = rng.randrange(width)
            draw.line((x, y, x + rng.randrange(8, 100), y), fill=(104, 163, 165), width=1)
        draw.polygon(((0, height * .77), (width * .36, height * .93), (width, height * .85), (width, height), (0, height)),
                     fill=(206, 177, 133))
    elif setting == "mountain":
        draw.rectangle((0, skyline, width, height), fill=(67, 102 + tint, 79))
        for index in range(5):
            center = width * (index + .2) / 4
            peak = skyline * rng.uniform(.25, .8)
            draw.polygon(((center - width * .27, height * .8), (center, peak), (center + width * .32, height * .81)),
                         fill=(74 + tint, 103 + index * 4, 116))
            draw.polygon(((center - width * .07, peak + height * .15), (center, peak),
                          (center + width * .09, peak + height * .16), (center, peak + height * .13)), fill=(219, 228, 228))
    else:
        draw.rectangle((0, skyline, width, height), fill=(118, 111, 104))
        x = 0
        while x < width:
            span = rng.randrange(34, 95)
            top = skyline - rng.randrange(15, round(height * .26))
            bottom = skyline + rng.randrange(18, round(height * .25))
            draw.rectangle((x, top, x + span, bottom), fill=(rng.randrange(131, 223), rng.randrange(105, 160), rng.randrange(92, 133)))
            draw.polygon(((x, top), (x + span / 2, top - 18), (x + span, top)), fill=(101, 76, 75))
            for y in range(top + 12, bottom - 13, 22):
                for window in range(x + 9, x + span - 6, 16):
                    draw.rectangle((window, y, window + 5, y + 10), fill=(71, 82, 84))
            x += span + 2


def write_photo(path, place, captured, sequence, exif, rng):
    width, height = rng.choice(((720, 480), (720, 480), (540, 720), (864, 360)))
    with Image.new("RGB", (width, height)) as scene:
        draw_scene(scene, place.setting, rng)
        with Image.frombytes("L", scene.size, rng.randbytes(width * height)) as grain:
            with Image.blend(scene, grain.convert("RGB"), .055) as image:
                draw = ImageDraw.Draw(image)
                draw.text((14, height - 28), f"{place.name} · {captured:%d.%m.%Y} · {sequence:05d}", fill="#ffffff",
                          stroke_width=2, stroke_fill="#343230")
                if exif[274] == 6:
                    image = image.transpose(Image.Transpose.ROTATE_90)
                image.save(path, "JPEG", quality=78, exif=exif)


def generate(root):
    photos = root / "Fotos"
    photos.mkdir(parents=True)
    rng = random.Random(20261127)
    clock_counts = Counter()
    visits = Counter()
    unlocated = 0
    sequence = 0
    trip_index = []
    weekends = (
        (date(year, month, day).isoformat(), "Alltag und Ausflüge", (("home", "park", "esslingen"),))
        for year in range(2019, 2027) for month, day in ((1, 13), (4, 21), (11, 14))
    )
    for day, title, days in sorted((*TRIPS, *weekends)):
        start_day = date.fromisoformat(day)
        trip_count = 0
        folder = photos / str(start_day.year) / f"{day} {title}"
        for day_index, stops in enumerate(days):
            local_day = start_day + timedelta(days=day_index)
            subfolder = folder / local_day.isoformat()
            subfolder.mkdir(parents=True)
            early_walk = stops[:2] == ("vienna", "graben")
            for stop, key in enumerate(stops):
                place = PLACES[key]
                visits[place.name] += 1
                start = datetime.combine(local_day, time(8), ZoneInfo(place.zone))
                start += timedelta(minutes=35 * stop if early_walk else 170 * stop + rng.randrange(40))
                burst = rng.randrange(5, 13) if len(days) > 1 else rng.randrange(2, 5)
                for picture in range(burst):
                    captured = start + timedelta(seconds=picture * 48 + rng.randrange(35))
                    sequence += 1
                    filename = f"IMG_{sequence:05d}.jpg"
                    latitude = place.latitude + rng.uniform(-.00024, .00024)
                    longitude = place.longitude + rng.uniform(-.00024, .00024)
                    if (picture == 2 and sequence % 3 == 0) or sequence % 79 == 0:
                        latitude = longitude = None
                        unlocated += 1
                    timing = "offset"
                    if sequence % 73 == 0:
                        timing = "missing"
                    elif sequence % 43 == 0:
                        timing = "local"
                    elif sequence % 131 == 0 and latitude is not None:
                        timing = "conflict"
                    clock_counts[timing] += 1
                    orientation = 6 if sequence % 11 == 0 else 1
                    exif = make_exif(captured, latitude, longitude, timing, orientation)
                    write_photo(subfolder / filename, place, captured, sequence, exif, rng)
                    trip_count += 1
        if len(days) > 1:
            trip_index.append({"title": title, "from": day, "to": local_day.isoformat(), "photos": trip_count})
    manifest = {"photos": sequence, "with_gps": sequence - unlocated, "clock_modes": dict(clock_counts),
                "visits_per_place": dict(visits.most_common()), "trips": trip_index}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return photos, manifest


def index_photos(photos, library, manifest):
    os.environ["FACE_MANAGER_DATA_DIR"] = str(library)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.db import schema
    from backend.services.pipeline import ImportProcessor, ImportResources

    class NoFaces:
        def detect_and_embed(self, pixels):
            return []

    class ReviewImportResources(ImportResources):
        def get_model(self):
            return NoFaces()

    schema.init_db()
    ImportProcessor(ReviewImportResources()).process(str(photos), lambda update: None, threading.Event())
    with schema.get_conn() as connection:
        total, geotagged = connection.execute("SELECT COUNT(*), COUNT(latitude) FROM image").fetchone()
        if (total, geotagged) != (manifest["photos"], manifest["with_gps"]):
            raise RuntimeError(f"Imported {total} photos with {geotagged} coordinates. See manifest.json for the expected counts.")
        connection.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES ('automatic_update_checks', '0')")
        connection.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES ('last_seen_changelog_version', ?)",
                           ((Path(__file__).resolve().parent.parent / "VERSION").read_text().strip(),))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for the photos and manifest")
    parser.add_argument("--index", action="store_true", help="import the photos into an isolated library, with face detection stubbed")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    photos, manifest = generate(root)
    if args.index:
        index_photos(photos, root / "library", manifest)
    print(f"{manifest['photos']} JPEGs, {manifest['with_gps']} with GPS, {len(manifest['trips'])} trips, "
          f"{len(manifest['visits_per_place'])} places in {root}")


if __name__ == "__main__":
    main()
