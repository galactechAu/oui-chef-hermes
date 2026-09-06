import tempfile
import unittest
from pathlib import Path
import importlib.util


class JavaScriptGateTests(unittest.TestCase):
    def test_invalid_javascript_is_rejected_and_json_script_is_ignored(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'check_javascript.py'
        self.assertTrue(script.exists(), 'JavaScript syntax gate is missing')
        spec = importlib.util.spec_from_file_location('check_javascript', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / 'index.html'
            page.write_text('<script>const broken = ;</script>')
            self.assertTrue(module.check_tree(root))
            page.write_text('<script type="application/ld+json">{"name":"fixture"}</script><script>const valid = 1;</script>')
            self.assertEqual(module.check_tree(root), [])
            (root / 'app.js').write_text('function invalid( {')
            self.assertTrue(module.check_tree(root))
