import json
import tempfile
import unittest
from pathlib import Path
from store import ShoppingStore


class BooksBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'store.json'
        self.events = []
        self.store = ShoppingStore(self.path, publish=self.events.append)

    def save(self, name='Chicken', ingredients=None, **extra):
        return self.store.save_imported_recipe({'name': name, 'ingredients': ingredients or ['100 g chicken'], 'steps': ['Cook'], **extra}, {'url': 'https://example.test/recipe', 'label': 'Original'})

    def test_http_contract_paging_canonical_compatibility_and_validation(self):
        import app
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from unittest.mock import patch
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(path, body=None, method=None):
            req = Request(f'http://127.0.0.1:{server.server_port}'+path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'}, method=method)
            try:
                with urlopen(req) as response: return response.status, json.load(response)
            except HTTPError as error: return error.code, json.load(error)
        with patch.object(app, 'STORE', self.store):
            rid = self.save()['id']
            a, b = self.store.create_recipe_book('A'), self.store.create_recipe_book('B')
            status, page = request('/api/recipe-books?page=1&page_size=1')
            self.assertEqual(page['total_pages'], 2)
            self.assertEqual(len(page['books']), 1)
            self.assertEqual(request('/api/recipe-books?page=bad')[0], 400)
            mid = request('/api/meals')[1]['meals'][0]
            self.assertEqual(mid['id'], rid)
            self.assertTrue(mid['canonical_id'].startswith('meal-'))
            self.assertEqual(request('/api/recipe-books/memberships', {'book_ids': [a['id'], b['id']], 'meal_ids': [mid['canonical_id']]})[0], 200)
            self.assertEqual(request('/api/recipe-books/'+a['id']+'/pin', {'pinned': True})[1]['pinned'], True)
            self.assertEqual(request('/api/recipe-books/recent')[1]['books'][0]['id'], a['id'])
            self.assertEqual(request('/api/recipe-books/uncategorised')[1]['total'], 0)
            endpoint = '/api/recipe-books/'+a['id']+'/create-list'
            self.assertEqual(request(endpoint, {'meal_ids': []})[0], 400)
            status, result = request(endpoint, {'all': True, 'servings': 2})
            self.assertEqual(status, 201)
            self.assertEqual(result['selected_count'], 1)
            self.assertEqual(request('/api/recipe-books', [1])[0], 400)
            prefix = '/api/recipe-books/' + a['id']
            for suffix in ('?page=0', '?page_size=101', '?page_size=x', '/recent?limit=x', '/uncategorised?page=x'):
                self.assertEqual(request('/api/recipe-books'+suffix)[0], 400)
            for payload in ({'meal_ids': None}, {'meal_ids': 'bad'}, {'servings': True}, {'servings': 0}, {'servings': 11}, {'all': 'yes'}, {'name': None}, {'list_id': []}):
                before = self.path.read_bytes()
                self.assertEqual(request(endpoint, payload)[0], 400, payload)
                self.assertEqual(before, self.path.read_bytes())
            self.assertEqual(request(prefix+'/pin', {'pinned': 'false'})[0], 400)
            self.assertEqual(request(prefix+'/rename', {'title': None})[0], 400)
            self.assertEqual(request('/api/recipe-books/memberships', {'book_ids': [a['id'], 'absent'], 'meal_ids': [mid['canonical_id']]})[0], 400)
            self.assertEqual(request('/api/recipe-books?q=Chicken')[1]['books'][0]['matching_meal_titles'], ['Chicken'])
            self.assertEqual(request(prefix)[1]['meals'][0]['canonical_id'], mid['canonical_id'])
            history_row = request('/api/history')[1]['meals'][0]
            self.assertEqual(history_row['canonical_id'], mid['canonical_id'])
            self.assertEqual(request('/api/meals')[1]['total'], 1)
            self.assertEqual(request(endpoint, {'all': True})[0], 201)
            library = request('/api/meals')[1]
            self.assertEqual(library['total'], 1)
            self.assertEqual(set(library['meals'][0]['book_ids']), {a['id'], b['id']})
            self.assertEqual(len(request('/api/history')[1]['meals']), 2)
            self.events.clear()
            self.assertEqual(request(prefix+'/use', {})[0], 200)
            self.assertEqual(request(prefix+'/rename', {'title': 'Renamed'})[1]['title'], 'Renamed')
            self.assertEqual(request(prefix+'/meals/'+mid['canonical_id'], method='DELETE')[1]['meal_count'], 0)
            self.assertEqual(request(prefix+'/meals', {'meal_ids': [rid]})[1]['meal_count'], 1)
            self.assertEqual(request(prefix, method='DELETE')[0], 200)
            self.assertEqual(self.events, ['state.changed'] * 5)
            self.assertIsNotNone(self.store.get_imported_recipe(rid))
            self.assertIsNotNone(self.store.get_list(result['id']))
            self.assertEqual(request('/api/import-recipes/'+rid, method='DELETE')[0], 200)
            self.assertEqual(request('/api/meals')[1]['total'], 0)
            self.assertEqual(len(request('/api/history')[1]['meals']), 2)

    def test_invalid_purchase_metadata_rejects_before_writing(self):
        for invalid in (0, -1, 'bad', []):
            rid = self.save('Bad pack', [{'name': 'rice', 'quantity': 100, 'unit': 'g', 'package_size': invalid}])['id']
            book = self.store.create_recipe_book('Bad '+str(invalid))
            self.store.add_meals_to_recipe_book(book['id'], [rid])
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                self.store.create_list_from_recipe_book(book['id'])
            self.assertEqual(before, self.path.read_bytes())

    def test_book_assembly_merges_scales_and_preserves_purchase_metadata(self):
        a = self.save('A', [{'name': ' Chicken ', 'quantity': 200, 'unit': 'g', 'package_size': 500, 'purchase_unit': 'pack'}], servings=2)
        b = self.save('B', [{'name': 'chicken', 'quantity': 100, 'unit': 'g', 'package_size': 500, 'purchase_unit': 'pack'}], servings=4)
        book = self.store.create_recipe_book('Shop')
        self.store.add_meals_to_recipe_book(book['id'], [a['id'], b['id']])
        listing = self.store.create_list_from_recipe_book(book['id'], [], servings=6)
        self.assertEqual(len(listing['items']), 1)
        self.assertEqual(listing['items'][0]['quantity'], 500)
        self.assertEqual(listing['selected_count'], 2)
        from app import presentation
        self.assertEqual(presentation(listing)['items'][0]['display_quantity'], 2)
        item_id = listing['items'][0]['id']
        self.store.toggle_item(listing['id'], item_id, True)
        self.events.clear()
        updated = self.store.create_list_from_recipe_book(book['id'], [b['id'], b['id']], list_id=listing['id'])
        self.assertEqual(updated['selected_count'], 1)
        self.assertEqual(updated['items'][0]['quantity'], 600)
        self.assertEqual(updated['items'][0]['id'], item_id)
        self.assertFalse(updated['items'][0]['checked'])
        self.assertEqual(self.events, ['state.changed'])

    def test_assembly_allergy_failure_is_atomic_for_new_and_existing_targets(self):
        safe = self.save('Safe')['id']
        unsafe = self.save('Legacy', ['mushroom stock'])['id']
        book = self.store.create_recipe_book('Old')
        self.store.add_meals_to_recipe_book(book['id'], [safe, unsafe])
        target = self.store.create_empty_list('Existing')
        for target_id in ['', target['id']]:
            before = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'allergen'):
                self.store.create_list_from_recipe_book(book['id'], [], list_id=target_id)
            self.assertEqual(before, self.path.read_bytes())
        self.store.create_list_from_recipe_book(book['id'], [safe])
        self.store.set_dietary_allergies(['chicken'])
        with self.assertRaisesRegex(ValueError, 'chicken'):
            self.store.create_list_from_recipe_book(book['id'], [safe])

    def test_orphan_memberships_do_not_hide_uncategorised_meals(self):
        rid = self.save()['id']
        data = self.store._load()
        mid = next(row['id'] for row in data['meal_catalog'] if row.get('recipe_id') == rid)
        data['recipe_book_memberships'].append({'book_id': 'missing-book', 'meal_id': mid})
        self.store._save(data)
        self.assertEqual([row['id'] for row in self.store.get_uncategorised_meals()], [mid])
        self.assertEqual(len(self.store._load()['recipe_book_memberships']), 1)

    def test_bulk_membership_is_atomic_idempotent_and_recent(self):
        rid = self.save()['id']
        a, b = self.store.create_recipe_book('A'), self.store.create_recipe_book('B')
        before = self.path.read_bytes()
        with self.assertRaises(KeyError): self.store.add_meals_to_recipe_books([a['id'], 'missing'], [rid])
        self.assertEqual(before, self.path.read_bytes())
        self.store.add_meals_to_recipe_books([a['id'], b['id']], [rid, rid])
        self.store.add_meals_to_recipe_books([a['id'], b['id']], [rid])
        self.assertEqual(len(self.store._load()['recipe_book_memberships']), 2)
        self.assertEqual(len(self.store.get_recent_recipe_books()), 2)
        self.store.remove_meal_from_recipe_book(a['id'], rid)
        self.assertEqual(self.store.get_recipe_book(a['id'])['meal_count'], 0)
        self.assertEqual(self.store.get_uncategorised_meals(), [])
        self.store.rename_recipe_book(b['id'], 'Renamed')
        self.assertEqual(self.store.get_recent_recipe_books()[0]['id'], b['id'])

    def test_book_search_covers_all_meals_and_pin_recent_order(self):
        book = self.store.create_recipe_book('Suppers')
        ids = [self.save(f'Meal {i}', image_url='https://example.test/photo.jpg' if i == 4 else '')['id'] for i in range(5)]
        self.store.add_meals_to_recipe_book(book['id'], ids)
        other = self.store.create_recipe_book('Other')
        found = self.store.get_recipe_books('Meal 4')
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['matching_meal_titles'], ['Meal 4'])
        self.assertEqual(found[0]['cover_url'], 'https://example.test/photo.jpg')
        self.store.pin_recipe_book(book['id'], True)
        self.assertEqual(self.store.get_recipe_books()[0]['id'], book['id'])
        self.assertEqual(self.store.get_recent_recipe_books()[0]['id'], book['id'])
        self.assertEqual(ShoppingStore(self.path).get_recipe_book(book['id'])['pinned'], True)

    def test_reused_list_position_does_not_reuse_deleted_canonical_identity(self):
        target = self.store.create_empty_list('Origin')
        self.store._add_recipe_to_list({'name': 'Old', 'ingredients': ['rice']}, {}, target['id'])
        old = self.store.get_meal_catalogue()[0]['id']
        book = self.store.create_recipe_book('Archive')
        self.store.add_meals_to_recipe_book(book['id'], [old])
        self.store.delete_list_meal(target['id'], 0)
        self.store._add_recipe_to_list({'name': 'New', 'ingredients': ['chicken']}, {}, target['id'])
        new = self.store.get_meal_catalogue()[0]['id']
        self.assertNotEqual(old, new)
        self.assertEqual(self.store.get_recipe_book(book['id'])['missing_meal_ids'], [old])
        self.assertEqual(self.store.history()[0]['canonical_id'], new)

    def test_list_outer_recipe_fields_are_screened_and_preserved(self):
        target = self.store.create_empty_list('Legacy')
        data = self.store._load()
        data['lists'][0]['meals'] = [{'name': 'Rice', 'summary': 'Mushroom sauce', 'method': ['Add mushroom stock'], 'image_url': 'https://example.test/rice.jpg', 'recipe': {'summary': 'Plain rice', 'method': ['Boil'], 'ingredients': ['rice'], 'steps': ['Boil']}}]
        self.store._save(data)
        meal = self.store.get_meal_catalogue()[0]
        book = self.store.create_recipe_book('Rice')
        self.store.add_meals_to_recipe_book(book['id'], [meal['id']])
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'allergen'):
            self.store.create_list_from_recipe_book(book['id'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(meal['image_url'], 'https://example.test/rice.jpg')

    def test_list_origins_migrate_without_losing_references_or_shifting_identity(self):
        original = {'lists': [{'id': 'old', 'meals': [{'name': 'First', 'ingredients': ['chicken'], 'method': ['Cook']}, {'name': 'Second', 'recipe': {'ingredients': ['rice'], 'steps': ['Boil']}}], 'items': []}], 'meal_ratings': {'Second': 5}, 'calendar': [{'id': 'plan', 'source_list_id': 'old', 'source_meal_index': 1}], 'recipe_books': [{'id': 'b', 'title': 'Legacy'}], 'recipe_book_memberships': [{'book_id': 'b', 'meal_id': 'list:old:1'}]}
        self.path.write_text(json.dumps(original))
        meals = self.store.get_meal_catalogue()
        self.assertEqual(len(meals), 2)
        second = next(m for m in meals if m['name'] == 'Second')
        self.assertEqual(second['rating'], 5)
        self.assertEqual(self.store.get_recipe_book('b')['meal_ids'], [second['id']])
        self.store.delete_list_meal('old', 0)
        restored = ShoppingStore(self.path)
        self.assertEqual(restored.get_meal_catalogue()[0]['id'], second['id'])
        self.assertEqual(restored._load()['calendar'], original['calendar'])
        self.assertEqual(restored._load(), restored._load())
        self.assertEqual(restored.get_recipe_book('b')['meals'][0]['recipe']['steps'], ['Boil'])
