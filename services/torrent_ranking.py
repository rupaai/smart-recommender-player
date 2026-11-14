"""Combined "Best match" score for torrent copies (quality x seeders x size fit).

score = quality points x seeder health x size fit x scope match

All the tuning numbers live in config.yaml under `torrent_ranking:`; the
DEFAULTS below are used for anything left out. Pure functions, no network.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

GB = 1024 ** 3

DEFAULTS: Dict[str, Any] = {
    # Points per resolution tag (_infer_quality values; anything else -> "HD").
    "quality_points": {"2160p": 100, "1080p": 80, "720p": 55, "HD": 45, "480p": 25},
    # Seeders needed to finish comfortably = base + per_gb * size_gb.
    # Health = sqrt(seeders / needed), capped at 1.
    "seeders": {"base": 5, "per_gb": 2},
    # Multiplier for a file far smaller/bigger than normal for its quality.
    "size_fit_penalty": 0.75,
    # Normal [min, max] GB per film / per episode, by quality.
    "movie_size_bands_gb": {
        "2160p": [4, 30], "1080p": [1.4, 15], "720p": [0.6, 4], "HD": [0.6, 6], "480p": [0.3, 2],
    },
    "episode_size_bands_gb": {
        "2160p": [1.5, 10], "1080p": [0.5, 4], "720p": [0.2, 1.5], "HD": [0.2, 2], "480p": [0.1, 0.8],
    },
    # TV: a season pack when you searched an episode (or the reverse) scores x this.
    "scope_mismatch_multiplier": 0.5,
    # TV: episodes assumed per season when the name doesn't say.
    "episodes_per_season": 10,
    # "Best pick" (the copy a one-tap Download takes): the best quality that still has decent seeders.
    # Decent = at least min_seeders + per_gb x size in GB, so a big file needs more people sharing it.
    "best_pick": {"min_seeders": 10, "per_gb": 0.25},
}


def load_ranking_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """DEFAULTS overlaid with config['torrent_ranking'] (one level deep)."""
    cfg = copy.deepcopy(DEFAULTS)
    user = (config or {}).get("torrent_ranking") or {}
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


_EPISODE_RE = re.compile(r"(?i)(?<![a-z0-9])(?:s\d{1,2}[\s._-]?e\d{1,3}|\d{1,2}x\d{1,3})(?![a-z0-9])")
_SEASON_RANGE_RE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})\s*[-–]\s*s?(\d{1,2})(?![a-z0-9])")
_SEASONS_WORD_RE = re.compile(r"(?i)seasons?\s*(\d{1,2})\s*[-–]\s*(\d{1,2})")
_SEASON_RE = re.compile(r"(?i)(?<![a-z0-9])(?:s\d{1,2}|season[\s._-]*\d{1,2})(?![a-z0-9])")
_PACK_WORDS_RE = re.compile(r"(?i)\b(complete|full[\s._-]*series|all[\s._-]*seasons)\b")


def classify_release(name: str) -> Tuple[str, int]:
    """('episode'|'pack'|'other', episode_count) from a release name."""
    if _EPISODE_RE.search(name):
        return "episode", 1
    m = _SEASON_RANGE_RE.search(name) or _SEASONS_WORD_RE.search(name)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return "pack", max(1, hi - lo + 1)
    if _SEASON_RE.search(name) or _PACK_WORDS_RE.search(name):
        return "pack", 1
    return "other", 1


def query_scope(query: str) -> str:
    """What the search asked for: 'episode', 'season' or 'any'."""
    if _EPISODE_RE.search(query):
        return "episode"
    if _SEASON_RE.search(query):
        return "season"
    return "any"


def score_result(
    result: Dict[str, Any],
    media_type: str,
    scope: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Returns the result's score plus the numbers behind it (for display/tests)."""
    quality = result.get("quality") or "HD"
    points_map = cfg["quality_points"]
    points = points_map.get(quality, points_map.get("HD", 45))
    size_gb = (result.get("size_bytes") or 0) / GB
    seeds = result.get("seeds") or 0

    kind = "other"
    episodes = 1
    if media_type == "tv":
        kind, count = classify_release(result.get("name", ""))
        if kind == "pack":
            # count is seasons for ranges, or 1 -> assume one season's worth.
            episodes = max(1, count) * int(cfg["episodes_per_season"])

    seeders_cfg = cfg["seeders"]
    needed = seeders_cfg["base"] + seeders_cfg["per_gb"] * size_gb
    health = min(1.0, math.sqrt(seeds / needed)) if needed > 0 else 1.0

    bands = cfg["episode_size_bands_gb" if media_type == "tv" else "movie_size_bands_gb"]
    fit = 1.0
    too_small = False
    band = bands.get(quality)
    if band and size_gb > 0:
        per_item = size_gb / episodes
        too_small = per_item < band[0]
        if too_small or per_item > band[1]:
            fit = cfg["size_fit_penalty"]

    scope_mult = 1.0
    if media_type == "tv":
        if (scope == "season" and kind == "episode") or (scope == "episode" and kind == "pack"):
            scope_mult = cfg["scope_mismatch_multiplier"]

    return {
        "score": round(points * health * fit * scope_mult, 1),
        "seed_health": round(health, 2),
        "is_pack": kind == "pack",
        "episodes": episodes if kind == "pack" else None,
        "per_episode_bytes": int(result["size_bytes"] / episodes) if kind == "pack" and result.get("size_bytes") else None,
        "too_small": too_small,
    }


def decent_seeders(size_bytes: Optional[int], cfg: Dict[str, Any]) -> int:
    """Seeders a copy this size needs to count as "decent" for the best pick."""
    bp = cfg.get("best_pick") or DEFAULTS["best_pick"]
    return int(math.ceil(float(bp.get("min_seeders", 10)) + float(bp.get("per_gb", 0)) * (size_bytes or 0) / GB))


def best_pick_order(results: List[Dict[str, Any]], media_type: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Copies in "best pick" order: those with decent seeders first, best quality first among them
    (a copy far too small for its quality, often a fake, after all the others), then the
    bigger file (a remux beats an encode of the same resolution), then most seeders. Big files are never pushed down for being big. Copies without decent seeders follow in
    "Best match" order. Marks each copy `seeds_ok` and the first one `best_pick`. Scores the copies
    first when they have no score yet."""
    points = cfg["quality_points"]
    for r in results:
        if "score" not in r or "too_small" not in r:
            r.update(score_result(r, media_type, "any", cfg))
        r["seeds_ok"] = int(r.get("seeds") or 0) >= decent_seeders(r.get("size_bytes"), cfg)
        r["best_pick"] = False

    def key(r: Dict[str, Any]) -> Tuple:
        if r["seeds_ok"]:
            q = points.get(r.get("quality") or "HD", points.get("HD", 45))
            return (0, 1 if r.get("too_small") else 0, -q, -(r.get("size_bytes") or 0), -(r.get("seeds") or 0))
        return (1, 0, -(r.get("score") or 0), 0, -(r.get("seeds") or 0))

    ordered = sorted(results, key=key)
    if ordered:
        ordered[0]["best_pick"] = True
    return ordered


def rank_results(
    results: List[Dict[str, Any]],
    query: str,
    media_type: str,
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Annotates each result with score/is_pack/... and sorts best first."""
    scope = query_scope(query) if media_type == "tv" else "any"
    for r in results:
        r.update(score_result(r, media_type, scope, cfg))
    results.sort(key=lambda r: (-r["score"], -(r.get("seeds") or 0)))
    return results
