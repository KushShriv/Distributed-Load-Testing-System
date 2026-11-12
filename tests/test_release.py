import hashlib
import pathlib
import subprocess
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

class ReleaseTests(unittest.TestCase):
    def test_preview_is_reproducible_and_contains_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ['python3', str(ROOT / 'build_release.py'), '--output', directory]
            subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
            archive = pathlib.Path(directory) / 'load-tester-0.1.0-preview.tar.gz'
            checksum = pathlib.Path(directory) / 'SHA256SUMS'
            first = archive.read_bytes()
            self.assertIn(hashlib.sha256(first).hexdigest(), checksum.read_text())
            with tarfile.open(archive) as package:
                self.assertIn('load-tester-0.1.0-preview/start.sh', package.getnames())
                self.assertTrue(package.getmember('load-tester-0.1.0-preview/start.sh').mode & 0o111)
                self.assertFalse(any('__pycache__' in name or '.venv' in name for name in package.getnames()))
            subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
            self.assertEqual(first, archive.read_bytes())

if __name__ == '__main__': unittest.main()
