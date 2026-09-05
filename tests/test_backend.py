import contextlib
import http.client
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import MediaServer, Worker, byte_range

class RangeTests(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(byte_range(None, 10), (0, 9, False))
        self.assertEqual(byte_range('bytes=2-4', 10), (2, 4, True))
        self.assertEqual(byte_range('bytes=7-', 10), (7, 9, True))
        self.assertEqual(byte_range('bytes=-3', 10), (7, 9, True))
        self.assertEqual(byte_range('bytes=0-99', 10), (0, 9, True))
        for header in ['bytes=10-', 'bytes=5-2', 'bytes=-0', 'bytes=-', 'bytes=0-1,4-5', 'oops']:
            with self.assertRaises(ValueError): byte_range(header, 10)

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'movie.mp4'
        self.path.write_bytes(b'0123456789')
        self.server = MediaServer(('127.0.0.1', 0))
        self.url = self.server.share(self.path)
        self.route = '/' + self.url.rsplit('/', 1)[1]
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()
    def request(self, method, path, headers=None):
        c = http.client.HTTPConnection(*self.server.server_address)
        c.request(method, path, headers=headers or {})
        r = c.getresponse(); data = r.read(); status = r.status; headers = dict(r.getheaders()); c.close()
        return status, headers, data
    def test_full_and_seek(self):
        status, headers, body = self.request('GET', self.route)
        self.assertEqual((status, body), (200, b'0123456789'))
        self.assertEqual(headers['Access-Control-Allow-Origin'], '*')
        status, headers, body = self.request('GET', self.route, {'Range': 'bytes=3-6'})
        self.assertEqual((status, body), (206, b'3456'))
        self.assertEqual(headers['Content-Range'], 'bytes 3-6/10')
    def test_head_and_invalid(self):
        status, headers, body = self.request('HEAD', self.route)
        self.assertEqual((status, body, headers['Content-Length']), (200, b'', '10'))
        self.assertEqual(self.request('GET', self.route, {'Range': 'bytes=100-'})[0], 416)
    def test_no_unselected_files(self):
        for path in ['/', '/movie.mp4', '/../etc/passwd', '/%2e%2e/etc/passwd']:
            self.assertEqual(self.request('GET', path)[0], 404)
    def test_subtitle_mime_and_cors(self):
        url = self.server.share(self.path, 'text/vtt; charset=utf-8')
        _, headers, _ = self.request('GET', '/' + url.rsplit('/', 1)[1])
        self.assertEqual(headers['Content-Type'], 'text/vtt; charset=utf-8')
        self.assertEqual(self.request('OPTIONS', self.route)[0], 204)

class OwnershipTests(unittest.TestCase):
    def test_stop_does_not_interrupt_other_app(self):
        w = Worker(); cast = Mock(); w.cast = cast; w.url = 'our-video'
        cast.media_controller.status.content_id = 'another-video'
        w.stop()
        cast.media_controller.stop.assert_not_called()
        cast.disconnect.assert_called_once()
        w.tmp.cleanup()
    def test_stop_owned_video(self):
        w = Worker(); cast = Mock(); w.cast = cast; w.url = 'our-video'
        cast.media_controller.status.content_id = 'our-video'
        w.stop()
        cast.media_controller.stop.assert_called_once()
        w.tmp.cleanup()


class AudioAdjustmentTests(unittest.TestCase):
    def test_adjustment_preserves_position_pause_and_tracks(self):
        from types import SimpleNamespace
        from test_compatibility import media
        for paused in (False, True):
            with self.subTest(paused=paused):
                w=Worker(); w.emit=Mock(); w.cast=Mock(); w.url='owned'
                w.cast.cast_info.model_name='Google TV Streamer'
                w.cast.media_controller.status=SimpleNamespace(content_id='owned', adjusted_current_time=42.,
                    player_state='PAUSED' if paused else 'PLAYING', player_is_paused=paused, title='test')
                w.offset=30; w.duration=200; w.metadata=media()
                w.request=dict(source='movie.mp4',mode='direct',audio='default',subtitle='3')
                w.start_media=Mock()
                try:
                    w.command(dict(action='audio_delay',value=-.5))
                    w.start_media.assert_called_once_with(72.,paused=paused)
                    self.assertEqual(w.request['subtitle'],'3')
                    self.assertEqual(w.request['audioDelay'],-.5)
                    self.assertEqual(w.plan.transport,'hls')
                    self.assertFalse(w.plan.copy_audio)
                finally: w.stop(); w.tmp.cleanup()


class ConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess
        cls.folder = tempfile.TemporaryDirectory()
        cls.srt = Path(cls.folder.name) / 'sample.srt'
        cls.srt.write_text('1\n00:00:00,000 --> 00:00:01,500\nOmarchy Cast subtitle test\n')
        cls.video = Path(cls.folder.name) / 'two tracks.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=24:d=2', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-f', 'lavfi', '-i', 'sine=frequency=880:duration=2', '-i', str(cls.srt), '-map', '0', '-map', '1', '-map', '2', '-map', '3', '-c:v', 'mpeg4', '-c:a', 'aac', '-c:s', 'srt', '-y', str(cls.video)], check=True)
    @classmethod
    def tearDownClass(cls): cls.folder.cleanup()
    def test_real_conversion_audio_selection_and_embedded_subtitle(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from backend import probe
        w = Worker(); events = []; w.emit = lambda event, **data: events.append((event, data))
        info = SimpleNamespace(host='127.0.0.1', port=8009, uuid='test', model_name='Test', friendly_name='Test')
        w.devices['test'] = info
        cast = Mock(); cast.cast_info = info
        cast.status.volume_level = 1; cast.status.volume_muted = False
        cast.media_controller.status = SimpleNamespace(content_id='', player_state='IDLE', current_time=0., adjusted_current_time=0., idle_reason=None, title='test', duration=2.)
        def update():
            cast.media_controller.status.current_time += .3
            cast.media_controller.status.adjusted_current_time += .3
        cast.media_controller.update_status.side_effect = update
        def load(url, *_args, **_kw):
            cast.media_controller.status.content_id = url
            cast.media_controller.status.player_state = 'PLAYING'
        cast.media_controller.play_media.side_effect = load
        try:
            with patch('backend.pychromecast.get_chromecast_from_host', return_value=cast):
                w.play({'source': str(self.video), 'device': 'test', 'audio': '2', 'subtitle': '3', 'mode': 'convert', 'quality': '720'})
            out = w.live.playlist
            data = probe(out)
            self.assertEqual([s['codec_name'] for s in data['streams']], ['h264', 'aac'])
            self.assertIn('Omarchy Cast subtitle test', (Path(w.tmp.name) / 'subtitles.vtt').read_text())
            call = cast.media_controller.play_media.call_args
            self.assertEqual(call.kwargs['stream_type'], 'BUFFERED')
            self.assertTrue(call.kwargs['subtitles'].endswith('.vtt'))
            self.assertTrue(any(e == 'phase' and d['phase'] == 'playing' for e, d in events))
            # Verify the chosen 880 Hz audio track made it through the encode.
            import subprocess, array
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(out), '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'])
            samples = array.array('f', raw)[800:8800]
            crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
            self.assertGreater(crossings, 850)
            self.assertLess(crossings, 910)
        finally:
            w.stop(); w.tmp.cleanup()
    def test_external_srt_conversion(self):
        w = Worker(); w.emit = Mock()
        try:
            out = Path(w.tmp.name) / 'subtitles.vtt'
            w.run_ffmpeg(['-i', str(self.srt), '-c:s', 'webvtt', str(out)])
            self.assertTrue(out.read_text().startswith('WEBVTT'))
        finally: w.tmp.cleanup()
    def test_cancel_before_conversion(self):
        w = Worker(); w.cancel.set()
        try:
            with self.assertRaisesRegex(ValueError, 'cancelled'): w.run_ffmpeg([])
        finally: w.tmp.cleanup()

class PickerTests(unittest.TestCase):
    def test_picker_emits_selected_path_and_kind(self):
        import io,json
        from unittest.mock import patch
        w = Worker()
        try:
            out = io.StringIO()
            with patch('backend.subprocess.run', return_value=Mock(returncode=0, stdout='/tmp/a video.mkv\n')), contextlib.redirect_stdout(out):
                w.command({'action': 'pick', 'kind': 'video'})
            self.assertEqual([json.loads(line) for line in out.getvalue().splitlines()], [{'event': 'picked', 'kind': 'video', 'path': '/tmp/a video.mkv'}, {'event': 'pickerClosed'}])
        finally: w.tmp.cleanup()

if __name__ == '__main__': unittest.main()
