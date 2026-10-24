# ThemeSwitch v1.0

One-click desktop theme installer and restorer for Zorin OS 18.1 / GNOME 46.

Paste a theme URL (a direct archive link, a GitHub repo, or a GitHub
release), review what it found, apply it, and if you don't like it, hit
**Restore Defaults** to put your desktop back exactly how it was before
ThemeSwitch ever touched it.

## Install

```bash
cd ~/Desktop/themeswitch
./install.sh
```

This only adds a launcher to `~/.local/share/applications/` — nothing is
installed system-wide, no `sudo` is used anywhere by this app, ever.

## Run

From the app grid ("ThemeSwitch"), or directly:

```bash
python3 ~/Desktop/themeswitch/themeswitch.py
```

## Using it

1. Paste a theme page URL into the field and press Enter (or click
   **ANALYSE URL**). Supported: direct `.zip`/`.tar.gz`/`.tar.xz`/`.tar.bz2`
   links, GitHub repositories, and GitHub releases. Other pages get a
   best-effort scan for archive links; if nothing is found, you'll get an
   honest explanation rather than a fake success.
2. Review the detected components (GTK theme, icon theme, cursor theme,
   Shell theme, wallpaper) and untick anything you don't want.
3. Click **APPLY THEME**. Everything gets downloaded, safety-checked, and
   installed to your own `~/.local/share/{themes,icons,wallpapers}/` —
   never system-wide.
4. Don't like it? Click **RESTORE DEFAULTS**. This reapplies the exact
   desktop settings ThemeSwitch saved the *first* time it ever ran, before
   any change — not a guess at "factory defaults," your actual prior
   setup. Files ThemeSwitch installed are left on disk (nothing is
   deleted); only the active settings are reverted.

## If the GUI won't launch

Recovery doesn't depend on the GUI working:

```bash
python3 ~/Desktop/themeswitch/themeswitch-restore.py
```

This reads the same saved baseline and reapplies it, with no GTK
dependency at all.

## What it will never do

- Never runs `sudo` or asks for your password.
- Never installs anything outside your home directory.
- Never executes a script or binary found inside a downloaded archive.
- Never overwrites a theme/icon/cursor directory that already exists —
  if a name collides, the new one gets installed under a different name
  instead.
- Never deletes a file it didn't create itself.

## Safety architecture

- **Baseline** (`~/.local/share/themeswitch/backups/baseline.json`) — your
  desktop's state, captured once, the first time ThemeSwitch ever runs.
  This is what "Restore Defaults" restores. It's never silently
  overwritten by later theme changes.
- **Transactions** (`~/.local/share/themeswitch/backups/transactions/`) —
  one record per theme apply, listing exactly what changed. If an apply
  fails partway through, it's automatically rolled back using this record
  alone — the baseline is untouched either way.
- **Downloads are untrusted input.** HTTPS is required by default. Archive
  extraction is hand-validated per-entry: path traversal, symlink
  entries, and oversized/zip-bomb archives are all rejected before a
  single file is written. Nothing from inside a downloaded archive is
  ever executed.

## Uninstall

```bash
./uninstall.sh            # removes the launcher only
./uninstall.sh --purge    # also deletes backups/baseline, after confirming
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

All tests run against a mocked GSettings store and temporary directories —
none of them touch your real desktop.

## Known limitations (honest, not hidden)

- Theme-site scraping is best-effort. Sites that require JavaScript to
  render their download links, or that gate downloads behind a login,
  won't be auto-detected — paste a direct archive URL instead if you have
  one.
- GNOME Shell theme changes require the **User Themes** extension to be
  installed and enabled (it is, by default, on this system). If it's ever
  disabled, ThemeSwitch will say so rather than pretending the Shell
  theme applied.
- Desktop-icon styling and dash/taskbar extensions (Zorin's own
  `zorin-desktop-icons`, `zorin-taskbar`, etc.) are not managed by this
  app — those are separate GNOME Shell extensions with their own settings,
  outside a GTK/icon/cursor/wallpaper theme's scope.
