import unittest

from backend.services.storage import build_person_filename_preview


class FilenameRenamePreviewTest(unittest.TestCase):
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
