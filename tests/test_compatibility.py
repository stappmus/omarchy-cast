import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compatibility import choose_plan, device_profile


def media(codec='h264', height=1080, fps=30, level=41, audio='aac'):
    return {'streams': [dict(index=0, codec_type='video', codec_name=codec,
        width=height*16//9, height=height, avg_frame_rate=str(fps), level=level, pix_fmt='yuv420p'),
        dict(index=1, codec_type='audio', codec_name=audio, channels=2, sample_rate='48000')]}


class ProfileTests(unittest.TestCase):
    def plan(self, model='Chromecast', data=None, source='movie.mp4', **request):
        return choose_plan(model, source, data or media(), request)

    def test_codec_specific_limits(self):
        cases = [
            ('Chromecast', 'h264', 1080, 60, 42, False),
            ('Chromecast 3', 'h264', 1080, 60, 42, True),
            ('Chromecast Ultra', 'h264', 2160, 30, 51, False),
            ('Chromecast Ultra', 'hevc', 2160, 60, 153, True),
            ('Chromecast with Google TV', 'h264', 2160, 60, 52, False),
            ('Chromecast with Google TV', 'hevc', 2160, 60, 153, True),
            ('Google TV Streamer', 'h264', 2160, 60, 52, True),
            ('Google TV Streamer', 'av1', 2160, 60, 13, True),
            ('Google TV Streamer', 'av1', 2160, 60, 14, False),
            ('Google Nest Hub', 'h264', 720, 60, 41, True),
            ('Google Nest Hub Max', 'h264', 720, 60, 41, False),
        ]
        for model, codec, height, fps, level, expected in cases:
            with self.subTest(model=model, codec=codec, fps=fps):
                self.assertEqual(self.plan(model, media(codec,height,fps,level)).copy_video, expected)

    def test_hevc_never_mpeg_transport_stream(self):
        plan = self.plan('Chromecast Ultra', media('hevc',2160,60,153,'dts'), source='movie.mkv')
        self.assertTrue(plan.copy_video); self.assertTrue(plan.fmp4)
        self.assertFalse(plan.copy_audio); self.assertEqual(plan.transport, 'hls')

    def test_webm_vp8_preserved_on_legacy(self):
        self.assertEqual(self.plan(data=media('vp8',720,60,0,'vorbis'), source='movie.webm').transport, 'direct')

    def test_vp9_reencoded_only_when_container_change_needed(self):
        data = media('vp9',2160,60,51,'opus')
        self.assertEqual(self.plan('Chromecast Ultra',data,source='movie.webm').transport,'direct')
        self.assertFalse(self.plan('Chromecast Ultra',data,source='movie.webm',audioDelay=.5).copy_video)

    def test_vp9_without_level_metadata(self):
        self.assertEqual(self.plan('Google TV Streamer',media('vp9',1080,30,-99,'opus'),source='movie.webm').transport,'direct')

    def test_unknown_device_and_manual_override(self):
        data=media('h264',1080,60,42)
        self.assertFalse(self.plan('Living Room TV',data).copy_video)
        self.assertTrue(self.plan('Living Room TV',data,deviceProfile='chromecast3').copy_video)

    def test_audio_only(self):
        for model in ('Chromecast Audio','Google Home','Nest Audio','Google Cast Group'):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError,'audio-only'):
                self.plan(model)
        self.assertEqual(device_profile('Google Home Hub').name,'Google Nest Hub')

    def test_default_audio_and_cover_art_are_selected_consistently(self):
        data = media()
        data['streams'].insert(0,dict(index=4,codec_type='video', disposition={'attached_pic':1}))
        data['streams'].append(dict(index=2,codec_type='audio',codec_name='dts',channels=6,sample_rate='48000',disposition={'default':1}))
        plan=self.plan(data=data)
        self.assertEqual(plan.video_index,0); self.assertEqual(plan.audio_index,2)
        self.assertEqual(plan.transport,'hls'); self.assertFalse(plan.copy_audio)
        self.assertEqual(self.plan(data=data,audio='1').audio_index,1)

    def test_quality_and_delay_validation(self):
        self.assertFalse(self.plan('Google TV Streamer',media(height=2160,level=51),quality='1080').copy_video)
        for delay in (float('nan'),float('inf'),1.1,-1.1):
            with self.assertRaises(ValueError): self.plan(audioDelay=delay)

    def test_surround_requires_opt_in(self):
        data=media(); data['streams'][1]['channels']=6
        self.assertFalse(self.plan(data=data).copy_audio)
        self.assertTrue(self.plan(data=data,sound='surround').copy_audio)
