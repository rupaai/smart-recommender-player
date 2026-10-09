"""
Offline tests for the "Best match" copy ranking (tests/test_ranking.py)

    uv run python tests/test_ranking.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.torrent_ranking import (  # noqa: E402
    classify_release,
    load_ranking_config,
    query_scope,
    rank_results,
)

GB = 1024 ** 3
failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


def r(name, quality, gb, seeds):
    return {"name": name, "quality": quality, "size_bytes": int(gb * GB), "seeds": seeds}


cfg = load_ranking_config({})

print("Movies")
movies = [
    r("remux", "2160p", 62, 14),
    r("webdl4k", "2160p", 18.5, 85),
    r("x265_4k", "2160p", 8.2, 22),
    r("fake4k", "2160p", 3.1, 6),
    r("bluray1080", "1080p", 14.8, 9),
    r("webdl1080", "1080p", 6.4, 140),
    r("yts1080", "1080p", 2.4, 610),
    r("tiny1080", "1080p", 1.0, 45),
    r("720_big", "720p", 1.3, 900),
    r("720_dead", "720p", 0.75, 2),
    r("480", "480p", 0.7, 40),
]
ranked = [x["name"] for x in rank_results(movies, "Dune Part Two", "movie", cfg)]
check("healthy 4K first", ranked[0] == "webdl4k", ranked)
check("huge 4K remux with 14 seeders near the bottom", ranked.index("remux") >= len(ranked) - 2, ranked)
check("9-seeder 14.8 GB 1080p below healthy 1080p", ranked.index("bluray1080") > ranked.index("webdl1080"))
check("tiny 1080p below healthy 1080p", ranked.index("tiny1080") > ranked.index("yts1080"))
check("720p with 900 seeders below good 1080p", ranked.index("720_big") > ranked.index("webdl1080"))
check("score present", all("score" in x for x in movies))

print("TV classification")
check("episode", classify_release("The Bear S02E06 1080p")[0] == "episode")
check("episode 1x05", classify_release("Show 1x05 720p")[0] == "episode")
check("season pack", classify_release("The Bear S02 1080p WEB-DL")[0] == "pack")
check("season word pack", classify_release("The Bear Season 2 Complete")[0] == "pack")
check("multi-season counts seasons", classify_release("Show S01-S03 1080p") == ("pack", 3))
check("query scope episode", query_scope("The Bear S02E06") == "episode")
check("query scope season", query_scope("The Bear S02") == "season")
check("query scope any", query_scope("The Bear") == "any")

print("TV ranking")
tv = [
    r("The Bear S02 1080p WEB-DL", "1080p", 14, 180),
    r("The Bear S02 2160p WEB-DL", "2160p", 42, 25),
    r("The Bear S02 720p", "720p", 5.5, 320),
    r("The Bear S02E06 1080p", "1080p", 1.6, 95),
    r("The Bear S02E06 2160p", "2160p", 4.8, 12),
]
season = [x["name"] for x in rank_results([dict(x) for x in tv], "The Bear S02", "tv", cfg)]
check("season search: pack first", season[0] == "The Bear S02 1080p WEB-DL", season)
episode = [x["name"] for x in rank_results([dict(x) for x in tv], "The Bear S02E06", "tv", cfg)]
check("episode search: episodes first", episode[0].startswith("The Bear S02E06"), episode)
packs = rank_results([dict(x) for x in tv], "The Bear S02", "tv", cfg)
p1080 = next(x for x in packs if x["name"].endswith("1080p WEB-DL"))
check("pack flagged with episodes and per-episode size", p1080["is_pack"] and p1080["episodes"] == 10 and p1080["per_episode_bytes"] == int(14 * GB / 10))

print("Config overrides")
cfg2 = load_ranking_config({"torrent_ranking": {"quality_points": {"720p": 99}, "seeders": {"per_gb": 0}}})
check("override applies", cfg2["quality_points"]["720p"] == 99 and cfg2["quality_points"]["1080p"] == 80)
check("nested override keeps siblings", cfg2["seeders"] == {"base": 5, "per_gb": 0})

if failures:
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("\nAll ranking checks passed")
