"""Real-browser acceptance tests; opt in with OUI_BROWSER_EXECUTABLE.

Uses a disposable store and loopback HTTP server, never the runtime data file.
Run: OUI_BROWSER_EXECUTABLE=/path/to/chrome python -m unittest discover -s tests -p test_books_browser.py -v
Requires the optional playwright Python package.
"""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('OUI_BROWSER_EXECUTABLE'), 'Optional browser acceptance suite')
class BooksBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        import app
        from store import ShoppingStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ShoppingStore(Path(self.tmp.name) / 'test.json', publish=app.HUB.publish)
        self.patcher = patch.object(app, 'STORE', self.store)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        self.browser = self.pw.chromium.launch(executable_path=os.environ['OUI_BROWSER_EXECUTABLE'], args=['--no-sandbox'])
        self.addCleanup(self.browser.close)
        self.errors = []
        self.page = self.new_page()
        self.ids = []
        for i in range(27):
            row = self.store.save_imported_recipe({'name': f'Test chicken {i:02}', 'ingredients': ['100 g chicken', '1 tsp olive oil'], 'steps': ['Cook for 10 minutes.'], 'servings': 2}, {'label': 'Synthetic browser fixture'})
            self.ids.append(row['id'])

    def tearDown(self):
        print('Browser errors:', self.errors)
        print('Screen:', self.page.locator('body').inner_text()[-2500:])

    def new_page(self, width=1000):
        context = self.browser.new_context(viewport={'width': width, 'height': 900})
        page = context.new_page()
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.on('dialog', lambda dialog: (self.errors.append('Unexpected native dialog: '+dialog.message), dialog.dismiss()))
        page.route('https://**/*', lambda route: route.abort())
        return page

    def open(self, tab, page=None):
        page = page or self.page
        page.goto(self.base, wait_until='domcontentloaded')
        page.locator(f'[data-tab="{tab}"]').click()
        return page

    def test_bulk_picker_manage_quick_toggle_rating_and_shopping(self):
        from playwright.sync_api import expect
        a = self.store.create_recipe_book('Weeknight')
        b = self.store.create_recipe_book('Weekend')
        p = self.open('books')
        p.locator('[data-book-mode="uncategorised"]').click()
        expect(p.locator('#bookGrid [data-meal-id]')).to_have_count(12)
        p.get_by_role('button', name='Select meals', exact=True).click()
        p.locator('#bookGrid input').nth(0).check()
        p.locator('#bookGrid input').nth(1).check()
        p.get_by_role('button', name='Add to Recipe Books', exact=True).click()
        expect(p.locator('#saveMembership')).to_be_disabled()
        p.locator(f'#pickerOptions input[value="{a["id"]}"]').check()
        p.locator(f'#pickerOptions input[value="{b["id"]}"]').check()
        expect(p.locator('#saveMembership')).to_contain_text('2 meals · 2 Recipe Books')
        p.locator('#saveMembership').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        self.assertEqual(self.store.get_recipe_book(a['id'])['meal_count'], 2)
        self.assertEqual(self.store.get_recipe_book(b['id'])['meal_count'], 2)
        p.locator('[data-book-mode="books"]').click()
        p.locator(f'[data-open-book="{a["id"]}"]').click()
        expect(p.locator('#bookGrid [data-meal-id]')).to_have_count(2)
        p.locator('[data-shop-all]').click()
        expect(p.locator('#shoppingCount')).to_have_text('2 recipes will be added.')
        p.locator('#shoppingName').fill('Browser dinner')
        p.locator('#confirmShopping').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        expect(p.locator('#shopping h2')).to_have_text('Browser dinner')
        p.locator('[data-tab="books"]').click()
        p.locator(f'[data-open-book="{a["id"]}"]').click()
        p.locator('[data-shop-all]').click()
        target = self.store.get_lists()[0]['id']
        p.locator('#shoppingTarget').select_option(target)
        expect(p.locator('#shoppingName')).to_be_hidden()
        p.locator('#confirmShopping').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        p.locator('[data-tab="meals"]').click()
        p.locator('#mealsSearch').fill('Test chicken 00')
        canonical = self.store.get_recipe_book(a['id'])['meal_ids'][0]
        card = p.locator(f'#ratings [data-open-meal="{canonical}"]')
        expect(card).to_have_count(1)
        card.click()
        expect(p.locator('#mealSheet')).to_be_visible()
        p.get_by_role('button', name='Rate 4 out of five').click()
        expect(p.get_by_role('button', name='Rate 4 out of five')).to_have_attribute('aria-pressed', 'true')
        expect(p.locator('#recentBooks button')).to_have_count(2)
        quick = p.locator(f'[data-quick-book="{a["id"]}"]')
        quick.click()
        expect(quick).to_have_attribute('aria-pressed', 'false')
        p.locator('#manageMealBooks').click()
        expect(p.locator(f'#pickerOptions input[value="{a["id"]}"]')).not_to_be_checked()
        p.locator(f'#pickerOptions input[value="{b["id"]}"]').uncheck()
        p.locator('#saveMembership').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        self.assertEqual(self.store.get_recipe_book(b['id'])['meal_count'], 1)
        self.assertEqual(self.errors, [])

    def test_search_infinite_pin_rename_delete_and_sse_detail(self):
        from playwright.sync_api import expect
        books = [self.store.create_recipe_book(f'Book {i:02}') for i in range(16)]
        a = books[-1]
        self.store.add_meals_to_recipe_book(a['id'], self.ids[:1])
        p = self.open('books')
        expect(p.locator('[data-open-book]')).to_have_count(12)
        p.locator('#booksSentinel').scroll_into_view_if_needed()
        expect(p.locator('[data-open-book]')).to_have_count(16)
        self.assertEqual(len(set(p.locator('[data-open-book]').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.openBook)'))), 16)
        p.locator('#bookSearch').fill('Test chicken 00')
        expect(p.locator('[data-open-book]')).to_have_count(1)
        expect(p.locator('#bookGrid')).to_contain_text('Matching: Test chicken 00')
        p.locator('[data-open-book]').click()
        expect(p.locator('#bookGrid [data-meal-id]')).to_have_count(1)
        other = self.new_page(390)
        self.open('books', other)
        other.locator(f'[data-open-book="{a["id"]}"]').click()
        expect(other.locator('#bookGrid [data-meal-id]')).to_have_count(1)
        # Mutation through the second browser's API context, delivered via real SSE.
        response = other.request.post(self.base+f'/api/recipe-books/{a["id"]}/meals', data={'meal_ids': self.ids[1:2]})
        self.assertTrue(response.ok)
        expect(p.locator('#bookGrid [data-meal-id]')).to_have_count(2)
        p.locator('[data-pin-book]').click()
        expect(other.locator('[data-pin-book]')).to_have_attribute('aria-pressed', 'true')
        p.locator('[data-menu-book]').click()
        p.locator('#renameBookTitle').fill('Family favourites')
        p.get_by_role('button', name='Save name').click()
        expect(other.locator('.detail-hero h2')).to_have_text('Family favourites')
        p.locator('[data-menu-book]').click()
        p.locator('#deleteBookButton').click()
        expect(p.locator('#mealSheet')).to_contain_text('Meals stay in your library and shopping-list history is unchanged.')
        p.locator('#confirmAction').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        self.assertIsNone(self.store.get_recipe_book(a['id']))
        self.assertEqual(len(self.store.get_imported_recipes()), 27)
        self.assertEqual(self.errors, [])

    def test_create_inline_keyboard_picker_and_polling_fallback(self):
        from playwright.sync_api import expect
        p = self.open('books')
        p.locator('#bookTitle').fill('Family cooking')
        p.locator('#bookTitle').press('Enter')
        expect(p.locator('[data-open-book]')).to_have_count(1)
        book = self.store.get_recipe_books()[0]
        p.locator('[data-book-mode="uncategorised"]').click()
        p.get_by_role('button', name='Select meals', exact=True).click()
        p.locator('#bookGrid input').first.check()
        p.get_by_role('button', name='Add to Recipe Books', exact=True).click()
        checkbox = p.locator('#pickerOptions input').first
        checkbox.focus()
        p.keyboard.press('Space')
        expect(checkbox).to_be_checked()
        expect(checkbox).to_be_focused()
        p.locator('#pickerTitle').fill('Lunch ideas')
        p.locator('#pickerCreate button').click()
        expect(p.locator('#pickerOptions input:checked')).to_have_count(2)
        p.locator('#saveMembership').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        p.locator('[data-book-mode="books"]').click()
        p.locator(f'[data-open-book="{book["id"]}"]').click()
        expect(p.locator('#bookGrid [data-meal-id]')).to_have_count(1)
        p.evaluate('realtime.close();window.EventSource=undefined;startRealtime()')
        self.store.add_meals_to_recipe_book(book['id'], self.ids[1:2])
        expect(p.locator('#bookGrid [data-meal-id]')).to_have_count(2, timeout=8000)
        self.assertEqual(self.errors, [])

    def test_meals_shopping_new_existing_and_ownership_removal(self):
        from playwright.sync_api import expect
        p = self.open('meals')
        p.locator('#ratings [data-open-meal]').first.click()
        p.locator('#mealShopping').click()
        expect(p.locator('#recipeScope')).to_have_count(0)
        expect(p.locator('#shoppingCount')).to_have_text('1 recipe will be added.')
        p.locator('#shoppingName').fill('From Meals')
        p.locator('#confirmShopping').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        expect(p.locator('#shopping h2')).to_have_text('From Meals')
        target = self.store.get_lists()[0]['id']
        p.locator('[data-tab="meals"]').click()
        p.locator('#ratings [data-open-meal]').first.click()
        p.locator('#mealShopping').click()
        p.locator('#shoppingTarget').select_option(target)
        p.locator('#confirmShopping').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        p.locator('[data-tab="meals"]').click()
        p.locator('#mealsSearch').fill('Test chicken 25')
        expect(p.locator('#ratings [data-open-meal]')).to_have_count(1)
        p.locator('#ratings [data-open-meal]').click()
        p.locator('#deleteCanonicalMeal').click()
        p.locator('#confirmAction').click()
        expect(p.locator('#mealSheet')).to_be_hidden()
        expect(p.locator('#ratings [data-open-meal]')).to_have_count(0)
        self.assertIsNotNone(self.store.get_list(target))
        self.assertEqual(self.errors, [])

    def test_mobile_fallback_focus_and_meal_infinite_search(self):
        from playwright.sync_api import expect
        p = self.page
        p.set_viewport_size({'width': 390, 'height': 844})
        self.open('meals')
        expect(p.locator('#ratings [data-meal-id]')).to_have_count(12)
        self.assertEqual(p.locator('#ratings').evaluate('(n)=>getComputedStyle(n).gridTemplateColumns.split(" ").length'), 2)
        expect(p.locator('#ratings img')).to_have_count(0)
        first = p.locator('#ratings [data-open-meal]').first
        first.focus()
        p.keyboard.press('Enter')
        expect(p.locator('#closeMealSheet')).to_be_focused()
        p.keyboard.press('Shift+Tab')
        expect(p.locator('#deleteCanonicalMeal')).to_be_focused()
        p.keyboard.press('Tab')
        expect(p.locator('#closeMealSheet')).to_be_focused()
        p.keyboard.press('Escape')
        expect(first).to_be_focused()
        self.assertFalse(p.evaluate('document.documentElement.scrollWidth > innerWidth'))
        p.locator('#mealsSentinel').scroll_into_view_if_needed()
        expect(p.locator('#ratings [data-meal-id]')).to_have_count(24)
        p.locator('#mealsSentinel').scroll_into_view_if_needed()
        expect(p.locator('#ratings [data-meal-id]')).to_have_count(27)
        p.locator('#mealsSearch').fill('Test chicken 02')
        expect(p.locator('#ratings [data-meal-id]')).to_have_count(1)
        self.assertEqual(self.errors, [])
