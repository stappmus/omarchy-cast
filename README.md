# Omarchy Cast

A theme-native Chromecast video player for the Quickshell-based Omarchy desktop.
Open **Omarchy Cast** from the app menu. Close the panel to keep casting.
Right-click its bar icon and choose **Exit** to stop and remove the icon.

## Version 0.3

- A focused video drop area, receiver selector, subtitles, and one Play action.
- Device-specific compatibility rules based on Google's published codec limits, with a model override in Options.
- Original video and audio are preserved when compatible with the selected output.
- Live HLS conversion instead of preparing the entire movie first.
- Unsupported audio becomes 320 kbps AAC stereo; optional 512 kbps AAC surround.
- Supported H.264 is copied into HLS without video re-encoding. Supported HEVC uses fragmented MP4 HLS.
- Required video conversion uses H.264 CRF 18, up to the device's resolution and frame-rate limits. No automatic quality ladder.
- A small initial buffer, with encoding held roughly 12–24 seconds ahead; actual startup depends on keyframes and encoding speed.
- Pause, seek, volume, mute, subtitles, and visible pending-command feedback.
- Animated connecting, compatibility, and buffering states.
- External SRT/VTT/ASS and embedded text subtitle selection, size, and language.

Profiles cover Chromecast generations 1–3, Ultra, Chromecast with Google TV,
Google TV Streamer, Nest Hub and Nest Hub Max. Codec limits are independent:
Ultra can retain 4K HEVC while its H.264 output is limited to 1080p. The Google TV
HD profile deliberately uses a conservative 1080p H.264 subset. Unknown devices
use conservative limits; ambiguous names such as "Chromecast" can be overridden
under **Options → Device model**. Third-party Cast TVs are not individually certified.

Published limits: [Google Cast supported media](https://developers.google.com/cast/docs/media).
Only Stue (Google TV Streamer) has been tested on physical hardware here; see
[validation results](VALIDATION.md) for exactly what passed.
The audio output defaults to stereo because the default receiver does not expose
the attached TV/sound system's full capabilities. Select Surround 5.1 only for a
compatible audio setup. HEVC/HDR preservation depends on source and profile;
required HDR video conversion applies SDR tone mapping.

## Install

```bash
omarchy pkg add python-pychromecast ffmpeg zenity
git clone https://github.com/stappmus/omarchy-cast.git
cd omarchy-cast
./install.sh
./setup-network.sh # If UFW is enabled
omarchy-cast
```

Requires the recent Quickshell Omarchy shell, Python 3.10+, PyChromecast 14+,
and FFmpeg with HLS, libx264, AAC, and readrate_initial_burst support.
HDR conversion additionally requires zscale and tonemap filters.

The receiver must reach TCP 49786 on this computer. The network helper opens
only that port from the current local subnet. Explicitly selected files and
session-generated segments are served over local HTTP under random URLs;
directories and arbitrary file paths are not exposed. Temporary segments live
under `~/.cache/omarchy-cast/` and are removed on Stop or Exit, or after a
10-second grace period when playback finishes or another app takes over. Abrupt termination
can leave a session directory there, but the encoder is terminated by the kernel
even if it was suspended. Error and playback-phase diagnostics are kept locally
in `~/.cache/omarchy-cast/diagnostics.jsonl`, rotating at 512 KiB.

## Use

Drop or select a video, select a TV, optionally select subtitles, and press
**Play on your TV**. Audio tracks, sound output, an audio-delay slider (−1 s to +1 s in 50 ms steps), quality limits, and a manual TV
address are under **Options**. Drag the audio-delay handle left to advance sound,
right to delay it. Releasing the handle applies automatically to the current
movie with a brief rebuffer, preserving its position and paused/playing state.
Scrolling over the handle never changes the setting. Other options apply when
restarting the video.

Audio adjustment uses FFmpeg sample trimming for earlier sound and silence
insertion for later sound, instead of offsetting timestamps alone
([FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html#atrim)).
Automatic reload was verified on Stue while playing and paused. Local signal
tests verify the sample shift; audible Bluetooth latency is not measured by the app.
URLs must point to media, not web pages or DRM content. Image subtitles require
an external text subtitle file. Closing the panel keeps playback running.

Live conversion retains a stable EVENT playlist and already-produced segments until Stop or Exit. Seeking beyond produced footage
restarts conversion at the selected movie position with another short buffer.
There is no silent automatic resolution reduction. If encoding cannot keep up,
choose a lower quality explicitly. Playback errors do not prove that a codec is
unsupported, and receiver status cannot confirm audible sound.

Re-run install.sh after updating. Use uninstall.sh to remove the launcher and
disable the plugin, then `omarchy plugin remove stappmus.cast` to remove files.

## Validation status

Version 0.1's prepared MP4 path was exercised on a Google TV Streamer.
Version 0.3 passes 33 local regression tests and was tested on Stue for original
playback, remuxing, live conversion, HEVC HLS, pause/resume, seeking and audio
adjustment. See [VALIDATION.md](VALIDATION.md) for the test scope and limits.

## Development checks

Run `python -m unittest discover -s tests -v` for local tests (including FFmpeg).
These tests do not connect to a Chromecast.

## Efficiency

Unchanged playlists are checked by file metadata instead of repeatedly reading
and parsing the full movie index. The governor checks twice per second; idle
workers sleep for up to five seconds but wake immediately for commands. Duplicate
status messages are suppressed. Local metadata is cached for the last selected
file and invalidated when the file changes. HTTP transfers use `socket.sendfile`
with byte-range limits. Encoding quality and buffer headroom are unchanged.
