import array
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import probe
from compatibility import choose_plan
from streaming import LiveStream, PlaybackEvidence, shift_vtt


class EvidenceTests(unittest.TestCase):
    def test_only_actual_progress_counts(self):
        evidence=PlaybackEvidence()
        self.assertEqual(evidence.observe('PLAYING',0),'waiting')
        self.assertEqual(evidence.observe('PLAYING',0),'waiting')
        self.assertEqual(evidence.observe('BUFFERING',1),'waiting')
        self.assertEqual(evidence.observe('PLAYING',1),'playing')

    def test_interruption_is_not_codec_failure(self):
        for reason in ('CANCELLED','INTERRUPTED','FINISHED',None):
            self.assertEqual(PlaybackEvidence().observe('IDLE',0,reason),'waiting')
        self.assertEqual(PlaybackEvidence().observe('IDLE',0,'ERROR'),'failed')

    def test_subtitles_clip_and_shift(self):
        text='WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nold\n\n00:00:01.000 --> 00:00:04.000\nvisible\n'
        shifted=shift_vtt(text,2)
        self.assertNotIn('old',shifted)
        self.assertIn('00:00:00.000 --> 00:00:02.000\nvisible',shifted)

    def test_finished_encoder_refreshes_playlist_before_failing(self):
        live=LiveStream.__new__(LiveStream)
        live.closed=threading.Event(); live.process=Mock(); live.process.poll.return_value=0
        live.window=(0,0); live._read_window=Mock(return_value=(0,2)); live.errors=[]
        self.assertEqual(live.wait_ready(threading.Event(),timeout=1),2)


class RealStreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.source=Path(cls.tmp.name)/'pulse.mkv'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=320x240:r=24:d=6',
            '-f','lavfi','-i','aevalsrc=if(between(t\\,3\\,3.02)\\,0.8\\,0):s=48000:d=6',
            '-c:v','libx264','-g','48','-pix_fmt','yuv420p','-c:a','pcm_s16le','-y',str(cls.source)],check=True)
        cls.metadata=probe(cls.source)
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def test_audio_shift_changes_decoded_samples_after_seek(self):
        for delay in (-1,0,1):
            with self.subTest(delay=delay):
                plan=choose_plan('Google TV Streamer',str(self.source),self.metadata,{'mode':'convert','audioDelay':delay})
                live=LiveStream(Path(self.tmp.name)/f'delay{delay}',self.source,plan,offset=1,audio_delay=delay)
                try:
                    live.wait_ready(threading.Event()); live.process.wait(timeout=10)
                    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(live.playlist),'-map','0:a:0','-ar','48000','-ac','1','-f','f32le','-'])
                    samples=array.array('f',raw)
                    pulse=next(i/48000 for i,v in enumerate(samples) if abs(v)>.25)
                    self.assertAlmostEqual(pulse,2+delay,delta=.08)
                    text=live.playlist.read_text()
                    self.assertIn('#EXT-X-PLAYLIST-TYPE:EVENT',text)
                    self.assertIn('#EXT-X-ENDLIST',text)
                finally:
                    live.close()
                self.assertIsNotNone(live.process.poll())
                self.assertFalse(live.folder.exists())

    def test_suspended_encoder_dies_with_parent(self):
        code="""import os,signal,time
from streaming import spawn_encoder
p=spawn_encoder(['ffmpeg','-v','error','-re','-f','lavfi','-i','anullsrc','-f','null','-'])
time.sleep(.3)
os.kill(p.pid,signal.SIGSTOP)
print(p.pid,flush=True)
time.sleep(60)
"""
        parent=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,text=True)
        child=None
        try:
            child=int(parent.stdout.readline())
            parent.kill(); parent.wait(timeout=3)
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                path=Path(f'/proc/{child}/stat')
                if not path.exists() or path.read_text().split()[2]=='Z': break
                time.sleep(.05)
            else: self.fail('Suspended encoder survived its parent')
        finally:
            if parent.poll() is None: parent.kill(); parent.wait()
            parent.stdout.close()
            if child and Path(f'/proc/{child}').exists():
                try: os.kill(child,signal.SIGKILL)
                except ProcessLookupError: pass
