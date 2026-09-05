# Omarchy Cast

A theme-native Chromecast video player for the Quickshell-based Omarchy desktop.
Open **Omarchy Cast** from the app menu. Close the panel to keep casting.
Right-click its bar icon and choose **Exit** to stop and remove the icon.

## Version 0.2

- A focused video drop area, receiver selector, subtitles, and one Play action.
- A Google TV Streamer compatibility profile based on Google's published limits.
- Original video and audio are preserved when compatible with the selected output.
- Live HLS conversion instead of preparing the entire movie first.
- Unsupported audio becomes 320 kbps AAC stereo; optional 512 kbps AAC surround.
- Supported H.264 is copied into HLS without video re-encoding. Supported HEVC uses fragmented MP4 HLS.
- Required video conversion uses H.264 CRF 18, up to the device's resolution and frame-rate limits. No automatic quality ladder.
- A small initial buffer, with encoding held roughly 12–24 seconds ahead; actual startup depends on keyframes and encoding speed.
- Pause, seek, volume, mute, subtitles, and visible pending-command feedback.
- Animated connecting, compatibility, and buffering states.
- External SRT/VTT/ASS and embedded text subtitle selection, size, and language.

Google TV Streamer is the primary target for this release. Other video Cast devices
use a conservative standard profile; this is not a complete per-model database.
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
under `~/.cache/omarchy-cast/` and are removed on Stop or Exit. Abrupt termination
can leave a session directory there.

## Use

Drop or select a video, select a TV, optionally select subtitles, and press
**Play on your TV**. Audio tracks, sound output, an audio-delay slider (−1 s to +1 s in 50 ms steps), quality limits, and a manual TV
address are under **Options**. Changes apply when restarting the video.
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
Version 0.2's Python syntax and plugin manifest were checked and the panel loaded.
Live playback and the new compatibility policies have **not** been playback-tested;
testing was deferred at the user's request. The automated suite is being updated
for the new streaming architecture; do not interpret earlier conversion results
as validation of live HLS.

Compatibility references: [Google Cast supported media](https://developers.google.com/cast/docs/media)
and [FFmpeg HLS documentation](https://ffmpeg.org/ffmpeg-formats.html#hls-1).
Transport uses [PyChromecast](https://github.com/home-assistant-libs/pychromecast).
MIT licensed.
