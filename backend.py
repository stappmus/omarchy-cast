#!/usr/bin/python
"""JSON-lines Cast worker. Only explicitly selected files are served."""
from __future__ import annotations
import contextlib
import http.server
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import signal
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
        super().__init__(address, Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def share(self, path, mime=None):
        key = '/' + uuid.uuid4().hex + Path(path).suffix
        self.files[key] = (Path(path), mime or mimetypes.guess_type(path)[0] or 'video/mp4')
        host, port = self.server_address
        return f'http://{host}:{port}{key}'


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
        item = self.server.files.get(urlsplit(self.path).path)
        if not item:
            self.send_error(404)
            return
        path, mime = item
        try:
            with path.open('rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                try:
                    start, end, partial = byte_range(self.headers.get('Range'), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', mime)
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

    def emit(self, event, **values):
        with self.lock:
            print(json.dumps(dict(event=event, **values)), flush=True)

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

    def stop(self):
        if self.cast:
            with contextlib.suppress(Exception):
                if self.url and self.cast.media_controller.status.content_id == self.url:
                    self.cast.media_controller.stop(timeout=3)
            with contextlib.suppress(Exception):
                self.cast.disconnect(timeout=3)
            self.cast = None
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.url = ''
        for path in Path(self.tmp.name).iterdir():
            path.unlink(missing_ok=True)

    def play(self, request):
        source = request.get('source', '').strip()
        remote = urlsplit(source).scheme in ('http', 'https')
        if not remote and not Path(source).is_file():
            raise ValueError('Select an existing video file or an HTTP(S) video URL')
        self.stop()
        host = request.get('host', '').strip()
        if host:
            # Discovery with a known host also obtains the receiver UUID and model.
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
                raise ValueError('Scan and select a Chromecast, or enter its IP address')
            self.cast = pychromecast.get_chromecast_from_host((info.host, info.port, info.uuid, info.model_name, info.friendly_name), tries=2, timeout=5)
        self.cast.wait(timeout=10)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((self.cast.cast_info.host, self.cast.cast_info.port))
            local_ip = route.getsockname()[0]
        self.server = MediaServer((local_ip, int(os.environ.get("OMARCHY_CAST_PORT", "49786"))))
        subs_url = None
        subtitle = request.get('subtitle', 'none')
        external = request.get('external', '').strip()
        if subtitle != 'none':
            out = Path(self.tmp.name) / 'subtitles.vtt'
            if subtitle == 'external':
                if not Path(external).is_file():
                    raise ValueError('Choose a subtitle file')
                args = ['-i', external, '-map', '0:0', '-c:s', 'webvtt', str(out)]
            elif remote:
                raise ValueError('Embedded subtitles require a local video')
            else:
                args = ['-i', source, '-map', f'0:{int(subtitle)}', '-c:s', 'webvtt', str(out)]
            self.run_ffmpeg(args)
            subs_url = self.server.share(out, 'text/vtt; charset=utf-8')
        media_source = source
        if not remote:
            data = probe(source)
            streams = data.get('streams', [])
            video = next((s for s in streams if s['codec_type'] == 'video'), {})
            audio = next((s for s in streams if s['codec_type'] == 'audio'), {})
            selected = request.get('audio', 'default')
            mode = request.get('mode', 'auto')
            compatible = Path(source).suffix.lower() in ('.mp4', '.m4v') and video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p' and audio.get('codec_name', 'aac') in ('aac', 'mp3')
            convert = mode == 'convert' or (mode == 'auto' and (not compatible or selected != 'default' or request.get('quality', 'original') != 'original'))
            if mode == 'direct' and selected != 'default':
                raise ValueError('Use Auto or Compatibility mode to change audio tracks')
            if convert:
                self.emit('message', message='Preparing a seekable MP4. This may take a few minutes…')
                out = Path(self.tmp.name) / 'video.mp4'
                copy_video = mode == 'auto' and video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p' and request.get('quality', 'original') == 'original'
                video_args = ['-c:v', 'copy'] if copy_video else ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-pix_fmt', 'yuv420p']
                args = ['-i', source, '-map', '0:v:0', '-map', '0:a:0?' if selected == 'default' else f'0:{int(selected)}', '-sn', *video_args, '-c:a', 'aac', '-ac', '2', '-b:a', '192k', '-movflags', '+faststart']
                quality = request.get('quality', 'original')
                if quality in ('720', '1080'):
                    args += ['-vf', rf"scale=-2:trunc(min(ih\,{quality})/2)*2"]
                args += [str(out)]
                self.run_ffmpeg(args, float(data.get('format', {}).get('duration', 0)))
                media_source = str(out)
            self.url = self.server.share(media_source)
        else:
            self.url = source
        if self.cancel.is_set():
            raise ValueError('Preparation cancelled')
        title = Path(urlsplit(source).path).name or 'Video'
        self.cast.media_controller.play_media(self.url, mimetypes.guess_type(urlsplit(media_source).path)[0] or 'video/mp4', title=title, stream_type='BUFFERED', current_time=max(0, float(request.get('start', 0))), subtitles=subs_url, subtitles_lang=request.get('language', 'en') or 'en', media_info={'textTrackStyle': {'fontScale': float(request.get('subtitleSize', 1)), 'backgroundColor': '#00000080', 'foregroundColor': '#FFFFFFFF', 'edgeType': 'OUTLINE'}})
        self.cast.media_controller.block_until_active(timeout=15)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = self.cast.media_controller.status
            if status.content_id == self.url and status.player_state in ('PLAYING', 'PAUSED', 'BUFFERING'):
                self.emit('message', message='Casting to ' + self.cast.cast_info.friendly_name)
                return
            if self.cancel.wait(0.25):
                raise ValueError('Cast cancelled')
        raise ValueError('Receiver did not start playback. Try Compatibility mode and check that the TV can reach this computer on the local network.')

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
        elif self.cast:
            mc = self.cast.media_controller
            if mc.status.content_id != self.url:
                raise ValueError('Another app has taken over this Chromecast')
            if action == 'pause':
                mc.pause() if mc.status.player_is_playing else mc.play()
            elif action == 'seek':
                target = float(r.get('value', 0))
                if r.get('relative'):
                    target += mc.status.adjusted_current_time
                mc.seek(max(0, min(target, mc.status.duration or target)))
            elif action == 'volume':
                self.cast.set_volume(max(0, min(1, float(r['value']))))
            elif action == 'mute':
                self.cast.set_volume_muted(not self.cast.status.volume_muted)
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
        try:
            while not self.quit.is_set():
                try:
                    r = self.commands.get(timeout=1)
                except queue.Empty:
                    if self.cast:
                        s = self.cast.media_controller.status
                        own = s.content_id == self.url
                        self.emit('status', state=s.player_state if own else 'TAKEN_OVER', title=s.title or '', position=s.adjusted_current_time, duration=s.duration or 0, volume=self.cast.status.volume_level, muted=self.cast.status.volume_muted, connected=own, device=self.cast.cast_info.friendly_name)
                    continue
                self.cancel.clear()
                self.emit('busy', busy=True)
                try:
                    self.command(r)
                except Exception as error:
                    self.emit('error', message=str(error))
                finally:
                    self.emit('busy', busy=False)
                    if not self.cast:
                        self.emit('status', state='IDLE', connected=False, position=0, duration=0)
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
