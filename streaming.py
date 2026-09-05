"""Bounded-ahead HLS encoder and receiver playback evidence."""
from __future__ import annotations
from dataclasses import dataclass
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time


class PlaybackEvidence:
    """Uses receiver-reported timestamps; extrapolated local time is not evidence."""
    def __init__(self):
        self.first_position = None
        self.last_position = None
        self.progress_samples = 0

    def observe(self, state, position, idle_reason=None):
        if state == 'IDLE' and idle_reason == 'ERROR':
            return 'failed'
        if state != 'PLAYING':
            return 'waiting'
        position = float(position or 0)
        if self.first_position is None:
            self.first_position = position
        elif self.last_position is not None and position > self.last_position + .15:
            self.progress_samples += 1
        self.last_position = position
        if position - self.first_position >= .5 and self.progress_samples >= 1:
            return 'playing'
        return 'waiting'


def shift_vtt(text, offset):
    """Clip/retime full-file WebVTT for an HLS stream starting at offset."""
    def seconds(value):
        parts = value.split(':')
        return sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))
    def stamp(value):
        ms = max(0, round(value * 1000))
        return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}.{ms % 1000:03}'
    blocks = re.split(r'\n\s*\n', text.replace('\r\n', '\n'))
    result = ['WEBVTT']
    for block in blocks:
        match = re.search(r'((?:\d+:)?\d{2}:\d{2}\.\d{3}) --> ((?:\d+:)?\d{2}:\d{2}\.\d{3})', block)
        if not match or seconds(match[2]) <= offset:
            continue
        result.append(block[:match.start()] + stamp(seconds(match[1]) - offset) + ' --> ' + stamp(seconds(match[2]) - offset) + block[match.end():])
    return '\n\n'.join(result) + '\n'


class LiveStream:
    """FFmpeg produces 2-second HLS segments, keeping ~12–24 seconds ahead.

    An EVENT playlist retains produced segments so a paused movie never ages
    out of a moving live window. Seeking beyond produced footage starts
    another stream at the requested movie timestamp. SIGSTOP throttles the
    encoder without discarding its codec state, including during TV pauses.
    """
    def __init__(self, folder, source, profile, audio='default', offset=0, playhead=None, audio_delay=0):
        self.folder = Path(folder)
        self.folder.mkdir(mode=0o700)
        self.playlist = self.folder / 'index.m3u8'
        self.profile = profile
        self.offset = offset
        self.playhead = playhead or (lambda: 0)
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.suspended = False
        self.durations = {}
        self.window = (0., 0.)
        self.window_lock = threading.Lock()
        self.errors = []
        args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-readrate', '4', '-readrate_initial_burst', '12']
        if offset:
            args += ['-ss', str(offset)]
        args += ['-i', str(source), '-map', '0:v:0', '-map', '0:a:0?' if audio == 'default' else f'0:{int(audio)}', '-sn']
        if profile.copy_video:
            args += ['-c:v', 'copy']
            if profile.fmp4:
                args += ['-tag:v', 'hvc1']
        else:
            filters = []
            if profile.hdr_to_sdr:
                filters += ['zscale=t=linear:npl=100', 'format=gbrpf32le', 'zscale=p=bt709', 'tonemap=tonemap=hable:desat=0', 'zscale=t=bt709:m=bt709:r=tv']
            filters += [rf'scale=w=min(iw\,{profile.height * 16 // 9}):h=min(ih\,{profile.height}):force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos', f'fps={profile.fps}']
            args += ['-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-crf', '18', '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-level', profile.level, '-vf', ','.join(filters), '-force_key_frames', 'expr:gte(t,n_forced*2)', '-sc_threshold', '0']
        audio_delay = max(-1., min(1., float(audio_delay)))
        if audio_delay:
            args += ['-af', f'asetpts=PTS+({audio_delay})/TB']
        if profile.copy_audio and not audio_delay:
            args += ['-c:a', 'copy']
        else:
            args += ['-c:a', 'aac', '-ac', str(max(1, profile.channels)), '-ar', '48000', '-b:a', '512k' if profile.channels > 2 else '320k']
        suffix = 'm4s' if profile.fmp4 else 'ts'
        args += ['-f', 'hls', '-hls_time', '2', '-hls_list_size', '0', '-hls_playlist_type', 'event', '-hls_flags', 'temp_file+independent_segments']
        if profile.fmp4:
            args += ['-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4']
        args += ['-hls_segment_filename', str(self.folder / ('segment%06d.' + suffix)), str(self.playlist)]
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.error_thread = threading.Thread(target=self._errors, daemon=True)
        self.governor = threading.Thread(target=self._govern, daemon=True)
        self.error_thread.start()
        self.governor.start()

    def _errors(self):
        for line in self.process.stderr:
            self.errors.append(line.strip())
            del self.errors[:-12]

    def _read_window(self):
        try:
            text = self.playlist.read_text()
        except OSError:
            return self.window
        entries = re.findall(r'#EXTINF:([\d.]+),[^\n]*\nsegment(\d+)\.(?:ts|m4s)', text)
        if not entries:
            return self.window
        for duration, index in entries:
            self.durations[int(index)] = float(duration)
        first = int(entries[0][1])
        start = sum(d for i, d in self.durations.items() if i < first)
        end = start + sum(float(d) for d, _ in entries)
        self.window = (start, end)
        return self.window

    def _govern(self):
        while not self.closed.wait(.1):
            _, end = self._read_window()
            head = self.playhead()
            position, starting = head if isinstance(head, tuple) else (head or 0, head is None)
            ahead = end - max(0, position)
            # A rolling HLS receiver may begin near the live edge and require
            # several target-duration segments before reporting PLAYING.
            # Do not deadlock startup by throttling against an unstarted clock.
            segment_duration = max(self.durations.values(), default=2)
            high_water = max(24, segment_duration * 6) if starting else max(24, segment_duration * 4)
            low_water = high_water - max(12, segment_duration * 2)
            with self.lock:
                if self.process.poll() is not None:
                    return
                with contextlib.suppress(ProcessLookupError):
                    if not self.suspended and ahead >= high_water:
                        self.process.send_signal(signal.SIGSTOP)
                        self.suspended = True
                    elif self.suspended and ahead < low_water:
                        self.process.send_signal(signal.SIGCONT)
                        self.suspended = False

    def wait_ready(self, cancel, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel.is_set() or self.closed.is_set():
                raise InterruptedError('Cancelled')
            _, end = self.window
            code = self.process.poll()
            if end >= 6 or (code == 0 and end > 0):
                return end
            if code is not None:
                raise RuntimeError('Live encoder failed: ' + ('; '.join(self.errors)[-400:] or str(code)))
            cancel.wait(.1)
        raise RuntimeError('Live encoder could not produce the initial buffer in time')

    def close(self):
        self.closed.set()
        with self.lock:
            if self.process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.send_signal(signal.SIGCONT)
                    self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.governor.join(timeout=2)
        self.error_thread.join(timeout=2)
        self.process.stderr.close()
        shutil.rmtree(self.folder, ignore_errors=True)
