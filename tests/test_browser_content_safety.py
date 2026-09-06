"""Browser regression for untrusted recipe text and broken image fallbacks."""
import os
import unittest
import test_books_browser as fixtures


@unittest.skipUnless(os.environ.get('OUI_BROWSER_EXECUTABLE'), 'Optional browser acceptance suite')
class BrowserContentSafetyTests(unittest.TestCase):
    setUp = fixtures.BooksBrowserTests.setUp
    new_page = fixtures.BooksBrowserTests.new_page
    open = fixtures.BooksBrowserTests.open

    def test_book_and_meal_text_cannot_execute_markup(self):
        from playwright.sync_api import expect
        title = '<img src=x onerror="window.contentExecuted=true">'
        book = self.store.create_recipe_book(title)
        recipe = self.store.save_imported_recipe({'name':title, 'ingredients':['100 g rice'], 'steps':['Cook'], 'image_url':'https://example.test/missing.jpg'}, {'type':'text'})
        self.store.add_meals_to_recipe_book(book['id'], [recipe['id']])
        page = self.open('books')
        expect(page.locator('[data-open-book]')).to_have_count(1)
        expect(page.locator('[data-open-book]')).to_contain_text(title)
        self.assertFalse(page.evaluate('Boolean(window.contentExecuted)'))
        page.locator('[data-open-book]').click()
        expect(page.locator('#bookGrid [data-open-meal]')).to_have_count(1)
        page.locator('#bookGrid [data-open-meal]').click()
        expect(page.locator('#mealSheetTitle')).to_have_text(title)
        self.assertFalse(page.evaluate('Boolean(window.contentExecuted)'))
        self.assertEqual(self.errors, [])
