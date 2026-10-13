"""Picture and sound notes for the player (M10c, P20 and P24).

P20: when Jellyfin has to convert a stream (the device can't play the video or sound as it is, or the person chose
a lower quality) the player says so in plain words, and a format that can't be played at all is named by its codec.
P24: audio-description tracks are recognised from their names and labelled "AD".

Pure functions over Jellyfin's MediaStream / TranscodeReasons data; no network.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlsplit

VIDEO_NAMES = {
    "h264": "H.264", "avc": "H.264", "hevc": "HEVC", "h265": "HEVC", "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
    "mpeg2video": "MPEG-2", "mpeg4": "MPEG-4", "vc1": "VC-1", "wmv3": "WMV", "msmpeg4v3": "MPEG-4", "theora": "Theora",
}
AUDIO_NAMES = {
    "aac": "AAC", "mp3": "MP3", "ac3": "Dolby Digital", "eac3": "Dolby Digital Plus", "dts": "DTS", "truehd": "TrueHD",
    "flac": "FLAC", "opus": "Opus", "vorbis": "Vorbis", "pcm_s16le": "PCM", "pcm_s24le": "PCM", "alac": "ALAC", "mp2": "MP2",
}
HDR_NAMES = {"hdr10": "HDR10", "hdr10plus": "HDR10+", "hlg": "HLG", "dovi": "Dolby Vision", "dovipq": "Dolby Vision",
             "dovihlg": "Dolby Vision", "dovi_with_hdr10": "Dolby Vision", "doviwithhdr10": "Dolby Vision",
             "doviwithhlg": "Dolby Vision", "doviwithsdr": "Dolby Vision"}

_VIDEO_REASONS = {
    "VideoCodecNotSupported", "VideoProfileNotSupported", "VideoLevelNotSupported", "VideoResolutionNotSupported",
    "VideoBitDepthNotSupported", "VideoFramerateNotSupported", "RefFramesNotSupported", "AnamorphicVideoNotSupported",
    "InterlacedVideoNotSupported", "VideoRangeTypeNotSupported",
}
_AUDIO_REASONS = {
    "AudioCodecNotSupported", "AudioChannelsNotSupported", "AudioProfileNotSupported", "AudioSampleRateNotSupported",
    "AudioBitDepthNotSupported", "AudioBitrateNotSupported", "SecondaryAudioNotSupported",
}
_CONTAINER_REASONS = {"ContainerNotSupported"}
_RATE_REASONS = {"ContainerBitrateExceedsLimit", "VideoBitrateNotSupported"}
_SUBTITLE_REASONS = {"SubtitleCodecNotSupported"}
_HDR_REASONS = {"VideoRangeTypeNotSupported"}


def codec_name(kind: str, codec: Any) -> str:
    """"hevc" -> "HEVC", "dts" -> "DTS"; an unknown codec is shown upper-case as Jellyfin names it ("" if empty)."""
    c = str(codec or "").strip().lower()
    if not c:
        return ""
    table = VIDEO_NAMES if kind == "video" else AUDIO_NAMES
    return table.get(c, c.upper())


def hdr_name(range_type: Any) -> Optional[str]:
    """Jellyfin's VideoRangeType ("HDR10", "DOVI", "SDR") as a plain name, None for ordinary (SDR) pictures."""
    r = str(range_type or "").strip().lower().replace("-", "").replace(" ", "")
    if not r or r in ("sdr", "unknown"):
        return None
    return HDR_NAMES.get(r, str(range_type))


def transcode_reasons(transcoding_url: Any) -> List[str]:
    """The TranscodeReasons Jellyfin put in the transcoding URL, as a list (empty if there are none)."""
    if not transcoding_url:
        return []
    try:
        query = parse_qs(urlsplit(str(transcoding_url)).query, keep_blank_values=False)
    except ValueError:
        return []
    out: List[str] = []
    for key, values in query.items():
        if key.lower() == "transcodereasons":
            for v in values:
                out.extend(p for p in v.split(",") if re.fullmatch(r"[A-Za-z]{3,60}", p))
    return out


def formats_of(media_source: Dict[str, Any], audio_index: Optional[int] = None) -> Dict[str, Any]:
    """The video codec, the playing audio track's codec and the HDR kind of a Jellyfin MediaSource, by name."""
    video = ""
    hdr = None
    audio_codec = ""
    first_audio = ""
    for st in media_source.get("MediaStreams") or []:
        kind = st.get("Type")
        if kind == "Video" and not video:
            video = st.get("Codec") or ""
            hdr = hdr_name(st.get("VideoRangeType") or st.get("VideoRange"))
        elif kind == "Audio":
            if not first_audio:
                first_audio = st.get("Codec") or ""
            if audio_index is not None and st.get("Index") == audio_index:
                audio_codec = st.get("Codec") or ""
    audio = audio_codec or first_audio
    return {"video": codec_name("video", video), "audio": codec_name("audio", audio), "hdr": hdr,
            "video_codec": str(video).lower(), "audio_codec": str(audio).lower()}


def describe_conversion(
    play_method: str,
    reasons: Iterable[str],
    formats: Dict[str, Any],
    device: str,
    cap_kbps: Optional[int] = None,
    hdr: Optional[str] = None,
) -> Dict[str, Any]:
    """What the player tells the viewer. `device` is "This TV" or "This browser". Returns
    {state: direct | converting, note: str | None, detail: str | None, reasons: [...], formats}.

    The note is written for a person: "This TV can't play this format, so it's being converted." with the
    parts named in `detail` ("HEVC video, DTS sound"); a conversion that is only down to the quality they
    chose says so instead; HDR turned into an ordinary picture gets its own sentence."""
    rs = set(reasons)
    base = {"reasons": sorted(rs), "formats": {"video": formats.get("video") or None, "audio": formats.get("audio") or None, "hdr": formats.get("hdr")}}
    if play_method == "DirectPlay" or (play_method == "DirectStream" and rs <= _CONTAINER_REASONS):
        # Repackaging the file (an mkv sent as HLS) leaves picture and sound untouched: nothing to tell.
        return {"state": "direct", "note": None, "detail": None, **base}
    video_bad = bool(rs & (_VIDEO_REASONS - _HDR_REASONS))
    audio_bad = bool(rs & _AUDIO_REASONS)
    container_bad = bool(rs & _CONTAINER_REASONS)
    hdr_bad = bool(rs & _HDR_REASONS)
    rate = bool(rs & _RATE_REASONS)
    sub = bool(rs & _SUBTITLE_REASONS)

    parts: List[str] = []
    if video_bad and formats.get("video"):
        parts.append(f"{formats['video']} video")
    if audio_bad and formats.get("audio"):
        parts.append(f"{formats['audio']} sound")
    if container_bad and not parts:
        parts.append("this kind of file")
    format_problem = video_bad or audio_bad or container_bad
    if format_problem:
        note = f"{device} can't play this format, so it's being converted."
        detail = ", ".join(parts) or None
    elif hdr_bad:
        label = formats.get("hdr") or hdr or "HDR"
        note = f"{device} can't show {label}, so the picture is being converted to standard colours."
        detail = None
    elif rate and cap_kbps:
        note = f"Playing at {cap_kbps // 1000} Mbps or less as you chose, so it's being converted."
        detail = None
    elif rate:
        note = "This file is too big for the connection, so it's being converted."
        detail = None
    elif sub:
        note = "The subtitles are being burned into the picture, so it's being converted."
        detail = None
    else:
        note = "This is being converted so it can play here."
        detail = None
    return {"state": "converting", "note": note, "detail": detail, **base}


# ---------------------------------------------------------------- P24 audio description

_AD_WORDS = re.compile(
    r"audio[ ._-]?descri|descriptive[ ._-]?(?:audio|video|service)|visually[ ._-]?impaired|\bdescribed[ ._-]video\b",
    re.I,
)
_AD_ABBREVIATION = re.compile(r"(?<![A-Za-z0-9])AD(?![A-Za-z0-9])")   # upper case only: "ad" inside words is not it


def is_audio_description(stream: Dict[str, Any]) -> bool:
    """A Jellyfin audio MediaStream that is an audio-description track (a narrator describing what is on screen).
    Jellyfin has no flag for it, so the track's title or display title decides: "Audio Description", "Descriptive
    Audio", "Visually Impaired", or the capitals "AD" as a word ("English - AD - AAC")."""
    if str(stream.get("Type") or "Audio") != "Audio":
        return False
    for key in ("Title", "DisplayTitle"):
        text = str(stream.get(key) or "")
        if text and (_AD_WORDS.search(text) or _AD_ABBREVIATION.search(text)):
            return True
    return False
