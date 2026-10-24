#!/bin/bash
# Installs the ThemeSwitch .desktop launcher so it shows up in your app
# grid. Does NOT touch any system directories, does NOT use sudo, and
# does NOT modify anything outside your own home directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"

sed "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/themeswitch.desktop" \
  > "$APPS_DIR/themeswitch.desktop"

chmod +x "$SCRIPT_DIR/themeswitch.py" "$SCRIPT_DIR/themeswitch-restore.py"

echo "Installed launcher: $APPS_DIR/themeswitch.desktop"
echo "You should now find ThemeSwitch in your application grid."
echo ""
echo "Run directly any time with:"
echo "  python3 $SCRIPT_DIR/themeswitch.py"
echo ""
echo "Recovery, even without the GUI:"
echo "  python3 $SCRIPT_DIR/themeswitch-restore.py"
