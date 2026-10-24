"""Tests for installer.py — theme component detection and safe install paths."""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import config, installer


def _write_index_theme(d, extra_section=""):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.theme"), "w") as f:
        f.write("[Desktop Entry]\nType=X-GNOME-Metatheme\nName=Test\n" + extra_section)


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detects_gtk_theme(self):
        root = os.path.join(self.tmp, "MyTheme")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "gtk-4.0"))
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertIn("gtk-theme", kinds)

    def test_gtk_and_shell_in_same_dir_merged_into_one_component(self):
        # A theme folder providing both must become ONE component, not two
        # — installing it twice under two different names is both wasteful
        # and confusing (this was a real bug: "TailyaGTK-main" and
        # "TailyaGTK-main-themeswitch-2" both installed from the same dir).
        root = os.path.join(self.tmp, "MyTheme")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "gtk-4.0"))
        os.makedirs(os.path.join(root, "gnome-shell"))
        det = installer.detect_components(self.tmp)
        self.assertEqual(len(det.components), 1)
        self.assertEqual(det.components[0].kind, "gtk-shell-theme")

    def test_gtk_only_without_shell_stays_separate_kind(self):
        root = os.path.join(self.tmp, "MyTheme")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "gtk-4.0"))
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertEqual(kinds, ["gtk-theme"])

    def test_detects_icon_theme_size_first_layout(self):
        root = os.path.join(self.tmp, "MyIcons")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "scalable"))
        os.makedirs(os.path.join(root, "16x16"))
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertIn("icon-theme", kinds)

    def test_detects_icon_theme_context_first_layout(self):
        # Real-world layout (e.g. Suru++): context dirs at the top level,
        # each with its own size subdirectories inside.
        root = os.path.join(self.tmp, "ContextIcons")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "apps"))
        os.makedirs(os.path.join(root, "actions"))
        os.makedirs(os.path.join(root, "mimetypes"))
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertIn("icon-theme", kinds)
        # And individual icon files inside it must NOT be misdetected as
        # standalone wallpapers.
        wallpapers = [c for c in det.components if c.kind == "wallpaper"]
        self.assertEqual(wallpapers, [])

    def test_detects_cursor_theme(self):
        root = os.path.join(self.tmp, "MyCursors")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "cursors"))
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertIn("cursor-theme", kinds)

    def test_detects_wallpaper_image(self):
        img = os.path.join(self.tmp, "wallpaper.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n" + b"0" * 20_000)  # over the tiny-file skip threshold
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertIn("wallpaper", kinds)

    def test_tiny_images_skipped_as_incidental(self):
        img = os.path.join(self.tmp, "badge.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG" + b"0" * 100)
        det = installer.detect_components(self.tmp)
        self.assertEqual(det.components, [])

    def test_empty_archive_reports_nothing_found(self):
        det = installer.detect_components(self.tmp)
        self.assertEqual(det.components, [])
        self.assertTrue(det.notes)

    def test_nested_github_zipball_layout(self):
        # GitHub zipballs wrap everything in "<repo>-<branch>/"
        root = os.path.join(self.tmp, "cool-theme-main", "src", "CoolTheme")
        _write_index_theme(root)
        os.makedirs(os.path.join(root, "gtk-3.0"))
        det = installer.detect_components(self.tmp)
        kinds = [c.kind for c in det.components]
        self.assertIn("gtk-theme", kinds)


class TestUniqueDest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_never_overwrites_existing_name(self):
        existing = os.path.join(self.tmp, "SomeTheme")
        os.makedirs(existing)
        with open(os.path.join(existing, "marker.txt"), "w") as f:
            f.write("pre-existing, do not touch")

        dest = installer._unique_dest(self.tmp, "SomeTheme")
        self.assertNotEqual(dest, existing)
        # The pre-existing directory and its content must be untouched.
        self.assertTrue(os.path.isfile(os.path.join(existing, "marker.txt")))


if __name__ == "__main__":
    unittest.main()
