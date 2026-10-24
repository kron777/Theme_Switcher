"""Tests for archive.py — path traversal, symlinks, size limits."""
import os
import sys
import tarfile
import tempfile
import zipfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import archive, config


class TestZipSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp, "dest")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_zip(self, entries: dict) -> str:
        path = os.path.join(self.tmp, "a.zip")
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return path

    def test_normal_extraction_works(self):
        z = self._make_zip({"theme/index.theme": "[Desktop Entry]\n", "theme/gtk-4.0/gtk.css": "/* x */"})
        res = archive.extract(z, self.dest)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "theme", "index.theme")))

    def test_path_traversal_rejected(self):
        z = self._make_zip({"../../etc/passwd": "pwned"})
        res = archive.extract(z, self.dest)
        self.assertFalse(res.ok)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.dest), "..", "etc", "passwd")))

    def test_absolute_path_rejected(self):
        z = self._make_zip({"/etc/passwd": "pwned"})
        res = archive.extract(z, self.dest)
        self.assertFalse(res.ok)

    def test_nested_traversal_rejected(self):
        z = self._make_zip({"theme/../../../tmp/evil.txt": "pwned"})
        res = archive.extract(z, self.dest)
        self.assertFalse(res.ok)

    def test_symlink_with_absolute_target_rejected(self):
        path = os.path.join(self.tmp, "sym.zip")
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("evil-link")
            info.external_attr = (0o120777 << 16)  # symlink mode bits
            zf.writestr(info, "/etc/passwd")
        res = archive.extract(path, self.dest)
        self.assertFalse(res.ok)

    def test_symlink_escaping_extraction_dir_rejected(self):
        path = os.path.join(self.tmp, "sym2.zip")
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("theme/evil-link")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "../../../../etc/passwd")
        res = archive.extract(path, self.dest)
        self.assertFalse(res.ok)

    def test_safe_internal_symlink_allowed(self):
        # This is the real-world case: icon themes commonly symlink a
        # shared subdirectory between variants (e.g. Suru++'s Light and
        # Dark variants share an "actions" icon set via a symlink).
        path = os.path.join(self.tmp, "sym3.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("theme/Dark/index.theme", "[Icon Theme]\n")
            zf.writestr("theme/Light/actions/icon.svg", "<svg/>")
            info = zipfile.ZipInfo("theme/Dark/actions")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "../Light/actions")
        res = archive.extract(path, self.dest)
        self.assertTrue(res.ok, res.error)
        link_path = os.path.join(self.dest, "theme", "Dark", "actions")
        self.assertTrue(os.path.islink(link_path))
        # And it must actually resolve to the real shared file.
        resolved_file = os.path.join(link_path, "icon.svg")
        self.assertTrue(os.path.isfile(resolved_file))

    def test_entry_count_limit(self):
        orig = config.MAX_ARCHIVE_ENTRIES
        config.MAX_ARCHIVE_ENTRIES = 3
        try:
            z = self._make_zip({f"f{i}.txt": "x" for i in range(10)})
            res = archive.extract(z, self.dest)
            self.assertFalse(res.ok)
        finally:
            config.MAX_ARCHIVE_ENTRIES = orig

    def test_extraction_size_limit(self):
        orig = config.MAX_EXTRACTED_BYTES
        config.MAX_EXTRACTED_BYTES = 10
        try:
            z = self._make_zip({"big.txt": "x" * 1000})
            res = archive.extract(z, self.dest)
            self.assertFalse(res.ok)
        finally:
            config.MAX_EXTRACTED_BYTES = orig

    def test_bad_archive_handled_gracefully(self):
        path = os.path.join(self.tmp, "notazip.zip")
        with open(path, "wb") as f:
            f.write(b"not actually a zip file")
        res = archive.extract(path, self.dest)
        self.assertFalse(res.ok)
        self.assertIn("invalid", res.error.lower())


class TestTarSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dest = os.path.join(self.tmp, "dest")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_tar(self, path, members: dict):
        with tarfile.open(path, "w:gz") as tf:
            for name, data in members.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                import io
                tf.addfile(info, io.BytesIO(data.encode()))

    def test_normal_extraction_works(self):
        p = os.path.join(self.tmp, "a.tar.gz")
        self._make_tar(p, {"theme/index.theme": "x"})
        res = archive.extract(p, self.dest)
        self.assertTrue(res.ok, res.error)

    def test_path_traversal_rejected(self):
        p = os.path.join(self.tmp, "evil.tar.gz")
        self._make_tar(p, {"../../tmp/evil.txt": "pwned"})
        res = archive.extract(p, self.dest)
        self.assertFalse(res.ok)

    def test_symlink_with_absolute_target_rejected(self):
        p = os.path.join(self.tmp, "sym.tar.gz")
        with tarfile.open(p, "w:gz") as tf:
            info = tarfile.TarInfo(name="evil-link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        res = archive.extract(p, self.dest)
        self.assertFalse(res.ok)

    def test_symlink_escaping_extraction_dir_rejected(self):
        p = os.path.join(self.tmp, "sym2.tar.gz")
        with tarfile.open(p, "w:gz") as tf:
            info = tarfile.TarInfo(name="theme/evil-link")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../../etc/passwd"
            tf.addfile(info)
        res = archive.extract(p, self.dest)
        self.assertFalse(res.ok)

    def test_safe_internal_symlink_allowed(self):
        p = os.path.join(self.tmp, "sym3.tar.gz")
        with tarfile.open(p, "w:gz") as tf:
            import io
            f1 = tarfile.TarInfo(name="theme/Dark/index.theme")
            data = b"[Icon Theme]\n"
            f1.size = len(data)
            tf.addfile(f1, io.BytesIO(data))

            f2 = tarfile.TarInfo(name="theme/Light/actions/icon.svg")
            data2 = b"<svg/>"
            f2.size = len(data2)
            tf.addfile(f2, io.BytesIO(data2))

            link = tarfile.TarInfo(name="theme/Dark/actions")
            link.type = tarfile.SYMTYPE
            link.linkname = "../Light/actions"
            tf.addfile(link)
        res = archive.extract(p, self.dest)
        self.assertTrue(res.ok, res.error)
        link_path = os.path.join(self.dest, "theme", "Dark", "actions")
        self.assertTrue(os.path.islink(link_path))
        self.assertTrue(os.path.isfile(os.path.join(link_path, "icon.svg")))

    def test_hardlink_rejected(self):
        p = os.path.join(self.tmp, "hard.tar.gz")
        with tarfile.open(p, "w:gz") as tf:
            info = tarfile.TarInfo(name="evil-hardlink")
            info.type = tarfile.LNKTYPE
            info.linkname = "some-other-member"
            tf.addfile(info)
        res = archive.extract(p, self.dest)
        self.assertFalse(res.ok)


if __name__ == "__main__":
    unittest.main()
