"""
Orchestrates one full apply: download -> validate/extract -> detect ->
install, inside a single Transaction. Any failure partway through rolls
back everything that transaction did so far.
"""
from __future__ import annotations
import os
import shutil
import threading
import time

from . import config
from . import downloader
from . import archive
from . import installer
from . import scanner
from . import backup as backup_mod


class Pipeline:
    def __init__(self, log_cb=lambda *_: None):
        self.log_cb = log_cb
        self.cancel_event = threading.Event()

    def log(self, msg: str):
        self.log_cb(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def cancel(self):
        self.cancel_event.set()

    def download_and_extract(self, url: str, allow_http: bool = False):
        """Download + validate + extract only — used for the analysis/preview
        step, before the user has chosen to apply anything. No GSettings or
        installed files are touched here."""
        self.log(f"downloading {url}")
        dl = downloader.download(url, allow_http=allow_http, cancel_event=self.cancel_event,
                                  progress_cb=lambda got, total: None)
        if not dl.ok:
            self.log(f"download failed: {dl.error}")
            return None, dl.error

        self.log(f"downloaded {dl.bytes_written} bytes -> {dl.path}")
        extract_dest = os.path.join(config.EXTRACT_DIR, os.path.basename(dl.path) + "_x")
        if os.path.exists(extract_dest):
            shutil.rmtree(extract_dest)

        self.log("validating and extracting archive")
        res = archive.extract(dl.path, extract_dest)
        if not res.ok:
            self.log(f"extraction failed: {res.error}")
            try:
                os.remove(dl.path)
            except OSError:
                pass
            return None, res.error

        self.log(f"extracted {len(res.extracted_files)} file(s)")

        self.log("scanning extracted files for anything that isn't a theme asset")
        scan = scanner.scan_directory(extract_dest)
        if not scan.ok:
            details = "; ".join(f"{f.path}: {f.reason}" for f in scan.findings[:5])
            self.log(f"SCAN BLOCKED install — {len(scan.findings)} suspicious file(s): {details}")
            shutil.rmtree(extract_dest, ignore_errors=True)
            try:
                os.remove(dl.path)
            except OSError:
                pass
            return None, (f"Blocked: this archive contains {len(scan.findings)} file(s) that "
                           f"aren't legitimate theme assets ({details}). Nothing was installed.")

        self.log(f"scan clean — {scan.files_scanned} file(s) checked, all recognised theme assets")
        return extract_dest, None

    def apply(self, source_url: str, theme_name: str, components: list):
        """Runs a full transaction applying the given (already-detected,
        user-selected) components. Rolls back automatically on any error."""
        txn = backup_mod.begin_transaction(source_url, theme_name)
        self.log(f"transaction {txn.id} started")
        try:
            results = installer.install_components(components, txn, log=self.log)
            backup_mod.commit_transaction(txn)
            self.log(f"transaction {txn.id} committed")
            return True, results, txn.id
        except Exception as e:
            self.log(f"error during apply: {e} — rolling back")
            backup_mod.rollback_transaction(txn, log=self.log)
            return False, [f"Apply failed and was rolled back: {e}"], txn.id
