"""
Tests for installer.install_components — specifically the real bug found
live: a theme folder providing both GTK and Shell styling must be copied
ONCE, not twice under two different names.
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import config, installer, backup as backup_mod


class FakeGSettings:
    def __init__(self):
        self.store = {}
        self.schemas = {
            "org.gnome.desktop.interface": {"gtk-theme", "icon-theme", "cursor-theme"},
            "org.gnome.shell.extensions.user-theme": {"name"},
        }

    def run(self, cmd, **kwargs):
        class R:
            pass
        r = R()
        r.returncode = 0
        r.stdout = ""
        if cmd[1] == "get":
            r.stdout = self.store.get(f"{cmd[2]}::{cmd[3]}", "")
        elif cmd[1] == "set":
            self.store[f"{cmd[2]}::{cmd[3]}"] = cmd[4]
        elif cmd[1] == "list-keys":
            r.stdout = " ".join(self.schemas.get(cmd[2], set()))
        return r


class TestGtkShellMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.APP_DATA_DIR = self.tmp
        config.BACKUPS_DIR = os.path.join(self.tmp, "backups")
        config.TRANSACTIONS_DIR = os.path.join(self.tmp, "backups", "transactions")
        config.BASELINE_FILE = os.path.join(config.BACKUPS_DIR, "baseline.json")
        config.THEMES_DIR = os.path.join(self.tmp, "themes")
        os.makedirs(config.TRANSACTIONS_DIR)
        os.makedirs(config.THEMES_DIR)

        self.fake = FakeGSettings()
        self.patcher = patch("subprocess.run", side_effect=self.fake.run)
        self.patcher.start()

        # A real-world-shaped theme dir: index.theme + gtk-4.0 + gnome-shell.
        self.src = os.path.join(self.tmp, "src", "SomeTheme")
        os.makedirs(os.path.join(self.src, "gtk-4.0"))
        os.makedirs(os.path.join(self.src, "gnome-shell"))
        with open(os.path.join(self.src, "index.theme"), "w") as f:
            f.write("[Desktop Entry]\n")
        with open(os.path.join(self.src, "gtk-4.0", "gtk.css"), "w") as f:
            f.write("/* x */")

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detected_as_single_combined_component(self):
        det = installer.detect_components(os.path.dirname(self.src))
        self.assertEqual(len(det.components), 1)
        self.assertEqual(det.components[0].kind, "gtk-shell-theme")

    def test_install_copies_exactly_once(self):
        det = installer.detect_components(os.path.dirname(self.src))
        txn = backup_mod.begin_transaction("http://example.com", "SomeTheme")
        results = installer.install_components(det.components, txn, log=lambda *_: None)

        installed_dirs = [d for d in os.listdir(config.THEMES_DIR)
                           if os.path.isdir(os.path.join(config.THEMES_DIR, d))]
        self.assertEqual(len(installed_dirs), 1, f"expected exactly one install, got {installed_dirs}")
        self.assertEqual(len(results), 1)

    def test_both_gsettings_keys_point_to_the_same_installed_name(self):
        det = installer.detect_components(os.path.dirname(self.src))
        txn = backup_mod.begin_transaction("http://example.com", "SomeTheme")
        installer.install_components(det.components, txn, log=lambda *_: None)

        gtk_val = self.fake.store["org.gnome.desktop.interface::gtk-theme"]
        shell_val = self.fake.store["org.gnome.shell.extensions.user-theme::name"]
        self.assertEqual(gtk_val, shell_val)


if __name__ == "__main__":
    unittest.main()
