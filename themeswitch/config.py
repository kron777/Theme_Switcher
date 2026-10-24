"""Paths and constants for ThemeSwitch. Nothing here touches the system."""
from __future__ import annotations
import os

HOME = os.path.expanduser("~")

APP_DATA_DIR = os.path.join(HOME, ".local", "share", "themeswitch")
BACKUPS_DIR = os.path.join(APP_DATA_DIR, "backups")
DOWNLOADS_DIR = os.path.join(APP_DATA_DIR, "downloads")
EXTRACT_DIR = os.path.join(APP_DATA_DIR, "extracted")
LOG_FILE = os.path.join(APP_DATA_DIR, "themeswitch.log")
BASELINE_FILE = os.path.join(BACKUPS_DIR, "baseline.json")
TRANSACTIONS_DIR = os.path.join(BACKUPS_DIR, "transactions")

# User-local install targets only — never system-wide.
THEMES_DIR = os.path.join(HOME, ".local", "share", "themes")
ICONS_DIR = os.path.join(HOME, ".local", "share", "icons")
WALLPAPERS_DIR = os.path.join(HOME, ".local", "share", "wallpapers")
CURSORS_DIR = ICONS_DIR  # cursor themes live under the icons tree by convention

# GSettings keys ThemeSwitch is allowed to touch. Nothing outside this list
# is ever written, and every key here has a known safe default fallback.
MANAGED_KEYS = [
    ("org.gnome.desktop.interface", "gtk-theme"),
    ("org.gnome.desktop.interface", "icon-theme"),
    ("org.gnome.desktop.interface", "cursor-theme"),
    ("org.gnome.desktop.interface", "color-scheme"),
    ("org.gnome.shell.extensions.user-theme", "name"),
    ("org.gnome.desktop.background", "picture-uri"),
    ("org.gnome.desktop.background", "picture-uri-dark"),
]

# Safety limits for untrusted downloads/archives.
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024       # 300 MB cap on a single download
MAX_EXTRACTED_BYTES = 800 * 1024 * 1024      # 800 MB cap on decompressed size — the real
                                               # zip-bomb guard (checked both up-front from
                                               # declared sizes and enforced mid-stream)
MAX_ARCHIVE_ENTRIES = 200000                  # entry-count guard — a secondary defense against
                                               # millions-of-tiny-files abuse, not the primary
                                               # zip-bomb check. Large real icon themes (Papirus,
                                               # Suru++) legitimately ship 40-50k+ small files,
                                               # so this must sit well above that, not near it.
DOWNLOAD_TIMEOUT_S = 30                      # per-request timeout
DOWNLOAD_CHUNK = 65536

ALLOWED_ARCHIVE_EXT = (".zip", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tbz2")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".svg", ".webp")

for d in (APP_DATA_DIR, BACKUPS_DIR, DOWNLOADS_DIR, EXTRACT_DIR, TRANSACTIONS_DIR,
          THEMES_DIR, ICONS_DIR, WALLPAPERS_DIR):
    os.makedirs(d, exist_ok=True)
