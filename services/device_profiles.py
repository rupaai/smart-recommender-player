"""
Jellyfin DeviceProfiles (services/device_profiles.py)

A DeviceProfile tells Jellyfin what a client can play, so PlaybackInfo can choose
direct play or an HLS transcode. Kept conservative on purpose. TV and Cast profiles
come in later Northern Lights milestones (M3, M8).
"""

from __future__ import annotations

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
