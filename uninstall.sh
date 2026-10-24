#!/bin/bash
# Removes the ThemeSwitch launcher. By default this leaves installed
# theme files and backups alone — your desktop keeps whatever is
# currently applied. Pass --purge to also delete ThemeSwitch's own data
# (backups, downloads, baseline) after confirming.
set -euo pipefail

APPS_DIR="$HOME/.local/share/applications"
rm -f "$APPS_DIR/themeswitch.desktop"
echo "Removed launcher: $APPS_DIR/themeswitch.desktop"

if [ "${1:-}" = "--purge" ]; then
  DATA_DIR="$HOME/.local/share/themeswitch"
  echo ""
  echo "This will permanently delete ThemeSwitch's data, including your"
  echo "ORIGINAL DESKTOP BASELINE at $DATA_DIR"
  echo "After this, 'Restore Defaults' will no longer be possible."
  read -r -p "Type YES to confirm: " confirm
  if [ "$confirm" = "YES" ]; then
    rm -rf "$DATA_DIR"
    echo "Removed $DATA_DIR"
  else
    echo "Cancelled — data left in place."
  fi
else
  echo "Theme files and your backup/restore data were left in place."
  echo "Run with --purge to also remove them (after a confirmation prompt)."
fi
