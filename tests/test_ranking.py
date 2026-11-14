"""
Offline tests for the "Best match" copy ranking (tests/test_ranking.py)

    uv run python tests/test_ranking.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.torrent_ranking import (  # noqa: E402
    best_pick_order,
    classify_release,
    decent_seeders,
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

print("Best pick (best quality that still has decent seeders)")
check("decent seeders grow with size", decent_seeders(int(2 * GB), cfg) == 11 and decent_seeders(int(80 * GB), cfg) == 30)
films = [
    r("webdl1080", "1080p", 6.4, 140),
    r("remux_thin", "2160p", 118, 6),        # best quality, but 6 people will never finish 118 GB
    r("remux_ok", "2160p", 80, 45),          # big but well shared: the one she'd take by hand
    r("webdl4k", "2160p", 18.5, 85),
    r("fake4k", "2160p", 1.0, 400),          # far too small for 4K
    r("720_big", "720p", 1.3, 900),
]
picked = best_pick_order([dict(x) for x in films], "movie", cfg)
names = [x["name"] for x in picked]
check("well shared 4K remux is the best pick, not pushed down for being big", names[0] == "remux_ok", names)
check("only the first is marked best pick", picked[0]["best_pick"] and not any(x["best_pick"] for x in picked[1:]))
check("4K before 1080p before 720p among well shared", names.index("webdl4k") < names.index("webdl1080") < names.index("720_big"), names)
check("a far-too-small 4K comes after every normal-sized well shared copy", names.index("fake4k") > names.index("720_big"), names)
check("thin remux is last and flagged", names[-1] == "remux_thin" and picked[-1]["seeds_ok"] is False, names)
nobody = best_pick_order([dict(r("a", "2160p", 40, 3)), dict(r("b", "1080p", 10, 8))], "movie", cfg)
check("nothing decent: falls back to Best match order and still picks one", nobody[0]["best_pick"] and nobody[0]["name"] == "b",
      [x["name"] for x in nobody])
strict = load_ranking_config({"torrent_ranking": {"best_pick": {"min_seeders": 100}}})
check("min_seeders comes from the config", best_pick_order([dict(x) for x in films], "movie", strict)[0]["name"] == "webdl1080")
packs = best_pick_order([dict(r("Show S03 2160p", "2160p", 41, 64)), dict(r("Show S03 1080p", "1080p", 15.8, 240))], "tv", cfg)
check("season packs: 4K pack with 64 seeders wins", packs[0]["name"] == "Show S03 2160p" and packs[0]["is_pack"])
check("empty list is fine", best_pick_order([], "movie", cfg) == [])

print("Config overrides")
cfg2 = load_ranking_config({"torrent_ranking": {"quality_points": {"720p": 99}, "seeders": {"per_gb": 0}}})
check("override applies", cfg2["quality_points"]["720p"] == 99 and cfg2["quality_points"]["1080p"] == 80)
check("nested override keeps siblings", cfg2["seeders"] == {"base": 5, "per_gb": 0})

if failures:
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1)
print("\nAll ranking checks passed")
