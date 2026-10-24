"""
Tests for backup.py — baseline capture/restore and transaction rollback.
Uses a temp directory for storage and mocks every gsettings call, so this
never touches the real desktop, per the project's own testing rules.
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from themeswitch import config, backup as backup_mod


class FakeGSettings:
    """In-memory stand-in for the real desktop's GSettings store."""
    def __init__(self, initial: dict):
        self.store = dict(initial)
        self.schemas_with_keys = {k.split("::")[0]: set() for k in initial}
        for k in initial:
            schema, key = k.split("::")
            self.schemas_with_keys.setdefault(schema, set()).add(key)

    def run(self, cmd, **kwargs):
        class R:
            pass
        r = R()
        r.returncode = 0
        r.stdout = ""
        if cmd[1] == "get":
            schema, key = cmd[2], cmd[3]
            v = self.store.get(f"{schema}::{key}")
            if v is None:
                r.returncode = 1
            else:
                r.stdout = v
        elif cmd[1] == "set":
            schema, key, val = cmd[2], cmd[3], cmd[4]
            self.store[f"{schema}::{key}"] = val
        elif cmd[1] == "list-keys":
            schema = cmd[2]
            r.stdout = " ".join(self.schemas_with_keys.get(schema, set()))
        return r


class TestBaseline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.APP_DATA_DIR = self.tmp
        config.BACKUPS_DIR = os.path.join(self.tmp, "backups")
        config.TRANSACTIONS_DIR = os.path.join(self.tmp, "backups", "transactions")
        config.BASELINE_FILE = os.path.join(config.BACKUPS_DIR, "baseline.json")
        os.makedirs(config.TRANSACTIONS_DIR)

        initial = {f"{s}::{k}": "original-value" for s, k in config.MANAGED_KEYS}
        self.fake = FakeGSettings(initial)
        self.patcher = patch("subprocess.run", side_effect=self.fake.run)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_baseline_captured_once(self):
        self.assertFalse(backup_mod.baseline_exists())
        snap1 = backup_mod.ensure_baseline()
        self.assertTrue(backup_mod.baseline_exists())
        # Change something, ensure_baseline again must NOT overwrite it.
        self.fake.store["org.gnome.desktop.interface::gtk-theme"] = "changed-value"
        snap2 = backup_mod.ensure_baseline()
        self.assertEqual(snap1["state"], snap2["state"])

    def test_force_overwrite_baseline(self):
        backup_mod.write_baseline(force=False)
        self.fake.store["org.gnome.desktop.interface::gtk-theme"] = "new-baseline-value"
        snap = backup_mod.write_baseline(force=True)
        self.assertEqual(snap["state"]["org.gnome.desktop.interface::gtk-theme"], "new-baseline-value")

    def test_overwrite_without_force_raises(self):
        backup_mod.write_baseline(force=False)
        with self.assertRaises(FileExistsError):
            backup_mod.write_baseline(force=False)

    def test_restore_baseline_reapplies_and_verifies(self):
        backup_mod.ensure_baseline()
        self.fake.store["org.gnome.desktop.interface::gtk-theme"] = "SomeOtherTheme"
        ok = backup_mod.restore_baseline(log=lambda *_: None)
        self.assertTrue(ok)
        self.assertEqual(self.fake.store["org.gnome.desktop.interface::gtk-theme"], "original-value")

    def test_restore_with_no_baseline_returns_false(self):
        ok = backup_mod.restore_baseline(log=lambda *_: None)
        self.assertFalse(ok)

    def test_missing_schema_key_recorded_as_unavailable(self):
        del self.fake.schemas_with_keys["org.gnome.shell.extensions.user-theme"]
        snap = backup_mod.write_baseline(force=False)
        self.assertIsNone(snap["state"]["org.gnome.shell.extensions.user-theme::name"])


class TestTransactionRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.APP_DATA_DIR = self.tmp
        config.BACKUPS_DIR = os.path.join(self.tmp, "backups")
        config.TRANSACTIONS_DIR = os.path.join(self.tmp, "backups", "transactions")
        config.BASELINE_FILE = os.path.join(config.BACKUPS_DIR, "baseline.json")
        os.makedirs(config.TRANSACTIONS_DIR)

        initial = {f"{s}::{k}": "original-value" for s, k in config.MANAGED_KEYS}
        self.fake = FakeGSettings(initial)
        self.patcher = patch("subprocess.run", side_effect=self.fake.run)
        self.patcher.start()

        self.work_dir = os.path.join(self.tmp, "work")
        os.makedirs(self.work_dir)

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rollback_restores_gsettings_and_removes_written_files(self):
        txn = backup_mod.begin_transaction("http://example.com/t.zip", "TestTheme")

        self.fake.store["org.gnome.desktop.interface::gtk-theme"] = "NewTheme"
        written_dir = os.path.join(self.work_dir, "NewTheme")
        os.makedirs(written_dir)
        written_file = os.path.join(written_dir, "gtk.css")
        with open(written_file, "w") as f:
            f.write("/* x */")
        backup_mod.record_dir_created(txn, written_dir)
        backup_mod.record_file_written(txn, written_file)

        backup_mod.rollback_transaction(txn, log=lambda *_: None)

        self.assertEqual(self.fake.store["org.gnome.desktop.interface::gtk-theme"], "original-value")
        self.assertFalse(os.path.exists(written_file))
        self.assertFalse(os.path.exists(written_dir))
        self.assertEqual(txn.status, "rolled_back")

    def test_rollback_never_touches_files_outside_this_transaction(self):
        # A file that existed BEFORE this transaction must survive rollback.
        pre_existing = os.path.join(self.work_dir, "already-here.txt")
        with open(pre_existing, "w") as f:
            f.write("keep me")

        txn = backup_mod.begin_transaction("http://example.com/t.zip", "TestTheme")
        backup_mod.rollback_transaction(txn, log=lambda *_: None)

        self.assertTrue(os.path.exists(pre_existing))

    def test_commit_marks_status_and_captures_after_state(self):
        txn = backup_mod.begin_transaction("http://example.com/t.zip", "TestTheme")
        self.fake.store["org.gnome.desktop.interface::gtk-theme"] = "NewTheme"
        backup_mod.commit_transaction(txn)
        self.assertEqual(txn.status, "committed")
        self.assertEqual(txn.after_state["org.gnome.desktop.interface::gtk-theme"], "NewTheme")

    def test_second_transaction_does_not_disturb_first_baseline(self):
        backup_mod.ensure_baseline()
        baseline_before = backup_mod.read_baseline()

        txn1 = backup_mod.begin_transaction("u1", "Theme1")
        backup_mod.commit_transaction(txn1)
        txn2 = backup_mod.begin_transaction("u2", "Theme2")
        backup_mod.commit_transaction(txn2)

        baseline_after = backup_mod.read_baseline()
        self.assertEqual(baseline_before, baseline_after)


if __name__ == "__main__":
    unittest.main()
