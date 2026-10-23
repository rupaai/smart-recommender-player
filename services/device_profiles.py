"""
Jellyfin DeviceProfiles (services/device_profiles.py)

A DeviceProfile tells Jellyfin what a client can play, so PlaybackInfo can choose
direct play or an HLS transcode. Kept conservative on purpose. TV and Cast profiles
come in later Northern Lights milestones (TV: below, M3; Cast: below, M8).
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

BROWSER_PROFILE = {
    "Name": "Northern Lights (browser)",
    "MaxStreamingBitrate": 20_000_000,
    "MaxStaticBitrate": 20_000_000,
    "MusicStreamingTranscodingBitrate": 192_000,
    "DirectPlayProfiles": [
        {
            "Container": "mp4,m4v",
            "Type": "Video",
            "VideoCodec": "h264",
            "AudioCodec": "aac,mp3",
        },
        {
            "Container": "webm",
            "Type": "Video",
            "VideoCodec": "vp9,av1",
            "AudioCodec": "opus,vorbis",
        },
    ],
    "TranscodingProfiles": [
        {
            "Container": "ts",
            "Type": "Video",
            "VideoCodec": "h264",
            "AudioCodec": "aac",
            "Protocol": "hls",
            "Context": "Streaming",
            "MaxAudioChannels": "2",
        },
    ],
    "ContainerProfiles": [],
    "CodecProfiles": [],
    "SubtitleProfiles": [
        {"Format": "vtt", "Method": "External"},
    ],
}


# ---- Device-aware browser profile -------------------------------------------------
# The page probes what its browser can decode (player-core.js, NLPlayerCore.capabilities)
# and sends it as `caps`, e.g. "v=h264.hevc;a=aac.mp3;h=h264.hevc.aac". Only allowlisted
# tokens are accepted; anything else is dropped.

CAPS_MAX_LENGTH = 200
_VIDEO_TOKENS = ("h264", "hevc", "av1", "vp9")
_AUDIO_TOKENS = ("aac", "mp3", "opus", "flac", "ac3", "eac3")
_CAPS_KEYS = {"v": ("video", _VIDEO_TOKENS), "a": ("audio", _AUDIO_TOKENS), "h": ("hls", _VIDEO_TOKENS + _AUDIO_TOKENS)}
_JF_CODEC = {"h264": "h264", "hevc": "hevc", "av1": "av1", "vp9": "vp9"}

Caps = Dict[str, List[str]]


def parse_caps(raw: Optional[str]) -> Optional[Caps]:
    """Parse the `caps` query value. None for missing, overlong or garbage input."""
    if not raw or not isinstance(raw, str) or len(raw) > CAPS_MAX_LENGTH:
        return None
    caps: Caps = {"video": [], "audio": [], "hls": []}
    for part in raw.split(";"):
        key, sep, value = part.strip().partition("=")
        if not sep or key not in _CAPS_KEYS:
            continue
        name, allowed = _CAPS_KEYS[key]
        for token in value.split("."):
            token = token.strip().lower()
            if token in allowed and token not in caps[name]:
                caps[name].append(token)
    if not any(caps.values()):
        return None
    return caps


def _ordered(tokens: List[str], preferred: tuple) -> List[str]:
    return [t for t in preferred if t in tokens]


def browser_profile(caps: Optional[Caps]) -> dict:
    """The DeviceProfile for this browser. No caps: the conservative BROWSER_PROFILE."""
    if not caps:
        return BROWSER_PROFILE
    profile = copy.deepcopy(BROWSER_PROFILE)
    video, audio, hls = caps.get("video", []), caps.get("audio", []), caps.get("hls", [])

    mp4_video = _ordered(video, ("h264", "hevc", "av1"))
    mp4_audio = _ordered(audio, ("aac", "mp3", "opus", "flac", "ac3", "eac3"))
    direct = []
    if mp4_video and mp4_audio:
        direct.append({
            "Container": "mp4,m4v,mov",
            "Type": "Video",
            "VideoCodec": ",".join(mp4_video),
            "AudioCodec": ",".join(mp4_audio),
        })
    webm_video = _ordered(video, ("vp9", "av1"))
    if webm_video:
        direct.append({
            "Container": "webm",
            "Type": "Video",
            "VideoCodec": ",".join(webm_video),
            "AudioCodec": "opus,vorbis",
        })
    if direct:
        profile["DirectPlayProfiles"] = direct

    # fMP4 HLS lets Jellyfin copy HEVC/AV1 video and only convert the audio.
    fmp4_video = _ordered(hls, ("hevc", "av1", "h264"))
    if any(c in fmp4_video for c in ("hevc", "av1")):
        fmp4_audio = _ordered(hls, ("aac", "mp3", "opus", "flac", "ac3", "eac3")) or ["aac"]
        if "aac" not in fmp4_audio:
            fmp4_audio.append("aac")
        profile["TranscodingProfiles"].insert(0, {
            "Container": "mp4",
            "Type": "Video",
            "VideoCodec": ",".join(_JF_CODEC[c] for c in fmp4_video),
            "AudioCodec": ",".join(fmp4_audio),
            "Protocol": "hls",
            "Context": "Streaming",
            "MaxAudioChannels": "2",
            "MinSegments": "1",
            "BreakOnNonKeyFrames": True,
        })

    if "hevc" in video or "hevc" in hls:
        # The probe tested Main10 (which implies Main); no other restriction is invented.
        profile["CodecProfiles"] = [{
            "Type": "Video",
            "Codec": "hevc",
            "Conditions": [{
                "Condition": "EqualsAny",
                "Property": "VideoProfile",
                "Value": "main|main 10",
                "IsRequired": False,
            }],
        }]
    return profile


# ---- TV profile (M3) --------------------------------------------------------------
# The Android TV app plays through Media3/ExoPlayer, which decodes far more than a browser.
# Chosen with `client=tv`; the page's `caps` are ignored. The TV always plays the original file:
# the app decodes TrueHD / DTS itself (Media3 + Jellyfin's FFmpeg audio decoder) and switches audio
# tracks inside the file, so the profile lists every audio codec and a 400 Mbps ceiling. The
# conversion below is used only when the viewer chooses "Play a converted copy". Media3 can
# draw picture subtitles (PGS, VobSub, DVB) only when they are embedded in the file, so they
# are Embed for direct play and Encode (burned in) when Jellyfin transcodes.

TV_CLIENT = "tv"

TV_PROFILE = {
    "Name": "Northern Lights (TV)",
    "MaxStreamingBitrate": 400_000_000,  # LAN: the original file is never limited
    "MaxStaticBitrate": 400_000_000,
    "MusicStreamingTranscodingBitrate": 192_000,
    "DirectPlayProfiles": [
        {
            "Container": "mkv,mp4,m4v,mov,webm,ts",
            "Type": "Video",
            "VideoCodec": "h264,hevc,vp9,av1",
            "AudioCodec": "aac,mp3,ac3,eac3,opus,flac,vorbis,truehd,mlp,dts,dca,pcm_s16le,pcm_s24le,alac",
        },
    ],
    "TranscodingProfiles": [
        {
            "Container": "mp4",
            "Type": "Video",
            "VideoCodec": "hevc,h264",
            "AudioCodec": "aac,ac3,eac3",
            "Protocol": "hls",
            "Context": "Streaming",
            "MaxAudioChannels": "6",
            "MinSegments": "1",
            "BreakOnNonKeyFrames": True,
        },
    ],
    "ContainerProfiles": [],
    "CodecProfiles": [
        {
            "Type": "Video",
            "Codec": "hevc",
            "Conditions": [{
                "Condition": "EqualsAny",
                "Property": "VideoProfile",
                "Value": "main|main 10",
                "IsRequired": False,
            }],
        },
    ],
    "SubtitleProfiles": (
        [{"Format": f, "Method": "External"} for f in ("srt", "subrip", "vtt", "ass", "ssa")]
        + [{"Format": f, "Method": "Embed"} for f in ("pgssub", "pgs", "dvdsub", "vobsub", "dvbsub")]
        + [{"Format": f, "Method": "Encode"} for f in ("pgssub", "pgs", "dvdsub", "vobsub", "dvbsub")]
    ),
}


# ---- Cast profile (M8) ------------------------------------------------------------
# A Chromecast decodes H.264 + AAC everywhere (older sticks stop at level 4.1; 5.1 / HEVC only on
# the Ultra and Google TV models), so this is the lowest common denominator: direct play an mp4 that
# is already H.264/AAC, otherwise Jellyfin remuxes or converts to HLS (MPEG-TS segments, H.264 + AAC
# stereo). Chosen with `client=cast`; the page's `caps` are ignored (the browser is not what plays).
# Text subtitles go out as WebVTT tracks; picture subtitles are burned in.

CAST_CLIENT = "cast"

CAST_PROFILE = {
    "Name": "Northern Lights (Chromecast)",
    "MaxStreamingBitrate": 20_000_000,   # home Wi-Fi to a Chromecast
    "MaxStaticBitrate": 20_000_000,
    "MusicStreamingTranscodingBitrate": 192_000,
    "DirectPlayProfiles": [
        {
            "Container": "mp4,m4v",
            "Type": "Video",
            "VideoCodec": "h264",
            "AudioCodec": "aac,mp3",
        },
    ],
    "TranscodingProfiles": [
        {
            "Container": "ts",
            "Type": "Video",
            "VideoCodec": "h264",
            "AudioCodec": "aac",
            "Protocol": "hls",
            "Context": "Streaming",
            "MaxAudioChannels": "2",
        },
    ],
    "ContainerProfiles": [],
    "CodecProfiles": [
        {
            "Type": "Video",
            "Codec": "h264",
            "Conditions": [
                {"Condition": "LessThanEqual", "Property": "VideoLevel", "Value": "41", "IsRequired": False},
            ],
        },
    ],
    "SubtitleProfiles": (
        [{"Format": "vtt", "Method": "External"}]
        + [{"Format": f, "Method": "Encode"} for f in ("pgssub", "pgs", "dvdsub", "vobsub", "dvbsub")]
    ),
}


def profile_for(client: Optional[str], caps_raw: Optional[str]) -> dict:
    """The DeviceProfile for a play request: `client=tv` or `client=cast` wins (caps ignored), else the
    browser profile shaped by `caps`, else the conservative BROWSER_PROFILE."""
    if client == TV_CLIENT:
        return TV_PROFILE
    if client == CAST_CLIENT:
        return CAST_PROFILE
    return browser_profile(parse_caps(caps_raw))
