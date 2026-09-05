#!/bin/bash
set -euo pipefail
omarchy-shell -q stappmus.cast exit
omarchy plugin disable stappmus.cast >/dev/null
rm -f "$HOME/.local/bin/omarchy-cast" "$HOME/.local/share/applications/omarchy-cast.desktop" "$HOME/.local/share/icons/hicolor/scalable/apps/omarchy-cast.svg"
echo 'Launcher removed and plugin disabled. To remove its files: omarchy plugin remove stappmus.cast'
