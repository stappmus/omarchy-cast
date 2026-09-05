# Validation — 2026-09-06

## Automated local tests

`python -m unittest discover -s tests -v`

33 tests cover HTTP ranges/CORS and selected-file access, device and codec limits,
manual model selection, default/explicit audio tracks, cover-art exclusion,
subtitle conversion and seek retiming, real HLS encoding and audio selection,
receiver progress evidence, startup after a short encoder finishes, +/-1 second
audio sample shifts after seeking, paused/playing audio adjustments, and killing
a suspended encoder when its parent exits.

## Physical device: Stue / Google TV Streamer

Synthetic silent 320x180 clips were sent through the production backend, with
receiver-reported timeline progress required for success:

- Original H.264/AAC MP4: passed.
- H.264/AAC MKV remuxed into MPEG-TS HLS: passed.
- Live H.264 conversion: passed.
- HEVC MKV remuxed into fragmented-MP4 HLS: passed.
- Adjust audio to -500 ms while playing: passed, resumed playing.
- Adjust audio to +500 ms while paused: passed, stayed paused at the same position.

The user's 1080p H.264/DTS movie also passed with embedded text subtitles selected:
original video plus live AAC stereo, startup about 9 seconds, pause/resume, and a
seek to 10:00 beyond the produced buffer. All test sessions were stopped afterward.

Receiver progress does not establish audible sound, visual subtitle rendering,
or Bluetooth lip sync. The sample-shift test decodes audio locally. This is not
4K/HDR certification or a long-duration network endurance test.

Other device profiles are checked against Google's published limits and unit
cases; no other physical receiver was available for validation.

## Earlier failures reviewed

- Logs contain connection failures and 10-second play-command timeouts. They do
  not establish a codec incompatibility or a specific Bluetooth root cause.
- A suspended FFmpeg process from an obsolete session survived worker exit.
  Removed that process/cache; a parent-death signal now prevents recurrence.
- A completed short encoder could be mistaken for failure before the governor
  refreshed its playlist. Read the final playlist before checking readiness.
- Default audio selection ignored the container's default disposition. Select
  and map the same explicit stream index used by compatibility planning.
- Video mapping could select cover art despite planning around the real video.
  Map the planned video index.
- Broad device limits caused unnecessary conversion or mismatched codec limits.
  Use per-codec limits and a manual override for ambiguous advertised models.
- Conversion failures suggested enabling conversion even when already enabled.
  Use contextual messages and retain bounded local diagnostics.

Previous fixes retained: stable EVENT playlists for pause/resume, governor
headroom for long GOPs, keyframe-aligned remux seeks, sample-based audio delay,
and a drag-only delay control that ignores wheel scrolling.

## Efficiency update — 0.3.1

Added regressions for unchanged playlist reads and atomic updates, metadata cache
invalidation, duplicate status suppression, safe inactive-session cleanup, and
prompt worker shutdown while idle. The four synthetic Stue playback paths and
both playing/paused audio adjustments passed again after these optimizations.

A local microbenchmark with a 3,600-segment playlist took about 900 ms for 1,000
unchanged window checks before caching and 0.9 ms afterward. This measures only
the playlist-check path, not whole-app CPU or overall playback performance.
The governor now wakes at 2 Hz instead of 10 Hz; idle status checks at 0.2 Hz
instead of approximately 2 Hz. Playback status continues at 2 Hz, and queued
commands wake the worker immediately.

Removed 5.06 GiB of abandoned session cache on the development machine after
confirming that its Cast workers and encoders had exited. No source videos were
removed. Picture quality, codec policies, and buffer thresholds are unchanged.
