import unittest
from pathlib import Path
from unittest.mock import patch


class RecipeImporterTests(unittest.TestCase):
    def test_classifies_supported_sources(self):
        from recipe_importer import classify_source
        self.assertEqual(classify_source(url="https://www.youtube.com/watch?v=abc"), "youtube")
        self.assertEqual(classify_source(url="https://www.instagram.com/reel/abc/"), "instagram")
        self.assertEqual(classify_source(url="https://example.com/recipe"), "webpage")
        self.assertEqual(classify_source(text="2 eggs\nCook gently"), "text")
        self.assertEqual(classify_source(image_data="data:image/png;base64,AA=="), "image")

    def test_rejects_non_public_recipe_urls(self):
        from recipe_importer import validate_public_url
        for url in ("file:///etc/passwd", "http://127.0.0.1/", "http://localhost/", "http://user:pass@example.com/"):
            with self.assertRaises(ValueError):
                validate_public_url(url)

    def test_redirect_validation_checks_each_target_before_following(self):
        from recipe_importer import validate_public_redirects
        with patch("recipe_importer.validate_public_url", side_effect=lambda value: value) as valid:
            class Response:
                def geturl(self): return "https://recipes.example/final"
                def close(self): pass
            class Opener:
                def open(self, request, timeout): return Response()
            with patch("recipe_importer.build_opener", return_value=Opener()):
                self.assertEqual(validate_public_redirects("https://recipes.example/start"), "https://recipes.example/final")
        self.assertGreaterEqual(valid.call_count, 2)

    def test_fetch_public_source_text_is_bounded(self):
        from recipe_importer import fetch_public_source_text
        class Response:
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def read(self, amount): return b"<h1>Chicken</h1>"
            def close(self): pass
        class Opener:
            def open(self, request, timeout): return Response()
        with patch("recipe_importer.validate_public_redirects", return_value="https://example.com/r"), patch("recipe_importer.build_opener", return_value=Opener()):
            self.assertIn("Chicken", fetch_public_source_text("https://example.com/r"))

    def test_image_payload_is_bounded_and_decoded(self):
        from recipe_importer import decode_image_data
        mime, raw = decode_image_data("data:image/png;base64,aGVsbG8=")
        self.assertEqual(mime, "image/png")
        self.assertEqual(raw, b"hello")

    def test_transcribes_downloaded_public_video_audio(self):
        from app import public_video_transcript
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        def run(command, **_kwargs):
            if command[0] == "yt-dlp":
                Path(command[command.index("-o") + 1].replace("%(ext)s", "m4a")).write_bytes(b"audio")
            elif command[0] == "whisper-cli":
                Path(command[command.index("-of") + 1] + ".txt").write_text("Dice the onion, then simmer chicken for 10 minutes.")
            return Result()
        with patch("app.subprocess.run", side_effect=run):
            transcript = public_video_transcript("https://www.instagram.com/reel/public/")
        self.assertIn("simmer chicken", transcript)

    def test_recipe_prompt_requires_preparation_aware_steps(self):
        from recipe_importer import recipe_import_prompt
        prompt = recipe_import_prompt("text", "Dice an onion and cook it")
        self.assertIn("before it is used", prompt)
        self.assertIn("mushroom", prompt.lower())
        self.assertIn("ingredients", prompt)


if __name__ == "__main__":
    unittest.main()
