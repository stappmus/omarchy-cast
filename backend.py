#!/usr/bin/python
"""JSON-lines Cast worker. Only explicitly selected files are served."""
from __future__ import annotations
import contextlib
import http.server
import json
import mimetypes
import math
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit
import pychromecast
from pychromecast.discovery import CastBrowser, SimpleCastListener, stop_discovery
from zeroconf import Zeroconf
from compatibility import choose_plan
from streaming import LiveStream, PlaybackEvidence, shift_vtt


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Cannot read this video')
    return json.loads(result.stdout)


def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not match or not any(match.groups()):
        raise ValueError('Invalid range')
    start, end = match.groups()
    if not start:
        start, end = max(0, size - int(end)), size - 1
    else:
        start, end = int(start), min(int(end), size - 1) if end else size - 1
    if start > end or start >= size:
        raise ValueError('Unsatisfiable range')
    return start, end, True


class MediaServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address):
        self.files = {}
        self.mounts = {}
        self.requests = {}
        super().__init__(address, Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def share(self, path, mime=None):
        key = '/' + uuid.uuid4().hex + Path(path).suffix
        self.files[key] = (Path(path), mime or mimetypes.guess_type(path)[0] or 'video/mp4')
        host, port = self.server_address
        return f'http://{host}:{port}{key}'

    def mount(self, folder):
        key = '/' + uuid.uuid4().hex
        self.mounts[key] = Path(folder)
        host, port = self.server_address
        return f'http://{host}:{port}{key}/index.m3u8'

    def resolve(self, route):
        if route in self.files:
            return self.files[route]
        prefix, _, name = route.rpartition('/')
        folder = self.mounts.get(prefix)
        if folder and re.fullmatch(r'(?:index\.m3u8|init\.mp4|segment\d+\.(?:ts|m4s))', name):
            mime = {'m3u8': 'application/vnd.apple.mpegurl', 'ts': 'video/mp2t', 'm4s': 'video/iso.segment', 'mp4': 'video/mp4'}[name.rsplit('.', 1)[1]]
            return folder / name, mime
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Range, Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.end_headers()

    def do_HEAD(self):
        self.serve(False)

    def do_GET(self):
        self.serve(True)

    def serve(self, body):
        route = urlsplit(self.path).path
        item = self.server.resolve(route)
        if not item:
            self.send_error(404)
            return
        path, mime = item
        try:
            with path.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                if body:
                    self.server.requests[route] = self.server.requests.get(route, 0) + 1
                try:
                    start, end, partial = byte_range(self.headers.get('Range'), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', mime)
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Content-Length', str(max(0, end - start + 1)))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Expose-Headers', 'Content-Range, Accept-Ranges')
                if partial:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                if body:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        data = stream.read(min(256 * 1024, remaining))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
        except FileNotFoundError:
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            self.close_connection = True


class Worker:
    def __init__(self):
        self.lock = threading.Lock()
        self.commands = queue.Queue()
        self.quit = threading.Event()
        self.cancel = threading.Event()
        self.cast = None
        self.server = None
        self.url = ''
        self.busy = False
        self.devices = {}
        cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'omarchy-cast'
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='session-', dir=cache)
        self.process = None
        self.live = None
        self.offset = 0.
        self.duration = 0.
        self.plan = None
        self.request = {}
        self.subtitle_text = ''
        self.load_error = None
        self.phase_name = 'idle'
        self.last_diagnostic = {}

    def new_media_status(self, status):
        pass

    def load_media_failed(self, queue_item_id, error_code):
        self.load_error = f'The TV rejected playback (code {error_code})'

    def phase(self, name, message):
        self.phase_name = name
        self.emit('phase', phase=name, message=message)

    def snapshot(self):
        if not self.cast:
            return dict(state='IDLE', connected=False, position=0, duration=0)
        s = self.cast.media_controller.status
        own = bool(self.url and s.content_id == self.url)
        start, end = self.live.window if self.live else (0, self.duration)
        position = self.offset + float(s.adjusted_current_time or 0) if own else 0
        return dict(state=s.player_state if own else 'TAKEN_OVER', title=s.title or '',
                    position=min(position, self.duration) if self.duration else position,
                    duration=self.duration or s.duration or 0, volume=self.cast.status.volume_level,
                    muted=self.cast.status.volume_muted, connected=own,
                    device=self.cast.cast_info.friendly_name, live=bool(self.live),
                    bufferedStart=self.offset + start, bufferedEnd=self.offset + end,
                    profile=self.plan.description if self.plan else '', phase=self.phase_name,
                    audioDelay=self.request.get('audioDelay', 0))

    def emit(self, event, **values):
        with self.lock:
            print(json.dumps(dict(event=event, **values)), flush=True)
            if event in ('error', 'diagnostic'):
                print('Cast diagnostic: ' + json.dumps(dict(event=event, **values)), file=sys.stderr, flush=True)

    def run_ffmpeg(self, args, duration=0):
        if self.cancel.is_set():
            raise ValueError('Preparation cancelled')
        self.process = subprocess.Popen(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *args, '-progress', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Drain stderr separately so a malformed file cannot block the encoder.
        errors = []
        def drain():
            for line in self.process.stderr:
                errors.append(line)
                del errors[:-20]
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        if self.cancel.is_set():
            self.process.terminate()
        for line in self.process.stdout:
            if line.startswith('out_time_us=') and duration:
                with contextlib.suppress(ValueError):
                    self.emit('progress', percent=min(99, int(int(line.split('=')[1]) / 10000 / duration)))
        code = self.process.wait()
        reader.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()
        self.process = None
        if self.cancel.is_set():
            raise ValueError('Preparation cancelled')
        if code:
            raise ValueError('Conversion failed: ' + ''.join(errors)[-500:])

    def discover(self):
        browser = CastBrowser(SimpleCastListener(), Zeroconf())
        browser.start_discovery()
        try:
            self.cancel.wait(8)
            infos = list(browser.devices.values())
            self.devices = {str(c.uuid): c for c in infos}
            self.emit('devices', devices=[{'value': str(c.uuid), 'label': c.friendly_name + ' · ' + (c.model_name or 'Google Cast')} for c in sorted(infos, key=lambda c: c.friendly_name)])
        finally:
            stop_discovery(browser)

    def inspect(self, path):
        data = probe(path)
        streams = data.get('streams', [])
        def options(kind):
            return [{'value': str(s['index']), 'label': f"{s['index']}: {s.get('tags', {}).get('title', s.get('tags', {}).get('language', 'Unknown language'))} · {s.get('codec_name', '?')}"} for s in streams if s['codec_type'] == kind]
        subs = options('subtitle')
        # Image subtitle formats cannot be converted to WebVTT without OCR.
        supported = {str(s['index']) for s in streams if s.get('codec_name') in ('subrip', 'ass', 'ssa', 'webvtt', 'mov_text', 'text')}
        self.emit('tracks', audio=options('audio'), subtitles=[s for s in subs if s['value'] in supported], duration=float(data.get('format', {}).get('duration', 0)), omittedSubtitles=len(subs) - len(supported))

    def release_live(self):
        if self.live:
            stream = self.live
            self.live = None
            if self.server:
                for key, folder in list(self.server.mounts.items()):
                    if folder == stream.folder:
                        del self.server.mounts[key]
            stream.close()

    def stop(self):
        if self.cast:
            with contextlib.suppress(Exception):
                if self.url and self.cast.media_controller.status.content_id == self.url:
                    self.cast.media_controller.stop(timeout=3)
            with contextlib.suppress(Exception):
                self.cast.disconnect(timeout=3)
            self.cast = None
        self.release_live()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.url = ''
        self.offset = 0
        self.phase_name = 'idle'
        for path in Path(self.tmp.name).iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def connect(self, request):
        self.phase('connecting', 'Connecting to your TV…')
        host = request.get('host', '').strip()
        if host:
            casts, browser = pychromecast.get_chromecasts(known_hosts=[host], timeout=5, tries=1)
            try:
                chosen = next((c for c in casts if c.cast_info.host == socket.gethostbyname(host)), None)
                for c in casts:
                    if c is not chosen:
                        c.disconnect()
                if not chosen:
                    raise ValueError('No Chromecast found at that address')
                self.cast = chosen
            finally:
                stop_discovery(browser)
        else:
            info = self.devices.get(request.get('device'))
            if not info:
                raise ValueError('Select your TV, or enter its IP address in Options')
            self.cast = pychromecast.get_chromecast_from_host((info.host, info.port, info.uuid, info.model_name, info.friendly_name), tries=2, timeout=5)
        self.cast.wait(timeout=10)
        # A user-requested new cast gets a fresh Default Media Receiver.
        # In particular, do not reuse its audio renderer after a Bluetooth
        # route change. This runs only on Cast, never on pause/seek/resume.
        if self.cast.status.app_id == 'CC1AD845':
            self.phase('connecting', 'Refreshing the TV player…')
            self.cast.quit_app(timeout=5)
        self.cast.media_controller.register_status_listener(self)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((self.cast.cast_info.host, self.cast.cast_info.port))
            local_ip = route.getsockname()[0]
        self.server = MediaServer((local_ip, int(os.environ.get('OMARCHY_CAST_PORT', '49786'))))

    def play(self, request):
        source = request.get('source', '').strip()
        remote = urlsplit(source).scheme in ('http', 'https')
        if not remote and not Path(source).is_file():
            raise ValueError('Select an existing video file or an HTTP(S) video URL')
        self.stop()
        self.request = dict(request, source=source)
        self.connect(request)
        self.phase('compatibility', 'Matching picture and sound to your TV…')
        metadata = probe(source)
        self.metadata = metadata
        self.duration = float(metadata.get('format', {}).get('duration', 0) or 0)
        self.plan = choose_plan(self.cast.cast_info.model_name, source, metadata, request)
        self.emit('profile', **self.plan.json())
        self.subtitle_text = ''
        subtitle = request.get('subtitle', 'none')
        if subtitle != 'none':
            self.phase('subtitles', 'Preparing subtitles…')
            out = Path(self.tmp.name) / 'subtitles.vtt'
            external = request.get('external', '').strip()
            if subtitle == 'external':
                if not Path(external).is_file():
                    raise ValueError('Choose a subtitle file')
                args = ['-i', external, '-map', '0:0', '-c:s', 'webvtt', str(out)]
            elif remote:
                raise ValueError('Use an external subtitle file for video links')
            else:
                args = ['-i', source, '-map', f'0:{int(subtitle)}', '-c:s', 'webvtt', str(out)]
            self.run_ffmpeg(args)
            self.subtitle_text = out.read_text()
        self.start_media(max(0, float(request.get('start', 0))))

    def live_playhead(self):
        if self.cast and self.url:
            status = self.cast.media_controller.status
            if status.content_id == self.url:
                return (float(status.adjusted_current_time or 0), status.player_state not in ('PLAYING', 'PAUSED'))
        return None

    def seek_anchor(self, offset):
        if not offset or not self.plan.copy_video:
            return offset
        # Stream-copy input seeks preserve pre-roll. Align BOTH tracks to an
        # actual video keyframe, then let the receiver seek the small remainder.
        result = subprocess.run(['ffprobe', '-v', 'error', '-read_intervals', f'{max(0, offset - 30)}%{offset + 2}',
                                 '-select_streams', 'v:0', '-skip_frame', 'nokey', '-show_frames',
                                 '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', self.request['source']],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError('Could not locate a clean video seek point')
        frames = json.loads(result.stdout).get('frames', [])
        times = [float(f['best_effort_timestamp_time']) for f in frames if 'best_effort_timestamp_time' in f]
        candidates = [t for t in times if 0 <= t <= offset]
        return max(candidates) if candidates else 0

    def start_media(self, offset, paused=False):
        self.release_live()
        self.offset = self.seek_anchor(offset) if self.plan.transport == 'hls' else 0
        self.load_error = None
        source = self.request['source']
        subtitles_url = None
        if self.subtitle_text:
            path = Path(self.tmp.name) / ('subtitle-' + uuid.uuid4().hex + '.vtt')
            path.write_text(shift_vtt(self.subtitle_text, self.offset))
            subtitles_url = self.server.share(path, 'text/vtt; charset=utf-8')
        if self.plan.transport == 'hls':
            self.phase('buffering', 'Building a small playback buffer…')
            self.url = ''
            self.live = LiveStream(Path(self.tmp.name) / uuid.uuid4().hex, source, self.plan,
                                   self.request.get('audio', 'default'), self.offset, self.live_playhead, self.request.get('audioDelay', 0))
            self.live.wait_ready(self.cancel)
            self.url = self.server.mount(self.live.folder)
            mime = 'application/vnd.apple.mpegurl'
        else:
            self.url = source if urlsplit(source).scheme in ('http', 'https') else self.server.share(source)
            mime = mimetypes.guess_type(urlsplit(source).path)[0] or 'video/mp4'
        if self.cancel.is_set():
            raise InterruptedError('Cancelled')
        self.phase('loading', 'Starting on ' + self.cast.cast_info.friendly_name + '…')
        info = {'textTrackStyle': {'fontScale': float(self.request.get('subtitleSize', 1)), 'backgroundColor': '#00000080', 'foregroundColor': '#FFFFFFFF', 'edgeType': 'OUTLINE'}}
        if self.plan.fmp4 and self.live:
            info.update(hlsVideoSegmentFormat='FMP4', hlsSegmentFormat='FMP4')
        if self.duration:
            info['duration'] = max(0, self.duration - self.offset)
        def loaded(success, response):
            if not success:
                self.load_error = 'The TV could not load this stream'
        mc = self.cast.media_controller
        mc.play_media(self.url, mime, title=Path(urlsplit(source).path).name or 'Video',
                      stream_type='BUFFERED', current_time=max(0, offset - self.offset) if self.live else offset,
                      autoplay=not paused, subtitles=subtitles_url,
                      subtitles_lang=self.request.get('language', 'en') or 'en',
                      media_info=info, callback_function=loaded)
        evidence = PlaybackEvidence()
        deadline = time.monotonic() + 45
        next_poll = 0
        while time.monotonic() < deadline:
            if self.cancel.wait(.15):
                raise InterruptedError('Cancelled')
            if self.load_error:
                raise ValueError(self.load_error + '. Try Convert for this TV in Options.')
            if self.live and self.live.process.poll() not in (None, 0):
                raise ValueError('Live conversion failed: ' + '; '.join(self.live.errors)[-300:])
            if time.monotonic() >= next_poll:
                mc.update_status()
                next_poll = time.monotonic() + .75
            status = mc.status
            if status.content_id != self.url:
                continue
            result = evidence.observe(status.player_state, status.current_time, status.idle_reason)
            if result == 'failed':
                self.emit('diagnostic', state=status.player_state, idleReason=status.idle_reason,
                          position=status.current_time, httpRequests=sum(self.server.requests.values()),
                          requestedFiles=[Path(path).name for path in self.server.requests],
                          buffer=self.live.window if self.live else None)
                raise ValueError(f'The TV reported a playback error ({status.idle_reason}). Select Convert for this TV in Options and retry.')
            if result == 'playing' or (paused and status.player_state == 'PAUSED'):
                self.phase('playing', self.plan.description)
                self.emit('status', **self.snapshot())
                return
        route = urlsplit(self.url).path
        self.last_diagnostic = dict(state=mc.status.player_state, idleReason=mc.status.idle_reason,
                                    position=mc.status.current_time, matches=mc.status.content_id == self.url,
                                    httpRequests=sum(self.server.requests.values()),
                                    requestedFiles=[Path(path).name for path in self.server.requests],
                                    buffer=self.live.window if self.live else None,
                                    encoderPaused=self.live.suspended if self.live else False)
        self.emit('diagnostic', **self.last_diagnostic)
        if not self.server.requests.get(route) and urlsplit(self.url).hostname == self.server.server_address[0]:
            raise ValueError('The TV cannot reach this computer. Allow TCP 49786 from your local network.')
        raise ValueError('The TV did not confirm playback. Try Convert for this TV in Options; quality will not be reduced automatically.')

    def command(self, r):
        action = r.get('action')
        if action == 'scan':
            self.discover()
        elif action == 'inspect':
            self.inspect(r['source'])
        elif action == 'cast':
            try:
                self.play(r)
            except Exception:
                self.stop()
                raise
        elif action in ('stop', 'cancel'):
            self.stop()
            self.emit('message', message='Stopped')
        elif action == 'pick':
            result = subprocess.run(['zenity', '--file-selection', '--title=' + ('Choose subtitles' if r.get('kind') == 'subtitle' else 'Choose video')], capture_output=True, text=True)
            if result.returncode == 0:
                self.emit('picked', kind=r.get('kind'), path=result.stdout.strip())
            self.emit('pickerClosed')
        elif self.cast:
            mc = self.cast.media_controller
            if mc.status.content_id != self.url:
                raise ValueError('Another app has taken over this Chromecast')
            if action == 'pause':
                pause = r.get('paused', mc.status.player_is_playing)
                mc.pause() if pause else mc.play()
            elif action == 'audio_delay':
                delay = float(r['value'])
                if not math.isfinite(delay) or not -1 <= delay <= 1:
                    raise ValueError('Audio delay must be between -1 and 1 seconds')
                if delay == self.request.get('audioDelay', 0):
                    return
                position = self.snapshot()['position']
                paused = mc.status.player_is_paused
                request = dict(self.request, audioDelay=delay)
                if request.get('mode') == 'direct':
                    request['mode'] = 'auto'
                plan = choose_plan(self.cast.cast_info.model_name, request['source'], self.metadata, request)
                self.phase('buffering', 'Adjusting audio…')
                mc.stop(timeout=3)
                self.request = request
                self.plan = plan
                self.emit('profile', **plan.json())
                self.start_media(position, paused=paused)
            elif action == 'seek':
                target = float(r.get('value', 0))
                if r.get('relative'):
                    target += self.offset + mc.status.adjusted_current_time
                target = max(0, min(target, max(0, self.duration - 1) if self.duration else target))
                if self.live:
                    start, end = self.live.window
                    if not (self.offset + start + 1 <= target <= self.offset + end - 2):
                        was_paused = mc.status.player_is_paused
                        mc.stop(timeout=3)
                        self.start_media(target, paused=was_paused)
                    else:
                        mc.seek(target - self.offset)
                else:
                    mc.seek(target)
            elif action == 'volume':
                self.cast.set_volume(max(0, min(1, float(r['value']))))
            elif action == 'mute':
                self.cast.set_volume_muted(r.get('muted', not self.cast.status.volume_muted))
            elif action == 'subtitles':
                mc.enable_subtitle(1) if r.get('enabled') else mc.disable_subtitle()
        else:
            raise ValueError('Start casting first')

    def input(self):
        for line in sys.stdin:
            try:
                r = json.loads(line)
                if r.get('action') in ('cancel', 'stop', 'exit'):
                    self.cancel.set()
                    if self.process:
                        with contextlib.suppress(ProcessLookupError):
                            self.process.terminate()
                if r.get('action') == 'exit':
                    break
                self.commands.put(r)
            except (ValueError, AttributeError):
                self.emit('error', message='Invalid command')
        self.quit.set()
        self.cancel.set()
        if self.process:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()

    def run(self):
        threading.Thread(target=self.input, daemon=True).start()
        self.emit('ready')
        next_status = 0
        try:
            while not self.quit.is_set():
                if time.monotonic() >= next_status:
                    self.emit('status', **self.snapshot())
                    next_status = time.monotonic() + .5
                    if self.live and self.live.process.poll() not in (None, 0):
                        self.emit('error', message='Live encoder stopped: ' + '; '.join(self.live.errors)[-300:])
                        self.stop()
                try:
                    r = self.commands.get(timeout=.2)
                except queue.Empty:
                    continue
                self.cancel.clear()
                busy_action = r.get('action') in ('scan', 'inspect', 'cast', 'pick', 'audio_delay')
                if busy_action:
                    self.emit('busy', busy=True, action=r.get('action'))
                success = True
                try:
                    self.command(r)
                except Exception as error:
                    success = False
                    self.phase_name = 'idle'
                    self.emit('error', message=str(error))
                finally:
                    if busy_action:
                        self.emit('busy', busy=False, action=r.get('action'))
                    self.emit('status', **self.snapshot())
                    self.emit('commandResult', id=r.get('id'), action=r.get('action'), ok=success)
        finally:
            self.stop()
            self.tmp.cleanup()


if __name__ == '__main__':
    worker = Worker()
    def terminate(*_):
        worker.quit.set()
        worker.cancel.set()
        if worker.process:
            with contextlib.suppress(ProcessLookupError):
                worker.process.terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    worker.run()
