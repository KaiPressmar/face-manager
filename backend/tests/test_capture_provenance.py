import unittest
from io import BytesIO

from PIL import Image

from backend.services.geo import extract_image_metadata


class CaptureProvenanceTest(unittest.TestCase):
    def photo(self, values):
        exif = Image.Exif()
        exif[34665] = values
        output = BytesIO()
        Image.new("RGB", (8, 8)).save(output, "JPEG", exif=exif)
        output.seek(0)
        return output

    def test_scan_date_and_file_date_do_not_become_capture_time(self):
        scan = self.photo({36868: "2026:07:12 08:00:00", 36882: "+02:00"})
        self.assertEqual(extract_image_metadata(scan, include_timing=True)[2:], (None, None, "missing"))

    def test_original_date_and_matching_offset_are_recorded(self):
        original = self.photo({
            36867: "2005:07:12 00:04:00", 36881: "+02:00",
            36868: "2026:07:12 08:00:00", 36882: "+01:00",
        })
        self.assertEqual(extract_image_metadata(original, include_timing=True)[2:], (
            "2005-07-12T00:04:00", "2005-07-11T22:04:00.000000+00:00", "exif_original_offset",
        ))
