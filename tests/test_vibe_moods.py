"""
Mood map tests (tests/test_vibe_moods.py)

Fixture films go through vibe_popup.features(), as the pop-up will do; no DB.

    uv run python tests/test_vibe_moods.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.vibe_moods as vm  # noqa: E402
from services.vibe_popup import features  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def film(genres, facets=None, keywords="", feels=None):
    tags = {"feels": feels} if feels else None
    return features({
        "genres": genres, "tier2_keywords": keywords,
        "facets": json.dumps(facets) if facets else None,
        "vibe_tags": json.dumps(tags) if tags else None,
    })


def fits(fx):
    return {m["id"]: vm.fit(fx, m) for m in vm.MOODS}


def test_data():
    print("Data")
    try:
        ok = vm.validate()
    except ValueError as e:
        ok = str(e)
    check("validate() passes", ok is True, ok)
    check("30 moods", len(vm.MOODS) == 30, len(vm.MOODS))
    check("6 groups", sorted(vm.GROUPS) == ["dark", "drift", "heart", "light", "mind", "thrill"], sorted(vm.GROUPS))
    check("date_night is part Light, part Heart", vm.memberships("date_night") == {"light": 0.4, "heart": 0.6},
          vm.memberships("date_night"))

    bad = dict(vm.BY_ID["laugh_out_loud"], feels=["hilarious"], dials={"funniness": 0.9},
               dial_weight={"funniness": 1.0})
    saved = vm.MOODS[2]
    vm.MOODS[2] = bad
    try:
        vm.validate()
        msg = ""
    except ValueError as e:
        msg = str(e)
    finally:
        vm.MOODS[2] = saved
    check("validate() names unknown dials and feels", "funniness" in msg and "hilarious" in msg, msg)


def test_fit():
    print("Fit")
    rom_com = film("Romance, Comedy", {"humour": 0.5})
    f = fits(rom_com)
    check("rom-com fits romance_warm better than laugh_out_loud",
          f["romance_warm"] > f["laugh_out_loud"], (f["romance_warm"], f["laugh_out_loud"]))
    check("rom-com still fits laugh_out_loud above 0.3", f["laugh_out_loud"] > 0.3, f["laugh_out_loud"])
    check("every fit is in 0..1", all(0.0 <= v <= 1.0 for v in f.values()), f)

    horror = film("Horror, Thriller", {"darkness": 0.9, "gore": 0.75, "tension": 0.9, "humour": 0.05,
                                       "ending_valence": 0.2},
                  "haunted house, possession, supernatural", ["dread", "eerie"])
    f = fits(horror)
    best = max(f, key=f.get)
    check("horror fixture fits proper_horror best", best == "proper_horror", (best, round(f[best], 3),
                                                                              round(f["proper_horror"], 3)))

    bare_comedy = film("Comedy", keywords="parody, slapstick")
    bare_horror = film("Horror", keywords="slasher, ghost")
    check("films with no facets have no facets", not bare_comedy["facets"] and not bare_horror["facets"])
    c, h = fits(bare_comedy), fits(bare_horror)
    check("no facets: comedy film fits laugh_out_loud above proper_horror",
          c["laugh_out_loud"] > c["proper_horror"] + 0.2, (c["laugh_out_loud"], c["proper_horror"]))
    check("no facets: horror film fits proper_horror above laugh_out_loud",
          h["proper_horror"] > h["laugh_out_loud"] + 0.2, (h["proper_horror"], h["laugh_out_loud"]))
    genre_only = fits(film("Comedy")), fits(film("Horror"))
    check("no facets or keywords: genres alone still split comedy from horror",
          genre_only[0]["laugh_out_loud"] > genre_only[1]["laugh_out_loud"]
          and genre_only[1]["proper_horror"] > genre_only[0]["proper_horror"], genre_only)

    m = vm.BY_ID["laugh_out_loud"]
    check("a missing dial counts 0.5, never 0", vm.parts(film("Drama"), m)["dials"] == 0.5,
          vm.parts(film("Drama"), m))
    check("TV genres are split and mapped (Sci-Fi & Fantasy → science fiction, fantasy)",
          vm._film_genres(["Sci-Fi & Fantasy"]) == {"science fiction", "fantasy"},
          vm._film_genres(["Sci-Fi & Fantasy"]))
    check("keywords match whole words (war is not award)",
          not vm._kw_hit("war", ["award winning"]) and vm._kw_hit("war", ["world war ii"]))
    fx = {"kw": ["award winning", "world war ii"], "genres": set()}
    war = {"keywords": ["war"], "dial_weight": {}, "dials": {}}
    check("fit's keyword test is whole-word too", vm.parts(fx, war).get("keywords") == 0.5, vm.parts(fx, war))
    check("a term can't span two keywords",
          "keywords" not in vm.parts({"kw": ["world", "war"], "genres": set()},
                                     {"keywords": ["world war"], "dial_weight": {}, "dials": {}})
          or vm.parts({"kw": ["world", "war"], "genres": set()},
                      {"keywords": ["world war"], "dial_weight": {}, "dials": {}})["keywords"] == 0.0)


if __name__ == "__main__":
    test_data()
    test_fit()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
