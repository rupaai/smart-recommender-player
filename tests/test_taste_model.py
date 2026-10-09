"""
Offline tests for the taste model (tests/test_taste_model.py)

Pure functions only, no database.

    uv run python tests/test_taste_model.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import taste_model as tm  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def film(score, dials, cluster=None, inferred=0, **extra):
    rec = {"taste_score": score, "facets": dials, "vibe_cluster_id": cluster, "inferred": inferred}
    rec.update(extra)
    return rec


def many(n, score, dials, **kw):
    return [film(score, dict(dials), **kw) for _ in range(n)]


def test_dial_preference():
    print("Dial preference")
    liked = many(8, 0.6, {"pace": 0.2, "humour": 0.8}) + many(4, 0.6, {"pace": 0.3, "humour": 0.7})
    disliked = many(4, -1.0, {"pace": 0.9, "humour": 0.1})
    m = tm.build_taste_model(liked + disliked, {"min_verdicts": 5})
    check("model is active", m["active"])
    p = m["dials"]["pace"]
    check("preferred pace is low (slow-burn)", p["pref"] < 0.25, p)
    check("pushed away from the disliked level (below the liked mean)", p["pref"] < 0.2333, p)
    check("humour preferred high", m["dials"]["humour"]["pref"] > 0.7, m["dials"]["humour"])
    check("tolerance reflects spread", m["dials"]["pace"]["tolerance"] >= 0.12)
    slow = tm.facet_fit(m, {"facets": {"pace": 0.2, "humour": 0.8}})
    fast = tm.facet_fit(m, {"facets": {"pace": 0.9, "humour": 0.1}})
    check("a film at my levels fits better than one at the disliked levels", slow["value"] > fast["value"] + 0.3,
          (slow["value"], fast["value"]))
    check("film with no facets scores 0", tm.facet_fit(m, {"facets": {}})["value"] == 0.0)

    # Disliked or unfinished films alone never create a preference.
    only_bad = tm.build_taste_model(many(8, -1.0, {"pace": 0.9}), {"min_verdicts": 5})
    check("no liked films -> no preferred level", "pace" not in only_bad["dials"], only_bad["dials"])


def test_corrections():
    print("Vibe-check corrections")
    dials = {"darkness": 0.5, "pace": 0.5, "attention": 0.5, "tension": 0.5, "humour": 0.5,
             "strangeness": 0.5, "weight": 0.5, "spectacle": 0.5}
    out, lim = tm.corrected_dials(dials, {"darker": "darker"}, 0.2)
    check("'Darker than expected' raises darkness", abs(out["darkness"] - 0.7) < 1e-9, out["darkness"])
    out, _ = tm.corrected_dials(dials, {"darker": "lighter"}, 0.2)
    check("'Lighter' lowers darkness", abs(out["darkness"] - 0.3) < 1e-9, out["darkness"])
    out, lim = tm.corrected_dials(dials, {"darker": "expected", "pace": "right", "tension": "right"}, 0.2)
    check("'As expected' / 'Just right' confirm the tag", out == dials and lim == [], (out, lim))
    out, lim = tm.corrected_dials(dials, {"pace": "dragged", "tension": "toomuch", "weird": "ordinary",
                                          "brain": "half", "funny": "funnier"}, 0.2)
    check("pace, tension, strangeness, attention, humour all shift",
          out["pace"] < 0.5 and out["tension"] > 0.5 and out["strangeness"] < 0.5
          and out["attention"] < 0.5 and out["humour"] > 0.5, out)
    check("Dragged and Too much mark limits", ("pace", "min") in lim and ("tension", "max") in lim, lim)
    out, _ = tm.corrected_dials({"darkness": 0.95}, {"darker": "darker"}, 0.2)
    check("shift is clipped to 0..1", out["darkness"] == 1.0)
    out, _ = tm.corrected_dials({}, {"darker": "darker"}, 0.2)
    check("an unknown dial is not invented", out == {})
    check("multi-pick accepts a list or a string",
          tm.answers_for({"felt": ["warm", "joyful"]}, "felt") == ["warm", "joyful"]
          and tm.answers_for({"felt": "warm"}, "felt") == ["warm"]
          and tm.answers_for({}, "felt") == [])

    # Corrected values are what the preferred level is learned from.
    plain = many(10, 0.6, {"darkness": 0.5})
    shifted = [film(0.6, {"darkness": 0.5}, vibe_check={"darker": "darker"}) for _ in range(10)]
    m0 = tm.build_taste_model(plain, {"min_verdicts": 5})
    m1 = tm.build_taste_model(shifted, {"min_verdicts": 5})
    check("learned darkness uses the corrected value", m1["dials"]["darkness"]["pref"] > m0["dials"]["darkness"]["pref"] + 0.15,
          (m0["dials"]["darkness"], m1["dials"]["darkness"]))

    # A 'Too much' tension on a liked film is a limit; on a disliked film it is not.
    base = many(8, 0.6, {"tension": 0.5})
    lim_m = tm.build_taste_model(base + [film(0.6, {"tension": 0.8}, vibe_check={"tension": "toomuch"})], {"min_verdicts": 5})
    no_lim = tm.build_taste_model(base + [film(-1.0, {"tension": 0.8}, vibe_check={"tension": "toomuch"})], {"min_verdicts": 5})
    check("limit recorded from a liked film", "tension" in lim_m["limits"] and "max" in lim_m["limits"]["tension"], lim_m["limits"])
    check("no limit from a disliked film", "tension" not in no_lim["limits"], no_lim["limits"])
    over = tm.facet_fit(lim_m, {"facets": {"tension": 1.0}})["parts"]["tension"]
    under = tm.facet_fit(lim_m, {"facets": {"tension": 0.5}})["parts"]["tension"]
    check("a film beyond the limit is penalised", over < under, (over, under))


def test_chips():
    print("Chip weights")
    check("pace/humour/ending/story/visuals map to features",
          tm.CHIP_DIALS["pace"] == ("pace",) and tm.CHIP_DIALS["humour"] == ("humour",)
          and tm.CHIP_DIALS["ending"] == ("ending_valence",) and set(tm.CHIP_DIALS["story"]) == {"attention", "strangeness"}
          and "spectacle" in tm.CHIP_DIALS["visuals"] and tm.CHIP_SETS["vibe"] == ("feels",) and "palette" in tm.CHIP_SETS["visuals"])
    check("acting / music / characters / feeling map to nothing",
          all(c not in tm.CHIP_DIALS and c not in tm.CHIP_SETS for c in tm.IGNORED_CHIPS))
    dials = {"pace": 0.3, "humour": 0.7}
    plain = many(10, 0.6, dials)
    chipped = [film(0.6, dict(dials), worked=["pace", "acting"]) for _ in range(10)]
    a = tm.build_taste_model(plain, {"min_verdicts": 5})
    b = tm.build_taste_model(chipped, {"min_verdicts": 5})
    check("'pace' under worked on loved films raises the pace weight only",
          b["dials"]["pace"]["weight"] > a["dials"]["pace"]["weight"] and
          abs(b["dials"]["humour"]["weight"] - a["dials"]["humour"]["weight"]) < 1e-9,
          (a["dials"]["pace"]["weight"], b["dials"]["pace"]["weight"]))
    weak = [film(0.2, dict(dials), worked=["pace"]) for _ in range(10)]
    c = tm.build_taste_model(weak, {"min_verdicts": 5})
    check("worked on merely 'liked' films adds nothing", abs(c["dials"]["pace"]["weight"] - a["dials"]["pace"]["weight"]) < 1e-9)
    check("an ignored chip changes nothing",
          tm.build_taste_model([film(0.6, dict(dials), worked=["acting", "music", "feeling"]) for _ in range(10)],
                               {"min_verdicts": 5})["dials"] == a["dials"])
    # didn't + disliked: films near the blamed pace level are penalised.
    liked = many(8, 0.6, {"pace": 0.5})
    bad = [film(-1.0, {"pace": 0.9}, didnt=["pace"]) for _ in range(4)]
    bad_plain = [film(-1.0, {"pace": 0.9}) for _ in range(4)]
    m = tm.build_taste_model(liked + bad, {"min_verdicts": 5})
    m0 = tm.build_taste_model(liked + bad_plain, {"min_verdicts": 5})
    near = tm.facet_fit(m, {"facets": {"pace": 0.9}})["parts"]["pace"]
    near0 = tm.facet_fit(m0, {"facets": {"pace": 0.9}})["parts"]["pace"]
    check("'didn't' on disliked films penalises that level", near < near0, (near, near0))
    # feels set affinity from the vibe-check 'felt' answers
    cosy = [film(0.6, {"pace": 0.4}, vibe_check={"felt": ["cozy"]}) for _ in range(6)]
    grim = [film(-1.0, {"pace": 0.4}, vibe_check={"felt": ["dread"]}) for _ in range(4)]
    warm = [film(0.6, {"pace": 0.4}, vibe_check={"felt": ["warm"]}) for _ in range(4)]
    ms = tm.build_taste_model(cosy + grim + warm, {"min_verdicts": 5})
    fc = tm.facet_fit(ms, {"facets": {"pace": 0.4}, "vibe_tags": {"feels": ["cozy"]}})["parts"].get("feels", 0)
    fd = tm.facet_fit(ms, {"facets": {"pace": 0.4}, "vibe_tags": {"feels": ["dread"]}})["parts"].get("feels", 0)
    check("feels you loved score above feels you disliked", fc > 0 > fd, (fc, fd))


def test_clusters():
    print("Cluster affinity")
    rated = [film(0.6, {"pace": 0.5}, cluster=7) for _ in range(5)] + [film(-1.0, {"pace": 0.5}, cluster=9)] * 1
    rated += [film(0.6, {"pace": 0.5}, cluster=3) for _ in range(20)] + [film(0.6, {"pace": 0.5}, cluster=4)]
    m = tm.build_taste_model(rated, {"min_verdicts": 5})
    c7 = m["clusters"][7]
    check("5 films at +0.6 -> 0.6 * 5/10 = 0.3", abs(c7 - 0.3) < 1e-3, c7)
    check("many films -> close to the mean", m["clusters"][3] > 0.45, m["clusters"][3])
    check("one film barely moves a cluster", abs(m["clusters"][4]) < 0.12, m["clusters"][4])
    check("one dislike is shrunk toward zero but stays negative", -0.25 < m["clusters"][9] < 0, m["clusters"][9])
    check("unknown cluster scores 0", tm.cluster_affinity(m, {"vibe_cluster_id": 999}) == 0.0)
    check("item with no cluster scores 0", tm.cluster_affinity(m, {}) == 0.0)


def test_inferred_half_weight():
    print("Inferred verdicts at half weight")
    dials_a, dials_b = {"pace": 0.2}, {"pace": 0.8}
    confirmed = [film(0.6, dials_a)] * 6 + [film(0.6, dials_b)] * 6
    mixed_c = [film(0.6, dials_a)] * 6 + [film(0.6, dials_b, inferred=1)] * 6
    m_c = tm.build_taste_model(confirmed, {"min_verdicts": 5})
    m_m = tm.build_taste_model(mixed_c, {"min_verdicts": 5})
    check("confirmed split sits in the middle", abs(m_c["dials"]["pace"]["pref"] - 0.5) < 1e-6, m_c["dials"]["pace"])
    # weights 1 : 0.5 -> mean = (0.2*6 + 0.8*3) / 9 = 0.4
    check("inferred films pull half as hard", abs(m_m["dials"]["pace"]["pref"] - 0.4) < 1e-6, m_m["dials"]["pace"])
    check("effective count counts inferred as 0.5", abs(m_m["n_effective"] - 9.0) < 1e-6, m_m["n_effective"])
    check("verdict count is unweighted", m_m["n_verdicts"] == 12 and m_m["n_confirmed"] == 6)
    ci = tm.build_taste_model([film(0.6, {"pace": 0.5}, cluster=1, inferred=1)] * 10, {"min_verdicts": 5})
    cc = tm.build_taste_model([film(0.6, {"pace": 0.5}, cluster=1)] * 10, {"min_verdicts": 5})
    check("inferred-only history is trusted less overall", ci["confidence"] < cc["confidence"], (ci["confidence"], cc["confidence"]))
    check("cluster shrink uses the half-weighted count", ci["clusters"][1] < cc["clusters"][1], (ci["clusters"][1], cc["clusters"][1]))


def test_gate():
    print("Evidence gate")
    few = many(4, 0.6, {"pace": 0.2})
    m = tm.build_taste_model(few, {"min_verdicts": 10})
    check("below the minimum the model is inactive", not m["active"])
    check("inactive model scores 0 for facet_fit and cluster", tm.facet_fit(m, {"facets": {"pace": 0.2}})["value"] == 0.0
          and tm.cluster_affinity(m, {"vibe_cluster_id": 1}) == 0.0)
    b = tm.blend([(1.0, m)], {"facets": {"pace": 0.2}})
    check("blend of an inactive model is exactly neutral", b["facet_fit"] == 0.0 and b["cluster_affinity"] == 0.0 and b["parts"] == {})
    check("inferred verdicts count toward the minimum", tm.build_taste_model(many(10, 0.2, {"pace": 0.3}, inferred=1), {"min_verdicts": 10})["active"])
    # a dial with too little evidence is left out
    rated = many(12, 0.6, {"pace": 0.3}) + [film(0.6, {"pace": 0.3, "gore": 0.9})] * 2
    m2 = tm.build_taste_model(rated, {"min_verdicts": 5, "min_dial_evidence": 4})
    check("a dial seen on only 2 films is ignored", "gore" not in m2["dials"] and "pace" in m2["dials"], list(m2["dials"]))
    # thin history changes little: same preference, bigger history -> bigger effect
    small = tm.build_taste_model(many(10, 0.6, {"pace": 0.2}), {"min_verdicts": 10})
    big = tm.build_taste_model(many(80, 0.6, {"pace": 0.2}), {"min_verdicts": 10})
    s = tm.facet_fit(small, {"facets": {"pace": 0.2}})["value"]
    g = tm.facet_fit(big, {"facets": {"pace": 0.2}})["value"]
    check("thin history gives a smaller facet_fit than a long one", 0 < s < g, (s, g))
    # blending: a partner with no history doesn't dilute
    solo = tm.blend([(0.75, big), (0.25, m)], {"facets": {"pace": 0.2}})
    alone = tm.blend([(1.0, big)], {"facets": {"pace": 0.2}})
    check("an inactive partner adds nothing and the weights are not renormalised",
          abs(solo["facet_fit"] - 0.75 * alone["facet_fit"]) < 1e-3, (solo["facet_fit"], alone["facet_fit"]))


def test_reasons():
    print("Reasons")
    rated = many(12, 0.6, {"pace": 0.2, "humour": 0.8}, cluster=4)
    m = tm.build_taste_model(rated, {"min_verdicts": 5})
    s = tm.blend([(1.0, m)], {"facets": {"pace": 0.2, "humour": 0.8}, "vibe_cluster_id": 4})
    r = tm.reasons(s, {"facet_fit": 1.0, "cluster_affinity": 0.8}, "gentle comedies")
    check("at most two badges", 1 <= len(r) <= 2, r)
    check("badges come from real contributors", all(x in tm.DIAL_PHRASE.values() or x == "from your gentle comedies cluster" for x in r), r)
    only_cluster = tm.reasons({"parts": {}, "cluster_affinity": 0.4}, {"facet_fit": 1.0, "cluster_affinity": 0.8}, "gentle comedies")
    check("cluster badge uses the cluster's name", only_cluster == ["from your gentle comedies cluster"], only_cluster)
    check("no badge for a negative or zero contribution",
          tm.reasons({"parts": {"pace": -0.3}, "cluster_affinity": -0.2}, {"facet_fit": 1.0, "cluster_affinity": 0.8}, "x") == [])
    pace_first = tm.reasons({"parts": {"pace": 0.5, "humour": 0.05}, "cluster_affinity": 0.0}, {"facet_fit": 1.0, "cluster_affinity": 0.8})
    check("a tiny contributor earns no badge", pace_first == [tm.DIAL_PHRASE["pace"]], pace_first)


if __name__ == "__main__":
    test_dial_preference()
    test_corrections()
    test_chips()
    test_clusters()
    test_inferred_half_weight()
    test_gate()
    test_reasons()
    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        sys.exit(1)
    print("All taste model checks passed")
