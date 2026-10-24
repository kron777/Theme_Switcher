#!/usr/bin/env python3
"""
Standalone recovery utility — restores the desktop ThemeSwitch snapshotted,
without needing the GUI (or even GTK) to work. Run this directly if the
app won't launch:

    python3 themeswitch-restore.py

It only touches GSettings keys it already knows how to read back and
verify; it never deletes any files.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from themeswitch import backup as backup_mod  # noqa: E402


def main():
    snap = backup_mod.read_baseline()
    if snap is None:
        print("No ThemeSwitch baseline found at", backup_mod.config.BASELINE_FILE)
        print("Nothing to restore.")
        return 1

    print("Restoring desktop to the state saved before ThemeSwitch's first change...")
    ok = backup_mod.restore_baseline(log=print)
    if ok:
        print("\nDone. Every saved setting was reapplied and verified.")
        return 0
    else:
        print("\nSome settings could not be restored or verified — see above.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
