import unittest
from unittest.mock import patch

from backend.services.filename_format import build_person_filename_preview


class FilenameRenamePreviewTest(unittest.TestCase):
    def test_windows_preview_shortens_by_whole_names_and_keeps_the_extension(self):
        stem = "x" * 247
        names = ["A😀", "Regina"]
        options = {"block_separator": "_", "joiner": ", ", "filename_limit": 255}
        with patch("backend.services.filename_format.os.name", "nt"):
            preview = build_person_filename_preview(f"{stem}.jpg", names, **options)
            self.assertEqual(preview["proposed_filename"], f"{stem}_A😀.jpg")
            self.assertEqual(preview["omitted_person_count"], 1)
            self.assertIsNone(preview["validation_error"])
            self.assertIsNone(build_person_filename_preview(preview["proposed_filename"], names, **options))

            overflow = build_person_filename_preview(f"{stem}y.jpg", names, **options)
            self.assertEqual(overflow["validation_error"], "filename_too_long")

    def test_formatted_names_are_recognized_on_the_next_rename(self):
        for separator, joiner in (("_", " und "), ("_", ""), ("", " und ")):
            with self.subTest(separator=separator, joiner=joiner):
                preview = build_person_filename_preview(
                    "Sommerurlaub.jpg", ["Kai", "Regina"], block_separator=separator, joiner=joiner,
                )
                self.assertIsNone(build_person_filename_preview(
                    preview["proposed_filename"], ["Kai", "Regina"], block_separator=separator, joiner=joiner,
                ))

    def test_parenthesis_in_base_filename_is_preserved(self):
        preview = build_person_filename_preview(
            "Test (1) Kai.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], ["Kai"])
        self.assertEqual(preview["proposed_filename"], "Test (1) Kai, Regina.jpg")

    def test_detected_name_inside_base_filename_is_not_replaced(self):
        preview = build_person_filename_preview(
            "Kai's Geburtstag.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], [])
        self.assertEqual(preview["proposed_filename"], "Kai's Geburtstag Kai, Regina.jpg")

    def test_existing_suffix_with_wrong_order_is_normalized(self):
        preview = build_person_filename_preview(
            "Kai's Geburtstag Regina, Kai.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], ["Regina", "Kai"])
        self.assertEqual(preview["proposed_filename"], "Kai's Geburtstag Kai, Regina.jpg")

    def test_existing_suffix_with_subset_is_extended(self):
        preview = build_person_filename_preview(
            "Kai's Geburtstag Regina.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], ["Regina"])
        self.assertEqual(preview["proposed_filename"], "Kai's Geburtstag Kai, Regina.jpg")

    def test_existing_suffix_with_single_matching_name_is_extended(self):
        preview = build_person_filename_preview(
            "Kai's Geburtstag Kai.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], ["Kai"])
        self.assertEqual(preview["proposed_filename"], "Kai's Geburtstag Kai, Regina.jpg")

    def test_existing_suffix_with_multiple_spaces_is_cleaned_up(self):
        preview = build_person_filename_preview(
            "Kai's Geburtstag Kai   Regina.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], ["Kai", "Regina"])
        self.assertEqual(preview["proposed_filename"], "Kai's Geburtstag Kai, Regina.jpg")

    def test_existing_suffix_without_space_after_delimiter_is_cleaned_up(self):
        preview = build_person_filename_preview(
            "Kai's Geburtstag Kai,Regina.jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], ["Kai", "Regina"])
        self.assertEqual(preview["proposed_filename"], "Kai's Geburtstag Kai, Regina.jpg")

    def test_non_person_trailing_text_is_not_treated_as_suffix(self):
        preview = build_person_filename_preview(
            "Test (1).jpg",
            ["Kai", "Regina"],
            block_separator=" ",
            joiner=", ",
        )

        self.assertEqual(preview["current_suffix_person_names"], [])
        self.assertEqual(preview["proposed_filename"], "Test (1) Kai, Regina.jpg")


if __name__ == "__main__":
    unittest.main()
