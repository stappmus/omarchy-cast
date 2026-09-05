# Omarchy Cast

A native Omarchy shell plugin for sending videos to Chromecast and Google Cast TVs.
Open **Omarchy Cast** from the app launcher. Its icon remains in the bar after the
panel closes. Right-click the icon and choose **Exit** to stop casting, close the
media server, clean up converted files, and remove the icon. Opening it again
restores the icon. An enabled icon survives shell restarts; playback does not.

## Features

- Drag-and-drop local videos, a native file picker, and direct HTTP(S) video links.
- Receiver discovery plus a manual receiver IP override.
- Embedded text subtitles and external SRT, VTT, ASS/SSA files, converted to WebVTT.
- Subtitle language, size, and live CC toggle.
- Audio-track selection for local files.
- Auto, direct, and H.264/AAC compatibility modes; original, 1080p, or 720p conversion.
- Play/pause, seek slider, ±30 seconds, starting offset, volume, mute, and stop.
- Preparation progress and cancellation.
- Shared Omarchy popup, control, font, spacing, border, and color tokens; responds to theme changes.

## Install

Requires a recent **Quickshell-based Omarchy** with `qs.Ui.KeyboardPanel`,
`omarchy plugin`, Python 3.10+, PyChromecast 14+, FFmpeg, and Zenity.
The older Waybar-based Omarchy shell is not supported.

```bash
omarchy pkg add python-pychromecast ffmpeg zenity
git clone https://github.com/stappmus/omarchy-cast.git
cd omarchy-cast
./install.sh
./setup-network.sh  # If UFW is enabled
omarchy-cast
```

The installer creates the plugin at `~/.config/omarchy/plugins/stappmus.cast`,
a launcher at `~/.local/bin/omarchy-cast`, and an app-menu desktop entry.
It does not edit packaged Omarchy files or replace your bar configuration.
Re-run the installer after pulling updates. To uninstall, run `./uninstall.sh`
and then `omarchy plugin remove stappmus.cast` to remove the plugin files.

## Using Cast

1. Drop a video on the card, or click it to browse. Use **Or paste a video link** for a URL.
2. Select your TV under **Play on**. The first discovered TV is selected automatically.
3. Choose subtitles if wanted, then press **Play on _TV name_**.

The player appears while casting. **Options** holds audio tracks, subtitle size,
compatibility, resolution, starting time, and a manual TV address. Right-click
the bar icon for **Exit**. Closing the panel keeps casting.

## Playback notes

Use a Chromecast on the same reachable local network. Discovery uses mDNS;
with segmented networks, try the receiver IP. The receiver must be able to
connect back to this computer's HTTP port (TCP 49786). The server
binds to the local interface used to reach the receiver. Only explicitly chosen
media and subtitle files are exposed, under random URLs, for the cast session;
it does not serve directories. This is unencrypted local-network HTTP.

**Auto** sends H.264/yuv420p MP4 files with AAC/MP3 audio directly and prepares
other local formats as MP4. Compatible H.264 video is copied unchanged when
only its container or audio needs conversion. Audio selection or resolution limits also trigger
conversion in Auto. **Compatibility** always converts; **Direct** lets the
receiver try the original file. Codec support varies by receiver generation.
Conversion finishes before playback, allowing dependable seeking; large videos
can take time and require temporary disk space in `~/.cache/omarchy-cast/`
(or `$XDG_CACHE_HOME/omarchy-cast/`). Temporary media is deleted on
Stop or Exit. Abrupt process termination or power loss can leave an
`session-*` directory in `~/.cache/omarchy-cast/`.

Video URLs must point to playable media, not YouTube/web pages or DRM content.
URLs are played directly without conversion or embedded-track inspection.
Image subtitles (PGS/VobSub) are omitted because WebVTT requires text; use an
external SRT/VTT instead. Subtitle appearance support varies by receiver.
Changing track, subtitle-file, size, or conversion settings takes effect when
you press **Play on your TV** again, or **Options → Apply & restart video**
during playback. The CC button toggles the loaded subtitle track.
Closing the panel leaves playback running; Exit stops playback owned by this
plugin. If another app takes over the receiver, the plugin will not stop it.

## Development and validation

```bash
python -m unittest discover -s tests -v
omarchy plugin validate .
python -m py_compile backend.py
```

The worker uses JSON lines over stdin/stdout, never shell-interpolated media
paths. A single worker owns each mounted bar widget. Intended for one bar
instance; avoid manually duplicating it across multiple bars.

## Validation

Automated coverage exercises real FFmpeg conversion and subtitle extraction,
selects the second audio track and checks the resulting audio frequency,
verifies HTTP seeking/HEAD/CORS, rejects unselected paths, and checks receiver
ownership on Stop. The launcher, native panel, and Exit lifecycle were exercised
on a Quickshell Omarchy desktop.

Cast transport uses [PyChromecast](https://github.com/home-assistant-libs/pychromecast),
including its [media/subtitle API](https://github.com/home-assistant-libs/pychromecast/blob/master/pychromecast/controllers/media.py).
The plugin and installer are MIT licensed.
