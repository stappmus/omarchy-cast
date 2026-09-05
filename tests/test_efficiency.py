import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import subprocess
import sys
import time
import threading
import unittest
from unittest.mock import Mock, patch
from backend import Worker
from streaming import LiveStream


class EfficiencyTests(unittest.TestCase):
    def test_unchanged_playlist_is_not_reread_and_atomic_updates_are_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            stream=LiveStream.__new__(LiveStream)
            stream.playlist=Path(folder)/'index.m3u8'
            stream.window_lock=threading.Lock(); stream.window=(0,0)
            stream.durations={}; stream.playlist_signature=None; stream.segment_duration=2
            stream.playlist.write_text('#EXTM3U\n#EXTINF:2.0,\nsegment000000.ts\n')
            self.assertEqual(stream._read_window(),(0,2))
            with patch.object(Path,'read_text',side_effect=AssertionError('Unchanged playlist read again')):
                self.assertEqual(stream._read_window(),(0,2))
            replacement=Path(folder)/'new'
            replacement.write_text('#EXTM3U\n#EXTINF:3.0,\nsegment000000.ts\n')
            replacement.replace(stream.playlist)
            self.assertEqual(stream._read_window(),(0,3))
            self.assertEqual(stream.segment_duration,3)

    def test_probe_cache_invalidates_when_file_changes(self):
        w=Worker()
        try:
            source=Path(w.tmp.name)/'movie'; source.write_bytes(b'a')
            with patch('backend.probe',side_effect=[{'version':1},{'version':2}]) as probe:
                self.assertEqual(w.metadata_for(source),{'version':1})
                self.assertEqual(w.metadata_for(source),{'version':1})
                source.write_bytes(b'changed')
                self.assertEqual(w.metadata_for(source),{'version':2})
                self.assertEqual(probe.call_count,2)
        finally: w.tmp.cleanup()

    def test_identical_status_is_not_emitted_again(self):
        w=Worker(); out=io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                w.emit('status',state='IDLE'); w.emit('status',state='IDLE')
                w.emit('status',state='PLAYING')
            self.assertEqual(len(out.getvalue().splitlines()),2)
        finally: w.tmp.cleanup()

    def test_cleanup_waits_for_grace_and_preserves_paused_movie(self):
        w=Worker(); w.cast=Mock(); w.url='owned'; w.stop=Mock()
        status=SimpleNamespace(content_id='owned',player_state='PAUSED',idle_reason=None)
        w.cast.media_controller.status=status
        try:
            w.release_inactive_session(0); w.release_inactive_session(100)
            w.stop.assert_not_called()
            status.content_id='another-app'
            w.release_inactive_session(100); w.release_inactive_session(109)
            w.stop.assert_not_called()
            status.content_id='owned'
            w.release_inactive_session(110)
            status.player_state='IDLE'; status.idle_reason='FINISHED'
            w.release_inactive_session(120); w.release_inactive_session(130)
            w.stop.assert_called_once()
        finally: w.tmp.cleanup()

    def test_idle_worker_exits_promptly_on_input_close(self):
        worker=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'backend.py')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertIn('ready',worker.stdout.readline())
            self.assertIn('status',worker.stdout.readline())
            time.sleep(.05)
            worker.communicate(timeout=2)
            self.assertEqual(worker.returncode,0)
        finally:
            if worker.poll() is None:
                worker.kill(); worker.communicate()
