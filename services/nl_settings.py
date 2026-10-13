"""Playback and display settings of a person (M10c, H5 and H6): the allowed values and how they read.

Stored per person with the M4 prefs (`nl_person_prefs`, schema v20):
    skip_seconds   how far the skip buttons jump: 10, 15, 30 or 60 (null = the device's own choice, 10 at first)
    autoplay       start the next episode by itself; a show's own switch (show page) wins, this is the default for
                   shows that have none (null = on)
    quality_kbps   the highest stream rate to ask Jellyfin for (PlaybackInfo MaxStreamingBitrate): null = Auto (no
                   limit of our own), else 8000, 4000, 2000 or 1000
    picture_fit    fit (whole picture, bars if needed), fill (cover the screen, crop) or original (no scaling)

Pure helpers only; nothing here touches the database or Jellyfin.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SKIP_CHOICES = (10, 15, 30, 60)
QUALITY_CHOICES = (8000, 4000, 2000, 1000)          # kbit/s
FIT_CHOICES = ("fit", "fill", "original")

QUALITY_LABELS = {
    8000: "High (8 Mbps)",
    4000: "Medium (4 Mbps)",
    2000: "Low (2 Mbps)",
    1000: "Lowest (1 Mbps)",
}
FIT_LABELS = {"fit": "Fit", "fill": "Fill", "original": "Original size"}


def quality_options() -> List[Dict[str, Any]]:
    """The quality choices, best first, with Auto on top. `kbps` is None for Auto."""
    return [{"kbps": None, "label": "Auto (original quality)"}] + [
        {"kbps": k, "label": QUALITY_LABELS[k]} for k in QUALITY_CHOICES
    ]


def valid_quality(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in QUALITY_CHOICES


def valid_skip(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in SKIP_CHOICES


def valid_fit(value: Any) -> bool:
    return isinstance(value, str) and value in FIT_CHOICES


def effective_cap_bps(profile_max_bps: Optional[int], quality_kbps: Optional[int]) -> Optional[int]:
    """The stream rate to ask Jellyfin for, in bit/s: the profile's own ceiling, or lower when a quality is chosen.
    A chosen quality never raises the ceiling."""
    if not quality_kbps:
        return profile_max_bps
    wanted = int(quality_kbps) * 1000
    return min(wanted, profile_max_bps) if profile_max_bps else wanted
