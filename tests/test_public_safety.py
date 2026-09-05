import importlib.util
from pathlib import Path
import unittest
import subprocess
import sys
import tempfile


class PublicSafetyTests(unittest.TestCase):
    def test_staged_scan_reads_index_not_working_tree(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'public_safety_scan.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', directory], check=True)
            target = root / 'note.txt'
            target.write_text('.'.join(map(str, (192, 168, 1, 1))))
            subprocess.run(['git', 'add', 'note.txt'], cwd=root, check=True)
            target.write_text('portable source')
            result = subprocess.run([sys.executable, str(script), '--staged'], cwd=root, capture_output=True)
            self.assertNotEqual(result.returncode, 0, 'The staged private blob must be rejected despite clean working file')

    def test_private_address_and_key_patterns(self):
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'public_safety_scan.py'
        spec = importlib.util.spec_from_file_location('public_safety_scan', path)
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        for octets in [(192, 168, 1, 1), (172, 16, 1, 1), (172, 31, 255, 1), (10, 1, 2, 3), (100, 64, 1, 1)]:
            address = '.'.join(map(str, octets))
            self.assertIsNotNone(scanner.PATTERN.search(address), 'Private address must be rejected')
        header = '-----BEGIN ' + 'PRIVATE KEY-----'
        self.assertIsNotNone(scanner.PATTERN.search(header))
        self.assertIsNone(scanner.PATTERN.search('https://example.com/recipes'))
