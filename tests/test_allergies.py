import unittest

from allergies import detect_allergens


class AllergyTests(unittest.TestCase):
    def test_detects_configured_allergen_in_recipe_text(self):
        self.assertEqual(detect_allergens("Chicken satay with peanut oil", ["peanuts", "dairy"]), ["peanuts"])

    def test_detects_phrase_allergen_case_insensitively(self):
        self.assertEqual(detect_allergens("Add FISH SAUCE and lime", ["fish sauce"]), ["fish sauce"])

    def test_does_not_match_partial_words(self):
        self.assertEqual(detect_allergens("Peanut-free chicken", ["nuts"]), [])


if __name__ == "__main__":
    unittest.main()
