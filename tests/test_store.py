import json
import tempfile
import unittest
from pathlib import Path

from store import ShoppingStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "lists.json"
        self.path.write_text(json.dumps({"lists": [{"id": "week", "base_servings": 4, "servings": 4, "items": [{"id": "tomato", "name": "Tomatoes", "quantity": 2, "unit": "each", "checked": False}]}]}))
        self.store = ShoppingStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_updates_servings_persistently(self):
        updated = self.store.update_servings("week", 6)
        self.assertEqual(updated["servings"], 6)
        self.assertEqual(ShoppingStore(self.path).get_list("week")["servings"], 6)

    def test_adds_a_manual_item_and_assigns_a_sensible_aisle(self):
        self.store.add_item("week", {"name": "Bananas", "quantity": 6, "unit": "each", "notes": ""})
        saved = self.store.get_list("week")
        item = saved["items"][-1]
        self.assertEqual(item["name"], "Bananas")
        self.assertEqual(item["aisle"], "Fresh produce")
        self.assertFalse(item["checked"])

    def test_history_includes_recipe_link_and_meal_details(self):
        self.path.write_text(json.dumps({"lists": [{"id": "week", "meals": [{"name": "Chilli", "url": "https://example.test/chilli", "description": "Lean and spicy", "complexity": "Easy"}]}]}))
        meal = self.store.history()[0]
        self.assertEqual(meal["url"], "https://example.test/chilli")
        self.assertEqual(meal["description"], "Lean and spicy")

    def test_dietary_allergies_are_persisted_with_mushrooms_as_a_baseline(self):
        saved = self.store.set_dietary_allergies(["dairy", "fish sauce"])
        self.assertEqual(saved, ["mushrooms", "dairy", "fish sauce"])
        self.assertEqual(ShoppingStore(self.path).get_dietary_allergies(), saved)

    def test_meal_catalogue_migrates_saved_recipes_to_stable_ids(self):
        first = self.store.save_imported_recipe({"name": "Stable chicken", "ingredients": ["chicken"], "steps": ["Cook"]}, {"url": "https://example.test/chicken"})
        catalogue = self.store.get_meal_catalogue()
        self.assertEqual([meal["recipe_id"] for meal in catalogue], [first["id"]])
        self.assertTrue(catalogue[0]["id"].startswith("meal-"))
        self.assertEqual(self.store.get_meal_catalogue()[0]["id"], catalogue[0]["id"])

    def test_recipe_books_allow_many_to_many_membership_and_uncategorised_meals(self):
        first = self.store.save_imported_recipe({"name": "Lemon chicken", "ingredients": ["chicken"], "steps": ["Cook"]}, {"url": "https://example.test/lemon"})
        second = self.store.save_imported_recipe({"name": "Salmon tray bake", "ingredients": ["salmon"], "steps": ["Bake"]}, {"url": "https://example.test/salmon"})
        quick = self.store.create_recipe_book("Quick dinners")
        favourites = self.store.create_recipe_book("Favourites")
        self.store.add_meals_to_recipe_book(quick["id"], [first["id"]])
        self.store.add_meals_to_recipe_book(favourites["id"], [first["id"]])
        second_meal_id = next(row["id"] for row in self.store.get_meal_catalogue() if row["recipe_id"] == second["id"])
        self.assertEqual([meal["id"] for meal in self.store.get_uncategorised_meals()], [second_meal_id])
        self.assertEqual(self.store.get_recipe_book(quick["id"])["meal_count"], 1)
        self.store.delete_recipe_book(quick["id"])
        self.assertIsNotNone(self.store.get_imported_recipe(first["id"]))
        self.assertEqual(self.store.get_recipe_book(favourites["id"])["meal_count"], 1)

    def test_saves_imported_recipe_data_for_a_meal(self):
        self.path.write_text(json.dumps({"lists": [{"id": "week", "meals": [{"name": "Chilli"}]}]}))
        self.store.save_recipe("week", 0, {"steps": ["Cook"], "image_url": "https://img.test/a.jpg", "summary": "Lean"})
        meal = self.store.get_list("week")["meals"][0]
        self.assertEqual(meal["recipe"]["image_url"], "https://img.test/a.jpg")

    def test_adds_a_saved_recipe_to_a_new_or_existing_shopping_list(self):
        recipe = {"name": "Lemon chicken", "ingredients": ["500 g chicken breast", "1 lemon", "2 garlic cloves"], "steps": ["Cook chicken"]}
        saved = self.store.save_imported_recipe(recipe, {"type": "instagram", "label": "Public Reel"})
        created = self.store.add_imported_recipe_to_list(saved["id"], name="Lemon chicken shop")
        self.assertEqual(created["meals"][0]["name"], "Lemon chicken")
        self.assertIn("chicken breast", [item["name"].lower() for item in created["items"]])
        updated = self.store.add_imported_recipe_to_list(saved["id"], list_id=created["id"])
        self.assertEqual(len(updated["meals"]), 2)

    def test_mutation_notifies_realtime_subscribers_after_persisting(self):
        from realtime import EventHub
        hub = EventHub(); subscriber = hub.subscribe()
        store = ShoppingStore(self.path, publish=hub.publish)
        store.add_item("week", {"name": "Live coriander", "quantity": 1, "unit": "each"})
        self.assertEqual(subscriber.get(timeout=0.1)["type"], "state.changed")

    def test_creates_an_empty_named_shopping_list(self):
        listing = self.store.create_empty_list("Weekend top-up", "2026-09-12")
        self.assertEqual(listing["name"], "Weekend top-up")
        self.assertEqual(listing["date"], "2026-09-12")
        self.assertEqual(listing["meals"], [])
        self.assertEqual(listing["items"], [])

    def test_removes_one_list_meal_without_deleting_its_shopping_items(self):
        self.path.write_text(json.dumps({"lists": [{"id": "week", "meals": [{"name": "Chicken"}, {"name": "Salmon"}], "items": [{"id": "chicken", "name": "Chicken"}]}]}))
        listing = self.store.delete_list_meal("week", 0)
        self.assertEqual([meal["name"] for meal in listing["meals"]], ["Salmon"])
        self.assertEqual([item["id"] for item in listing["items"]], ["chicken"])

    def test_deletes_jobs_meals_and_shopping_lists_independently(self):
        recipe = {"name": "Temporary chicken", "ingredients": ["Chicken breast"], "steps": ["Cook"]}
        saved = self.store.save_imported_recipe(recipe, {"type": "text", "label": "Notes"})
        job = self.store.create_import_job({"text": "temporary"})
        self.store.update_import_job(job["id"], status="failed", progress=100, stage="Import failed", error="test")
        self.store.delete_import_job(job["id"])
        self.assertIsNone(self.store.get_import_job(job["id"]))
        self.store.delete_imported_recipe(saved["id"])
        self.assertIsNone(self.store.get_imported_recipe(saved["id"]))
        self.store.delete_list("week")
        self.assertIsNone(self.store.get_list("week"))

    def test_persists_recipe_import_job_progress_and_result(self):
        job = self.store.create_import_job({"text": "chicken recipe"})
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["progress"], 0)
        updated = self.store.update_import_job(job["id"], status="review", progress=100, stage="Ready to review", recipe={"name": "Chicken", "ingredients": ["Chicken"], "steps": ["Cook"]})
        restored = ShoppingStore(self.path).get_import_job(job["id"])
        self.assertEqual(updated["status"], "review")
        self.assertEqual(restored["recipe"]["name"], "Chicken")
        self.assertEqual(restored["events"][-1]["stage"], "Ready to review")

    def test_generation_job_enforces_one_active_job_and_can_be_stopped_deleted(self):
        job = self.store.create_generation_job({"count": 1, "instruction": "chicken", "complexity": "Easy"})
        with self.assertRaises(ValueError): self.store.create_generation_job({"count": 1, "instruction": "salmon", "complexity": "Easy"})
        stopped = self.store.cancel_generation_job(job["id"])
        self.assertEqual(stopped["status"], "cancelled")
        self.store.delete_generation_job(job["id"])
        self.assertIsNone(self.store.get_generation_job(job["id"]))

    def test_saves_and_reads_a_standalone_imported_recipe(self):
        recipe = {"name": "Quick chicken", "ingredients": ["Chicken breast"], "steps": ["Dice onion", "Cook chicken"], "health_rating": 5}
        saved = self.store.save_imported_recipe(recipe, {"type": "text", "label": "Pasted notes"})
        restored = ShoppingStore(self.path).get_imported_recipe(saved["id"])
        self.assertEqual(restored["recipe"]["name"], "Quick chicken")
        self.assertEqual(restored["source"]["type"], "text")

    def test_toggles_checked_item_persistently(self):
        updated = self.store.toggle_item("week", "tomato", True)
        self.assertTrue(updated["items"][0]["checked"])
        self.assertTrue(ShoppingStore(self.path).get_list("week")["items"][0]["checked"])

    def test_persists_global_aisle_order(self):
        order = self.store.set_aisle_order(["Pantry", "Fresh produce", "Meat & seafood"])
        self.assertEqual(order[:2], ["Pantry", "Fresh produce"])
        self.assertEqual(ShoppingStore(self.path).get_aisle_order()[:2], ["Pantry", "Fresh produce"])

    def test_rejects_more_than_ten_servings(self):
        with self.assertRaisesRegex(ValueError, "1 to 10"):
            self.store.update_servings("week", 11)

    def test_edits_and_deletes_a_single_manual_item(self):
        added = self.store.add_item("week", {"name": "Bananas", "quantity": 6, "unit": "each"})
        item_id = added["items"][-1]["id"]
        edited = self.store.update_item("week", item_id, {"name": "Baby spinach", "quantity": 2, "unit": "bag"})
        self.assertEqual(edited["items"][-1]["name"], "Baby spinach")
        self.store.delete_item("week", item_id)
        self.assertFalse(any(row["id"] == item_id for row in self.store.get_list("week")["items"]))

    def test_persists_generation_candidates_and_excludes_them_from_future_runs(self):
        meal = {"name": "Lemon chicken", "source_url": "https://example.com/lemon-chicken?utm_source=x", "source_title": "Lemon chicken", "ingredients": []}
        self.store.record_generation_candidates([meal])
        keys = self.store.generation_exclusions()
        self.assertIn("lemon chicken", keys["names"])
        self.assertIn("https://example.com/lemon-chicken", keys["urls"])
        row = self.store._load()["generation_candidates"][0]
        self.assertEqual(row["source_domain"], "example.com")
        self.assertFalse(row["selected"])
        self.assertIsNone(row["saved_meal_id"])
        self.assertIn("recipe", row)
        self.assertEqual(self.store._load()["household_preferences"], {"dietary_allergies": ["mushrooms"]})
        with self.assertRaisesRegex(ValueError, "already generated"):
            self.store.record_generation_candidates([{**meal, "source_url": "https://example.com/lemon-chicken"}])

    def test_import_job_history_is_not_silently_truncated(self):
        for index in range(22):
            self.store.create_import_job({"url": f"https://example.com/{index}"})
        self.assertEqual(len(self.store.get_import_jobs()), 22)

    def test_positive_rated_meals_are_stable_preferences(self):
        self.path.write_text(json.dumps({"lists": [{"id": "week", "meals": [{"name": "Lemon chicken"}, {"name": "Beef bowl"}]}], "meal_ratings": {"Lemon chicken": 4, "Beef bowl": 2}}))
        self.assertEqual(self.store.positive_rated_meals(), [{"name": "Lemon chicken", "rating": 4}])

    def test_meal_search_page_uses_a_stable_contract(self):
        from app import paginate_search
        rows = [{"name": "Lemon chicken", "description": "High protein"}, {"name": "Beef bowl", "description": "Low carb"}, {"name": "Lemon fish", "description": "Fast dinner"}]
        page = paginate_search(rows, "lemon", page=1, page_size=1)
        self.assertEqual([row["name"] for row in page["items"]], ["Lemon chicken"])
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["total_pages"], 2)
        self.assertEqual(page["page_size"], 1)

    def test_schedules_a_meal_for_a_date_and_builds_a_date_list(self):
        self.path.write_text(json.dumps({"lists": [{"id": "week", "meals": [{"name": "Lemon chicken", "recipe": {"name": "Lemon chicken", "ingredients": ["500 g chicken breast", "1 lemon"], "steps": ["Cook"]}}]}]}))
        scheduled = self.store.schedule_list_meal("week", 0, "2026-09-10")
        self.assertEqual(scheduled["date"], "2026-09-10")
        self.assertEqual(self.store.get_schedule("2026-09-10")[0]["meal"]["name"], "Lemon chicken")
        listing = self.store.create_list_from_schedule("2026-09-10")
        self.assertEqual(listing["date"], "2026-09-10")
        self.assertEqual(listing["name"], "10 September — planned meals")
        self.assertEqual(listing["meals"][0]["name"], "Lemon chicken")
        self.assertIn("chicken breast", [item["name"].lower() for item in listing["items"]])


if __name__ == "__main__":
    unittest.main()
