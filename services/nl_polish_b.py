"""
Northern Lights browse polish (services/nl_polish_b.py), M7b

What the details page shows below the buttons, all read here so the browser never sees a Jellyfin or
TMDB field name, id of another user or key:

- Cast & crew        from the Jellyfin item's People (photos through our /nl/jf/ image proxy).
- Trailer            a YouTube key. TMDB's /videos first (official trailer, then any trailer, then a
                     teaser); Jellyfin's RemoteTrailers when TMDB has nothing or cannot be reached.
- Tech details       container, size, bitrate, video, audio and subtitle tracks, from Jellyfin's MediaSources.
- More like this     library titles close to this one: TMDB "similar" and "recommendations" (when there is
                     a TMDB key and the title has a TMDB id), the existing recommender's vibe index (the
                     embeddings behind the mood search, when its files are present) and shared genres.
                     Every part fails on its own: with none of them the row is just empty.

The pure functions at the top take plain dicts so they can be tested without a server.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.jellyfin_playback import JellyfinUnavailable
from services.nl_browse import image_url

CAST_MAX = 20
CREW_MAX = 12
MORE_MAX = 12
TMDB_TTL = 6 * 3600          # a good TMDB answer is reused this long
TMDB_FAIL_TTL = 300          # a failed one is not retried for this long
TMDB_CACHE_MAX = 400
TRAILER_URL = "https://www.youtube.com/watch?v={key}"

_YT_KEY = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_YT_URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,20})")

CAST_TYPES = ("Actor", "GuestStar")
CREW_TYPES = ("Director", "Writer", "Producer", "Composer")
VIDEO_CODECS = {"h264": "H.264", "hevc": "HEVC (H.265)", "h265": "HEVC (H.265)", "av1": "AV1", "vp9": "VP9",
                "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "vc1": "VC-1"}
AUDIO_CODECS = {"ac3": "AC3", "eac3": "E-AC3", "dts": "DTS", "truehd": "TrueHD", "aac": "AAC", "mp3": "MP3",
                "opus": "Opus", "flac": "FLAC", "vorbis": "Vorbis"}


# ---------------------------------------------------------------- pure helpers


def youtube_key(value: Any) -> Optional[str]:
    """A YouTube video key from a bare key or any usual YouTube address; None when it isn't one."""
    text = str(value or "").strip()
    if _YT_KEY.match(text):
        return text
    m = _YT_URL.search(text)
    return m.group(1) if m else None


def trailer_card(key: Optional[str], name: str = "") -> Optional[Dict[str, Any]]:
    if not key or not _YT_KEY.match(key):
        return None
    return {"key": key, "name": (name or "Trailer")[:120], "url": TRAILER_URL.format(key=key)}


def pick_tmdb_trailer(data: Any) -> Optional[Dict[str, Any]]:
    """TMDB's /videos reply -> the best YouTube video: official trailer, any trailer, then a teaser."""
    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for v in (data or {}).get("results") or [] if isinstance(data, dict) else []:
        if not isinstance(v, dict) or str(v.get("site") or "").lower() != "youtube":
            continue
        key = youtube_key(v.get("key"))
        if not key:
            continue
        kind = str(v.get("type") or "")
        if kind == "Trailer":
            rank = 0 if v.get("official") else 1
        elif kind == "Teaser":
            rank = 2
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, {"key": key, "name": str(v.get("name") or "")})
    return trailer_card(best[1]["key"], best[1]["name"]) if best else None


def jellyfin_trailer(details: Any) -> Optional[Dict[str, Any]]:
    """The first YouTube address among a Jellyfin item's RemoteTrailers."""
    for t in (details or {}).get("RemoteTrailers") or [] if isinstance(details, dict) else []:
        url = t.get("Url") if isinstance(t, dict) else t
        key = youtube_key(url)
        if key:
            return trailer_card(key, str(t.get("Name") or "") if isinstance(t, dict) else "")
    return None


def parse_people(details: Any) -> Dict[str, List[Dict[str, Any]]]:
    """A Jellyfin item's People -> {"cast": [...], "crew": [...]} in our own shape."""
    cast: List[Dict[str, Any]] = []
    crew: List[Dict[str, Any]] = []
    seen = set()
    for p in (details or {}).get("People") or [] if isinstance(details, dict) else []:
        if not isinstance(p, dict) or not p.get("Name"):
            continue
        kind = str(p.get("Type") or "")
        role = str(p.get("Role") or "")
        if kind in CAST_TYPES:
            bucket, shown_role = cast, role
        elif kind in CREW_TYPES:
            bucket, shown_role = crew, kind
        else:
            continue
        key = (kind, str(p["Name"]))
        if key in seen:
            continue
        seen.add(key)
        bucket.append({
            "name": str(p["Name"]),
            "role": shown_role,
            "image": image_url(p.get("Id"), "Primary", 200) if p.get("PrimaryImageTag") else None,
        })
    return {"cast": cast[:CAST_MAX], "crew": crew[:CREW_MAX]}


def human_size(num: Any) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def human_rate(bps: Any) -> str:
    try:
        n = float(bps)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    return f"{n / 1_000_000:.1f} Mbps" if n >= 1_000_000 else f"{n / 1000:.0f} kbps"


def resolution_label(width: Any, height: Any) -> str:
    try:
        w, h = int(width or 0), int(height or 0)
    except (TypeError, ValueError):
        return ""
    if not (w and h):
        return ""
    name = "4K" if (h >= 1800 or w >= 3400) else "1080p" if (h >= 900 or w >= 1800) else "720p" if (h >= 650 or w >= 1200) else f"{h}p"
    return f"{name} ({w}×{h})"


def _channels(stream: Dict[str, Any]) -> str:
    layout = str(stream.get("ChannelLayout") or "")
    if layout:
        return layout
    n = stream.get("Channels")
    return {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(n, f"{n} ch" if n else "")


def tech_rows(details: Any, source: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """[{label, value}] for the Tech details panel. From Jellyfin's first media source; when Jellyfin has
    nothing, the little our own database knows about the file (type and size). Never a path."""
    rows: List[Dict[str, str]] = []

    def add(label: str, value: str) -> None:
        if value:
            rows.append({"label": label, "value": value})

    src = None
    for m in (details or {}).get("MediaSources") or [] if isinstance(details, dict) else []:
        if isinstance(m, dict):
            src = m
            break
    if src:
        container = str(src.get("Container") or "").split(",")[0].strip()
        add("Container", container.upper())
        add("File size", human_size(src.get("Size")))
        add("Bitrate", human_rate(src.get("Bitrate")))
        streams = [s for s in src.get("MediaStreams") or [] if isinstance(s, dict)]
        video = next((s for s in streams if s.get("Type") == "Video"), None)
        if video:
            bits = [VIDEO_CODECS.get(str(video.get("Codec") or "").lower(), str(video.get("Codec") or "").upper()),
                    resolution_label(video.get("Width"), video.get("Height"))]
            rng = str(video.get("VideoRangeType") or video.get("VideoRange") or "")
            if rng and rng.upper() != "UNKNOWN":
                bits.append(rng)
            fps = video.get("RealFrameRate") or video.get("AverageFrameRate")
            if fps:
                try:
                    bits.append(f"{float(fps):g} fps")
                except (TypeError, ValueError):
                    pass
            add("Video", " · ".join(b for b in bits if b))
        audio = [s for s in streams if s.get("Type") == "Audio"]
        for i, a in enumerate(audio[:6], 1):
            bits = [str(a.get("DisplayLanguage") or a.get("Language") or "").strip(),
                    AUDIO_CODECS.get(str(a.get("Codec") or "").lower(), str(a.get("Codec") or "").upper()),
                    _channels(a)]
            if a.get("IsDefault") and len(audio) > 1:
                bits.append("default")
            add("Audio" if len(audio) == 1 else f"Audio {i}", " · ".join(b for b in bits if b))
        subs = [s for s in streams if s.get("Type") == "Subtitle"]
        if subs:
            langs = []
            for s in subs:
                lang = str(s.get("DisplayLanguage") or s.get("Language") or "").strip()
                if lang and lang not in langs:
                    langs.append(lang)
            add("Subtitles", ", ".join(langs[:8]) if langs else f"{len(subs)} track" + ("s" if len(subs) != 1 else ""))
    elif source:
        path = str(source.get("file_path") or "")
        ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        add("Container", ext.upper() if 0 < len(ext) <= 5 else "")
        add("File size", human_size(source.get("file_size")))
    return rows


def genre_set(genres: Any) -> set:
    return {g.strip().lower() for g in str(genres or "").split(",") if g.strip()}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def rank_candidates(
    base_genres: set,
    candidates: Sequence[Dict[str, Any]],
    tmdb_pos: Dict[int, int],
    vibe: Dict[int, float],
    limit: int = MORE_MAX,
) -> List[Dict[str, Any]]:
    """Orders library titles by how close they are to one title.

    candidates: {id, tmdb_id, genres, rating}. tmdb_pos: {tmdb id: position in TMDB's lists, 0 = first}.
    vibe: {our id: cosine}. A title needs a reason to be listed: it is on TMDB's lists, it is among the
    closest in the recommender's vibe index, or it shares at least half its genres. Returns the
    candidates (copies) with `score` and `why`, best first."""
    near = sorted(vibe.items(), key=lambda kv: -kv[1])[:MORE_MAX]
    vibe_rank = {i: n for n, (i, _) in enumerate(near)}
    out: List[Dict[str, Any]] = []
    for c in candidates:
        genres = genre_set(c.get("genres"))
        jac = jaccard(base_genres, genres)
        pos = tmdb_pos.get(c.get("tmdb_id")) if c.get("tmdb_id") is not None else None
        vr = vibe_rank.get(c["id"])
        if pos is None and vr is None and jac < 0.5:
            continue
        t_score = 0.0 if pos is None else max(0.5, 1.0 - pos / 40.0)
        v_score = 0.0 if vr is None else 1.0 - 0.5 * vr / max(1, MORE_MAX)
        score = 2.0 * t_score + 1.5 * v_score + 1.0 * jac + float(c.get("rating") or 0) / 100.0
        why = []
        signals = []
        if pos is not None:
            why.append("Similar on TMDB")
            signals.append("tmdb")
        if vr is not None:
            why.append("Close in feel")
            signals.append("recommender")
        shared = sorted(base_genres & genres)
        if shared:
            signals.append("genres")
            if len(why) < 2:
                why.append("Same genres: " + ", ".join(g.title() for g in shared[:3]))
        out.append({**c, "score": round(score, 4), "why": " · ".join(why[:2]), "signals": signals})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


# --------------------------------------------------------------------- service


class NLExtras:
    """Cast, trailer, tech details and "More like this" for one title.

    tmdb_get(path, params) -> dict or None (None = no key, no network or an error): the TMDB reader,
    handed in so tests need no network and so the key stays inside the server.
    vibe: a callable returning the recommender's VibeIndex (or None)."""

    def __init__(self, db: Any, browse: Any, tmdb_get: Callable[..., Optional[Dict[str, Any]]],
                 vibe: Optional[Callable[[], Any]] = None):
        self.db = db
        self.browse = browse
        self._tmdb_raw = tmdb_get
        self._vibe = vibe
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ tmdb

    def _tmdb(self, path: str) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(path)
            if hit and hit[0] > now:
                return hit[1]
        try:
            data = self._tmdb_raw(path, None)
        except Exception as e:  # noqa: BLE001 - TMDB is optional
            print(f"[NLExtras] TMDB {path.split('?')[0]} failed: {type(e).__name__}", flush=True)
            data = None
        if not isinstance(data, dict):
            data = None
        with self._lock:
            if len(self._cache) >= TMDB_CACHE_MAX:
                self._cache.clear()
            self._cache[path] = (now + (TMDB_TTL if data is not None else TMDB_FAIL_TTL), data)
        return data

    # ---------------------------------------------------------------- extras

    def _jellyfin_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """The Jellyfin item of this title (a show's series) as the current user; {} when unavailable."""
        try:
            if item["media_type"] == "tv":
                jf_id = self.browse.series_id(item)
            else:
                src = item["sources"][0]
                jf_id = self.browse._ids_for([src]).get(src["id"])
            return self.browse.pb.item_details(jf_id) if jf_id else {}
        except JellyfinUnavailable:
            return {}

    def title_extras(self, item_id: int) -> Optional[Dict[str, Any]]:
        item = self.db.get_nl_title(item_id)
        if not item:
            return None
        is_show = item["media_type"] == "tv"
        details = self._jellyfin_item(item)
        people = parse_people(details)
        trailer = None
        if item.get("tmdb_id"):
            trailer = pick_tmdb_trailer(self._tmdb(f"/{'tv' if is_show else 'movie'}/{int(item['tmdb_id'])}/videos"))
        trailer = trailer or jellyfin_trailer(details)
        tech = None if is_show else (tech_rows(details, item["sources"][0]) or None)
        return {
            "id": item["id"],
            "cast": people["cast"],
            "crew": people["crew"],
            "trailer": trailer,
            "tech": tech,
            "jellyfin": bool(details),
        }

    # ---------------------------------------------------------- more like this

    def _vibe_scores(self, item_id: int, ids: List[int]) -> Dict[int, float]:
        if self._vibe is None:
            return {}
        try:
            index = self._vibe()
            if index is None or not index.is_ready():
                return {}
            vec = index.item_vector(item_id)
            if vec is None:
                return {}
            return {i: s for i, s in index.similarities(vec, ids).items() if i != item_id}
        except Exception as e:  # noqa: BLE001 - the vibe index is optional
            print(f"[NLExtras] vibe index failed: {type(e).__name__}", flush=True)
            return {}

    def more_like_this(self, item_id: int) -> Optional[Dict[str, Any]]:
        item = self.db.get_nl_title(item_id)
        if not item:
            return None
        mtype = item["media_type"]
        rows = {r["id"]: r for r in self.browse._on_disk(mtype)}
        profiles = [p for p in self.db.get_library_profiles(mtype) if p["id"] in rows and p["id"] != item_id]

        tmdb_pos: Dict[int, int] = {}
        if item.get("tmdb_id"):
            for kind in ("similar", "recommendations"):
                data = self._tmdb(f"/{mtype}/{int(item['tmdb_id'])}/{kind}")
                results = (data or {}).get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    continue
                for pos, r in enumerate(results):
                    if isinstance(r, dict) and isinstance(r.get("id"), int):
                        tmdb_pos[r["id"]] = min(pos, tmdb_pos.get(r["id"], pos))
        vibe = self._vibe_scores(item_id, [p["id"] for p in profiles])

        ranked = rank_candidates(genre_set(item.get("genres")), profiles, tmdb_pos, vibe)
        engines = [e for e in ("tmdb", "recommender", "genres") if any(e in r["signals"] for r in ranked)]
        cards = []
        for r in ranked:
            card = self.browse._card(rows[r["id"]], None)
            card["reason"] = r["why"]
            cards.append(card)
        return {"items": cards, "engines": engines}
