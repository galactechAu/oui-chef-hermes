import json
import tempfile
import unittest
from pathlib import Path

from generation import extract_json, generation_prompt, validate_generation
from store import ShoppingStore


class GenerationTests(unittest.TestCase):
    def test_extract_json_ignores_cli_banner(self):
        payload = extract_json('warning line\n{"meals":[{"name":"Lean chilli","ingredients":[]}]}\nsession_id: x')
        self.assertEqual(payload["meals"][0]["name"], "Lean chilli")

    def test_validation_rejects_mushroom_ingredient(self):
        with self.assertRaises(ValueError):
            validate_generation({"meals": [{"name": "Bad", "ingredients": [{"name": "mushrooms", "quantity": 1, "unit": "pack"}]}]})

    def test_validation_rejects_configured_allergen(self):
        payload = {"meals": [{"name": "Peanut chicken", "source_url": "https://example.com/chicken", "source_title": "Chicken recipe", "ingredients": [{"name": "Peanut oil", "quantity": 1, "unit": "tbsp"}]}]}
        with self.assertRaisesRegex(ValueError, "peanuts"):
            validate_generation(payload, ["peanuts"])

    def test_prompt_includes_dietary_allergies(self):
        prompt = generation_prompt(1, "", "Easy", [], allergies=["mushrooms", "dairy"])
        self.assertIn("dairy", prompt)

    def test_prompt_includes_three_star_or_better_taste_signals(self):
        prompt = generation_prompt(2, "", "Easy", [], {"names": set(), "urls": set()}, [{"name": "Lemon chicken", "rating": 3}])
        self.assertIn("Positive household taste signals", prompt)
        self.assertIn("Lemon chicken (3★)", prompt)

    def test_validation_requires_a_public_recipe_source(self):
        with self.assertRaisesRegex(ValueError, "public recipe source"):
            validate_generation({"meals": [{"name": "Chicken", "ingredients": [{"name": "Chicken breast", "quantity": 500, "unit": "g"}]}]})

    def test_validation_rejects_private_recipe_source(self):
        with self.assertRaisesRegex(ValueError, "public recipe source"):
            validate_generation({"meals": [{"name": "Chicken", "source_url": "http://127.0.0.1/recipe", "source_title": "Chicken recipe", "ingredients": [{"name": "Chicken breast", "quantity": 500, "unit": "g"}]}]})

    def test_validation_accepts_a_public_recipe_source(self):
        meals = validate_generation({"meals": [{"name": "Chicken", "source_url": "https://example.com/recipes/chicken", "source_title": "Chicken recipe", "ingredients": [{"name": "Chicken breast", "quantity": 500, "unit": "g"}]}]})
        self.assertEqual(meals[0]["source_title"], "Chicken recipe")

    def test_source_evidence_requires_recipe_title_and_ingredients_in_fetched_page(self):
        from generation import verify_source_evidence
        meal = {"name": "Lemon chicken", "source_title": "Lemon chicken recipe", "ingredients": [{"name": "chicken breast", "quantity": 500, "unit": "g"}]}
        self.assertTrue(verify_source_evidence(meal, "<title>Lemon chicken recipe</title><p>chicken breast</p>"))
        self.assertFalse(verify_source_evidence(meal, "<title>Another page</title><p>rice</p>"))

    def test_generation_rejects_recipe_not_supported_by_fetched_source(self):
        from unittest.mock import patch
        from app import ask_hermes
        output = json.dumps({"meals": [{"name": "Chicken", "source_url": "https://example.com/chicken", "source_title": "Chicken recipe", "ingredients": [{"name": "Chicken breast", "quantity": 500, "unit": "g"}]}]})
        with patch("app.request_hermes", return_value={"output": output}), patch("app.validate_public_redirects", side_effect=lambda url: url), patch("app.fetch_public_source_text", return_value="<title>Other recipe</title>"), patch("app.STORE.record_generation_candidates"):
            with self.assertRaisesRegex(ValueError, "source evidence"):
                ask_hermes(1, "", "Easy")

    def test_import_rejects_configured_dietary_allergen(self):
        from unittest.mock import patch
        from app import ask_import_recipe
        recipe = {"name": "Peanut chicken", "ingredients": ["chicken", "peanut oil"], "steps": ["Cook safely"]}
        with patch("app.request_hermes", return_value={"output": json.dumps(recipe)}), patch("app.STORE.get_dietary_allergies", return_value=["mushrooms", "peanuts"]):
            with self.assertRaisesRegex(ValueError, "peanuts"):
                ask_import_recipe({"text": "Chicken recipe"})

    def test_selected_draft_creates_a_new_aggregated_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lists.json"
            path.write_text(json.dumps({"lists": []}))
            store = ShoppingStore(path)
            draft = store.create_draft("more vegetarian", [{
                "name": "Chickpea chilli", "ingredients": [
                    {"name": "Chickpeas", "quantity": 2, "unit": "can", "aisle": "Pantry"},
                    {"name": "Tomatoes", "quantity": 2, "unit": "each", "aisle": "Fresh produce"},
                ], "method": ["Cook it"], "complexity": "Easy"
            }])
            created = store.create_list_from_draft(draft["id"], [0], "Vegetarian week")
            self.assertEqual(created["name"], "Vegetarian week")
            self.assertEqual(created["meals"][0]["name"], "Chickpea chilli")
            self.assertEqual(len(created["items"]), 2)
            self.assertEqual(store.get_draft(draft["id"]), None)


if __name__ == "__main__":
    unittest.main()
