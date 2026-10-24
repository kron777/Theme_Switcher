"""
Integration test: a poisoned archive must be blocked by the full
pipeline (download -> extract -> scan), not just by scanner.py in
isolation, and nothing from it should be left on disk afterward.
"""
import os
import sys
import shutil
import tempfile
import zipfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import config, pipeline


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data
        self.headers = {"Content-Length": str(len(data))}
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self._data


class TestPipelineScanIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.DOWNLOADS_DIR = os.path.join(self.tmp, "downloads")
        config.EXTRACT_DIR = os.path.join(self.tmp, "extracted")
        os.makedirs(config.DOWNLOADS_DIR)
        os.makedirs(config.EXTRACT_DIR)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_zip_bytes(self, entries: dict) -> bytes:
        path = os.path.join(self.tmp, "src.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        with open(path, "rb") as f:
            return f.read()

    @patch("requests.get")
    def test_poisoned_archive_blocked_end_to_end(self, mock_get):
        data = self._make_zip_bytes({
            "index.theme": "[Desktop Entry]\n",
            "gtk-3.0/gtk.css": "/* fine */",
            "gtk-3.0/payload.sh": "#!/bin/bash\nrm -rf ~\n",  # the trojan
        })
        mock_get.return_value = FakeResponse(data)

        p = pipeline.Pipeline(log_cb=lambda *_: None)
        extracted, err = p.download_and_extract("https://example.com/evil-theme.zip")

        self.assertIsNone(extracted)
        self.assertIsNotNone(err)
        self.assertIn("payload.sh", err)
        # Nothing should survive on disk — not the extraction, not the download.
        self.assertEqual(os.listdir(config.EXTRACT_DIR), [])
        self.assertEqual(os.listdir(config.DOWNLOADS_DIR), [])

    @patch("requests.get")
    def test_clean_archive_passes_through(self, mock_get):
        data = self._make_zip_bytes({
            "index.theme": "[Desktop Entry]\n",
            "gtk-3.0/gtk.css": "/* fine */",
            "gtk-3.0/settings.ini": "[Settings]\n",
        })
        mock_get.return_value = FakeResponse(data)

        p = pipeline.Pipeline(log_cb=lambda *_: None)
        extracted, err = p.download_and_extract("https://example.com/good-theme.zip")

        self.assertIsNone(err)
        self.assertIsNotNone(extracted)
        self.assertTrue(os.path.isfile(os.path.join(extracted, "index.theme")))


if __name__ == "__main__":
    unittest.main()
