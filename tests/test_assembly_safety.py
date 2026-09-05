import json
import tempfile
import threading
import unittest
from pathlib import Path
from store import ShoppingStore


class AssemblySafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.events = []
        self.store = ShoppingStore(Path(temp.name) / 'store.json', self.events.append)

    def recipe(self, ingredients=None, **extra):
        return self.store.save_imported_recipe({'name': 'Rice', 'ingredients': ingredients or ['1 g rice'], 'steps': ['Cook'], **extra}, {})['id']

    def test_invalid_destination_and_overflow_are_atomic(self):
        store = self.store
        rid = self.recipe()
        book = store.create_recipe_book('Book')
        store.add_meals_to_recipe_book(book['id'], [rid])
        target = store.create_empty_list('Target')
        good = store._load()
        cases = [('base_servings', 0), ('base_servings', 'bad'), ('base_servings', True), ('package_size', 'bad'), ('package_size', []), ('quantity', 'bad'), ('quantity', 1e308)]
        for field, value in cases:
            data = json.loads(json.dumps(good))
            listing = next(row for row in data['lists'] if row['id'] == target['id'])
            if field == 'base_servings': listing[field] = value
            else:
                listing['items'] = [{'id': 'old', 'name': 'rice', 'unit': 'g', 'quantity': 1, field: value}]
                listing['servings'] = 10
            store.path.write_text(json.dumps(data))
            before = store.path.read_bytes()
            self.events.clear()
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError): store.create_list_from_recipe_book(book['id'], list_id=target['id'])
                self.assertEqual(store.path.read_bytes(), before)
                self.assertEqual(self.events, [])
        store.path.write_text(json.dumps(good))
        rid = self.recipe([{'name': 'rice', 'quantity': 1e308, 'unit': 'g'}], servings=0.01)
        before = store.path.read_bytes()
        with self.assertRaises(ValueError): store.add_imported_recipe_to_list(rid)
        self.assertEqual(store.path.read_bytes(), before)

    def test_nonfinite_json_never_persists(self):
        self.store.create_empty_list('Keep')
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError): self.recipe([{'name': 'rice', 'quantity': float('inf')}])
        self.assertEqual(before, self.store.path.read_bytes())

    def test_mutation_rejects_unpresentable_output_without_event(self):
        listing = self.store.create_empty_list('Keep')
        before = self.store.path.read_bytes()
        self.events.clear()
        with self.assertRaises(ValueError):
            self.store.add_item(listing['id'], {'name': 'rice', 'quantity': 1, 'package_size': 'bad'})
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(self.events, [])

    def test_aggregation_overflow_rejects_without_event(self):
        rid = self.recipe([{'name': 'rice', 'quantity': 9e307, 'unit': 'g'}], servings=4)
        listing = self.store.create_empty_list('Keep')
        data = self.store._load()
        data['lists'][0]['items'] = [{'id': 'rice', 'name': 'rice', 'quantity': 9e307, 'unit': 'g'}]
        data['lists'][0]['base_servings'] = 1
        data['lists'][0]['servings'] = 1
        data['imported_recipes'][0]['recipe']['servings'] = 1
        self.store._save(data)
        before = self.store.path.read_bytes()
        self.events.clear()
        with self.assertRaises(ValueError): self.store.add_imported_recipe_to_list(rid, listing['id'])
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(self.events, [])

    def test_supported_units_and_mixed_fractions_merge_and_round(self):
        rid = self.recipe(['1 can tomatoes', '1 cans tomatoes', '1 1/2 cups rice', '1/2 cup rice', '1/2 packs lentils'])
        listing = self.store.add_imported_recipe_to_list(rid)
        items = {row['name']: row for row in listing['items']}
        self.assertEqual(len(listing['items']), 3)
        self.assertEqual(items['tomatoes']['quantity'], 2)
        self.assertEqual(items['rice']['quantity'], 2)
        self.assertEqual(items['rice']['unit'], 'cup')
        from app import presentation
        self.assertEqual(next(row for row in presentation(listing)['items'] if row['name'] == 'lentils')['display_quantity'], 1)
        self.assertEqual(self.store._recipe_items(['1 lemon'])[0]['name'], 'lemon')

    def test_all_writer_snapshots_hold_transaction_lock(self):
        store = self.store
        rid = self.recipe()
        listing = store.create_empty_list('Origin')
        draft = store.create_draft('Dinner', [{'name': 'Rice', 'ingredients': [{'name': 'rice', 'quantity': 1}]}])
        job = store.create_generation_job({})
        original = store._load
        def checked_load():
            self.assertTrue(store._lock._is_owned(), 'writer loaded outside transaction lock')
            return original()
        store._load = checked_load
        for operation in (lambda: self.recipe(), lambda: store.rate_meal('Rice', 5), lambda: store.create_draft('new', []), lambda: store.create_list_from_draft(draft['id'], [0]), lambda: store.cancel_generation_job(job['id'])):
            with self.subTest(operation=operation): operation()

    def test_same_title_legacy_meals_and_orphan_ids_are_preserved(self):
        store = self.store
        store.path.write_text(json.dumps({'lists': [{'id': 'origin', 'meals': [{'name': 'Rice', 'ingredients': ['1 cup rice']}, {'name': 'Rice', 'ingredients': ['2 cups rice']}], 'items': []}]}))
        meals = store.get_meal_catalogue()
        self.assertEqual(len({m['id'] for m in meals}), 2)
        book = store.create_recipe_book('Archive')
        store.add_meals_to_recipe_book(book['id'], [meals[0]['id']])
        copy = store.create_list_from_recipe_book(book['id'])
        store.delete_list('origin')
        self.assertEqual(store.get_meal_catalogue(), [])
        self.assertEqual(store.get_list(copy['id'])['meals'][0]['canonical_id'], meals[0]['id'])
        self.assertEqual(store.get_recipe_book(book['id'])['missing_meal_ids'], [meals[0]['id']])
        self.assertEqual({m['id'] for m in store._load()['meal_catalog']}, {m['id'] for m in meals})
