"""Conservative sender-side policies; Google TV Streamer is the primary target.

Source: https://developers.google.com/cast/docs/media (2026-09-06).
The receiver's HDMI/speaker capabilities are not exposed by the default receiver.
Stereo AAC is therefore the default for converted audio; surround is opt-in.
"""
from dataclasses import dataclass, asdict
from fractions import Fraction
import math
from pathlib import Path
from urllib.parse import urlsplit

@dataclass(frozen=True)
class Plan:
    device: str
    transport: str
    copy_video: bool
    copy_audio: bool
    height: int
    fps: float
    level: str
    channels: int
    fmp4: bool
    hdr_to_sdr: bool
    description: str

    video_index: int = 0
    audio_index: int | None = None

    def json(self):
        return asdict(self)


@dataclass(frozen=True)
class VideoLimit:
    height: int
    fps: int
    level: int = 0
    low_res_fps: int = 0


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    codecs: dict
    hdr: bool = False


LEGACY = {'h264': VideoLimit(1080, 30, 41, 60), 'vp8': VideoLimit(1080, 30, 0, 60)}
MODERN = {'hevc': VideoLimit(2160, 60, 153), 'vp9': VideoLimit(2160, 60, 51)}
# Limits are per codec, not a single resolution/FPS ceiling for every decoder.
# Source: https://developers.google.com/cast/docs/media (checked 2026-09-06).
PROFILES = {
    'generic': DeviceProfile('Unknown Cast · conservative 1080p', LEGACY),
    'chromecast12': DeviceProfile('Chromecast 1st / 2nd generation', LEGACY),
    'chromecast3': DeviceProfile('Chromecast 3rd generation', dict(LEGACY, h264=VideoLimit(1080, 60, 42))),
    'ultra': DeviceProfile('Chromecast Ultra', dict(MODERN, h264=VideoLimit(1080, 60, 42), vp8=VideoLimit(2160, 30)), True),
    'googletv4k': DeviceProfile('Chromecast with Google TV (4K)', dict(MODERN, h264=VideoLimit(2160, 30, 51)), True),
    # The published Cast codec table does not distinguish HD. Keep this bounded
    # to 1080p without assuming that its additional Android codecs work in Cast.
    'googletvhd': DeviceProfile('Chromecast with Google TV (HD)', {'h264': VideoLimit(1080, 60, 42)}),
    'streamer': DeviceProfile('Google TV Streamer', dict(MODERN, h264=VideoLimit(2160, 60, 52), av1=VideoLimit(2160, 60, 13)), True),
    'nesthub': DeviceProfile('Google Nest Hub', {'h264': VideoLimit(720, 60, 41), 'vp9': VideoLimit(720, 60, 40)}),
    'nesthubmax': DeviceProfile('Google Nest Hub Max', {'h264': VideoLimit(720, 30, 41), 'vp9': VideoLimit(720, 30, 40)}),
}


def device_profile(model, override='auto'):
    model = (model or '').lower()
    if any(name in model for name in ('chromecast audio', 'google home', 'nest mini', 'home mini', 'nest audio', 'google cast group')) and 'hub' not in model:
        raise ValueError('This receiver is audio-only. Select a TV or video Chromecast.')
    if override != 'auto':
        if override not in PROFILES:
            raise ValueError('Unknown device compatibility profile')
        return PROFILES[override]
    for names, key in [
        (('google tv streamer',), 'streamer'), (('chromecast ultra',), 'ultra'),
        (('nest hub max',), 'nesthubmax'), (('nest hub', 'google home hub'), 'nesthub'),
        (('chromecast hd', 'chromecast with google tv (hd)'), 'googletvhd'),
        (('chromecast with google tv',), 'googletv4k'),
        (('chromecast 3', 'chromecast (3rd', 'chromecast third'), 'chromecast3'),
        (('chromecast 1', 'chromecast 2', 'chromecast (1st', 'chromecast (2nd'), 'chromecast12'),
    ]:
        if any(name in model for name in names):
            return PROFILES[key]
    # Many generations advertise simply "Chromecast". Never guess the hardware.
    return PROFILES['generic']


def choose_plan(model, source, metadata, request):
    device = device_profile(model, request.get('deviceProfile', 'auto'))
    encode_limit = device.codecs['h264']
    max_height, max_fps, h264_level = encode_limit.height, encode_limit.fps, encode_limit.level
    quality = request.get('quality', 'original')
    if quality in ('720', '1080', '2160'):
        max_height = min(max_height, int(quality))
    streams = metadata.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise ValueError('No video track found')
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    selected = request.get('audio', 'default')
    audio = next((s for s in audios if str(s['index']) == selected), None) if selected != 'default' else next((s for s in audios if s.get('disposition', {}).get('default')), audios[0] if audios else None)
    if selected != 'default' and audio is None:
        raise ValueError('The selected audio track is not in this video')
    try:
        fps = float(Fraction(video.get('avg_frame_rate') or video.get('r_frame_rate') or '30'))
    except (ValueError, ZeroDivisionError):
        fps = 30
    fps = fps or 30
    codec = video.get('codec_name')
    width, height = int(video.get('width', 0)), int(video.get('height', 0))
    level = int(video.get('level', 0) or 0)
    pixel = video.get('pix_fmt', '')
    hdr = video.get('color_transfer') in ('smpte2084', 'arib-std-b67')
    limit = device.codecs.get(codec)
    quality_height = int(quality) if quality in ('720', '1080', '2160') else 2160
    def fits(cap):
        rate = cap.low_res_fps if height <= 720 and width <= 1280 and cap.low_res_fps else cap.fps
        return 0 < height <= min(cap.height, quality_height) and 0 < width <= min(cap.height, quality_height) * 16 / 9 and 0 < fps <= rate + .1
    pixels = ('yuv420p', 'yuvj420p') if codec in ('h264', 'vp8') else ('yuv420p', 'yuv420p10le')
    # FFprobe reports -99 for ordinary VP9 streams without level metadata.
    # Bound those by profile/pixel format, resolution and frame rate instead.
    level_ok = limit and (not limit.level or 0 <= level <= limit.level or (codec == 'vp9' and level == -99))
    supported = bool(limit and fits(limit) and level_ok and pixel in pixels)
    copy_video = bool(supported and (not hdr or device.hdr))
    if height <= 720 and width <= 1280 and encode_limit.low_res_fps:
        max_fps = encode_limit.low_res_fps
    audio_codec = audio.get('codec_name', '') if audio else ''
    source_channels = int(audio.get('channels', 2)) if audio else 0
    channels = min(source_channels, 6 if request.get('sound') == 'surround' else 2)
    sample_rate = int(audio.get('sample_rate', 48000)) if audio else 48000
    copy_audio = not audio or (audio_codec == 'aac' and audio.get('profile', 'LC') in ('LC', 'HE-AAC', 'HE-AACv2') and source_channels <= channels and 0 < sample_rate <= 48000)
    suffix = Path(urlsplit(source).path).suffix.lower()
    direct = copy_video and copy_audio and codec in ('h264', 'hevc', 'av1') and suffix in ('.mp4', '.m4v') and len(audios) <= 1 and selected == 'default'
    # VP8/VP9/AV1 transport changes are not assumed to work through HLS.
    # Preserve them in their original compatible container when possible.
    if suffix == '.webm' and codec in ('vp8', 'vp9') and copy_video and audio_codec in ('opus', 'vorbis') and len(audios) == 1 and source_channels <= channels and selected == 'default':
        direct = True
        copy_audio = True
    audio_delay = float(request.get('audioDelay', 0))
    if not math.isfinite(audio_delay) or not -1 <= audio_delay <= 1:
        raise ValueError('Audio delay must be between -1 and 1 seconds')
    if audio_delay:
        direct = False
        copy_audio = False
    if request.get('mode') == 'direct':
        if not direct:
            raise ValueError('This file does not match the receiver profile for original-file playback. Use Automatic.')
    if request.get('mode') == 'convert':
        direct = False
        copy_video = False
    if not direct and codec not in ('h264', 'hevc'):
        copy_video = False
    transport = 'direct' if direct else 'hls'
    description = 'Original picture and sound' if direct else ('Original picture' if copy_video else f'High-quality {min(height, max_height)}p video')
    if not direct:
        description += ' · original audio' if copy_audio else (' · AAC 5.1' if channels > 2 else ' · AAC stereo')
    return Plan(device.name, transport, copy_video, copy_audio, max_height, min(fps, max_fps), f'{h264_level / 10:.1f}', channels, bool(copy_video and codec == 'hevc'), bool(hdr and not copy_video), description, video['index'], audio['index'] if audio else None)
