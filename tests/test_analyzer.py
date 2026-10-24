"""Tests for analyzer.py URL parsing/classification. Network calls mocked."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import analyzer


class TestURLClassification(unittest.TestCase):
    def test_direct_zip_link(self):
        r = analyzer.analyze("https://example.com/themes/Nice-Theme.zip")
        self.assertTrue(r.ok)
        self.assertEqual(len(r.candidates), 1)
        self.assertEqual(r.candidates[0].source, "direct-link")

    def test_direct_tar_xz_link(self):
        r = analyzer.analyze("https://example.com/Nice-Theme.tar.xz")
        self.assertTrue(r.ok)
        self.assertEqual(r.candidates[0].filename_hint, "Nice-Theme.tar.xz")

    def test_rejects_non_http_scheme(self):
        r = analyzer.analyze("ftp://example.com/theme.zip")
        self.assertFalse(r.ok)

    def test_rejects_garbage_input(self):
        r = analyzer.analyze("not a url at all")
        self.assertFalse(r.ok)

    def test_github_release_asset_direct(self):
        url = "https://github.com/owner/repo/releases/download/v1.0/theme.tar.gz"
        r = analyzer.analyze(url)
        self.assertTrue(r.ok)
        self.assertEqual(r.candidates[0].source, "direct-link")

    def test_github_release_asset_unsupported_extension(self):
        url = "https://github.com/owner/repo/releases/download/v1.0/notes.pdf"
        r = analyzer.analyze(url)
        self.assertFalse(r.ok)

    @patch("themeswitch.downloader.fetch_text")
    def test_github_repo_root_uses_api(self, mock_fetch):
        mock_fetch.return_value = (
            '{"name": "repo", "default_branch": "main"}'
        )
        r = analyzer.analyze("https://github.com/owner/repo")
        self.assertTrue(r.ok)
        self.assertTrue(any(c.source == "github-zipball" for c in r.candidates))

    @patch("themeswitch.downloader.fetch_text")
    def test_github_repo_not_found(self, mock_fetch):
        mock_fetch.return_value = '{"message": "Not Found"}'
        r = analyzer.analyze("https://github.com/owner/doesnotexist")
        self.assertFalse(r.ok)

    @patch("themeswitch.downloader.fetch_text")
    def test_github_api_unreachable(self, mock_fetch):
        mock_fetch.return_value = None
        r = analyzer.analyze("https://github.com/owner/repo")
        self.assertFalse(r.ok)
        self.assertIn("couldn't reach", r.message)

    @patch("themeswitch.downloader.fetch_text")
    def test_generic_page_with_archive_links(self, mock_fetch):
        mock_fetch.return_value = (
            '<html><body><a href="/dl/theme-v2.zip">download</a></body></html>'
        )
        r = analyzer.analyze("https://example.com/theme-page")
        self.assertTrue(r.ok)
        self.assertEqual(r.candidates[0].url, "https://example.com/dl/theme-v2.zip")

    @patch("themeswitch.downloader.fetch_text")
    def test_generic_page_with_no_archive_links_degrades_honestly(self, mock_fetch):
        mock_fetch.return_value = "<html><body>just some text, no downloads</body></html>"
        r = analyzer.analyze("https://example.com/no-theme-here")
        self.assertFalse(r.ok)
        self.assertIn("paste that instead", r.message)

    @patch("themeswitch.downloader.fetch_text")
    def test_generic_page_unreachable(self, mock_fetch):
        mock_fetch.return_value = None
        r = analyzer.analyze("https://example.com/down")
        self.assertFalse(r.ok)
        self.assertIn("couldn't reach", r.message)


if __name__ == "__main__":
    unittest.main()
