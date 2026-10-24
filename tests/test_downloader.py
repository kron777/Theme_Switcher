"""Tests for downloader.py — size limits, HTTPS enforcement, cancellation,
network-failure handling. All network calls are mocked."""
import os
import sys
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import config, downloader


class FakeResponse:
    def __init__(self, chunks, headers=None, status=200, raise_exc=None):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status
        self._raise_exc = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")

    def iter_content(self, chunk_size):
        for c in self._chunks:
            yield c


class TestDownloader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.DOWNLOADS_DIR = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refuses_http_without_explicit_allow(self):
        r = downloader.download("http://example.com/theme.zip")
        self.assertFalse(r.ok)
        self.assertIn("http", r.error.lower())

    def test_refuses_unsupported_scheme(self):
        r = downloader.download("ftp://example.com/theme.zip")
        self.assertFalse(r.ok)

    @patch("requests.get")
    def test_successful_download(self, mock_get):
        mock_get.return_value = FakeResponse([b"abc", b"def"], headers={"Content-Length": "6"})
        r = downloader.download("https://example.com/theme.zip")
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.bytes_written, 6)
        with open(r.path, "rb") as f:
            self.assertEqual(f.read(), b"abcdef")

    @patch("requests.get")
    def test_declared_content_length_over_limit_rejected_upfront(self, mock_get):
        orig = config.MAX_DOWNLOAD_BYTES
        config.MAX_DOWNLOAD_BYTES = 10
        try:
            mock_get.return_value = FakeResponse([b"x" * 100], headers={"Content-Length": "1000"})
            r = downloader.download("https://example.com/theme.zip")
            self.assertFalse(r.ok)
            self.assertIn("size limit", r.error)
        finally:
            config.MAX_DOWNLOAD_BYTES = orig

    @patch("requests.get")
    def test_mid_stream_size_limit_aborts_and_cleans_up(self, mock_get):
        orig = config.MAX_DOWNLOAD_BYTES
        config.MAX_DOWNLOAD_BYTES = 5
        try:
            # No Content-Length header, so the cap must be enforced mid-stream.
            mock_get.return_value = FakeResponse([b"xxxxx", b"xxxxx", b"xxxxx"])
            r = downloader.download("https://example.com/theme.zip")
            self.assertFalse(r.ok)
            self.assertFalse(os.listdir(self.tmp))  # partial file must be removed
        finally:
            config.MAX_DOWNLOAD_BYTES = orig

    @patch("requests.get")
    def test_cancellation_removes_partial_file(self, mock_get):
        event = threading.Event()
        event.set()  # cancel immediately
        mock_get.return_value = FakeResponse([b"a", b"b", b"c"])
        r = downloader.download("https://example.com/theme.zip", cancel_event=event)
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "cancelled")
        self.assertFalse(os.listdir(self.tmp))

    @patch("requests.get")
    def test_network_timeout_reported_cleanly(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = downloader.download("https://example.com/theme.zip")
        self.assertFalse(r.ok)
        self.assertIn("timed out", r.error)

    @patch("requests.get")
    def test_http_error_status_reported_cleanly(self, mock_get):
        mock_get.return_value = FakeResponse([], status=404)
        r = downloader.download("https://example.com/missing.zip")
        self.assertFalse(r.ok)

    @patch("requests.get")
    def test_filename_sanitised_from_content_disposition(self, mock_get):
        mock_get.return_value = FakeResponse(
            [b"x"], headers={"Content-Disposition": 'attachment; filename="../../evil.zip"'})
        r = downloader.download("https://example.com/theme.zip")
        self.assertTrue(r.ok)
        # The property that actually matters: no path separators survive,
        # so the file can only land inside DOWNLOADS_DIR, never escape it.
        self.assertNotIn("/", os.path.basename(r.path))
        self.assertNotIn("..", os.path.basename(r.path))
        self.assertEqual(os.path.commonpath([self.tmp, os.path.abspath(r.path)]), self.tmp)


if __name__ == "__main__":
    unittest.main()
