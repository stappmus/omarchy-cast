#!/bin/bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/stappmus.cast"
for dependency in python ffmpeg ffprobe zenity omarchy omarchy-shell; do
  command -v "$dependency" >/dev/null || { echo "Missing $dependency. Install dependencies: omarchy pkg add python-pychromecast ffmpeg zenity" >&2; exit 1; }
done
python -c 'import pychromecast' || { echo 'Install: omarchy pkg add python-pychromecast' >&2; exit 1; }
omarchy plugin validate "$source_dir"
mkdir -p "$target" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
if [[ "$source_dir" != "$target" ]]; then
  for file in manifest.json CastPanel.qml backend.py streaming.py compatibility.py omarchy-cast install.sh uninstall.sh README.md LICENSE icon.svg setup-network.sh; do
    install -m 644 "$source_dir/$file" "$target/$file"
  done
  chmod +x "$target/omarchy-cast" "$target/install.sh" "$target/uninstall.sh"
fi
install -m 755 "$source_dir/omarchy-cast" "$HOME/.local/bin/omarchy-cast"
install -m 644 "$source_dir/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/omarchy-cast.svg"
cat > "$HOME/.local/share/applications/omarchy-cast.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Omarchy Cast
GenericName=Chromecast Video Player
Comment=Cast videos with subtitles and audio-track selection
Exec="$HOME/.local/bin/omarchy-cast"
Icon=omarchy-cast
Terminal=false
Categories=AudioVideo;Video;Player;
Keywords=Chromecast;Cast;TV;Subtitles;Video;
StartupNotify=false
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
omarchy-shell shell rescanPlugins >/dev/null
echo 'Installed. Open Omarchy Cast from the app menu.'
