"""Conservative sender-side policies; Google TV Streamer is the primary target.

Source: https://developers.google.com/cast/docs/media (2026-09-05).
The receiver's HDMI/speaker capabilities are not exposed by the default receiver.
Stereo AAC is therefore the default for converted audio; surround is opt-in.
"""
from dataclasses import dataclass, asdict
from fractions import Fraction
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

    def json(self):
        return asdict(self)


def choose_plan(model, source, metadata, request):
    model = (model or '').lower()
    streamer = 'google tv streamer' in model
    if any(name in model for name in ('chromecast audio', 'google home', 'nest mini', 'home mini')):
        raise ValueError('This receiver is audio-only. Select a TV or video Chromecast.')
    device = 'Google TV Streamer' if streamer else 'Standard Cast compatibility'
    # Only the tested target has a high-capability profile in this release.
    max_height, max_fps, h264_level = (2160, 60, 52) if streamer else (1080, 30, 41)
    if 'nest hub' in model:
        max_height = 720
    quality = request.get('quality', 'original')
    if quality in ('720', '1080', '2160'):
        max_height = min(max_height, int(quality))
    streams = metadata.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if not video:
        raise ValueError('No video track found')
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    selected = request.get('audio', 'default')
    audio = next((s for s in audios if str(s['index']) == selected), None) if selected != 'default' else (audios[0] if audios else None)
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
    limits_fit = height <= max_height and width <= max_height * 16 / 9 and fps <= max_fps + .1
    if not streamer and height <= 720 and width <= 1280 and 'nest hub' not in model:
        limits_fit = height <= max_height and fps <= 60.1
    supported = codec == 'h264' and pixel in ('yuv420p', 'yuvj420p') and level <= h264_level
    if streamer:
        supported |= codec == 'hevc' and pixel in ('yuv420p', 'yuv420p10le') and level <= 153
        supported |= codec == 'vp9' and pixel in ('yuv420p', 'yuv420p10le') and level <= 51
        supported |= codec == 'av1' and pixel in ('yuv420p', 'yuv420p10le')
    copy_video = bool(supported and limits_fit and (not hdr or streamer))
    audio_codec = audio.get('codec_name', '') if audio else ''
    source_channels = int(audio.get('channels', 2)) if audio else 0
    channels = min(source_channels, 6 if request.get('sound') == 'surround' else 2)
    sample_rate = int(audio.get('sample_rate', 48000)) if audio else 48000
    copy_audio = not audio or (audio_codec == 'aac' and source_channels <= channels and sample_rate <= 48000)
    suffix = Path(urlsplit(source).path).suffix.lower()
    direct = copy_video and copy_audio and suffix in ('.mp4', '.m4v') and selected == 'default'
    # VP8/VP9/AV1 transport changes are not assumed to work through HLS.
    # Preserve them in their original compatible container when possible.
    if suffix == '.webm' and copy_video and audio_codec in ('opus', 'vorbis') and source_channels <= channels and selected == 'default':
        direct = True
        copy_audio = True
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
    return Plan(device, transport, copy_video, copy_audio, max_height, min(fps, max_fps), f'{h264_level / 10:.1f}', channels, bool(copy_video and codec == 'hevc'), bool(hdr and not copy_video), description)
