import unittest
from pathlib import Path


class ShoppingUiTests(unittest.TestCase):
    def test_redesign_has_food_app_shell_and_recipe_card_classes(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        self.assertIn('class="app-shell"', html)
        self.assertIn('.recipe-card{', html)
        self.assertIn('class="bottom-nav"', html)
        self.assertIn('aria-label="Main navigation"', html)
        self.assertIn('position:fixed;left:0;right:0;bottom:0', html)
        self.assertIn('env(safe-area-inset-bottom)', html)
        self.assertIn('<title>Oui, Chef</title>', html)
        self.assertIn('>Oui, Chef</div>', html)
        self.assertNotIn('>Meal Planner</div>', html)

    def test_aisles_are_native_collapsible_sections_that_auto_close_when_complete(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        self.assertIn('<details class="aisle"', html)
        self.assertIn('items.some(i=>!i.checked)', html)
    def test_shopping_rows_use_stable_mobile_action_columns(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        self.assertIn('.item{display:grid;grid-template-columns:28px minmax(0,1fr) 48px 48px', html)
        self.assertIn('.item>span{min-width:0;line-height:1.25}', html)

    def test_import_has_a_single_link_mode_with_url_classification_preserved(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        for marker in ('data-import-mode="link"', '>Link</button>', 'data-import-mode="text"', 'data-import-mode="image"', 'data-mode="link"', 'id="importSearch"', 'id="importSentinel"', 'loadImportJobs(page=1,append=false)'):
            self.assertIn(marker, html)
        self.assertNotIn('data-import-mode="video"', html)
        self.assertNotIn('data-import-mode="web"', html)
        self.assertIn('.import-mode-field[hidden]{display:none!important}', html)

    def test_libraries_use_infinite_scroll_and_lucide_icon_markup(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        for marker in ('id="mealsSentinel"', 'id="importSentinel"', 'IntersectionObserver', 'data-lucide="shopping-cart"', 'data-lucide="trash-2"'):
            self.assertIn(marker, html)
        self.assertNotIn('id="mealsPager"', html)
        self.assertNotIn('id="importPager"', html)

    def test_recipe_books_ui_has_accessible_detail_selection_and_existing_list_export(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        for marker in ('id="books"', 'id="booksLibrary"', 'id="bookSelectAll"', 'class="checkbox book-pick"', 'Add selected to shopping list', 'Use existing shopping list'):
            self.assertIn(marker, html)

    def test_bottom_navigation_uses_cart_for_lists(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        self.assertIn('data-tab="list"><span class="nav-icon" aria-hidden="true"><i data-lucide="shopping-cart"></i></span>', html)

    def test_lists_has_calendar_hub_and_new_list_creation(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        for marker in ('id="listModes"', 'data-list-mode="calendar"', 'id="calendarView"', 'id="calendarGrid"', 'id="newListName"', 'id="createList"', "'/api/lists','POST'", 'Create shopping list for this day'):
            self.assertIn(marker, html)
        self.assertNotIn('id="calendarSchedule"', html)

    def test_recipe_import_tab_has_url_text_image_review_and_save_controls(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        for marker in ('data-tab="import"', 'id="importUrl"', 'id="importText"', 'id="importImage"', 'Preview recipe', '＋ Add meal', 'import-jobs', 'Import progress', 'Job trace', '<progress', '＋ Ingredients', '/api/meals', 'Original source', 'Delete list', 'Remove meal', 'Remove import job', "new EventSource('/api/events')", 'refreshRealtime'):
            self.assertIn(marker, html)
        self.assertIn("let endpoint=m.kind==='imported'?'/api/import-recipes/'+m.id+'/add-to-list'", html)
        self.assertIn("if(current&&d.lists.some(x=>x.id===current.id))return openList(current.id)", html)
        self.assertNotIn('onclick="addMealToList(\'+endpoint+', html)


if __name__ == "__main__":
    unittest.main()
