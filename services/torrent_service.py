"""
Torrent Service (services/torrent_service.py)
Consolidates search, non-blocking download, cancelation, Jellyfin file organization
(Movies & TV Shows), and download progress tracking into a single service module.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import re
import shutil
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
import yaml

from services.disk_guard import check_download
from services.jellyfin_service import JellyfinService
from services.torrent_ranking import load_ranking_config, rank_results

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}

# Common 3-letter subtitle language codes → 2-letter Jellyfin codes
SUBTITLE_LANGUAGE_MAP = {
    "eng": "en", "english": "en",
    "fre": "fr", "fra": "fr", "french": "fr",
    "spa": "es", "spanish": "es",
    "deu": "de", "ger": "de", "german": "de",
    "ita": "it", "italian": "it",
    "jpn": "ja", "japanese": "ja",
    "kor": "ko", "korean": "ko",
    "chi": "zh", "zho": "zh", "chinese": "zh",
    "rus": "ru", "russian": "ru",
    "por": "pt", "portuguese": "pt", "bra": "pt-br",
    "dut": "nl", "nld": "nl", "dutch": "nl",
    "swe": "sv", "swedish": "sv",
    "nor": "no", "norwegian": "no",
    "dan": "da", "danish": "da",
    "fin": "fi", "finnish": "fi",
    "pol": "pl", "polish": "pl",
    "tur": "tr", "turkish": "tr",
    "ara": "ar", "arabic": "ar",
    "hin": "hi", "hindi": "hi",
    "tha": "th", "thai": "th",
    "vie": "vi", "viet": "vi",
    "ces": "cs", "cze": "cs", "czech": "cs",
    "hun": "hu", "hungarian": "hu",
    "heb": "he", "hebrew": "he",
    "ell": "el", "gre": "el", "greek": "el",
    "ron": "ro", "rum": "ro",
    "ind": "id", "ukr": "uk",
    "ben": "bn", "tam": "ta", "tel": "te", "kan": "kn", "mal": "ml",
}

# Known 2-letter codes accepted directly. Note: 'hi' is deliberately
# excluded — in release names it conventionally means hearing-impaired.
SUBTITLE_ISO2_CODES = {
    "en", "es", "fr", "de", "it", "ja", "ko", "zh", "ru", "pt", "nl",
    "sv", "no", "da", "fi", "pl", "tr", "ar", "th", "vi", "cs", "hu",
    "he", "el", "ro", "id", "uk", "bn", "ta", "te", "kn", "ml",
}

# Release-name tags that usually mean the TV client must transcode
TRANSCODE_HINT_TAGS = ("2160p", "4k", "uhd", "hevc", "h265", "x265", "dolby.vision", "hdr10", "hdr")


def _parse_sxxeyy(name: str) -> Optional[Tuple[int, int, Optional[int]]]:
    """Parses (season, episode, episode2|None) from a release filename.

    Handles `S01E02`, `1x02`, and multi-episode files like `S01E02-E03`
    (also `1x02-03`). Returns None when no episode tag is present.
    """
    lower = name.lower()
    m = re.search(
        r"(?<!\d)s(\d{1,2})e(\d{1,2})(?:\s*[-_. ]\s*(?:e|ep)?(\d{1,2}))?(?!\d)",
        lower,
    )
    if m:
        return int(m.group(1)), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)
    m = re.search(r"(?<!\d)(\d{1,2})x(\d{1,2})(?:\s*[-_. ]\s*(\d{1,2}))?(?!\d)", lower)
    if m:
        return int(m.group(1)), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)
    return None


def _parse_subtitle_tags(name: str) -> Tuple[str, List[str]]:
    """Parses (language, flags) from a subtitle filename.

    Language: 3-letter codes via SUBTITLE_LANGUAGE_MAP (eng→en, fre→fr,
    spa→es, deu→de…) or known 2-letter ISO codes; defaults to 'en'.
    Flags: preserves 'forced' and 'sdh'/'cc' markers found in the name.
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    tokens = re.split(r"[._\-\[\]() ]+", stem.lower())
    lang: Optional[str] = None
    flags: List[str] = []
    for tok in tokens:
        if not tok:
            continue
        if tok in ("forced", "forcedsub", "forcedsubs"):
            if "forced" not in flags:
                flags.append("forced")
        elif tok in ("sdh", "cc", "sdhsub"):
            if "sdh" not in flags:
                flags.append("sdh")
        elif tok in SUBTITLE_LANGUAGE_MAP:
            lang = SUBTITLE_LANGUAGE_MAP[tok]
        elif tok in SUBTITLE_ISO2_CODES:
            lang = tok
    return (lang or "en", flags)


def _log_transcode_hint(path: str) -> None:
    """Warns when a 4K/HEVC release may need transcoding on the TV client."""
    lower = os.path.basename(path).lower()
    tags = [t for t in TRANSCODE_HINT_TAGS if t in lower]
    if tags:
        print(
            f"[Organizer] ⚠️ 4K/HEVC-grade release detected ({', '.join(tags)}): "
            f"'{os.path.basename(path)}'. If it won't play on your TV, enable transcoding in "
            "Jellyfin (Dashboard → Playback) or grab a 1080p copy.",
            flush=True,
        )

_RELEASE_NOISE_TOKENS = {
    "2160p", "1080p", "720p", "480p", "web", "webrip", "webdl", "web-dl",
    "webdl-480p", "h264", "h265", "x264", "x265", "hevc", "aac", "aac2",
    "bluray", "blu-ray", "brrip", "bdrip", "dvdrip", "hdr", "hdr10", "dv",
    "proper", "repack", "amzn", "nf", "netflix", "hdtv", "dsnp", "atvp",
    "remux", "truehd", "atmos", "ddp", "dd5", "ac3", "dts", "5", "1", "2",
    "0", "multi", "subs", "rarbg", "yts", "yify", "ettv", "eztv", "tgx",
    "mp4", "mkv", "avi", "1080i", "2160i", "pal", "ntsc", "complete",
    "season", "s", "part", "pt",
}


def _release_show_tokens(filename: str) -> set:
    """Extracts plausible show-name tokens from a release filename.

    Cuts at the SxxEyy tag (or 1xYY), drops quality/release noise and digits,
    lowercases. Empty set when nothing usable remains.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r"(?i)(?<!\d)s\d{1,2}e\d{1,3}", stem)
    if not m:
        m = re.search(r"(?i)(?<!\d)\d{1,2}x\d{1,2}", stem)
    show_part = stem[: m.start()] if m else stem
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9']*", show_part)
    # Length filter drops junk fragments split off resolutions ("1080p"→"p")
    return {
        t.lower().strip("-._")
        for t in raw
        if len(t) > 1
        and t.lower().strip("-._") not in _RELEASE_NOISE_TOKENS
    }


def _show_name_mismatch(filename: str, clean_title: str) -> bool:
    """True when a video filename's show tokens share NO word with the title.

    Conservative: only fires when BOTH sides have usable tokens — a genuine
    'Game.of.Thrones.S01E01.mkv' inside a 'New Girl' download trips it, while
    oddly-named-but-plausible files never do.
    """
    file_tokens = _release_show_tokens(filename)
    title_tokens = {
        t.lower()
        for t in re.findall(r"[a-zA-Z][a-zA-Z0-9']*", clean_title)
        if t.lower() not in _RELEASE_NOISE_TOKENS
    }
    if not file_tokens or not title_tokens:
        return False
    return not (file_tokens & title_tokens)


def _clean_release_title(name: str) -> str:
    """Extracts a clean show/movie title from a torrent release name.

    "New Girl S05E12 D-Day 1080p DSNP WEB-DL DDP5 1 H 264-MiU"  → "New Girl"
    "Game of Thrones S01E01 Winter Is Coming 1080p BluRay x264-OFT"
        → "Game of Thrones"
    "They.Live.1988.REMASTERED.1080p.BluRay.H264.AAC-GoodFilms" → "They Live"

    Cuts at the episode tag, then keeps leading real words until the first
    noise token (quality/codec/group). Falls back to the input when nothing
    usable remains — never returns an empty string.
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"[._]+", " ", stem)
    m = re.search(r"(?i)(?<!\d)s\d{1,2}e\d{1,3}", stem)
    if not m:
        m = re.search(r"(?i)(?<!\d)\d{1,2}x\d{1,2}", stem)
    if m:
        stem = stem[: m.start()]
    words: List[str] = []
    for tok in stem.split():
        low = tok.lower().strip("-")
        if low in _RELEASE_NOISE_TOKENS or re.fullmatch(r"\d{4}", low) or not re.match(r"^[a-zA-Z]", tok):
            break
        words.append(tok)
    cleaned = " ".join(words).strip(" -")
    return cleaned if cleaned else name.strip()



def _remove_dir_tree_if_empty(path: str) -> bool:
    """Recursively removes empty directories bottom-up starting at path.

    Returns True when path itself no longer exists. Leftover non-media files
    keep the directory in place on purpose (never delete user data).
    """
    if not os.path.isdir(path):
        return False
    changed = True
    while changed:
        changed = False
        for root, dirs, files in os.walk(path, topdown=False):
            if not dirs and not files:
                try:
                    os.rmdir(root)
                    changed = True
                except OSError:
                    pass
    return not os.path.exists(path)

# ---------------------------------------------------------------------------
# Global in-memory download progress store
# Keyed by movie_id → status dict polled via GET /api/torrent/status/<id>
# ---------------------------------------------------------------------------
downloads_store: Dict[int, Dict[str, Any]] = {}


# ===========================================================================
# Prowlarr Searcher (optional aggregated provider, configured via .env)
# ===========================================================================
PROWLARR_CATEGORY_MAP = {"movie": "2000", "tv": "5000"}


def _magnet_info_hash(magnet: str) -> Optional[str]:
    """Extracts and normalizes the btih info-hash from a magnet link."""
    m = re.search(r"xt=urn:btih:([A-Za-z0-9]+)", magnet or "")
    if not m:
        return None
    return m.group(1).lower()


# ---------------------------------------------------------------------------
# Bencode (minimal) — converts Prowlarr's proxied .torrent grabs into magnets
# ---------------------------------------------------------------------------
def _bencode_decode(data: bytes, pos: int = 0):
    """Decodes one bencode value at pos. Returns (value, next_pos)."""
    c = data[pos:pos + 1]
    if c == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if c == b"l":
        pos += 1
        out = []
        while data[pos:pos + 1] != b"e":
            val, pos = _bencode_decode(data, pos)
            out.append(val)
        return out, pos + 1
    if c == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            key, pos = _bencode_decode(data, pos)
            val, pos = _bencode_decode(data, pos)
            out[key] = val
        return out, pos + 1
    if c.isdigit():
        end = data.index(b":", pos)
        length = int(data[pos:end])
        start = end + 1
        return data[start:start + length], start + length
    raise ValueError(f"bad bencode byte {c!r} at {pos}")


def _bencode_encode(obj) -> bytes:
    """Canonical bencode encoding (sorted dict keys) — required for info-hash."""
    if isinstance(obj, bool):
        raise ValueError("bool is not a valid bencode type")
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    if isinstance(obj, str):
        b = obj.encode()
        return str(len(b)).encode() + b":" + b
    if isinstance(obj, list):
        return b"l" + b"".join(_bencode_encode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        return b"d" + b"".join(
            _bencode_encode(k) + _bencode_encode(v) for k, v in sorted(obj.items())
        ) + b"e"
    raise ValueError(f"cannot encode {type(obj)}")


def torrent_url_to_magnet(url: str, timeout: int = 25) -> Optional[str]:
    """Fetches a .torrent URL (e.g. Prowlarr's proxied grab link) and builds a
    real magnet link from its info-hash (SHA-1 of the bencoded info dict),
    display name, and trackers. Returns None on any failure.
    """
    try:
        current = url
        torrent = None
        # Prowlarr's grab link may serve the .torrent directly or redirect to
        # the site's original magnet — handle both, following up to 5 hops.
        for _ in range(5):
            resp = requests.get(current, timeout=timeout, allow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                nxt = resp.headers.get("Location") or ""
                if nxt.startswith("magnet:"):
                    return nxt
                if not nxt:
                    return None
                current = nxt
                continue
            resp.raise_for_status()
            torrent, _ = _bencode_decode(resp.content)
            break
        if torrent is None:
            return None
        info = torrent[b"info"]
        info_hash = hashlib.sha1(_bencode_encode(info)).hexdigest()
        name = (info.get(b"name") or b"torrent").decode("utf-8", "replace")
        from urllib.parse import quote
        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(name)}"
        trackers = [torrent.get(b"announce")] + list(torrent.get(b"trackers") or [])
        for tracker in trackers:
            if isinstance(tracker, bytes) and tracker:
                magnet += "&tr=" + quote(tracker.decode("utf-8", "replace"))
        return magnet
    except Exception as e:
        print(f"[Downloader] Could not convert torrent URL to magnet: {e}", flush=True)
        return None


def _map_prowlarr_results(
    items: List[Dict[str, Any]],
    media_type: str,
) -> List[Dict[str, Any]]:
    """Maps raw Prowlarr search results into the searcher's result dict shape.

    Keeps torrent-protocol results only (NZBs dropped). A result's magnet is
    either a real magnet URL, or Prowlarr's proxied /download grab link which
    `download()` converts into a real magnet on demand. Dedup by info-hash
    (or title+size when the hash is unknown). Seed counts are NOT filtered
    here — several indexers report 0 seeders for live torrents — the
    downloader itself aborts dead/fake torrents. Malformed entries skipped.
    """
    mapped: List[Dict[str, Any]] = []
    seen_hashes: Set[str] = set()
    for item in items:
        try:
            if item.get("protocol") != "torrent":
                continue
            title = item.get("title") or ""
            size_bytes = int(item.get("size") or 0)
            # magnetUrl is either a real magnet or Prowlarr's proxied grab
            # link (which serves the .torrent file); download() converts
            # proxied links into real magnets on demand. downloadUrl is the
            # equivalent grab link kept as a fallback.
            magnet = item.get("magnetUrl") or item.get("downloadUrl") or ""
            if not magnet:
                continue
            info_hash = _magnet_info_hash(magnet)
            dedup_key = info_hash or f"{title.lower()}|{size_bytes}"
            if dedup_key in seen_hashes:
                continue
            seen_hashes.add(dedup_key)
            mapped.append({
                "name": title,
                "quality": _infer_quality(title),
                "size_bytes": size_bytes,
                "size_display": _format_size(size_bytes),
                "seeds": int(item.get("seeders") or 0),
                "peers": int(item.get("leechers") or 0),
                "magnet": magnet,
                "media_type": media_type,
            })
        except Exception:
            continue
    return mapped


class ProwlarrSearcher:
    """Searches a local Prowlarr instance when configured (.env), else disabled.

    PROWLARR_URL / PROWLARR_API_KEY in .env enable it. On any failure
    (unreachable, 401, HTTP error, malformed JSON) it warns and returns []
    so TorrentSearcher falls back to YTS / Apibay.
    """

    def __init__(self):
        from dotenv import load_dotenv
        load_dotenv()
        self.base_url = (os.getenv("PROWLARR_URL") or "").rstrip("/")
        self.api_key = os.getenv("PROWLARR_API_KEY")

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def search(self, query: str, media_type: str = "movie") -> List[Dict[str, Any]]:
        """Returns mapped torrent results, or [] on any failure (fallback kicks in)."""
        if not self.configured:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/search",
                headers={"X-Api-Key": self.api_key},
                params={
                    "query": query,
                    "type": "search",
                    "categories": PROWLARR_CATEGORY_MAP.get(media_type, "2000"),
                    "limit": 100,
                },
                timeout=25,
            )
        except requests.exceptions.RequestException as e:
            print(f"[Searcher] Prowlarr unreachable ({e}) — falling back.", flush=True)
            return []
        if resp.status_code == 401:
            print("[Searcher] Prowlarr 401: PROWLARR_API_KEY invalid — falling back.", flush=True)
            return []
        if resp.status_code != 200:
            print(f"[Searcher] Prowlarr HTTP {resp.status_code} — falling back.", flush=True)
            return []
        try:
            items = resp.json() or []
        except ValueError:
            print("[Searcher] Prowlarr returned malformed JSON — falling back.", flush=True)
            return []
        results = _map_prowlarr_results(items, media_type)
        if results:
            print(f"[Searcher] Prowlarr: {len(results)} torrent results across indexers.", flush=True)
        return results


# ===========================================================================
# Torrent Searcher
# ===========================================================================
class TorrentSearcher:
    """Searches YTS (for movies) and Apibay (for movies and TV shows).

    Returns results with name, quality badge, size_bytes, seeds, and magnet link.
    Sort modes: 'best' (combined score, see services/torrent_ranking.py), 'smallest' or 'quality'.
    """

    YTS_DOMAINS = ["https://yts.mx"]

    TRACKERS = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.demonii.com:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://explodie.org:6969/announce",
    ]

    # Minimum size thresholds (bytes)
    MIN_MOVIE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB for feature films
    MIN_TV_SIZE_BYTES = 100 * 1024 * 1024     # 100 MB for individual TV episodes

    def __init__(self, config: Optional[Dict] = None):
        self.ranking_cfg = load_ranking_config(config)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.prowlarr = ProwlarrSearcher()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        sort_by_size: bool = True,
        media_type: str = "movie",
        sort: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Searches torrent endpoints for movies or TV shows.

        Args:
            query: Movie or TV show title search string.
            sort_by_size: If True sort smallest-first (quick downloads).
                          If False sort by quality tier then seeders (best quality).
            media_type: 'movie' or 'tv'.
            sort: 'best' (combined score, default for the UI), 'smallest' or
                  'quality'. When given it wins over sort_by_size.
        """
        if sort is None:
            sort = "smallest" if sort_by_size else "quality"
        results: List[Dict[str, Any]] = []

        # 1. Preferred provider: Prowlarr (aggregated indexers) when configured
        #    via .env; on failure/empty it falls back to the direct endpoints.
        prowlarr_results = self.prowlarr.search(query, media_type=media_type)
        if prowlarr_results:
            results = prowlarr_results
        elif media_type == "tv":
            # YTS only indexes movies, so search Apibay for TV shows
            results = self._search_apibay(query, min_size=self.MIN_TV_SIZE_BYTES)
        else:
            # For movies, search YTS then fallback to Apibay
            results = self._search_yts(query)
            if not results:
                print("[Searcher] YTS returned no valid results. Trying Apibay backup...", flush=True)
                results = self._search_apibay(query, min_size=self.MIN_MOVIE_SIZE_BYTES)

        # Filter dead torrents
        active = [r for r in results if r["seeds"] >= 1]

        # Always score, so every result carries score / is_pack for the UI.
        rank_results(active, query, media_type, self.ranking_cfg)

        if sort == "smallest":
            active.sort(key=lambda x: x["size_bytes"])
        elif sort == "quality":
            # Quality tier (4K > 1080p > 720p) then seeds descending
            quality_order = {"2160p": 0, "4K": 0, "1080p": 1, "720p": 2}
            active.sort(
                key=lambda x: (quality_order.get(x.get("quality", ""), 3), -x["seeds"])
            )

        return active

    # ------------------------------------------------------------------
    # Internal: Tracker URL string
    # ------------------------------------------------------------------
    def _build_tracker_string(self) -> str:
        return "".join(f"&tr={requests.utils.quote(t)}" for t in self.TRACKERS)

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    # ------------------------------------------------------------------
    # Internal: YTS Search (Movies Only)
    # ------------------------------------------------------------------
    def _search_yts(self, query: str) -> List[Dict[str, Any]]:
        encoded_query = requests.utils.quote(query)
        tracker_str = self._build_tracker_string()
        norm_query = self._normalize(query)

        for base_url in self.YTS_DOMAINS:
            try:
                url = f"{base_url}/api/v2/list_movies.json?query_term={encoded_query}"
                response = self.session.get(url, timeout=8)

                if response.status_code != 200:
                    continue

                data = response.json()
                if data.get("status") != "ok" or data["data"].get("movie_count", 0) == 0:
                    continue

                results: List[Dict[str, Any]] = []
                for movie in data["data"]["movies"]:
                    movie_title = movie.get("title", "")
                    norm_movie_title = self._normalize(movie_title)

                    if norm_query not in norm_movie_title and norm_movie_title not in norm_query:
                        continue

                    for torrent in movie.get("torrents", []):
                        size_bytes = torrent.get("size_bytes", 0)
                        seeds = torrent.get("seeds", 0)
                        quality = torrent.get("quality", "")

                        if size_bytes < self.MIN_MOVIE_SIZE_BYTES or seeds < 1:
                            continue

                        info_hash = torrent["hash"]
                        display_name = f"{movie['title']} ({movie['year']}) [{quality}]"
                        magnet_uri = (
                            f"magnet:?xt=urn:btih:{info_hash}"
                            f"&dn={requests.utils.quote(display_name)}"
                            f"{tracker_str}"
                        )
                        results.append({
                            "name": display_name,
                            "quality": quality,
                            "size_bytes": size_bytes,
                            "size_display": _format_size(size_bytes),
                            "seeds": seeds,
                            "peers": torrent.get("peers", 0),
                            "magnet": magnet_uri,
                            "media_type": "movie",
                        })

                if results:
                    return results

            except requests.exceptions.RequestException as e:
                print(f"[Searcher] YTS request error: {e}", flush=True)
                continue

        return []

    # ------------------------------------------------------------------
    # Internal: Apibay Fallback (Movies & TV Shows)
    # ------------------------------------------------------------------
    def _search_apibay(self, query: str, min_size: int = 100 * 1024 * 1024) -> List[Dict[str, Any]]:
        try:
            url = f"https://apibay.org/q.php?q={requests.utils.quote(query)}"
            response = self.session.get(url, timeout=8)
            items = response.json() if response.status_code == 200 else []

            tracker_str = self._build_tracker_string()
            results: List[Dict[str, Any]] = []
            query_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]

            for item in items:
                if item.get("id") == "0":
                    break

                name = item.get("name", "")
                name_lower = name.lower()

                if query_words and not all(word in name_lower for word in query_words):
                    continue

                size_bytes = int(item.get("size", 0))
                seeds = int(item.get("seeders", 0))

                if size_bytes < min_size or seeds < 1:
                    continue

                quality = _infer_quality(name)
                info_hash = item["info_hash"]
                magnet = (
                    f"magnet:?xt=urn:btih:{info_hash}"
                    f"&dn={requests.utils.quote(name)}"
                    f"{tracker_str}"
                )

                results.append({
                    "name": name,
                    "quality": quality,
                    "size_bytes": size_bytes,
                    "size_display": _format_size(size_bytes),
                    "seeds": seeds,
                    "peers": int(item.get("leechers", 0)),
                    "magnet": magnet,
                    "media_type": "tv" if any(k in name_lower for k in ("s0", "s1", "season", "complete")) else "movie",
                })

            return results

        except Exception as e:
            print(f"[Searcher] Apibay search failed: {e}", flush=True)
            return []


def organize_media_files(
    staging_dir: str,
    movie_target: str,
    tv_target: str,
    clean_title: str,
    year: Optional[int],
    media_type: str = "movie",
    db: Any = None,
    issues: Optional[List[str]] = None,
) -> List[str]:
    """Pure filesystem organizer for the Jellyfin directory layout.

    Movies: <movie_target>/<Title> (<Year>)/<Title> (<Year>).<ext> with each
            subtitle copied beside it as <Title> (<Year>).<lang>[.flags].<ext>
    TV:     <tv_target>/<Show> (<Year>)/Season <XX>/<Show> - S<XX>E<YY>[-E<ZZ>].<ext>
            for EVERY video found (season packs), with subtitles paired via the
            SxxEyy tag parsed from their own filename.

    Filenames whose show tokens share no word with the requested title are
    organized anyway (never deleted) but reported into `issues` (if provided)
    with a loud log — a mislabeled/fake torrent must not pass silently.

    Unpairable subtitles are logged and skipped, every episode is registered
    in media_sources, and the staging directory is removed once emptied.
    Returns the list of organized destination paths.
    """
    organized: List[str] = []
    staging_abs = os.path.abspath(staging_dir)
    skip_roots = {os.path.abspath(movie_target), os.path.abspath(tv_target)}

    videos: List[str] = []
    subtitles: List[str] = []
    for root, _, files in os.walk(staging_abs):
        root_abs = os.path.abspath(root)
        if root_abs in skip_roots or any(root_abs.startswith(sr + os.sep) for sr in skip_roots):
            continue
        for f in files:
            full = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                videos.append(full)
            elif ext in SUBTITLE_EXTENSIONS:
                subtitles.append(full)

    if not videos:
        return organized

    videos.sort(key=lambda p: os.path.getsize(p), reverse=True)
    folder_name = f"{clean_title} ({year})" if year else clean_title

    if media_type != "tv":
        # ---- Movie: largest video becomes <Title> (<Year>).<ext> ----
        main_video = videos[0]
        if _show_name_mismatch(main_video, clean_title):
            msg = (
                f"possible mislabeled torrent: video '{os.path.basename(main_video)}' "
                f"shares no name token with requested title '{clean_title}'"
            )
            print(f"[Organizer] ⚠️ {msg}", flush=True)
            if issues is not None:
                issues.append(msg)
        ext = os.path.splitext(main_video)[1].lower()
        target_dir = os.path.join(movie_target, folder_name)
        os.makedirs(target_dir, exist_ok=True)
        dest = os.path.join(target_dir, f"{folder_name}{ext}")
        shutil.move(main_video, dest)
        organized.append(dest)
        print(f"[Organizer] Movie: {dest}", flush=True)
        _log_transcode_hint(main_video)

        if db:
            db.register_source_file(
                file_path=dest,
                parsed_title=clean_title,
                parsed_year=year,
                media_type="movie",
                file_size=os.path.getsize(dest),
                source_type="local",
            )

        # Copy every subtitle beside the movie with proper naming
        for sub in subtitles:
            lang, flags = _parse_subtitle_tags(os.path.basename(sub))
            parts = [folder_name, lang] + flags
            sub_dest = os.path.join(target_dir, ".".join(parts) + os.path.splitext(sub)[1])
            if os.path.abspath(sub) == os.path.abspath(sub_dest):
                continue  # already correctly named
            shutil.copy2(sub, sub_dest)
            try:
                os.remove(sub)  # clear staging so it can be removed once emptied
            except OSError:
                pass
            organized.append(sub_dest)
            print(f"[Organizer] Subtitle: {sub_dest}", flush=True)
    else:
        # ---- TV: every episode in staging + paired subtitles ----
        show_dir = os.path.join(tv_target, folder_name)
        sub_entries = []
        for sub in subtitles:
            parsed = _parse_sxxeyy(os.path.basename(sub))
            if parsed is None:
                print(
                    "[Organizer] WARNING: unpairable subtitle (no SxxEyy tag) skipped: "
                    f"{os.path.basename(sub)}",
                    flush=True,
                )
                continue
            lang, flags = _parse_subtitle_tags(os.path.basename(sub))
            sub_entries.append((parsed, sub, lang, flags))

        for video in videos:
            parsed = _parse_sxxeyy(os.path.basename(video))
            if parsed is None:
                print(
                    f"[Organizer] WARNING: no SxxEyy tag in '{os.path.basename(video)}' — skipped.",
                    flush=True,
                )
                continue
            if _show_name_mismatch(video, clean_title):
                msg = (
                    f"possible mislabeled torrent: episode '{os.path.basename(video)}' "
                    f"shares no name token with requested title '{clean_title}' — "
                    "organized under the requested show, but verify the content!"
                )
                print(f"[Organizer] ⚠️ {msg}", flush=True)
                if issues is not None:
                    issues.append(msg)
            s, e, e2 = parsed
            ext = os.path.splitext(video)[1].lower()
            season_dir = os.path.join(show_dir, f"Season {s:02d}")
            os.makedirs(season_dir, exist_ok=True)
            ep_tag = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
            dest = os.path.join(season_dir, f"{clean_title} - {ep_tag}{ext}")
            shutil.move(video, dest)
            organized.append(dest)
            print(f"[Organizer] Episode: {dest}", flush=True)
            _log_transcode_hint(video)

            if db:
                db.register_source_file(
                    file_path=dest,
                    parsed_title=clean_title,
                    parsed_year=year,
                    media_type="tv",
                    season_number=s,
                    episode_number=e,
                    file_size=os.path.getsize(dest),
                    source_type="local",
                )

        # Subtitles land in their own parsed Season folder, named after their
        # own episode tag so Jellyfin pairs them automatically.
        for (s, e, e2), sub, lang, flags in sub_entries:
            season_dir = os.path.join(show_dir, f"Season {s:02d}")
            os.makedirs(season_dir, exist_ok=True)
            ep_tag = f"S{s:02d}E{e:02d}" + (f"-E{e2:02d}" if e2 else "")
            parts = [f"{clean_title} - {ep_tag}", lang] + flags
            sub_dest = os.path.join(season_dir, ".".join(parts) + os.path.splitext(sub)[1])
            if os.path.abspath(sub) == os.path.abspath(sub_dest):
                organized.append(sub_dest)
                continue  # already correctly named
            shutil.copy2(sub, sub_dest)
            try:
                os.remove(sub)  # clear staging so it can be removed once emptied
            except OSError:
                pass
            organized.append(sub_dest)
            print(f"[Organizer] Subtitle: {sub_dest}", flush=True)

    # Clean up the staging directory once it has been emptied
    if _remove_dir_tree_if_empty(staging_abs):
        print(f"[Organizer] Staging directory emptied and removed: {staging_abs}", flush=True)
    elif os.path.exists(staging_abs):
        print(
            f"[Organizer] Staging directory kept (non-media files remain): {staging_abs}",
            flush=True,
        )

    return organized


# ===========================================================================
# Torrent Downloader & Jellyfin Organizer
# ===========================================================================
class TorrentDownloader:
    """Manages non-blocking torrent downloads via libtorrent.

    Features:
    - Launching background downloads.
    - Real-time download cancellation.
    - Automated file organizing for Movies and TV Shows.
    - Post-completion Jellyfin library refresh.
    """

    def __init__(self, config: Dict[str, Any], db_manager=None):
        import libtorrent as lt

        self.lt = lt
        # libtorrent 2.x dropped the 1.x `lt.session({'resume': True,
        # 'resume_dir': ...})` settings — passing them now raises
        # "unknown name in settings_pack: resume". Persistence in 2.x is
        # driven per-torrent: `add_torrent_params.resume_data` carries state
        # loaded from `lt.read_resume_data()`, and `save_resume_data_alert`
        # tells us when the session has flushed it to disk. The session's own
        # resume directory is `download_dir`, so flushed .ltresume files land
        # beside the partial data they describe.
        self.resume_dir = os.path.abspath(
            (config.get("torrent", {}) or {}).get("download_dir", "./downloads")
        )
        self.session = lt.session()
        self.db = db_manager
        self.config = config

        # Jellyfin library roots: prefer explicit jellyfin_library config,
        # fall back to legacy media_folders[0] so existing setups keep working.
        torrent_cfg = config.get("torrent", {})
        media_folders = config.get("media_folders", []) or []
        lib_cfg = config.get("jellyfin_library", {}) or {}
        if lib_cfg.get("movie_dir"):
            self.movie_target = os.path.abspath(lib_cfg["movie_dir"])
        elif media_folders:
            self.movie_target = os.path.abspath(media_folders[0])
        else:
            self.movie_target = os.path.abspath(torrent_cfg.get("download_dir", "./downloads"))
        self.tv_target = os.path.abspath(lib_cfg.get("tv_dir") or self.movie_target)

        # Staging area for in-progress downloads (separate from targets). Folder names carry the Homelab
        # environment (dev, prod) so two environments sharing one media folder never touch each other's.
        self.download_dir = os.path.abspath(torrent_cfg.get("download_dir", "./downloads"))
        env = os.getenv("HOMELAB_ENV", "").strip()
        self.staging_prefix = f"{re.sub(r'[^A-Za-z0-9_.-]', '', env)}-id_" if env else "id_"
        # The media folder can be missing (Homelab gives dev none): the app still runs, without downloads.
        self.media_available = True
        try:
            for folder in (self.movie_target, self.tv_target, self.download_dir):
                os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.media_available = False
            print(f"[Downloader] Media folder not available ({e}); downloads and the library are off.", flush=True)

        # Jellyfin library manager (create / refresh / verify)
        self.jellyfin = JellyfinService(config)

        print(f"[Downloader] Jellyfin movie target: {self.movie_target}", flush=True)
        print(f"[Downloader] Jellyfin TV target:    {self.tv_target}", flush=True)
        print(f"[Downloader] Download staging:      {self.download_dir}", flush=True)

        min_port = torrent_cfg.get("min_port", 6881)
        settings = {
            "listen_interfaces": f"0.0.0.0:{min_port}",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            "peer_fingerprint": "SM",
            "dht_bootstrap_nodes": (
                "router.bittorrent.com:6881,"
                "router.utorrent.com:6881,"
                "dht.transmissionbt.com:6881"
            ),
        }
        if "settings" in torrent_cfg:
            settings.update(torrent_cfg["settings"])

        self.session.apply_settings(settings)
        if hasattr(self.session, "start_dht"):
            self.session.start_dht()
        self.torrent_handles: Dict[str, Any] = {}
        self.cancelled_tasks: Set[int] = set()

    # ------------------------------------------------------------------
    # Cancellation & Cleanup
    # ------------------------------------------------------------------
    async def cleanup(self, info_hash: str) -> None:
        """Removes a completed or aborted torrent from the libtorrent session."""
        try:
            h = info_hash.lower()
            if h in self.torrent_handles:
                self.session.remove_torrent(self.torrent_handles[h])
                del self.torrent_handles[h]
        except Exception as e:
            print(f"[Downloader] Cleanup error: {e}", flush=True)

    async def _cancel_cleanup(self, info_hash: str, movie_id: int) -> None:
        """Removes a cancelled torrent (with partial files) and finalizes status."""
        # Remove the torrent from the session, deleting partial data
        hash_str = str(info_hash).lower()
        handle = self.torrent_handles.get(hash_str)
        if handle is not None:
            try:
                flags = int(getattr(getattr(self.lt, "options_t", None), "delete_files", 0) or 1)
                self.session.remove_torrent(handle, flags)
            except Exception:
                pass
        await self.cleanup(info_hash)
        self._purge_partial_files(movie_id)

        if movie_id in downloads_store:
            downloads_store[movie_id]["status"] = "cancelled"
            downloads_store[movie_id]["error"] = "Download cancelled by user"
            downloads_store[movie_id]["progress"] = 0.0
        self.cancelled_tasks.discard(movie_id)
        print(f"[Downloader] Cancelled & cleaned up: ID {movie_id}", flush=True)

    def _torrent_staging_dir(self, movie_id: int) -> str:
        """Per-torrent staging subdir — one torrent NEVER sees another's files."""
        path = self._staging_path(movie_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _staging_path(self, movie_id: int) -> str:
        return os.path.join(self.download_dir, f"{self.staging_prefix}{movie_id}")

    def remove_orphan_staging(self, owned_ids: Set[int]) -> List[str]:
        """Deletes this environment's staging folders that no stored download owns (left by a crash or a
        restart that lost the download). Other environments' folders and the .ltresume files stay."""
        removed: List[str] = []
        # Before the environment was in the name, prod's folders were plain id_<n>; they still count as prod's.
        env = os.getenv("HOMELAB_ENV", "").strip()
        prefixes = [self.staging_prefix] + (["id_"] if env == "prod" else [])
        pattern = re.compile(rf"^(?:{'|'.join(re.escape(p) for p in prefixes)})(\d+)$")
        try:
            names = os.listdir(self.download_dir)
        except OSError:
            return removed
        for name in names:
            m = pattern.match(name)
            path = os.path.join(self.download_dir, name)
            if m and int(m.group(1)) not in owned_ids and os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                removed.append(name)
                print(f"[Downloader] Removed orphan staging folder: {path}", flush=True)
        return removed

    def _resume_file_for(self, info_hash: str) -> str:
        """Path to the persisted resume file for a torrent."""
        return os.path.join(self.resume_dir, f"{info_hash}.ltresume")

    @staticmethod
    def _info_hash_from_magnet(magnet_link: str) -> Optional[str]:
        """Extract the hex info hash from a magnet URI.

        `magnet:?xt=urn:btih:<40 hex>` (v1) or `urn:btmh:1220<hex>` (v2). The
        hash is what libtorrent names resume files by, so this is how a retry
        after a restart finds its own partial download again.
        """
        try:
            if "btih:" in magnet_link:
                h = magnet_link.rsplit("btih:", 1)[-1].split("&")[0].split(" ")[0].lower()
                return h if len(h) >= 32 else None
            if "btmh:" in magnet_link:
                h = magnet_link.rsplit("btmh:", 1)[-1].split("&")[0].split(" ")[0]
                return h if len(h) >= 40 else None
        except Exception:
            pass
        return None

    def _load_resume_params(self, magnet_link: str):
        """Return add_torrent_params carrying saved state, or None.

        libtorrent 2.x has no 1.x-style `lt.session({'resume': True})`. The
        working loop is: flush with `lt.write_resume_data()` -> bencode to
        bytes -> read back with `lt.read_resume_data()` -> hand straight to
        `session.add_torrent()`. Verified against libtorrent 2.1.1 here.

        Any failure returns None so a corrupt, stale, or version-mismatched
        resume file degrades to a fresh download instead of blocking it.
        """
        lt = self.lt
        info_hash = self._info_hash_from_magnet(magnet_link)
        if not info_hash:
            return None
        path = self._resume_file_for(info_hash)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as fh:
                params = lt.read_resume_data(fh.read())
            if params is None:
                return None
            # The saved save_path can point at a staging dir from a previous
            # run that no longer exists; the caller overrides it anyway.
            return params
        except Exception as e:
            print(f"[Downloader] Resume data unusable ({info_hash[:12]}): {e}", flush=True)
            return None

    def _persist_resume_data(self, params) -> None:
        """Flush current torrent state to its .ltresume file (best effort)."""
        lt = self.lt
        try:
            info_hash = str(params.info_hashes[0]) if params.info_hashes else None
            if not info_hash:
                return
            path = self._resume_file_for(info_hash)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = lt.write_resume_data(params)
            with open(path, "wb") as fh:
                fh.write(lt.bencode(data))
        except Exception as e:
            # Persistence is an optimisation; a failure here must never abort
            # an otherwise healthy download.
            print(f"[Downloader] Could not persist resume data: {e}", flush=True)

    def _resume_file_for_handle(self, handle) -> Optional[str]:
        """Resolve the .ltresume path for a live torrent handle."""
        try:
            hashes = handle.info_hashes()
            if not hashes:
                return None
            return self._resume_file_for(str(hashes[0]))
        except Exception:
            return None

    def _flush_handle_resume(self, handle) -> bool:
        """Write a live handle's state to disk. Best effort; never raises.

        libtorrent 2.1 exposes no `session.save_resume_data`; the working call
        is `handle.write_resume_data()`, which returns the resume dict
        (deprecated in favour of the alert flow, but the only synchronous
        option here). The torrent's own info hash — the name its resume file
        uses — is carried inside that dict as `b'info-hash'`, so no
        `info_hashes()` stringification is needed (that object is not
        subscriptable or printable in 2.x).
        """
        lt = self.lt
        try:
            data = handle.write_resume_data()
        except Exception as e:
            print(f"[Downloader] write_resume_data failed: {e}", flush=True)
            return False
        if not data:
            return False

        try:
            raw = data.get(b'info-hash') if isinstance(data, dict) else None
            if not raw:
                return False
            if isinstance(raw, (bytes, bytearray)):
                info_hash = raw.hex().lower()
            elif hasattr(raw, "hex"):
                info_hash = raw.hex().lower()
            else:
                info_hash = str(raw).lower()
            path = self._resume_file_for(info_hash)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(lt.bencode(data))
            return True
        except Exception as e:
            # Persistence is an optimisation; a failure must never abort an
            # otherwise healthy download.
            print(f"[Downloader] Could not persist resume data: {e}", flush=True)
            return False

    def _clear_resume_file(self, info_hash: str) -> None:
        """Delete one torrent's .ltresume once its content is fully organized."""
        try:
            os.remove(self._resume_file_for(info_hash))
        except OSError:
            pass

    def _has_partial_data(self, movie_id: int) -> float:
        """Bytes already present in this torrent's staging dir (for reporting)."""
        staging = self._torrent_staging_dir(movie_id)
        total = 0
        for root, _dirs, files in os.walk(staging):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    def _purge_partial_files(self, movie_id: int) -> None:
        """Removes the per-torrent staging directory (partial files + leftovers)."""
        staging = self._staging_path(movie_id)
        try:
            if os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)
                print(f"[Downloader] Purged staging dir: {staging}", flush=True)
        except Exception as e:
            print(f"[Downloader] Partial-file purge error: {e}", flush=True)

    async def cancel(self, movie_id: int) -> bool:
        """Cancels an active download task for a given movie/show ID."""
        print(f"[Downloader] Cancellation requested for ID: {movie_id}", flush=True)
        self.cancelled_tasks.add(movie_id)

        status_info = downloads_store.get(movie_id)
        if not status_info:
            return False

        # Flag cancellation immediately for instant UI feedback.
        # The download loop performs the torrent cleanup and partial-file
        # purge itself, so we never race handle.status() on a removed torrent.
        downloads_store[movie_id]["status"] = "cancelled"
        downloads_store[movie_id]["error"] = "Download cancelled by user"
        return True

        return False

    # ------------------------------------------------------------------
    # Automated File Organization (Jellyfin Specification)
    # ------------------------------------------------------------------
    def _organize_for_jellyfin(
        self,
        movie_id: int,
        title: str,
        media_type: str = "movie",
    ) -> List[str]:
        """Moves staged files into the clean Jellyfin directory structure.

        - Movie: <movie_target>/<Title> (<Year>)/<Title> (<Year>).<ext> + subtitles
        - TV:    <tv_target>/<Show> (<Year>)/Season <XX>/<Show> - S<XX>E<YY>.<ext>
                 for EVERY episode in staging (+ paired subtitles)

        Afterwards a non-blocking async poller refreshes Jellyfin and verifies
        the title actually became visible (updates jellyfin_visible).
        """
        clean_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()

        year = None
        if self.db:
            item = self.db.get_item_by_id(movie_id)
            if item:
                year = item.get("release_year")
                if item.get("media_type"):
                    media_type = item["media_type"]

        issues: List[str] = []
        organized = organize_media_files(
            staging_dir=self._torrent_staging_dir(movie_id),
            movie_target=self.movie_target,
            tv_target=self.tv_target,
            clean_title=clean_title,
            year=year,
            media_type=media_type,
            db=self.db,
            issues=issues,
        )

        # Surface mislabeled-torrent warnings to the UI status store.
        if issues and movie_id in downloads_store:
            downloads_store[movie_id]["mislabeled_hint"] = " ".join(issues)

        if not organized:
            print(f"[Downloader] No video file found to organize for '{title}'.", flush=True)
            return organized

        # Kick off the async Jellyfin refresh + visibility verification poller
        try:
            asyncio.get_running_loop().create_task(
                self._verify_in_jellyfin(movie_id, title, media_type)
            )
        except RuntimeError:
            # No running event loop (e.g. manual invocation): verify inline.
            result = self.jellyfin.refresh_and_verify(title, media_type)
            if movie_id in downloads_store:
                downloads_store[movie_id]["jellyfin_visible"] = bool(result.get("visible"))
        return organized

    async def _verify_in_jellyfin(
        self,
        movie_id: int,
        title: str,
        media_type: str,
        timeout_s: int = 120,
    ) -> None:
        """Async poller: refreshes Jellyfin and confirms the title is visible.

        Runs the blocking HTTP polling in a worker thread so the event loop is
        never blocked, then updates downloads_store with the result.
        """
        try:
            result = await asyncio.to_thread(
                self.jellyfin.refresh_and_verify, title, media_type, timeout_s
            )
        except Exception as e:
            print(f"[Downloader] Jellyfin verification error for '{title}': {e}", flush=True)
            return

        visible = bool(result.get("visible"))
        if movie_id in downloads_store:
            downloads_store[movie_id]["jellyfin_visible"] = visible
            if result.get("hint"):
                downloads_store[movie_id]["jellyfin_hint"] = result["hint"]

        if visible:
            print(f"✅ '{title}' is now visible in Jellyfin", flush=True)
        else:
            print(
                f"⚠️ '{title}' not visible in Jellyfin after {timeout_s}s — "
                f"hint: {result.get('hint')}",
                flush=True,
            )

    # ------------------------------------------------------------------
    # Non-blocking Download Task
    # ------------------------------------------------------------------
    async def download(
        self,
        magnet_link: str,
        movie_id: int,
        movie_title: str,
        media_type: str = "movie",
    ) -> None:
        """Non-blocking download task. Updates downloads_store every second."""
        lt = self.lt
        print(f"\n[Downloader] Starting: '{movie_title}' [ID: {movie_id}, {media_type.upper()}]", flush=True)

        # Reset cancellation flag
        if movie_id in self.cancelled_tasks:
            self.cancelled_tasks.remove(movie_id)

        if not self.media_available:
            downloads_store[movie_id] = {
                "movie_id": movie_id,
                "status": "error",
                "error": "This server has no media folder, so it cannot download.",
            }
            return
        try:
            if magnet_link.startswith("http"):
                # Prowlarr proxied grab link: convert .torrent → magnet on demand
                print("[Downloader] Converting Prowlarr grab link to magnet...", flush=True)
                converted = torrent_url_to_magnet(magnet_link)
                if not converted:
                    downloads_store[movie_id] = {
                        "movie_id": movie_id,
                        "status": "error",
                        "error": "Could not convert Prowlarr grab link to a magnet link.",
                    }
                    return
                magnet_link = converted
            params = lt.parse_magnet_uri(magnet_link)
            # Per-torrent staging dir: the organizer only ever sweeps THIS
            # torrent's files — concurrent downloads can never contaminate
            # each other's show folders (the bug that mixed GoT into New Girl).
            staging_dir = self._torrent_staging_dir(movie_id)
            params.save_path = staging_dir

            # Resume from a previous run if this torrent has saved state and its
            # partial data is still on disk. `_load_resume_params` returns fully
            # reconstructed params (including trackers), so parse_magnet_uri above
            # is only needed on a cold start.
            resumed_from = self._load_resume_params(magnet_link)
            resumed = False
            if resumed_from is not None:
                resumed_from.save_path = staging_dir
                params = resumed_from
                partial_bytes = self._has_partial_data(movie_id)
                resumed = partial_bytes > 0
                if resumed:
                    print(
                        f"[Downloader] Resuming '{movie_title}': "
                        f"{partial_bytes / (1024 ** 2):.1f} MB already on disk",
                        flush=True,
                    )
                else:
                    # Resume file exists but the data is gone (cleaned up or
                    # moved); starting fresh is the only correct option.
                    resumed = False

            handle = self.session.add_torrent(params)
            info_hash = str(handle.info_hash()).lower()
            self.torrent_handles[info_hash] = handle

            downloads_store[movie_id] = {
                "hash": info_hash,
                "movie_id": movie_id,
                "name": movie_title,
                "media_type": media_type,
                "progress": 0.0,
                "status": "connecting",
                "download_speed": 0,
                "total_size": 0,
                "downloaded": 0,
                "error": None,
            }

            min_threshold = self.TorrentSearcher_min_size(media_type)

            # Declared outside the polling loop: inside it, the counter would
            # reset to 0 every second and flush resume data on every tick
            # instead of every 30s.
            last_resume_flush = time.time()
            disk_checked = False

            while True:
                await asyncio.sleep(1)

                # Check if cancelled by user (loop owns cleanup to avoid races)
                if movie_id in self.cancelled_tasks:
                    print(f"[Downloader] Aborting cancelled task: '{movie_title}'", flush=True)
                    await self._cancel_cleanup(info_hash, movie_id)
                    return

                # Poll torrent status safely — a removed/invalid handle must
                # never clobber the 'cancelled' status set by cancel().
                try:
                    status = handle.status()
                    has_meta = handle.has_metadata()
                    state_str = str(status.state)
                except Exception:
                    if movie_id in self.cancelled_tasks:
                        await self._cancel_cleanup(info_hash, movie_id)
                        return
                    raise

                total_wanted = status.total_wanted if has_meta else 0
                total_done = status.total_done if has_meta else 0

                if has_meta and total_wanted > 0:
                    progress_pct = round((total_done / total_wanted) * 100, 2)
                    torrent_name = handle.name()
                else:
                    progress_pct = 0.0
                    torrent_name = movie_title

                # ETA from instantaneous speed. `download_rate` is bytes/sec,
                # so remaining/seconds gives a time estimate; without it the UI
                # showed a bare percentage with no sense of how long was left.
                remaining = max(0, total_wanted - total_done) if has_meta else 0
                if status.download_rate > 0 and remaining > 0:
                    eta_seconds = int(remaining / status.download_rate)
                else:
                    eta_seconds = None

                # Disk guard: checked once, as soon as the size is known.
                if has_meta and total_wanted > 0 and not disk_checked:
                    disk_checked = True
                    refusal = check_download(
                        self.config, total_wanted, downloads_store, exclude_id=movie_id
                    )
                    if refusal:
                        downloads_store[movie_id] = {
                            "movie_id": movie_id,
                            "status": "error",
                            "error": refusal,
                        }
                        await self.cleanup(info_hash)
                        self._purge_partial_files(movie_id)
                        return

                # Abort fake / shortcut torrents
                if has_meta and total_wanted < min_threshold:
                    downloads_store[movie_id] = {
                        "movie_id": movie_id,
                        "status": "error",
                        "error": "File size too small (fake torrent link).",
                    }
                    await self.cleanup(info_hash)
                    return

                # Bail out before clobbering a just-issued cancel request
                if movie_id in self.cancelled_tasks:
                    await self._cancel_cleanup(info_hash, movie_id)
                    return

                downloads_store[movie_id] = {
                    "hash": info_hash,
                    "movie_id": movie_id,
                    "name": torrent_name,
                    "media_type": media_type,
                    "progress": progress_pct,
                    "status": state_str,
                    "download_speed": status.download_rate,
                    "total_size": total_wanted,
                    "downloaded": total_done,
                    "eta_seconds": eta_seconds,
                    "resumed": resumed,
                    "peers": status.num_peers,
                    "seeds": status.num_seeds,
                    "error": None,
                }

                # Flush resume data periodically so a crash or restart doesn't lose the
                # partial download. Every 30s is frequent enough to bound lost
                # progress without hammering the disk.
                now = time.time()
                if now - last_resume_flush >= 30:
                    self._flush_handle_resume(handle)
                    last_resume_flush = now

                is_complete = (
                    has_meta
                    and total_wanted > 0
                    and total_done >= total_wanted
                    and state_str in ("seeding", "finished")
                )

                if is_complete:
                    downloads_store[movie_id]["progress"] = 100.0
                    downloads_store[movie_id]["status"] = "organizing"
                    downloads_store[movie_id]["eta_seconds"] = 0

                    self._organize_for_jellyfin(movie_id, movie_title, media_type)

                    downloads_store[movie_id]["status"] = "completed"
                    # The file is now in the Jellyfin library, so the resume
                    # file is stale — drop it or a future re-add would try to
                    # resume an already-finished torrent.
                    self._clear_resume_file(info_hash)
                    await self.cleanup(info_hash)
                    return

        except Exception as e:
            if movie_id in self.cancelled_tasks:
                # Cancelled downloads must not surface as errors
                await self._cancel_cleanup(info_hash, movie_id)
                return
            print(f"[Downloader] Error: {e}", flush=True)
            downloads_store[movie_id] = {
                "movie_id": movie_id,
                "status": "error",
                "error": str(e),
            }

    @staticmethod
    def TorrentSearcher_min_size(media_type: str) -> int:
        if media_type == "tv":
            return 80 * 1024 * 1024  # 80 MB
        return 450 * 1024 * 1024      # 450 MB


# ===========================================================================
# Helpers
# ===========================================================================
def _format_size(size_bytes: int) -> str:
    """Returns a human-readable file size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _infer_quality(name: str) -> str:
    """Infers quality tag from a torrent filename."""
    lower = name.lower()
    if "2160p" in lower or "4k" in lower or "uhd" in lower:
        return "2160p"
    if "1080p" in lower:
        return "1080p"
    if "720p" in lower:
        return "720p"
    if "480p" in lower:
        return "480p"
    return "HD"
