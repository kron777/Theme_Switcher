"""
Safety core: snapshot, restore, and per-transaction rollback.

Two distinct things are stored here, and they must never be confused:

1. The BASELINE (backups/baseline.json) — the desktop state captured the
   very first time ThemeSwitch ever ran, before it touched anything.
   "Restore Defaults" restores THIS. It is never silently overwritten;
   only an explicit "Save Current Desktop as Baseline" action replaces it.

2. TRANSACTIONS (backups/transactions/<id>.json) — one record per theme
   apply, listing exactly what was changed (old + new GSettings values,
   and any files this app wrote). Used to roll back a single failed
   apply without touching the baseline or anything from before it.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import config


def _gsettings_get(schema: str, key: str) -> Optional[str]:
    try:
        r = subprocess.run(["gsettings", "get", schema, key],
                            capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except Exception:
        return None


def _gsettings_set(schema: str, key: str, value: str) -> bool:
    try:
        r = subprocess.run(["gsettings", "set", schema, key, value],
                            capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def schema_key_exists(schema: str, key: str) -> bool:
    """Confirm a schema/key actually exists before touching it — not every
    GNOME key exists on every Zorin/GNOME version."""
    try:
        r = subprocess.run(["gsettings", "list-keys", schema],
                            capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return False
        return key in r.stdout.split()
    except Exception:
        return False


def capture_desktop_state() -> dict:
    """Snapshot every managed GSettings key's current value. Keys whose
    schema doesn't exist on this system are recorded as unavailable
    rather than guessed at."""
    state = {}
    for schema, key in config.MANAGED_KEYS:
        if schema_key_exists(schema, key):
            state[f"{schema}::{key}"] = _gsettings_get(schema, key)
        else:
            state[f"{schema}::{key}"] = None  # not available on this system
    return state


def baseline_exists() -> bool:
    return os.path.isfile(config.BASELINE_FILE)


def write_baseline(force: bool = False) -> dict:
    """Capture and persist the baseline. Refuses to overwrite an existing
    baseline unless force=True (the explicit 'Save Current Desktop as
    Baseline' action)."""
    if baseline_exists() and not force:
        raise FileExistsError(
            "A baseline already exists. Pass force=True (explicit user "
            "confirmation) to replace it."
        )
    snapshot = {
        "created": time.time(),
        "state": capture_desktop_state(),
    }
    tmp = config.BASELINE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snapshot, f, indent=2)
    os.replace(tmp, config.BASELINE_FILE)
    return snapshot


def ensure_baseline() -> dict:
    """Capture the baseline if (and only if) none exists yet. Safe to call
    on every app start — it's a no-op once a baseline is present."""
    if baseline_exists():
        with open(config.BASELINE_FILE) as f:
            return json.load(f)
    return write_baseline(force=False)


def read_baseline() -> Optional[dict]:
    if not baseline_exists():
        return None
    with open(config.BASELINE_FILE) as f:
        return json.load(f)


@dataclass
class Transaction:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    status: str = "in_progress"           # in_progress | committed | rolled_back | failed
    source_url: str = ""
    theme_name: str = ""
    before_state: dict = field(default_factory=dict)
    after_state: dict = field(default_factory=dict)
    files_written: list = field(default_factory=list)   # paths this app created
    dirs_created: list = field(default_factory=list)     # dirs this app created (for clean rollback)

    def path(self) -> str:
        return os.path.join(config.TRANSACTIONS_DIR, f"{self.id}.json")

    def save(self):
        with open(self.path(), "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, txn_id: str) -> "Transaction":
        with open(os.path.join(config.TRANSACTIONS_DIR, f"{txn_id}.json")) as f:
            d = json.load(f)
        t = cls()
        t.__dict__.update(d)
        return t


def begin_transaction(source_url: str, theme_name: str) -> Transaction:
    ensure_baseline()
    txn = Transaction(source_url=source_url, theme_name=theme_name,
                       before_state=capture_desktop_state())
    txn.save()
    return txn


def record_file_written(txn: Transaction, path: str, persist: bool = True):
    """persist=False skips the disk write — use that for bulk recording
    (e.g. walking a just-copied directory tree with thousands of files)
    and call txn.save() once yourself when the batch is done. Rewriting
    the whole transaction JSON after every single file turns an O(n)
    copy into an O(n^2) one on themes with tens of thousands of files."""
    txn.files_written.append(path)
    if persist:
        txn.save()


def record_dir_created(txn: Transaction, path: str, persist: bool = True):
    txn.dirs_created.append(path)
    if persist:
        txn.save()


def commit_transaction(txn: Transaction):
    txn.after_state = capture_desktop_state()
    txn.finished = time.time()
    txn.status = "committed"
    txn.save()


def rollback_transaction(txn: Transaction, log=lambda *_: None):
    """Undo exactly what this one transaction did: restore the GSettings
    values it changed, and remove only the files/dirs IT created — never
    anything that existed before it started."""
    for k, v in txn.before_state.items():
        if v is None:
            continue
        schema, key = k.split("::", 1)
        _gsettings_set(schema, key, v)

    for p in reversed(txn.files_written):
        try:
            if os.path.isfile(p) or os.path.islink(p):
                os.remove(p)
                log(f"removed {p}")
        except FileNotFoundError:
            pass
        except Exception as e:
            log(f"could not remove {p}: {e}")

    for p in reversed(txn.dirs_created):
        # dirs_created only ever holds a directory this app created
        # wholesale (e.g. a freshly-copied theme tree) — never a
        # pre-existing directory it merely wrote into — so a full
        # recursive removal here can't touch anything from before this
        # transaction. Files under it were already individually removed
        # above; this also mops up any now-empty nested subdirectories
        # that loop didn't address.
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
                log(f"removed {p}")
        except FileNotFoundError:
            pass
        except Exception as e:
            log(f"could not remove dir {p}: {e}")

    txn.finished = time.time()
    txn.status = "rolled_back"
    txn.save()


def restore_baseline(log=lambda *_: None) -> bool:
    """'Restore Defaults' — reapply every value from the saved baseline
    snapshot and verify each one actually took effect."""
    snap = read_baseline()
    if snap is None:
        log("No baseline found — nothing to restore (ThemeSwitch has never "
            "captured your original desktop state).")
        return False

    ok = True
    for k, v in snap["state"].items():
        schema, key = k.split("::", 1)
        if v is None:
            continue
        if not _gsettings_set(schema, key, v):
            log(f"FAILED to restore {schema} {key}")
            ok = False
            continue
        current = _gsettings_get(schema, key)
        if current != v:
            log(f"VERIFY MISMATCH on {schema} {key}: wanted {v}, got {current}")
            ok = False
        else:
            log(f"restored {schema} {key} -> {v}")
    return ok


def list_transactions() -> list:
    out = []
    if not os.path.isdir(config.TRANSACTIONS_DIR):
        return out
    for fn in sorted(os.listdir(config.TRANSACTIONS_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(config.TRANSACTIONS_DIR, fn)) as f:
                out.append(json.load(f))
    return out
