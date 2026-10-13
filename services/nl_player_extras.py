"""
Northern Lights M7a: what the player draws on top of the picture, read from Jellyfin on the server.

- Trickplay (seek-bar thumbnails): the item's `Trickplay` manifest says which tile sheets exist.
  We hand the page the geometry plus a proxy URL template; the sheet images themselves come through
  the /nl/jf/ proxy (GET only, exact path pattern, see jellyfin_playback.is_trickplay_path).
- Chapters: the item's `Chapters`.
- Segments (Intro Skipper or any media-segment provider): `GET /MediaSegments/{id}` (Jellyfin 10.10+).

Everything here is OUR json. Jellyfin's replies, ids of other users and the API key never reach the page.
Each part fails on its own: no Trickplay, no plugin, an old server or Jellyfin being down just
leaves that part empty, never an error.

The parse_* functions take plain dicts so they can be tested without a server.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.jellyfin_playback import JellyfinPlayback, proxy_url, valid_jellyfin_id

_TICKS = 10_000_000
MAX_CHAPTERS = 200
MAX_SEGMENTS = 6
# Jellyfin sends the enum as a string; accept its numbers too (Intro = 5, Outro = 4).
SEGMENT_TYPES = {"intro": "intro", "outro": "outro", 5: "intro", 4: "outro"}


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_id(value: Any) -> str:
    return str(value or "").replace("-", "").lower()


def parse_trickplay(details: Dict[str, Any], jf_id: str) -> Optional[Dict[str, Any]]:
    """The item's Trickplay manifest -> {width, height, tile_width, tile_height, count, interval_seconds, url}.

    `url` is a proxy URL with a literal `{index}` the page replaces by the sheet number. The manifest is
    {media source id: {width: info}}; the source matching the item wins, else the first. The smallest
    width is used (the bubble is small and the sheet loads sooner). None when there is nothing usable."""
    manifest = (details or {}).get("Trickplay")
    if not isinstance(manifest, dict) or not manifest or not valid_jellyfin_id(jf_id or ""):
        return None
    keys = [k for k, v in manifest.items() if isinstance(v, dict) and v]
    if not keys:
        return None
    key = next((k for k in keys if _norm_id(k) == _norm_id(jf_id)), keys[0])
    best: Optional[Dict[str, Any]] = None
    for width_key, info in manifest[key].items():
        if not isinstance(info, dict):
            continue
        w = _int(info.get("Width") if info.get("Width") is not None else width_key)
        h, tw, th = _int(info.get("Height")), _int(info.get("TileWidth")), _int(info.get("TileHeight"))
        count, interval = _int(info.get("ThumbnailCount")), _int(info.get("Interval"))
        if not (w and h and tw and th and count and interval):
            continue
        if not (10 <= w <= 2000 and 0 < h <= 2000 and 0 < tw <= 40 and 0 < th <= 40 and 0 < count <= 200_000):
            continue
        if best is None or w < best["width"]:
            best = {
                "width": w,
                "height": h,
                "tile_width": tw,
                "tile_height": th,
                "count": count,
                "interval_seconds": interval / 1000.0,
            }
    if best is None:
        return None
    best["url"] = proxy_url(f"Videos/{jf_id}/Trickplay/{best['width']}/{{index}}.jpg")
    return best


def parse_chapters(details: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The item's Chapters -> [{start_seconds, name}] in order. Fewer than two chapters is no chapter list."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for c in ((details or {}).get("Chapters") or [])[:MAX_CHAPTERS]:
        if not isinstance(c, dict):
            continue
        ticks = _int(c.get("StartPositionTicks"))
        if ticks is None or ticks < 0 or ticks in seen:
            continue
        seen.add(ticks)
        out.append({"start_seconds": round(ticks / _TICKS, 2), "name": str(c.get("Name") or "").strip()[:120]})
    out.sort(key=lambda c: c["start_seconds"])
    if len(out) < 2:
        return []
    for i, c in enumerate(out):
        if not c["name"]:
            c["name"] = f"Chapter {i + 1}"
    return out


def parse_segments(data: Any) -> List[Dict[str, Any]]:
    """Jellyfin's MediaSegments reply -> [{type: "intro"|"outro", start_seconds, end_seconds}], by start."""
    out: List[Dict[str, Any]] = []
    items = data.get("Items") if isinstance(data, dict) else None
    for s in items or []:
        if not isinstance(s, dict):
            continue
        raw = s.get("Type")
        kind = SEGMENT_TYPES.get(raw.lower() if isinstance(raw, str) else raw)
        start, end = _int(s.get("StartTicks")), _int(s.get("EndTicks"))
        if kind is None or start is None or end is None or start < 0 or end <= start:
            continue
        out.append({"type": kind, "start_seconds": round(start / _TICKS, 2), "end_seconds": round(end / _TICKS, 2)})
    out.sort(key=lambda s: s["start_seconds"])
    return out[:MAX_SEGMENTS]


def empty_extras() -> Dict[str, Any]:
    return {"trickplay": None, "chapters": [], "segments": []}


def player_extras(pb: JellyfinPlayback, jf_id: str) -> Dict[str, Any]:
    """Trickplay, chapters and segments of one Jellyfin item, each empty when unavailable."""
    out = empty_extras()
    if not valid_jellyfin_id(jf_id or ""):
        return out
    details = pb.item_details(jf_id)   # {} when Jellyfin can't be read
    try:
        out["trickplay"] = parse_trickplay(details, jf_id)
        out["chapters"] = parse_chapters(details)
    except Exception:  # noqa: BLE001 - a surprising shape must not break playback
        out["trickplay"], out["chapters"] = None, []
    try:
        out["segments"] = parse_segments(pb.media_segments(jf_id))
    except Exception:  # noqa: BLE001
        out["segments"] = []
    return out
