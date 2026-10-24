"""
Detects theme components inside an extracted archive and installs them to
user-local directories only, applying them through GSettings. Every file
this module writes, and every setting it changes, is recorded on the
Transaction passed in so backup.rollback_transaction() can undo it exactly.
"""
from __future__ import annotations
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from . import config
from . import backup as backup_mod


@dataclass
class Component:
    kind: str              # gtk-theme | icon-theme | cursor-theme | shell-theme | wallpaper
    name: str
    source_dir: str        # extracted source path (for theme dirs) or file (for wallpaper)
    selected: bool = True


@dataclass
class DetectionResult:
    components: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _has_index_theme(d: str) -> bool:
    return os.path.isfile(os.path.join(d, "index.theme"))


def _looks_like_icon_theme(d: str) -> bool:
    """Icon themes come in two common layouts: size-first (16x16/, 22x22/,
    scalable/ ...) or context-first (apps/, actions/, devices/, mimetypes/,
    places/, status/ ... per the XDG Icon Theme spec, each containing its
    own size subdirectories). Real themes mix and match these, so check
    for either signal rather than assuming one convention."""
    if not _has_index_theme(d):
        return False
    entries = set(os.listdir(d))
    if "cursors" in entries:
        return False

    size_dirs = {"16x16", "22x22", "24x24", "32x32", "48x48", "64x64",
                 "96x96", "128x128", "256x256", "scalable", "symbolic"}
    context_dirs = {"actions", "apps", "devices", "emblems", "mimetypes",
                     "places", "status", "categories", "animations",
                     "panel", "stock", "emotes", "intl"}
    return bool(entries & size_dirs) or bool(entries & context_dirs)


def _looks_like_cursor_theme(d: str) -> bool:
    return os.path.isdir(os.path.join(d, "cursors"))


def _looks_like_gtk_or_shell(d: str) -> tuple[bool, bool]:
    entries = set(os.listdir(d))
    has_gtk = bool(entries & {"gtk-2.0", "gtk-3.0", "gtk-4.0"})
    has_shell = "gnome-shell" in entries
    return has_gtk, has_shell


def detect_components(extracted_root: str) -> DetectionResult:
    result = DetectionResult()
    seen_roots = set()

    for dirpath, dirnames, filenames in os.walk(extracted_root):
        if "index.theme" not in filenames:
            continue
        if dirpath in seen_roots:
            continue
        seen_roots.add(dirpath)
        name = os.path.basename(dirpath.rstrip("/")) or "theme"

        if _looks_like_cursor_theme(dirpath):
            result.components.append(Component("cursor-theme", name, dirpath))
            continue
        if _looks_like_icon_theme(dirpath):
            result.components.append(Component("icon-theme", name, dirpath))
            continue

        has_gtk, has_shell = _looks_like_gtk_or_shell(dirpath)
        if has_gtk:
            result.components.append(Component("gtk-theme", name, dirpath))
        if has_shell:
            result.components.append(Component("shell-theme", name, dirpath))
        if not has_gtk and not has_shell:
            result.notes.append(f"found index.theme in {name} but couldn't tell what kind it is")

    # Wallpapers: standalone images not inside a detected theme directory.
    theme_dirs = {c.source_dir for c in result.components}
    for dirpath, dirnames, filenames in os.walk(extracted_root):
        if any(dirpath.startswith(t) for t in theme_dirs):
            continue
        for fn in filenames:
            if fn.lower().endswith(config.IMAGE_EXT):
                full = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(full) < 10 * 1024:
                        continue  # skip tiny icons/badges picked up incidentally
                except OSError:
                    continue
                result.components.append(Component("wallpaper", fn, full))

    if not result.components:
        result.notes.append("no recognisable theme components found in this archive")

    return result


def _unique_dest(base_dir: str, name: str) -> str:
    """Never overwrite something that already exists — pick a free name."""
    dest = os.path.join(base_dir, name)
    if not os.path.exists(dest):
        return dest
    i = 2
    while os.path.exists(os.path.join(base_dir, f"{name}-themeswitch-{i}")):
        i += 1
    return os.path.join(base_dir, f"{name}-themeswitch-{i}")


def _copy_tree_tracked(src: str, dest: str, txn, log) -> bool:
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # symlinks=True preserves symlinks as-is instead of dereferencing
        # them. Icon themes commonly share directories between variants
        # via internal symlinks (already validated safe at extraction
        # time) — dereferencing would silently balloon a 65MB theme into
        # gigabytes by duplicating shared content at every link.
        shutil.copytree(src, dest, symlinks=True)
    except Exception as e:
        log(f"failed to install {src} -> {dest}: {e}")
        return False

    backup_mod.record_dir_created(txn, dest)
    dangling = []
    for root, dirs, files in os.walk(dest, followlinks=False):
        for f in files:
            p = os.path.join(root, f)
            backup_mod.record_file_written(txn, p, persist=False)
            if os.path.islink(p) and not os.path.exists(p):
                dangling.append(os.path.relpath(p, dest))
        for d in dirs:
            p = os.path.join(root, d)
            if os.path.islink(p):
                backup_mod.record_file_written(txn, p, persist=False)  # symlink itself, not its contents
                if not os.path.exists(p):
                    dangling.append(os.path.relpath(p, dest))
    txn.save()  # one write for the whole batch, not one per file

    log(f"installed {os.path.basename(src)} -> {dest} "
        f"({sum(1 for _ in os.walk(dest))} dirs)")
    if dangling:
        log(f"WARNING: {os.path.basename(src)} has {len(dangling)} broken link(s) — "
            f"it references files from a sibling theme variant that wasn't installed "
            f"alongside it (e.g. {dangling[0]})")
    return True


def _gsettings_set(schema: str, key: str, value: str) -> bool:
    try:
        r = subprocess.run(["gsettings", "set", schema, key, value],
                            capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def install_components(components: list, txn, log=lambda *_: None) -> list[str]:
    """Copies selected components into place and applies them. Returns a
    list of human-readable results, one per component."""
    results = []
    for c in components:
        if not c.selected:
            continue

        if c.kind == "gtk-theme":
            dest = _unique_dest(config.THEMES_DIR, c.name)
            if _copy_tree_tracked(c.source_dir, dest, txn, log):
                if backup_mod.schema_key_exists("org.gnome.desktop.interface", "gtk-theme"):
                    _gsettings_set("org.gnome.desktop.interface", "gtk-theme", os.path.basename(dest))
                    results.append(f"Applied GTK theme: {os.path.basename(dest)}")
                else:
                    results.append(f"Installed GTK theme files ({os.path.basename(dest)}) "
                                    f"but gtk-theme setting isn't available on this system")

        elif c.kind == "shell-theme":
            dest = _unique_dest(config.THEMES_DIR, c.name)
            if _copy_tree_tracked(c.source_dir, dest, txn, log):
                if backup_mod.schema_key_exists("org.gnome.shell.extensions.user-theme", "name"):
                    _gsettings_set("org.gnome.shell.extensions.user-theme", "name", os.path.basename(dest))
                    results.append(f"Applied Shell theme: {os.path.basename(dest)}")
                else:
                    results.append(f"Installed Shell theme files ({os.path.basename(dest)}) but the "
                                    f"User Themes extension isn't available — enable it in Extensions "
                                    f"to actually see this applied")

        elif c.kind == "icon-theme":
            dest = _unique_dest(config.ICONS_DIR, c.name)
            if _copy_tree_tracked(c.source_dir, dest, txn, log):
                if backup_mod.schema_key_exists("org.gnome.desktop.interface", "icon-theme"):
                    _gsettings_set("org.gnome.desktop.interface", "icon-theme", os.path.basename(dest))
                    results.append(f"Applied icon theme: {os.path.basename(dest)}")

        elif c.kind == "cursor-theme":
            dest = _unique_dest(config.CURSORS_DIR, c.name)
            if _copy_tree_tracked(c.source_dir, dest, txn, log):
                if backup_mod.schema_key_exists("org.gnome.desktop.interface", "cursor-theme"):
                    _gsettings_set("org.gnome.desktop.interface", "cursor-theme", os.path.basename(dest))
                    results.append(f"Applied cursor theme: {os.path.basename(dest)}")

        elif c.kind == "wallpaper":
            os.makedirs(config.WALLPAPERS_DIR, exist_ok=True)
            dest = _unique_dest(config.WALLPAPERS_DIR, c.name)
            try:
                shutil.copy2(c.source_dir, dest)
                backup_mod.record_file_written(txn, dest)
            except Exception as e:
                log(f"failed to install wallpaper {c.name}: {e}")
                continue
            uri = f"file://{dest}"
            applied = False
            if backup_mod.schema_key_exists("org.gnome.desktop.background", "picture-uri"):
                _gsettings_set("org.gnome.desktop.background", "picture-uri", uri)
                applied = True
            if backup_mod.schema_key_exists("org.gnome.desktop.background", "picture-uri-dark"):
                _gsettings_set("org.gnome.desktop.background", "picture-uri-dark", uri)
                applied = True
            results.append(f"Applied wallpaper: {c.name}" if applied
                            else f"Installed wallpaper file ({c.name}) but couldn't set it automatically")

    return results
