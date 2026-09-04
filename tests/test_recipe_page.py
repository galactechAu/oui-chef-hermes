import unittest


class RecipePageTests(unittest.TestCase):
    def test_recipe_page_only_renders_timers_for_timed_steps_and_marks_steps_complete(self):
        from recipe_page import render_recipe_page
        page = render_recipe_page({
            "name": "Test tray bake",
            "recipe": {
                "summary": "Fast dinner",
                "ingredients": ["Chicken", "Lemon"],
                "steps": ["Season the chicken.", "Roast for 18 minutes.", "Serve immediately."],
            },
        }).decode()
        self.assertIn('class="recipe-app"', page)
        self.assertEqual(page.count('class="timer"'), 1)
        self.assertIn('value="18"', page)
        self.assertNotIn('value="5"', page)
        self.assertEqual(page.count('class="complete-step"'), 3)
        self.assertIn('Mark complete', page)
        self.assertEqual(page.count('class="toggle-step"'), 3)
        self.assertIn('.step-card.minimised', page)
        self.assertIn('Show step details', page)

    def test_recipe_page_displays_scraped_info_pills(self):
        from recipe_page import render_recipe_page
        page = render_recipe_page({"name": "Test", "recipe": {"complexity": "Easy", "prep_time_min": 10, "cook_time_min": 25, "health_rating": 5, "steps": []}}).decode()
        self.assertIn('Easy', page)
        self.assertIn('10 min prep', page)
        self.assertIn('25 min cook', page)
        self.assertIn('Health fit 5/5', page)
    def test_timer_alarm_repeats_until_cancelled(self):
        from recipe_page import render_recipe_page
        rendered = render_recipe_page({"name": "Test", "recipe": {"steps": ["Cook for 1 minute"]}}).decode()
        self.assertIn("state.alarm=setInterval(beep,2500)", rendered)
        self.assertIn("if(state.alarm)clearInterval(state.alarm)", rendered)

    def test_recipe_page_uses_oui_chef_branding(self):
        from recipe_page import render_recipe_page
        page = render_recipe_page({"name": "Test", "recipe": {"steps": []}}).decode()
        self.assertIn('<title>Test · Oui, Chef</title>', page)
        self.assertIn('Oui, Chef', page)

    def test_import_recipe_without_bridge_has_actionable_error(self):
        import app
        original = app.BRIDGE_URL
        app.BRIDGE_URL = None
        try:
            with self.assertRaisesRegex(ValueError, "HERMES_BRIDGE_URL"):
                app.ask_import_recipe({"text": "Chicken, salt, cook it."})
        finally:
            app.BRIDGE_URL = original

    def test_health_payload_never_returns_bridge_url(self):
        import app
        payload = app.health_payload()
        self.assertEqual(payload["service"], "oui-chef")
        self.assertIn("hermes_bridge_configured", payload)
        self.assertNotIn("bridge", payload)

    def test_missing_hermes_bridge_has_actionable_error(self):
        import app
        original = app.BRIDGE_URL
        app.BRIDGE_URL = None
        try:
            with self.assertRaisesRegex(ValueError, "HERMES_BRIDGE_URL"):
                app.ask_hermes(1, "", "Easy")
        finally:
            app.BRIDGE_URL = original

    def test_scrape_prompt_requests_recipe_information_pills(self):
        from app import import_recipe_prompt
        prompt = import_recipe_prompt("https://example.com/recipe")
        self.assertIn('prep_time_min', prompt)
        self.assertIn('cook_time_min', prompt)
        self.assertIn('health_rating', prompt)
        self.assertIn('preparation action', prompt)
        self.assertIn('chop', prompt)
        self.assertIn('before it is used', prompt)

    def test_timer_suggestion_understands_hour_ranges(self):
        from recipe_page import suggested_minutes
        self.assertEqual(suggested_minutes("Simmer for 1½–2 hours."), 120)


if __name__ == "__main__":
    unittest.main()
