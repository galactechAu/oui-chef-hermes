import tempfile
import threading
import unittest
from pathlib import Path
from store import ShoppingStore


class ReleaseRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ShoppingStore(Path(self.temp.name) / 'data.json')

    def test_legacy_add_routes_share_identity_scaling_and_allergy_checks(self):
        store = self.store
        saved = store.save_imported_recipe({'name': 'Rice', 'servings': 2, 'ingredients': ['100 g rice'], 'steps': ['Boil']}, {})
        mid = store.get_meal_catalogue()[0]['id']
        listing = store.add_imported_recipe_to_list(saved['id'])
        self.assertEqual(listing['meals'][0]['canonical_id'], mid)
        self.assertEqual(listing['items'][0]['quantity'], 200)
        listing = store.add_list_meal_to_list(listing['id'], 0, listing['id'])
        self.assertEqual(len(listing['items']), 1)
        self.assertEqual(listing['items'][0]['quantity'], 400)
        self.assertEqual(len(store.get_meal_catalogue()), 1)
        bulk = store.create_list_from_imported_recipes([saved['id']])
        self.assertEqual(bulk['meals'][0]['canonical_id'], mid)
        store.set_dietary_allergies(['rice'])
        for operation in (lambda: store.add_imported_recipe_to_list(saved['id']), lambda: store.add_list_meal_to_list(listing['id'], 0), lambda: store.create_list_from_imported_recipes([saved['id']])):
            before = store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'allergen'): operation()
            self.assertEqual(store.path.read_bytes(), before)
        store.delete_imported_recipe(saved['id'])
        self.assertEqual(store.get_meal_catalogue(), [])
        self.assertEqual(len(store.history()), 3)

    def test_meals_create_list_accepts_canonical_selection_and_target(self):
        import app, json
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        recipe = self.store.save_imported_recipe({'name':'Chicken', 'ingredients':['100 g chicken'], 'steps':['Cook']}, {'type':'text'})
        mid = self.store.get_meal_catalogue()[0]['id']
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(app, 'STORE', self.store):
            body = {'meal_ids':[mid], 'name':'From Meals', 'servings':2}
            req = Request(f'http://127.0.0.1:{server.server_port}/api/meals/create-list', data=json.dumps(body).encode(), headers={'Content-Type':'application/json'})
            try:
                with urlopen(req) as response: status, result = response.status, json.load(response)
            except HTTPError as error:
                status, result = error.code, json.load(error)
            self.assertEqual(status, 201, result)
            self.assertEqual(result['selected_count'], 1)
            self.assertEqual(result['servings'], 2)
            self.assertEqual(self.store.get_list(result['id'])['meals'][0]['canonical_id'], mid)

    def test_concurrent_list_edit_does_not_erase_book_append(self):
        store = self.store
        recipe = store.save_imported_recipe({'name':'Chicken', 'ingredients':['100 g chicken'], 'steps':['Cook']}, {'type':'text'})
        book = store.create_recipe_book('Dinner')
        store.add_meals_to_recipe_book(book['id'], [recipe['id']])
        listing = store.create_empty_list('Shopping')
        loaded, resume, appended = threading.Event(), threading.Event(), threading.Event()
        original_load = store._load
        failures = []
        def controlled_load():
            data = original_load()
            if threading.current_thread().name == 'list-edit':
                loaded.set()
                if not resume.wait(3): raise TimeoutError('test did not release edit')
            return data
        store._load = controlled_load
        def edit():
            try: store.update_servings(listing['id'], 2)
            except Exception as error: failures.append(error)
        def append():
            try: store.create_list_from_recipe_book(book['id'], list_id=listing['id'])
            except Exception as error: failures.append(error)
            finally: appended.set()
        first = threading.Thread(target=edit, name='list-edit')
        second = threading.Thread(target=append, name='book-append')
        first.start()
        self.assertTrue(loaded.wait(2))
        second.start()
        appended.wait(0.3)
        resume.set()
        first.join(3); second.join(3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertFalse(failures, failures)
        saved = store.get_list(listing['id'])
        self.assertEqual(len(saved['meals']), 1, 'Concurrent list edit erased successful Book addition')
        self.assertEqual(saved['servings'], 2)
